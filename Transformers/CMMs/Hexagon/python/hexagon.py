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
from protocols.tcp import TCP
import json
import base64
from transformers.abstract_device import AbstractDevice
from werkzeug.exceptions import HTTPException
from exceptions.flexxCoreExceptions import (
    FlexxCoreException,
    BadRequestException,
    ServerErrorException,
    ServiceUnavailableException,
)


class Hexagon(AbstractDevice):

    def __init__(self, device: Device):
        """
        Hexagon CMM device class (JSON messages over TCP to the Hexagon bridge).

        :param device:
                    the device object

        :return:    a new instance
        """
        # Get meta data of the device from its attributes, this contains information such as: ip address, ports, etc
        super().__init__(device)
        self.meta_data = device.metaData or {}
        self.address = self.meta_data.get("ip_address", "")
        self.port = str(self.meta_data.get("port", ""))
        if not self.address:
            raise ValueError("Hexagon: metaData must contain 'ip_address'")
        if not self.port:
            raise ValueError("Hexagon: metaData must contain 'port'")

        # protocols.tcp.TCP opens and closes a socket on every send(); _connect() creates the client
        self.client = None
        self._connected = False
        self.active_program = ""
        self.parameters = {}

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
            Returns: {"status": "ok", "connected": true|false}
        delete_programs
            Sends delete_programs for the loaded program; the reply is not checked.
            Returns: {"status": "ok"}
        pass
            Sends pass for the loaded program; the reply is not checked.
            Returns: {"status": "ok", "value": "<bridge reply>"}
        clear_parameters
            Clears the trace fields sent with the next run. Returns: {"status": "ok"}
        run_program
            Runs the loaded program. Returns: {"status": "ok", "value": "routineRunning"}

        Errors raise: 400 for bad args or an unknown command, 500 when run_program does
        not get "routineRunning" back (including when the bridge does not respond) or
        anything else fails. protocols.tcp.TCP does not raise network errors, so there
        is no 503.

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
            elif command_name == "disconnect":
                self._disconnect()
                return json.dumps({"status": "ok", "connected": False})
            elif command_name == "delete_programs":
                cmm_command = {
                    "type": "delete_programs",
                    "action": "run",
                    "name": self.active_program,
                    "traceFields": {}
                }
                data = json.dumps(cmm_command)
                self._ensure_connected()
                response = self.client.send(data=data, encoding="utf-8", response_time=0.5)
                self._info(message="Sent command. Returning OK")
                return json.dumps({"status": "ok"})
            elif command_name == "pass":
                cmm_command = {
                    "type": "pass",
                    "name": self.active_program,
                    "traceFields": {}
                }
                data = json.dumps(cmm_command)
                self._ensure_connected()
                response = self.client.send(data=data, encoding="utf-8", response_time=0.5)
                self._info(message="Sent command. Returning OK")
                return json.dumps({"status": "ok", "value": str(response)})
            elif command_name == "clear_parameters":
                self.parameters = {}
                return json.dumps({"status": "ok"})
            elif command_name == "run_program":
                result = self._run_program()
                if result != "routineRunning":
                    raise ServerErrorException(str(result))
                return json.dumps({"status": "ok", "value": result})
        except Exception as e:
            self._error(message="Failed to send command: " + str(e))
            self._raise_device_error(f"Command '{command_name}'", e)

        raise BadRequestException(f"Unknown command '{command_name}'")

    def _read_interval_data(self) -> str:
        """
        Not used by this transformer: does nothing.

        :return:    None
        """
        pass

    def _read_status(self, function: str = None) -> str:
        """
        Method to read the status of the device

        :param function:
                    Ignored

        :return:    status - "IDLE", "RUNNING", "FAULT", "" for any other bridge status,
                    or "ERROR" when the bridge cannot be read
        """
        status = ""
        try:
            cmm_command = {
                "type": "status",
            }
            data = json.dumps(cmm_command)
            self._ensure_connected()
            result = self.client.send(data=data, encoding="ascii", response_time=0.5)
            cmm_response = json.loads(result)
            cmm_status = cmm_response["status"]
            if cmm_status == "routineComplete" or cmm_status == "waiting":
                return "IDLE"
            elif cmm_status == "routineRunning":
                return "RUNNING"
            elif cmm_status == "error":
                return "FAULT"
        except Exception as e:
            self._error(message=f"Status read failed: {e}")
            self._connected = False
            return "ERROR"

        return status

    def _read_variable(self, variable_name: str, function: str = None) -> str:
        """
        Method to read the specified variable from the device

        :param variable_name:
                    The measurement name to ask the bridge for - string

        :param function:
                    Ignored

        :return:    value - string. Raises 500 on failure, including when the bridge does
                    not respond.
        """
        try:
            cmm_command = {
                "type": "measurement",
                "name": variable_name,
            }
            data = json.dumps(cmm_command)
            self._ensure_connected()
            result = self.client.send(data=data, encoding="utf-8", response_time=0.5)
            cmm_response = json.loads(result)
            value = cmm_response["value"]
            return str(value)
        except Exception as e:
            self._raise_device_error(f"Read variable '{variable_name}'", e)

    def _run_program(self, function: str = None) -> str:
        """
        Method to run the active program on the device, sending the stored parameters
        as trace fields

        :param function:
                    Ignored

        :return:    value - string: "routineRunning" when the bridge started the routine,
                    otherwise "Error running program". Never raises.
        """
        self._info(message="HexagonCMM - Sending command to run program...")
        cmm_command = {
            "type": "routine",
            "action": "run",
            "name": self.active_program,
            "traceFields": self.parameters,
        }
        try:
            data = json.dumps(cmm_command)
            self._ensure_connected()
            response = self.client.send(data=data, encoding="utf-8", response_time=0.5)
            cmm_response = json.loads(response)
            if cmm_response["status"] == "routineRunning":
                self._info(
                    message="HexagonCMM - Ran program result: "
                    + str(cmm_response["status"])
                )
                return cmm_response["status"]
            else:
                return "Error running program"
        except Exception as e:
            response = "Error running program"
            self._error(message=str(e))

        return response

    def _write_parameter(
        self, parameter_name: str, parameter_value: str, function: str = None
    ) -> str:
        """
        Method to store the specified parameter as a trace field for the next run_program
        (nothing is sent to the device)

        :param parameter_name:
                    The parameter to write - string

        :param parameter_value:
                    The value of the parameter to write - string

        :param function:
                    Ignored
        :return:    value - string: "Parameter <name> wrote to value <value>"
        """
        self.parameters[parameter_name] = parameter_value

        return f"Parameter {parameter_name} wrote to value {parameter_value}"

    def _read_file_names(self) -> list:
        """
        Not implemented: does not contact the bridge.

        :return:    an empty list
        """

        # Return list of available filenames from the device
        self.programs = []

        return self.programs  # TODO is this the actual response object we want?

    def _write_file(self, file_name: str, file_data: str):
        """
        Not implemented: does nothing.

        :param file_name:
                    the name of the file to write.
        :param file_data:
                    the data of the file to write as base64 string.
        """
        pass

    def _load_file(self, file_name: str):
        """
        Sets the active program and sends a load_program request to the bridge
        (the reply is not checked)

        :param file_name: the name of the file to load into memory
        :return: "OK", also when the bridge does not answer; "Error loading program" only when
                 building the request or the TCP client raises. Never raises.
        """
        self.active_program = file_name
        self._info(message="HexagonCMM - Sending command to run program...")
        cmm_command = {
            "type": "load_program",
            "action": "run",
            "name": self.active_program,
            "traceFields": {},
        }
        try:
            data = json.dumps(cmm_command)
            self._ensure_connected()
            response = self.client.send(data=data, encoding="utf-8", response_time=0.5)
            return "OK"

        except Exception as e:
            response = "Error loading program"
            self._error(message=str(e))

        return response



    # ############################################################################## #
    # INTERFACE HELPER METHODS
    #
    # These are private methods with naming convention _some_method(self). These are used to faciliate
    # any specific functions that are needed to communicate via the transformer. For example,
    # connection methods, read/write methods, specific functions, etc.
    # ############################################################################## #

    def _connect(self) -> None:
        if self._connected and self.client is not None:
            return
        # No probe: protocols.tcp.TCP opens and closes a socket on every send()
        self.client = TCP(address=self.address, port=self.port)
        self._connected = True

    def _disconnect(self) -> None:
        # Nothing is held open between calls; just drop the client
        self.client = None
        self._connected = False

    def _ensure_connected(self) -> None:
        if not self._connected or self.client is None:
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
