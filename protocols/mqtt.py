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
    import paho.mqtt.client as mqtt  # type: ignore
except Exception as e:  # pragma: no cover
    mqtt = None  # type: ignore
    _PAHO_IMPORT_ERROR = e


@dataclass(frozen=True)
class MqttPublish:
    topic: str
    payload: Union[str, bytes, Dict[str, Any], List[Any]]
    qos: int = 0
    retain: bool = False


@dataclass(frozen=True)
class MqttSubscribe:
    topic: str
    qos: int = 0


class MQTT(AbstractProtocol):
    """
    Generic MQTT client using paho-mqtt.

    Pattern:
      - connect()/disconnect(): TCP-style retry loop + background network thread
      - send(): publish operation(s)
      - receive(): returns queued subscribed messages as JSON (or raw bytes if desired)

    Helpers:
      - publish(topic, payload, qos=0, retain=False)
      - subscribe(topic(s), qos=0)
      - unsubscribe(topic(s))
      - set_last_will(...)
    """

    def __init__(
        self,
        host: str,
        port: int = 1883,
        client_id: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        keepalive: int = 30,
        tls: bool = False,
        ca_certs: Optional[str] = None,
        certfile: Optional[str] = None,
        keyfile: Optional[str] = None,
        insecure_tls: bool = False,
        timeout: float = 4.0,
        retry: int = 2,
        retry_interval: float = 0.1,
        # receive queue:
        receive_queue_maxlen: int = 5000,
        # if True, receive() returns payload decoded as utf-8 when possible
        decode_payloads: bool = True,
    ):
        if mqtt is None:  # pragma: no cover
            raise ImportError(f"paho-mqtt is not installed or failed to import: {_PAHO_IMPORT_ERROR}")

        super().__init__()
        self.__host = host
        self.__port = int(port)
        self.__keepalive = int(keepalive)
        self.__timeout = float(timeout)
        self.__retry = int(retry)
        self.__retry_interval = float(retry_interval)
        self.__attempts = 0
        self.__decode_payloads = bool(decode_payloads)

        self.__username = username
        self.__password = password

        self.__tls = bool(tls)
        self.__ca_certs = ca_certs
        self.__certfile = certfile
        self.__keyfile = keyfile
        self.__insecure_tls = bool(insecure_tls)

        # paho client
        self.__client = mqtt.Client(client_id=client_id)  # type: ignore[arg-type]
        if self.__username is not None:
            self.__client.username_pw_set(self.__username, self.__password)

        if self.__tls:
            # If you pass no cert paths, paho will use default CA certs from OS if supported.
            self.__client.tls_set(
                ca_certs=self.__ca_certs,
                certfile=self.__certfile,
                keyfile=self.__keyfile,
            )
            if self.__insecure_tls:
                self.__client.tls_insecure_set(True)

        # callbacks
        self.__client.on_connect = self._on_connect
        self.__client.on_disconnect = self._on_disconnect
        self.__client.on_message = self._on_message

        self.__connected: bool = False
        self.__last_payload: Dict[str, Any] = {}

        self.__rx_queue: Deque[Dict[str, Any]] = deque(maxlen=receive_queue_maxlen)

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}({self.__host}:{self.__port}, retry={self.__retry})>"

    def __del__(self):
        try:
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
                # connect is synchronous; loop_start spins background networking
                self.__client.connect(self.__host, self.__port, self.__keepalive)
                self.__client.loop_start()

                # Wait briefly for on_connect to flip the flag
                deadline = time.time() + self.__timeout
                while time.time() < deadline and not self.__connected:
                    time.sleep(0.01)

                if self.__connected:
                    self._debug(self, f"{self.__repr__}: connected")
                    return 0

                raise TimeoutError("MQTT connect timed out waiting for CONNACK")

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
                self.__client.loop_stop()
            except Exception:
                pass
            try:
                self.__client.disconnect()
            except Exception:
                pass

            self.__connected = False
            self._debug(self, f"{self.__repr__}: disconnected")
            return 0

        except Exception as e:
            self._error(self, f"{self.__repr__}: disconnect error ({e})")
            return 1

    def send(
        self,
        data: Union[
            bytes,
            str,
            Dict[str, Any],
            MqttPublish,
            List[Union[MqttPublish, Dict[str, Any]]],
        ],
    ) -> int:
        """
        Generic 'send' = publish operation(s).

        Accepts:
          - MqttPublish(topic, payload, qos=0, retain=False)
          - dict: {"topic": "...", "payload": ..., "qos": 0, "retain": false}
          - list of the above
          - bytes/str: interpreted as JSON dict or list (same shapes)

        Returns: number of successful publish calls (enqueued to client).
        """
        self._ensure_connected()

        if isinstance(data, (bytes, bytearray)):
            data = data.decode("utf-8")
        if isinstance(data, str):
            data = json.loads(data)

        publishes: List[MqttPublish] = []
        if isinstance(data, MqttPublish):
            publishes.append(data)
        elif isinstance(data, dict):
            publishes.append(
                MqttPublish(
                    topic=str(data["topic"]),
                    payload=data.get("payload", ""),
                    qos=int(data.get("qos", 0)),
                    retain=bool(data.get("retain", False)),
                )
            )
        elif isinstance(data, list):
            for item in data:
                if isinstance(item, MqttPublish):
                    publishes.append(item)
                elif isinstance(item, dict):
                    publishes.append(
                        MqttPublish(
                            topic=str(item["topic"]),
                            payload=item.get("payload", ""),
                            qos=int(item.get("qos", 0)),
                            retain=bool(item.get("retain", False)),
                        )
                    )
                else:
                    raise TypeError(f"Unsupported publish item type: {type(item)}")
        else:
            raise TypeError(f"Unsupported send() payload type: {type(data)}")

        ok = 0
        results = []
        for p in publishes:
            try:
                payload_bytes = self._encode_payload(p.payload)
                info = self.__client.publish(p.topic, payload_bytes, qos=p.qos, retain=p.retain)
                results.append({"topic": p.topic, "mid": getattr(info, "mid", None), "rc": getattr(info, "rc", None)})
                ok += 1
            except Exception as e:
                results.append({"topic": p.topic, "error": str(e)})

        self.__last_payload = {"publishes": results}
        return ok

    def receive(self, buffer_size: int = 1) -> str:
        """
        Pops up to buffer_size messages from the RX queue and returns JSON list.

        Each entry: {"topic": "...", "payload": <str|hex>, "qos": int, "retain": bool, "timestamp": float}
        """
        self._ensure_connected()

        n = max(1, int(buffer_size))
        items: List[Dict[str, Any]] = []
        for _ in range(n):
            try:
                items.append(self.__rx_queue.popleft())
            except IndexError:
                break

        return json.dumps(items, default=str)

    # ----------------------------
    # MQTT helpers
    # ----------------------------

    def publish(self, topic: str, payload: Any, qos: int = 0, retain: bool = False) -> None:
        self.send(MqttPublish(topic=topic, payload=payload, qos=qos, retain=retain))

    def subscribe(self, topics: Union[str, Iterable[Union[str, MqttSubscribe]]], qos: int = 0) -> None:
        self._ensure_connected()

        if isinstance(topics, str):
            topics_list: List[Tuple[str, int]] = [(topics, int(qos))]
        else:
            topics_list = []
            for t in topics:
                if isinstance(t, MqttSubscribe):
                    topics_list.append((t.topic, int(t.qos)))
                else:
                    topics_list.append((str(t), int(qos)))

        # paho allows list of (topic, qos)
        self.__client.subscribe(topics_list)
        self._debug(self, f"{self.__repr__}: subscribed to {len(topics_list)} topic(s)")

    def unsubscribe(self, topics: Union[str, Iterable[str]]) -> None:
        self._ensure_connected()
        if isinstance(topics, str):
            topics = [topics]
        self.__client.unsubscribe(list(topics))

    def set_last_will(self, topic: str, payload: Any, qos: int = 0, retain: bool = False) -> None:
        payload_bytes = self._encode_payload(payload)
        self.__client.will_set(topic, payload_bytes, qos=int(qos), retain=bool(retain))

    # ----------------------------
    # Callbacks
    # ----------------------------

    def _on_connect(self, client, userdata, flags, rc, properties=None):
        # rc == 0 => success
        self.__connected = (rc == 0)
        self.__last_payload = {"event": "connect", "rc": rc, "flags": flags}
        if rc != 0:
            self._warn(self, f"{self.__repr__}: on_connect rc={rc}")

    def _on_disconnect(self, client, userdata, rc, properties=None):
        self.__connected = False
        self.__last_payload = {"event": "disconnect", "rc": rc}

    def _on_message(self, client, userdata, msg):
        try:
            payload = msg.payload
            entry: Dict[str, Any] = {
                "topic": msg.topic,
                "qos": int(getattr(msg, "qos", 0)),
                "retain": bool(getattr(msg, "retain", False)),
                "timestamp": time.time(),
            }

            if self.__decode_payloads:
                try:
                    entry["payload"] = payload.decode("utf-8")
                except Exception:
                    entry["payload_hex"] = payload.hex()
            else:
                entry["payload_hex"] = payload.hex()

            self.__rx_queue.append(entry)
        except Exception as e:
            self._warn(self, f"{self.__repr__}: on_message error ({e})")

    # ----------------------------
    # Internal
    # ----------------------------

    def _ensure_connected(self) -> None:
        if not self.__connected:
            rc = self.connect()
            if rc != 0:
                raise ConnectionError(f"{self.__repr__}: not connected")

    @staticmethod
    def _encode_payload(payload: Any) -> bytes:
        if payload is None:
            return b""
        if isinstance(payload, (bytes, bytearray)):
            return bytes(payload)
        if isinstance(payload, str):
            return payload.encode("utf-8")
        # JSON-encode dict/list/number/bool
        return json.dumps(payload).encode("utf-8")
