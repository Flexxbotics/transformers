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

from data_models.device import Device
from transformers.abstract_device import AbstractDevice


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
        self.meta_data = device.metaData
        self.ip_address = self.meta_data["ip_address"]
        self.port = self.meta_data["port"]
        self.root_url = "http://" + self.ip_address + ":" + self.port
        self.client = Session()

        # This config specifies the Karel server endpoints
        self.endpoint_cfg = {"state": "/KAREL/FLEXX_GET_STATE"}

        self._run_state_array = []

        self.connect_attempts = 60  # try to connect for 30 seconds
        self.wait_between_connect_attempts = 0.5

        self._parent_directory = str(
            Path(__file__).parent.parent.parent.parent.parent.parent
        )
        self._robot_state = self._get_state()
        self._previous_cycle_end = 0
        self.previous_failure_count = self._robot_state.get("failure_count", 0)
        self.previous_part_count = self._robot_state.get("part_count", 0)

        self._targetFTPpath = self.meta_data["ftp_path"]
        self._programPath = "/md:/"
        self._username = "flexxbotics"
        self._pwd = "flexxbotics"
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
        pass

    # ############################################################################## #
    # DEVICE COMMUNICATION METHODS
    # ############################################################################## #
    def _execute_command(self, command: str) -> str:
        """
        Executes the command sent to the device.

        :param command:
                    the command to be executed

        :return:    the response after execution of command.

        :author:    tylerjm@flexxbotics.com
        :since:     NOLA.1 (7.1.14.1)
        """
        # Parse the command from the incoming request
        command_string = command["commandJson"]
        command_json = json.loads(command_string)
        command_name = command_json["command"]
        args = command_json["args"]
        response = ""

        self._info(message="Sending command: " + command_string)
        try:
            if len(args) < 0:
                URL = self.root_url + "/KARELCMD/" + command_name
            else:
                URL = self.root_url + "/KARELCMD/" + command_name + "?"
                i = 1
                for key in args.keys():
                    karal_var = key
                    karal_val = args[key]
                    if i == len(args.keys()):
                        URL += karal_var + "=" + karal_val
                    else:
                        URL += karal_var + "=" + karal_val + "&"
                    i += 1

            # Send request
            self._info(message="Request: " + URL)
            send_command_req = Request("GET", URL)
            if self._send_request(req=send_command_req):
                pass
            else:
                self._info(message="Send request failed")
                raise Exception("Error returned from FANUC... " + command_name)

        except Exception as e:
            self._error(message=str(e))
            raise Exception(
                "Error when sending command, did not get response from FANUC: "
                + command_name
            )

        return ">OK<"

    def _execute_command_v2(self, command_name: str, command_args: str, receive_json=False) -> str:
        """
        Executes the command sent to the device.

        :param command_name:
                    the command to be executed
        :param command_args:
                    json with the arguments for the command

        :return:    the response after execution of command.

        :author:    tylerjm@flexxbotics.com
        :since:     ODOULS.3 (7.1.15.3)
        """
        try:
            # Parse the command from the incoming request
            args = json.loads(command_args)
            args = json.loads(args["value"])
            self._info(message="Sending command: " + command_name)

            response = ""
            if len(args) < 0:
                URL = self.root_url + "/KARELCMD/" + command_name.upper()
            else:
                URL = self.root_url + "/KARELCMD/" + command_name.upper() + "?"
                i = 1
                for key in args.keys():
                    karal_var = key
                    karal_val = args[key]
                    if i == len(args.keys()):
                        URL += karal_var.upper() + "=" + karal_val.upper()
                    else:
                        URL += karal_var.upper() + "=" + karal_val.upper() + "&"
                    i += 1

            # Send request
            self._info(message="Request: " + URL)
            send_command_req = Request("GET", URL)
            if self._send_request(req=send_command_req):
                pass
            else:
                self._info(message="Send request failed")
                raise Exception("Error returned from FANUC... " + command_name)

        except Exception as e:
            self._error(message=str(e))
            raise Exception(
                "Error when sending command, did not get response from FANUC: "
                + command_name
            )

        if receive_json:
            return self.response.json()
        
        return ">OK<"

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
        # Robot status
        self._robot_state = self._get_state()
        status = self._robot_state.get("status", "ROBOT_READ_FAULT")

        return status

    def _read_variable(self, variable_name: str, function: str = None) -> str:
        """
        Method to read the specified variable from the robot

        :param variable_name:
                    The name of the variable to read - string

        :param function:
                    Optional parameter to provide the name of a function to run - string

        :return:    value - string

        :author:    tylerjm@flexxbotics.com
        :since:     ODOULS.3 (7.1.15.3)
        """
        value = self._read_variable_from_robot(variable_name=variable_name)

        return value

    def _write_variable(self, variable_name: str, variable_value: str, function: str = None) -> str:
        """
        Method to write the specified variable on the robot

        :param variable_name:
                    The name of the variable to write - string

        :param variable_value:
                    The value of the variable to write - string

        :param function:
                    Optional parameter to provide the name of a function to run - string

        :return:    value - string

        :author:    tylerjm@flexxbotics.com
        :since:     ODOULS.3 (7.1.15.3)
        """
        value = self._set_variable_on_robot()

        return value

    def _write_parameter(self, parameter_name: str, parameter_value: str, function: str = None) -> str:
        """
        Method to write the specified parameter to the robot

        :param parameter:
                    The parameter to write - string

        :param parameter:
                    The value of the parameter to write - string

        :param function:
                    Optional parameter to provide the name of a function to run - string

        :return:    value - string

        :author:    tylerjm@flexxbotics.com
        :since:     ODOULS.3 (7.1.15.3)
        """
        self._set_parameter_on_robot(parameter_name=parameter_name, parameter_value=parameter_value)

    def _read_parameter(self, parameter_name: str, function: str = None) -> str:
        """
        Method to read the specified parameter from the robot

        :param parameter:
                    The parameter to write - string

        :param function:
                    Optional parameter to provide the name of a function to run - string

        :return:    value - string

        :author:    tylerjm@flexxbotics.com
        :since:     ODOULS.3 (7.1.15.3)
        """
        value = self._read_parameter_from_robot(parameter_name=parameter_name)

        return value

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
            with ftputil.FTPHost(self.host, self.username, self.pwd) as host:
                names = host.listdir(host.curdir + self.programPath)
                for name in names:
                    self.programs.append(name)
            self._info(message="got program names from machine")
            self._info(message=str(self.programs))
        except Exception as e:
            self.programs = []
            self.status = "MACHINE_DISCONNECTED"
            self._error(str(e))

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
        file_data = self._transfer_file_from_device(file_name)

        return base64.b64encode(file_data)

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
        self._transfer_file_to_device(file_name=file_name, file_data=file_data)

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
    def _send_request(self, req: Request):
        """
        Method to send a message to the Flexxbotics Karel Server on the FANUC Robot

        :param: message

        :return:    success boolean

        :author:    tylerjm@flexxbotics.com
        :author:    cadenc@flexxbotics.com
        :since:     NOLA.1 (7.1.14.1)
        """
        try:
            prepped_req = req.prepare()
            self.response = self.client.send(request=prepped_req)
            self._info("Response Text: " + self.response.text)
        except Exception as e:
            self._info(message=str(e))
            self._error(message="Error sending message to FANUC Robot")
            self._error(message=str(e))
            return False

        return True

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
        try:
            if self._send_request(req=send_command_req):
                variable_response = self.response.json()
                self._info(message="Variable Response: ")
                self._info(message=variable_response)
            else:
                self._info(message="Send request failed")
                raise Exception("Error returned from FANUC while getting variable... ")

        except Exception as e:
            raise Exception(str(e))

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
            self._set_status(value="ROBOT_DISCONNECTED")
            self._error(str(e))
            raise Exception(str(e))

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
                    + self.targetFTPpath
                    + self.programPath
                    + " --user "
                    + self.username
                    + " --password "
                    + self.pwd
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
            self._set_status(value="ROBOT_DISCONNECTED")
            self._error(str(e))
            raise Exception(str(e))

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
