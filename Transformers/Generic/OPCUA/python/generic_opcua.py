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
from protocols.opcua import OPCUA, OpcuaWrite


class GenericOPCUA(AbstractDevice):
    """
    Generic OPC-UA transformer.

    Connects to any device that exposes an OPC-UA server — PLCs, CNCs, robots,
    vision systems, IoT gateways, or any industrial device supporting the
    OPC-UA standard (IEC 62541).

    Because OPC-UA is a vendor-neutral open standard, this transformer is not
    tied to any specific machine brand.  The same class can be pointed at a
    Siemens S7-1500, a Fanuc CNC, a Beckhoff IPC, a custom IoT edge device, or
    any other OPC-UA server simply by changing the metaData configuration.

    Connection parameters (metaData)
    ---------------------------------
    endpoint          : Full OPC-UA endpoint URL, e.g. "opc.tcp://192.168.1.10:4840"
                        If omitted, built from ip_address + port.
    ip_address        : IP address of the OPC-UA server (used if endpoint not set)
    port              : OPC-UA port (default 4840)
    username          : Username for authenticated sessions (optional)
    password          : Password for authenticated sessions (optional)
    security_string   : python-opcua security string, e.g.
                        "Basic256Sha256,SignAndEncrypt,cert.pem,key.pem" (optional)
    timeout           : Request timeout in seconds (default 4.0)
    retry             : Connection retry attempts (default 2)
    retry_interval    : Seconds between retries (default 0.1)
    status_node_id    : NodeId to read for _read_status, e.g. "ns=2;s=Status"
                        Defaults (also when blank) to the OPC-UA standard
                        ServerState node (i=2259).
                        Value 0 = Running, 1 = Failed, 2 = NoConfig, 3 = Suspended,
                        4 = Shutdown, 5 = Test, 6 = CommunicationFault, 7 = Unknown.
    interval_node_ids : Comma-separated NodeIds to poll on interval, e.g.
                        "ns=2;s=Speed,ns=2;s=Temp"
    subscription_queue_maxlen : Max buffered subscription notifications (default 5000)

    OPC-UA nuances
    --------------
    * NodeIds can be numeric (i=), string (s=), GUID (g=), or opaque (b=).
      Always use the fully qualified form: "ns=<namespace>;i=<id>" or
      "ns=<namespace>;s=<name>".  Namespace 0 is the OPC-UA standard namespace.
    * The standard ServerState node (i=2259) returns an enum:
      0=Running, 1=Failed, 2=NoConfig, 3=Suspended, 4=Shutdown, 5=Test,
      6=CommunicationFault, 7=Unknown.
    * Security: for production deployments, always use at least
      Basic256Sha256,Sign.  Anonymous/None is for development only.
    * Subscriptions are push-based (server notifies client on change) and are
      far more efficient than polling for high-frequency signals.  Use the
      subscribe + receive_notifications command pair for event-driven reads.
    * OPC-UA servers often restart their NamespaceIndex on firmware update —
      always verify namespace indices after any server update.

    Errors
    ------
    Failures raise the runtime's exceptions, which the API returns as HTTP
    errors carrying the message: BadRequestException (400) for missing or
    non-JSON args or an unknown command, ServiceUnavailableException (503)
    when the server does not respond, ServerErrorException (500) for anything
    else (e.g. a bad NodeId, or a non-numeric value for a numeric arg).
    _read_status never raises (it returns "ERROR"), and neither does
    _read_interval_data.
    """

    # Standard OPC-UA ServerState node
    _SERVER_STATE_NODE = "i=2259"
    _SERVER_STATE_NAMES = {
        0: "Running",
        1: "Failed",
        2: "NoConfig",
        3: "Suspended",
        4: "Shutdown",
        5: "Test",
        6: "CommunicationFault",
        7: "Unknown",
    }

    def __init__(self, device: Device):
        super().__init__(device)

        self.meta_data        = device.metaData or {}
        self.ip_address       = self.meta_data.get("ip_address", "")
        self.port             = self.meta_data.get("port", "4840")
        self.username         = self.meta_data.get("username", "") or None
        self.password         = self.meta_data.get("password", "") or None
        self.security_string  = self.meta_data.get("security_string", "") or None
        self.timeout          = float(self.meta_data.get("timeout", 4.0))
        self.retry            = int(self.meta_data.get("retry", 2))
        self.retry_interval   = float(self.meta_data.get("retry_interval", 0.1))
        self.status_node_id   = self.meta_data.get("status_node_id") or self._SERVER_STATE_NODE
        self.interval_node_ids = [
            n.strip()
            for n in self.meta_data.get("interval_node_ids", "").split(",")
            if n.strip()
        ]
        self.subscription_queue_maxlen = int(
            self.meta_data.get("subscription_queue_maxlen", 5000)
        )

        # Build endpoint URL from parts if not explicitly provided
        endpoint = self.meta_data.get("endpoint", "").strip()
        if not endpoint:
            if not self.ip_address:
                raise ValueError("GenericOPCUA: metaData must contain 'endpoint' or 'ip_address'")
            endpoint = f"opc.tcp://{self.ip_address}:{self.port}"
        self.endpoint = endpoint

        self._client: Optional[OPCUA] = None
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
        Execute a named OPC-UA command.

        Supported commands
        ------------------
        connect
            Open OPC-UA session to the server.
            Returns: {"status": "ok", "connected": true, "endpoint": "..."}

        disconnect
            Close OPC-UA session.
            Returns: {"status": "ok", "connected": false}

        read
            Read a single node value.
            Args: node_id (str, required)
            Returns: {"status": "ok", "node_id": "...", "value": <value>}

        write
            Write a value to a single node.
            Args: node_id (str, required), value (<any>), variant_type (str, optional)
                  variant_type values: Boolean, Int16, Int32, Int64, UInt16,
                                       UInt32, UInt64, Float, Double, String,
                                       DateTime, Byte, SByte (any other name
                                       writes untyped)
            Returns: {"node_id": "...", "value": <value>, "status": "ok"}

        read_nodes
            Batch read multiple nodes in a single request.
            Args: node_ids (comma-separated string or JSON array, required)
            Returns: {"status": "ok", "values": {"<node_id>": <value>, ...}}
                     A node that fails to read has the value "ERROR: ...".

        browse
            Browse children of a node.
            Args: node_id (str, default "i=85" = Objects folder)
            Returns: {"status": "ok", "node_id": "...", "nodes": [{"node_id": "...",
                      "browse_name": "...", "display_name": "..."}, ...]}

        subscribe
            Subscribe to data-change notifications for one or more nodes,
            replacing any previous subscription.  The subscription is lost if
            the connection drops; call subscribe again after a reconnect.
            Args: node_ids (comma-separated or JSON array, required),
                  publishing_interval_ms (int, default 250)
            Returns: {"status": "ok", "subscribed": <count>,
                      "publishing_interval_ms": n}

        receive_notifications
            Drain buffered subscription notifications (non-blocking).
            Args: buffer_size (int, default 10)
            Returns: {"status": "ok", "notifications": [{"node_id": "...",
                      "value": ..., "source_timestamp": ...,
                      "server_timestamp": ...}, ...]}

        health_check
            Probe the server's CurrentTime node to verify liveness.
            Returns: {"status": "ok", "alive": true/false}

        Errors raise: 400 for missing or non-JSON args or an unknown command,
        503 when the server does not respond, 500 otherwise (including a
        non-numeric value for a numeric arg).
        """
        args = self._parse_args(command_args)
        self._info(message=f"Sending command: {command_name}")

        try:
            # ---- connect / disconnect ----
            if command_name == "connect":
                self._connect()
                return json.dumps({"status": "ok", "connected": True,
                                   "endpoint": self.endpoint})

            if command_name == "disconnect":
                self._disconnect()
                return json.dumps({"status": "ok", "connected": False})

            # ---- read ----
            if command_name == "read":
                node_id = str(self._require(args, "node_id"))
                self._ensure_connected()
                value = self._client.read_node(node_id)
                return json.dumps({"status": "ok", "node_id": node_id,
                                   "value": value}, default=str)

            # ---- write ----
            if command_name == "write":
                node_id      = str(self._require(args, "node_id"))
                value        = args.get("value")
                variant_type = args.get("variant_type")
                self._ensure_connected()
                vt = self._variant_type_from_str(variant_type)
                self._client.write_node(node_id, value, variant_type=vt)
                return json.dumps({"status": "ok", "node_id": node_id,
                                   "value": value}, default=str)

            # ---- read_nodes (batch) ----
            if command_name == "read_nodes":
                node_ids = self._parse_node_ids(args.get("node_ids", ""))
                if not node_ids:
                    raise BadRequestException("Missing required arg 'node_ids'")
                self._ensure_connected()
                results = self._client.read_nodes(node_ids)
                return json.dumps({"status": "ok", "values": results},
                                  default=str)

            # ---- browse ----
            if command_name == "browse":
                node_id = str(args.get("node_id") or "i=85")
                self._ensure_connected()
                nodes   = self._client.browse(node_id)
                return json.dumps({"status": "ok", "node_id": node_id,
                                   "nodes": nodes}, default=str)

            # ---- subscribe ----
            if command_name == "subscribe":
                node_ids     = self._parse_node_ids(args.get("node_ids", ""))
                interval_ms  = int(args.get("publishing_interval_ms") or 250)
                if not node_ids:
                    raise BadRequestException("Missing required arg 'node_ids'")
                self._ensure_connected()
                self._client.subscribe_data_change(node_ids,
                                                   publishing_interval_ms=interval_ms)
                return json.dumps({"status": "ok", "subscribed": len(node_ids),
                                   "publishing_interval_ms": interval_ms})

            # ---- receive_notifications ----
            if command_name == "receive_notifications":
                buf_size = int(args.get("buffer_size") or 10)
                self._ensure_connected()
                raw      = self._client.receive(buffer_size=buf_size)
                return json.dumps({"status": "ok", "notifications": json.loads(raw)},
                                  default=str)

            # ---- health_check ----
            if command_name == "health_check":
                self._ensure_connected()
                alive = self._client.health_check(force=True)
                return json.dumps({"status": "ok", "alive": alive})

        except Exception as e:
            self._raise_device_error(f"Command '{command_name}'", e)

        raise BadRequestException(f"Unknown command '{command_name}'")

    def _read_interval_data(self) -> str:
        """
        Periodic poll. Reads each node in metaData.interval_node_ids and
        returns their values as a JSON map.  Never raises: a node that fails
        (or every node, when the server is unreachable) reports "ERROR: ...".
        Falls back to _read_status when no interval_node_ids are configured.
        """
        if not self.interval_node_ids:
            return json.dumps({"status": self._read_status()})

        results = {}
        try:
            self._ensure_connected()
            for node_id in self.interval_node_ids:
                try:
                    results[node_id] = self._client.read_node(node_id)
                except Exception as e:
                    results[node_id] = f"ERROR: {e}"
        except Exception as e:
            self._error(message=f"Interval read failed: {e}")
            results = {node_id: f"ERROR: {e}" for node_id in self.interval_node_ids}
        return json.dumps({"status": "ok", "values": results}, default=str)

    def _read_status(self, function: str = None) -> str:
        """
        Read the OPC-UA server state.

        If metaData.status_node_id is set to a custom node, reads that and
        returns its string value directly ("UNKNOWN" when it holds no value).

        If using the default standard ServerState node (i=2259):
          0=Running            → "RUNNING"
          1=Failed             → "FAILED"
          2=NoConfig           → "NOCONFIG"
          3=Suspended          → "SUSPENDED"
          4=Shutdown           → "SHUTDOWN"
          5=Test               → "TEST"
          6=CommunicationFault → "COMMUNICATIONFAULT"
          other (or no value)  → "UNKNOWN"

        Any failure returns "ERROR" (never raises).
        """
        try:
            self._ensure_connected()
            raw = self._client.read_node(self.status_node_id)

            if self.status_node_id == self._SERVER_STATE_NODE:
                state_int = int(raw) if raw is not None else 7
                self.status = self._SERVER_STATE_NAMES.get(state_int, "UNKNOWN").upper()
            else:
                self.status = str(raw) if raw is not None else "UNKNOWN"

        except Exception as e:
            self._error(message=f"Status read failed: {e}")
            # Reconnect next time only after a communication error; a node-level
            # error (e.g. a bad status_node_id) must not drop the session and
            # its subscriptions on every poll.
            if isinstance(e, (OSError, TimeoutError, ConnectionError)):
                self._connected = False
            self.status = "ERROR"

        return self.status

    def _read_variable(self, variable_name: str, function: str = None) -> str:
        """
        Read an OPC-UA node by NodeId string (e.g. "ns=2;s=Speed") and return
        its value as a plain string ("" when the node holds no value).
        Raises on failure (503 when the server does not respond, 500 otherwise).
        """
        try:
            self._ensure_connected()
            value = self._client.read_node(variable_name)
            return str(value) if value is not None else ""
        except Exception as e:
            self._raise_device_error(f"Read variable '{variable_name}'", e)

    def _write_variable(self, variable_name: str, variable_value: str,
                        function: str = None) -> str:
        """
        Write a value to an OPC-UA node. Coerces string value to bool/int/float.
        Returns the written value.  Raises on failure (503 when the server does
        not respond, 500 otherwise).
        """
        try:
            self._ensure_connected()
            value = self._coerce_value(variable_value)
            self._client.write_node(variable_name, value)
            return variable_value
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
        self._info(message=f"Connecting to OPC-UA server: {self.endpoint}")
        if self._connected and self._client is not None:
            return

        self._client = OPCUA(
            endpoint=self.endpoint,
            timeout=self.timeout,
            retry=self.retry,
            retry_interval=self.retry_interval,
            username=self.username,
            password=self.password,
            security_string=self.security_string,
            subscription_queue_maxlen=self.subscription_queue_maxlen,
        )

        rc = self._client.connect()
        if rc != 0:
            self._connected = False
            raise ServiceUnavailableException(
                f"GenericOPCUA: connect failed (endpoint={self.endpoint})"
            )

        self._connected = True
        self._info(message=f"Connected to OPC-UA server: {self.endpoint}")

    def _disconnect(self) -> None:
        self._info(message=f"Disconnecting from OPC-UA server: {self.endpoint}")
        try:
            if self._client is not None:
                self._client.disconnect()
        finally:
            self._client = None
            self._connected = False

    def _ensure_connected(self) -> None:
        if not self._connected or self._client is None:
            self._connect()

    @staticmethod
    def _parse_node_ids(raw) -> List[str]:
        """Accept a comma-separated string or JSON array of NodeId strings."""
        if isinstance(raw, list):
            return [str(n).strip() for n in raw if str(n).strip()]
        if isinstance(raw, str):
            # Try JSON array first
            stripped = raw.strip()
            if stripped.startswith("["):
                try:
                    return [str(n).strip() for n in json.loads(stripped) if str(n).strip()]
                except Exception:
                    pass
            return [n.strip() for n in stripped.split(",") if n.strip()]
        return []

    @staticmethod
    def _variant_type_from_str(vt_str: Optional[str]):
        """Convert a string variant type name to the ua.VariantType enum value."""
        if not vt_str:
            return None
        try:
            from opcua import ua  # type: ignore
            mapping = {
                "Boolean":  ua.VariantType.Boolean,
                "Int16":    ua.VariantType.Int16,
                "Int32":    ua.VariantType.Int32,
                "Int64":    ua.VariantType.Int64,
                "UInt16":   ua.VariantType.UInt16,
                "UInt32":   ua.VariantType.UInt32,
                "UInt64":   ua.VariantType.UInt64,
                "Float":    ua.VariantType.Float,
                "Double":   ua.VariantType.Double,
                "String":   ua.VariantType.String,
                "DateTime": ua.VariantType.DateTime,
                "Byte":     ua.VariantType.Byte,
                "SByte":    ua.VariantType.SByte,
            }
            return mapping.get(str(vt_str).strip())
        except Exception:
            return None

    @staticmethod
    def _coerce_value(raw: str):
        """Coerce string to bool → int → float → str."""
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
