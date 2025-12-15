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
#Library imports
import json
import base64
import time
import requests
from dataclasses import dataclass

# Flexx core references
from data_models.device import Device
from drivers.abstract_device import AbstractDevice
from protocols import modbus

class HeidenhainTNC7_Profinet(AbstractDevice):

    def __init__(self, device: Device):
        """
        Template device class. Inherits AbstractDevice class.

        :param Device:
                    the device object

        :return:    a new instance
        """
        super().__init__(device)
        # Get meta data of the device from its attributes, this contains information such as: ip address, ports, etc
        self.meta_data = device.metaData
        self.PROFINET_PLC_IP = self.meta_data["plc_ip_address"]
        self.PLC_PORT = int(self.meta_data["plc_port"])
        self.CNC_NUMBER = int(self.meta_data["cnc_number"])
        self._client = modbus.ModbusTCP(self.PROFINET_PLC_IP, self.PLC_PORT)

        self.base = 6 * (6 - self.CNC_NUMBER)
        self.modbus_write_profinet_address = self.base + 0  # write address
        self.modbus_write_profinet_value = self.base + 1  # write value
        self.modbus_read_profinet_address = self.base + 2  # read address
        self.modbus_profinet_read_value_address = self.base + 3  # read result
        self.modbus_trigger_write = self.base + 4
        self.modbus_trigger_read = self.base + 5

        self.CNC_NUM_REG = 12




        #Profinet Status Addresses

        @dataclass
        class BitSignal:
            name: str
            address: str
            value: int = 0

        self.bit_signals = {
            "MACHINE_ERROR_STATE": BitSignal("MACHINE_ERROR_STATE", "IX0.4"),
            "PROGRAM_IN_PROG": BitSignal("PROGRAM_IN_PROG", "IX1.7"),
            "OPERATOR_OK": BitSignal("OPERATOR_OK", "IX0.7"),
            "M365": BitSignal("M365", "IX0.5"),
            "LOADING": BitSignal("LOADING", "IX1.1"),
            "NOK_PART": BitSignal("NOK_PART","IX1.6")
        }


        # Needed for connection to TNC Remo Server to load files to memory
        self.cnc_ip_address = self.meta_data["cnc_ip_address"]
        self.host = "http://host.docker.internal"
        self.host_port = 7083
        self.base_url = self.host + ":" + str(self.host_port)

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

        :author:    tylerjm@flexxbotics.com
        :since:     ODOULS.3 (7.1.15.3)
        """
        # Parse the command from the incoming request
        command_string = command["commandJson"]
        command_json = json.loads(command_string)
        command_name = command_json["command"]
        response = ""

        self._info(message="Sending command: " + command_string)
        try:
            pass

        except Exception as e:
            raise Exception(
                "Error when sending command, did not get response"
                + command_name
            )

        finally:
            pass

        if "ERROR" in response:
            raise Exception("Error returned from device.. " + command_name)

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
        # Parse the command from the incoming request
        args = json.loads(command_args)
        response = ""

        self._info(message="Sending command: " + command_name)
        try:

            if command_name == "set_profinet_bit":
                # self._set_profinet_bit(args["profinet_address"],int(args["profinet_value"]))
                self.write_pn_bit(args["profinet_address"],args["profinet_value"],self.CNC_NUMBER)
                response = str(self._client.read_holding_register(self.modbus_write_profinet_address).registers)
            if command_name == "read_profinet_bit":
                response = str(self._read_pn_bit(args["profinet_address"],self.CNC_NUMBER))

            if command_name == "set_multiple_profinet_bits":
                addresses = args["addresses"]
                values = args["values"]
                idx = 0
                response = []
                if len(values) == len(addresses):
                    for add in addresses:
                        self.write_pn_bit(add,values[idx],self.CNC_NUMBER)
                        response.append(str(self._client.read_holding_register(self.modbus_write_profinet_address).registers))
                        idx+=1
                else:
                    raise Exception("length of addresses and values is not the same")

        except Exception as e:
            raise Exception(
                "Error when sending command, did not get response from device: "
                + command_name + "Exception: " + str(e)
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
            # Write standard read status statements



            self._update_bits()
            print(self.bit_signals)
            if self.bit_signals["MACHINE_ERROR_STATE"].value == 0:
                status = "FAULT"
            elif self.bit_signals["OPERATOR_OK"].value == 1 and self.bit_signals["PROGRAM_IN_PROG"].value == 0:
                status = "IDLE"
            elif self.bit_signals["PROGRAM_IN_PROG"].value == 1 and self.bit_signals["LOADING"].value == 0 and self.bit_signals["M365"].value == 0:
                status = "RUNNING"
            elif self.bit_signals["M365"].value == 1:
                status = "PREPARE_FOR_LOADING"
            elif self.bit_signals["LOADING"].value == 1:
                status = "LOADING"
            elif self.bit_signals["OPERATOR_OK"].value == 0:
                status = "DOOR_UNLOCKED"
            else:
                status = "NO MATCH"
            if self.bit_signals["NOK_PART"].value == 1:
                status = status + " NOK_PART"
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
            value = self._read_pn_bit(variable_name, self.CNC_NUMBER)
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
            # Write standard write variable statements
            # self._set_profinet_bit(variable_name, int(variable_value))
            self.write_pn_bit(variable_name, int(variable_value), self.CNC_NUMBER)
            value = str(self._client.read_holding_register(self.modbus_write_profinet_address).registers[0])
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
        if function is None:
            # Write standard write parameter statements
            pass
        elif function == "":  # Some string
            # Write specific function call to write parameter
            pass
        else:
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
        :since:     ODOULS.3 (7.1.15.3)
        """
        value = ""
        if function is None:
            # Write standard read variable statements
            pass
        elif function == "":  # Some string
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

        :author:    tylerjm@flexxbotics.com
        :since:     Q.5 (7.1.17.5)
        """
        # Connects to TNC Remo Server to load the file specified
        resp = requests.post(self.base_url + "/load", json={"filename": file_name, "ip_address": self.cnc_ip_address})

        return str(resp.json())

    # ############################################################################## #
    # INTERFACE HELPER METHODS
    #
    # These are private methods with naming convention _some_method(self). These are used to faciliate
    # any specific functions that are needed to communicate via the driver. For example,
    # connection methods, read/write methods, specific functions, etc.
    # ############################################################################## #

    def _encode_qx(self,addr: str) -> int:
        """'QX0.1' -> doc-bit absolute index (0*8+1 = 1)."""
        byte, bit = addr.upper().replace("QX", "").split(".")
        return int(byte) * 8 + int(bit)

    def _encode_ix(self,addr: str) -> int:
        """'IX7.4' -> doc-bit absolute index (7*8+4 = 60)."""
        byte, bit = addr.upper().replace("IX", "").split(".")
        return int(byte) * 8 + int(bit)


    def write_pn_bit(self, qx_addr: str, value: int, cnc_num: int):
        """
        qx_addr like 'QX0.1' (DOC address), value 0/1, cnc_num 1..6.
        """
        abs_addr = self._encode_qx(qx_addr)
        # Fill command registers
        self._client.write_multiple_registers(self.modbus_write_profinet_address, [abs_addr])  # QW0
        self._client.write_multiple_registers(self.modbus_write_profinet_value, [value])  # QW1
        self._client.write_multiple_registers(self.CNC_NUM_REG, [cnc_num])  # QW4

        # Fire one-shot trigger
        self._client.write_multiple_registers(self.modbus_trigger_write, [1])  # QW5

        # Optional: wait until PLC finished

    def _read_pn_bit(self, ix_addr: str, cnc_num: int) -> int:
        """
        ix_addr like 'IX7.4' (DOC address on input side), returns 0 or 1.
        """
        abs_addr = self._encode_ix(ix_addr)
        # Set up read command
        self._client.write_multiple_registers(self.modbus_read_profinet_address, [abs_addr])  # QW2
        self._client.write_multiple_registers(self.CNC_NUM_REG, [cnc_num])  # QW4
        self._client.write_multiple_registers(self.modbus_trigger_read, [1])  # QW6

        # Read back result from QW3
        rr = self._client.read_holding_register(self.modbus_profinet_read_value_address)
        if rr.isError():
            raise RuntimeError(f"Modbus error reading PN input value: {rr}")
        return rr.registers[0]  # 0 or 1

    def _update_bits(self):
        for sig in self.bit_signals.values():
            sig.value = int(self._read_variable(sig.address))