from __future__ import annotations

import json
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Deque, Dict, Iterable, List, Optional, Tuple, Union

from protocols.abstract_protocol import AbstractProtocol

try:
    # python-opcua package
    from opcua import Client, ua  # type: ignore
except Exception as e:  # pragma: no cover
    Client = None  # type: ignore
    ua = None  # type: ignore
    _OPCUA_IMPORT_ERROR = e


@dataclass(frozen=True)
class OpcuaWrite:
    node_id: str
    value: Any
    # Optional explicit typing for correct OPC-UA writes.
    # Accepts ua.VariantType (enum) or int.
    variant_type: Optional[Any] = None  # Any to avoid mypy pain if ua isn't available at type-check time.


class _SubHandler:
    """
    python-opcua subscription handler: called by the library on data changes.
    We push notifications into a bounded deque for receive().
    """

    def __init__(self, queue: Deque[Dict[str, Any]], logger, maxlen: int):
        self._queue = queue
        self._logger = logger
        self._maxlen = maxlen
        self._dropped = 0

    @property
    def dropped_count(self) -> int:
        return self._dropped

    def datachange_notification(self, node, val, data):
        try:
            item = {
                "node_id": str(node.nodeid),
                "value": val,
                "source_timestamp": getattr(data.monitored_item.Value, "SourceTimestamp", None)
                if hasattr(data, "monitored_item") and hasattr(data.monitored_item, "Value")
                else None,
                "server_timestamp": getattr(data.monitored_item.Value, "ServerTimestamp", None)
                if hasattr(data, "monitored_item") and hasattr(data.monitored_item, "Value")
                else None,
            }

            # Track drops (deque drops leftmost when maxlen is hit)
            if self._queue.maxlen is not None and len(self._queue) >= self._queue.maxlen:
                self._dropped += 1

            self._queue.append(item)
        except Exception as ex:
            self._logger.error(f"OPCUA subscription handler error: {ex}")


