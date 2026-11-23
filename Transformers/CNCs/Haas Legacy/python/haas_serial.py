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
from protocols.serial import Serial
from protocols.serial import ParityType
from data_models.part_count_event import PartCountEvent
from data_models.run_record import RunRecord
from data_models.abstractions.variables.abstract_variable import AbstractVariable

import json
import base64
import serial
import time
from werkzeug.exceptions import HTTPException
from transformers.abstract_device import AbstractDevice
from exceptions.flexxCoreExceptions import (
    FlexxCoreException,
    BadRequestException,
    ServerErrorException,
    ServiceUnavailableException,
)

class HaasSerial(AbstractDevice):

    # Q-command replies: command name -> (expected reply header, index of the value in the reply)
    _Q_REPLIES = {
        "get_mode": ("MODE", 1),
        "get_tool_changes": ("TOOLCHANGES", 1),
        "get_current_tool_number": ("USINGTOOL", 1),
        "get_power_time": ("P.O.TIME", 1),
        "get_motion_time": ("C.S.TIME", 1),
        "get_last_cycle": ("LASTCYCLE", 1),
        "get_previous_cycle": ("PREVCYCLE", 1),
        "get_part_count": ("PROGRAM", 4),
        "get_active_program": ("PROGRAM", 1),
    }

    def __init__(self, device: Device):
        """
        Haas legacy control transformer (Q commands over RS-232). Does not open the serial port.
        Raises ValueError when metaData has no 'port'.

        :param device:
                    the device object

        :return:    a new instance
        """
        # Get meta data of the device from its attributes, this contains information such as: ip address, ports, etc
        super().__init__(device)
        self.meta_data = device.metaData or {}
        self.port = self.meta_data.get("port", "")
        self.baudrate = int(self.meta_data.get("baudrate", 9600))
        self.bytesize = self._get_byte_size(self.meta_data.get("bytesize", "SEVENBITS"))
        self.stopbits = self._get_stop_bits(self.meta_data.get("stopbits", "1"))
        self.parity = self._get_parity(self.meta_data.get("parity", "EVEN"))
        self.xonxoff = self._as_bool(self.meta_data.get("xonxoff", False))
        self.rtscts = self._as_bool(self.meta_data.get("rtscts", False))
        self.dsrdtr = self._as_bool(self.meta_data.get("dsrdtr", False))
        self.write_timeout = float(self.meta_data.get("write_timeout_seconds", 5))
        if not self.port:
            raise ValueError("HaasSerial: metaData must contain 'port'")

        # Created by _connect (creating it opens the serial port)
        self.client = None
        self._connected = False
        self.q_commands = {
            "write": "?E",
            "read": "?Q600",
            "status": "?Q500",
            "get_mode": "?Q104",
            "get_tool_changes": "?Q200",
            "get_current_tool_number": "?Q201",
            "get_power_time": "?Q300",
            "get_motion_time": "?Q301",
            "get_last_cycle": "?Q303",
            "get_previous_cycle": "?Q304",
            "get_part_count": "?Q500",
            "get_active_program": "?Q500"
        }
        self.internal_part_counter = 0
        self.interval_count = 0
        self._idle_spindle_intervals = 0

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

        :param command_name:
                    the command to be executed
        :param command_args:
                    json with the arguments for the command

        :return:    the response after execution of command.

        Commands (no args): connect, disconnect, and the Q-command reads
        get_mode, get_tool_changes, get_current_tool_number, get_power_time,
        get_motion_time, get_last_cycle, get_previous_cycle, get_part_count,
        get_active_program.
        Returns: {"status": "ok", "command": "<name>", "value": "<value from the reply>"}
                 (connect / disconnect: {"status": "ok", "connected": true|false})

        Errors raise: 400 for an unknown command, 503 when the serial port
        cannot be used, 500 for an unexpected or empty reply.
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

            if command_name in self._Q_REPLIES:
                value = self._query(command_name)
                return json.dumps({"status": "ok", "command": command_name, "value": value})

        except Exception as e:
            self._raise_device_error(f"Command '{command_name}'", e)

        raise BadRequestException(f"Unknown command '{command_name}'")

    def _read_interval_data(self) -> str:
        """
        Method called on the poll interval. Reads the status again (_read_status). When it is not
        RUNNING or IDLE_SPINDLE, reads the active program (?Q500), sets it as the latest run
        record's partNumber ("NOT_REPORTED" when blank or unreadable) and resets partCount when it
        changed, then reads the CNC part count (?Q500) and creates a PartCountEvent when it changed.
        Errors are logged, never raised.

        :return:    None
        """
        try:
            # Get the most recent run record
            run_record: RunRecord = self._run_record_service.get_run_records()[0]

            # Get statys
            status = self._read_status()
            time.sleep(0.25)

            # Get the active program
            if status != "RUNNING" and status != "IDLE_SPINDLE":
                try:
                    active_program = str(self._query("get_active_program"))
                except ServerErrorException:
                    # Unexpected reply: handled as "None" below, as before
                    active_program = "None"
                time.sleep(0.25)
                self._logger.info("Active program: " + str(active_program))

                # Reset the part count on a program change
                if run_record.partNumber != active_program and active_program != "None":
                    run_record.partCount = 0
                else:
                    pass

                # Set the active program on the active run record
                if active_program != "":
                        if active_program != "None":
                            run_record.partNumber = active_program
                        else:
                            # Program name is "None"
                            run_record.partNumber = "NOT_REPORTED"
                else:
                    run_record.partNumber = "NOT_REPORTED"
                self._run_record_service.update_run_record(run_record=run_record)

                # Part count events
                raw_cnc_count = int(self._query("get_part_count"))
                time.sleep(0.25)
                if self.internal_part_counter == 0:
                    self.internal_part_counter = raw_cnc_count
                if raw_cnc_count != self.internal_part_counter:
                    # Part count event
                    self._logger.info("Part count detected")
                    event: PartCountEvent = PartCountEvent()
                    event.deviceId = self.device_id
                    self._run_record_service.create_event(event=event)
                    self.internal_part_counter = raw_cnc_count
                    self._logger.info("Part count event complete")

            # Variable events approximately every 15 minutes
            # if self.interval_count % 450 == 0:
                # variables: list[AbstractVariable] = self._variable_service.get_variables_by_device_id(device_id=self.device_id)
                # for variable in variables:
                    # self._device_service.read_device_variable(device_id=self.device_id, variable_name=variable.machineVariableName)
                    # time.sleep(0.25)

            self.interval_count += 1
        except Exception as e:
            self._error(message=f"Interval read failed: {e}")

    def _read_status(self, function: str = None) -> str:
        """
        Method to read the status of the device

        :param function:
                    Ignored.

        :return:    status - string: RUNNING, IDLE_SPINDLE, NO_DATA (empty ?Q500 reply),
                    UNKNOWN_HAAS_STATE or the PROGRAM status field; "ERROR" when the serial
                    port cannot be used or a read raises.
        """
        status = ""
        try:
            data = self.q_commands["status"] + "\r\n"
            result = self._send(data)
            time.sleep(0.25)

            status = self._process_status(status=result)
            self._logger.info("Status Result: " + str(status))

            # Read spindle speed safely
            spindle_speed = None
            try:
                spindle_speed_raw = self._read_variable(variable_name="3027") # 3027 macro = Spindle RPM
                time.sleep(0.25)
                if spindle_speed_raw is not None:
                    spindle_speed = float(spindle_speed_raw)
            except Exception as e:
                self._logger.error(f"Failed to read spindle speed: {str(e)}", exc_info=True)

            if spindle_speed is not None:
                self._logger.info("Spindle Speed: " + str(spindle_speed))
                # Only track consecutive low-speed intervals while status is RUNNING
                if status == "RUNNING":
                    if spindle_speed < 1.0:
                        self._idle_spindle_intervals += 1
                    else:
                        # Spindle came back -> reset counter and stay RUNNING
                        self._idle_spindle_intervals = 0

                    if self._idle_spindle_intervals >= 15:
                        status = "IDLE_SPINDLE"
                    else:
                        # Ensure we return RUNNING while building toward 15 intervals
                        status = "RUNNING"
                else:
                    # Not running -> reset counter
                    self._idle_spindle_intervals = 0
            else:
                # If we can't read spindle speed, don't let an old counter linger
                self._idle_spindle_intervals = 0

            alarm_status = "NO ACTIVE ALARMS" # TODO how to get alarm status on a haas legacy?
            if alarm_status == "NO ACTIVE ALARMS":
                pass
            else:
                status = alarm_status

        except Exception as e:
            self._error(message=f"Status read failed: {e}")
            self._connected = False
            status = "ERROR"

        return status

    def _read_variable(self, variable_name: str, function: str = None) -> str:
        """
        Method to read the specified variable from the device

        :param variable_name:
                    The name of the variable to read - string

        :param function:
                    Ignored.

        :return:    value - string
        """
        value = ""
        try:
            q_command = self.q_commands["read"] + " " + str(variable_name) + "\r\n"
            result = self._send(q_command)
            #result = result.split(",")
            value = self._process_response(
                result=result,
                expected="MACRO",
                actual_idx=0,
                data_idx=2,
            )
        except Exception as e:
            self._raise_device_error(f"Read variable '{variable_name}'", e)

        return value

    def _write_variable(self, variable_name: str, variable_value: str, function: str = None) -> str:
        """
        Method to write the specified variable on the device

        :param variable_name:
                    The name of the variable to write - string

        :param variable_value:
                    The value of the variable to write - string

        :param function:
                    Ignored.

        :return:    value - string: always "", since the write only succeeds when the reply's
                    first field is empty (including no reply); any other reply raises 500.
        """
        value = ""
        try:
            q_command = self.q_commands["write"] + str(variable_name) + " " + str(variable_value) + "\r\n"
            result = self._send(q_command)
            #result = result.split(",")
            value = self._process_response(
                result=result,
                expected="",
                actual_idx=0,
                data_idx=0,
            )
        except Exception as e:
            self._raise_device_error(f"Write variable '{variable_name}'", e)

        return value

    def _write_parameter(self, parameter_name: str, parameter_value: str, function: str = None) -> str:
        """
        Method to write the specified parameter on the device. Sent as a macro variable
        write (?E), the same as _write_variable.

        :param parameter_name:
                    The macro variable number to write - string

        :param parameter_value:
                    The value of the parameter to write - string

        :param function:
                    Ignored.

        :return:    value - string: always "", since the write only succeeds when the reply's
                    first field is empty (including no reply); any other reply raises 500.
        """
        value = ""
        try:
            q_command = self.q_commands["write"] + str(parameter_name) + " " + str(parameter_value) + "\r\n"
            result = self._send(q_command)
            #result = result.split(",")
            value = self._process_response(
                result=result,
                expected="",
                actual_idx=0,
                data_idx=0,
            )
        except Exception as e:
            self._raise_device_error(f"Write parameter '{parameter_name}'", e)

        return value

    def _read_parameter(self, parameter_name: str, function: str = None) -> str:
        """
        Method to read the specified parameter from the device

        :param parameter_name:
                    The macro variable number to read (?Q600) - string

        :param function:
                    Ignored.

        :return:    value - string
        """
        value = ""
        try:
            q_command = self.q_commands["read"] + " " + str(parameter_name) + "\r\n"
            result = self._send(q_command)
            #result = result.split(",")
            value = self._process_response(
                result=result,
                expected="MACRO",
                actual_idx=0,
                data_idx=2,
            )
        except Exception as e:
            self._raise_device_error(f"Read parameter '{parameter_name}'", e)

        return value

    def _read_file_names(self) -> list:
        """
        Method to get a list of filenames from the device. Not implemented.

        :return:    an empty list, always
        """

        # Return list of available filenames from the device
        self.programs = []

        return self.programs  # TODO is this the actual response object we want?

    def _write_file(self, file_name: str, file_data: str):
        """
        Not implemented: does nothing, so no file is written.

        :param file_name:
                    the name of the file to write.
        :param file_data:
                    the data of the file to write as base64 string.
        """
        pass

    def _load_file(self, file_name: str):
        """
        Not implemented: does nothing, so no file is loaded.

        :param file_name: the name of the file to load into memory
        :return: None (the body is empty)
        """
        pass

    # ############################################################################## #
    # INTERFACE HELPER METHODS
    #
    # These are private methods with naming convention _some_method(self). These are used to faciliate
    # any specific functions that are needed to communicate via the transformer. For example,
    # connection methods, read/write methods, specific functions, etc.
    # ############################################################################## #

    def _process_status(self, status):
        self._logger.info("Process Status: " + str(status))
        if status[0] == "STATUS" and status[1] == "BUSY":
            return "RUNNING"
        if status[0] == "PROGRAM":
            return status[2]
        if "PROGRAM" in status[0]:
            return status[2]
        if status[0] == '':
            return "NO_DATA"
        if 'STATUS' in status[0]:
            return "RUNNING"

        return "UNKNOWN_HAAS_STATE"

    def _process_response(self, result, expected, actual_idx, data_idx):
        if len(result) > max(actual_idx, data_idx) and expected == result[actual_idx]:
            value = result[data_idx]
            return value
        else:
            self._error(message="Error reading variable from device")
            reply = ",".join(str(r) for r in result)
            if not reply:
                raise ServerErrorException(f"No reply from device (expected '{expected}')")
            raise ServerErrorException(f"Unexpected reply from device (expected '{expected}'): '{reply}'")

    def _query(self, command_name: str) -> str:
        """Send the Q command for command_name and return the raw value from the reply."""
        expected, data_idx = self._Q_REPLIES[command_name]
        command = self.q_commands[command_name] + "\r\n"
        result = self._send(command)
        return self._process_response(
            result=result,
            expected=expected,
            actual_idx=0,
            data_idx=data_idx
        )

    def _send(self, data: str) -> list:
        """Send a Q command over the serial port and return the reply split into fields."""
        self._ensure_connected()
        result = self.client.send(data=data, encoding="ascii", response_time=0.5)
        if result is None or result == -1:
            # protocols/serial.py logs the error and returns -1 (or None when the read fails)
            self._connected = False
            raise ServiceUnavailableException(f"HaasSerial: no response on serial port '{self.port}'")
        return result

    def _connect(self) -> None:
        """Create the serial client, which opens the port. Serial.send reopens it for every call."""
        if self._connected:
            return
        # Release a port left open by an earlier failed call before opening it again
        try:
            self._disconnect()
        except Exception:
            pass
        try:
            self.client = Serial(port=self.port,
                                 baudrate=self.baudrate,
                                 bytesize=self.bytesize,
                                 stopbits=self.stopbits,
                                 parity=self.parity,
                                 xonxoff=self.xonxoff,
                                 rtscts=self.rtscts,
                                 dsrdtr=self.dsrdtr,
                                 write_timeout=self.write_timeout,
                                 )
        except Exception as e:
            raise ServiceUnavailableException(f"HaasSerial: cannot open serial port '{self.port}': {e}") from e
        self._connected = True

    def _disconnect(self) -> None:
        """Close the serial port and drop the client. Safe to call twice."""
        try:
            if self.client is not None:
                self.client.disconnect()
        finally:
            self.client = None
            self._connected = False

    def _ensure_connected(self) -> None:
        if not self._connected:
            self._connect()

    def _get_parity(self, parity_string):
        if parity_string == "EVEN":
            return serial.PARITY_EVEN
        if parity_string == "ODD":
            return serial.PARITY_ODD
        if parity_string == "NONE":
            return serial.PARITY_NONE
        else:
            return serial.PARITY_MARK

    def _get_byte_size(self, byte_size_string):
        if byte_size_string == "SEVENBITS":
            return serial.SEVENBITS
        else:
            return serial.EIGHTBITS

    def _get_stop_bits(self, stop_bits_string):
        if stop_bits_string == "1":
            return serial.STOPBITS_ONE
        if stop_bits_string == "1.5":
            return serial.STOPBITS_ONE_POINT_FIVE
        else:
            return serial.STOPBITS_TWO

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
