"""
    Copyright 2025 Flexxbotics, Inc.

    Licensed under the Apache License, Version 2.0 (the "License");
    you may not use this file except in compliance with the License.
    You may obtain a copy of the License at

        http://www.apache.org/licenses/LICENSE-2.0

    Unless required by applicable law or agreed to in writing, software
    distributed under the License is distributed on an "AS IS" BASIS,
    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
    See the License for the specific language governing permissions and
    limitations under the License.
"""
import string
import time

from data_models.device import Device
import json
import base64
from transformers.abstract_device import AbstractDevice
from protocols import modbus
from werkzeug.exceptions import HTTPException
from exceptions.flexxCoreExceptions import (
    FlexxCoreException,
    BadRequestException,
    ServerErrorException,
    ServiceUnavailableException,
)


class CognexModbusTCP(AbstractDevice):

    def __init__(self, device: Device):
        """
        Cognex In-Sight camera device class: native string commands sent over Modbus TCP.
        Reads metaData "ip_address" and "port" (both required; ValueError when missing).
        Does not connect.

        :param device:
                    the device object

        :return:    a new instance
        """
        # Get meta data of the device from its attributes, this contains information such as: ip address, ports, etc
        super().__init__(device)


        # modbusTCP setup
        self.meta_data = device.metaData or {}
        self.CAMERA_IP = self.meta_data.get("ip_address", "")
        self.CAMERA_PORT = int(self.meta_data.get("port") or 0)
        if not self.CAMERA_IP:
            raise ValueError("CognexModbusTCP: metaData must contain 'ip_address'")
        if not self.CAMERA_PORT:
            raise ValueError("CognexModbusTCP: metaData must contain 'port'")

        # The Modbus wrapper opens and closes the socket on every call; _connect() creates the client
        self._client = None
        self._connected = False

        # register setup
        self.STATUS_REGISTER = 100
        self.CONTROL_REGISTER = 0
        self.MAX_REGS_PER_READ = 125

        # String command send setup
        self.STRING_COMMAND_LENGTH_ADDR = 1000
        self.STRING_COMMAND_START_ADDR = 1001
        self.INITIATE_STRING_COMMAND_COIL = 17
        self.ACK_INPUT_BIT = 17
        self.ERROR_INPUT_BIT = 18

        #String command recieve setup
        self.RESULT_CODE_ADDR = 1000  # Input Register
        self.RESULT_LENGTH_ADDR = 1001
        self.RESULT_START_ADDR = 1002

        #String commands
        self.SET_ONLINE = "SO1"
        self.SET_OFFLINE = "SO0"
        self.GET_VARIABLE = "GV"
        self.TRIGGER = "MT"
        self.GET_FILE_LIST = "Get FileList"



    def __del__(self):
        try:
            self._disconnect()
        except Exception:
            pass

    # ############################################################################## #
    # DEVICE COMMUNICATION METHODS
    # ############################################################################## #

    def _execute_command_v2(self, command_name : str, command_args : str) -> str:
        """
        Executes the command sent to the device.

        Supported commands
        ------------------
        connect     Returns: {"status": "ok", "connected": true}
        disconnect  Returns: {"status": "ok", "connected": false}

        Any other command raises BadRequestException (400).

        :param command_name:
                    the command to be executed
        :param command_args:
                    json with the arguments for the command

        :return:    the response after execution of command.
        """
        # Parse the command from the incoming request
        args = self._parse_args(command_args)

        self._info(message="Sending command: " + command_name)
        try:
            if command_name == "connect":
                self._connect()
                return json.dumps({"status": "ok", "connected": True})

            if command_name == "disconnect":
                self._disconnect()
                return json.dumps({"status": "ok", "connected": False})

        except Exception as e:
            self._raise_device_error(f"Command '{command_name}'", e)

        raise BadRequestException(f"Unknown command '{command_name}'")

    def _read_interval_data(self) -> str:
        """
        Does nothing; no interval data is read.

        :return:    None
        """
        pass

    def _read_status(self, function : str=None) -> str:
        """
        Method to read the status of the device

        :param function:
                    Ignored

        :return:    status - "ONLINE" / "OFFLINE", or "ERROR" when the camera cannot be read
        """
        try:
            self._ensure_connected()
            response = self._client.read_holding_register(self.STATUS_REGISTER, count=2)
            reg1 = response.registers[0]
            reg2 = response.registers[1]
            byte0 = reg1 & 0xFF
            byte1 = reg2 & 0xFF

            online_status = bool(byte0 & 0x80)
            program_status = bool(byte1 & 0x01)
            error_status = bool(byte1 & 0x80)

            if response.isError():
                return "ERROR GETTING RESPONSE"
            return "ONLINE" if bool(byte0 & 0x80) else "OFFLINE"
        except Exception as e:
            self._error(message=f"Status read failed: {e}")
            self._connected = False
            return "ERROR"

    def _read_variable(self, variable_name : str, function : str=None) -> str:
        """
        Method to read the specified variable from the device

        :param variable_name:
                    The In-Sight variable name; sent as the native "GV<name>" command - string

        :param function:
                    Ignored

        :return:    value - string. Raises on failure (503 when the camera does not acknowledge the
                    command within about 5 s or on a raw socket error, 500 otherwise, including
                    when pymodbus cannot reach the camera).
        """
        try:
            value = self._send_string_command("GV" + variable_name)
            return str(value)
        except Exception as e:
            self._raise_device_error(f"Read variable '{variable_name}'", e)

    def _run_program(self, function: str = None):
        """
            Method to run the active program on the device: sends the native "SW8" command

            :param function:
                        Ignored

            :return:    the camera's reply - string, or "Error sending program" on failure (never raises)
        """
        try:
            response = self._send_string_command("SW8")
        except Exception as e:
            response = "Error sending program"
            self._error(message=str(e))

        return response

    def _read_file_names(self) -> list:
        """
        Method to get a list of filenames from the device

        :return:    list of filenames
        """

        # Return list of available filenames from the device
        try:
            programs_string = self._send_string_command("Get FileList")
            programs = programs_string.splitlines()
            return programs #TODO is this the actual response object we want?
        except Exception as e:
            self._raise_device_error("Read file names", e)

    def _read_file(self, file_name : str) -> str:
        """
        Method to read a file from a device

        :param file_name:
                    the name of the file to read; sent as the native "RJ<file_name>" command.

        :return:    the camera's reply as base64 string.
        """
        # Reads the file content off the device
        try:
            file_data = self._send_string_command("RJ" + file_name)
            # The register decoder maps each byte to one character, so latin-1 gives the bytes back
            return base64.b64encode(file_data.encode("latin-1")).decode("ascii")
        except Exception as e:
            self._raise_device_error(f"Read file '{file_name}'", e)

    def _write_file(self, file_name : str, file_data : str):
        """
        Not supported: does nothing.

        :param file_name:
                    the name of the file to write.
        :param file_data:
                    the data of the file to write as base64 string.
        """
        pass

    def _load_file(self, file_name : str):
        """
        Loads a job on the camera: takes it offline ("SO0"), sends "SJ<file_name>", reads the
        current job with "GJ", and puts the camera back online ("SO1", even when the change failed).
        Raises on failure (503 when the camera does not acknowledge a command, 500 otherwise;
        see _read_variable).

        :param file_name: the job to load (its number), sent as "SJ<file_name>"
        :return: "Current Job Set To: " + the reply to "RJ<current job>" - string
        """

        #For Cognex Jobs the file name corresponds to an int at the start of the job name

        try:
            #Camera starts online and needs to be offline to change jobs
            try:
                self._send_string_command("SO0")
                self._send_string_command("SJ" + file_name)
                current_job = self._send_string_command("GJ")
            finally:
                # Always put the camera back online, even when the job change failed
                self._send_string_command("SO1")

            return "Current Job Set To: " + self._send_string_command("RJ" + current_job)
        except Exception as e:
            self._raise_device_error(f"Load file '{file_name}'", e)

    # ############################################################################## #
    # INTERFACE HELPER METHODS
    #
    # These are private methods with naming convention _some_method(self). These are used to faciliate
    # any specific functions that are needed to communicate via the transformer. For example,
    # connection methods, read/write methods, specific functions, etc.
    # ############################################################################## #

    def _read_input_registers_chunked(self, start_addr, count):
        """Read Modbus input registers in chunks of max 125."""
        all_regs = []
        addr = start_addr
        remaining = count

        while remaining > 0:
            this_count = min(self.MAX_REGS_PER_READ, remaining)
            response = self._client.read_input_register(address=addr, count=this_count)
            if not response or not hasattr(response, "registers"):
                self._error(message=f"Failed to read {this_count} registers from {addr}")
                break
            all_regs.extend(response.registers)
            addr += this_count
            remaining -= this_count

        return all_regs

    def _decode_registers_to_string(self, registers, high_byte_first=False):
        """Convert Modbus registers to ASCII string."""
        chars = []
        for reg in registers:
            if high_byte_first:
                # Reads high byte first
                high = (reg >> 8) & 0xFF
                low = reg & 0xFF
                if high != 0:
                    chars.append(chr(high))
                if low != 0:
                    chars.append(chr(low))
            else:
                # Reads low byte first
                chars.append(chr(reg & 0xFF))  # Low byte first
                high = (reg >> 8) & 0xFF
                if high != 0:
                    chars.append(chr(high))
        return ''.join(chars).strip('\x00')

    def _send_string_command(self, command: str) -> str:
        """Initiate String Command Via ModbusTCP. Returns the result string; raises on failure."""
        self._ensure_connected()
        ascii_vals = [ord(c) for c in command]
        if len(ascii_vals) % 2 != 0:
            ascii_vals.append(0)

        words = [(ascii_vals[i + 1] << 8) | ascii_vals[i] for i in range(0, len(ascii_vals), 2)]

        # write the length of the command
        self._client.write_multiple_registers(address=self.STRING_COMMAND_LENGTH_ADDR, values=[len(command)])

        # write the command itself
        self._client.write_multiple_registers(address=self.STRING_COMMAND_START_ADDR, values=words)

        # initiate the command
        self._client.write_single_coil(self.INITIATE_STRING_COMMAND_COIL, value=True)

        # wait for acknowledgment of command

        for _ in range(50):
            ack = self._client.read_discrete_inputs(self.ACK_INPUT_BIT, count=1).bits[0]
            if ack:
                break
            time.sleep(0.1)
        else:
            raise ServiceUnavailableException(f"Cognex: timed out waiting for ACK of command '{command}'")

        error = self._client.read_discrete_inputs(self.ERROR_INPUT_BIT, count=1).bits[0]
        if error:
            error_response = self._read_string_command_results()
            raise ServerErrorException(f"Cognex: command '{command}' failed: {error_response}")

            # Clear Initiate String Command
        self._client.write_single_coil(self.INITIATE_STRING_COMMAND_COIL, False)

        return self._read_string_command_results()

    def _read_string_command_results(self) -> str:
        """Read result code. Returns the result string; raises ServerErrorException on a failure code."""
        result_code = self._client.read_input_register(self.RESULT_CODE_ADDR, count=1).registers[0]
        if result_code != 1:
            raise ServerErrorException(f"Cognex: string command failed, result code {result_code}")

        # Read result length
        result_length = self._client.read_input_register(self.RESULT_LENGTH_ADDR, count=1).registers[0]
        reg_count = (result_length + 1) // 2
        if reg_count > 0:
            result_regs = self._read_input_registers_chunked(self.RESULT_START_ADDR, count=reg_count)
            result_str = self._decode_registers_to_string(result_regs)
            result_str = result_str[:result_length]  # Truncate to exact length

        else:
            result_str = "command was sent with no response"

        return result_str

    def _connect(self) -> None:
        if self._connected and self._client is not None:
            return
        # No probe: the Modbus wrapper opens and closes the socket on every call
        self._client = modbus.ModbusTCP(self.CAMERA_IP, self.CAMERA_PORT)
        self._connected = True

    def _disconnect(self) -> None:
        try:
            if self._client is not None:
                self._client.disconnect()
        finally:
            self._client = None
            self._connected = False

    def _ensure_connected(self) -> None:
        if not self._connected or self._client is None:
            self._connect()

    # ############################################################################## #
    # STANDARD HELPERS (identical in every transformer)
    # ############################################################################## #

    def _execute_command(self, command) -> str:
        """
        Legacy command path, used by Edge HMI control buttons
        (POST /devices/<id>/execute_command). Hands off to _execute_command_v2.
        commandJson may be {"command": "x", "args": {...}} or {"command": "x", <args>}.
        """
        raw = command.get("commandJson") or "{}"
        command_json = json.loads(raw) if isinstance(raw, str) else raw
        args = command_json.get("args")
        if not isinstance(args, dict):
            args = {k: v for k, v in command_json.items() if k != "command"}
        return self._execute_command_v2(command_json.get("command", ""), json.dumps(args))

    @staticmethod
    def _parse_args(command_args) -> dict:
        """
        Parse command_args (the raw request body). Accepts empty, a JSON object, or the
        documented wrapper {"value": "<JSON object string>"}. The wrapper is only unwrapped
        when "value" is the only key, so a real "value" arg is left alone.
        """
        if not command_args:
            return {}
        try:
            args = json.loads(command_args) if isinstance(command_args, (str, bytes)) else command_args
            if isinstance(args, dict) and list(args) == ["value"]:
                inner = args["value"]
                if isinstance(inner, str) and inner.strip().startswith("{"):
                    inner = json.loads(inner)
                if isinstance(inner, dict):
                    return inner
        except ValueError as e:
            raise BadRequestException(f"Command args are not valid JSON: {e}")
        if not isinstance(args, dict):
            raise BadRequestException("Command args must be a JSON object")
        return args

    @staticmethod
    def _require(args: dict, key: str):
        """Return args[key], or raise BadRequestException (HTTP 400) when it is missing or blank."""
        if args.get(key) in (None, ""):
            raise BadRequestException(f"Missing required arg '{key}'")
        return args[key]

    @staticmethod
    def _as_bool(value, default: bool = False) -> bool:
        """metaData / arg booleans: accepts True/False, "true"/"false" (any case), "1"/"0", "yes"/"no"."""
        if isinstance(value, bool):
            return value
        if value is None or str(value).strip() == "":
            return default
        return str(value).strip().lower() in ("1", "true", "yes", "on")

    def _raise_device_error(self, action: str, error: Exception):
        """
        Re-raise a failed device call as the runtime exception the API turns into an HTTP error:
        runtime/HTTP exceptions pass through unchanged; network errors -> 503 (and the transformer
        is marked disconnected so the next call reconnects); anything else -> 500.
        """
        if isinstance(error, (FlexxCoreException, HTTPException)):
            raise error
        if isinstance(error, (OSError, TimeoutError, ConnectionError)):
            self._connected = False
            raise ServiceUnavailableException(f"{action}: device did not respond ({error})") from error
        raise ServerErrorException(f"{action} failed: {error}") from error
