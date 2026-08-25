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
from flask import current_app
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
        self.meta_data = device.metaData
        self.address = self.meta_data["ip_address"]
        self.port = self.meta_data["port"]
        self._logger = current_app.config["logger"]

        self.client = TCP(address=self.address, port=self.port)
        self.variable_service = self.core_services.variable_service
        self.alignment_data = {}

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
            if command_name == "get_alignment_status":
                layout_position = args.get("layout_position", 0)
                mark_set_index = args.get("mark_set_index", 0)
                data = FobaCmd.GETALIGNMENTSTATUS.value.build(layout_position, mark_set_index)
                self._info(message=data)
                raw = self.client.send(data=data, encoding="ascii", response_time=0.5)
                command_response = FobaCmd.GETALIGNMENTSTATUS.value.parse(raw)
                self._info(message=command_response)
                if command_response["meaning"] != "OK":
                    return "ERROR"
                extra = command_response["extra"]
                if extra[0] == "NotProcessed":
                    return "NotProcessed"
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
                response = command_response["meaning"]
            if command_name == "get_alignment_data":
                data_index = args.get("data", "")
                return self.alignment_data[data_index]
            if command_name == "get_active_order":
                work_cell = self._work_cell_service.get_work_cell()
                run_record = RunRecordDocument.objects(
                    workCellId=str(work_cell.id), isCompleted=False
                ).first()
                return str(run_record.name) if run_record else ""

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
            status = FobaCmd.STATUS.value.parse(raw=raw)["meaning"]
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
            data = FobaCmd.READ.value.build(variable_name)
            raw = self.client.send(data=data, encoding="ascii", response_time=0.5)
            result = FobaCmd.READ.value.parse(raw)
            if result["meaning"] != "OK":
                self._error(
                    message="Error reading variable '"
                    + str(variable_name)
                    + "' from device: "
                    + result["meaning"]
                )
            else:
                value = result["extra"][0] if result["extra"] else ""
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
            data = FobaCmd.WRITE.value.build(variable_name, variable_value)
            raw = self.client.send(data=data, encoding="ascii", response_time=0.5)
            result = FobaCmd.WRITE.value.parse(raw)
            if result["meaning"] != "OK":
                self._error(
                    message="Error writing variable '"
                    + str(variable_name)
                    + "' to device: "
                    + result["meaning"]
                )
            else:
                value = variable_value
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
            data = FobaCmd.WRITE.value.build(parameter_name, parameter_value)
            raw = self.client.send(data=data, encoding="ascii", response_time=0.5)
            result = FobaCmd.WRITE.value.parse(raw)
            if result["meaning"] != "OK":
                self._error(
                    message="Error writing parameter '"
                    + str(parameter_name)
                    + "' to device: "
                    + result["meaning"]
                )
            else:
                value = parameter_value
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
            data = FobaCmd.READ.value.build(parameter_name)
            raw = self.client.send(data=data, encoding="ascii", response_time=0.5)
            result = FobaCmd.READ.value.parse(raw)
            if result["meaning"] != "OK":
                self._error(
                    message="Error reading parameter '"
                    + str(parameter_name)
                    + "' from device: "
                    + result["meaning"]
                )
            else:
                value = result["extra"][0] if result["extra"] else ""
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
        data = FobaCmd.LOAD.value.build(file_name)
        raw = self.client.send(data=data, encoding="ascii", response_time=0.5)
        status = FobaCmd.LOAD.value.parse(raw=raw)["meaning"]
        return status

    def run_program(self, function: str = None) -> str:
        """
            Method to run the active program on the device

            :param function:
                        Optional parameter to provide the name of a function to run - string

            :return:    value - string

            :author:    tylerjm@flexxbotics.com
            :since:     P.2 (7.1.16.2)
        """
        data = FobaCmd.RUN.value.build()
        raw = self.client.send(data=data, encoding="ascii", response_time=0.5)
        status = FobaCmd.RUN.value.parse(raw=raw)["meaning"]
        return status

    # ############################################################################## #
    # INTERFACE HELPER METHODS
    #
    # These are private methods with naming convention _some_method(self). These are used to faciliate
    # any specific functions that are needed to communicate via the transformer. For example,
    # connection methods, read/write methods, specific functions, etc.
    # ############################################################################## #
