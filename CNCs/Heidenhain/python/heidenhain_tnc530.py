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

from transformers.abstract_device import AbstractDevice
import requests


class HeidenhainTNC530(AbstractDevice):

    def __init__(self, device: Device):
        """
        Template device class. Inherits AbstractDevice class.

        :param Device:
                    the device object

        :return:    a new instance
        """
        # Get meta data of the device from its attributes, this contains information such as: ip address, ports, etc
        super().__init__(device)
        self.meta_data = device.metaData
        self.address = self.meta_data["ip_address"]

        self.host = "http://host.docker.internal"
        self.host_port = 7083
        self.base_url = self.host + ":" + str(self.host_port)

    def __del__(self):
        pass

    # ############################################################################## #
    # DEVICE COMMUNICATION METHODS
    # ############################################################################## #

    def _execute_command(self, command_name: str, command_args: str) -> str:
        """
        Executes the command sent to the device.

        :param command_name:
                    the command to be executed
        :param command_args:
                    json with the arguments for the command

        :return:    the response after execution of command.

        :author:    tylerjm@flexxbotics.com
        :since:     Q.5 (7.1.17.5)
        """
        pass

    def _read_interval_data(self) -> str:
        """
        Method to read the status of the device on an interval

        :return:    status - string

        :author:    tylerjm@flexxbotics.com
        :since:     Q.5 (7.1.17.5)
        """
        pass

    def _read_status(self, function: str = None) -> str:
        """
        Method to read the status of the device

        :param function:
                    Optional parameter to provide the name of a function to run - string

        :return:    status - string

        :author:    tylerjm@flexxbotics.com
        :since:     Q.5 (7.1.17.5)
        """
        pass

    def _read_variable(self, variable_name: str, function: str = None) -> str:
        """
        Method to read the specified variable from the device

        :param variable_name:
                    The name of the variable to read - string

        :param function:
                    Optional parameter to provide the name of a function to run - string

        :return:    value - string

        :author:    tylerjm@flexxbotics.com
        :since:     Q.5 (7.1.17.5)
        """
        pass

    def _write_variable(self, variable_name: str, variable_value: str, function: str = None) -> str:
        """
        Method to write the specified variable on the device

        :param variable_name:
                    The name of the variable to write - string

        :param variable_value:
                    The value of the variable to write - string

        :param function:
                    Optional parameter to provide the name of a function to run - string

        :return:    value - string

        :author:    tylerjm@flexxbotics.com
        :since:     Q.5 (7.1.17.5)
        """
        pass

    def _write_parameter(self, parameter_name: str, parameter_value: str, function: str = None) -> str:
        """
        Method to write the specified parameter on the device

        :param parameter:
                    The parameter to write - string

        :param parameter:
                    The value of the parameter to write - string

        :param function:
                    Optional parameter to provide the name of a function to run - string

        :return:    value - string

        :author:    tylerjm@flexxbotics.com
        :since:     Q.5 (7.1.17.5)
        """
        pass

    def _read_parameter(self, parameter_name: str, function: str = None) -> str:
        """
        Method to read the specified parameter from the device

        :param parameter:
                    The parameter to write - string

        :param function:
                    Optional parameter to provide the name of a function to run - string

        :return:    value - string

        :author:    tylerjm@flexxbotics.com
        :since:     Q.5 (7.1.17.5)
        """
        pass

    def _read_file_names(self) -> list:
        """
        Method to get a list of filenames from the device

        :return:    list of filenames

        :author:    tylerjm@flexxbotics.com
        :since:     Q.5 (7.1.17.5)
        """
        pass

    def _read_file(self, file_name: str) -> str:
        """
        Method to read a file from a device

        :param file_name:
                    the name of the file to read.

        :return:    the file's data as base64 string.

        :author:    tylerjm@flexxbotics.com
        :since:     Q.5 (7.1.17.5)
        """
        pass

    def _write_file(self, file_name: str, file_data: str):
        """
        Method to write a file to a device

        :param file_name:
                    the name of the file to write.
        :param file_data:
                    the data of the file to write as base64 string.

        :author:    tylerjm@flexxbotics.com
        :since:     Q.5 (7.1.17.5)
        """
        pass

    def _load_file(self, file_name: str):
        """
        Loads a file into memory on the device

        :param file_name: the name of the file to load into memory
        :return: the file name

        :author:    tylerjm@flexxbotics.com
        :since:     Q.5 (7.1.17.5)
        """
        resp = requests.post(self.base_url + "/load", json={"filename": file_name, "ip_address": self.address})

        return str(resp.json())

    # ############################################################################## #
    # INTERFACE HELPER METHODS
    #
    # These are private methods with naming convention _some_method(self). These are used to faciliate
    # any specific functions that are needed to communicate via the transformer. For example,
    # connection methods, read/write methods, specific functions, etc.
    # ############################################################################## #