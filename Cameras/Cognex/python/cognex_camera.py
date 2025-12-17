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
import string
import time

from data_models.device import Device
import json
import base64
from transformers.abstract_device import AbstractDevice
from protocols import modbus
import sys
"""

    THIS IS A TEMPLATE. Be wary about making changes directly to it. It is meant to serve as guidance to future
    device interface developers. Order of operations to use this file should be:
        1. Make a copy of template.py and rename it as device_interface_name.py.
        2. Make edits directly in the copied file

"""

class CognexCamera(AbstractDevice):

    def __init__(self, device: Device):
        """
        Template device class. Inherits AbstractDevice class.

        :param Device:
                    the device object

        :return:    a new instance
        """
        # Get meta data of the device from its attributes, this contains information such as: ip address, ports, etc
        super().__init__(device)


        # modbusTCP setup
        self.meta_data = device.metaData
        self.CAMERA_IP = self.meta_data["ip_address"]
        self.CAMERA_PORT = self.meta_data["port"]
        self._client = modbus.ModbusTCP(self.CAMERA_IP, self.CAMERA_PORT)
        self._client.connect()

        # register setup
        self.STATUS_REGISTER = 100
        self.CONTROL_REGISTER = 0
        self.MAX_REGS_PER_READ = 125

        # String command send setup
        self.STRING_COMMAND_LENGTH_ADDR = 1000
        self.STRING_COMMAND_START_ADDR = 1001
        self.INITIATE_STRING_COMMAND_COIL = 17
        self.ACK_INPUT_BIT = 17
        self.ERROR_INPUT_BIT = 18

        #String command recieve setup
        self.RESULT_CODE_ADDR = 1000  # Input Register
        self.RESULT_LENGTH_ADDR = 1001
        self.RESULT_START_ADDR = 1002

        #String commands
        self.SET_ONLINE = "SO1"
        self.SET_OFFLINE = "SO0"
        self.GET_VARIABLE = "GV"
        self.TRIGGER = "MT"
        self.GET_FILE_LIST = "Get FileList"



    def __del__(self):
        pass

    # ############################################################################## #
    # DEVICE COMMUNICATION METHODS
    # ############################################################################## #

    def _execute_command(self, command : str) -> str:
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
            pass

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
        pass

    def _read_status(self, function : str=None) -> str:
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
            response = self._client.read_holding_register(self.STATUS_REGISTER, count=2)
            reg1 = response.registers[0]
            reg2 = response.registers[1]
            byte0 = reg1 & 0xFF
            byte1 = reg2 & 0xFF

            online_status = bool(byte0 & 0x80)
            program_status = bool(byte1 & 0x01)
            error_status = bool(byte1 & 0x80)

            if response.isError():
                return "ERROR GETTING RESPONSE"
            print(sys.executable)
            return "ONLINE" if bool(byte0 & 0x80) else "OFFLINE"
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

        value = self._send_string_command("GV" + variable_name)

        # value = self.send_string_command("GI")
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
            response = self._send_string_command("SW8")
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
        programs_string = self._send_string_command("Get FileList")
        programs = programs_string.splitlines()
        return programs #TODO is this the actual response object we want?

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

        return self._send_string_command("RJ" + file_name)

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

        #For Cognex Jobs the file name corresponds to an int at the start of the job name

        #Camera starts online and needs to be offline to change jobs
        self._send_string_command("SO0")
        self._send_string_command("SJ" + file_name)
        current_job = self._send_string_command("GJ")
        self._send_string_command("SO1")

        return "Current Job Set To: " + self._send_string_command("RJ" + current_job)
    # ############################################################################## #
    # INTERFACE HELPER METHODS
    #
    # These are private methods with naming convention _some_method(self). These are used to faciliate
    # any specific functions that are needed to communicate via the transformer. For example,
    # connection methods, read/write methods, specific functions, etc.
    # ############################################################################## #

    def _read_input_registers_chunked(self, start_addr, count):
        """Read Modbus input registers in chunks of max 125."""
        all_regs = []
        addr = start_addr
        remaining = count

        while remaining > 0:
            this_count = min(self.MAX_REGS_PER_READ, remaining)
            response = self._client.read_input_register(address=addr, count=this_count)
            if not response or not hasattr(response, "registers"):
                print(f"Failed to read {this_count} registers from {addr}")
                break
            all_regs.extend(response.registers)
            addr += this_count
            remaining -= this_count

        return all_regs

    def _decode_registers_to_string(self, registers, high_byte_first=False):
        """Convert Modbus registers to ASCII string."""
        chars = []
        for reg in registers:
            if high_byte_first:
                # Reads high byte first
                high = (reg >> 8) & 0xFF
                low = reg & 0xFF
                if high != 0:
                    chars.append(chr(high))
                if low != 0:
                    chars.append(chr(low))
            else:
                # Reads low byte first
                chars.append(chr(reg & 0xFF))  # Low byte first
                high = (reg >> 8) & 0xFF
                if high != 0:
                    chars.append(chr(high))
        return ''.join(chars).strip('\x00')

    def _send_string_command(self, command: str):
        """Initiate String Command Via ModbusTCP"""
        ascii_vals = [ord(c) for c in command]
        if len(ascii_vals) % 2 != 0:
            ascii_vals.append(0)

        words = [(ascii_vals[i + 1] << 8) | ascii_vals[i] for i in range(0, len(ascii_vals), 2)]

        # write the length of the command
        self._client.write_multiple_registers(address=self.STRING_COMMAND_LENGTH_ADDR, values=[len(command)])

        # write the command itself
        self._client.write_multiple_registers(address=self.STRING_COMMAND_START_ADDR, values=words)

        # initiate the command
        self._client.write_single_coil(self.INITIATE_STRING_COMMAND_COIL, value=True)

        # wait for acknowledgment of command

        for _ in range(50):
            ack = self._client.read_discrete_inputs(self.ACK_INPUT_BIT, count=1).bits[0]
            if ack:
                break
            time.sleep(0.1)
        else:
            print("Timeout waiting for ACK")
            return "timed out waiting for ACK"

        error = self._client.read_discrete_inputs(self.ERROR_INPUT_BIT, count=1).bits[0]
        if error:
            error_response = self._read_string_command_results()
            print("error response", error_response)
            return "error"

            # Clear Initiate String Command
        self._client.write_single_coil(self.INITIATE_STRING_COMMAND_COIL, False)

        return self._read_string_command_results()

    def _read_string_command_results(self):
        """Read result code"""
        result_code = self._client.read_input_register(self.RESULT_CODE_ADDR, count=1).registers[0]
        print("result code ", result_code)
        if result_code != 1:
            print(f"Result Code: {result_code} (Fail)")
            return result_code

        # Read result length
        result_length = self._client.read_input_register(self.RESULT_LENGTH_ADDR, count=1).registers[0]
        print("result Length", result_length)
        reg_count = (result_length + 1) // 2
        if reg_count > 0:
            result_regs = self._read_input_registers_chunked(self.RESULT_START_ADDR, count=reg_count)
            result_str = self._decode_registers_to_string(result_regs)
            result_str = result_str[:result_length]  # Truncate to exact length

        else:
            result_str = "command was sent with no response"

        return result_str