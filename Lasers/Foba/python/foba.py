"""
    :copyright: (c) 2022-2024, Flexxbotics, a Delaware corporation (the "COMPANY")
        All rights reserved.

        THIS SOFTWARE IS PROVIDED BY THE COMPANY ''AS IS'' AND ANY
        EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
        WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
        DISCLAIMED. IN NO EVENT SHALL THE COMPANY BE LIABLE FOR ANY
        DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
        (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
        LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
        ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
        (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
        SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
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
        self.foba_commands = {
            "write": "SETVAR:",
            "read": "GETVAR:", #TODO not sure this is correct, check documentation
            "status": "GETSTATUS",
            "load_program": "LOADJOB:",
            "start_job": "STARTJOB",
            "open_door": "OPENLIFTINGDOOR",
            "close_door": "CLOSELIFTINGDOOR",
            "enable_lots": "SETLOTSENABLED:",
            "set_lot_size": "SETLOTSIZE:"
        }

    def __del__(self):
        pass

    # ############################################################################## #
    # DEVICE COMMUNICATION METHODS
    # ############################################################################## #

    def execute_command(self, command_name : str, command_args : str) -> str:
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
                data_idx=data_idx
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

    def read_status(self, function : str=None) -> str:
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
            data = self.foba_commands["status"] + "\r\n"
            result = self.client.send(data=data, encoding="ascii", response_time=0.5)
            result = result.split(",")
            status = self._process_status(result=result)
        elif function == "": # Some string
            # Write specific function call to read status
            pass
        else:
            pass

        return status

    def read_variable(self, variable_name : str, function : str=None) -> str:
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
            result = self.client.send(data=q_command, encoding="ascii", response_time=0.5)
            result = result.split(",")
            value = self._process_response(
                result=result,
                expected="MACRO",
                actual_idx=0,
                data_idx=1,
            )
        elif function == "": # Some string
            # Write specific function call to read variable
            pass
        else:
            pass

        return value

    def write_variable(self, variable_name : str, variable_value : str, function : str=None) -> str:
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
            q_command = self.foba_commands["write"] + str(variable_name) + " " + str(variable_value) + "\r\n"
            result = self.client.send(data=q_command, encoding="ascii", response_time=0.5)
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

    def write_parameter(self, parameter_name : str, parameter_value : str, function : str=None) -> str:
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
            q_command = self.foba_commands["write"] + str(parameter_name) + " " + str(parameter_value) + "\r\n"
            result = self.client.send(data=q_command, encoding="ascii", response_time=0.5)
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

    def read_parameter(self, parameter_name : str, function : str=None) -> str:
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
            result = self.client.send(data=q_command, encoding="ascii", response_time=0.5)
            result = result.split(",")
            value = self._process_response(
                result=result,
                expected="MACRO",
                actual_idx=0,
                data_idx=1,
            )
        elif function == "": # Some string
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

        return self.programs #TODO is this the actual response object we want?

    def read_file(self, file_name : str) -> str:
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

    def write_file(self, file_name : str, file_data : str):
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

    def load_file(self, file_name : str):
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

    def _process_status(self, result : list):
        print ("Process status: ")
        print (result)
        if result[0] == "STATUSBUSY":
            return result[0]
        if result[0] == "PROGRAM":
            return result[2]
        if result[0] == '':
            return "BLANKSTRING"
        if 'STATUSBUSY' in result[0]:
            return "STATUSBUSY"

        return "ERROR"

    def _process_response(self, result, expected, actual_idx, data_idx):
        if expected == result[actual_idx]:
            value = result[data_idx]
            return value
        else:
            self._error(message="Error reading variable from device")


