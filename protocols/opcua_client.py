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


class _SubHandler:
    """
    python-opcua subscription handler: called by the library on data changes.
    We push notifications into a bounded deque for receive().
    """

    def __init__(self, queue: Deque[Dict[str, Any]], logger, maxlen: int):
        self._queue = queue
        self._logger = logger
        self._maxlen = maxlen

    def datachange_notification(self, node, val, data):
        try:
            # node is opcua.Node
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
            # bounded queue: deque will drop leftmost when maxlen is hit
            self._queue.append(item)
        except Exception as ex:
            self._logger.error(f"OPCUA subscription handler error: {ex}")


class OPCUA(AbstractProtocol):
    """
    Generic OPC-UA client protocol.

    - connect()/disconnect() behave like other protocols with retry semantics (see TCP.connect()).
    - send() maps to "write" operations for generic use.
    - receive() returns a JSON string of queued subscription notifications (if subscriptions are enabled).

    Prefer using read_node()/write_node()/browse()/subscribe_data_change() for explicit OPC-UA usage.
    """

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
        self.__sub_handler = None
        self.__sub_queue: Deque[Dict[str, Any]] = deque(maxlen=subscription_queue_maxlen)

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}({self.__endpoint}, timeout={self.__timeout}, retry={self.__retry})>"

    def __del__(self):
        try:
            self._info(self, f"{self.__repr__}: deleted")
            self.disconnect()
        except Exception:
            # avoid noisy destructor errors
            pass

    # ----------------------------
    # AbstractProtocol methods
    # ----------------------------

    def connect(self) -> int:
        """
        Returns 0 on success, non-zero on failure (mirrors TCP.connect_ex-style convention).
        """
        self.__attempts = 0
        last_err: Optional[Exception] = None

        while self.__attempts < self.__retry:
            try:
                self.__client = Client(self.__endpoint, timeout=self.__timeout)

                # Optional auth / security
                if self.__username is not None:
                    self.__client.set_user(self.__username)
                    self.__client.set_password(self.__password or "")

                # security_string example (python-opcua):
                # "Basic256Sha256,SignAndEncrypt,cert.der,key.pem"
                if self.__security_string:
                    self.__client.set_security_string(self.__security_string)

                self.__client.connect()
                self.__connected = True
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
            # tear down subscription first
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
            self._debug(self, f"{self.__repr__}: disconnected")
            return 0
        except Exception as e:
            self._error(self, f"{self.__repr__}: disconnect error ({e})")
            return 1

    def send(self, data: Union[OpcuaWrite, Dict[str, Any], List[Union[OpcuaWrite, Dict[str, Any]]], bytes]) -> int:
        """
        Generic 'send' for OPC-UA = write operation(s).

        Accepts:
          - OpcuaWrite(node_id, value)
          - {"node_id": "...", "value": ...}
          - list of either of the above
          - bytes: interpreted as JSON with the same shape

        Returns: number of successful writes.
        """
        self._ensure_connected()

        writes: List[Tuple[str, Any]] = []

        if isinstance(data, (bytes, bytearray)):
            payload = json.loads(data.decode("utf-8"))
            data = payload  # fall through

        if isinstance(data, OpcuaWrite):
            writes.append((data.node_id, data.value))
        elif isinstance(data, dict):
            writes.append((str(data["node_id"]), data.get("value")))
        elif isinstance(data, list):
            for item in data:
                if isinstance(item, OpcuaWrite):
                    writes.append((item.node_id, item.value))
                elif isinstance(item, dict):
                    writes.append((str(item["node_id"]), item.get("value")))
                else:
                    raise TypeError(f"Unsupported write item type: {type(item)}")
        else:
            raise TypeError(f"Unsupported send() payload type: {type(data)}")

        ok = 0
        for node_id, value in writes:
            try:
                self.write_node(node_id=node_id, value=value)
                ok += 1
            except Exception as e:
                self._warn(self, f"{self.__repr__}: write failed for {node_id} ({e})")

        return ok

    def receive(self, buffer_size: int = 1) -> str:
        """
        Receive returns subscription notifications (if subscribe_data_change() was called),
        serialized as JSON.

        buffer_size = max number of queued notifications to return.
        """
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
    # OPC-UA specific helpers
    # ----------------------------

    def read_node(self, node_id: str) -> Any:
        self._ensure_connected()
        assert self.__client is not None
        node = self.__client.get_node(node_id)
        return node.get_value()

    def write_node(self, node_id: str, value: Any) -> None:
        self._ensure_connected()
        assert self.__client is not None
        node = self.__client.get_node(node_id)

        # If it's already a UA Variant/DataValue, pass through.
        if ua is not None and isinstance(value, (ua.Variant, ua.DataValue)):
            node.set_value(value)
        else:
            node.set_value(value)

    def browse(self, node_id: str = "i=85") -> List[Dict[str, Any]]:
        """
        Browse children of a node. Default i=85 = Objects folder.
        """
        self._ensure_connected()
        assert self.__client is not None
        node = self.__client.get_node(node_id)

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
        """
        Create a subscription and monitor one or more nodes for value changes.
        Notifications are pushed into an internal queue, read out via receive().
        """
        self._ensure_connected()
        assert self.__client is not None

        # reset prior sub
        try:
            if self.__sub is not None:
                self.__sub.delete()
        except Exception:
            pass
        self.__sub = None
        self.__sub_handler = None

        handler = _SubHandler(self.__sub_queue, self._logger, self.__sub_queue.maxlen or 0)
        sub = self.__client.create_subscription(publishing_interval_ms, handler)

        if isinstance(node_ids, str):
            node_ids = [node_ids]

        nodes = [self.__client.get_node(nid) for nid in node_ids]
        sub.subscribe_data_change(nodes)

        self.__sub_handler = handler
        self.__sub = sub
        self._debug(self, f"{self.__repr__}: subscribed to {len(nodes)} node(s)")

    # ----------------------------
    # Internal
    # ----------------------------

    def _ensure_connected(self) -> None:
        if not self.__connected or self.__client is None:
            rc = self.connect()
            if rc != 0:
                raise ConnectionError(f"{self.__repr__}: not connected")
