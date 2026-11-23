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
from werkzeug.exceptions import HTTPException
from data_models.device import Device
from transformers.abstract_device import AbstractDevice
from exceptions.flexxCoreExceptions import (
    FlexxCoreException,
    BadRequestException,
    ServerErrorException,
    ServiceUnavailableException,
)
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
      ip_address          : PLC IP address (required)
      port                : TCP port (default 5007 for 3E frame)
      frame_type          : "3E" (default) or "1E"
      plc_type            : "Q" | "iQ-R" | "iQ-F" | "L" | "QnA"
      timeout             : socket timeout in seconds (default 2.0)
      retry               : connection retry attempts (default 2)
      network             : Melsec network number (default 0)
      pc_number           : Melsec PC/station number (default 255)
      status_device       : device to poll for _read_status (default "SM400")
      interval_devices    : comma-separated device list for _read_interval_data

    Errors
    ------
    Failures raise the runtime's exceptions, which the API returns as HTTP
    errors carrying the message: BadRequestException (400) for bad args, an
    unknown device prefix or an unknown command, ServiceUnavailableException
    (503) when the PLC does not respond, ServerErrorException (500) for
    anything else.  _read_status never raises (it returns "ERROR"), and
    neither does _read_interval_data.
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

        if not self.ip_address:
            raise ValueError("MitsubishiMelsec: metaData must contain 'ip_address'")

        # Creating the client does not touch the network; it connects on first use.
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
        self._connected = False

        self.status = "transformer Initiated"

    def __del__(self):
        try:
            self._disconnect()
        except Exception:
            pass

    # ############################################################################## #
    # DEVICE COMMUNICATION METHODS
    # ############################################################################## #

    def _execute_command_v2(self, command_name: str, command_args: str) -> str:
        """
        Execute a named command against the Mitsubishi PLC.

        Supported commands
        ------------------
        connect
            Explicitly open TCP connection to the PLC.
            Returns: {"status": "ok", "connected": true}

        disconnect
            Close TCP connection.
            Returns: {"status": "ok", "connected": false}

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

        Errors raise: 400 for missing/invalid args or an unknown command,
        503 when the PLC does not respond, 500 otherwise.
        """
        args = self._parse_args(command_args)
        self._info(message=f"Sending command: {command_name}")

        try:
            if command_name == "connect":
                self._connect()
                return json.dumps({"status": "ok", "connected": True})

            if command_name == "disconnect":
                self._disconnect()
                return json.dumps({"status": "ok", "connected": False})

            if command_name == "read_words":
                device = str(self._require(args, "device"))
                count  = int(args.get("count") or 1)
                self._ensure_connected()
                values = self._client.read_words(device, count)
                return json.dumps({"status": "ok", "device": device,
                                   "count": count, "values": values})

            if command_name == "read_bits":
                device = str(self._require(args, "device"))
                count  = int(args.get("count") or 1)
                self._ensure_connected()
                values = self._client.read_bits(device, count)
                return json.dumps({"status": "ok", "device": device,
                                   "count": count, "values": values})

            if command_name == "write_words":
                device = str(self._require(args, "device"))
                values = [int(v) for v in self._require(args, "values")]
                self._ensure_connected()
                self._client.write_words(device, values)
                return json.dumps({"status": "ok", "device": device, "values": values})

            if command_name == "write_bits":
                device = str(self._require(args, "device"))
                values = [int(v) for v in self._require(args, "values")]
                self._ensure_connected()
                self._client.write_bits(device, values)
                return json.dumps({"status": "ok", "device": device, "values": values})

            if command_name == "random_read":
                word_devices = args.get("word_devices") or []
                bit_devices  = args.get("bit_devices") or []
                self._ensure_connected()
                result = self._client.random_read(
                    word_devices=word_devices, bit_devices=bit_devices
                )
                return json.dumps({"status": "ok", **result})

            if command_name == "random_write":
                word_devices = args.get("word_devices") or []
                word_values  = [int(v) for v in args.get("word_values") or []]
                bit_devices  = args.get("bit_devices") or []
                bit_values   = [int(v) for v in args.get("bit_values") or []]
                self._ensure_connected()
                self._client.random_write(
                    word_devices=word_devices, word_values=word_values,
                    bit_devices=bit_devices, bit_values=bit_values,
                )
                return json.dumps({"status": "ok"})

        except Exception as e:
            self._raise_device_error(f"Command '{command_name}'", e)

        raise BadRequestException(f"Unknown command '{command_name}'")

    def _read_interval_data(self) -> str:
        """
        Periodic poll. Reads the devices listed in metaData.interval_devices
        (comma-separated, e.g. "D0,D1,M0,Y0") in a single random_read call.
        Falls back to _read_status when no interval devices are configured.
        Never raises.
        """
        if not self.interval_devices:
            return json.dumps({"status": self._read_status()})

        try:
            word_devices = [d for d in self.interval_devices if not self._is_bit_device(d)]
            bit_devices  = [d for d in self.interval_devices if self._is_bit_device(d)]
            self._ensure_connected()
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
            self._ensure_connected()
            values = self._client.read_bits(self.status_device, 1)
            running = bool(values[0]) if values else False
            self.status = "RUNNING" if running else "STOPPED"
        except Exception as e:
            self._error(message=f"Status read failed: {e}")
            self._connected = False
            self.status = "ERROR"

        return self.status

    def _read_variable(self, variable_name: str, function: str = None) -> str:
        """
        Read a named device from the PLC.  variable_name is a Melsec device
        address such as "D100", "M5" or "X0F" (bit or word is decided by the
        device prefix; see _is_bit_device).  Returns the value as a plain
        string.  Raises on failure (400 for an unknown device prefix, 503 when
        the PLC does not respond, 500 otherwise).
        """
        try:
            is_bit = self._is_bit_device(variable_name)
            self._ensure_connected()
            if is_bit:
                values = self._client.read_bits(variable_name, 1)
            else:
                values = self._client.read_words(variable_name, 1)
            if not values:
                raise ServerErrorException(f"Read '{variable_name}' failed: the PLC returned no value")
            return str(values[0])
        except Exception as e:
            self._raise_device_error(f"Read variable '{variable_name}'", e)

    def _write_variable(self, variable_name: str, variable_value: str,
                        function: str = None) -> str:
        """
        Write a value to a named device.  variable_name is a Melsec device
        address; variable_value is cast to int.  A bit device gets a single
        bit write, a word device a single word write.  Returns the written
        value.  Raises on failure (400 for an unknown device prefix, 503 when
        the PLC does not respond, 500 otherwise).
        """
        try:
            val = int(variable_value)
            is_bit = self._is_bit_device(variable_name)
            self._ensure_connected()
            if is_bit:
                self._client.write_bits(variable_name, [val])
            else:
                self._client.write_words(variable_name, [val])
            return variable_value
        except Exception as e:
            self._raise_device_error(f"Write variable '{variable_name}'", e)

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
        """
        Return True for a bit-unit device, False for a word-unit device.

        Classified by the longest known device prefix at the START of the
        address, followed by the device number (decimal or hex digits).  The
        prefix is not "all the letters": X, Y, B, W, SB and SW numbers are
        hexadecimal, so "X0F", "XF" and "Y1A" are bits and "W1A" is a word.
        Raises BadRequestException for an unknown prefix.
        """
        _BIT_PREFIXES  = {"X", "Y", "M", "L", "F", "V", "B", "S", "SM", "SB",
                          "TS", "TC", "CS", "CC", "SS", "SC"}
        _WORD_PREFIXES = {"D", "W", "R", "ZR", "Z", "TN", "CN", "SN", "SD", "SW"}
        address = str(device).strip().upper()
        for length in (2, 1):
            prefix, number = address[:length], address[length:]
            if not number or any(c not in "0123456789ABCDEF" for c in number):
                continue
            if prefix in _BIT_PREFIXES:
                return True
            if prefix in _WORD_PREFIXES:
                return False
        raise BadRequestException(
            f"Unknown Melsec device '{device}': expected a bit device "
            f"({', '.join(sorted(_BIT_PREFIXES))}) or a word device "
            f"({', '.join(sorted(_WORD_PREFIXES))}) followed by its number"
        )

    def _connect(self) -> None:
        if self._connected:
            return
        self._info(message=f"Connecting to Melsec PLC: {self.ip_address}:{self.port}")
        # connect() replaces the session without closing it; close a stale one first.
        self._client.disconnect()
        rc = self._client.connect()
        if rc != 0:
            self._connected = False
            raise ServiceUnavailableException(
                f"MitsubishiMelsec: connect failed ({self.ip_address}:{self.port})"
            )
        self._connected = True

    def _disconnect(self) -> None:
        try:
            self._client.disconnect()
        finally:
            self._connected = False

    def _ensure_connected(self) -> None:
        if not self._connected:
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
