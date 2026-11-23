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
from werkzeug.exceptions import HTTPException
from transformers.abstract_device import AbstractDevice
from exceptions.flexxCoreExceptions import (
    FlexxCoreException,
    BadRequestException,
    ServerErrorException,
    ServiceUnavailableException,
)
from protocols.modbus import ModbusTCP

class FlexiCompact(AbstractDevice):
    """
    SICK Flexi Compact safety controller, read over Modbus TCP (input registers
    of the Result block).  _read_status maps the safety I/O to a status word.

    Connection parameters (metaData):
      ip_address : controller IP address (required)
      port       : Modbus TCP port (default 502)

    Commands: connect, disconnect.  The Modbus wrapper opens and closes the
    socket on every call, so connect only prepares the client.

    Errors: an unknown command or bad args raise BadRequestException (400).
    _read_status never raises (it returns "ERROR"), and neither does
    _read_interval_data.
    """

    def __init__(self, device: Device):
        """
        Template device class. Inherits AbstractDevice class.

        :param Device:
                    the device object

        :return:    a new instance
        """
        # Get meta data of the device from its attributes, this contains information such as: ip address, ports, etc

        super().__init__(device)

        self.meta_data = device.metaData or {}

        self.address = self.meta_data.get("ip_address", "")
        self.port = int(self.meta_data.get("port") or 502)  # default is 502
        if not self.address:
            raise ValueError("FlexiCompact: metaData must contain 'ip_address'")
        self._client = None
        self._connected = False


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
        try:
            self._disconnect()
        except Exception:
            pass

    # ############################################################################## #
    # DEVICE COMMUNICATION METHODS
    # ############################################################################## #

    def _execute_command_v2(self, command_name : str, command_args : str) -> str:
        """
        Executes the command sent to the device.

        Supported commands
        ------------------
        connect
            Prepare the Modbus client (the socket is opened per call).
            Returns: {"status": "ok", "connected": true}

        disconnect
            Close and drop the Modbus client.
            Returns: {"status": "ok", "connected": false}

        Errors raise: 400 for invalid args or an unknown command.

        :param command_name:
                    the command to be executed
        :param command_args:
                    json with the arguments for the command

        :return:    the response after execution of command.

        :author:    tylerjm@flexxbotics.com
        :since:     ODOULS.3 (7.1.15.3)
        """
        # Parse the command from the incoming request
        args = self._parse_args(command_args)
        self._info(message=f"Sending command: {command_name}")

        try:
            if command_name == "connect":
                self._connect()
                return json.dumps({"status": "ok", "connected": True})

            if command_name == "disconnect":
                self._disconnect()
                return json.dumps({"status": "ok", "connected": False})

        except Exception as e:
            self._raise_device_error(f"Command '{command_name}'", e)

        raise BadRequestException(f"Unknown command '{command_name}'")

    def _read_interval_data(self) -> str:
        """
        Method to read the status of the device on an interval.  Never raises.

        :return:    status - string

        :author:    tylerjm@flexxbotics.com
        :author:    sanua@flexxbotics.com

        :since:     ODOULS.3 (7.1.15.3)
        """
        try:
            return self.read_status()
        except Exception as e:
            self._error(message=f"Interval read failed: {e}")
            return "ERROR"

    def _read_status(self, function : str=None) -> str:
        """
        Method to read the status of the device

        :param function:
                    Optional parameter to provide the name of a function to run - string

        :return:    status - string ("ERROR" when the controller can't be read; never raises)

        :author:    tylerjm@flexxbotics.com
        :since:     ODOULS.3 (7.1.15.3)
        """
        try:
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
        except Exception as e:
            self._error(message=f"Status read failed: {e}")
            self._connected = False
            self.status = "ERROR"

        return self.status

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


        self._ensure_connected()
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

    def _connect(self) -> None:
        if self._connected and self._client is not None:
            return
        # No probe: the Modbus wrapper opens and closes the socket on every call
        self._client = ModbusTCP(ip_address=self.address, port=self.port)
        self._connected = True

    def _disconnect(self) -> None:
        try:
            if self._client is not None:
                self._client.disconnect()
        finally:
            self._client = None
            self._connected = False

    def _ensure_connected(self) -> None:
        if not self._connected or self._client is None:
            self._connect()

    # ############################################################################## #
    # STANDARD HELPERS (identical in every transformer)
    # ############################################################################## #

    def _execute_command(self, command) -> str:
        """
        Legacy command path, used by Edge HMI control buttons
        (POST /devices/<id>/execute_command). Hands off to _execute_command_v2.
        commandJson may be {"command": "x", "args": {...}} or {"command": "x", <args>}.
        """
        raw = command.get("commandJson") or "{}"
        command_json = json.loads(raw) if isinstance(raw, str) else raw
        args = command_json.get("args")
        if not isinstance(args, dict):
            args = {k: v for k, v in command_json.items() if k != "command"}
        return self._execute_command_v2(command_json.get("command", ""), json.dumps(args))

    @staticmethod
    def _parse_args(command_args) -> dict:
        """
        Parse command_args (the raw request body). Accepts empty, a JSON object, or the
        documented wrapper {"value": "<JSON object string>"}. The wrapper is only unwrapped
        when "value" is the only key, so a real "value" arg is left alone.
        """
        if not command_args:
            return {}
        try:
            args = json.loads(command_args) if isinstance(command_args, (str, bytes)) else command_args
            if isinstance(args, dict) and list(args) == ["value"]:
                inner = args["value"]
                if isinstance(inner, str) and inner.strip().startswith("{"):
                    inner = json.loads(inner)
                if isinstance(inner, dict):
                    return inner
        except ValueError as e:
            raise BadRequestException(f"Command args are not valid JSON: {e}")
        if not isinstance(args, dict):
            raise BadRequestException("Command args must be a JSON object")
        return args

    @staticmethod
    def _require(args: dict, key: str):
        """Return args[key], or raise BadRequestException (HTTP 400) when it is missing or blank."""
        if args.get(key) in (None, ""):
            raise BadRequestException(f"Missing required arg '{key}'")
        return args[key]

    @staticmethod
    def _as_bool(value, default: bool = False) -> bool:
        """metaData / arg booleans: accepts True/False, "true"/"false" (any case), "1"/"0", "yes"/"no"."""
        if isinstance(value, bool):
            return value
        if value is None or str(value).strip() == "":
            return default
        return str(value).strip().lower() in ("1", "true", "yes", "on")

    def _raise_device_error(self, action: str, error: Exception):
        """
        Re-raise a failed device call as the runtime exception the API turns into an HTTP error:
        runtime/HTTP exceptions pass through unchanged; network errors -> 503 (and the transformer
        is marked disconnected so the next call reconnects); anything else -> 500.
        """
        if isinstance(error, (FlexxCoreException, HTTPException)):
            raise error
        if isinstance(error, (OSError, TimeoutError, ConnectionError)):
            self._connected = False
            raise ServiceUnavailableException(f"{action}: device did not respond ({error})") from error
        raise ServerErrorException(f"{action} failed: {error}") from error
