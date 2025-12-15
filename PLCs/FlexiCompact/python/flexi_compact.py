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
import json
import base64
from transformers.abstract_device import AbstractDevice
from protocols.modbus import ModbusTCP
"""

    THIS IS A TEMPLATE. Be wary about making changes directly to it. It is meant to serve as guidance to future
    device interface developers. Order of operations to use this file should be:
        1. Make a copy of template.py and rename it as device_interface_name.py.
        2. Make edits directly in the copied file

"""

class FlexiCompact(AbstractDevice):

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


        self.BASE_IR = 256
        self.B0_IS_MSB = True
        self.CPU_DI_BYTES = [1, 2, 3]  # B1(I1..I8), B2(I9..I16), B3(I17..I20)
        self.CPU_DO_BYTE = 8
        self.MODULES = [
            # name       di_byte  do_byte (or None if no outputs in this block)
            ("XTDI1[1]", 4, None),  # B4
            ("XTDI1[2]", 5, None),  # B5
            ("XTDI1[3]", 6, None),  # B6
            ("XTDO1[4]", 7, 9),  # B7 inputs, B9 outputs
        ]

        self.status = "transformer Initiated"

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


        return self.read_status()

    def _read_status(self, function : str=None) -> str:
        """
        Method to read the status of the device

        :param function:
                    Optional parameter to provide the name of a function to run - string

        :return:    status - string

        :author:    tylerjm@flexxbotics.com
        :since:     ODOULS.3 (7.1.15.3)
        """
        # status = "getting io map"
        self._read_io_map()
        # print(self.io_map)
        ingress_signal = self.io_map["XTDI1[1]"]["di"]["I5"]
        carousel_signal = self.io_map["XTDI1[1]"]["di"]["I8"]
        op_open_signal = self.io_map["XTDI1[2]"]["di"]["I1"]
        op_close_signal = self.io_map["XTDI1[2]"]["di"]["I2"]
        device_E_stops = self.io_map["CPU"]["di"]["I1"]
        CNC_2_4_6 = self.io_map["CPU"]["di"]["I3"]
        CNC_1_3_5 = self.io_map["CPU"]["di"]["I5"]

        SYSTEM_PRESSURE = self.io_map["XTDO1[4]"]["di"]["I2"]
        REQUEST_SAFE = self.io_map["XTDO1[4]"]["do"]["Q8"]
        CNC_1_DCS_STOP = self.io_map["XTDI1[1]"]["di"]["I1"]
        CNC_2_DCS_STOP = self.io_map["XTDI1[1]"]["di"]["I3"]
        CNC_3_DCS_STOP = self.io_map["XTDI1[3]"]["di"]["I1"]
        CNC_4_DCS_STOP = self.io_map["XTDI1[3]"]["di"]["I3"]
        CNC_5_DCS_STOP = self.io_map["XTDI1[3]"]["di"]["I5"]
        CNC_6_DCS_STOP = self.io_map["XTDI1[3]"]["di"]["I7"]


        if REQUEST_SAFE == 1:
            self.status = "SAFE_POSITION_REQUESTED"
        elif carousel_signal == 0:
            self.status = "CAROUSEL_DOOR_OPEN"
        elif ingress_signal == 0:
            self.status = "FENCE_DOOR_OPEN"
        elif op_close_signal == op_open_signal:
            self.status = "OPERATOR_DOOR_OPEN"
        elif device_E_stops == 0:
            self.status = "AUTOMATION_E-STOP_CONSOLE_FENCE_OPERATOR"
        elif CNC_1_3_5 == 0:
            self.status = "CNC_1_3_5_AUTOMATION_E-STOP"
        elif CNC_2_4_6 == 0:
            self.status = "CNC_2_4_6_AUTOMATION_E-STOP"
        elif SYSTEM_PRESSURE == 0:
            self.status = "MAIN_PRESSURE_DROP"
        elif CNC_1_DCS_STOP == 0:
            self.status = "CNC_1_CABIN_DOOR_OPEN"
        elif CNC_2_DCS_STOP == 0:
            self.status = "CNC_2_CABIN_DOOR_OPEN"
        elif CNC_3_DCS_STOP == 0:
            self.status = "CNC_3_CABIN_DOOR_OPEN"
        elif CNC_4_DCS_STOP == 0:
            self.status = "CNC_4_CABIN_DOOR_OPEN"
        elif CNC_5_DCS_STOP == 0:
            self.status = "CNC_5_CABIN_DOOR_OPEN"
        elif CNC_6_DCS_STOP == 0:
            self.status = "CNC_6_CABIN_DOOR_OPEN"
        else:
            self.status = "OK"

        if function is None:
            # Write standard read status statements
            pass
        elif function == "": # Some string
            # Write specific function call to read status
            pass
        else:
            pass

        return self.status

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

    def _bits_to_dict(self,byte_val: int, label_prefix: str, start_idx: int, count: int) -> dict[str, int]:
        """Return {f'{label_prefix}{n}': bit} for bit0..bit(count-1)."""
        return {f"{label_prefix}{start_idx + i}": (byte_val >> i) & 1 for i in range(count)}

    def _read_bytes(self, max_byte: int) -> list[int]:
        """Read bytes B0..B{max_byte} from the Result block and return a list of ints."""
        regs_needed = (max_byte // 2) + 1
        rr = self._client.read_input_register(address=self.BASE_IR, count=regs_needed)
        if rr.isError():
            raise RuntimeError(f"Modbus read error @ IR {self.BASE_IR} count {regs_needed}: {rr}")

        out: list[int] = []
        for w in rr.registers:
            hi = (w >> 8) & 0xFF
            lo = w & 0xFF
            out.extend([hi, lo] if self.B0_IS_MSB else [lo, hi])
        return out

    def _read_io_map(self):
        """
        Build:
          {
            "CPU":      {"di": {"I1":0..}, "do": {"Q1":0..}},
            "XTDI1[1]": {"di": {"I1":0..}, "do": {}},
            "XTDI1[2]": {...},
            "XTDI1[3]": {...},
            "XTDO1[4]": {"di": {"I1":..}, "do": {"Q1":..}}
          }
        """
        # figure out how many bytes we need to read (max of all referenced bytes)
        max_byte = max([*self.CPU_DI_BYTES, self.CPU_DO_BYTE, *(b for _, b, _ in self.MODULES if b is not None),
                        *(d for _, d, _ in self.MODULES if d is not None)], default=0)
        # That comprehension got messy because MODULES has (name, di, do). Let's correct it:
        max_byte = max([*self.CPU_DI_BYTES, self.CPU_DO_BYTE] + [di for _, di, _ in self.MODULES] + [do for _, _, do in self.MODULES if
                                                                                      do is not None])


        if not self._client.connect():
            raise RuntimeError(f"Could not connect to {self.address}:{self.port}")

        block = self._read_bytes(max_byte)

        # CPU DI (I1..I20 across B1..B3)
        cpu_di = {}
        b1, b2, b3 = (block[i] for i in self.CPU_DI_BYTES)
        cpu_di.update(self._bits_to_dict(b1, "I", 1, 8))  # I1..I8
        cpu_di.update(self._bits_to_dict(b2, "I", 9, 8))  # I9..I16
        cpu_di.update(self._bits_to_dict(b3, "I", 17, 4))  # I17..I20

        # CPU DO (Q1..Q4 at B8)
        cpu_do = self._bits_to_dict(block[self.CPU_DO_BYTE], "Q", 1, 4)

        io_map: dict[str, dict[str, dict[str, int]]] = {
            "CPU": {"di": cpu_di, "do": cpu_do}
        }

        # Modules
        for name, di_byte, do_byte in self.MODULES:
            di = self._bits_to_dict(block[di_byte], "I", 1, 8)
            do = self._bits_to_dict(block[do_byte], "Q", 1, 8) if do_byte is not None else {}
            io_map[name] = {"di": di, "do": do}

        self.io_map =  io_map
