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


@dataclass(frozen=True)
class LaserCommand:
    command: str
    min_args: int = 0
    max_args: int | None = 0
    response_codes: dict[str, str] = field(default_factory=dict)

    def build(self, *args: Any) -> str:
        self.validate_args(args)
        if args:
            return f"{self.keyword}:{';'.join(str(a) for a in args)}"
        return self.keyword

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
        parts = raw.strip().split(";")
        code = parts[0]

        return {
            "code": code,
            "meaning": self.response_codes.get(code, "UNKNOWN"),
            "extra": parts[1:] if len(parts) > 1 else [],
        }


class FobaCmd(Enum):
    WRITE = LaserCommand("SETVAR", min_args=2, max_args=2)
    READ = LaserCommand("GETVAR", min_args=1, max_args=1)
    STATUS = LaserCommand(
        "GETSTATUS",
        response_codes={
            "1": "IDLE",
            "-1": "OFFLINE",
            "-5": "RUNNING",
            "-6": "INVALID FORMAT",
        },
    )


"""

    THIS IS A TEMPLATE. Be wary about making changes directly to it. It is meant to serve as guidance to future
    device interface developers. Order of operations to use this file should be:
        1. Make a copy of template.py and rename it as device_interface_name.py.
        2. Make edits directly in the copied file

"""


