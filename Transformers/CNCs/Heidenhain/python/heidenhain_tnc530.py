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

from transformers.abstract_device import AbstractDevice
from exceptions.flexxCoreExceptions import (
    FlexxCoreException,
    BadRequestException,
    ServerErrorException,
    ServiceUnavailableException,
)
import requests


class HeidenhainTNC530(AbstractDevice):
    """
    Heidenhain TNC 530 transformer. Loads programs through the TNC Remo bridge (HTTP), which talks
    to the CNC. Status is not read (_read_status always returns ""); variables and file
    listing/reading/writing are not supported (the runtime answers 501).

    Connection parameters (metaData)
    ---------------------------------
    ip_address : IP address of the CNC, passed to the bridge
    bridge_url : base URL of the TNC Remo bridge (default "http://host.docker.internal:7083")

    Errors
    ------
    Failures raise the runtime's exceptions, which the API returns as HTTP errors carrying the
    message: BadRequestException (400) for bad args or an unknown command,
    ServiceUnavailableException (503) when the bridge does not respond, ServerErrorException (500)
    for anything else.
    """

    _BRIDGE_TIMEOUT_S = 10

    def __init__(self, device: Device):
        """
        Heidenhain TNC 530 transformer. Reads ip_address and bridge_url from metaData; does not
        contact the bridge.

        :param device:
                    the device object

        :return:    a new instance
        """
        # Get meta data of the device from its attributes, this contains information such as: ip address, ports, etc
        super().__init__(device)
        self.meta_data = device.metaData or {}
        self.address = self.meta_data.get("ip_address", "")

        # TNC Remo bridge, which loads files on the CNC
        self.base_url = str(
            self.meta_data.get("bridge_url") or "http://host.docker.internal:7083"
        ).rstrip("/")

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

        Supported commands
        ------------------
        connect / disconnect
            The bridge is called over HTTP per request, so these only set the connected flag.
            Returns: {"status": "ok", "connected": true|false}

        Errors raise: 400 for invalid args or an unknown command.

        :param command_name:
                    the command to be executed
        :param command_args:
                    json with the arguments for the command

        :return:    the response after execution of command.
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

        except Exception as e:
            self._raise_device_error(f"Command '{command_name}'", e)

        raise BadRequestException(f"Unknown command '{command_name}'")

    def _read_interval_data(self) -> str:
        """
        Method to read the status of the device on an interval. Nothing to read; never raises.

        :return:    None (the body is empty)
        """
        pass

    def _read_status(self, function: str = None) -> str:
        """
        Method to read the status of the device

        :param function:
                    Not used

        :return:    status - string: always "" (nothing to report), since the bridge has no status read
        """
        return ""

    def _load_file(self, file_name: str):
        """
        Loads a file into memory on the device

        :param file_name: the name of the file to load into memory
        :return: {"status": "ok", "response": <the bridge's JSON reply>}. Raises on failure (503 when
                 the bridge does not respond within _BRIDGE_TIMEOUT_S, 500 when it replies with an
                 HTTP error or a reply that is not JSON).
        """
        try:
            self._ensure_connected()
            resp = requests.post(
                self.base_url + "/load",
                json={"filename": file_name, "ip_address": self.address},
                timeout=self._BRIDGE_TIMEOUT_S,
            )
            try:
                body = resp.json()
            except ValueError:
                raise ServerErrorException(
                    f"Bridge returned a reply that is not JSON (HTTP {resp.status_code}): {resp.text[:200]}"
                )
            if not resp.ok:
                raise ServerErrorException(f"Bridge returned HTTP {resp.status_code}: {body}")
            return json.dumps({"status": "ok", "response": body}, default=str)
        except Exception as e:
            self._raise_device_error(f"Load file '{file_name}'", e)

    # ############################################################################## #
    # INTERFACE HELPER METHODS
    #
    # These are private methods with naming convention _some_method(self). These are used to faciliate
    # any specific functions that are needed to communicate via the transformer. For example,
    # connection methods, read/write methods, specific functions, etc.
    # ############################################################################## #

    def _connect(self) -> None:
        """The bridge is called over HTTP per request, so there is nothing to open."""
        if self._connected:
            return
        self._connected = True

    def _disconnect(self) -> None:
        """Nothing stays open between calls. Safe to call twice."""
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
