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
from transformers.abstract_device import AbstractDevice
import os
import shutil
import time


class Okuma(AbstractDevice):

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

        self.client = TCP(address=self.address, port=self.port, timeout=5)

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
        skip_sending = False
        self._info(message="Sending command: " + command_name)
        try:
            if command_name == "read_machine_offset":
                thinc_command = "GET_MACHINE_OFFSET:" + args["offset_axis"]
            elif command_name == "add_machine_offset":
                thinc_command = (
                    "ADD_MACHINE_OFFSET:"
                    + args["offset_axis"]
                    + ":"
                    + args["offset_value"]
                )
            elif command_name == "set_machine_offset":
                thinc_command = (
                    "SET_MACHINE_OFFSET:"
                    + args["offset_axis"]
                    + ":"
                    + args["offset_value"]
                )
            elif command_name == "get_current_tool":
                thinc_command = (
                    "GET_CURRENT_TOOL"
                )
            elif command_name == "read_tool_offset":
                thinc_command = (
                    "GET_TOOL_OFFSET:" + args["tool_num"] + ":" + args["tool_comp"]
                )
            elif command_name == "add_tool_offset":
                thinc_command = (
                    "ADD_TOOL_OFFSET:"
                    + args["tool_num"]
                    + ":"
                    + args["tool_comp"]
                    + ":"
                    + args["offset_value"]
                )
            elif command_name == "set_tool_offset":
                thinc_command = (
                    "SET_TOOL_OFFSET:"
                    + args["tool_num"]
                    + ":"
                    + args["tool_comp"]
                    + ":"
                    + args["offset_value"]
                )
            elif command_name == "set_cutter_comp_offset":
                thinc_command = (
                    "SET_CUTTER_COMP_OFFSET:"
                    + args["tool_num"]
                    + ":"
                    + args["tool_comp"]
                    + ":"
                    + args["offset_value"]
                )
            elif command_name == "add_cutter_comp_offset":
                thinc_command = (
                    "ADD_CUTTER_COMP_OFFSET:"
                    + args["tool_num"]
                    + ":"
                    + args["tool_comp"]
                    + ":"
                    + args["offset_value"]
                )
            elif command_name == "set_cutter_comp_wear_offset":
                thinc_command = (
                    "SET_CUTTER_COMP_WEAR_OFFSET:"
                    + args["tool_num"]
                    + ":"
                    + args["tool_comp"]
                    + ":"
                    + args["offset_value"]
                )
            elif command_name == "add_cutter_comp_wear_offset":
                thinc_command = (
                    "ADD_CUTTER_COMP_WEAR_OFFSET:"
                    + args["tool_num"]
                    + ":"
                    + args["tool_comp"]
                    + ":"
                    + args["offset_value"]
                )
            elif command_name == "set_cutter_offset":
                thinc_command = (
                    "SET_CUTTER_OFFSET:"
                    + args["tool_num"]
                    + ":"
                    + args["tool_comp"]
                    + ":"
                    + args["offset_value"]
                )
            elif command_name == "add_cutter_offset":
                thinc_command = (
                    "ADD_CUTTER_OFFSET:"
                    + args["tool_num"]
                    + ":"
                    + args["tool_comp"]
                    + ":"
                    + args["offset_value"]
                )
            elif command_name == "set_cutter_wear_offset":
                thinc_command = (
                    "SET_CUTTER_WEAR_OFFSET:"
                    + args["tool_num"]
                    + ":"
                    + args["tool_comp"]
                    + ":"
                    + args["offset_value"]
                )
            elif command_name == "add_cutter_wear_offset":
                thinc_command = (
                    "ADD_CUTTER_WEAR_OFFSET:"
                    + args["tool_num"]
                    + ":"
                    + args["tool_comp"]
                    + ":"
                    + args["offset_value"]
                )
            elif command_name == "read_workpiece_offset":
                thinc_command = (
                    "GET_WORKPIECE_OFFSET:"
                    + args["offset_axis"]
                    + ":"
                    + args["axis_index"]
                )
            elif command_name == "add_workpiece_offset":
                thinc_command = (
                    "ADD_WORKPIECE_OFFSET:"
                    + args["offset_axis"]
                    + ":"
                    + args["axis_index"]
                    + ":"
                    + args["offset_value"]
                )
            elif command_name == "set_workpiece_offset":
                thinc_command = (
                    "SET_WORKPIECE_OFFSET:"
                    + args["offset_axis"]
                    + ":"
                    + args["axis_index"]
                    + ":"
                    + args["offset_value"]
                )
            elif command_name == "set_program":
                # Set program but don't load it
                thinc_command = (
                        "SET_PROGRAM:"
                        + args["name"]
                )
            elif command_name == "get_remaining_tool_life":
                thinc_command = (
                        "GET_REMAINING_TOOL_LIFE:"
                        + args["tool"]
                )
            elif command_name == "get_tool_life":
                thinc_command = (
                        "GET_TOOL_LIFE:"
                        + args["tool"]
                )
            elif command_name == "get_active_tool":
                thinc_command = (
                        "GET_ACTIVE_TOOL"
                )
            elif command_name == "get_all_tool_life":
                skip_sending = True
                response = self._get_all_tool_life(number_of_tools=args["num_tools"])
            else:
                pass
            if skip_sending:
                pass
            else:
                result = self.client.send(
                    data=thinc_command+"\r\n", encoding="ascii", response_time=0.5
                )
                response = result  # TODO add any post processing required
        except Exception as e:
            raise Exception(
                "Error when sending command, did not get response from device: "
                + command_name
            )
        finally:
            pass

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
            data = "GET_STATUS" + "\r\n"
            result = self.client.send(data=data, encoding="utf-8", response_time=0.5)
        elif function == "":  # Some string
            # Write specific function call to read status
            pass
        else:
            pass

        return result

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
            data = "GET_VAR:" + str(variable_name) + "\r\n"
            result = self.client.send(data=data, encoding="utf-8", response_time=0.5)
            value = result  # TODO add any post processing required
        elif function == "":  # Some string
            # Write specific function call to read variable
            pass
        else:
            pass

        return value

    def _write_variable(
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
            data = "SET_VAR:" + variable_name + ":" + str(variable_value) + "\r\n"
            result = self.client.send(data=data, encoding="ascii", response_time=0.5)
            value = result  # TODO add any post processing required
        elif function == "":  # Some string
            # Write specific function call to write variable
            pass
        else:
            pass

        return value

    def _write_parameter(
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
            data = "SET_VAR:" + parameter_name + ":" + str(parameter_value) + "\r\n"
            result = self.client.send(data=data, encoding="ascii", response_time=0.5)
            value = result  # TODO add any post processing required
        elif function == "":  # Some string
            # Write specific function call to write variable
            pass
        else:
            pass

        return value

    def _read_parameter(self, parameter_name: str, function: str = None) -> str:
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
            data = "GET_VAR:" + str(parameter_name) + "\r\n"
            result = self.client.send(data=data, encoding="utf-8", response_time=0.5)
            value = result  # TODO add any post processing required
        elif function == "":  # Some string
            # Write specific function call to read variable
            pass
        else:
            pass

        return value

    def _load_file(self, file_name: str):
        """
        Loads a file into memory on the device

        :param file_name: the name of the file to load into memory
        :return: the file name

        :author:    cadenc@flexxbotics.com
        :since:     PBR.6 (7.1.16.6)
        """
        data = "SELECT_PROGRAM:" + file_name + "\r\n"
        return self.client.send(data=data, encoding="ascii", response_time=0.5)

    def _get_all_tool_life(self, number_of_tools):
        """
        Loads a file into memory on the device

        :param number_of_tools: the number of tools to get tool life for
        :return: success

        :author:    tylerjm@flexxbotics.com
        :since:     PBR.6 (7.1.16.6)
        """
        number_of_tools = int(number_of_tools)
        try:
            for tool in range(1, number_of_tools + 1):

                thinc_command = (
                        "GET_REMAINING_TOOL_LIFE:"
                        + str(tool)
                )

                try:
                    result = self.client.send(
                        data=thinc_command + "\r\n", encoding="ascii", response_time=0.5
                    )
                    time.sleep(0.1)
                    self._send_variable_event(device_id=self.device_id,
                                            variable_name="tool_"+str(tool)+"_life",
                                            value=result.strip())
                except Exception as e:
                    self._logger.error(str(e))
        except Exception as e:
            self._logger.error(str(e))

        return "OK"

    # ############################################################################## #
    # INTERFACE HELPER METHODS
    #
    # These are private methods with naming convention _some_method(self). These are used to faciliate
    # any specific functions that are needed to communicate via the transformer. For example,
    # connection methods, read/write methods, specific functions, etc.
    # ############################################################################## #
