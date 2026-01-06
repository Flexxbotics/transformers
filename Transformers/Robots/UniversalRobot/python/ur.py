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

from data_models.device import Device
from transformers.abstract_device import AbstractDevice


class UR(AbstractDevice):
    """
    The device transformer for the Universal Robot.

    :author:    cristc@flexxbotics.com
    :author:    tylerjm@flexxbotics.com
    :author:    johnc@flexxbotics.com
    `
    :since:     KEYSTONE.4 (7.1.11.4)
    """

    def __init__(self, device: Device):
        """
        Universal Robot device class.
        :param Device:
                    the device object

        :return:    a new instance
        """
        super().__init__(device)

        # Setup specifics to the device interface
        self.meta_data = self.device.metaData
        self.ip_address = self.meta_data["ip_address"]
        self.port = self.meta_data["port"]
        self.address = self.ip_address + ":" + self.port
        self.client = xmlrpc.client.ServerProxy("http://" + self.address + "/RPC2")
        self._programs_directory = (
                os.path.join(os.getcwd(), "temp_program_files") + os.sep
        )
        self._robot_state = self._get_state()
        self._previous_cycle_end = ""
        self.previous_failure_count = self._robot_state.get("failure_count", 0)
        self.previous_part_count = self._robot_state.get("part_count", 0)

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

        :author:    sanua@flexxbotics.com
        :since:     MODELO.2 (7.1.13.2)
        """
        # Parse the command from the incoming request
        command_string = command["commandJson"]
        command_json = json.loads(command_string)
        command_name = command_json["command"]
        response = ""

        self._info(message="Sending command: " + command_string)
        try:
            # Move to joints requires a longer timeout
            if command_name == "move_to_joints":
                socket.setdefaulttimeout(int(command_json["timeout"]))

            # Build a tuple with any passed in arguments to enable dynamic function calls
            function_name = f"self.client.{command_name}"
            first_key = next(iter(command_json))
            command_json.pop(first_key)

            # If the request has arguments, pass them to eval
            #   Otherwise just call the requested function directly from eval

            if len(command_json) > 0:
                # if there are more arguments, it will grab the values of every other key-value pair
                args = tuple(command_json.values())
                function_name = f"{function_name}(*{args})"
                response = eval(function_name)
            else:
                ur_cap_function = eval(function_name)
                response = ur_cap_function()

        except Exception as e:
            raise Exception(
                "Error when sending command, did not get response from urcap: "
                + command_name
            )

        finally:
            socket.setdefaulttimeout(3)

        if "ERROR" in response:
            raise Exception("Error returned from urcap... " + command_name)

        return response

    def _execute_command_v2(self, command_name: str, command_args: str) -> str:
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
            # Move to joints requires a longer timeout
            if command_name == "move_to_joints":
                socket.setdefaulttimeout(int(args["timeout"])) # self.client.play_program()

            # Build a tuple with any passed in arguments to enable dynamic function calls
            function_name = f"self.client.{command_name}"

            # If the request has arguments, pass them to eval
            #   Otherwise just call the requested function directly from eval

            if len(args) > 0:
                # if there are more arguments, it will grab the values of every other key-value pair
                args = tuple(args.values())
                function_name = f"{function_name}(*{args})"
                response = eval(function_name)
            else:
                ur_cap_function = eval(function_name)
                response = ur_cap_function()

        except Exception as e:
            self._error(str(e))
            raise Exception(
                "Error when sending command, did not get response from urcap: "
                + command_name
            )

        finally:
            socket.setdefaulttimeout(3)

        if "ERROR" in response:
            raise Exception("Error returned from urcap... " + command_name)

        return response

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
        self._robot_state = self._get_state()
        status = self._robot_state.get("status", "")
        match status:
            case "true":
                status = "RUNNING"
            case "false":
                status = "IDLE"
            case "":
                pass

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
        value = self.client.get_variable(variable_name)

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
        value = self.client.set_value(variable_name, variable_value)

        return value

    def _write_parameter(self, parameter_name: str, parameter_value: str, function: str = None) -> str:
        """
        Method to write the specified parameter on the robot

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
        self.client.set_value(parameter_name, parameter_value)

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
        value = self.client.get_value(parameter_name)

        return value

    def _run_program(self, function: str = None) -> str:
        """
            Method to run the active program on the device

            :param function:
                        Optional parameter to provide the name of a function to run - string

            :return:    value - string

            :author:    tylerjm@flexxbotics.com
            :since:     P.2 (7.1.16.2)
        """
        self._info(message="UR- Sending command to run program...")
        try:
            response = self.client.play_program()
            self._info(message="HexagonCMM - Ran program result: " + str(response))
        except Exception as e:
            response = "Error running program"
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

        try:
            response = json.loads(self.client.get_program_names())
            self.programs = response["filenames"]
        except Exception as e:
            self.programs = []
            self.status = "ROBOT_DISCONNECTED"
            self._error(str(e))
            raise Exception(str(e))

        return self.programs  # TODO is this the actual response object we want?

    def _read_file(self, file_name: str) -> str:
        """
        Method to read a file from a device

        :param file_name:
                    the name of the file to read.

        :return:    the file's data as base64 string

        :author:    tylerjm@flexxbotics.com
        :since:     KEYSTONE.4 (7.1.11.4)
        """
        # Reads the file content off the device
        file_data = self._transfer_file_from_device(file_name)

        return base64.b64encode(file_data)

    def _write_file(self, file_name: str, file_data: str):
        """
        Method to write a file to a device

        :param file_name:
                    the name of the file to write.
        :param file_data:
                    the data of the file to write as base64 string

        :author:    tylerjm@flexxbotics.com
        :since:     ODOULS.3 (7.1.15.3)
        """
        self._transfer_file_to_device(file_name=file_name, file_data=file_data)

    def _load_file(self, file_name: str):
        """
        Loads a file into memory on the device

        :param file_name: the name of the file to load into memory
        :return: the file name

        :author:    zacc@flexxbotics.com
        :since:     MODELO.3 (7.1.13.3)
        """

        # Load the file into memory on the UR
        self.client.load_robot_program(file_name)

    # ############################################################################## #
    # INTERFACE HELPER METHODS
    # ############################################################################## #

    def _convert_hms_string_to_microseconds(self, hms_time_string):
        """
        Method to convert the UR's cycle time from hh:mm:ss.ssssss to microseconds for timedelta

        :param hms_time_string:
                    the time as a string: to convert.

        :return:    the converted time in microseconds

        :author:    tylerjm@flexxbotics.com
        :since:     KEYSTONE.4 (7.1.11.4)
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

        :return:    json string with the state

        :author:    tylerjm@flexxbotics.com
        :since:     KEYSTONE.4 (7.1.11.4)
        """
        try:
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

        :author:    tylerjm@flexxbotics.com
        :since:     KEYSTONE.4 (7.1.11.4)
        """
        self._info("transferring program file")
        try:

            decoded_string = base64.b64decode(file_data)
            """
            xmlrpc is the server on UR's side. the file must be converted back to 
            binary using xmlrpc.client.Binary or the file will not format correctly. 
            """
            binary_data = xmlrpc.client.Binary(decoded_string)
            self.client.send_program_file(file_name, binary_data)
            self._info("transfer program file complete")

        except Exception as e:
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
        :since:     KEYSTONE.4 (7.1.11.4)
        """
        self._info("backing up program file")
        program_file_path = self._programs_directory + file_name
        try:
            program_file_binary = self.client.get_program_file(file_name)
            with open(program_file_path, "wb") as handle:
                handle.write(program_file_binary.data)

            with open(program_file_path, "rb") as program_file:
                data = program_file.read()
                return data
        except Exception as e:
            self._error(str(e))
            raise Exception(str(e))