class FOBA(AbstractDevice):

    def __init__(self, device: Device):
        """
        Template device class. Inherits AbstractDevice class.

        :param Device:
                    the device object

        :return:    a new instance
        """
        # Get meta data of the device from its attributes, this contains information such as: ip address, ports, etc
        self.meta_data = device.metaData
        self.connection_string = self.meta_data["address"].split(":")
        self.address = self.connection_string[0]
        self.port = self.connection_string[1]

        self.client = TCP(address=self.address, port=self.port)

    def __del__(self):
        pass

    # ############################################################################## #
    # DEVICE COMMUNICATION METHODS
    # ############################################################################## #

    def execute_command(self, command_name: str, command_args: str) -> str:
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
            command = self.foba_commands[command_name] + "\r\n"
            result = self.client.send(data=command, encoding="ascii", response_time=0.5)
            result = result.split(",")
            if command_name == "get_mode":
                expected = "MODE"
                actual_idx = 0
                data_idx = 1
            elif command_name == "get_tool_changes":
                expected = "TOOLCHANGES"
                actual_idx = 0
                data_idx = 1
            elif command_name == "get_current_tool_number":
                expected = "USINGTOOL"
                actual_idx = 0
                data_idx = 1
            elif command_name == "get_power_time":
                expected = "P.O.TIME"
                actual_idx = 0
                data_idx = 1
            elif command_name == "get_motion_time":
                expected = "C.S.TIME"
                actual_idx = 0
                data_idx = 1
            elif command_name == "get_last_cycle":
                expected = "LASTCYCLE"
                actual_idx = 0
                data_idx = 1
            elif command_name == "get_previous_cycle":
                expected = "PREVCYCLE"
                actual_idx = 0
                data_idx = 1
            elif command_name == "get_part_count":
                expected = "PROGRAM"
                actual_idx = 0
                data_idx = 4
            else:
                pass
            response = self._process_response(
                result=result,
                expected=expected,
                actual_idx=actual_idx,
                data_idx=data_idx,
            )
        except Exception as e:
            raise Exception(
                "Error when sending command, did not get response from device: "
                + command_name
            )
        finally:
            pass

        return response

    def read_interval_data(self) -> str:
        """
        Method to read the status of the device on an interval

        :return:    status - string

        :author:    tylerjm@flexxbotics.com
        :author:    sanua@flexxbotics.com

        :since:     ODOULS.3 (7.1.15.3)
        """
        pass

    def read_status(self, function: str = None) -> str:
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
            data = FobaCmd.STATUS.value.build()
            raw = self.client.send(data=data, encoding="ascii", response_time=0.5)
            result = FobaCmd.STATUS.value.parse(raw=raw)["meaning"]
            status = self._process_status(result=result)
        elif function == "":  # Some string
            # Write specific function call to read status
            pass
        else:
            pass

        return status

    def read_variable(self, variable_name: str, function: str = None) -> str:
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
            q_command = self.foba_commands["read"] + " " + str(variable_name) + "\r\n"
            result = self.client.send(
                data=q_command, encoding="ascii", response_time=0.5
            )
            result = result.split(",")
            value = self._process_response(
                result=result,
                expected="MACRO",
                actual_idx=0,
                data_idx=1,
            )
        elif function == "":  # Some string
            # Write specific function call to read variable
            pass
        else:
            pass

        return value

    def write_variable(
        self, variable_name: str, variable_value: str, function: str = None
    ) -> str:
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
        :since:     ODOULS.3 (7.1.15.3)
        """
        value = ""
        if function is None:
            q_command = (
                self.foba_commands["write"]
                + str(variable_name)
                + " "
                + str(variable_value)
                + "\r\n"
            )
            result = self.client.send(
                data=q_command, encoding="ascii", response_time=0.5
            )
            result = result.split(",")
            value = self._process_response(
                result=result,
                expected="!",
                actual_idx=0,
                data_idx=0,
            )
        elif function == "":  # Some string
            # Write specific function call to write variable
            pass
        else:
            pass

        return value

    def write_parameter(
        self, parameter_name: str, parameter_value: str, function: str = None
    ) -> str:
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
        :since:     ODOULS.3 (7.1.15.3)
        """
        value = ""
        if function is None:
            q_command = (
                self.foba_commands["write"]
                + str(parameter_name)
                + " "
                + str(parameter_value)
                + "\r\n"
            )
            result = self.client.send(
                data=q_command, encoding="ascii", response_time=0.5
            )
            result = result.split(",")
            value = self._process_response(
                result=result,
                expected="!",
                actual_idx=0,
                data_idx=0,
            )
        elif function == "":  # Some string
            # Write specific function call to write parameter
            pass
        else:
            pass

        return value

    def read_parameter(self, parameter_name: str, function: str = None) -> str:
        """
        Method to read the specified parameter from the device

        :param parameter:
                    The parameter to write - string

        :param function:
                    Optional parameter to provide the name of a function to run - string

        :return:    value - string

        :author:    tylerjm@flexxbotics.com
        :since:     ODOULS.3 (7.1.15.3)
        """
        value = ""
        if function is None:
            q_command = self.foba_commands["read"] + " " + str(parameter_name) + "\r\n"
            result = self.client.send(
                data=q_command, encoding="ascii", response_time=0.5
            )
            result = result.split(",")
            value = self._process_response(
                result=result,
                expected="MACRO",
                actual_idx=0,
                data_idx=1,
            )
        elif function == "":  # Some string
            # Write specific function call to read variable
            pass
        else:
            pass

        return value

    def read_file_names(self) -> list:
        """
        Method to get a list of filenames from the device

        :return:    list of filenames

        :author:    tylerjm@flexxbotics.com
        :since:     KEYSTONE.4 (7.1.11.4)
        """

        # Return list of available filenames from the device
        self.programs = []

        return self.programs  # TODO is this the actual response object we want?

    def read_file(self, file_name: str) -> str:
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

    def write_file(self, file_name: str, file_data: str):
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

    def load_file(self, file_name: str):
        """
        Loads a file into memory on the device

        :param file_name: the name of the file to load into memory
        :return: the file name

        :author:    zacc@flexxbotics.com
        :since:     MODELO.3 (7.1.13.3)
        """
        pass

    # ############################################################################## #
    # INTERFACE HELPER METHODS
    #
    # These are private methods with naming convention _some_method(self). These are used to faciliate
    # any specific functions that are needed to communicate via the transformer. For example,
    # connection methods, read/write methods, specific functions, etc.
    # ############################################################################## #
