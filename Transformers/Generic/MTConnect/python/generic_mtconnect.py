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
import xml.etree.ElementTree as ET
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
from protocols.mtconnect import MTConnect


class GenericMTConnect(AbstractDevice):
    """
    Generic MTConnect transformer.

    Reads data from any device that exposes an MTConnect Agent — CNCs, robots,
    CMMs, additive machines, or any device with an MTConnect adapter installed.

    MTConnect (ANSI/MTC1-1) is a read-only, HTTP/XML-based standard for
    manufacturing equipment data.  Unlike OPC-UA or Modbus, there is no
    write capability — MTConnect is purely an observation/monitoring protocol.
    Writing to a machine must be done through a separate interface.

    Connection parameters (metaData)
    ---------------------------------
    ip_address        : IP address of the MTConnect Agent (required)
    port              : HTTP port of the agent (default 5000)
    device_name       : Agent device name or UUID to scope queries to a
                        specific machine (optional — leave blank for single-
                        device agents)
    status_tag        : dataItemId to read for _read_status (default "execution")
                        Common tags: "execution", "availability", "mode"
    interval_tags     : Comma-separated dataItemIds to read on interval, e.g.
                        "execution,program,sspeed,afeed"

    MTConnect nuances
    -----------------
    * MTConnect agents serve three primary endpoints:
        /probe    → device/component metadata and data item catalog
        /current  → latest snapshot of all data item values
        /sample   → time-series stream of recent observations
      This transformer uses /current for all polling operations.
    * dataItemIds are defined by the device's MTConnect Device Information
      Model (the XML document served by /probe).  Common standard IDs:
        execution   → ACTIVE, READY, INTERRUPTED, FEED_HOLD, STOPPED, etc.
        availability→ AVAILABLE, UNAVAILABLE
        mode        → AUTOMATIC, MANUAL, MANUAL_DATA_INPUT
        program     → currently loaded NC program name
        sspeed      → spindle speed (actual)
        afeed       → feed rate (actual)
        load        → spindle/axis load
        alarm       → alarm/fault messages (ComponentStream/Alarms)
    * ComponentStream filtering: use component_stream arg to scope a read_tag
      call to a specific subsystem, e.g. "Controller", "Axes", "Spindle".
    * MTConnect does not have a "connection" in the TCP sense — every call is
      a fresh HTTP GET.  connect() / disconnect() are implemented for interface
      compatibility and simply verify/clear reachability.
    * Timestamps in MTConnect responses are ISO 8601 UTC strings.
    * UNAVAILABLE is the sentinel value when a data item has no current reading
      (e.g. machine is powered off, or the adapter hasn't reported it yet).

    Errors
    ------
    Failures raise the runtime's exceptions, which the API returns as HTTP
    errors carrying the message: BadRequestException (400) for missing or
    non-JSON args, an unknown command, or a write (MTConnect is read-only);
    ServiceUnavailableException (503) when the agent does not respond or
    answers with an HTTP error; ServerErrorException (500) for anything else
    (e.g. unparseable XML, or a non-numeric count).  _read_status never raises
    (it returns "ERROR"), and neither does _read_interval_data.
    """

    # Standard MTConnect Execution values
    _EXECUTION_RUNNING = {"ACTIVE", "FEED_HOLD", "INTERRUPTED", "WAIT"}
    _EXECUTION_IDLE    = {"READY", "STOPPED", "OPTIONAL_STOP", "PROGRAM_STOPPED",
                          "PROGRAM_COMPLETED"}

    def __init__(self, device: Device):
        super().__init__(device)

        self.meta_data    = device.metaData or {}
        self.ip_address   = self.meta_data.get("ip_address", "")
        self.port         = str(self.meta_data.get("port", "5000"))
        self.device_name  = self.meta_data.get("device_name", "").strip() or None
        self.status_tag   = self.meta_data.get("status_tag", "execution").strip()
        self.interval_tags = [
            t.strip()
            for t in self.meta_data.get("interval_tags", "").split(",")
            if t.strip()
        ]

        if not self.ip_address:
            raise ValueError("GenericMTConnect: metaData must contain 'ip_address'")

        # Base paths
        self._path_current = "/current"
        self._path_sample  = "/sample"
        self._path_probe   = "/probe"

        # Scope to a named device if provided
        if self.device_name:
            self._path_current = f"/{self.device_name}/current"
            self._path_sample  = f"/{self.device_name}/sample"
            self._path_probe   = f"/{self.device_name}/probe"

        self._client: Optional[MTConnect] = None
        self._reachable = False
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
        Execute a named MTConnect command.

        Supported commands
        ------------------
        connect
            Verify the MTConnect Agent is reachable by fetching /probe.
            Returns: {"status": "ok", "connected": true, "agent_url": "..."}

        disconnect
            Clear the client reference.
            Returns: {"status": "ok", "connected": false}

        probe
            Fetch the full MTConnect Device Information Model (/probe).
            Returns {"status": "ok", "xml": "..."}: the XML as re-serialized by
            ElementTree (not byte-for-byte; namespaces get ns0-style prefixes).

        current
            Fetch the full current snapshot of all data items (/current).
            Returns {"status": "ok", "xml": "..."}, re-serialized as for probe.

        sample
            Fetch a time-series sample (/sample).
            Args: from_sequence (int, optional), count (int, optional, default 100)
            Returns {"status": "ok", "xml": "..."}, re-serialized as for probe.

        read_tag
            Read specific data item(s) by dataItemId from /current (matched
            case-insensitively; an element without a dataItemId is matched on
            its id or name).
            Args: tag (str, required)
                  component_stream (str, optional) — scope to a ComponentStream
                                   name/component/componentId, e.g. "Controller"
            Returns: {"status": "ok", "tag": "...", "results": [{"matched_by":
                      "dataItemId", "tag": "<element name>", "text": "...",
                      "attrib": {...}, "alarms": [...]}]}

        read_tags
            Read multiple tags, fetching /current once per tag.
            Args: tags (comma-separated string or JSON array of dataItemIds,
                        required)
                  component_stream (str, optional)
            Returns: {"status": "ok", "values": {"<tag>": "<value>", ...}}
                     (the first match's text, or "UNAVAILABLE" when none)

        Errors raise: 400 for missing or non-JSON args or an unknown command,
        503 when the agent does not respond or answers with an HTTP error,
        500 otherwise (e.g. unparseable XML, or a non-numeric count).
        """
        args = self._parse_args(command_args)
        self._info(message=f"Sending command: {command_name}")

        try:
            if command_name == "connect":
                self._connect()
                return json.dumps({
                    "status": "ok",
                    "connected": True,
                    "agent_url": self._agent_url(),
                })

            if command_name == "disconnect":
                self._disconnect()
                return json.dumps({"status": "ok", "connected": False})

            if command_name == "probe":
                self._ensure_connected()
                client = MTConnect(self.ip_address, self.port, self._path_probe)
                tree   = client._get_data()
                xml_str = ET.tostring(tree.getroot(), encoding="unicode")
                return json.dumps({"status": "ok", "xml": xml_str})

            if command_name == "current":
                self._ensure_connected()
                client  = MTConnect(self.ip_address, self.port, self._path_current)
                tree    = client._get_data()
                xml_str = ET.tostring(tree.getroot(), encoding="unicode")
                return json.dumps({"status": "ok", "xml": xml_str})

            if command_name == "sample":
                self._ensure_connected()
                from_seq = args.get("from_sequence")
                count    = int(args.get("count") or 100)
                path     = self._path_sample + f"?count={count}"
                if from_seq not in (None, ""):
                    path += f"&from={from_seq}"
                client  = MTConnect(self.ip_address, self.port, path)
                tree    = client._get_data()
                xml_str = ET.tostring(tree.getroot(), encoding="unicode")
                return json.dumps({"status": "ok", "xml": xml_str})

            if command_name == "read_tag":
                tag              = str(self._require(args, "tag")).strip()
                component_stream = args.get("component_stream")
                self._ensure_connected()
                results = self._read_tag(tag, component_stream=component_stream)
                return json.dumps({
                    "status": "ok",
                    "tag": tag,
                    "results": results,
                }, default=str)

            if command_name == "read_tags":
                raw_tags         = args.get("tags", "")
                component_stream = args.get("component_stream")
                tags             = self._parse_tags(raw_tags)
                if not tags:
                    raise BadRequestException("Missing required arg 'tags'")
                self._ensure_connected()
                values = {}
                for tag in tags:
                    results = self._read_tag(tag, component_stream=component_stream)
                    values[tag] = results[0]["text"] if results else "UNAVAILABLE"
                return json.dumps({"status": "ok", "values": values}, default=str)

        except Exception as e:
            self._raise_device_error(f"Command '{command_name}'", e)

        raise BadRequestException(f"Unknown command '{command_name}'")

    def _read_interval_data(self) -> str:
        """
        Periodic poll. Reads each tag in metaData.interval_tags from /current
        and returns their latest values ("UNAVAILABLE" for a tag with no
        match).  Never raises: a tag that fails (or every tag, when the agent
        is unreachable) reports "ERROR: ...".  Falls back to _read_status when
        no interval_tags are configured.
        """
        if not self.interval_tags:
            return json.dumps({"status": self._read_status()})

        values = {}
        try:
            self._ensure_connected()
            for tag in self.interval_tags:
                try:
                    results = self._read_tag(tag)
                    values[tag] = results[0]["text"] if results else "UNAVAILABLE"
                except Exception as e:
                    values[tag] = f"ERROR: {e}"
        except Exception as e:
            self._error(message=f"Interval read failed: {e}")
            values = {tag: f"ERROR: {e}" for tag in self.interval_tags}
        return json.dumps({"status": "ok", "values": values}, default=str)

    def _read_status(self, function: str = None) -> str:
        """
        Read machine status via the configured status_tag (default: "execution").

        MTConnect Execution values → Flexx status:
          ACTIVE, FEED_HOLD, WAIT, INTERRUPTED → "RUNNING"
          READY, STOPPED, OPTIONAL_STOP,
          PROGRAM_STOPPED, PROGRAM_COMPLETED   → "IDLE"
          UNAVAILABLE, or no matching item     → "UNAVAILABLE"
          anything else                        → returned upper-cased
          any failure                          → "ERROR" (never raises)
        """
        try:
            self._ensure_connected()
            results = self._read_tag(self.status_tag)
            if not results:
                self.status = "UNAVAILABLE"
                return self.status

            raw = results[0].get("text", "UNAVAILABLE").strip().upper()

            if raw in self._EXECUTION_RUNNING:
                self.status = "RUNNING"
            elif raw in self._EXECUTION_IDLE:
                self.status = "IDLE"
            elif raw == "UNAVAILABLE":
                self.status = "UNAVAILABLE"
            else:
                self.status = raw

        except Exception as e:
            self._error(message=f"Status read failed: {e}")
            self.status = "ERROR"

        return self.status

    def _read_variable(self, variable_name: str, function: str = None) -> str:
        """
        Read a single MTConnect dataItemId (e.g. "execution") from /current and
        return its text as a plain string ("UNAVAILABLE" when the agent has no
        such item).  Raises on failure (503 when the agent does not respond
        or answers with an HTTP error, 500 otherwise).
        """
        try:
            self._ensure_connected()
            results = self._read_tag(variable_name)
            return results[0]["text"] if results else "UNAVAILABLE"
        except Exception as e:
            self._raise_device_error(f"Read variable '{variable_name}'", e)

    def _write_variable(self, variable_name: str, variable_value: str,
                        function: str = None) -> str:
        """
        MTConnect is read-only — writes are not supported by the protocol.
        Always raises BadRequestException (400); use a separate write interface
        (e.g. OPC-UA, Modbus) for machine writes.
        """
        raise BadRequestException("MTConnect is read-only")

    def _read_parameter(self, parameter_name: str, function: str = None) -> str:
        return self._read_variable(parameter_name, function)

    def _write_parameter(self, parameter_name: str, parameter_value: str,
                         function: str = None) -> str:
        return self._write_variable(parameter_name, parameter_value, function)

    def _read_file_names(self) -> list:
        """Read active program name from the agent as a proxy for loaded file."""
        try:
            self._ensure_connected()
            results = self._read_tag("program")
            name    = results[0]["text"] if results else ""
            return [name] if name and name != "UNAVAILABLE" else []
        except Exception:
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
        Verify the agent is reachable by fetching /probe.
        MTConnect is stateless HTTP — there is no persistent TCP session.
        """
        self._info(message=f"Connecting to MTConnect Agent: {self._agent_url()}")
        try:
            client = MTConnect(self.ip_address, self.port, self._path_probe)
            client._get_data()   # raises on HTTP error or network failure
            self._reachable = True
            self._client    = MTConnect(self.ip_address, self.port, self._path_current)
            self._info(message=f"MTConnect Agent reachable: {self._agent_url()}")
        except Exception as e:
            self._reachable = False
            raise ServiceUnavailableException(
                f"GenericMTConnect: cannot reach agent at {self._agent_url()}: {e}"
            ) from e

    def _disconnect(self) -> None:
        self._client    = None
        self._reachable = False

    def _ensure_connected(self) -> None:
        if not self._reachable or self._client is None:
            self._connect()

    def _read_tag(self, tag: str,
                  component_stream: Optional[str] = None) -> list:
        """
        Internal: fetch /current and read a dataItemId.
        Returns list of result dicts from MTConnect.read_tag().
        """
        client = MTConnect(self.ip_address, self.port, self._path_current)
        return client.read_tag(
            component_stream=component_stream,
            tag=tag,
        )

    def _agent_url(self) -> str:
        return f"http://{self.ip_address}:{self.port}"

    @staticmethod
    def _parse_tags(raw) -> List[str]:
        """Accept a comma-separated string or JSON array of tag IDs."""
        if isinstance(raw, list):
            return [str(t).strip() for t in raw if str(t).strip()]
        if isinstance(raw, str):
            stripped = raw.strip()
            if stripped.startswith("["):
                try:
                    return [str(t).strip() for t in json.loads(stripped)
                            if str(t).strip()]
                except Exception:
                    pass
            return [t.strip() for t in stripped.split(",") if t.strip()]
        return []

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
