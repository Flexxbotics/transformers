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

"""

    THIS IS A TEMPLATE. Be wary about making changes directly to it. It is meant to serve as guidance to future
    device interface developers. Order of operations to use this file should be:
        1. Make a copy of template.py and rename it as device_interface_name.py.
        2. Make edits directly in the copied file

"""


class Hexagon(AbstractDevice):

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
        self.port = self.meta_data["port"]

        self.client = TCP(address=self.address, port=self.port)
        self.active_program = ""
        self.parameters = {}

    def __del__(self):
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

        :author:    tylerjm@flexxbotics.com
        :since:     ODOULS.3 (7.1.15.3)
        """
        # Parse the command from the incoming request
        args = json.loads(command_args)
        response = ""

        self._info(message="Sending command: " + command_name)
        try:
            if command_name == "delete_programs":
                cmm_command = {
                    "type": "delete_programs",
                    "action": "run",
                    "name": self.active_program,
                    "traceFields": {}
                }
                data = json.dumps(cmm_command)
                response = self.client.send(data=data, encoding="utf-8", response_time=0.5)
                self._info(message="Sent command. Returning OK")
                return "OK"
            elif command_name == "pass":
                cmm_command = {
                    "type": "pass",
                    "name": self.active_program,
                    "traceFields": {}
                }
                data = json.dumps(cmm_command)
                response = self.client.send(data=data, encoding="utf-8", response_time=0.5)
                self._info(message="Sent command. Returning OK")
                return str(response)
            elif command_name == "clear_parameters":
                self.parameters = {}
            else:
                return "Error executing command"
        except Exception as e:
            self._error(message="Failed to send command: " + str(e))
            raise Exception(
                "Error when sending command, did not get response from device: "
                + command_name
            )
        finally:
            pass

        self._info(message="Returning Empty Response")
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
        Method to read the status of the device

        :param function:
                    Optional parameter to provide the name of a function to run - string

        :return:    status - string

        :author:    tylerjm@flexxbotics.com
        :since:     ODOULS.3 (7.1.15.3)
        """
        status = ""
        if function is None:
            cmm_command = {
                "type": "status",
            }
            data = json.dumps(cmm_command)
            result = self.client.send(data=data, encoding="ascii", response_time=0.5)
            cmm_response = json.loads(result)
            cmm_status = cmm_response["status"]
            if cmm_status == "routineComplete" or cmm_status == "waiting":
                return "IDLE"
            elif cmm_status == "routineRunning":
                return "RUNNING"
            elif cmm_status == "error":
                return "FAULT"
        elif function == "":  # Some string
            # Write specific function call to read status
            pass
        else:
            pass

        return status

    def _read_variable(self, variable_name: str, function: str = None) -> str:
        """
        Method to read the specified variable from the device

        :param variable_name:
                    The name of the variable to read - string

        :param function:
                    Optional parameter to provide the name of a function to run - string

        :return:    value - string

        :author:    tylerjm@flexxbotics.com
        :since:     ODOULS.3 (7.1.15.3)
        """
        value = ""
        if function is None:
            cmm_command = {
                "type": "measurement",
                "name": variable_name,
            }
            data = json.dumps(cmm_command)
            result = self.client.send(data=data, encoding="utf-8", response_time=0.5)
            cmm_response = json.loads(result)
            value = cmm_response["value"]
        elif function == "":  # Some string
            # Write specific function call to read variable
            pass
        else:
            pass

        return str(value)

    def _run_program(self, function: str = None) -> str:
        """
        Method to run the active program on the device

        :param function:
                    Optional parameter to provide the name of a function to run - string

        :return:    value - string

        :author:    tylerjm@flexxbotics.com
        :since:     P.2 (7.1.16.2)
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
        Method to write the specified parameter to the device

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
        self.parameters[parameter_name] = parameter_value

        return f"Parameter {parameter_name} wrote to value {parameter_value}"

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

    def _read_file(self, file_name: str) -> str:
        """
        Method to read a file from a device

        :param file_name:
                    the name of the file to read.

        :return:    the file's data as base64 string.

        :author:    tylerjm@flexxbotics.com
        :since:     KEYSTONE.4 (7.1.11.4)
        """
        # Reads the file content off the device
        file_data = ""

        return base64.b64encode(file_data)

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
        :return: the file name

        :author:    zacc@flexxbotics.com
        :since:     MODELO.3 (7.1.13.3)
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
