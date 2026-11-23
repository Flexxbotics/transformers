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
from transformers.abstract_device import AbstractDevice
import os
import shutil
import time

from werkzeug.exceptions import HTTPException
from exceptions.flexxCoreExceptions import (
    FlexxCoreException,
    BadRequestException,
    ServerErrorException,
    ServiceUnavailableException,
)


class Okuma(AbstractDevice):
    """
    Okuma OSP transformer. Talks to the Flexxbotics THINC server on the CNC over TCP, one
    connection per call (protocols.tcp.TCP.send opens and closes the socket each time).

    Connection parameters (metaData)
    ---------------------------------
    ip_address : IP address of the THINC server (required)
    port       : TCP port of the THINC server (required)

    Errors
    ------
    Failures raise the runtime's exceptions, which the API returns as HTTP errors carrying the
    message: BadRequestException (400) for bad args or an unknown command,
    ServiceUnavailableException (503) when the device does not respond, ServerErrorException (500)
    for anything else.  _read_status never raises (it returns "ERROR").
    """

    # v2 command -> (THINC command, required arg keys). The line sent is the THINC command followed
    # by each arg value in this order, separated by ":", e.g. "SET_TOOL_OFFSET:<tool_num>:<tool_comp>:<offset_value>".
    _THINC_COMMANDS = {
        "read_machine_offset": ("GET_MACHINE_OFFSET", ("offset_axis",)),
        "add_machine_offset": ("ADD_MACHINE_OFFSET", ("offset_axis", "offset_value")),
        "set_machine_offset": ("SET_MACHINE_OFFSET", ("offset_axis", "offset_value")),
        "get_current_tool": ("GET_CURRENT_TOOL", ()),
        "read_tool_offset": ("GET_TOOL_OFFSET", ("tool_num", "tool_comp")),
        "add_tool_offset": ("ADD_TOOL_OFFSET", ("tool_num", "tool_comp", "offset_value")),
        "set_tool_offset": ("SET_TOOL_OFFSET", ("tool_num", "tool_comp", "offset_value")),
        "set_cutter_comp_offset": ("SET_CUTTER_COMP_OFFSET", ("tool_num", "tool_comp", "offset_value")),
        "add_cutter_comp_offset": ("ADD_CUTTER_COMP_OFFSET", ("tool_num", "tool_comp", "offset_value")),
        "set_cutter_comp_wear_offset": ("SET_CUTTER_COMP_WEAR_OFFSET", ("tool_num", "tool_comp", "offset_value")),
        "add_cutter_comp_wear_offset": ("ADD_CUTTER_COMP_WEAR_OFFSET", ("tool_num", "tool_comp", "offset_value")),
        "set_cutter_offset": ("SET_CUTTER_OFFSET", ("tool_num", "tool_comp", "offset_value")),
        "add_cutter_offset": ("ADD_CUTTER_OFFSET", ("tool_num", "tool_comp", "offset_value")),
        "set_cutter_wear_offset": ("SET_CUTTER_WEAR_OFFSET", ("tool_num", "tool_comp", "offset_value")),
        "add_cutter_wear_offset": ("ADD_CUTTER_WEAR_OFFSET", ("tool_num", "tool_comp", "offset_value")),
        "read_workpiece_offset": ("GET_WORKPIECE_OFFSET", ("offset_axis", "axis_index")),
        "add_workpiece_offset": ("ADD_WORKPIECE_OFFSET", ("offset_axis", "axis_index", "offset_value")),
        "set_workpiece_offset": ("SET_WORKPIECE_OFFSET", ("offset_axis", "axis_index", "offset_value")),
        # Set program but don't load it
        "set_program": ("SET_PROGRAM", ("name",)),
        "get_remaining_tool_life": ("GET_REMAINING_TOOL_LIFE", ("tool",)),
        "get_tool_life": ("GET_TOOL_LIFE", ("tool",)),
        "get_active_tool": ("GET_ACTIVE_TOOL", ()),
        "get_axis_load": ("GET_AXIS_LOAD", ("axis_index",)),
        "get_spindle_load": ("GET_SPINDLE_LOAD", ()),
    }

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
        self.port = str(self.meta_data.get("port", ""))

        if not self.address:
            raise ValueError("Okuma: metaData must contain 'ip_address'")
        if not self.port:
            raise ValueError("Okuma: metaData must contain 'port'")

        self.client = None
        self._connected = False

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

        get_all_tool_life
            Reads the remaining tool life of tools 1..num_tools and sends each one as the
            variable event "tool_<n>_life".
            Args: num_tools (int, required)
            Returns: {"status": "ok", "command": "get_all_tool_life", "response": "OK"}

        Every command in _THINC_COMMANDS
            Sends the THINC command with its args. All listed args are required; numbers are
            accepted as well as strings.
            Returns: {"status": "ok", "command": "<command_name>", "response": "<THINC reply>"}

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

            if command_name == "get_all_tool_life":
                num_tools = self._require(args, "num_tools")
                self._ensure_connected()
                response = self._get_all_tool_life(number_of_tools=num_tools)
                return json.dumps({"status": "ok", "command": command_name, "response": response})

            if command_name in self._THINC_COMMANDS:
                thinc_name, arg_keys = self._THINC_COMMANDS[command_name]
                thinc_command = ":".join([thinc_name] + [str(self._require(args, key)) for key in arg_keys])
                self._ensure_connected()
                response = self.client.send(
                    data=thinc_command + "\r\n", encoding="ascii", response_time=0.5
                )
                return json.dumps({"status": "ok", "command": command_name, "response": response}, default=str)

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
        pass

    def _read_status(self, function: str = None) -> str:
        """
        Method to read the status of the device

        :param function:
                    Not used

        :return:    status - string: the THINC server's GET_STATUS reply, or "ERROR" when it
                    cannot be read (never raises)

        :author:    tylerjm@flexxbotics.com
        :since:     ODOULS.3 (7.1.15.3)
        """
        try:
            self._ensure_connected()
            data = "GET_STATUS" + "\r\n"
            return self.client.send(data=data, encoding="utf-8", response_time=0.5)
        except Exception as e:
            self._error(message=f"Status read failed: {e}")
            self._connected = False
            return "ERROR"

    def _read_variable(self, variable_name: str, function: str = None) -> str:
        """
        Method to read the specified variable from the device

        :param variable_name:
                    The name of the variable to read, as the THINC server's GET_VAR takes it - string

        :param function:
                    Not used

        :return:    value - string. Raises on failure (503 when the device does not respond,
                    500 otherwise).

        :author:    tylerjm@flexxbotics.com
        :since:     ODOULS.3 (7.1.15.3)
        """
        try:
            self._ensure_connected()
            data = "GET_VAR:" + str(variable_name) + "\r\n"
            return self.client.send(data=data, encoding="utf-8", response_time=0.5)
        except Exception as e:
            self._raise_device_error(f"Read variable '{variable_name}'", e)

    def _write_variable(
        self, variable_name: str, variable_value: str, function: str = None
    ) -> str:
        """
        Method to write the specified variable on the device

        :param variable_name:
                    The name of the variable to write - string

        :param variable_value:
                    The value of the variable to write - string

        :param function:
                    Not used

        :return:    value - string: the THINC server's reply. Raises on failure (503 when the
                    device does not respond, 500 otherwise).

        :author:    tylerjm@flexxbotics.com
        :since:     ODOULS.3 (7.1.15.3)
        """
        try:
            self._ensure_connected()
            data = "SET_VAR:" + str(variable_name) + ":" + str(variable_value) + "\r\n"
            return self.client.send(data=data, encoding="ascii", response_time=0.5)
        except Exception as e:
            self._raise_device_error(f"Write variable '{variable_name}'", e)

    def _write_parameter(
        self, parameter_name: str, parameter_value: str, function: str = None
    ) -> str:
        """
        Method to write the specified parameter on the device

        :param parameter:
                    The parameter to write - string

        :param parameter:
                    The value of the parameter to write - string

        :param function:
                    Not used

        :return:    value - string. Parameters are THINC variables (SET_VAR), so this is
                    _write_variable.

        :author:    tylerjm@flexxbotics.com
        :since:     ODOULS.3 (7.1.15.3)
        """
        return self._write_variable(parameter_name, parameter_value, function)

    def _read_parameter(self, parameter_name: str, function: str = None) -> str:
        """
        Method to read the specified parameter from the device

        :param parameter:
                    The parameter to write - string

        :param function:
                    Not used

        :return:    value - string. Parameters are THINC variables (GET_VAR), so this is
                    _read_variable.

        :author:    tylerjm@flexxbotics.com
        :since:     ODOULS.3 (7.1.15.3)
        """
        return self._read_variable(parameter_name, function)

    def _load_file(self, file_name: str):
        """
        Loads a file into memory on the device

        :param file_name: the name of the file to load into memory
        :return: the THINC server's reply. Raises on failure (503 when the device does not
                 respond, 500 otherwise).

        :author:    cadenc@flexxbotics.com
        :since:     PBR.6 (7.1.16.6)
        """
        try:
            self._ensure_connected()
            data = "SELECT_PROGRAM:" + str(file_name) + "\r\n"
            return self.client.send(data=data, encoding="ascii", response_time=0.5)
        except Exception as e:
            self._raise_device_error(f"Load file '{file_name}'", e)

    def _get_all_tool_life(self, number_of_tools):
        """
        Loads a file into memory on the device

        :param number_of_tools: the number of tools to get tool life for
        :return: success

        :author:    tylerjm@flexxbotics.com
        :since:     PBR.6 (7.1.16.6)
        """
        number_of_tools = int(number_of_tools)
        try:
            for tool in range(1, number_of_tools + 1):

                thinc_command = (
                        "GET_REMAINING_TOOL_LIFE:"
                        + str(tool)
                )

                try:
                    result = self.client.send(
                        data=thinc_command + "\r\n", encoding="ascii", response_time=0.5
                    )
                    time.sleep(0.1)
                    self._send_variable_event(device_id=self.device_id,
                                            variable_name="tool_"+str(tool)+"_life",
                                            value=result.strip())
                except Exception as e:
                    self._logger.error(str(e))
        except Exception as e:
            self._logger.error(str(e))

        return "OK"

    # ############################################################################## #
    # INTERFACE HELPER METHODS
    #
    # These are private methods with naming convention _some_method(self). These are used to faciliate
    # any specific functions that are needed to communicate via the transformer. For example,
    # connection methods, read/write methods, specific functions, etc.
    # ############################################################################## #

    def _connect(self) -> None:
        """TCP.send opens and closes its own socket on every call, so this only creates the client."""
        if self._connected and self.client is not None:
            return
        self.client = TCP(address=self.address, port=self.port, timeout=5)
        self._connected = True

    def _disconnect(self) -> None:
        """Nothing stays open between calls; drop the client. Safe to call twice."""
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
