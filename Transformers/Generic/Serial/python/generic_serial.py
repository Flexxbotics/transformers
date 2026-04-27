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

from data_models.device import Device
from transformers.abstract_device import AbstractDevice
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

        self.status = "Transformer Initiated"

    def __del__(self):
        pass

    # ############################################################################## #
    # DEVICE COMMUNICATION METHODS
    # ############################################################################## #

    def _execute_command(self, command: str) -> str:
        """Legacy v1 entry point — delegates to _execute_command_v2."""
        command_string = command["commandJson"]
        command_json   = json.loads(command_string)
        command_name   = command_json.get("command", "")
        command_args   = json.dumps({k: v for k, v in command_json.items() if k != "command"})
        self._info(message=f"Sending command: {command_string}")
        return self._execute_command_v2(command_name, command_args)

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
        """
        args = json.loads(command_args) if command_args else {}
        self._info(message=f"Sending command: {command_name}")

        try:
            if command_name == "connect":
                client = self._make_client()
                rc = client.connect()
                client.disconnect()
                connected = (rc == 0)
                return json.dumps({
                    "status": "ok" if connected else "error",
                    "connected": connected,
                    "port": self.port,
                })

            if command_name == "disconnect":
                return json.dumps({"status": "ok", "connected": False})

            if command_name == "send":
                raw_cmd    = str(args.get("command", ""))
                terminator = args.get("terminator", self.command_terminator)
                resp_time  = float(args.get("response_time", self.response_time))
                buf_size   = int(args.get("buffer_size", self.buffer_size))
                if not raw_cmd:
                    return self._err("Missing required field: command")
                response = self._send(raw_cmd + terminator, resp_time, buf_size)
                return json.dumps({
                    "status": "ok",
                    "command": raw_cmd,
                    "response": response,
                })

            if command_name == "receive":
                buf_size = int(args.get("buffer_size", self.buffer_size))
                client = self._make_client()
                rc = client.connect()
                if rc != 0:
                    return self._err(f"Could not open port {self.port}")
                raw = client.receive(buffer_size=buf_size)
                client.disconnect()
                response = self._join_response(raw)
                return json.dumps({"status": "ok", "response": response})

            return self._err(f"Unknown command: '{command_name}'")

        except Exception as e:
            self._error(message=f"Command '{command_name}' failed: {e}")
            return self._err(str(e))

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
        Send variable_name as the serial command and return the response.
        metaData.command_terminator is appended automatically.
        """
        try:
            return self._send(
                variable_name + self.command_terminator,
                self.response_time,
                self.buffer_size,
            )
        except Exception as e:
            self._error(message=f"Read variable '{variable_name}' failed: {e}")
            return ""

    def _write_variable(self, variable_name: str, variable_value: str,
                        function: str = None) -> str:
        """
        Format metaData.write_template with {name} and {value}, then send it.
        metaData.command_terminator is appended automatically.
        Returns the device response.
        """
        try:
            cmd = self.write_template.format(name=variable_name, value=variable_value)
            return self._send(cmd + self.command_terminator,
                              self.response_time, self.buffer_size)
        except Exception as e:
            self._error(message=f"Write variable '{variable_name}' failed: {e}")
            return ""

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
        """
        client = self._make_client()
        raw = client.send(
            data=data,
            buffer_size=buffer_size,
            encoding=self.encoding,
            response_time=response_time,
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

    def _err(self, message: str) -> str:
        try:
            self._error(message=message)
        except Exception:
            pass
        return json.dumps({"status": "error", "message": str(message)}, default=str)