class OPCUA(AbstractProtocol):
    """
    Generic OPC-UA client protocol.

    Enhancements added:
      - Health check + auto-reconnect
      - Data typing / write correctness (optional VariantType)
      - Batch reading/writing (+ lightweight node cache)
    """

    # Common node for "is server alive" checks:
    # ServerStatus/CurrentTime = i=2258 (standard)
    _HEALTHCHECK_NODE_ID = "i=2258"

    def __init__(
        self,
        endpoint: str,
        timeout: float = 4.0,
        retry: int = 2,
        retry_interval: float = 0.1,
        username: Optional[str] = None,
        password: Optional[str] = None,
        security_string: Optional[str] = None,
        # subscription queue settings
        subscription_queue_maxlen: int = 5000,
        # health check behavior
        health_check_node_id: str = _HEALTHCHECK_NODE_ID,
        health_check_every_s: float = 5.0,
        op_retry_on_disconnect: int = 1,
    ):
        if Client is None:  # pragma: no cover
            raise ImportError(
                f"python-opcua is not installed or failed to import: {_OPCUA_IMPORT_ERROR}"
            )

        super().__init__()
        self.__endpoint = endpoint
        self.__timeout = timeout
        self.__retry = retry
        self.__retry_interval = retry_interval
        self.__attempts = 0

        self.__username = username
        self.__password = password
        self.__security_string = security_string

        self.__client: Optional[Client] = None
        self.__connected: bool = False

        # subscription state
        self.__sub = None
        self.__sub_handler: Optional[_SubHandler] = None
        self.__sub_queue: Deque[Dict[str, Any]] = deque(maxlen=subscription_queue_maxlen)

        # Remember desired subscription so we can re-create it after reconnect
        self.__desired_sub_node_ids: List[str] = []
        self.__desired_sub_pub_interval_ms: int = 250

        # Health check
        self.__health_node_id = health_check_node_id
        self.__health_every_s = float(health_check_every_s)
        self.__last_health_ok_ts: float = 0.0

        # Auto-retry per operation on disconnect-ish failures
        self.__op_retry_on_disconnect = int(op_retry_on_disconnect)

        # Node cache (avoids re-parsing NodeId and repeated object creation)
        self.__node_cache: Dict[str, Any] = {}

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}({self.__endpoint}, timeout={self.__timeout}, retry={self.__retry})>"

    def __del__(self):
        try:
            self._info(self, f"{self.__repr__}: deleted")
            self.disconnect()
        except Exception:
            pass

    # ----------------------------
    # AbstractProtocol methods
    # ----------------------------

    def connect(self) -> int:
        self.__attempts = 0
        last_err: Optional[Exception] = None

        while self.__attempts < self.__retry:
            try:
                self.__client = Client(self.__endpoint, timeout=self.__timeout)

                if self.__username is not None:
                    self.__client.set_user(self.__username)
                    self.__client.set_password(self.__password or "")

                if self.__security_string:
                    self.__client.set_security_string(self.__security_string)

                self.__client.connect()
                self.__connected = True
                self.__last_health_ok_ts = time.time()

                # clear cache on new session
                self.__node_cache.clear()

                # re-subscribe if needed
                if self.__desired_sub_node_ids:
                    try:
                        self._resubscribe()
                    except Exception as e:
                        self._warn(self, f"{self.__repr__}: reconnect resubscribe failed ({e})")

                self._debug(self, f"{self.__repr__}: connected")
                return 0

            except Exception as e:
                last_err = e
                self.__connected = False
                self._warn(self, f"{self.__repr__}: connect failed ({e}). Retrying...")
                self.__attempts += 1
                time.sleep(self.__retry_interval)

        self._error(self, f"{self.__repr__}: unable to connect, max retry limit reached ({last_err})")
        return 1

    def disconnect(self) -> int:
        try:
            try:
                if self.__sub is not None:
                    self.__sub.delete()
            except Exception:
                pass
            self.__sub = None
            self.__sub_handler = None

            if self.__client is not None:
                self.__client.disconnect()

            self.__connected = False
            self.__node_cache.clear()

            self._debug(self, f"{self.__repr__}: disconnected")
            return 0
        except Exception as e:
            self._error(self, f"{self.__repr__}: disconnect error ({e})")
            return 1

    def send(
        self,
        data: Union[
            OpcuaWrite,
            Dict[str, Any],
            List[Union[OpcuaWrite, Dict[str, Any]]],
            bytes,
        ],
    ) -> int:
        """
        Generic 'send' for OPC-UA = write operation(s).

        Accepts:
          - OpcuaWrite(node_id, value, variant_type=None)
          - {"node_id": "...", "value": ..., "variant_type": ...}
          - list of either of the above
          - bytes: interpreted as JSON with the same shape

        Returns: number of successful writes.
        """
        self._ensure_connected()

        writes: List[Tuple[str, Any, Optional[Any]]] = []

        if isinstance(data, (bytes, bytearray)):
            payload = json.loads(data.decode("utf-8"))
            data = payload

        if isinstance(data, OpcuaWrite):
            writes.append((data.node_id, data.value, data.variant_type))
        elif isinstance(data, dict):
            writes.append((str(data["node_id"]), data.get("value"), data.get("variant_type")))
        elif isinstance(data, list):
            for item in data:
                if isinstance(item, OpcuaWrite):
                    writes.append((item.node_id, item.value, item.variant_type))
                elif isinstance(item, dict):
                    writes.append((str(item["node_id"]), item.get("value"), item.get("variant_type")))
                else:
                    raise TypeError(f"Unsupported write item type: {type(item)}")
        else:
            raise TypeError(f"Unsupported send() payload type: {type(data)}")

        # Batch write (prefers server-side multi-write if available)
        results = self.write_nodes(writes)
        return sum(1 for ok, _err in results.values() if ok)

    def receive(self, buffer_size: int = 1) -> str:
        self._ensure_connected()

        n = max(1, int(buffer_size))
        items: List[Dict[str, Any]] = []
        for _ in range(n):
            try:
                items.append(self.__sub_queue.popleft())
            except IndexError:
                break

        return json.dumps(items, default=str)

    # ----------------------------
    # Health check + auto-reconnect
    # ----------------------------

    def health_check(self, force: bool = False) -> bool:
        """
        Returns True if server appears reachable. Uses a standard read of ServerStatus/CurrentTime by default.

        If force=False, rate-limits health checks to health_check_every_s.
        """
        if not self.__connected or self.__client is None:
            return False

        now = time.time()
        if not force and self.__health_every_s > 0 and (now - self.__last_health_ok_ts) < self.__health_every_s:
            return True

        try:
            # Do a tiny read; if it fails, treat as unhealthy
            _ = self.read_node(self.__health_node_id, _internal_call=True)
            self.__last_health_ok_ts = now
            return True
        except Exception:
            return False

    def _reconnect(self) -> None:
        """
        Force a disconnect + connect. Raises ConnectionError if unable.
        """
        try:
            self.disconnect()
        except Exception:
            pass

        rc = self.connect()
        if rc != 0:
            raise ConnectionError(f"{self.__repr__}: reconnect failed")

    def _call_with_reconnect(self, fn, *args, **kwargs):
        """
        Execute an OPC-UA operation; if it fails in a likely-connection-related way,
        attempt reconnect and retry (up to op_retry_on_disconnect).
        """
        attempts = 0
        last_err: Optional[Exception] = None

        while attempts <= self.__op_retry_on_disconnect:
            try:
                self._ensure_connected()
                return fn(*args, **kwargs)
            except Exception as e:
                last_err = e
                attempts += 1
                # Best-effort heuristic: try reconnect once; if it still fails, re-raise
                try:
                    self._warn(self, f"{self.__repr__}: op failed ({e}); reconnecting and retrying...")
                    self._reconnect()
                except Exception:
                    break

        raise last_err if last_err else RuntimeError("OPC-UA operation failed")

    # ----------------------------
    # OPC-UA specific helpers
    # ----------------------------

    def _get_node(self, node_id: str):
        self._ensure_connected()
        assert self.__client is not None
        node_id = str(node_id)
        n = self.__node_cache.get(node_id)
        if n is None:
            n = self.__client.get_node(node_id)
            self.__node_cache[node_id] = n
        return n

    def read_node(self, node_id: str, _internal_call: bool = False) -> Any:
        """
        Read a single node value.

        _internal_call: avoids recursive health-check calls from health_check().
        """
        def _op():
            node = self._get_node(node_id)
            return node.get_value()

        if _internal_call:
            return _op()
        return self._call_with_reconnect(_op)

    def write_node(self, node_id: str, value: Any, variant_type: Optional[Any] = None) -> None:
        """
        Write a single node value.

        If variant_type is provided (ua.VariantType or int), writes a typed Variant for correctness.
        """
        def _op():
            node = self._get_node(node_id)

            # Pass-through for UA objects
            if ua is not None and isinstance(value, (ua.Variant, ua.DataValue)):
                node.set_value(value)
                return

            if variant_type is not None and ua is not None:
                # variant_type can be ua.VariantType enum or int
                node.set_value(ua.Variant(value, variant_type))
            else:
                node.set_value(value)

        return self._call_with_reconnect(_op)

    # ----------------------------
    # Batch read / write
    # ----------------------------

    def read_nodes(self, node_ids: Iterable[str]) -> Dict[str, Any]:
        """
        Batch read. Returns {node_id: value OR Exception}.

        Prefers client.get_values(nodes) if available; falls back to per-node reads.
        """
        node_ids_list = [str(n) for n in node_ids]

        def _op():
            assert self.__client is not None
            nodes = [self._get_node(nid) for nid in node_ids_list]

            # Prefer library batch read if it exists
            if hasattr(self.__client, "get_values"):
                try:
                    values = self.__client.get_values(nodes)  # type: ignore[attr-defined]
                    return {nid: val for nid, val in zip(node_ids_list, values)}
                except Exception:
                    # fall back to per-node
                    pass

            out: Dict[str, Any] = {}
            for nid in node_ids_list:
                try:
                    out[nid] = self._get_node(nid).get_value()
                except Exception as e:
                    out[nid] = e
            return out

        return self._call_with_reconnect(_op)

    def write_nodes(
        self,
        writes: Union[
            Iterable[OpcuaWrite],
            Iterable[Tuple[str, Any]],
            Iterable[Tuple[str, Any, Optional[Any]]],
        ],
    ) -> Dict[str, Tuple[bool, Optional[str]]]:
        """
        Batch write. Returns {node_id: (ok, error_str)}.

        Prefers client.set_values(nodes, values) if available *and* no per-item variant types are requested.
        Falls back to per-node writes otherwise.
        """
        # Normalize
        normalized: List[Tuple[str, Any, Optional[Any]]] = []
        for w in writes:
            if isinstance(w, OpcuaWrite):
                normalized.append((w.node_id, w.value, w.variant_type))
            elif isinstance(w, tuple):
                if len(w) == 2:
                    normalized.append((str(w[0]), w[1], None))
                elif len(w) == 3:
                    normalized.append((str(w[0]), w[1], w[2]))
                else:
                    raise TypeError(f"Unsupported write tuple length: {len(w)}")
            else:
                raise TypeError(f"Unsupported write item type: {type(w)}")

        node_ids = [nid for nid, _val, _vt in normalized]

        def _op():
            assert self.__client is not None
            results: Dict[str, Tuple[bool, Optional[str]]] = {}

            # If no variant types are used, try a true batch set_values (if present)
            has_typed = any(vt is not None for _nid, _val, vt in normalized)
            if not has_typed and hasattr(self.__client, "set_values"):
                try:
                    nodes = [self._get_node(nid) for nid in node_ids]
                    values = [val for _nid, val, _vt in normalized]
                    self.__client.set_values(nodes, values)  # type: ignore[attr-defined]
                    for nid in node_ids:
                        results[nid] = (True, None)
                    return results
                except Exception:
                    # fall back to per-node
                    pass

            # Per-item writes (supports typed Variants)
            for nid, val, vt in normalized:
                try:
                    node = self._get_node(nid)

                    if ua is not None and isinstance(val, (ua.Variant, ua.DataValue)):
                        node.set_value(val)
                    elif vt is not None and ua is not None:
                        node.set_value(ua.Variant(val, vt))
                    else:
                        node.set_value(val)

                    results[nid] = (True, None)
                except Exception as e:
                    results[nid] = (False, str(e))

            return results

        return self._call_with_reconnect(_op)

    def browse(self, node_id: str = "i=85") -> List[Dict[str, Any]]:
        self._ensure_connected()
        assert self.__client is not None
        node = self._get_node(node_id)

        out: List[Dict[str, Any]] = []
        for child in node.get_children():
            try:
                out.append(
                    {
                        "node_id": str(child.nodeid),
                        "browse_name": str(child.get_browse_name()),
                        "display_name": str(child.get_display_name().Text),
                    }
                )
            except Exception:
                out.append({"node_id": str(child.nodeid)})
        return out

    def subscribe_data_change(
        self,
        node_ids: Union[str, Iterable[str]],
        publishing_interval_ms: int = 250,
    ) -> None:
        self._ensure_connected()
        assert self.__client is not None

        if isinstance(node_ids, str):
            node_ids_list = [node_ids]
        else:
            node_ids_list = [str(n) for n in node_ids]

        # remember desired subscription for reconnect
        self.__desired_sub_node_ids = node_ids_list
        self.__desired_sub_pub_interval_ms = int(publishing_interval_ms)

        def _op():
            # reset prior sub
            try:
                if self.__sub is not None:
                    self.__sub.delete()
            except Exception:
                pass
            self.__sub = None
            self.__sub_handler = None

            handler = _SubHandler(self.__sub_queue, self._logger, self.__sub_queue.maxlen or 0)
            sub = self.__client.create_subscription(self.__desired_sub_pub_interval_ms, handler)

            nodes = [self._get_node(nid) for nid in self.__desired_sub_node_ids]
            sub.subscribe_data_change(nodes)

            self.__sub_handler = handler
            self.__sub = sub
            self._debug(self, f"{self.__repr__}: subscribed to {len(nodes)} node(s)")

        self._call_with_reconnect(_op)

    def _resubscribe(self) -> None:
        """
        Internal: recreate the last requested subscription after reconnect.
        """
        if not self.__desired_sub_node_ids:
            return
        self.subscribe_data_change(self.__desired_sub_node_ids, self.__desired_sub_pub_interval_ms)

    # ----------------------------
    # Internal
    # ----------------------------

    def _ensure_connected(self) -> None:
        if not self.__connected or self.__client is None:
            rc = self.connect()
            if rc != 0:
                raise ConnectionError(f"{self.__repr__}: not connected")
            return

        # if connected, optionally verify health at a low rate
        if not self.health_check(force=False):
            self._warn(self, f"{self.__repr__}: health check failed; reconnecting...")
            self._reconnect()

    # Optional helper for visibility
    def get_subscription_dropped_count(self) -> int:
        return self.__sub_handler.dropped_count if self.__sub_handler else 0
