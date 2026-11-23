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
#Library imports
import json
import base64
import time
import requests
from dataclasses import dataclass

from werkzeug.exceptions import HTTPException

# Flexx core references
from data_models.device import Device
from transformers.abstract_device import AbstractDevice
from exceptions.flexxCoreExceptions import (
    FlexxCoreException,
    BadRequestException,
    ServerErrorException,
    ServiceUnavailableException,
)
from protocols import modbus

class HeidenhainTNC7_Profinet(AbstractDevice):
    """
    Heidenhain TNC7 over Profinet. Reads and writes the CNC's Profinet bits through a PLC that
    exposes them as Modbus TCP command registers (one Modbus connection per call), and loads
    programs through the TNC Remo bridge (HTTP).

    Connection parameters (metaData)
    ---------------------------------
    plc_ip_address : IP address of the Modbus TCP PLC
    plc_port       : Modbus TCP port of the PLC (default 502)
    cnc_number     : number of this CNC on the PLC, 1..6 (required)
    cnc_ip_address : IP address of the CNC, passed to the bridge
    bridge_url     : base URL of the TNC Remo bridge (default "http://host.docker.internal:7083")

    Errors
    ------
    Failures raise the runtime's exceptions, which the API returns as HTTP errors carrying the
    message: BadRequestException (400) for bad args or an unknown command,
    ServiceUnavailableException (503) when the device does not respond, ServerErrorException (500)
    for anything else.  _read_status never raises (it returns "ERROR"), and neither does
    _read_interval_data.
    """

    _BRIDGE_TIMEOUT_S = 10

    def __init__(self, device: Device):
        """
        Template device class. Inherits AbstractDevice class.

        :param Device:
                    the device object

        :return:    a new instance
        """
        super().__init__(device)
        # Get meta data of the device from its attributes, this contains information such as: ip address, ports, etc
        self.meta_data = device.metaData or {}
        self.PROFINET_PLC_IP = self.meta_data.get("plc_ip_address", "")
        self.PLC_PORT = int(self.meta_data.get("plc_port") or 502)
        cnc_number = str(self.meta_data.get("cnc_number") or "").strip()
        if not cnc_number:
            raise ValueError("HeidenhainTNC7_Profinet: metaData must contain 'cnc_number'")
        try:
            self.CNC_NUMBER = int(cnc_number)
        except ValueError:
            raise ValueError(f"HeidenhainTNC7_Profinet: metaData 'cnc_number' must be a number, got '{cnc_number}'")
        self._client = None
        self._connected = False

        self.base = 6 * (6 - self.CNC_NUMBER)
        self.modbus_write_profinet_address = self.base + 0  # write address
        self.modbus_write_profinet_value = self.base + 1  # write value
        self.modbus_read_profinet_address = self.base + 2  # read address
        self.modbus_profinet_read_value_address = self.base + 3  # read result
        self.modbus_trigger_write = self.base + 4
        self.modbus_trigger_read = self.base + 5

        self.CNC_NUM_REG = 12




        #Profinet Status Addresses

        @dataclass
        class BitSignal:
            name: str
            address: str
            value: int = 0

        self.bit_signals = {
            "MACHINE_ERROR_STATE": BitSignal("MACHINE_ERROR_STATE", "IX0.4"),
            "PROGRAM_IN_PROG": BitSignal("PROGRAM_IN_PROG", "IX1.7"),
            "OPERATOR_OK": BitSignal("OPERATOR_OK", "IX0.7"),
            "M365": BitSignal("M365", "IX0.5"),
            "LOADING": BitSignal("LOADING", "IX1.1"),
            "NOK_PART": BitSignal("NOK_PART","IX1.6")
        }


        # Needed for connection to TNC Remo Server to load files to memory
        self.cnc_ip_address = self.meta_data.get("cnc_ip_address", "")
        self.base_url = str(
            self.meta_data.get("bridge_url") or "http://host.docker.internal:7083"
        ).rstrip("/")

    def __del__(self):
        try:
            self._disconnect()
        except Exception:
            pass

    # ############################################################################## #
    # DEVICE COMMUNICATION METHODS
    # ############################################################################## #

    def _execute_command_v2(self, command_name: str, command_args: str) -> str:
        """
        Executes the command sent to the device.

        Supported commands
        ------------------
        connect / disconnect
            Modbus connects per call, so these only create / drop the client.
            Returns: {"status": "ok", "connected": true|false}

        set_profinet_bit
            Write one Profinet output bit.
            Args: profinet_address (str, required) - output address, e.g. "QX0.1"
                  profinet_value (int 0/1, required)
            Returns: {"status": "ok", "value": "<write-address register read back, e.g. [1]>"}

        read_profinet_bit
            Read one Profinet input bit.
            Args: profinet_address (str, required) - input address, e.g. "IX7.4"
            Returns: {"status": "ok", "value": 0|1}

        set_multiple_profinet_bits
            Write several Profinet output bits, in order.
            Args: addresses (list of str, required), values (list of int 0/1, required, same length)
            Returns: {"status": "ok", "value": ["<register read back after each write>", ...]}

        Errors raise: 400 for missing/invalid args or an unknown command,
        503 when the device does not respond, 500 otherwise.

        :param command_name:
                    the command to be executed
        :param command_args:
                    json with the arguments for the command

        :return:    the response after execution of command.

        :author:    tylerjm@flexxbotics.com
        :since:     ODOULS.3 (7.1.15.3)
        """
        # Parse the command from the incoming request
        args = self._parse_args(command_args)

        self._info(message=f"Sending command: {command_name}")
        try:
            if command_name == "connect":
                self._connect()
                return json.dumps({"status": "ok", "connected": True})

            if command_name == "disconnect":
                self._disconnect()
                return json.dumps({"status": "ok", "connected": False})

            if command_name == "set_profinet_bit":
                address = str(self._require(args, "profinet_address"))
                value = int(self._require(args, "profinet_value"))
                # self._set_profinet_bit(args["profinet_address"],int(args["profinet_value"]))
                self.write_pn_bit(address, value, self.CNC_NUMBER)
                response = str(self._client.read_holding_register(self.modbus_write_profinet_address).registers)
                return json.dumps({"status": "ok", "value": response})

            if command_name == "read_profinet_bit":
                address = str(self._require(args, "profinet_address"))
                return json.dumps({"status": "ok", "value": self._read_pn_bit(address, self.CNC_NUMBER)})

            if command_name == "set_multiple_profinet_bits":
                addresses = self._require(args, "addresses")
                values = self._require(args, "values")
                if not isinstance(addresses, list) or not isinstance(values, list) or len(values) != len(addresses):
                    raise BadRequestException("addresses and values must be lists of the same length")
                response = []
                for add, value in zip(addresses, values):
                    self.write_pn_bit(str(add), int(value), self.CNC_NUMBER)
                    response.append(str(self._client.read_holding_register(self.modbus_write_profinet_address).registers))
                return json.dumps({"status": "ok", "value": response})

        except Exception as e:
            self._raise_device_error(f"Command '{command_name}'", e)

        raise BadRequestException(f"Unknown command '{command_name}'")

    def _read_interval_data(self) -> str:
        """
        Method to read the status of the device on an interval. Never raises.

        :return:    status - string

        :author:    tylerjm@flexxbotics.com
        :author:    sanua@flexxbotics.com

        :since:     ODOULS.3 (7.1.15.3)
        """
        try:
            status = self.read_status()
        except Exception as e:
            self._error(message=f"Interval read failed: {e}")
            status = "ERROR"

        return status

    def _read_status(self, function: str = None) -> str:
        """
        Method to read the status of the device

        :param function:
                    Not used

        :return:    status - string: FAULT, IDLE, RUNNING, PREPARE_FOR_LOADING, LOADING,
                    DOOR_UNLOCKED or NO MATCH, with " NOK_PART" appended when that bit is set;
                    "ERROR" when the bits cannot be read (never raises)

        :author:    tylerjm@flexxbotics.com
        :since:     ODOULS.3 (7.1.15.3)
        """
        status = ""
        try:
            self._update_bits()
            if self.bit_signals["MACHINE_ERROR_STATE"].value == 0:
                status = "FAULT"
            elif self.bit_signals["OPERATOR_OK"].value == 1 and self.bit_signals["PROGRAM_IN_PROG"].value == 0:
                status = "IDLE"
            elif self.bit_signals["PROGRAM_IN_PROG"].value == 1 and self.bit_signals["LOADING"].value == 0 and self.bit_signals["M365"].value == 0:
                status = "RUNNING"
            elif self.bit_signals["M365"].value == 1:
                status = "PREPARE_FOR_LOADING"
            elif self.bit_signals["LOADING"].value == 1:
                status = "LOADING"
            elif self.bit_signals["OPERATOR_OK"].value == 0:
                status = "DOOR_UNLOCKED"
            else:
                status = "NO MATCH"
            if self.bit_signals["NOK_PART"].value == 1:
                status = status + " NOK_PART"
        except Exception as e:
            self._error(message=f"Status read failed: {e}")
            self._connected = False
            return "ERROR"

        return status

    def _read_variable(self, variable_name: str, function: str = None) -> str:
        """
        Method to read the specified variable from the device

        :param variable_name:
                    Profinet input address, e.g. "IX7.4" - string

        :param function:
                    Not used

        :return:    value - string: "0" or "1". Raises on failure (503 when the device does not
                    respond, 500 otherwise).

        :author:    tylerjm@flexxbotics.com
        :since:     ODOULS.3 (7.1.15.3)
        """
        try:
            return str(self._read_pn_bit(variable_name, self.CNC_NUMBER))
        except Exception as e:
            self._raise_device_error(f"Read variable '{variable_name}'", e)

    def _write_variable(self, variable_name: str, variable_value: str, function: str = None) -> str:
        """
        Method to write the specified variable on the device

        :param variable_name:
                    Profinet output address, e.g. "QX0.1" - string

        :param variable_value:
                    The value of the bit to write, 0 or 1 - string

        :param function:
                    Not used

        :return:    value - string: the write-address register read back. Raises on failure
                    (503 when the device does not respond, 500 otherwise).

        :author:    tylerjm@flexxbotics.com
        :since:     ODOULS.3 (7.1.15.3)
        """
        try:
            # self._set_profinet_bit(variable_name, int(variable_value))
            self.write_pn_bit(variable_name, int(variable_value), self.CNC_NUMBER)
            return str(self._client.read_holding_register(self.modbus_write_profinet_address).registers[0])
        except Exception as e:
            self._raise_device_error(f"Write variable '{variable_name}'", e)

    def _run_program(self, function: str = None):
        """
            Method to run the active program on the device

            :param function:
                        Optional parameter to provide the name of a function to run - string

            :return:    value - string

            :author:    tylerjm@flexxbotics.com
            :since:     P.2 (7.1.16.2)
        """
        try:
            response = ""
        except Exception as e:
            response = "Error sending program"
            self._error(message=str(e))

        return response

    def _read_file_names(self) -> list:
        """
        Method to get a list of filenames from the device

        :return:    list of filenames

        :author:    tylerjm@flexxbotics.com
        :since:     KEYSTONE.4 (7.1.11.4)
        """

        # Return list of available filenames from the device
        self.programs = []

        return self.programs  # TODO is this the actual response object we want?

    def _write_file(self, file_name: str, file_data: str):
        """
        Method to write a file to a device

        :param file_name:
                    the name of the file to write.
        :param file_data:
                    the data of the file to write as base64 string.

        :author:    tylerjm@flexxbotics.com
        :since:     ODOULS.3 (7.1.15.3)
        """
        pass

    def _load_file(self, file_name: str):
        """
        Loads a file into memory on the device

        :param file_name: the name of the file to load into memory
        :return: {"status": "ok", "response": <the bridge's JSON reply>}. Raises on failure (503 when
                 the bridge does not respond within _BRIDGE_TIMEOUT_S, 500 when it replies with an
                 HTTP error or a reply that is not JSON).

        :author:    tylerjm@flexxbotics.com
        :since:     Q.5 (7.1.17.5)
        """
        try:
            # Connects to TNC Remo Server to load the file specified
            resp = requests.post(
                self.base_url + "/load",
                json={"filename": file_name, "ip_address": self.cnc_ip_address},
                timeout=self._BRIDGE_TIMEOUT_S,
            )
            try:
                body = resp.json()
            except ValueError:
                raise ServerErrorException(
                    f"Bridge returned a reply that is not JSON (HTTP {resp.status_code}): {resp.text[:200]}"
                )
            if not resp.ok:
                raise ServerErrorException(f"Bridge returned HTTP {resp.status_code}: {body}")
            return json.dumps({"status": "ok", "response": body}, default=str)
        except Exception as e:
            self._raise_device_error(f"Load file '{file_name}'", e)

    # ############################################################################## #
    # INTERFACE HELPER METHODS
    #
    # These are private methods with naming convention _some_method(self). These are used to faciliate
    # any specific functions that are needed to communicate via the driver. For example,
    # connection methods, read/write methods, specific functions, etc.
    # ############################################################################## #

    def _encode_qx(self,addr: str) -> int:
        """'QX0.1' -> doc-bit absolute index (0*8+1 = 1)."""
        byte, bit = addr.upper().replace("QX", "").split(".")
        return int(byte) * 8 + int(bit)

    def _encode_ix(self,addr: str) -> int:
        """'IX7.4' -> doc-bit absolute index (7*8+4 = 60)."""
        byte, bit = addr.upper().replace("IX", "").split(".")
        return int(byte) * 8 + int(bit)


    def write_pn_bit(self, qx_addr: str, value: int, cnc_num: int):
        """
        qx_addr like 'QX0.1' (DOC address), value 0/1, cnc_num 1..6.
        """
        self._ensure_connected()
        abs_addr = self._encode_qx(qx_addr)
        # Fill command registers
        self._client.write_multiple_registers(self.modbus_write_profinet_address, [abs_addr])  # QW0
        self._client.write_multiple_registers(self.modbus_write_profinet_value, [value])  # QW1
        self._client.write_multiple_registers(self.CNC_NUM_REG, [cnc_num])  # QW4

        # Fire one-shot trigger
        self._client.write_multiple_registers(self.modbus_trigger_write, [1])  # QW5

        # Optional: wait until PLC finished

    def _read_pn_bit(self, ix_addr: str, cnc_num: int) -> int:
        """
        ix_addr like 'IX7.4' (DOC address on input side), returns 0 or 1.
        """
        self._ensure_connected()
        abs_addr = self._encode_ix(ix_addr)
        # Set up read command
        self._client.write_multiple_registers(self.modbus_read_profinet_address, [abs_addr])  # QW2
        self._client.write_multiple_registers(self.CNC_NUM_REG, [cnc_num])  # QW4
        self._client.write_multiple_registers(self.modbus_trigger_read, [1])  # QW6

        # Read back result from QW3
        rr = self._client.read_holding_register(self.modbus_profinet_read_value_address)
        if rr.isError():
            raise RuntimeError(f"Modbus error reading PN input value: {rr}")
        return rr.registers[0]  # 0 or 1

    def _update_bits(self):
        for sig in self.bit_signals.values():
            sig.value = int(self._read_variable(sig.address))

    def _connect(self) -> None:
        """protocols.modbus connects and closes on every call, so this only creates the client."""
        if self._connected and self._client is not None:
            return
        self._client = modbus.ModbusTCP(self.PROFINET_PLC_IP, self.PLC_PORT)
        self._connected = True

    def _disconnect(self) -> None:
        """Nothing stays open between calls; drop the client. Safe to call twice."""
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
