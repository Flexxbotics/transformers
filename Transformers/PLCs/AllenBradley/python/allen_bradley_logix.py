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
import importlib
from werkzeug.exceptions import HTTPException
from data_models.device import Device
from transformers.abstract_device import AbstractDevice
from exceptions.flexxCoreExceptions import (
    FlexxCoreException,
    BadRequestException,
    ServerErrorException,
    ServiceUnavailableException,
)
# The protocol file is protocols/allen_bradley_eip-logix.py (hyphen) in this repo, in the runtime,
# and in FlexxControl's built-in protocol list, so a plain "from ... import" can't reach it.
# importlib takes the name as a string. The underscore name is tried first in case the file is
# ever renamed.
try:
    _eip_logix = importlib.import_module("protocols.allen_bradley_eip_logix")
except ModuleNotFoundError:
    _eip_logix = importlib.import_module("protocols.allen_bradley_eip-logix")
EIPLogix = _eip_logix.EIPLogix
EIPWrite = _eip_logix.EIPWrite


class AllenBradleyLogix(AbstractDevice):
    """
    Transformer for Allen-Bradley Logix PLCs using EtherNet/IP (CIP) via pycomm3.

    Supported series (all share the same tag-based interface):
      ControlLogix 5570  : 1756-L71/L72/L73/L74/L75  — chassis-based, slot routing required
      ControlLogix 5580  : 1756-L81E/L82E/L83E/L84E/L85E — current flagship, slot routing required
      GuardLogix 5580    : 1756-L81ES/L82ES — safety-rated, same interface as 5580
      CompactLogix 5370  : 1769-L30ER/L33ER/L36ERM — no slot needed
      CompactLogix 5380  : 5069-L306ER/L310ER/L320ER/L330ER — no slot needed

    Connection parameters (all in metaData):
      ip_address   : PLC IP address (required)
      slot         : CPU backplane slot (required for ControlLogix/GuardLogix; omit for CompactLogix)
      timeout      : connection timeout in seconds (default 2.0)
      retry        : connection retry attempts (default 2)
      status_tag   : controller tag to probe for _read_status (default "")

    Allen-Bradley / Logix-specific nuances
    ---------------------------------------
    * **Tag-based access**: Unlike Modbus or S7 (address/offset based), Logix
      uses symbolic tag names defined in Studio 5000.  Tags reference named
      variables directly (e.g. "ConveyorRunning", "Recipe.Speed",
      "Program:Main.PartCount").
    * **Slot routing (ControlLogix only)**: The CPU lives in a chassis backplane.
      The path must include the CPU slot: metaData.slot = 0 → path becomes
      "192.168.1.1/0".  CompactLogix CPUs are standalone — leave slot blank.
    * **Scope — controller vs program tags**:
      Controller-scoped tags: "TagName"
      Program-scoped tags: "Program:ProgramName.TagName"
    * **Structured tags (UDTs and arrays)**:
      UDT member:   "Drive.Fault"
      Array element: "Conveyor[2]"
      Nested:        "Station[1].Recipe.Setpoint"
    * **pycomm3 batch reads**: read_tags() sends all tags in one CIP request,
      dramatically reducing round-trip overhead vs individual reads.
    * **Controller must have EtherNet/IP enabled**: Verify in Studio 5000 that
      the controller's Ethernet port is enabled and the correct IP is set.
    * **No PUT/GET setting needed**: Unlike Siemens S7-1200/1500, Logix PLCs
      do not require a special "allow external access" toggle — EtherNet/IP
      is the native communication mechanism.
    * **BOOL array elements**: Addressed as "BoolArray[0]", not bit-level offsets.
    * **Run/Program mode writes**: Tags can be read in both Run and Program mode,
      but write access to some tags may require the controller to be in Run mode.

    Errors
    ------
    Failures raise the runtime's exceptions, which the API returns as HTTP
    errors carrying the message: BadRequestException (400) for bad args or an
    unknown command, ServiceUnavailableException (503) when the connection to
    the PLC cannot be opened, ServerErrorException (500) for anything else,
    including a tag read or write the PLC rejected and a PLC that stops
    answering on an open session (pycomm3 raises CommError, which is not a
    network error).  _read_status never raises (it returns "ERROR"), and
    neither does _read_interval_data.
    """

    def __init__(self, device: Device):
        super().__init__(device)

        self.meta_data  = device.metaData or {}
        self.ip_address = self.meta_data.get("ip_address", "")
        self.timeout    = float(self.meta_data.get("timeout", 2.0))
        self.retry      = int(self.meta_data.get("retry", 2))
        self.status_tag = self.meta_data.get("status_tag", "")

        # slot is optional — only needed for ControlLogix chassis routing
        raw_slot = self.meta_data.get("slot", "")
        self.slot = int(raw_slot) if str(raw_slot).strip() != "" else None

        if not self.ip_address:
            raise ValueError("AllenBradleyLogix: metaData must contain 'ip_address'")

        # Creating the client does not touch the network; it connects on first use.
        self._client = EIPLogix(
            ip_address=self.ip_address,
            timeout=self.timeout,
            retry=self.retry,
            slot=self.slot,
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
        Execute a named command against the Allen-Bradley Logix PLC.

        Supported commands
        ------------------
        connect
            Explicitly open EtherNet/IP connection (does nothing when already connected).
            Returns: {"status": "ok", "connected": true}

        disconnect
            Close EtherNet/IP connection.
            Returns: {"status": "ok", "connected": false}

        read_tag
            Read a single controller or program tag.
            Args: tag (str, required)  e.g. "ConveyorRunning", "Program:Main.PartCount"
            Returns: {"status": "ok", "tag": "...", "value": <value>}
            A read that returns no value raises 500.

        read_tags
            Read multiple tags in a single CIP round-trip.
            Args: tags ([str], required)
            Returns: {"status": "ok", "tags": {"TagA": <val>, "TagB": <val>, ...}}
            A tag the PLC cannot read comes back as null (no error is raised).
            With exactly one tag the result is {"": null}: pycomm3 returns a
            single Tag, not a list, and the protocol iterates its fields.

        write_tag
            Write a value to a single tag.
            Args: tag (str, required), value (<any>, required: the key must be present)
            Returns: {"status": "ok", "tag": "...", "value": <value>}

        write_tags
            Write multiple tags in a single CIP round-trip.
            Args: writes ([{"tag": str, "value": <any>}, ...], required)
            Returns: {"status": "ok", "writes": [{"tag": "...", "status": "ok"}, ...]}
            Raises 500 if the PLC rejects any of the writes.

        get_tag_list
            Retrieve the controller tag list (controller-scoped tags only).
            Args: prefix (str, optional) — filter tags starting with this string
            Returns: {"status": "ok", "tags": ["TagA", "TagB", ...]}
            Currently fails with 500 whenever the PLC has tags (see _get_tag_list).

        Errors raise: 400 for missing/invalid args or an unknown command,
        503 when the connection to the PLC cannot be opened, 500 otherwise
        (including a tag read or write the PLC rejected, and a PLC that stops
        answering on an open session).
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

            if command_name == "read_tag":
                tag   = str(self._require(args, "tag"))
                value = self._read_tag(tag)
                return json.dumps({"status": "ok", "tag": tag, "value": value},
                                  default=str)

            if command_name == "read_tags":
                tags   = [str(t) for t in self._require(args, "tags")]
                self._ensure_connected()
                result = self._client.read_tags(tags)
                return json.dumps({"status": "ok", "tags": result}, default=str)

            if command_name == "write_tag":
                tag   = str(self._require(args, "tag"))
                if "value" not in args:
                    raise BadRequestException("Missing required arg 'value'")
                value = args["value"]
                self._write_tag(tag, value)
                return json.dumps({"status": "ok", "tag": tag, "value": value},
                                  default=str)

            if command_name == "write_tags":
                try:
                    writes = [(str(w["tag"]), w["value"]) for w in self._require(args, "writes")]
                except (KeyError, TypeError) as e:
                    raise BadRequestException(
                        f"'writes' must be a list of {{\"tag\": ..., \"value\": ...}} objects ({e})")
                self._write_tags(writes)
                results = [{"tag": t, "status": "ok"} for t, _ in writes]
                return json.dumps({"status": "ok", "writes": results}, default=str)

            if command_name == "get_tag_list":
                prefix = args.get("prefix", "")
                tags   = self._get_tag_list(prefix=prefix)
                return json.dumps({"status": "ok", "tags": tags}, default=str)

        except Exception as e:
            self._raise_device_error(f"Command '{command_name}'", e)

        raise BadRequestException(f"Unknown command '{command_name}'")

    def _read_interval_data(self) -> str:
        """
        Periodic poll. Reads metaData.status_tag if configured and returns
        {"status": "ok", "tag": ..., "value": ...}; otherwise returns the last
        _read_status result as {"status": ...} without a PLC round-trip.
        Returns {"status": "ERROR"} on failure (the runtime discards the
        value).  Never raises.
        """
        try:
            if self.status_tag:
                value = self._read_tag(self.status_tag)
                return json.dumps({"status": "ok", "tag": self.status_tag, "value": value},
                                  default=str)
            return json.dumps({"status": self.status})
        except Exception as e:
            self._error(message=f"Interval read failed: {e}")
            return json.dumps({"status": "ERROR"})

    def _read_status(self, function: str = None) -> str:
        """
        Check PLC reachability by reading metaData.status_tag: "RUNNING" when
        the read succeeds (the tag's value is not used), "ERROR" when it fails.

        If no status_tag is configured, re-opens the PLC session on every call
        and returns "RUNNING" on success, "ERROR" on failure.  `function` is
        ignored.  Never raises.

        Note: Logix PLCs do not expose a dedicated run/stop register over
        EtherNet/IP, and this method does not interpret the status_tag value,
        so it reports reachability only.
        """
        try:
            if self.status_tag:
                self._read_tag(self.status_tag)
            else:
                # Re-open the session each poll (as before) so an offline PLC shows as ERROR;
                # _connect closes the previous session first.
                self._connected = False
                self._connect()
            self.status = "RUNNING"
        except Exception as e:
            self._error(message=f"Status check failed: {e}")
            self._connected = False
            self.status = "ERROR"
        return self.status

    def _read_variable(self, variable_name: str, function: str = None) -> str:
        """
        Read a tag by name.  variable_name is a Logix tag string
        e.g. "ConveyorRunning", "Drive.Speed", "Program:Main.Counter".
        Returns the value as a plain string.  Raises on failure (503 when the
        connection cannot be opened, 500 when the read fails, including a PLC
        that stops answering on an open session).
        """
        try:
            return str(self._read_tag(variable_name))
        except Exception as e:
            self._raise_device_error(f"Read variable '{variable_name}'", e)

    def _write_variable(self, variable_name: str, variable_value: str,
                        function: str = None) -> str:
        """
        Write a value to a tag.  Attempts to preserve native type by trying
        bool ("true"/"false") → int → float → string coercion in that order.
        Returns the written value.  Raises on failure (503 when the connection
        cannot be opened, 500 when the PLC rejects the write or stops answering
        on an open session).
        """
        try:
            value = self._coerce_value(variable_value)
            self._write_tag(variable_name, value)
            return variable_value
        except Exception as e:
            self._raise_device_error(f"Write variable '{variable_name}'", e)

    def _read_parameter(self, parameter_name: str, function: str = None) -> str:
        return self._read_variable(parameter_name, function)

    def _write_parameter(self, parameter_name: str, parameter_value: str,
                         function: str = None) -> str:
        return self._write_variable(parameter_name, parameter_value, function)

    def _run_program(self, function: str = None):
        """Not applicable — Logix PLCs execute programs autonomously in tasks."""
        self._info(message="run_program called — Logix PLCs run programs autonomously")
        return ""

    def _read_file_names(self) -> list:
        """Return controller tag names as a proxy for 'file' listing (currently raises; see _get_tag_list)."""
        return self._get_tag_list()

    def _read_file(self, file_name: str) -> str:
        return base64.b64encode(b"").decode("utf-8")

    def _write_file(self, file_name: str, file_data: str):
        pass

    def _load_file(self, file_name: str):
        pass

    # ############################################################################## #
    # INTERFACE HELPER METHODS
    # ############################################################################## #

    def _get_tag_list(self, prefix: str = "") -> list:
        """
        Return controller-scoped tag names from the PLC.
        Filtered by prefix when provided.  Raises on failure.
        Note: pycomm3's get_tag_list() returns dicts, and this reads
        t.tag_name, so it currently raises ServerErrorException (500,
        AttributeError) whenever the PLC has any tags.
        """
        try:
            self._ensure_connected()
            # pycomm3 LogixDriver exposes get_tag_list() on the underlying driver
            # Access it via the protocol's internal driver through read_tags workaround:
            # We use the driver attribute path that pycomm3 exposes publicly.
            driver = getattr(self._client, "_EIPLogix__driver", None)
            if driver is None:
                return []
            tags = driver.get_tag_list()
            names = [t.tag_name for t in tags] if tags else []
            if prefix:
                names = [n for n in names if n.startswith(prefix)]
            return names
        except Exception as e:
            self._raise_device_error("Get tag list", e)

    def _read_tag(self, tag: str):
        """
        Read one tag and return its value.  pycomm3 reports a failed read as a
        value of None instead of raising, so None is treated as a failed read.
        """
        self._ensure_connected()
        value = self._client.read_tag(tag)
        if value is None:
            raise ServerErrorException(f"Read tag '{tag}' failed: the PLC returned no value")
        return value

    def _write_tag(self, tag: str, value) -> None:
        """Write one tag; raises ServerErrorException when the PLC rejects it."""
        self._ensure_connected()
        self._check_write_result(self._client.write_tag(tag, value))

    def _write_tags(self, writes: list) -> None:
        """Write several (tag, value) pairs in one request; raises if any is rejected."""
        self._ensure_connected()
        self._check_write_result(self._client.write_tags(writes))

    @staticmethod
    def _check_write_result(result) -> None:
        """
        pycomm3 reports a failed write in the returned Tag's .error instead of
        raising.  It returns one Tag for a single write and a list for several
        (a Tag is a NamedTuple, so only a list is treated as several).
        """
        results = result if isinstance(result, list) else [result]
        errors = [f"{getattr(r, 'tag', '?')}: {r.error}" for r in results if getattr(r, "error", None)]
        if errors:
            raise ServerErrorException(f"Write failed: {'; '.join(errors)}")

    def _connect(self) -> None:
        if self._connected:
            return
        self._info(message=f"Connecting to Logix PLC: {self.ip_address}")
        # connect() replaces the session without closing it; close a stale one first.
        self._client.disconnect()
        rc = self._client.connect()
        if rc != 0:
            self._connected = False
            raise ServiceUnavailableException(
                f"AllenBradleyLogix: connect failed ({self.ip_address})"
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

    @staticmethod
    def _coerce_value(raw: str):
        """Try bool keyword ("true"/"false"), then int, then float, then return as-is string."""
        lower = raw.strip().lower()
        if lower == "true":
            return True
        if lower == "false":
            return False
        try:
            return int(raw)
        except ValueError:
            pass
        try:
            return float(raw)
        except ValueError:
            pass
        return raw

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
