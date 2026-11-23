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

import os
import socket

# 3rd party python library imports
import xmlrpc.client
import json
from datetime import datetime, timedelta
import base64

from werkzeug.exceptions import HTTPException

from data_models.device import Device
from transformers.abstract_device import AbstractDevice
from exceptions.flexxCoreExceptions import (
    FlexxCoreException,
    BadRequestException,
    ServerErrorException,
    ServiceUnavailableException,
)


class _TimeoutTransport(xmlrpc.client.Transport):
    """XML-RPC transport with a socket timeout for this proxy only (socket.setdefaulttimeout is process-wide)."""

    def __init__(self, timeout: float):
        super().__init__()
        self._timeout = timeout

    def make_connection(self, host):
        connection = super().make_connection(host)
        connection.timeout = self._timeout
        return connection


class UR(AbstractDevice):
    """
    The device transformer for the Universal Robot.
    """

    # The XML-RPC methods the bundled URCap registers (extensions/connect.v2-8.1.16.5.urcap,
    # flexx_connect/flexx_connect.py: server.register_function(...)). Only these can be called.
    URCAP_METHODS = frozenset({
        "set_value", "get_value", "get_variable", "generate_random_number", "get_ur_scripts",
        "read_flexx_interface_commands", "send_flexx_interface_command", "read_flexx_core_commands",
        "load_robot_program", "get_loaded_program", "run_script_exporter", "play_program",
        "stop_program", "power_on", "brake_release", "set_freedrive", "end_freedrive",
        "send_program_file", "backup_active_program", "get_program_names", "get_program_file",
        "unlock_protective_stop", "restart_safety", "stopj_primary", "stopj_secondary",
        "stopl_primary", "stopl_secondary", "set_waypoint_to_current_pose",
        "set_standard_digital_output", "set_configurable_digital_output", "set_tool_digital_output",
        "move_to_joints", "close_popup", "get_flexx_feature_pose", "increase_part_count",
        "reset_part_count", "increase_failure_count", "set_failure_reason", "set_file_path",
        "reset_failure_count", "cycle_start", "cycle_end", "on_going_cycle_start",
        "on_going_cycle_end", "calculate_cycle", "get_part_count", "get_failure_count",
        "get_failure_reason", "get_file_path", "get_robot_state", "set_custom_status",
        "set_use_custom_status_false", "set_use_custom_status_true", "get_label", "set_label",
        "set_monitoring_speed", "activate_edge", "register_edge", "get_edge_registration_status",
        "connect_internet", "get_internet_status", "enable_remote_session", "disable_remote_session",
        "set_headless_mode", "get_headless_mode", "set_edge_ip_address", "get_edge_ip_address",
        "get_edge_connected",
    })
    # Command names that call a URCap method under another name.
    COMMAND_ALIASES = {"reset_safety": "restart_safety"}

    def __init__(self, device: Device):
        """
        Universal Robot device class.
        :param device:
                    the device object

        metaData: ip_address (required), port (default "49495"),
                  timeout (XML-RPC socket timeout in seconds, default 3)

        :return:    a new instance
        """
        super().__init__(device)

        # Setup specifics to the device interface
        self.meta_data = self.device.metaData or {}
        self.ip_address = self.meta_data.get("ip_address", "")
        self.port = str(self.meta_data.get("port", "49495"))
        self.timeout = float(self.meta_data.get("timeout") or 3)
        if not self.ip_address:
            raise ValueError("UR: metaData must contain 'ip_address'")
        self.address = self.ip_address + ":" + self.port
        # The XML-RPC proxy is created by _connect (no device I/O here)
        self.client = None
        self._connected = False
        self._programs_directory = (
                os.path.join(os.getcwd(), "temp_program_files") + os.sep
        )
        # The robot state is read on demand by _read_status (no device I/O here)
        self._robot_state = {}
        self._previous_cycle_end = ""
        self.previous_failure_count = self._robot_state.get("failure_count", 0)
        self.previous_part_count = self._robot_state.get("part_count", 0)

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

        connect / disconnect
            Create / drop the XML-RPC proxy; no call is made to the URCap.
            Returns {"status": "ok", "connected": true|false}.
        <URCap method> (one of URCAP_METHODS, or an alias in COMMAND_ALIASES)
            Calls that URCap method with the arg values as positional arguments, in the
            order given. move_to_joints uses args["timeout"] (seconds) as its socket timeout.
            Returns {"status": "ok", "response": <the URCap's reply>}.

        Errors raise: 400 for an unknown command or bad args, 503 when the URCap does not
        respond, 500 when it replies ">ERROR<" or fails.

        :param command_name:
                    the command to be executed
        :param command_args:
                    json with the arguments for the command

        :return:    the response after execution of command.
        """
        args = self._parse_args(command_args)
        self._info(message="Sending command: " + command_name)
        try:
            if command_name == "connect":
                self._connect()
                return json.dumps({"status": "ok", "connected": True})

            if command_name == "disconnect":
                self._disconnect()
                return json.dumps({"status": "ok", "connected": False})

            method = self.COMMAND_ALIASES.get(command_name, command_name)
            if method in self.URCAP_METHODS:
                self._ensure_connected()
                client = self.client
                # Move to joints requires a longer timeout
                if method == "move_to_joints":
                    client = self._new_client(float(args.get("timeout") or self.timeout))
                try:
                    response = getattr(client, method)(*args.values())
                finally:
                    if client is not self.client:
                        client("close")()
                self._check_reply(response, f"Command '{command_name}'")
                return json.dumps({"status": "ok", "response": response}, default=str)

        except Exception as e:
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
        Method to read the status of the robot

        :param function:
                    Ignored

        :return:    status - string
        """
        try:
            self._robot_state = self._get_state()
            status = self._robot_state.get("status", "")
            match status:
                case "true":
                    status = "RUNNING"
                case "false":
                    status = "IDLE"
                case "":
                    pass
        except Exception as e:
            self._error(message=f"Status read failed: {e}")
            self._connected = False
            return "ERROR"

        return status

    def _read_variable(self, variable_name: str, function: str = None) -> str:
        """
        Method to read the specified variable from the robot

        :param variable_name:
                    The URCap variable key - string

        :param function:
                    Ignored

        :return:    value - string: the URCap's get_variable reply. Raises on failure or ">ERROR<"
                    (variable not set).
        """
        try:
            self._ensure_connected()
            value = self.client.get_variable(variable_name)
            self._check_reply(value, f"Read variable '{variable_name}'")

            return str(value)
        except Exception as e:
            self._raise_device_error(f"Read variable '{variable_name}'", e)

    def _write_variable(self, variable_name: str, variable_value: str, function: str = None) -> str:
        """
        Method to write the specified variable on the robot

        :param variable_name:
                    The name of the variable to write - string

        :param variable_value:
                    The value of the variable to write - string

        :param function:
                    Ignored

        :return:    value - string: the URCap's reply (the value written). Raises on failure.
        """
        try:
            self._ensure_connected()
            value = self.client.set_value(variable_name, variable_value)
            self._check_reply(value, f"Write variable '{variable_name}'")

            return str(value)
        except Exception as e:
            self._raise_device_error(f"Write variable '{variable_name}'", e)

    def _write_parameter(self, parameter_name: str, parameter_value: str, function: str = None) -> str:
        """
        Method to write the specified parameter on the robot

        :param parameter_name:
                    The parameter to write - string

        :param parameter_value:
                    The value of the parameter to write - string

        :param function:
                    Ignored

        :return:    value - string: the URCap's reply (the value written). Raises on failure.
        """
        try:
            self._ensure_connected()
            value = self.client.set_value(parameter_name, parameter_value)
            self._check_reply(value, f"Write parameter '{parameter_name}'")

            return str(value)
        except Exception as e:
            self._raise_device_error(f"Write parameter '{parameter_name}'", e)

    def _read_parameter(self, parameter_name: str, function: str = None) -> str:
        """
        Method to read the specified parameter from the robot

        :param parameter_name:
                    The URCap value key - string

        :param function:
                    Ignored

        :return:    value - string: the URCap's get_value reply. Raises on failure or ">ERROR<"
                    (value not set).
        """
        try:
            self._ensure_connected()
            value = self.client.get_value(parameter_name)
            self._check_reply(value, f"Read parameter '{parameter_name}'")

            return str(value)
        except Exception as e:
            self._raise_device_error(f"Read parameter '{parameter_name}'", e)

    def _run_program(self, function: str = None) -> str:
        """
            Method to run the active program on the device (URCap play_program)

            :param function:
                        Ignored

            :return:    value - string: the URCap's play_program reply. Raises on failure or ">ERROR<".
        """
        self._info(message="UR- Sending command to run program...")
        try:
            self._ensure_connected()
            response = self.client.play_program()
            self._info(message="HexagonCMM - Ran program result: " + str(response))
            self._check_reply(response, "Run program")
        except Exception as e:
            self._error(message=str(e))
            self._raise_device_error("Run program", e)

        return str(response)

    def _read_file_names(self) -> list:
        """
        Method to get a list of filenames from the device

        :return:    list of filenames
        """

        # Return list of available filenames from the device
        self.programs = []

        try:
            self._ensure_connected()
            response = json.loads(self.client.get_program_names())
            self.programs = response["filenames"]
        except Exception as e:
            self.programs = []
            self.status = "ROBOT_DISCONNECTED"
            self._error(str(e))
            self._raise_device_error("Read file names", e)

        return self.programs  # TODO is this the actual response object we want?

    def _read_file(self, file_name: str) -> str:
        """
        Method to read a file from a device

        :param file_name:
                    the name of the file to read.

        :return:    the file's data as base64 string
        """
        # Reads the file content off the device
        try:
            file_data = self._transfer_file_from_device(file_name)

            return base64.b64encode(file_data).decode("utf-8")
        except Exception as e:
            self._raise_device_error(f"Read file '{file_name}'", e)

    def _write_file(self, file_name: str, file_data: str):
        """
        Method to write a file to a device

        :param file_name:
                    the name of the file to write.
        :param file_data:
                    the data of the file to write as base64 string
        """
        try:
            self._transfer_file_to_device(file_name=file_name, file_data=file_data)
        except Exception as e:
            self._raise_device_error(f"Write file '{file_name}'", e)

    def _load_file(self, file_name: str):
        """
        Loads a file into memory on the device (URCap load_robot_program)

        :param file_name: the name of the file to load into memory
        :return: None. Raises on failure or ">ERROR<".
        """

        # Load the file into memory on the UR
        try:
            self._ensure_connected()
            response = self.client.load_robot_program(file_name)
            self._check_reply(response, f"Load file '{file_name}'")
        except Exception as e:
            self._raise_device_error(f"Load file '{file_name}'", e)

    # ############################################################################## #
    # INTERFACE HELPER METHODS
    # ############################################################################## #

    def _connect(self) -> None:
        """XML-RPC is per request: this only creates the proxy (no network I/O)."""
        if self._connected and self.client is not None:
            return
        if self.client is None:
            self.client = self._new_client(self.timeout)
        self._connected = True

    def _disconnect(self) -> None:
        try:
            if self.client is not None:
                self.client("close")()
        finally:
            self.client = None
            self._connected = False

    def _ensure_connected(self) -> None:
        if not self._connected or self.client is None:
            self._connect()

    def _new_client(self, timeout: float) -> xmlrpc.client.ServerProxy:
        """An XML-RPC proxy to the URCap whose calls time out after `timeout` seconds."""
        return xmlrpc.client.ServerProxy(
            "http://" + self.address + "/RPC2", transport=_TimeoutTransport(timeout)
        )

    @staticmethod
    def _check_reply(reply, action: str) -> None:
        """The URCap answers ">ERROR<" when a call fails on the robot; raise ServerErrorException (HTTP 500) for that reply."""
        if isinstance(reply, str) and ">ERROR<" in reply:
            raise ServerErrorException(f"{action}: URCap returned {reply}")

    def _convert_hms_string_to_microseconds(self, hms_time_string):
        """
        Method to convert the UR's cycle time from hh:mm:ss.ssssss to microseconds for timedelta

        :param hms_time_string:
                    the time as a string: to convert.

        :return:    the converted time in microseconds
        """
        # Convert the cycle time from hh:mm:ss.mmmm to milliseconds
        hours, minutes, seconds = hms_time_string.split(":")
        seconds, milliseconds = seconds.split(".")

        # only take 3 significant milliseconds
        milliseconds = milliseconds[0:3]

        # Convert the values to integers
        hours, minutes, seconds, milliseconds = map(
            int, (hours, minutes, seconds, milliseconds)
        )

        # Convert the split time segments into milliseconds and then multiple by 1000 to get microseconds (for
        # timedelta)
        return ((hours * 3600 + minutes * 60 + seconds) * 1000 + milliseconds) * 1000

    def _get_state(self):
        """
        Method to get state data from the universal robot

        :return:    the state as a dict (the parsed get_robot_state reply); {"status": "ROBOT_DISCONNECTED"}
                    when the connection is refused or there is no route to the robot, {} on any other
                    failure. Never raises.
        """
        try:
            self._ensure_connected()
            response = self.client.get_robot_state()
            state = json.loads(response)
        except Exception as e:
            state = {}
            if (
                    "No connection could be made because the target machine actively refused it"
                    in str(e)
                    or "No route to host" in str(e)
                    or "Connection refused" in str(e)
            ):
                self._error(
                    "Connection to Universal Robot Failed. Setting ROBOT_DISCONNECTED status"
                )
                state = {"status": "ROBOT_DISCONNECTED"}
            self._error(str(e))

        return state

    def _transfer_file_to_device(self, file_name, file_data):
        """
        Method to copy a program file to the device.

        :param file_name:
            the name of the file

        :param file_data:
            the file data

        :return:    the file data
        """
        self._info("transferring program file")
        try:

            decoded_string = base64.b64decode(file_data)
            """
            xmlrpc is the server on UR's side. the file must be converted back to 
            binary using xmlrpc.client.Binary or the file will not format correctly. 
            """
            binary_data = xmlrpc.client.Binary(decoded_string)
            self._ensure_connected()
            self.client.send_program_file(file_name, binary_data)
            self._info("transfer program file complete")

        except Exception as e:
            self._error(str(e))
            raise

        return file_data

    def _transfer_file_from_device(self, file_name):
        """
        Method to copy a program file off the device and save it to a local backup location.

        :param file_name:
                    the name of the file to transfer

        :return:    the data associated with the named file.
        """
        self._info("backing up program file")
        program_file_path = self._programs_directory + file_name
        try:
            self._ensure_connected()
            program_file_binary = self.client.get_program_file(file_name)
            with open(program_file_path, "wb") as handle:
                handle.write(program_file_binary.data)

            with open(program_file_path, "rb") as program_file:
                data = program_file.read()
                return data
        except Exception as e:
            self._error(str(e))
            raise

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
