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
from bson import ObjectId

from data_models.device import Device
from data_models.part_count_event import PartCountEvent
from data_models.run_record import RunRecord
from data_models.abstractions.variables.abstract_variable import AbstractVariable

from protocols.tcp import TCP
from protocols.mtconnect import MTConnect
import json
import base64
from transformers.abstract_device import AbstractDevice
from flask import current_app, g
import time

"""

    THIS IS A TEMPLATE. Be wary about making changes directly to it. It is meant to serve as guidance to future
    device interface developers. Order of operations to use this file should be:
        1. Make a copy of template.py and rename it as device_interface_name.py.
        2. Make edits directly in the copied file

"""


class HaasNextGen(AbstractDevice):

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
        self.fileshare_path = self.meta_data["fileshare_path"]
        self.fileshare_username = self.meta_data["fileshare_username"]
        self.fileshare_password = self.meta_data["fileshare_password"]
        self._logger = current_app.config["logger"]

        self.client = TCP(address=self.address, port=self.port)
        self.q_commands = {
            "write": "?E",
            "read": "?Q600",
            "status": "?Q500",
            "get_mode": "?Q104",
            "get_tool_changes": "?Q200",
            "get_current_tool_number": "?Q201",
            "get_power_time": "?Q300",
            "get_motion_time": "?Q301",
            "get_last_cycle": "?Q303",
            "get_previous_cycle": "?Q304",
            "get_part_count": "?Q500",
            "get_active_program": "?Q500"
        }

        self.mtconnect_client = MTConnect(ip_address=self.address, port=8082, path="/current")

        self.internal_part_counter = 0
        self.interval_count = 0
        self.internal_last_program = ""

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
        :since:     ODOULS.3 (7.1.15.3)
        """
        # Parse the command from the incoming request
        args = json.loads(command_args)
        response = ""

        self._info(message="Sending command: " + command_name)
        try:
            command = self.q_commands[command_name] + "\r\n"
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
            elif command_name == "get_active_program":
                expected = "PROGRAM"
                actual_idx = 0
                data_idx = 1
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

    def _read_interval_data(self) -> str:
        """
        Method to read the status of the device on an interval

        :return:    status - string

        :author:    tylerjm@flexxbotics.com
        :author:    sanua@flexxbotics.com

        :since:     ODOULS.3 (7.1.15.3)
        """
        # Get the most recent run record
        run_record: RunRecord = self._run_record_service.get_run_records()[0]
        run_record_id = run_record.id

        # Get statys
        status = self._read_status()

        # Get the active program
        time.sleep(0.5)
        active_program = self.mtconnect_client.read_tag(tag="ncprog")
        active_program = active_program[0]["text"].strip(".nc")
        self._logger.info("Active program: " + active_program)

        # Reset the part count on a program change
        if run_record.partNumber != active_program:
            database_variable: AbstractVariable = self._variable_service.get_variable_by_id_name_or_machine_variable_name(
                machine_variable_name="active_program")
            database_variable.latestValue = active_program
            self._variable_service.update_variable(variable_id=str(database_variable.id), variable=database_variable)
            run_record.partCount = 0
            self.internal_last_program = active_program
        else:
            pass

        # Set the active program on the active run record
        if active_program != "":
            run_record.partNumber = active_program
        else:
            run_record.partNumber = "123456789"
        self._run_record_service.update_run_record(run_record=run_record)

        if status != "RUNNING":
            # Part count events
            raw_cnc_count = int(self._execute_command(command_name="get_part_count", command_args="{}"))
            if self.internal_part_counter == 0:
                self.internal_part_counter = raw_cnc_count
            if raw_cnc_count != self.internal_part_counter:
                # Part count event
                self._logger.debug("Part count detected")
                event: PartCountEvent = PartCountEvent()
                event.deviceId = self.device_id
                self._run_record_service.create_event(event=event)
                self.internal_part_counter = raw_cnc_count
                self._logger.debug("Part count event complete")

        # Variable events approximately every 2 minutes
        if self.interval_count % 60 == 0:
            variables: list[AbstractVariable] = self._variable_service.get_variables_by_device_id(device_id=self.device_id)
            for variable in variables:
                self._device_service.read_device_variable(device_id=self.device_id, variable_name=variable.machineVariableName)
                time.sleep(0.2)

        self.interval_count += 1

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
        tid = getattr(g, 'transaction_id', 'unknown')
        if function is None:
            data = self.q_commands["status"] + "\r\n"
            self._logger.debug(f"transaction_id[{tid}] - HaasNextGen - Sending status command: {data}")
            result = self.client.send(data=data, encoding="ascii", response_time=0.5)
            self._logger.debug(f"transaction_id[{tid}] - HaasNextGen - Got status response: {result}")
            result = result.split(",")
            status = self._process_status(result=result)

            spindle_speed = float(self.mtconnect_client.read_tag(tag="sspeed")[0]["text"].strip())
            self._logger.info("Spindle Speed: " + str(spindle_speed))
            if spindle_speed <= 1.0 and status == "RUNNING":
                status = "IDLE_SPINDLE"


            time.sleep(0.5)
            alarm_status = self.mtconnect_client.read_tag(tag="aalarms")
            self._logger.info(str(alarm_status))
            alarm_data = self.mtconnect_client.read_tag(tag="aalarms")[0]
            if alarm_data["alarms"]:
                alarm_status = alarm_data["alarms"][0]["text"].strip()
            else:
                alarm_status = alarm_data["text"].strip()
            self._logger.info("Alarm Status: " + alarm_status)
            if alarm_status == "NO ACTIVE ALARMS":
                pass
            else:
                status = alarm_status

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
        tid = getattr(g, 'transaction_id', 'unknown')
        if function is None:
            q_command = self.q_commands["read"] + " " + str(variable_name) + "\r\n"
            self._logger.debug(f"transaction_id[{tid}] - HaasNextGen - Sending macro read command: {q_command}")
            result = self.client.send(data=q_command, encoding="ascii", response_time=0.5)
            self._logger.debug(f"transaction_id[{tid}] - HaasNextGen - Got macro response: {result}")
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
        :since:     ODOULS.3 (7.1.15.3)
        """
        value = ""
        if function is None:
            q_command = self.q_commands["write"] + str(variable_name) + " " + str(variable_value) + "\r\n"
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
        :since:     ODOULS.3 (7.1.15.3)
        """
        value = ""
        if function is None:
            q_command = self.q_commands["write"] + str(parameter_name) + " " + str(parameter_value) + "\r\n"
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
            q_command = self.q_commands["read"] + " " + str(parameter_name) + "\r\n"
            result = self.client.send(data=q_command, encoding="ascii", response_time=0.5)
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
        pass

    # ############################################################################## #
    # INTERFACE HELPER METHODS
    #
    # These are private methods with naming convention _some_method(self). These are used to faciliate
    # any specific functions that are needed to communicate via the transformer. For example,
    # connection methods, read/write methods, specific functions, etc.
    # ############################################################################## #

    def _process_status(self, result: list):
        print("Process status: ")
        print(result)
        if result[0] == "STATUSBUSY":
            return "RUNNING"
        if result[0] == "PROGRAM":
            return result[2]
        if result[0] == '':
            return "BLANKSTRING"
        if 'STATUSBUSY' in result[0]:
            return "RUNNING"

        return "ERROR"

    def _process_response(self, result, expected, actual_idx, data_idx):
        if expected == result[actual_idx]:
            value = result[data_idx]
            return value
        else:
            self._error(message="Error reading variable from device")
