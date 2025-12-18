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
import json
import base64
from transformers.abstract_device import AbstractDevice
from protocols.modbus import ModbusTCP
from types import SimpleNamespace

class YaskawaMP2600(AbstractDevice):

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
        self.port = self.meta_data["port"]  # default is 502
        self._client = ModbusTCP(ip_address=self.address, port=self.port)

        self.BASE_BYTE = 73728
        self.MANUAL_CONTROL_COIL = 0
        self.modbus_map = SimpleNamespace(
            carousel_index =0,
            moving_status = self._qx_index(73728,1),
            enabled_status = self._qx_index(73728,0)
        )

    def __del__(self):
        pass

    # ############################################################################## #
    # DEVICE COMMUNICATION METHODS
    # ############################################################################## #

    def _execute_command_v2(self, command_name : str, command_args : str) -> str:
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
            if command_name == "set_carousel_index":
                response = self._write_modbus_holding_register(self.modbus_map.carousel_index, args["index"])
            if command_name == "set_manual_control":
                flag = args["value"]
                b = flag.lower() == "true"
                response = self._write_modbus_coil(self.MANUAL_CONTROL_COIL, b)

        except Exception as e:
            raise Exception(
                "Error when sending command, did not get response from device: "
                + command_name
            )

        finally:
            pass

        if "ERROR" in response:
            raise Exception("Error returned from device... " + command_name)

        return response

    def _read_interval_data(self) -> str:
        """
        Method to read the status of the device on an interval

        :return:    status - string

        :author:    tylerjm@flexxbotics.com
        :author:    sanua@flexxbotics.com

        :since:     ODOULS.3 (7.1.15.3)
        """
        status = self.read_status()

        return status

    def _read_status(self, function : str=None) -> str:
        """
        Method to read the status of the device

        :param function:
                    Optional parameter to provide the name of a function to run - string

        :return:    status - string

        :author:    tylerjm@flexxbotics.com
        :since:     ODOULS.3 (7.1.15.3)
        """

        moving_status = self._read_modbus_discrete_register(1,1)[0]
        enabled_status = self._read_modbus_discrete_register(0,1)[0]

        if moving_status and enabled_status:
            status = "IDLE"
        elif not enabled_status and not moving_status:
            status = "RUNNING"
        else:
            status = "ERROR"
        # status = str(enabled_status)

        if function is None:
            # Write standard read status statements
            pass
        elif function == "": # Some string
            # Write specific function call to read status
            pass
        else:
            pass

        return status

    def _read_variable(self, variable_name : str, function : str=None) -> str:
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
            # Write standard read variable statements
            pass
        elif function == "": # Some string
            # Write specific function call to read variable
            pass
        else:
            pass

        return value

    def _write_variable(self, variable_name : str, variable_value : str, function : str=None) -> str:
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
            # Write standard write variable statements
            pass
        elif function == "":  # Some string
            # Write specific function call to write variable
            pass
        else:
            pass

        return value

    def _write_parameter(self, parameter_name : str, parameter_value : str, function : str=None) -> str:
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
        if function is None:
            # Write standard write parameter statements
            pass
        elif function == "":  # Some string
            # Write specific function call to write parameter
            pass
        else:
            pass

    def _read_parameter(self, parameter_name : str, function : str=None) -> str:
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
            # Write standard read variable statements
            pass
        elif function == "": # Some string
            # Write specific function call to read variable
            pass
        else:
            pass

        return value

    def _run_program(self, function: str = None):
        """
            Method to run the active program on the device

            :param function:
                        Optional parameter to provide the name of a function to run - string

            :return:    value - string

            :author:    tylerjm@flexxbotics.com
            :since:     P.2 (7.1.16.2)
        """
        try:
            response = ""
        except Exception as e:
            response = "Error sending program"
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

        return self.programs #TODO is this the actual response object we want?

    def _read_file(self, file_name : str) -> str:
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

    def _write_file(self, file_name : str, file_data : str):
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

    def _load_file(self, file_name : str):
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


    def _write_modbus_holding_register(self, address, value):
        wr = self._client.write_single_register(address=address, value=value)
        rr = self._client.read_holding_register(address=address, count=1)
        if rr.isError() or wr.isError():
            response = "failed to write"
        else:
            response = str(rr.registers[0])
        return response

    def _read_modbus_discrete_register(self, address,count):
        rr = self._client.read_discrete_inputs(address=address, count=count)
        print(address, rr)
        if rr.isError():
            raise RuntimeError(f"Modbus error: {rr}")
        return rr.bits

    def _qx_index(self, byte_addr: int, bit: int) -> int:
        BASE = 73728
        return (byte_addr - BASE) * 8 + bit

    def _write_modbus_coil(self, address,value):
        wc = self._client.write_single_coil(address=address,value=value)
        rc = self._client.read_coils(address=address,count=1)
        if rc.isError():
            response = "Error reading coil"
        else:
            response = "manual_control state:" + str(rc.bits[0])
        return response









