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

from data_models.device import Device
import json
import base64
import time
from typing import Optional, Tuple, Any, Dict

import requests

from transformers.abstract_device import AbstractDevice

# --------------------------------------------------------------------------------------
# Holzher driver class
# --------------------------------------------------------------------------------------

class Holzher(AbstractDevice):
    """
    Holzher device transformer.

    This implementation treats the Holzher/Cannon Automata PC "Communicator/Webserver"
    as an HTTP service. Connection state is managed with an HTTP Session.

    Expects device.metaData to contain (at minimum):
      - ip_address: "a.b.c.d" (or "address")
      - port: int

    Optional metaData:
      - scheme: "http" | "https" (default "http")
      - timeout: float seconds (default 2.0)
      - retry: int (default 2)
      - retry_interval: float seconds (default 0.1)
      - auto_connect: bool (default True)
      - health_path: str (optional preferred probe path, e.g. "/ping")
      - username/password OR token (optional auth)
    """

    def __init__(self, device: Device):
        """
        Holzher device class.
        """
        try:
            super().__init__(device)

            self.meta_data = device.metaData or {}

            # Common metadata keys
            self.address = self.meta_data.get("ip_address") or self.meta_data.get("address")
            self.port = int(self.meta_data.get("port"))

            # Connection tuning
            self.timeout = float(self.meta_data.get("timeout", 2.0))
            self.retry = int(self.meta_data.get("retry", 2))
            self.retry_interval = float(self.meta_data.get("retry_interval", 0.1))
            self.auto_connect = bool(self.meta_data.get("auto_connect", True))

            # HTTP client (requests.Session) once connected
            self.client: Optional[requests.Session] = None

            # Track state
            self._connected = False

        except Exception as e:
            self._logger.error(str(e))
            raise RuntimeError(f"Holzher init failed: {e}") from e

    def __del__(self):
        try:
            if getattr(self, "_connected", False):
                self._disconnect()
        except Exception:
            pass

    # ############################################################################## #
    # DEVICE COMMUNICATION METHODS
    # ############################################################################## #

    def _execute_command_v2(self, command_name: str, command_args: str) -> str:
        """
        Executes a command against the Holzher controller interface.

        Supported commands:
          - connect
          - disconnect
          - get_status   (lightweight connectivity check via GET probe)
          - read_http    (HTTP GET)
          - post_http    (HTTP POST)

        Notes on command_args:
          command_args is expected to be a JSON string, optionally wrapped as:
            {"value": "<json-string>"}  OR {"value": {...}}

        Args for read_http:
          {
            "path": "/status",                 # required
            "timeout": 2.0,                    # optional; defaults to self.timeout
            "headers": {"X-Foo": "bar"}        # optional
          }

        Args for post_http:
          {
            "path": "/api/do_something",       # required
            "json": {...},                     # optional JSON body
            "data": "raw",                     # optional raw body (string/bytes)
            "timeout": 2.0,                    # optional; defaults to self.timeout
            "headers": {"Content-Type":"..."}  # optional
          }

        Returns:
          Always returns a JSON string (success or error).
        """
        # Parse the command from the incoming request
        try:
            args: Any = json.loads(command_args) if command_args else {}
        except Exception:
            args = {}

        # unwrap {"value": ...}
        if isinstance(args, dict) and "value" in args:
            v = args.get("value")
            if isinstance(v, str):
                try:
                    args = json.loads(v)
                except Exception:
                    args = {}
            elif isinstance(v, dict):
                args = v
            else:
                args = {}

        args = args or {}

        try:
            if command_name == "connect":
                self._connect(timeout_s=int(args.get("timeout_s", 10)))
                return json.dumps({"status": "ok", "connected": True}, default=str)

            if command_name == "disconnect":
                self._disconnect()
                return json.dumps({"status": "ok", "connected": False}, default=str)

            if command_name in ("get_status", "status"):
                self._ensure_connected()
                # probe a known endpoint (health_path if provided, otherwise our probe list)
                ep = self.meta_data.get("health_path", None)
                if ep is None:
                    # pick first probe endpoint
                    ep = self._probe_endpoints()[0]
                code, snippet = self._http_request(
                    "GET",
                    ep,
                    timeout=float(args.get("timeout", self.timeout)),
                    headers=args.get("headers") if isinstance(args.get("headers"), dict) else None,
                )
                return json.dumps(
                    {
                        "status": "ok",
                        "connected": True,
                        "http_status": code,
                        "probe_path": ep,
                        "snippet": snippet,
                    },
                    default=str,
                )

            if command_name == "read_http":
                self._ensure_connected()
                path = args.get("path")
                if not path:
                    return self._err("read_http requires args.path")
                code, snippet = self._http_request(
                    "GET",
                    str(path),
                    timeout=float(args.get("timeout", self.timeout)),
                    headers=args.get("headers") if isinstance(args.get("headers"), dict) else None,
                )
                return json.dumps(
                    {"status": "ok", "http_status": code, "path": path, "body": snippet},
                    default=str,
                )

            if command_name == "post_http":
                self._ensure_connected()
                path = args.get("path")
                if not path:
                    return self._err("post_http requires args.path")

                # Prefer json body if provided; else use raw data if provided.
                json_body = args.get("json", None)
                data_body = args.get("data", None)

                code, snippet = self._http_request(
                    "POST",
                    str(path),
                    timeout=float(args.get("timeout", self.timeout)),
                    headers=args.get("headers") if isinstance(args.get("headers"), dict) else None,
                    json_body=json_body if isinstance(json_body, (dict, list)) else None,
                    data_body=data_body,
                )
                return json.dumps(
                    {"status": "ok", "http_status": code, "path": path, "body": snippet},
                    default=str,
                )

            # ---- Unknown command ----
            return self._err(f"UNKNOWN_COMMAND: {command_name}")

        except Exception as e:
            self._logger.error(str(e))
            return self._err(str(e))

    def _read_interval_data(self) -> str:
        """
        Method to read the status of the Holzher device on an interval.
        """
        self._ensure_connected()
        # TODO: implement polling reads once endpoints/symbols are known
        return json.dumps({"status": "ok"}, default=str)

    def _read_status(self, function: str = None) -> str:
        """
        Read Holzher status.

        function:
          - None: default status (TODO)
          - otherwise: future routing options (TODO)
        """
        status = ""
        self._ensure_connected()
        assert self.client is not None

        if function is None:
            # TODO: how to get status (endpoint + parsing)
            pass
        elif function == "":
            pass
        else:
            pass
        return status

    def _read_variable(self, variable_name: str, function: str = None) -> str:
        """
        Read a Holzher variable (placeholder until the real interface is confirmed).
        """
        value = ""
        if function is None:
            # TODO: how to read variables
            pass
        elif function == "":
            pass
        else:
            pass
        return value

    def _write_variable(self, variable_name: str, variable_value: str, function: str = None) -> str:
        """
        Write a Holzher variable (placeholder until the real interface is confirmed).
        """
        value = ""
        if function is None:
            pass
        elif function == "":
            pass
        else:
            pass
        return value

    def _write_parameter(self, parameter_name: str, parameter_value: str, function: str = None) -> str:
        """
        Write a Holzher parameter (placeholder).
        """
        value = ""
        if function is None:
            pass
        elif function == "":
            pass
        else:
            pass
        return value

    def _read_parameter(self, parameter_name: str, function: str = None) -> str:
        """
        Read a Holzher parameter (placeholder).
        """
        value = ""
        if function is None:
            pass
        elif function == "":
            pass
        else:
            pass
        return value

    def _read_file_names(self) -> list:
        """
        Read file/program names from the Holzher controller (placeholder).
        """
        self.programs = []
        return self.programs

    def _read_file(self, file_name: str) -> str:
        """
        Read a file from the Holzher controller (placeholder).
        """
        file_data = ""
        # Note: base64.b64encode expects bytes
        return base64.b64encode(file_data.encode("utf-8")).decode("utf-8")

    def _write_file(self, file_name: str, file_data: str):
        """
        Write a file to the Holzher controller (placeholder).
        """
        pass

    def _load_file(self, file_name: str):
        """
        Load/run a file on the Holzher controller (placeholder).
        """
        pass

    # ############################################################################## #
    # INTERFACE HELPER METHODS
    # ############################################################################## #

    def _base_url(self) -> str:
        if not self.address:
            raise RuntimeError("Holzher metaData missing ip_address/address")
        if not self.port:
            raise RuntimeError("Holzher metaData missing port")
        scheme = self.meta_data.get("scheme", "http")
        return f"{scheme}://{self.address}:{self.port}"

    def _probe_endpoints(self) -> list[str]:
        """
        Endpoints to try for a basic Holzher connectivity probe.
        """
        preferred = self.meta_data.get("health_path")
        eps = []
        if preferred:
            eps.append(str(preferred))
        eps.extend(
            [
                "/health",
                "/ping",
                "/status",
                "/api/health",
                "/api/ping",
                "/",
            ]
        )
        # de-dupe while keeping order
        seen = set()
        out = []
        for e in eps:
            if e not in seen:
                seen.add(e)
                out.append(e)
        return out

    def _http_request(
        self,
        method: str,
        path: str,
        *,
        timeout: float,
        headers: Optional[Dict[str, str]] = None,
        json_body: Any = None,
        data_body: Any = None,
    ) -> Tuple[int, str]:
        """
        Minimal request wrapper for the Holzher web service.
        Returns (status_code, response_text_snippet).
        """
        assert self.client is not None

        base = self._base_url().rstrip("/")
        if path == "":
            url = base
        else:
            url = base + (path if str(path).startswith("/") else f"/{path}")

        # Merge headers with optional auth header patterns
        merged_headers: Dict[str, str] = {}
        if isinstance(headers, dict):
            merged_headers.update({str(k): str(v) for k, v in headers.items()})

        token = self.meta_data.get("token")
        if token and "Authorization" not in merged_headers:
            merged_headers["Authorization"] = f"Bearer {token}"

        auth = None
        user = self.meta_data.get("username")
        pwd = self.meta_data.get("password")
        if user and pwd:
            auth = (user, pwd)

        r = self.client.request(
            method=method.upper(),
            url=url,
            timeout=timeout,
            headers=merged_headers if merged_headers else None,
            auth=auth,
            json=json_body,
            data=data_body,
        )
        snippet = (r.text or "")[:200]
        return r.status_code, snippet

    def _connect(self, *, timeout_s: int = 10) -> None:
        """
        Create Holzher client and open connection.

        For HTTP-based devices, "connect" means:
          - create a requests.Session
          - successfully probe at least one endpoint
        """
        self._logger.info("Connecting to Holzher...")

        # Create the HTTP client (session)
        self.client = requests.Session()

        last_err: Optional[Exception] = None
        deadline = time.time() + float(timeout_s)

        # attempt loop (time-based + short sleeps)
        while time.time() < deadline:
            try:
                for ep in self._probe_endpoints():
                    code, snippet = self._http_request("GET", ep, timeout=self.timeout)

                    # Any 2xx/3xx confirms service is responding
                    if 200 <= code < 400:
                        self._connected = True
                        self._logger.info(
                            f"Connected to Holzher at {self._base_url()} (probe={ep!r}, status={code})"
                        )
                        return

                    # 401/403 still proves reachability; auth can be configured
                    if code in (401, 403):
                        self._connected = True
                        self._logger.warning(
                            f"Holzher reachable but unauthorized at {self._base_url()} (probe={ep!r}, status={code}). "
                            f"Configure metaData.username/password or metaData.token."
                        )
                        return

                last_err = RuntimeError(
                    f"No probe endpoint returned 2xx/3xx/401/403. Last response snippet: {snippet!r}"
                )

            except Exception as e:
                last_err = e

            time.sleep(self.retry_interval)

        # Give up
        try:
            if self.client is not None:
                self.client.close()
        except Exception:
            pass

        self.client = None
        self._connected = False
        raise RuntimeError(f"Holzher connect failed: {last_err}") from last_err

    def _disconnect(self) -> None:
        """
        Close Holzher connection and release client.
        """
        self._logger.info("Disconnecting from Holzher...")

        try:
            if self.client is not None:
                try:
                    self.client.close()
                finally:
                    self.client = None
        finally:
            self._connected = False

        self._logger.info("Disconnected from Holzher!")

    def _ensure_connected(self) -> None:
        """
        Ensure the transformer has an active Holzher connection.
        """
        if not self._connected:
            self._connect(timeout_s=10)

    def _read_data(self):
        """
        Read raw data from the Holzher device (placeholder).
        """
        pass

    def _err(self, message: str) -> str:
        """
        Standardized error response for Holzher transformer.
        """
        try:
            self._error(message)  # log via AbstractDevice logger
        except Exception:
            pass

        return json.dumps(
            {
                "status": "error",
                "message": str(message),
            },
            default=str,
        )
