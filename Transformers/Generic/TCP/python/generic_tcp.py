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
    encoding            : Character encoding for outgoing text (default "utf-8").
                          Replies are always decoded as UTF-8.
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
      send_without_connect() for each operation.  A send/receive error (or a
      connection the device closed) is not raised: the TCP client closes the
      socket, that call returns "", and the next call reconnects.
    * With keep_alive=false (default) every send() call opens a fresh
      connection, which is more robust for intermittent devices but adds
      latency on high-frequency polling.
    * Replies to send, send_bytes and the read/write/status methods come from
      a single recv() of up to buffer_size bytes, with every space, CR, LF,
      ">", 0x02 and 0x17 removed by the TCP client.  An unreachable or silent
      device gives an empty reply, not an error.

    Errors
    ------
    Failures raise the runtime's exceptions, which the API returns as HTTP
    errors carrying the message: BadRequestException (400) for missing or
    non-JSON args, bad hex_data or an unknown command,
    ServiceUnavailableException (503) when the transformer's held connection
    (keep_alive=true, or the connect and receive commands) cannot be opened
    or a receive command fails, ServerErrorException (500) for anything else.
    A device that is unreachable or silent during send, send_bytes or a
    read/write/status call is not an error: the reply is "".  _read_status
    never raises (it returns "ERROR"), and neither does _read_interval_data.
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
        self.keep_alive         = self._as_bool(self.meta_data.get("keep_alive", False))
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
            Send a hex-encoded byte payload and return the reply as hex.
            Only the outgoing bytes are raw: the reply is decoded as UTF-8 and
            cleaned like any other reply (see the class Usage notes) before it
            is re-encoded with metaData.encoding, so a binary reply loses
            bytes, and one that is not valid UTF-8 comes back as "".
            Args: hex_data (str, required) — hex string, e.g. "0A0D4654"
                  response_time (float, optional)
                  buffer_size (int, optional)
            Returns: {"status": "ok", "response_hex": "..."}

        receive
            Receive data on the transformer's held socket without sending
            first, opening it if needed.  With keep_alive=false, send and the
            read/write methods use their own per-call sockets, so their
            replies never arrive here.  The decoded text is returned without
            the cleaning send applies; 503 if nothing arrives within
            metaData.timeout.
            Args: buffer_size (int, optional)
            Returns: {"status": "ok", "response": "..."}

        Errors raise: 400 for missing or non-JSON args, bad hex_data or an
        unknown command, 503 when the held connection cannot be opened or a
        receive fails, 500 otherwise.  An unreachable or silent device during
        send or send_bytes is not an error: the reply is "".
        """
        args = self._parse_args(command_args)
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

            if command_name == "send_bytes":
                hex_data  = str(self._require(args, "hex_data"))
                resp_time = float(args.get("response_time") or self.response_time)
                buf_size  = int(args.get("buffer_size") or self.buffer_size)
                try:
                    raw_bytes = bytes.fromhex(hex_data)
                except ValueError:
                    raise BadRequestException(f"hex_data is not valid hex: {hex_data!r}")
                response  = self._send_bytes(raw_bytes, resp_time, buf_size)
                resp_hex  = response.encode(self.encoding).hex() if response else ""
                return json.dumps({"status": "ok", "response_hex": resp_hex})

            if command_name == "receive":
                self._ensure_connected()
                buf_size = int(args.get("buffer_size") or self.buffer_size)
                response = self._client.receive(buffer_size=buf_size)
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
        An empty response (what an unreachable or silent device gives) returns
        "UNAVAILABLE" when status_ok_response is blank, and "" (no status to
        report) when it is set.  A failure that raises (e.g. keep_alive=true
        and the connection cannot be opened) returns "ERROR".
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
            self._connected = False
            self.status = "ERROR"

        return self.status

    def _read_variable(self, variable_name: str, function: str = None) -> str:
        """
        Send variable_name as the TCP command and return the response as a
        plain string.  metaData.command_terminator is appended automatically.
        An unreachable or silent device returns "" rather than raising.
        Raises 503 when keep_alive=true and the connection cannot be opened,
        500 on other failures.
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
        Returns the device response ("" when the device is unreachable or
        silent, rather than raising).  Raises 503 when keep_alive=true and the
        connection cannot be opened, 500 on other failures (e.g. a bad
        write_template).
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
            raise ServiceUnavailableException(
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
