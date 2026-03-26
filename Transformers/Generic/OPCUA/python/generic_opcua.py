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
                        Defaults to the OPC-UA standard ServerState node (i=2259).
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
        self.status_node_id   = self.meta_data.get("status_node_id", self._SERVER_STATE_NODE)
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
            if self._connected:
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
        Execute a named OPC-UA command.

        Supported commands
        ------------------
        connect
            Open OPC-UA session to the server.

        disconnect
            Close OPC-UA session.

        read
            Read a single node value.
            Args: node_id (str), variant_type (str, optional)
            Returns: {"node_id": "...", "value": <value>}

        write
            Write a value to a single node.
            Args: node_id (str), value (<any>), variant_type (str, optional)
                  variant_type values: Boolean, Int16, Int32, Int64, UInt16,
                                       UInt32, UInt64, Float, Double, String
            Returns: {"node_id": "...", "value": <value>, "status": "ok"}

        read_nodes
            Batch read multiple nodes in a single request.
            Args: node_ids (comma-separated string or JSON array)
            Returns: {"values": {"<node_id>": <value>, ...}}

        browse
            Browse children of a node.
            Args: node_id (str, default "i=85" = Objects folder)
            Returns: {"nodes": [{"node_id": "...", "browse_name": "...", ...}]}

        subscribe
            Subscribe to data-change notifications for one or more nodes.
            Args: node_ids (comma-separated or JSON array),
                  publishing_interval_ms (int, default 250)
            Returns: {"status": "ok", "subscribed": <count>}

        receive_notifications
            Drain buffered subscription notifications (non-blocking).
            Args: buffer_size (int, default 10)
            Returns: [{"node_id": "...", "value": ..., "source_timestamp": ...}, ...]

        health_check
            Probe the server's CurrentTime node to verify liveness.
            Returns: {"alive": true/false}
        """
        args = json.loads(command_args) if command_args else {}
        if isinstance(args, dict) and "value" in args and isinstance(args.get("value"), (str, dict)):
            inner = args["value"]
            if isinstance(inner, str):
                try:
                    inner = json.loads(inner)
                except Exception:
                    pass
            if isinstance(inner, dict):
                args = inner

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
                self._ensure_connected()
                node_id = str(args.get("node_id", ""))
                if not node_id:
                    return self._err("Missing required field: node_id")
                value = self._client.read_node(node_id)
                return json.dumps({"status": "ok", "node_id": node_id,
                                   "value": value}, default=str)

            # ---- write ----
            if command_name == "write":
                self._ensure_connected()
                node_id      = str(args.get("node_id", ""))
                value        = args.get("value")
                variant_type = args.get("variant_type")
                if not node_id:
                    return self._err("Missing required field: node_id")
                vt = self._variant_type_from_str(variant_type)
                self._client.write_node(node_id, value, variant_type=vt)
                return json.dumps({"status": "ok", "node_id": node_id,
                                   "value": value}, default=str)

            # ---- read_nodes (batch) ----
            if command_name == "read_nodes":
                self._ensure_connected()
                node_ids = self._parse_node_ids(args.get("node_ids", ""))
                if not node_ids:
                    return self._err("Missing required field: node_ids")
                results = self._client.read_nodes(node_ids)
                return json.dumps({"status": "ok", "values": results},
                                  default=str)

            # ---- browse ----
            if command_name == "browse":
                self._ensure_connected()
                node_id = str(args.get("node_id", "i=85"))
                nodes   = self._client.browse(node_id)
                return json.dumps({"status": "ok", "node_id": node_id,
                                   "nodes": nodes}, default=str)

            # ---- subscribe ----
            if command_name == "subscribe":
                self._ensure_connected()
                node_ids     = self._parse_node_ids(args.get("node_ids", ""))
                interval_ms  = int(args.get("publishing_interval_ms", 250))
                if not node_ids:
                    return self._err("Missing required field: node_ids")
                self._client.subscribe_data_change(node_ids,
                                                   publishing_interval_ms=interval_ms)
                return json.dumps({"status": "ok", "subscribed": len(node_ids),
                                   "publishing_interval_ms": interval_ms})

            # ---- receive_notifications ----
            if command_name == "receive_notifications":
                self._ensure_connected()
                buf_size = int(args.get("buffer_size", 10))
                raw      = self._client.receive(buffer_size=buf_size)
                return json.dumps({"status": "ok", "notifications": json.loads(raw)},
                                  default=str)

            # ---- health_check ----
            if command_name == "health_check":
                self._ensure_connected()
                alive = self._client.health_check(force=True)
                return json.dumps({"status": "ok", "alive": alive})

            return self._err(f"Unknown command: '{command_name}'")

        except Exception as e:
            self._error(message=f"Command '{command_name}' failed: {e}")
            return self._err(str(e))

    def _read_interval_data(self) -> str:
        """
        Periodic poll. Reads each node in metaData.interval_node_ids and
        returns their values as a JSON map.
        """
        if not self.interval_node_ids:
            return json.dumps({"status": self._read_status()})

        self._ensure_connected()
        results = {}
        for node_id in self.interval_node_ids:
            try:
                results[node_id] = self._client.read_node(node_id)
            except Exception as e:
                results[node_id] = f"ERROR: {e}"
        return json.dumps({"status": "ok", "values": results}, default=str)

    def _read_status(self, function: str = None) -> str:
        """
        Read the OPC-UA server state.

        If metaData.status_node_id is set to a custom node, reads that and
        returns its string value directly.

        If using the default standard ServerState node (i=2259):
          0=Running  → "RUNNING"
          1=Failed   → "FAILED"
          2=NoConfig → "NO_CONFIG"
          3=Suspended→ "SUSPENDED"
          4=Shutdown → "SHUTDOWN"
          other      → "UNKNOWN"
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
            self.status = "ERROR"

        return self.status

    def _read_variable(self, variable_name: str, function: str = None) -> str:
        """Read an OPC-UA node by NodeId string."""
        try:
            self._ensure_connected()
            value = self._client.read_node(variable_name)
            return str(value) if value is not None else ""
        except Exception as e:
            self._error(message=f"Read variable '{variable_name}' failed: {e}")
            return ""

    def _write_variable(self, variable_name: str, variable_value: str,
                        function: str = None) -> str:
        """Write a value to an OPC-UA node. Coerces string value to bool/int/float."""
        try:
            self._ensure_connected()
            value = self._coerce_value(variable_value)
            self._client.write_node(variable_name, value)
            return variable_value
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
            raise ConnectionError(
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

    def _err(self, message: str) -> str:
        try:
            self._error(message=message)
        except Exception:
            pass
        return json.dumps({"status": "error", "message": str(message)}, default=str)
