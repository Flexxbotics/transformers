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
import json
import base64
from data_models.device import Device
from transformers.abstract_device import AbstractDevice
from protocols.mitsubishi_melsec_mc import MelsecMC, MelsecWrite


class MitsubishiMelsec(AbstractDevice):
    """
    Transformer for Mitsubishi MELSEC PLCs using the MC Protocol (3E / 1E frame).

    Supported series (configured via metaData.plc_type):
      iQ-R  : R04CPU, R08CPU, R16CPU, R32CPU, R120CPU
      iQ-F  : FX5U, FX5UC, FX5UJ, FX5S
      Q     : Q03UDECPU, Q04UDEHCPU, Q06UDEHCPU, Q13UDEHCPU, Q26UDEHCPU
      L     : L02SCPU, L06CPU, L26CPU-BT

    Connection parameters (all in metaData):
      ip_address          : PLC IP address
      port                : TCP port (default 5007 for 3E frame)
      frame_type          : "3E" (default) or "1E"
      plc_type            : "Q" | "iQ-R" | "iQ-F" | "L" | "QnA"
      timeout             : socket timeout in seconds (default 2.0)
      retry               : connection retry attempts (default 2)
      network             : Melsec network number (default 0)
      pc_number           : Melsec PC/station number (default 255)
      status_device       : device to poll for _read_status (default "SM400")
      interval_devices    : comma-separated device list for _read_interval_data
    """

    def __init__(self, device: Device):
        super().__init__(device)

        self.meta_data = device.metaData or {}

        self.ip_address     = self.meta_data.get("ip_address", "")
        self.port           = int(self.meta_data.get("port", 5007))
        self.frame_type     = self.meta_data.get("frame_type", "3E")
        self.plc_type       = self.meta_data.get("plc_type", "Q")
        self.timeout        = float(self.meta_data.get("timeout", 2.0))
        self.retry          = int(self.meta_data.get("retry", 2))
        self.network        = int(self.meta_data.get("network", 0))
        self.pc_number      = int(self.meta_data.get("pc_number", 0xFF))
        self.status_device  = self.meta_data.get("status_device", "SM400")
        self.interval_devices = [
            d.strip()
            for d in self.meta_data.get("interval_devices", "").split(",")
            if d.strip()
        ]

        self._client = MelsecMC(
            ip_address=self.ip_address,
            port=self.port,
            frame_type=self.frame_type,
            plc_type=self.plc_type,
            timeout=self.timeout,
            retry=self.retry,
            network=self.network,
            pc_number=self.pc_number,
            auto_connect=True,
        )

        self.status = "transformer Initiated"

    def __del__(self):
        try:
            self._client.disconnect()
        except Exception:
            pass

    # ############################################################################## #
    # DEVICE COMMUNICATION METHODS
    # ############################################################################## #

    def _execute_command(self, command: str) -> str:
        """
        Legacy command interface. Parses commandJson and delegates to _execute_command_v2.
        """
        command_string = command["commandJson"]
        command_json = json.loads(command_string)
        command_name = command_json.get("command", "")
        command_args = json.dumps({k: v for k, v in command_json.items() if k != "command"})

        self._info(message=f"Sending command: {command_string}")
        return self._execute_command_v2(command_name, command_args)

    def _execute_command_v2(self, command_name: str, command_args: str) -> str:
        """
        Execute a named command against the Mitsubishi PLC.

        Supported commands
        ------------------
        connect
            Explicitly open TCP connection to the PLC.

        disconnect
            Close TCP connection.

        read_words
            Read consecutive word-unit (D, W, R, …) devices.
            Args: device (str), count (int, default 1)
            Returns: {"device": "D100", "count": 1, "values": [...]}

        read_bits
            Read consecutive bit-unit (M, X, Y, …) devices.
            Args: device (str), count (int, default 1)
            Returns: {"device": "M0", "count": 1, "values": [...]}

        write_words
            Write consecutive word-unit devices.
            Args: device (str), values ([int, ...])

        write_bits
            Write consecutive bit-unit devices (0 or 1).
            Args: device (str), values ([int, ...])

        random_read
            Non-contiguous read across word and/or bit devices in one round-trip.
            Args: word_devices ([str]), bit_devices ([str])
            Returns: {"word": {"D0": 10, ...}, "bit": {"M0": 1, ...}}

        random_write
            Non-contiguous write across word and/or bit devices in one round-trip.
            Args: word_devices ([str]), word_values ([int]),
                  bit_devices ([str]), bit_values ([int])
        """
        args = json.loads(command_args) if command_args else {}
        self._info(message=f"Sending command: {command_name}")

        try:
            if command_name == "connect":
                rc = self._client.connect()
                return json.dumps({"status": "ok" if rc == 0 else "error", "rc": rc})

            if command_name == "disconnect":
                rc = self._client.disconnect()
                return json.dumps({"status": "ok" if rc == 0 else "error", "rc": rc})

            if command_name == "read_words":
                device = str(args["device"])
                count  = int(args.get("count", 1))
                values = self._client.read_words(device, count)
                return json.dumps({"status": "ok", "device": device,
                                   "count": count, "values": values})

            if command_name == "read_bits":
                device = str(args["device"])
                count  = int(args.get("count", 1))
                values = self._client.read_bits(device, count)
                return json.dumps({"status": "ok", "device": device,
                                   "count": count, "values": values})

            if command_name == "write_words":
                device = str(args["device"])
                values = [int(v) for v in args["values"]]
                self._client.write_words(device, values)
                return json.dumps({"status": "ok", "device": device, "values": values})

            if command_name == "write_bits":
                device = str(args["device"])
                values = [int(v) for v in args["values"]]
                self._client.write_bits(device, values)
                return json.dumps({"status": "ok", "device": device, "values": values})

            if command_name == "random_read":
                word_devices = args.get("word_devices", [])
                bit_devices  = args.get("bit_devices", [])
                result = self._client.random_read(
                    word_devices=word_devices, bit_devices=bit_devices
                )
                return json.dumps({"status": "ok", **result})

            if command_name == "random_write":
                word_devices = args.get("word_devices", [])
                word_values  = [int(v) for v in args.get("word_values", [])]
                bit_devices  = args.get("bit_devices", [])
                bit_values   = [int(v) for v in args.get("bit_values", [])]
                self._client.random_write(
                    word_devices=word_devices, word_values=word_values,
                    bit_devices=bit_devices, bit_values=bit_values,
                )
                return json.dumps({"status": "ok"})

            raise ValueError(f"Unknown command: {command_name}")

        except Exception as e:
            self._error(message=f"Command '{command_name}' failed: {e}")
            raise Exception(f"Error executing command '{command_name}': {e}")

    def _read_interval_data(self) -> str:
        """
        Periodic poll. Reads the devices listed in metaData.interval_devices
        (comma-separated, e.g. "D0,D1,M0,Y0") in a single random_read call.
        Falls back to _read_status when no interval devices are configured.
        """
        if not self.interval_devices:
            return self._read_status()

        word_devices = [d for d in self.interval_devices if not self._is_bit_device(d)]
        bit_devices  = [d for d in self.interval_devices if self._is_bit_device(d)]

        try:
            result = self._client.random_read(
                word_devices=word_devices, bit_devices=bit_devices
            )
            return json.dumps({"status": "ok", **result})
        except Exception as e:
            self._error(message=f"Interval read failed: {e}")
            return json.dumps({"status": "error", "error": str(e)})

    def _read_status(self, function: str = None) -> str:
        """
        Read PLC run/stop status.

        Reads metaData.status_device (default SM400 — the always-ON special
        relay present on all modern Mitsubishi PLCs).  A value of 1 means the
        CPU is running normally; 0 indicates a stopped or faulted state.

        Common status devices:
          SM400  — always ON (1) when CPU is running  (iQ-R, iQ-F, Q, L)
          SM0    — first-scan flag (momentary, not useful for polling)
          SD0    — PLC type code (word, iQ-R/Q/L)
        """
        try:
            values = self._client.read_bits(self.status_device, 1)
            running = bool(values[0]) if values else False
            self.status = "RUNNING" if running else "STOPPED"
        except Exception as e:
            self._error(message=f"Status read failed: {e}")
            self.status = "ERROR"

        return self.status

    def _read_variable(self, variable_name: str, function: str = None) -> str:
        """
        Read a named device from the PLC.  variable_name is a Melsec device
        address such as "D100" or "M5".
        """
        try:
            if self._is_bit_device(variable_name):
                values = self._client.read_bits(variable_name, 1)
            else:
                values = self._client.read_words(variable_name, 1)
            return str(values[0]) if values else ""
        except Exception as e:
            self._error(message=f"Read variable '{variable_name}' failed: {e}")
            return ""

    def _write_variable(self, variable_name: str, variable_value: str,
                        function: str = None) -> str:
        """
        Write a value to a named device.  variable_name is a Melsec device
        address; variable_value is cast to int.
        """
        try:
            val = int(variable_value)
            if self._is_bit_device(variable_name):
                self._client.write_bits(variable_name, [val])
            else:
                self._client.write_words(variable_name, [val])
            return variable_value
        except Exception as e:
            self._error(message=f"Write variable '{variable_name}' failed: {e}")
            return ""

    def _read_parameter(self, parameter_name: str, function: str = None) -> str:
        """Read a PLC parameter by device address (delegates to _read_variable)."""
        return self._read_variable(parameter_name, function)

    def _write_parameter(self, parameter_name: str, parameter_value: str,
                         function: str = None) -> str:
        """Write a PLC parameter by device address (delegates to _write_variable)."""
        return self._write_variable(parameter_name, parameter_value, function)

    def _run_program(self, function: str = None):
        """Not applicable for Melsec PLCs — programs run continuously on the CPU."""
        self._info(message="run_program called — Melsec PLCs run programs autonomously")
        return ""

    def _read_file_names(self) -> list:
        """Not applicable — Melsec MC Protocol does not expose a file listing API."""
        return []

    def _read_file(self, file_name: str) -> str:
        """Not applicable for Melsec MC Protocol."""
        return base64.b64encode(b"").decode("utf-8")

    def _write_file(self, file_name: str, file_data: str):
        """Not applicable for Melsec MC Protocol."""
        pass

    def _load_file(self, file_name: str):
        """Not applicable for Melsec MC Protocol."""
        pass

    # ############################################################################## #
    # INTERFACE HELPER METHODS
    # ############################################################################## #

    @staticmethod
    def _is_bit_device(device: str) -> bool:
        """Return True when the device prefix indicates a bit-unit device."""
        _BIT_PREFIXES = {"X", "Y", "M", "L", "F", "V", "B", "S", "SC", "TC", "CC"}
        prefix = "".join(c for c in device if c.isalpha()).upper()
        return prefix in _BIT_PREFIXES
