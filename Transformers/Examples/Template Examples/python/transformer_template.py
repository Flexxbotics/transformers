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
import json
import base64

# Flexx core objects
from data_models.device import Device
from transformers.abstract_device import AbstractDevice


class Transformer(AbstractDevice):
    """
    The device transformer example
    """

    # ############################################################################## #
    # INSTANTIATION
    # ############################################################################## #

    def __init__(self, device: Device):
        """
        Transformer for the device class.
        :param Device:
                    the device object

        :return:    a new instance
        """
        super().__init__(device)

        # Setup specifics to the device interface
        self.meta_data = device.metaData
        self.ip_address = self.meta_data["ip_address"]
        self.port = self.meta_data["port"]


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
        """
        try:
            # Parse the command from the incoming request
            command_string = command["commandJson"]
            command_json = json.loads(command_string)
            command_name = command_json["command"]
            args = command_json["args"]
            response = ""
            self._info(message="Sending command: " + command_string)
        except Exception as e:
            self._error(message=str(e))
            raise Exception(
                "Error when sending command, did not get response from: "
                + command_name
            )

        return response

    def _execute_command_v2(self, command_name: str, command_args: str, receive_json=False) -> str:
        """
        Executes the command sent to the device.

        :param command_name:
                    the command to be executed
        :param command_args:
                    json with the arguments for the command

        :return:    the response after execution of command.
        """
        try:
            # Parse the command from the incoming request
            args = json.loads(command_args)
            response = ""
            self._info(message="Sending command: " + command_name)
        except Exception as e:
            self._error(message=str(e))
            raise Exception(str(e))
        
        return response

    def _read_interval_data(self) -> str:
        """
        Method to read the status of the device on an interval

        :return:    status - string
        """
        try:
            pass
        except Exception as e:
            self._error(message=str(e))
            raise Exception(str(e))

    def _read_status(self, function: str = None) -> str:
        """
        Method to read the status of the robot

        :param function:
                    Optional parameter to provide the name of a function to run - string

        :return:    status - string
        """
        status = ""
        try:
            pass
        except Exception as e:
            self._error(message=str(e))
            raise Exception(str(e))
        return status

    def _read_variable(self, variable_name: str, function: str = None) -> str:
        """
        Method to read the specified variable from the robot

        :param variable_name:
                    The name of the variable to read - string

        :param function:
                    Optional parameter to provide the name of a function to run - string

        :return:    value - string
        """
        value = ""
        try:
            pass
        except Exception as e:
            self._error(message=str(e))
            raise Exception(str(e))
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
        """
        value = ""
        try:
            pass
        except Exception as e:
            self._error(message=str(e))
            raise Exception(str(e))
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
        """
        try:
            pass
        except Exception as e:
            self._error(message=str(e))
            raise Exception(str(e))

    def _read_parameter(self, parameter_name: str, function: str = None) -> str:
        """
        Method to read the specified parameter from the robot

        :param parameter:
                    The parameter to write - string

        :param function:
                    Optional parameter to provide the name of a function to run - string

        :return:    value - string
        """
        value = ""
        try:
            pass
        except Exception as e:
            self._error(message=str(e))
            raise Exception(str(e))
        return value

    def _read_file_names(self) -> list:
        """
        Method to get a list of filenames from the device

        :return:    list of filenames
        """
        try:
            # Return list of available filenames from the device
            self.programs = []
            self._info(message="getting program names from machine")
        except Exception as e:
            self._error(message=str(e))
            raise Exception(str(e))

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
        file_data = ""
        try:
            pass
        except Exception as e:
            self._error(message=str(e))
            raise Exception(str(e))

        return base64.b64encode(file_data)

    def _write_file(self, file_name: str, file_data: str) -> str:
        """
        Method to write a file to a device

        :param file_name:
                    the name of the file to write.
        :param file_data:
                    the data of the file to write as base64 string
        """
        try:
            pass
        except Exception as e:
            self._error(message=str(e))
            raise Exception(str(e))

    def _load_file(self, file_name: str):
        """
        Loads a file into memory on the device

        :param file_name: the name of the file to load into memory
        """
        try:
            pass
        except Exception as e:
            self._error(message=str(e))
            raise Exception(str(e))

    # ############################################################################## #
    # EXAMPLES OF INTERFACE HELPER METHODS
    # ############################################################################## #
    def _connect(self):
        """
        Method to connect to the device
        """
        try:
            pass
        except Exception as e:
            raise Exception(str(e))

    def _disconnect(self):
        """
        Method to disconnect from the device
        """
        try:
            pass
        except Exception as e:
            raise Exception(str(e))

    def _send_request(self, message):
        """
        Method to send a message to the device

        :param: message
        :return: response
        """
        try:
            response = ""
            pass
        except Exception as e:
            raise Exception(str(e))
        return response

    def _get_state(self):
        """
        Method to get state data from the device

        :return:    dict with the state
        """
        try:
           state = {}
        except Exception as e:
            raise Exception(str(e))

        return state