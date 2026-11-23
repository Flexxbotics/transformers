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
from dataclasses import dataclass, field
from typing import Callable, Any
from enum import Enum
from werkzeug.exceptions import HTTPException
from exceptions.flexxCoreExceptions import (
    FlexxCoreException,
    BadRequestException,
    ServerErrorException,
    ServiceUnavailableException,
)
from repos.run_records.run_record_document import RunRecordDocument

@dataclass(frozen=True)
class LaserCommand:
    command: str
    min_args: int = 0
    max_args: int | None = 0
    response_codes: dict[str, str] = field(default_factory=dict)

    def build(self, *args: Any) -> str:
        self.validate_args(args)
        if args:
            return f"{self.command}:{';'.join(str(a) for a in args)}" + "\r\n"
        return self.command + "\r\n"

    def validate_args(self, args: tuple[Any, ...]) -> None:
        argc = len(args) if args else 0

        if argc < self.min_args:
            raise ValueError(
                f"{self.command} requires at least {self.min_args} arg(s), got {argc}"
            )

        if self.max_args is not None and argc > self.max_args:
            raise ValueError(
                f"{self.command} allows at most {self.max_args} arg(s), got {argc}"
            )

    def parse(self, raw: str) -> dict:
        parts = raw.strip().split(":")
        if len(parts) < 2:
            raise ServerErrorException(f"{self.command}: unexpected reply from laser: {raw!r}")
        data = parts[1].split(";")
        code = data[0]
        return {
            "code": code,
            "meaning": self.response_codes.get(code, "UNKNOWN"),
            "extra": data[1:] if len(data) > 1 else [],
        }


class FobaCmd(Enum):
    WRITE = LaserCommand(
        "SETVAR",
        min_args=2,
        max_args=2,
        response_codes={
            "1": "OK",
            "-2": "NOT FOUND",
            "-6": "INVALID FORMAT",
        },
    )
    READ = LaserCommand(
        "GETVAR",
        min_args=1,
        max_args=1,
        response_codes={
            "1": "OK",
            "-2": "NOT FOUND",
            "-6": "INVALID FORMAT",
        },
    )
    STATUS = LaserCommand(
        "GETSTATUS",
        response_codes={
            "1": "IDLE",
            "-1": "OFFLINE",
            "-5": "RUNNING",
            "-6": "INVALID FORMAT",
        },
    )
    RUN = LaserCommand(
        "STARTJOB",
        response_codes={
            "1": "OK",
            "-1": "ERROR",
            "-2": "NO JOB",
            "-4": "LOT PROCESSED",
            "-5": "BUSY",
            "-6": "INVALID FORMAT"
        }
    )
    LOAD = LaserCommand(
        "LOADJOB",
        response_codes={
            "1": "OK",
            "-2": "FILE NOT FOUND",
            "-5": "BUSY",
            "-6": "INVALID FORMAT"
        },
        min_args=1,
        max_args=1
    )
    GETALIGNMENTSTATUS = LaserCommand(
        "GETALIGNMENTSTATUS",
        response_codes={
            "1": "OK",
            "-2": "ITEM NOT FOUND",
            "-6": "INVALID FORMAT"
        },
        min_args=1,
        max_args=2
    )


