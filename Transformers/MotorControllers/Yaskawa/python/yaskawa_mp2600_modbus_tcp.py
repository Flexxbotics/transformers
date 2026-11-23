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
from data_models.device import Device
import json
import base64
from werkzeug.exceptions import HTTPException
from transformers.abstract_device import AbstractDevice
from exceptions.flexxCoreExceptions import (
    FlexxCoreException,
    BadRequestException,
    ServerErrorException,
    ServiceUnavailableException,
)
from protocols.modbus import ModbusTCP
from types import SimpleNamespace

class YaskawaMP2600ModbusTCP(AbstractDevice):

    def __init__(self, device: Device):
        """
        Template device class. Inherits AbstractDevice class.

        :param Device:
                    the device object

        :return:    a new instance
        """
        # Get meta data of the device from its attributes, this contains information such as: ip address, ports, etc
        super().__init__(device)
        self.meta_data = device.metaData or {}
        self.address = self.meta_data.get("ip_address", "")
        self.port = int(self.meta_data.get("port") or 502)  # default is 502
        if not self.address:
            raise ValueError("YaskawaMP2600ModbusTCP: metaData must contain 'ip_address'")
        # The Modbus client is created by _connect (it connects per call)
        self._client = None
        self._connected = False

        self.BASE_BYTE = 73728
        self.MANUAL_CONTROL_COIL = 0
        self.modbus_map = SimpleNamespace(
            carousel_index =0,
            moving_status = self._qx_index(73728,1),
            enabled_status = self._qx_index(73728,0)
        )

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

        connect / disconnect
            Create / drop the Modbus client; connect also checks that the drive answers
            (reads the status inputs). Returns {"status": "ok", "connected": true|false}.
        set_carousel_index
            Args: index (int, required). Writes the carousel index register and reads it back.
            Returns {"status": "ok", "value": "<register value read back>"}.
        set_manual_control
            Args: value (bool, required). Writes the manual-control coil and reads it back.
            Returns {"status": "ok", "value": "manual_control state:<True|False>"}.

        Errors raise: 400 for an unknown command or bad args, 503 when the drive does not
        respond, 500 otherwise.

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

        self._info(message="Sending command: " + command_name)
        try:
            if command_name == "connect":
                self._connect()
                return json.dumps({"status": "ok", "connected": True})

            if command_name == "disconnect":
                self._disconnect()
                return json.dumps({"status": "ok", "connected": False})

            if command_name == "set_carousel_index":
                index = self._require(args, "index")
                try:
                    index = int(index)
                except (TypeError, ValueError):
                    raise BadRequestException(f"Arg 'index' must be an integer, got {index!r}")
                self._ensure_connected()
                response = self._write_modbus_holding_register(self.modbus_map.carousel_index, index)
                return json.dumps({"status": "ok", "value": response}, default=str)

            if command_name == "set_manual_control":
                b = self._as_bool(self._require(args, "value"))
                self._ensure_connected()
                response = self._write_modbus_coil(self.MANUAL_CONTROL_COIL, b)
                return json.dumps({"status": "ok", "value": response}, default=str)

        except Exception as e:
            self._raise_device_error(f"Command '{command_name}'", e)

        raise BadRequestException(f"Unknown command '{command_name}'")

    def _read_interval_data(self) -> str:
        """
        Method to read the status of the device on an interval

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

    def _read_status(self, function : str=None) -> str:
        """
        Method to read the status of the device

        :param function:
                    Optional parameter to provide the name of a function to run - string

        :return:    status - string

        :author:    tylerjm@flexxbotics.com
        :since:     ODOULS.3 (7.1.15.3)
        """
        try:
            self._ensure_connected()
            moving_status = self._read_modbus_discrete_register(1,1)[0]
            enabled_status = self._read_modbus_discrete_register(0,1)[0]
        except Exception as e:
            self._error(message=f"Status read failed: {e}")
            self._connected = False
            return "ERROR"

        if moving_status and enabled_status:
            status = "IDLE"
        elif not enabled_status and not moving_status:
            status = "RUNNING"
        else:
            status = "ERROR"
        # status = str(enabled_status)

        if function is None:
            # Write standard read status statements
            pass
        elif function == "": # Some string
            # Write specific function call to read status
            pass
        else:
            pass

        return status

    def _read_file_names(self) -> list:
        """
        Method to get a list of filenames from the device

        :return:    list of filenames

        :author:    tylerjm@flexxbotics.com
        :since:     KEYSTONE.4 (7.1.11.4)
        """

        # Return list of available filenames from the device
        self.programs = []

        return self.programs #TODO is this the actual response object we want?

    def _write_file(self, file_name : str, file_data : str):
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

    def _load_file(self, file_name : str):
        """
        Loads a file into memory on the device

        :param file_name: the name of the file to load into memory
        :return: the file name

        :author:    zacc@flexxbotics.com
        :since:     MODELO.3 (7.1.13.3)
        """
        pass

    # ############################################################################## #
    # INTERFACE HELPER METHODS
    #
    # These are private methods with naming convention _some_method(self). These are used to faciliate
    # any specific functions that are needed to communicate via the transformer. For example,
    # connection methods, read/write methods, specific functions, etc.
    # ############################################################################## #


    def _connect(self) -> None:
        """The Modbus wrapper connects per call: this only creates the client (no network I/O)."""
        if self._connected and self._client is not None:
            return
        self._client = ModbusTCP(ip_address=self.address, port=self.port)
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

    def _write_modbus_holding_register(self, address, value):
        wr = self._client.write_single_register(address=address, value=value)
        rr = self._client.read_holding_register(address=address, count=1)
        if rr.isError() or wr.isError():
            raise ServerErrorException(f"failed to write register {address}: {wr if wr.isError() else rr}")
        else:
            response = str(rr.registers[0])
        return response

    def _read_modbus_discrete_register(self, address,count):
        rr = self._client.read_discrete_inputs(address=address, count=count)
        if rr.isError():
            raise RuntimeError(f"Modbus error: {rr}")
        return rr.bits

    def _qx_index(self, byte_addr: int, bit: int) -> int:
        BASE = 73728
        return (byte_addr - BASE) * 8 + bit

    def _write_modbus_coil(self, address,value):
        wc = self._client.write_single_coil(address=address,value=value)
        rc = self._client.read_coils(address=address,count=1)
        if rc.isError():
            raise ServerErrorException(f"Error reading coil {address}: {rc}")
        else:
            response = "manual_control state:" + str(rc.bits[0])
        return response

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









