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
from __future__ import annotations

import json
import base64
from typing import List, Optional

from werkzeug.exceptions import HTTPException

from data_models.device import Device
from transformers.abstract_device import AbstractDevice
from exceptions.flexxCoreExceptions import (
    FlexxCoreException,
    BadRequestException,
    ServerErrorException,
    ServiceUnavailableException,
)
from protocols.serial import Serial


class GenericSerial(AbstractDevice):
    """
    Generic Serial (RS-232 / RS-485 / RS-422) transformer.

    Sends and receives raw text commands over a serial port.
    Suitable for any device that exposes a serial command interface —
    legacy CNCs, PLCs, barcode scanners, label printers, vision sensors,
    weighing scales, or any instrument that predates Ethernet connectivity.

    Serial communication is inherently point-to-point and synchronous:
    one command is sent, one response is received.  This transformer
    wraps that pattern in the standard Flexx AbstractDevice interface.

    Connection parameters (metaData)
    ---------------------------------
    port                : Serial port identifier (required).
                          Windows: "COM1", "COM3", etc.
                          Linux/macOS: "/dev/ttyUSB0", "/dev/ttyS0", etc.
    baudrate            : Baud rate — must match the device setting.
                          Common values: 9600, 19200, 38400, 57600, 115200
                          (default 9600)
    bytesize            : Data bits: 5, 6, 7, or 8 (default 8)
    stopbits            : Stop bits: 1, 1.5, or 2 (default 1)
    parity              : Parity: "none", "even", "odd", "mark", "space"
                          (default "none")
    xonxoff             : Software flow control XON/XOFF: "true"/"false"
                          (default "false")
    rtscts              : Hardware flow control RTS/CTS: "true"/"false"
                          (default "false")
    dsrdtr              : Hardware flow control DSR/DTR: "true"/"false"
                          (default "false")
    write_timeout       : Write timeout in seconds (default 10.0)
    encoding            : Character encoding for send/receive (default "utf-8")
    response_time       : Seconds to wait after sending before reading
                          (default 0.1)
    buffer_size         : Receive buffer size in bytes (default 1024)
    command_terminator  : String appended to every outgoing command.
                          Most devices expect CR+LF ("\\r\\n"), some expect
                          only CR ("\\r") or LF ("\\n") (default "\\r\\n")
    write_template      : Python format string for write commands.
                          Use {name} and {value} placeholders.
                          (default "{name} {value}")
    status_command      : Command string to send for _read_status (optional).
                          If blank, status returns "NOT_CONFIGURED".
    status_ok_response  : Substring whose presence in the status response
                          maps to "RUNNING".  When blank the raw response is
                          returned as-is (default "").
    interval_commands   : Comma-separated command strings to send on interval.

    Serial nuances
    --------------
    * Baud rate, byte size, stop bits, and parity must exactly match the
      device's configured values or communication will fail silently.
    * RS-485 multi-drop networks require the device address to be embedded
      in the command string — this transformer does not handle addressing
      natively; include the address bytes in your command_terminator or
      write_template as needed.
    * The response_time delay is critical: too short and the read buffer
      will be empty; too long and throughput degrades.  Start at 0.2 s and
      tune down for faster devices.
    * Hardware flow control (rtscts / dsrdtr) should match the device's
      cable wiring.  Most modern devices work with both "false".

    Errors
    ------
    Failures raise the runtime's exceptions, which the API returns as HTTP
    errors carrying the message: BadRequestException (400) for bad args or an
    unknown command, ServiceUnavailableException (503) when the port cannot be
    opened or the send/receive fails, ServerErrorException (500) for anything
    else.  _read_status never raises (it returns "ERROR"), and neither does
    _read_interval_data.
    """

    def __init__(self, device: Device):
        super().__init__(device)

        self.meta_data          = device.metaData or {}
        self.port               = self.meta_data.get("port", "")
        self.baudrate           = int(self.meta_data.get("baudrate", 9600))
        self.bytesize           = int(self.meta_data.get("bytesize", 8))
        self.stopbits           = float(self.meta_data.get("stopbits", 1))
        self.parity             = self.meta_data.get("parity", "none").strip().lower()
        self.xonxoff            = (
            self.meta_data.get("xonxoff", "false").strip().lower() == "true"
        )
        self.rtscts             = (
            self.meta_data.get("rtscts", "false").strip().lower() == "true"
        )
        self.dsrdtr             = (
            self.meta_data.get("dsrdtr", "false").strip().lower() == "true"
        )
        self.write_timeout      = float(self.meta_data.get("write_timeout", 10.0))
        self.encoding           = self.meta_data.get("encoding", "utf-8")
        self.response_time      = float(self.meta_data.get("response_time", 0.1))
        self.buffer_size        = int(self.meta_data.get("buffer_size", 1024))
        self.command_terminator = self.meta_data.get("command_terminator", "\r\n")
        self.write_template     = self.meta_data.get("write_template", "{name} {value}")
        self.status_command     = self.meta_data.get("status_command", "").strip()
        self.status_ok_response = self.meta_data.get("status_ok_response", "").strip()
        self.interval_commands  = [
            c.strip()
            for c in self.meta_data.get("interval_commands", "").split(",")
            if c.strip()
        ]

        if not self.port:
            raise ValueError("GenericSerial: metaData must contain 'port'")

        # Resolve parity string to the pyserial constant via Serial's helper
        from protocols.serial import ParityType
        self._parity_char = ParityType.PARITY_NONE.get_parity(self.parity)

        self._connected = False
        self.status = "Transformer Initiated"

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
        Execute a named Serial command.

        Supported commands
        ------------------
        connect
            Open the serial port.
            Returns: {"status": "ok", "connected": true, "port": "..."}

        disconnect
            Close the serial port.
            Returns: {"status": "ok", "connected": false}

        send
            Send a text command string and read the response.
            Args: command (str, required) — raw command to send
                  terminator (str, optional) — overrides metaData.command_terminator
                  response_time (float, optional) — seconds to wait for response
                  buffer_size (int, optional) — receive buffer size in bytes
            Returns: {"status": "ok", "command": "...", "response": "..."}

        receive
            Read data from the serial port without sending anything first.
            Useful for streaming devices that push data continuously.
            Args: buffer_size (int, optional)
            Returns: {"status": "ok", "response": "..."}

        Errors raise: 400 for missing/invalid args or an unknown command,
        503 when the port cannot be opened or the send/receive fails,
        500 otherwise.
        """
        args = self._parse_args(command_args)
        self._info(message=f"Sending command: {command_name}")

        try:
            if command_name == "connect":
                self._connect()
                return json.dumps({
                    "status": "ok",
                    "connected": True,
                    "port": self.port,
                })

            if command_name == "disconnect":
                self._disconnect()
                return json.dumps({"status": "ok", "connected": False})

            if command_name == "send":
                raw_cmd    = str(self._require(args, "command"))
                terminator = args.get("terminator", self.command_terminator)
                resp_time  = float(args.get("response_time") or self.response_time)
                buf_size   = int(args.get("buffer_size") or self.buffer_size)
                response = self._send(raw_cmd + terminator, resp_time, buf_size)
                return json.dumps({
                    "status": "ok",
                    "command": raw_cmd,
                    "response": response,
                })

            if command_name == "receive":
                buf_size = int(args.get("buffer_size") or self.buffer_size)
                client = self._make_client()
                rc = client.connect()
                if rc != 0:
                    raise ServiceUnavailableException(
                        f"GenericSerial: could not open port {self.port}"
                    )
                raw = client.receive(buffer_size=buf_size)
                client.disconnect()
                if raw is None:
                    # Serial.receive() returns None when the read failed.
                    raise ServiceUnavailableException(
                        f"GenericSerial: read from port {self.port} failed"
                    )
                response = self._join_response(raw)
                return json.dumps({"status": "ok", "response": response})

        except Exception as e:
            self._raise_device_error(f"Command '{command_name}'", e)

        raise BadRequestException(f"Unknown command '{command_name}'")

    def _read_interval_data(self) -> str:
        """
        Periodic poll. Sends each command in metaData.interval_commands and
        returns a JSON map of command → response.
        Falls back to _read_status when no interval_commands are configured.
        """
        if not self.interval_commands:
            return json.dumps({"status": self._read_status()})

        values = {}
        for cmd in self.interval_commands:
            try:
                values[cmd] = self._send(
                    cmd + self.command_terminator,
                    self.response_time,
                    self.buffer_size,
                )
            except Exception as e:
                values[cmd] = f"ERROR: {e}"
        return json.dumps({"status": "ok", "values": values}, default=str)

    def _read_status(self, function: str = None) -> str:
        """
        Read device status by sending metaData.status_command and evaluating
        the response against metaData.status_ok_response.

        If no status_command is configured, returns "NOT_CONFIGURED".

        When status_ok_response is set, its presence in the response maps to
        "RUNNING"; any other response is returned verbatim.
        When status_ok_response is blank, the raw response is returned as-is.
        """
        if not self.status_command:
            self.status = "NOT_CONFIGURED"
            return self.status

        try:
            response = self._send(
                self.status_command + self.command_terminator,
                self.response_time,
                self.buffer_size,
            )
            if self.status_ok_response:
                self.status = "RUNNING" if self.status_ok_response in response else response
            else:
                self.status = response if response else "UNAVAILABLE"
        except Exception as e:
            self._error(message=f"Status read failed: {e}")
            self.status = "ERROR"

        return self.status

    def _read_variable(self, variable_name: str, function: str = None) -> str:
        """
        Send variable_name as the serial command and return the response as a
        plain string.  metaData.command_terminator is appended automatically.
        Raises on failure (503 when the send/receive fails, 500 otherwise).
        """
        try:
            return self._send(
                variable_name + self.command_terminator,
                self.response_time,
                self.buffer_size,
            )
        except Exception as e:
            self._raise_device_error(f"Read variable '{variable_name}'", e)

    def _write_variable(self, variable_name: str, variable_value: str,
                        function: str = None) -> str:
        """
        Format metaData.write_template with {name} and {value}, then send it.
        metaData.command_terminator is appended automatically.
        Returns the device response.  Raises on failure (503 when the
        send/receive fails, 500 otherwise).
        """
        try:
            cmd = self.write_template.format(name=variable_name, value=variable_value)
            return self._send(cmd + self.command_terminator,
                              self.response_time, self.buffer_size)
        except Exception as e:
            self._raise_device_error(f"Write variable '{variable_name}'", e)

    def _read_parameter(self, parameter_name: str, function: str = None) -> str:
        return self._read_variable(parameter_name, function)

    def _write_parameter(self, parameter_name: str, parameter_value: str,
                         function: str = None) -> str:
        return self._write_variable(parameter_name, parameter_value, function)

    def _read_file_names(self) -> list:
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

    def _connect(self) -> None:
        """
        Serial ports are opened per call by _send, so nothing is held open.
        "Connected" means the port could be opened: open it once and close it
        again.  Raises ServiceUnavailableException (503) when it can't.
        """
        if self._connected:
            return
        client = self._make_client()
        rc = client.connect()
        client.disconnect()
        if rc != 0:
            raise ServiceUnavailableException(
                f"GenericSerial: could not open port {self.port}"
            )
        self._connected = True

    def _disconnect(self) -> None:
        """Nothing is held open between calls; just clear the flag. Safe to call twice."""
        self._connected = False

    def _ensure_connected(self) -> None:
        if not self._connected:
            self._connect()

    def _make_client(self) -> Serial:
        """Create a configured Serial protocol instance."""
        return Serial(
            port=self.port,
            baudrate=self.baudrate,
            bytesize=self.bytesize,
            stopbits=self.stopbits,
            parity=self._parity_char,
            xonxoff=self.xonxoff,
            rtscts=self.rtscts,
            dsrdtr=self.dsrdtr,
            write_timeout=self.write_timeout,
        )

    def _send(self, data: str, response_time: float, buffer_size: int) -> str:
        """
        Open the serial port, send data, receive the response, then close.
        Serial.send() handles the connect/disconnect lifecycle internally.
        Serial.receive() returns a list split on commas; join it back to a string.
        Serial.send() reports any failure as -1 (or None when the read failed);
        that raises ServiceUnavailableException instead of coming back as a value.
        """
        client = self._make_client()
        raw = client.send(
            data=data,
            buffer_size=buffer_size,
            encoding=self.encoding,
            response_time=response_time,
        )
        if raw is None or raw == -1:
            raise ServiceUnavailableException(
                f"GenericSerial: send/receive on port {self.port} failed"
            )
        return self._join_response(raw)

    @staticmethod
    def _join_response(raw) -> str:
        """
        Serial.receive() returns a list (split on commas).
        Re-join to a plain string for uniform return values across the interface.
        """
        if isinstance(raw, list):
            return ",".join(str(item) for item in raw)
        return str(raw) if raw is not None else ""

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