class FOBA(AbstractDevice):

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
            raise ValueError("FOBA: metaData must contain 'ip_address'")
        if not self.port:
            raise ValueError("FOBA: metaData must contain 'port'")

        # protocols.tcp.TCP opens and closes a socket on every send(); _connect() creates the client
        self.client = None
        self._connected = False
        self.variable_service = self.core_services.variable_service
        self.alignment_data = {}

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
        get_alignment_status
            Args: layout_position (optional, default 0), mark_set_index (optional, default 0)
            Returns: {"status": "ok", "value": "OK" | "NotProcessed"}
        get_alignment_data
            Args: data (required) - a key of the last alignment result, e.g. "x_offset"
            Returns: {"status": "ok", "value": "<value>"}
        get_active_order
            Returns: {"status": "ok", "value": "<run record name>" | ""}
        run_program
            Starts the loaded job. Returns: {"status": "ok", "value": "OK"}

        Errors raise: 400 for missing args or an unknown command, 503 when the laser
        does not respond, 500 when it replies with an error.

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
            if command_name == "get_alignment_status":
                layout_position = args.get("layout_position", 0)
                mark_set_index = args.get("mark_set_index", 0)
                data = FobaCmd.GETALIGNMENTSTATUS.value.build(layout_position, mark_set_index)
                self._info(message=data)
                self._ensure_connected()
                raw = self.client.send(data=data, encoding="ascii", response_time=0.5)
                command_response = FobaCmd.GETALIGNMENTSTATUS.value.parse(raw)
                self._info(message=command_response)
                if command_response["meaning"] != "OK":
                    raise ServerErrorException(
                        f"GETALIGNMENTSTATUS failed: {command_response['meaning']} ({command_response['code']})"
                    )
                extra = command_response["extra"]
                if extra[0] == "NotProcessed":
                    return json.dumps({"status": "ok", "value": "NotProcessed"})
                self.alignment_data = {
                    "result": extra[0],
                    "type": extra[1],
                    "error": extra[2],
                    "x_offset": extra[3],
                    "y_offset": extra[4],
                    "angle": extra[5],
                    "x_scale": extra[6],
                    "y_scale": extra[7],
                    "x_shear": extra[8],
                    "y_shear": extra[9],
                    "x_trapezoid": extra[10],
                    "y_trapezoid": extra[11],
                    "duration": extra[12]
                }
                self._info(self.alignment_data)
                #self.variable_service.update_device_variable_latest_value_by_name("69dd7a3dfca98ab7d3e71932", "result", self.alignment_data["result"])
                #self.variable_service.update_device_variable_latest_value_by_name("69dd7a3dfca98ab7d3e71932", "x_offset", self.alignment_data["x_offset"])
                #self.variable_service.update_device_variable_latest_value_by_name("69dd7a3dfca98ab7d3e71932", "y_offset", self.alignment_data["y_offset"])
                #self.variable_service.update_device_variable_latest_value_by_name("69dd7a3dfca98ab7d3e71932", "error", self.alignment_data["error"])
                #self.variable_service.update_device_variable_latest_value_by_name("69dd7a3dfca98ab7d3e71932", "angle", self.alignment_data["angle"])
                return json.dumps({"status": "ok", "value": command_response["meaning"]})
            if command_name == "get_alignment_data":
                data_index = self._require(args, "data")
                if data_index not in self.alignment_data:
                    raise BadRequestException(
                        f"No alignment data '{data_index}' (run get_alignment_status first)"
                    )
                return json.dumps({"status": "ok", "value": self.alignment_data[data_index]}, default=str)
            if command_name == "get_active_order":
                work_cell = self._work_cell_service.get_work_cell()
                run_record = RunRecordDocument.objects(
                    workCellId=str(work_cell.id), isCompleted=False
                ).first()
                return json.dumps({"status": "ok", "value": str(run_record.name) if run_record else ""})
            if command_name == "run_program":
                status = self._run_program()
                if status != "OK":
                    raise ServerErrorException(f"STARTJOB failed: {status}")
                return json.dumps({"status": "ok", "value": status})

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
                    Ignored

        :return:    status - the laser's GETSTATUS meaning ("IDLE", "RUNNING", "OFFLINE", ...),
                    or "ERROR" when the laser cannot be read

        :author:    tylerjm@flexxbotics.com
        :since:     ODOULS.3 (7.1.15.3)
        """
        try:
            self._ensure_connected()
            data = FobaCmd.STATUS.value.build()
            raw = self.client.send(data=data, encoding="ascii", response_time=0.5)
            return FobaCmd.STATUS.value.parse(raw=raw)["meaning"]
        except Exception as e:
            self._error(message=f"Status read failed: {e}")
            self._connected = False
            return "ERROR"

    def _read_variable(self, variable_name: str, function: str = None) -> str:
        """
        Method to read the specified variable from the device

        :param variable_name:
                    The Mark-US variable name (GETVAR) - string

        :param function:
                    Ignored

        :return:    value - string. Raises on failure (503 when the laser does not respond,
                    500 when it replies with an error).

        :author:    tylerjm@flexxbotics.com
        :since:     ODOULS.3 (7.1.15.3)
        """
        try:
            self._ensure_connected()
            data = FobaCmd.READ.value.build(variable_name)
            raw = self.client.send(data=data, encoding="ascii", response_time=0.5)
            result = FobaCmd.READ.value.parse(raw)
            if result["meaning"] != "OK":
                raise ServerErrorException(
                    "Error reading variable '"
                    + str(variable_name)
                    + "' from device: "
                    + result["meaning"]
                )
            return result["extra"][0] if result["extra"] else ""
        except Exception as e:
            self._raise_device_error(f"Read variable '{variable_name}'", e)

    def _write_variable(
        self, variable_name: str, variable_value: str, function: str = None
    ) -> str:
        """
        Method to write the specified variable on the device

        :param variable_name:
                    The Mark-US variable name (SETVAR) - string

        :param variable_value:
                    The value of the variable to write - string

        :param function:
                    Ignored

        :return:    the written value - string. Raises on failure.

        :author:    tylerjm@flexxbotics.com
        :since:     ODOULS.3 (7.1.15.3)
        """
        try:
            self._ensure_connected()
            data = FobaCmd.WRITE.value.build(variable_name, variable_value)
            raw = self.client.send(data=data, encoding="ascii", response_time=0.5)
            result = FobaCmd.WRITE.value.parse(raw)
            if result["meaning"] != "OK":
                raise ServerErrorException(
                    "Error writing variable '"
                    + str(variable_name)
                    + "' to device: "
                    + result["meaning"]
                )
            return str(variable_value)
        except Exception as e:
            self._raise_device_error(f"Write variable '{variable_name}'", e)

    def _write_parameter(
        self, parameter_name: str, parameter_value: str, function: str = None
    ) -> str:
        """
        Method to write the specified parameter on the device. The laser has no separate
        parameter concept; this writes the Mark-US variable of that name.

        :author:    tylerjm@flexxbotics.com
        :since:     ODOULS.3 (7.1.15.3)
        """
        return self._write_variable(parameter_name, parameter_value, function)

    def _read_parameter(self, parameter_name: str, function: str = None) -> str:
        """
        Method to read the specified parameter from the device. The laser has no separate
        parameter concept; this reads the Mark-US variable of that name.

        :author:    tylerjm@flexxbotics.com
        :since:     ODOULS.3 (7.1.15.3)
        """
        return self._read_variable(parameter_name, function)

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
        :return: the laser's LOADJOB meaning ("OK", "FILE NOT FOUND", "BUSY", ...)

        :author:    zacc@flexxbotics.com
        :since:     MODELO.3 (7.1.13.3)
        """
        try:
            self._ensure_connected()
            data = FobaCmd.LOAD.value.build(file_name)
            raw = self.client.send(data=data, encoding="ascii", response_time=0.5)
            status = FobaCmd.LOAD.value.parse(raw=raw)["meaning"]
            return status
        except Exception as e:
            self._raise_device_error(f"Load file '{file_name}'", e)

    def _run_program(self, function: str = None) -> str:
        """
            Method to run the active program on the device

            :param function:
                        Optional parameter to provide the name of a function to run - string

            :return:    the laser's STARTJOB meaning ("OK", "BUSY", "NO JOB", ...)

            :author:    tylerjm@flexxbotics.com
            :since:     P.2 (7.1.16.2)
        """
        try:
            self._ensure_connected()
            data = FobaCmd.RUN.value.build()
            raw = self.client.send(data=data, encoding="ascii", response_time=0.5)
            status = FobaCmd.RUN.value.parse(raw=raw)["meaning"]
            return status
        except Exception as e:
            self._raise_device_error("Run program", e)

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
