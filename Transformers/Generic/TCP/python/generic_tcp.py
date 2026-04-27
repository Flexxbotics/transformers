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
from protocols.tcp import TCP


class GenericTCP(AbstractDevice):
    """
    Generic TCP transformer.

    Sends and receives raw text or byte commands over a plain TCP socket.
    Suitable for any device that exposes a simple request/response TCP
    interface — CNCs, PLCs, robots, sensors, custom controllers, or any
    device that lacks a higher-level protocol such as OPC-UA or MTConnect.

    Because TCP is a transport layer (not an application protocol), this
    transformer is intentionally unopinionated about message format.
    Commands, variable names, and write templates are all user-configurable
    via metaData, so the same transformer can talk to a Fanuc PMC console, a
    custom sensor hub, a barcode reader, or any other ASCII/binary device
    that speaks raw TCP.

    Connection parameters (metaData)
    ---------------------------------
    ip_address          : IP address of the target device (required)
    port                : TCP port (required)
    timeout             : Socket timeout in seconds (default 2.0)
    retry               : Connection retry attempts (default 2)
    retry_interval      : Seconds between retries (default 0.1)
    encoding            : Character encoding for send/receive (default "utf-8")
    response_time       : Seconds to wait after sending before reading
                          (default 0.1)
    buffer_size         : Receive buffer size in bytes (default 1024)
    command_terminator  : String appended to every outgoing command
                          (default "\\r\\n")
    write_template      : Python format string for write commands.
                          Use {name} and {value} placeholders.
                          (default "{name} {value}")
    status_command      : Command string to send for _read_status (optional).
                          If blank, status returns "NOT_CONFIGURED".
    status_ok_response  : Substring whose presence in the status response
                          maps to "RUNNING".  When blank the raw response is
                          returned as-is (default "").
    interval_commands   : Comma-separated command strings to send on interval.
    keep_alive          : "true" to hold the TCP connection open between calls.
                          "false" (default) opens a fresh connection per call.

    Usage notes
    -----------
    * _read_variable / _read_parameter treat variable_name as the raw TCP
      command to send (command_terminator is appended automatically).
    * _write_variable / _write_parameter format write_template with {name}
      and {value}, then append command_terminator.
    * With keep_alive=true the transformer reuses an open socket and calls
      send_without_connect() for each operation.  The connection is
      re-established automatically on any socket error.
    * With keep_alive=false (default) every send() call opens a fresh
      connection, which is more robust for intermittent devices but adds
      latency on high-frequency polling.
    """

    def __init__(self, device: Device):
        super().__init__(device)

        self.meta_data          = device.metaData or {}
        self.ip_address         = self.meta_data.get("ip_address", "")
        self.port               = str(self.meta_data.get("port", ""))
        self.timeout            = float(self.meta_data.get("timeout", 2.0))
        self.retry              = int(self.meta_data.get("retry", 2))
        self.retry_interval     = float(self.meta_data.get("retry_interval", 0.1))
        self.encoding           = self.meta_data.get("encoding", "utf-8")
        self.response_time      = float(self.meta_data.get("response_time", 0.1))
        self.buffer_size        = int(self.meta_data.get("buffer_size", 1024))
        self.command_terminator = self.meta_data.get("command_terminator", "\r\n")
        self.write_template     = self.meta_data.get("write_template", "{name} {value}")
        self.status_command     = self.meta_data.get("status_command", "").strip()
        self.status_ok_response = self.meta_data.get("status_ok_response", "").strip()
        self.keep_alive         = (
            self.meta_data.get("keep_alive", "false").strip().lower() == "true"
        )
        self.interval_commands  = [
            c.strip()
            for c in self.meta_data.get("interval_commands", "").split(",")
            if c.strip()
        ]

        if not self.ip_address:
            raise ValueError("GenericTCP: metaData must contain 'ip_address'")
        if not self.port:
            raise ValueError("GenericTCP: metaData must contain 'port'")

        self._client: Optional[TCP] = None
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
        Execute a named TCP command.

        Supported commands
        ------------------
        connect
            Open the TCP socket connection to the device.
            Returns: {"status": "ok", "connected": true, "host": "ip:port"}

        disconnect
            Close the TCP socket connection.
            Returns: {"status": "ok", "connected": false}

        send
            Send a text command string and read the response.
            Args: command (str, required) — raw command to send
                  terminator (str, optional) — overrides metaData.command_terminator
                  response_time (float, optional) — seconds to wait for response
                  buffer_size (int, optional) — receive buffer size in bytes
            Returns: {"status": "ok", "command": "...", "response": "..."}

        send_bytes
            Send a hex-encoded byte payload and receive the response as hex.
            Useful for binary protocols where commands are not printable ASCII.
            Args: hex_data (str, required) — hex string, e.g. "0A0D4654"
                  response_time (float, optional)
                  buffer_size (int, optional)
            Returns: {"status": "ok", "response_hex": "..."}

        receive
            Receive data from an already-open socket without sending first.
            Requires keep_alive=true (or a prior explicit connect command).
            Args: buffer_size (int, optional)
            Returns: {"status": "ok", "response": "..."}
        """
        args = json.loads(command_args) if command_args else {}
        self._info(message=f"Sending command: {command_name}")

        try:
            if command_name == "connect":
                self._connect()
                return json.dumps({
                    "status": "ok",
                    "connected": True,
                    "host": f"{self.ip_address}:{self.port}",
                })

            if command_name == "disconnect":
                self._disconnect()
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

            if command_name == "send_bytes":
                hex_data  = str(args.get("hex_data", ""))
                resp_time = float(args.get("response_time", self.response_time))
                buf_size  = int(args.get("buffer_size", self.buffer_size))
                if not hex_data:
                    return self._err("Missing required field: hex_data")
                raw_bytes = bytes.fromhex(hex_data)
                response  = self._send_bytes(raw_bytes, resp_time, buf_size)
                resp_hex  = response.encode(self.encoding).hex() if response else ""
                return json.dumps({"status": "ok", "response_hex": resp_hex})

            if command_name == "receive":
                self._ensure_connected()
                buf_size = int(args.get("buffer_size", self.buffer_size))
                response = self._client.receive(buffer_size=buf_size)
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
        Send variable_name as the TCP command and return the response.
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

    def _connect(self) -> None:
        self._info(message=f"Connecting to TCP device: {self.ip_address}:{self.port}")
        if self._connected and self._client is not None:
            return

        self._client = TCP(
            address=self.ip_address,
            port=self.port,
            timeout=self.timeout,
            retry=self.retry,
            retry_interval=self.retry_interval,
        )
        rc = self._client.connect()
        if rc != 0:
            self._connected = False
            raise ConnectionError(
                f"GenericTCP: connect failed ({self.ip_address}:{self.port})"
            )
        self._connected = True
        self._info(message=f"Connected to TCP device: {self.ip_address}:{self.port}")

    def _disconnect(self) -> None:
        self._info(message=f"Disconnecting from TCP device: {self.ip_address}:{self.port}")
        try:
            if self._client is not None:
                self._client.disconnect()
        finally:
            self._client = None
            self._connected = False

    def _ensure_connected(self) -> None:
        if not self._connected or self._client is None:
            self._connect()

    def _send(self, data: str, response_time: float, buffer_size: int) -> str:
        """Send a text command, routing through keep-alive or per-call connect."""
        if self.keep_alive:
            try:
                self._ensure_connected()
                return self._client.send_without_connect(
                    data=data,
                    buffer_size=buffer_size,
                    encoding=self.encoding,
                    response_time=response_time,
                )
            except Exception:
                # Socket may have dropped; reconnect once and retry.
                self._connected = False
                self._client = None
                self._connect()
                return self._client.send_without_connect(
                    data=data,
                    buffer_size=buffer_size,
                    encoding=self.encoding,
                    response_time=response_time,
                )
        else:
            client = TCP(
                address=self.ip_address,
                port=self.port,
                timeout=self.timeout,
                retry=self.retry,
                retry_interval=self.retry_interval,
            )
            return client.send(
                data=data,
                buffer_size=buffer_size,
                encoding=self.encoding,
                response_time=response_time,
                close_connection=True,
            )

    def _send_bytes(self, data: bytes, response_time: float, buffer_size: int) -> str:
        """Send raw bytes, routing through keep-alive or per-call connect."""
        if self.keep_alive:
            try:
                self._ensure_connected()
                return self._client.send_without_connect(
                    data=data,
                    buffer_size=buffer_size,
                    encoding=self.encoding,
                    response_time=response_time,
                )
            except Exception:
                self._connected = False
                self._client = None
                self._connect()
                return self._client.send_without_connect(
                    data=data,
                    buffer_size=buffer_size,
                    encoding=self.encoding,
                    response_time=response_time,
                )
        else:
            client = TCP(
                address=self.ip_address,
                port=self.port,
                timeout=self.timeout,
                retry=self.retry,
                retry_interval=self.retry_interval,
            )
            return client.send(
                data=data,
                buffer_size=buffer_size,
                encoding=self.encoding,
                response_time=response_time,
                close_connection=True,
            )

    def _err(self, message: str) -> str:
        try:
            self._error(message=message)
        except Exception:
            pass
        return json.dumps({"status": "error", "message": str(message)}, default=str)
