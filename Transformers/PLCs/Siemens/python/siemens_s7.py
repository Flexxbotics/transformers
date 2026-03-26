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
import struct
import base64
from data_models.device import Device
from transformers.abstract_device import AbstractDevice
from protocols.siemens_s7comm import S7, S7DbWrite

try:
    import snap7.util as _s7util   # type: ignore
    import snap7.types as _s7types  # type: ignore
except Exception:                  # pragma: no cover
    _s7util = None                 # type: ignore
    _s7types = None                # type: ignore


class SiemensS7(AbstractDevice):
    """
    Transformer for Siemens S7 PLCs using the S7comm protocol via python-snap7.

    Supported series (selected by setting the correct rack/slot in metaData):
      S7-1200  : rack=0, slot=1  (CPU 1211C … 1217C)
      S7-1500  : rack=0, slot=1  (CPU 1511 … 1518)
      ET 200SP : rack=0, slot=1  (CPU 1510SP, 1512SP)
      S7-300   : rack=0, slot=2  (CPU 314C, 315, 317, 319)
      S7-400   : rack=0, slot=3  (CPU 412, 414, 416, 417)

    Connection parameters (all in metaData):
      ip_address   : PLC IP address
      rack         : physical rack number (default 0)
      slot         : CPU slot number (default 1; use 2 for S7-300, 3 for S7-400)
      tcp_port     : ISO-on-TCP port (default 102)
      timeout      : socket timeout in seconds (default 2.0)
      retry        : connection retry attempts (default 2)
      status_db    : DB number to probe for _read_status (default 1)
      status_start : byte offset within status_db (default 0)

    S7-specific nuances
    -------------------
    * **Data Blocks (DB)** are the primary data exchange mechanism.  All read
      and write commands operate on DB number + byte offset + size.
    * **PUT/GET access must be enabled** on S7-1200 and S7-1500 PLCs.
      In TIA Portal: PLC Properties → Protection → "Permit access with PUT/GET
      communication from remote partner".  Without this, all connections will
      be refused at the PLC.
    * **Rack/Slot routing**: S7-300 CPUs sit at slot 2; S7-400 at slot 3;
      S7-1200/1500/ET200SP at slot 1.  Incorrect slot = connection failure.
    * **Data type encoding**: Raw bytes are read from DBs and decoded using
      snap7.util helpers.  The helper commands (read_bool, read_int, read_real,
      etc.) handle this transparently.
    * **CPU state**: Siemens exposes a run/stop state via snap7's
      get_cpu_state() call.  This transformer probes a configurable status DB
      as a connectivity check; a snap7-level CPU state call would require
      protocol-layer enhancement.
    * **Optimised DB access**: TIA Portal's "Optimised block access" setting
      reorders DB offsets at compile time, making absolute byte addressing
      unreliable.  Disable it (uncheck "Optimised block access" in DB
      properties) when using this transformer.
    """

    def __init__(self, device: Device):
        super().__init__(device)

        self.meta_data   = device.metaData or {}
        self.ip_address  = self.meta_data.get("ip_address", "")
        self.rack        = int(self.meta_data.get("rack", 0))
        self.slot        = int(self.meta_data.get("slot", 1))
        self.tcp_port    = int(self.meta_data.get("tcp_port", 102))
        self.timeout     = float(self.meta_data.get("timeout", 2.0))
        self.retry       = int(self.meta_data.get("retry", 2))
        self.status_db   = int(self.meta_data.get("status_db", 1))
        self.status_start = int(self.meta_data.get("status_start", 0))

        self._client = S7(
            ip_address=self.ip_address,
            rack=self.rack,
            slot=self.slot,
            tcp_port=self.tcp_port,
            timeout=self.timeout,
            retry=self.retry,
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
        """Legacy v1 interface — delegates to _execute_command_v2."""
        command_string = command["commandJson"]
        command_json   = json.loads(command_string)
        command_name   = command_json.get("command", "")
        command_args   = json.dumps({k: v for k, v in command_json.items() if k != "command"})
        self._info(message=f"Sending command: {command_string}")
        return self._execute_command_v2(command_name, command_args)

    def _execute_command_v2(self, command_name: str, command_args: str) -> str:
        """
        Execute a named command against the Siemens S7 PLC.

        Supported commands
        ------------------
        connect
            Explicitly open S7comm connection.

        disconnect
            Close S7comm connection.

        read_db
            Read raw bytes from a Data Block.
            Args: db_number (int), start (int), size (int)
            Returns: {"db_number":1, "start":0, "size":4, "data_hex":"...", "data_b64":"..."}

        write_db
            Write raw bytes to a Data Block.
            Args: db_number (int), start (int),
                  data (list[int] 0-255  OR  hex string e.g. "FF01A0")

        read_bool
            Read a single BOOL from a DB.
            Args: db_number (int), byte_offset (int), bit_offset (int 0-7)
            Returns: {"value": true/false}

        write_bool
            Write a single BOOL to a DB.
            Args: db_number (int), byte_offset (int), bit_offset (int 0-7),
                  value (bool)

        read_int
            Read a signed 16-bit INT from a DB (2 bytes).
            Args: db_number (int), start (int)
            Returns: {"value": <int>}

        write_int
            Write a signed 16-bit INT to a DB.
            Args: db_number (int), start (int), value (int)

        read_real
            Read a 32-bit IEEE-754 REAL (float) from a DB (4 bytes).
            Args: db_number (int), start (int)
            Returns: {"value": <float>}

        write_real
            Write a 32-bit REAL to a DB.
            Args: db_number (int), start (int), value (float)

        read_dword
            Read an unsigned 32-bit DWORD from a DB (4 bytes).
            Args: db_number (int), start (int)
            Returns: {"value": <int>}

        write_dword
            Write an unsigned 32-bit DWORD to a DB.
            Args: db_number (int), start (int), value (int)
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

            if command_name == "read_db":
                db    = int(args["db_number"])
                start = int(args["start"])
                size  = int(args["size"])
                data  = self._client.read_db(db, start, size)
                return json.dumps({
                    "status": "ok", "db_number": db, "start": start,
                    "size": size, "data_hex": data.hex(),
                    "data_b64": base64.b64encode(data).decode(),
                })

            if command_name == "write_db":
                db    = int(args["db_number"])
                start = int(args["start"])
                raw   = args["data"]
                data  = self._coerce_bytes(raw)
                self._client.send(S7DbWrite(db_number=db, start=start, data=data))
                return json.dumps({"status": "ok", "db_number": db,
                                   "start": start, "size": len(data)})

            if command_name == "read_bool":
                db         = int(args["db_number"])
                byte_off   = int(args["byte_offset"])
                bit_off    = int(args["bit_offset"])
                raw        = self._client.read_db(db, byte_off, 1)
                value      = self._get_bool(raw, 0, bit_off)
                return json.dumps({"status": "ok", "value": value})

            if command_name == "write_bool":
                db         = int(args["db_number"])
                byte_off   = int(args["byte_offset"])
                bit_off    = int(args["bit_offset"])
                value      = bool(args["value"])
                raw        = self._client.read_db(db, byte_off, 1)
                self._set_bool(raw, 0, bit_off, value)
                self._client.send(S7DbWrite(db_number=db, start=byte_off, data=bytes(raw)))
                return json.dumps({"status": "ok", "value": value})

            if command_name == "read_int":
                db    = int(args["db_number"])
                start = int(args["start"])
                raw   = self._client.read_db(db, start, 2)
                value = self._get_int(raw, 0)
                return json.dumps({"status": "ok", "value": value})

            if command_name == "write_int":
                db    = int(args["db_number"])
                start = int(args["start"])
                value = int(args["value"])
                data  = self._set_int(value)
                self._client.send(S7DbWrite(db_number=db, start=start, data=data))
                return json.dumps({"status": "ok", "value": value})

            if command_name == "read_real":
                db    = int(args["db_number"])
                start = int(args["start"])
                raw   = self._client.read_db(db, start, 4)
                value = self._get_real(raw, 0)
                return json.dumps({"status": "ok", "value": value})

            if command_name == "write_real":
                db    = int(args["db_number"])
                start = int(args["start"])
                value = float(args["value"])
                data  = self._set_real(value)
                self._client.send(S7DbWrite(db_number=db, start=start, data=data))
                return json.dumps({"status": "ok", "value": value})

            if command_name == "read_dword":
                db    = int(args["db_number"])
                start = int(args["start"])
                raw   = self._client.read_db(db, start, 4)
                value = self._get_dword(raw, 0)
                return json.dumps({"status": "ok", "value": value})

            if command_name == "write_dword":
                db    = int(args["db_number"])
                start = int(args["start"])
                value = int(args["value"])
                data  = self._set_dword(value)
                self._client.send(S7DbWrite(db_number=db, start=start, data=data))
                return json.dumps({"status": "ok", "value": value})

            raise ValueError(f"Unknown command: {command_name}")

        except Exception as e:
            self._error(message=f"Command '{command_name}' failed: {e}")
            raise Exception(f"Error executing command '{command_name}': {e}")

    def _read_interval_data(self) -> str:
        """
        Periodic poll. Reads metaData.status_db at metaData.status_start (4 bytes)
        and returns the raw hex payload alongside the current status string.
        """
        try:
            data = self._client.read_db(self.status_db, self.status_start, 4)
            self.status = "RUNNING"
            return json.dumps({
                "status": self.status,
                "db_number": self.status_db,
                "start": self.status_start,
                "data_hex": data.hex(),
            })
        except Exception as e:
            self._error(message=f"Interval read failed: {e}")
            self.status = "ERROR"
            return json.dumps({"status": self.status, "error": str(e)})

    def _read_status(self, function: str = None) -> str:
        """
        Probe the PLC by reading 1 byte from metaData.status_db.

        Returns "RUNNING" on success, "STOPPED" or "ERROR" on failure.

        Note: A true run/stop distinction requires python-snap7's
        get_cpu_state() call, which is not exposed by the current protocol
        layer.  Enhance the S7 protocol with a cpu_state() helper if
        run/stop differentiation is required.
        """
        try:
            self._client.read_db(self.status_db, self.status_start, 1)
            self.status = "RUNNING"
        except Exception as e:
            self._error(message=f"Status check failed: {e}")
            self.status = "ERROR"
        return self.status

    def _read_variable(self, variable_name: str, function: str = None) -> str:
        """
        Read a typed value from a DB using dot-notation address strings.

        Address formats:
          "DB1.DBX0.3"   → BOOL  at DB1, byte 0, bit 3
          "DB1.DBW2"     → INT   at DB1, byte 2  (16-bit signed)
          "DB1.DBD4"     → DWORD at DB1, byte 4  (32-bit unsigned)
          "DB1.DBR4"     → REAL  at DB1, byte 4  (32-bit float)
        """
        try:
            parsed = self._parse_address(variable_name)
            db, kind, byte_off, bit_off = parsed

            if kind == "X":
                raw   = self._client.read_db(db, byte_off, 1)
                value = self._get_bool(raw, 0, bit_off)
                return str(int(value))
            elif kind == "W":
                raw   = self._client.read_db(db, byte_off, 2)
                return str(self._get_int(raw, 0))
            elif kind == "D":
                raw   = self._client.read_db(db, byte_off, 4)
                return str(self._get_dword(raw, 0))
            elif kind == "R":
                raw   = self._client.read_db(db, byte_off, 4)
                return str(self._get_real(raw, 0))
        except Exception as e:
            self._error(message=f"Read variable '{variable_name}' failed: {e}")
        return ""

    def _write_variable(self, variable_name: str, variable_value: str,
                        function: str = None) -> str:
        """
        Write a typed value to a DB using the same dot-notation as _read_variable.
        """
        try:
            parsed = self._parse_address(variable_name)
            db, kind, byte_off, bit_off = parsed

            if kind == "X":
                raw = self._client.read_db(db, byte_off, 1)
                self._set_bool(raw, 0, bit_off, bool(int(variable_value)))
                self._client.send(S7DbWrite(db_number=db, start=byte_off, data=bytes(raw)))
            elif kind == "W":
                data = self._set_int(int(variable_value))
                self._client.send(S7DbWrite(db_number=db, start=byte_off, data=data))
            elif kind == "D":
                data = self._set_dword(int(variable_value))
                self._client.send(S7DbWrite(db_number=db, start=byte_off, data=data))
            elif kind == "R":
                data = self._set_real(float(variable_value))
                self._client.send(S7DbWrite(db_number=db, start=byte_off, data=data))
            return variable_value
        except Exception as e:
            self._error(message=f"Write variable '{variable_name}' failed: {e}")
        return ""

    def _read_parameter(self, parameter_name: str, function: str = None) -> str:
        return self._read_variable(parameter_name, function)

    def _write_parameter(self, parameter_name: str, parameter_value: str,
                         function: str = None) -> str:
        return self._write_variable(parameter_name, parameter_value, function)

    def _run_program(self, function: str = None):
        """Not applicable — S7 PLCs run programs autonomously in scan cycles."""
        self._info(message="run_program called — S7 PLCs run programs autonomously")
        return ""

    def _read_file_names(self) -> list:
        """Not supported via S7comm DB access."""
        return []

    def _read_file(self, file_name: str) -> str:
        return base64.b64encode(b"").decode("utf-8")

    def _write_file(self, file_name: str, file_data: str):
        pass

    def _load_file(self, file_name: str):
        pass

    # ############################################################################## #
    # INTERFACE HELPER METHODS
    # ############################################################################## #

    @staticmethod
    def _parse_address(addr: str):
        """
        Parse a TIA Portal-style DB address string into components.

        Supported patterns:
          DB<n>.DBX<byte>.<bit>   → (n, "X", byte, bit)
          DB<n>.DBW<byte>         → (n, "W", byte, 0)
          DB<n>.DBD<byte>         → (n, "D", byte, 0)
          DB<n>.DBR<byte>         → (n, "R", byte, 0)

        Raises ValueError for unrecognised formats.
        """
        import re
        m = re.match(
            r"DB(\d+)\.DB([XWDR])(\d+)(?:\.(\d+))?$",
            addr.strip().upper()
        )
        if not m:
            raise ValueError(
                f"Unrecognised S7 address '{addr}'. "
                f"Expected format: DB1.DBX0.3 | DB1.DBW2 | DB1.DBD4 | DB1.DBR4"
            )
        db      = int(m.group(1))
        kind    = m.group(2)          # X / W / D / R
        byte_off = int(m.group(3))
        bit_off  = int(m.group(4)) if m.group(4) is not None else 0
        return db, kind, byte_off, bit_off

    # --- snap7.util wrappers (fall back to struct when snap7 unavailable) ---

    @staticmethod
    def _get_bool(data: bytearray, byte_index: int, bit_index: int) -> bool:
        if _s7util is not None:
            return bool(_s7util.get_bool(data, byte_index, bit_index))
        return bool((data[byte_index] >> bit_index) & 1)

    @staticmethod
    def _set_bool(data: bytearray, byte_index: int, bit_index: int, value: bool) -> None:
        if _s7util is not None:
            _s7util.set_bool(data, byte_index, bit_index, value)
        else:
            if value:
                data[byte_index] |= (1 << bit_index)
            else:
                data[byte_index] &= ~(1 << bit_index)

    @staticmethod
    def _get_int(data: (bytes, bytearray), byte_index: int) -> int:
        if _s7util is not None:
            return int(_s7util.get_int(data, byte_index))
        return struct.unpack_from(">h", data, byte_index)[0]

    @staticmethod
    def _set_int(value: int) -> bytes:
        if _s7util is not None:
            buf = bytearray(2)
            _s7util.set_int(buf, 0, value)
            return bytes(buf)
        return struct.pack(">h", value)

    @staticmethod
    def _get_real(data: (bytes, bytearray), byte_index: int) -> float:
        if _s7util is not None:
            return float(_s7util.get_real(data, byte_index))
        return struct.unpack_from(">f", data, byte_index)[0]

    @staticmethod
    def _set_real(value: float) -> bytes:
        if _s7util is not None:
            buf = bytearray(4)
            _s7util.set_real(buf, 0, value)
            return bytes(buf)
        return struct.pack(">f", value)

    @staticmethod
    def _get_dword(data: (bytes, bytearray), byte_index: int) -> int:
        if _s7util is not None:
            return int(_s7util.get_dword(data, byte_index))
        return struct.unpack_from(">I", data, byte_index)[0]

    @staticmethod
    def _set_dword(value: int) -> bytes:
        if _s7util is not None:
            buf = bytearray(4)
            _s7util.set_dword(buf, 0, value)
            return bytes(buf)
        return struct.pack(">I", value)

    @staticmethod
    def _coerce_bytes(raw) -> bytes:
        """Coerce list[int], hex string, or bytes to bytes."""
        if isinstance(raw, (bytes, bytearray)):
            return bytes(raw)
        if isinstance(raw, list):
            return bytes(raw)
        if isinstance(raw, str):
            return bytes.fromhex(raw.replace(" ", ""))
        raise TypeError(f"Cannot coerce {type(raw)} to bytes")
