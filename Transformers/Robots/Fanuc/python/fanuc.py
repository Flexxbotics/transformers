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

# 3rd party python library imports
from requests import Request, Session
import json
from datetime import datetime, timedelta
import base64
import time
from pathlib import Path
import os
import ftputil
import subprocess
from collections import Counter
import re

from werkzeug.exceptions import HTTPException

from data_models.device import Device
from transformers.abstract_device import AbstractDevice
from exceptions.flexxCoreExceptions import (
    FlexxCoreException,
    BadRequestException,
    ServerErrorException,
    ServiceUnavailableException,
)


class Fanuc(AbstractDevice):
    """
    The device transformer for the FANUC Robot.

    :author:    cadenc@flexxbotics.com
    :author:    tylerjm@flexxbotics.com

    :since:     NOLA.1 (7.1.14.1)
    """

    # ############################################################################## #
    # INSTANTIATION
    # ############################################################################## #

    def __init__(self, device: Device):
        """
        FANUC Robot device class.
        :param Device:
                    the device object

        :return:    a new instance
        """
        super().__init__(device)

        # Setup specifics to the device interface
        self.meta_data = device.metaData or {}
        self.ip_address = self.meta_data.get("ip_address", "")
        self.port = str(self.meta_data.get("port", "3080"))
        if not self.ip_address:
            raise ValueError("Fanuc: metaData must contain 'ip_address'")
        self.root_url = "http://" + self.ip_address + ":" + self.port
        # The HTTP session is created by _connect (no device I/O here)
        self.client = None
        self._connected = False

        # This config specifies the Karel server endpoints
        self.endpoint_cfg = {"state": "/KAREL/FLEXX_GET_STATE"}

        self._run_state_array = []

        self.connect_attempts = 60  # try to connect for 30 seconds
        self.wait_between_connect_attempts = 0.5

        self._parent_directory = str(
            Path(__file__).parent.parent.parent.parent.parent.parent
        )
        # The robot state is read on demand by _read_status (no device I/O here)
        self._robot_state = {}
        self._previous_cycle_end = 0
        self.previous_failure_count = self._robot_state.get("failure_count", 0)
        self.previous_part_count = self._robot_state.get("part_count", 0)

        self._targetFTPpath = self.meta_data.get("ftp_path", "")
        self._programPath = "/md:/"
        self._username = self.meta_data.get("ftp_username", "flexxbotics")
        self._pwd = self.meta_data.get("ftp_password", "flexxbotics")
        self._programs_directory = (
                os.path.join(os.getcwd(), "temp_program_files") + os.sep
        )

        self.wago_id = ""
        self.sick_plc_id = ""
        self.yaskawa_carousel_id = ""
        self._last_sick_plc_status = ""
        devices = self._device_service.get_devices()

        self.GREEN_STACK_LIGHT = 9
        self.YELLOW_STACK_LIGHT = 11
        self.RED_STACK_LIGHT = 13
        self.CAROUSEL_RED_STACK_LIGHT = 52
        self.CAROUSEL_YELLOW_STACK_LIGHT = 53
        self.CAROUSEL_GREEN_STACK_LIGHT = 54
        self.CAROUSEL_BUZZER = 55

        for device in devices:
            if device.transformer == "Wago":
                self.wago_id = str(device.id)
            if device.transformer == "FlexiCompact":
                self.sick_plc_id = str(device.id)
            if device.transformer == "YaskawaMP2600":
                self.yaskawa_carousel_id = str(device.id)

    def __del__(self):
        try:
            self._disconnect()
        except Exception:
            pass

    # ############################################################################## #
    # DEVICE COMMUNICATION METHODS
    # ############################################################################## #
    def _execute_command_v2(self, command_name: str, command_args: str, receive_json=False) -> str:
        """
        Executes the command sent to the device.

        connect / disconnect
            Open / close the HTTP session; connect also checks that the robot answers
            (GET FLEXX_GET_STATE). Returns {"status": "ok", "connected": true|false}.
        <any other name>
            Runs the Karel program /KARELCMD/<NAME>?<ARG>=<VALUE>&... on the robot. The name,
            arg names and values are upper-cased. The name must be a Karel identifier
            (letter first, then letters, digits or "_"); anything else -> 400.
            Returns {"status": "ok", "response": <the robot's reply, as JSON when it is JSON>}.

        Errors raise: 400 for bad args or an invalid command name, 503 when the robot does
        not respond, 500 otherwise.

        :param command_name:
                    the command to be executed
        :param command_args:
                    json with the arguments for the command
        :param receive_json:
                    ignored; kept so existing direct callers don't break

        :return:    the response after execution of command.

        :author:    tylerjm@flexxbotics.com
        :since:     ODOULS.3 (7.1.15.3)
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

            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", command_name or ""):
                raise BadRequestException(f"Unknown command '{command_name}'")

            if len(args) < 0:
                URL = self.root_url + "/KARELCMD/" + command_name.upper()
            else:
                URL = self.root_url + "/KARELCMD/" + command_name.upper() + "?"
                i = 1
                for key in args.keys():
                    karal_var = str(key)
                    karal_val = str(args[key])
                    if i == len(args.keys()):
                        URL += karal_var.upper() + "=" + karal_val.upper()
                    else:
                        URL += karal_var.upper() + "=" + karal_val.upper() + "&"
                    i += 1

            # Send request
            self._info(message="Request: " + URL)
            send_command_req = Request("GET", URL)
            self._send_request(req=send_command_req)
            return json.dumps({"status": "ok", "response": self._response_body()}, default=str)

        except Exception as e:
            self._raise_device_error(f"Command '{command_name}'", e)

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
        Method to read the status of the robot

        :param function:
                    Optional parameter to provide the name of a function to run - string

        :return:    status - string

        :author:    tylerjm@flexxbotics.com
        :since:     ODOULS.3 (7.1.15.3)
        """
        try:
            # Robot status
            self._robot_state = self._get_state()
            status = self._robot_state.get("status", "ROBOT_READ_FAULT")
        except Exception as e:
            self._error(message=f"Status read failed: {e}")
            self._connected = False
            return "ERROR"

        return status

    def _read_variable(self, variable_name: str, function: str = None) -> str:
        """
        Method to read the specified variable from the robot (Karel FLEXX_GET_VARIABLE)

        :param variable_name:
                    The name of the variable to read - string

        :param function:
                    Ignored

        :return:    value - string: the reply's "value" field when it has one, else the reply as JSON.
                    Raises on failure.

        :author:    tylerjm@flexxbotics.com
        :since:     ODOULS.3 (7.1.15.3)
        """
        try:
            value = self._read_variable_from_robot(variable_name=variable_name)

            return self._reply_to_str(value)
        except Exception as e:
            self._raise_device_error(f"Read variable '{variable_name}'", e)

    def _write_parameter(self, parameter_name: str, parameter_value: str, function: str = None) -> str:
        """
        Method to write the specified parameter to the robot

        :param parameter:
                    The parameter to write - string

        :param parameter:
                    The value of the parameter to write - string

        :param function:
                    Optional parameter to provide the name of a function to run - string

        :return:    value - string: the written value. Raises on failure.

        :author:    tylerjm@flexxbotics.com
        :since:     ODOULS.3 (7.1.15.3)
        """
        try:
            self._set_parameter_on_robot(parameter_name=parameter_name, parameter_value=parameter_value)

            return str(parameter_value)
        except Exception as e:
            self._raise_device_error(f"Write parameter '{parameter_name}'", e)

    def _read_parameter(self, parameter_name: str, function: str = None) -> str:
        """
        Method to read the specified parameter from the robot (Karel FLEXX_GET_PARAMETER)

        :param parameter:
                    The parameter to write - string

        :param function:
                    Ignored

        :return:    value - string: the reply's "value" field when it has one, else the reply as JSON.
                    Raises on failure.

        :author:    tylerjm@flexxbotics.com
        :since:     ODOULS.3 (7.1.15.3)
        """
        try:
            value = self._read_parameter_from_robot(parameter_name=parameter_name)

            return self._reply_to_str(value)
        except Exception as e:
            self._raise_device_error(f"Read parameter '{parameter_name}'", e)

    def _read_file_names(self) -> list:
        """
        Method to get a list of filenames from the device

        :return:    list of filenames

        :author:    tylerjm@flexxbotics.com
        :since:     NOLA.3 (7.1.14.3)
        """

        # Return list of available filenames from the device
        self.programs = []
        self._info(message="getting program names from machine")
        try:
            with ftputil.FTPHost(self.ip_address, self._username, self._pwd) as host:
                names = host.listdir(host.curdir + self._programPath)
                for name in names:
                    self.programs.append(name)
            self._info(message="got program names from machine")
            self._info(message=str(self.programs))
        except Exception as e:
            self.programs = []
            self._raise_device_error("Read file names", e)

        return self.programs

    def _read_file(self, file_name: str) -> str:
        """
        Method to read a file from a device

        :param file_name:
                    the name of the file to read.

        :return:    the file's data as base64 string.

        :author:    tylerjm@flexxbotics.com
        :since:     NOLA.1 (7.1.14.1)
        """
        # Reads the file content off the device
        try:
            file_data = self._transfer_file_from_device(file_name)

            return base64.b64encode(file_data).decode("ascii")
        except Exception as e:
            self._raise_device_error(f"Read file '{file_name}'", e)

    def _write_file(self, file_name: str, file_data: str) -> str:
        """
        Method to write a file to a device

        :param file_name:
                    the name of the file to write.
        :param file_data:
                    the data of the file to write as base64 string

        :author:    tylerjm@flexxbotics.com
        :since:     NOLA.1 (7.1.14.1)
        """
        try:
            self._transfer_file_to_device(file_name=file_name, file_data=file_data)
        except Exception as e:
            self._raise_device_error(f"Write file '{file_name}'", e)

    def _load_file(self, file_name: str):
        """
        Loads a file into memory on the device

        :param file_name: the name of the file to load into memory
        :return: the file name

        :author:    tylerjm@flexxbotics.com
        :since:     NOLA.1 (7.1.14.1)
        """
        # TODO Implement way to load program into memory

    # ############################################################################## #
    # INTERFACE HELPER METHODS
    # ############################################################################## #
    def _connect(self) -> None:
        """HTTP is per request: this only creates the session (no network I/O)."""
        if self._connected and self.client is not None:
            return
        if self.client is None:
            self.client = Session()
        self._connected = True

    def _disconnect(self) -> None:
        try:
            if self.client is not None:
                self.client.close()
        finally:
            self.client = None
            self._connected = False

    def _ensure_connected(self) -> None:
        if not self._connected or self.client is None:
            self._connect()

    def _send_request(self, req: Request):
        """
        Method to send a message to the Flexxbotics Karel Server on the FANUC Robot

        :param: message

        :return:    True; raises ServiceUnavailableException when the robot does not respond

        :author:    tylerjm@flexxbotics.com
        :author:    cadenc@flexxbotics.com
        :since:     NOLA.1 (7.1.14.1)
        """
        self._ensure_connected()
        try:
            prepped_req = req.prepare()
            self.response = self.client.send(request=prepped_req)
            self._info("Response Text: " + self.response.text)
        except Exception as e:
            self._error(message="Error sending message to FANUC Robot: " + str(e))
            raise ServiceUnavailableException(f"FANUC Robot did not respond ({e})") from e

        return True

    def _response_body(self):
        """The last reply's body: parsed JSON when it is JSON, otherwise the text."""
        try:
            return self.response.json()
        except ValueError:
            return self.response.text

    @staticmethod
    def _reply_to_str(reply) -> str:
        """A Karel JSON reply as a plain string: its "value" field when it has one, else the reply as JSON."""
        if isinstance(reply, dict) and "value" in reply:
            reply = reply["value"]
        return reply if isinstance(reply, str) else json.dumps(reply, default=str)

    def _convert_hms_string_to_microseconds(self, hms_time_string):
        """
        Method to convert the UR's cycle time from hh:mm:ss.ssssss to microseconds for timedelta

        :param hms_time_string:
                    the time as a string: to convert.

        :return:    the converted time in microseconds

        :author:    tylerjm@flexxbotics.com
        :since:     NOLA.1 (7.1.14.1)
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
        Method to get state data from the FANUC robot

        :return:    json string with the state

        :author:    tylerjm@flexxbotics.com
        :author:    cadenc@flexxbotics.com
        :since:     NOLA.1 (7.1.14.1)
        """
        try:
            # TODO implement comes to get the state
            state_req = Request("GET", self.root_url + self.endpoint_cfg["state"])

            if self._send_request(state_req):
                state = self.response.json()
                self._info(state)
            else:
                state = {}

        except Exception as e:
            state = {}
            if (
                    "No connection could be made because the target machine actively refused it"
                    in str(e)
                    or "No route to host" in str(e)
                    or "Connection refused" in str(e)
            ):
                self._error(
                    "Connection to FANUC Robot Failed. Setting ROBOT_DISCONNECTED status"
                )
                state = {"status": "ROBOT_DISCONNECTED"}
            self._error(str(e))

        return state

    def _set_parameter_on_robot(self, parameter_name, parameter_value):
        """
        Method to set parameters on the FANUC robot
        :param: parameter - dict
        :return:    string

        :author:    tylerjm@flexxbotics.com
        :since:     ODOULS.3 (7.1.15.3)
        """
        URL = self.root_url + "/KARELCMD/FLEXX_SET_PARAMETERS?" + parameter_name + "=" + parameter_value
        self._info(message="Request: " + URL)
        send_command_req = Request("GET", URL)
        if self._send_request(req=send_command_req):
            pass
        else:
            self._info(message="Send request failed")
            raise Exception("Error returned from FANUC when setting parameters... ")

    def _set_parameters_on_robot(self, parameters):
        """
        Method to set parameters on the FANUC robot
        :param: parameters
        :return:    string

        :author:    tylerjm@flexxbotics.com
        :author:    cadenc@flexxbotics.com
        :since:     NOLA.2 (7.1.14.2)
        """

        URL = self.root_url + "/KARELCMD/FLEXX_SET_PARAMETERS?"
        i = 1
        self._info(message="Parameters:")
        self._info(message=parameters)
        for dict in parameters:
            self._info(message=dict)
            karal_var = dict["name"]
            karal_val = dict["value"]
            if i == len(parameters):
                URL += karal_var + "=" + karal_val
            else:
                URL += karal_var + "=" + karal_val + "&"
            i += 1

        self._info(message="Request: " + URL)
        send_command_req = Request("GET", URL)
        if self._send_request(req=send_command_req):
            pass
        else:
            self._info(message="Send request failed")
            raise Exception("Error returned from FANUC when setting parameters... ")

    def _read_parameter_from_robot(self, parameter_name):
        """
        Method to read a parameter from the FANUC robot

        :param: parameter_name
        :return:    json string with the variable

        :author:    tylerjm@flexxbotics.com
        :author:    cadenc@flexxbotics.com
        :since:     NOLA.2 (7.1.14.2)
        """
        URL = (
                self.root_url
                + "/KARELCMD/FLEXX_GET_PARAMETER?target_parameter="
                + parameter_name
        )
        self._info("Request: " + URL)
        send_command_req = Request("GET", URL)
        if self._send_request(req=send_command_req):
            parameter_response = self.response.json()
            self._info(message="Parameter Response: ")
            self._info(message=parameter_response)
        else:
            self._info(message="Send request failed")
            raise Exception("Error returned from FANUC while getting parameter... ")

        return parameter_response

    def _read_variable_from_robot(self, variable_name):
        """
        Method to read a variable from the FANUC robot

        :param: variable_name
        :return:    json string with the variable

        :author:    tylerjm@flexxbotics.com
        :author:    cadenc@flexxbotics.com
        :since:     NOLA.2 (7.1.14.2)
        """

        URL = (
                self.root_url
                + "/KARELCMD/FLEXX_GET_VARIABLE?target_variable="
                + variable_name
        )
        self._info("Request: " + URL)
        send_command_req = Request("GET", URL)
        if self._send_request(req=send_command_req):
            variable_response = self.response.json()
            self._info(message="Variable Response: ")
            self._info(message=variable_response)
        else:
            self._info(message="Send request failed")
            raise Exception("Error returned from FANUC while getting variable... ")

        return variable_response

    def _transfer_file_to_device(self, file_name, file_data):
        """
        Method to copy a program file to the device.

        :param file_name:
            the name of the file

        :param file_data:
            the file data

        :return:    the file data

        :author:    tylerjm@flexxbotics.com
        :since:     NOLA.1 (7.1.14.1)
        """
        self._info("transferring program file")
        try:
            bytes_data = base64.b64decode(file_data)
            program_file_path = self._programs_directory + file_name
            with open(program_file_path, "wb") as output_file:
                output_file.write(bytes_data)
            # use subprocess to copy the program from temp_program_files to device
            # TODO this is OS dependent need to figure out Widows
            subprocess.run(
                [
                    "lftp",
                    "-c",
                    "open "
                    + self._targetFTPpath
                    + self._programPath
                    + " --user "
                    + self._username
                    + " --password "
                    + self._pwd
                    + "; PUT -a "
                    + program_file_path,
                ]
            )
            time.sleep(1)
            os.remove(program_file_path)
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

        :author:    tylerjm@flexxbotics.com
        :since:     NOLA.1 (7.1.14.1)
        """
        self._info("backing up program file")
        try:
            # use subprocess to copy file off device to temp_program_files
            # TODO this is OS dependent need to figure out Widows
            program_file_path = self._programs_directory + file_name
            subprocess.run(
                [
                    "lftp",
                    "-c",
                    "open "
                    + self._targetFTPpath
                    + self._programPath
                    + " --user "
                    + self._username
                    + " --password "
                    + self._pwd
                    + "; GET "
                    + file_name
                    + " -o "
                    + program_file_path,
                ]
            )
            time.sleep(1)
            # Read temp_program_files directory to get the file and return a bytes object
            with open(program_file_path, "rb") as programFile:
                data = programFile.read()
            time.sleep(0.5)
            os.remove(program_file_path)
            return data
        except Exception as e:
            self._error(str(e))
            raise

    def _toggle_stack_light(self, status):
        if status == "RUNNING":
            # Stack lights green
            self._device_service.set_digital_output(device_id=self.wago_id, start=self.GREEN_STACK_LIGHT, values=[1])
            self._device_service.set_digital_output(device_id=self.wago_id, start=self.YELLOW_STACK_LIGHT, values=[0])
            self._device_service.set_digital_output(device_id=self.wago_id, start=self.RED_STACK_LIGHT, values=[0])

            self._device_service.set_digital_output(device_id=self.wago_id, start=self.CAROUSEL_GREEN_STACK_LIGHT,
                                                    values=[1])
            self._device_service.set_digital_output(device_id=self.wago_id, start=self.CAROUSEL_YELLOW_STACK_LIGHT,
                                                    values=[0])
            self._device_service.set_digital_output(device_id=self.wago_id, start=self.CAROUSEL_RED_STACK_LIGHT,
                                                    values=[0])
        elif status == "IDLE" or status == "TEACH_PENDANT_MODE":
            # Stack lights yellow
            self._device_service.set_digital_output(device_id=self.wago_id, start=self.GREEN_STACK_LIGHT, values=[0])
            self._device_service.set_digital_output(device_id=self.wago_id, start=self.YELLOW_STACK_LIGHT, values=[1])
            self._device_service.set_digital_output(device_id=self.wago_id, start=self.RED_STACK_LIGHT, values=[0])

            self._device_service.set_digital_output(device_id=self.wago_id, start=self.CAROUSEL_GREEN_STACK_LIGHT,
                                                    values=[0])
            self._device_service.set_digital_output(device_id=self.wago_id, start=self.CAROUSEL_YELLOW_STACK_LIGHT,
                                                    values=[1])
            self._device_service.set_digital_output(device_id=self.wago_id, start=self.CAROUSEL_RED_STACK_LIGHT,
                                                    values=[0])

        else:
            # Stack lights red
            self._device_service.set_digital_output(device_id=self.wago_id, start=self.GREEN_STACK_LIGHT, values=[0])
            self._device_service.set_digital_output(device_id=self.wago_id, start=self.YELLOW_STACK_LIGHT, values=[0])
            self._device_service.set_digital_output(device_id=self.wago_id, start=self.RED_STACK_LIGHT, values=[1])

            self._device_service.set_digital_output(device_id=self.wago_id, start=self.CAROUSEL_GREEN_STACK_LIGHT,
                                                    values=[0])
            self._device_service.set_digital_output(device_id=self.wago_id, start=self.CAROUSEL_YELLOW_STACK_LIGHT,
                                                    values=[0])
            self._device_service.set_digital_output(device_id=self.wago_id, start=self.CAROUSEL_RED_STACK_LIGHT,
                                                    values=[1])

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
