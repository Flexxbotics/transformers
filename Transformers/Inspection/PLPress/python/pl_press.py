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

from werkzeug.exceptions import HTTPException

from data_models.device import Device
from protocols.http_rest import HttpRest
from transformers.abstract_device import AbstractDevice
from exceptions.flexxCoreExceptions import (
    FlexxCoreException,
    BadRequestException,
    ServerErrorException,
    ServiceUnavailableException,
)


class PLPress(AbstractDevice):
    """
    Transformer for a PL Press inspection station.

    Rather than talking to the press directly, this transformer talks to the
    standalone PL Press report server (see ../extensions/pl_press_server.py)
    running on the press's Windows PC. That server scans a directory of PL Press
    measurement CSV files and exposes them over JSON-RPC 2.0. This transformer
    forwards commands to it via the HttpRest protocol and returns the structured
    inspection data.
    """

    def __init__(self, device: Device):
        """
        :param device:
                    the device object

        :return:    a new instance
        """
        super().__init__(device)

        # Connection metadata: the host/port the report server is bound to, and
        # an optional shared secret (X-Auth-Token) if the server requires one.
        self.meta_data = device.metaData or {}
        self.address = self.meta_data.get("ip_address", "")
        self.port = str(self.meta_data.get("port") or "")
        self.token = self.meta_data.get("token") or None

        if not self.address:
            raise ValueError("PLPress: metaData must contain 'ip_address'")
        if not self.port:
            raise ValueError("PLPress: metaData must contain 'port'")

        self.base_url = f"http://{self.address}:{self.port}"

        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["X-Auth-Token"] = self.token

        # HttpRest opens its session lazily on the first request (no I/O here).
        self.client = HttpRest(base_url=self.base_url, headers=headers, timeout=30.0)
        self._rpc_id = 0
        self._connected = False

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
        Executes the command sent to the device.

        Each supported command maps to one JSON-RPC method on the PL Press
        report server. Except for ping, the arguments object is forwarded as the
        RPC params; the server ignores keys it does not know.

        connect / disconnect
            connect pings the report server (unless already connected);
            disconnect closes the HttpRest session.
        ping
            No args. Result: the server's config ({"ok": true, "directory",
            "last_pulled", "server_time", ...}).
        list_reports
            Optional args: ``since``, ``until``, ``modified_within_hours``,
            ``pattern``, ``recursive``, ``directory`` (defaults from the server's
            config). Result: {"directory", "count", "files": [...]} (no CSV parsing).
        get_reports
            Optional args: as list_reports, plus ``include_measurements``
            (default true). With no ``since`` the server uses its stored
            ``last_pulled`` watermark, and returns only measurement rows newer
            than that bound; a pull without ``until`` advances the watermark.
            Result: {"directory", "count", "pulled_since", "last_pulled",
            "reports": {<report_id>: {...}}, "errors": [...]}.
        get_report
            Args: ``file`` (required; a name in the directory or an absolute
            path), ``directory`` (optional). Result: the parsed report.

        :param command_name:
                    the command to be executed:
                    "connect", "disconnect", "ping", "list_reports",
                    "get_reports", or "get_report"
        :param command_args:
                    json string with the arguments for the command (a JSON
                    object, or {"value": "<JSON object string>"})

        :return:    {"status": "ok", "result": <RPC result>} as a JSON string.
                    connect / disconnect return
                    {"status": "ok", "connected": true|false}.

        Errors raise: 400 for args that are not a JSON object or an unknown
        command, 503 when the report server does not respond, 500 with the
        server's message when it replies with an RPC error (this includes a
        missing ``file`` for get_report).
        """
        args = self._parse_args(command_args)
        self._info(message="Sending command: " + command_name)

        try:
            if command_name == "connect":
                self._connect()
                return json.dumps({"status": "ok", "connected": True})

            if command_name == "disconnect":
                self._disconnect()
                return json.dumps({"status": "ok", "connected": False})

            if command_name == "ping":
                # Liveness/config check for the report server.
                self._ensure_connected()
                result = self._rpc("ping")

            elif command_name == "list_reports":
                # Lightweight file listing (no CSV parsing) within the window.
                self._ensure_connected()
                result = self._rpc("list_reports", args)

            elif command_name == "get_reports":
                # Parse every CSV in the window into structured records.
                self._ensure_connected()
                result = self._rpc("get_reports", args)

            elif command_name == "get_report":
                # Parse a single CSV file by filename or absolute path.
                self._ensure_connected()
                result = self._rpc("get_report", args)

            else:
                raise BadRequestException(f"Unknown command '{command_name}'")

            return json.dumps({"status": "ok", "result": result}, default=str)

        except Exception as e:
            self._raise_device_error(f"Command '{command_name}'", e)

    def _read_status(self, function: str = None) -> str:
        """
        The report server has no machine status, so this returns "" (nothing
        to report) without contacting it.
        """
        return ""

    def _read_interval_data(self) -> str:
        """
        No-op: reports are pulled with the commands above. Never raises.
        """
        return ""

    # ############################################################################## #
    # INTERFACE HELPER METHODS
    # ############################################################################## #

    def _connect(self) -> None:
        """
        Check that the report server answers a ``ping``. HttpRest opens its
        session on that first request.
        """
        if self._connected:
            return
        self._info(message=f"Connecting to PL Press report server: {self.base_url}")
        try:
            self._rpc("ping")
        except FlexxCoreException:
            raise
        except Exception as e:
            raise ServiceUnavailableException(
                f"PLPress: report server did not respond ({self.base_url}): {e}"
            ) from e
        self._connected = True
        self._info(message=f"Connected to PL Press report server: {self.base_url}")

    def _disconnect(self) -> None:
        """Close the HttpRest session. Safe to call twice."""
        try:
            self.client.disconnect()
        finally:
            self._connected = False

    def _ensure_connected(self) -> None:
        if not self._connected:
            self._connect()

    def _rpc(self, method: str, params: dict = None) -> dict:
        """
        Send a single JSON-RPC 2.0 request to the PL Press report server and
        return the ``result`` object.

        :param method:
                    the JSON-RPC method name
        :param params:
                    the params object (defaults to empty)

        :return:    the RPC ``result`` value

        Raises ServerErrorException carrying the server's message for an RPC
        error, and ConnectionError when no reply arrives (mapped to 503).
        """
        self._rpc_id += 1
        envelope = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params or {},
            "id": self._rpc_id,
        }

        raw = self.client.post("/rpc", body=envelope)
        if not raw:
            raise ConnectionError(f"Empty response from PL Press report server for '{method}'")

        data = json.loads(raw)
        # HttpRest swallows a failed request and returns the previous reply, so
        # a reply carrying another request's id means this request got none.
        if data.get("id") not in (envelope["id"], None):
            raise ConnectionError(f"No response from PL Press report server for '{method}'")
        if "error" in data:
            error = data["error"]
            raise ServerErrorException(
                f"RPC error {error.get('code')}: {error.get('message')}"
            )

        return data.get("result")

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
