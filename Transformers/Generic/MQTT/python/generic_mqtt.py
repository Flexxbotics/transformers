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
import base64
from typing import List, Optional

from data_models.device import Device
from transformers.abstract_device import AbstractDevice
from protocols.mqtt import MQTT, MqttPublish, MqttSubscribe


class GenericMQTT(AbstractDevice):
    """
    Generic MQTT transformer.

    Connects to any MQTT broker (Mosquitto, HiveMQ, EMQX, AWS IoT Core,
    Azure IoT Hub, and so on) and exchanges messages with any device or
    service that publishes / subscribes on that broker — PLCs, CNCs, IoT
    edge gateways, sensors, SCADA systems, or custom applications.

    Because MQTT is a vendor-neutral publish/subscribe transport, this
    transformer is not tied to any specific machine brand.  The same class
    can talk to a factory-floor sensor network, an OEM telemetry feed, or a
    Sparkplug-B edge node simply by changing the metaData configuration and
    the topics used in commands.

    Publish/subscribe vs. request/response
    --------------------------------------
    MQTT has no native "read this value now" operation.  A client publishes
    to a topic and separately subscribes to topics to receive messages the
    broker pushes to it.  To make MQTT fit the Flexx read/write interface,
    this transformer models a topic read as: subscribe to the topic, then
    wait up to a timeout for a message (retained messages arrive immediately
    on subscribe) and return the most recent matching payload.  See the
    `read` command and `_read_variable`.

    Connection parameters (metaData)
    ---------------------------------
    host                : Broker hostname or IP address (required).
    port                : Broker port (default 1883; 8883 for TLS).
    client_id           : MQTT client identifier (optional; broker assigns
                          one when blank).
    username            : Username for authenticated brokers (optional).
    password            : Password for authenticated brokers (optional).
    keepalive           : Keepalive interval in seconds (default 30).
    tls                 : "true" to enable TLS (default "false").
    ca_certs            : Path to CA certificate bundle for TLS (optional).
    certfile            : Path to client certificate for mutual TLS (optional).
    keyfile             : Path to client private key for mutual TLS (optional).
    insecure_tls        : "true" to skip TLS hostname verification — development
                          only (default "false").
    timeout             : Seconds to wait for the broker CONNACK (default 4.0).
    retry               : Connection retry attempts (default 2).
    retry_interval      : Seconds between retries (default 0.1).
    receive_queue_maxlen: Max buffered inbound messages before oldest are
                          dropped (default 5000).
    decode_payloads     : "true" to decode inbound payloads as UTF-8 text,
                          "false" to return them hex-encoded (default "true").
    default_qos         : Default QoS (0, 1, or 2) for publish/subscribe/read
                          when a command does not specify one (default 0).
    default_retain      : Default retain flag for publishes when a command does
                          not specify one (default "false").
    read_timeout        : Seconds a `read` waits for a matching message before
                          giving up (default 2.0).
    write_topic_template: Python format string mapping a variable name to a
                          publish topic for _write_variable.  Use the {name}
                          placeholder (default "{name}").
    subscribe_on_connect: Comma-separated topics automatically subscribed on
                          every connect (optional).  Useful so interval polls
                          and reads see messages that arrive between calls.
    status_topic        : Topic read for _read_status (optional).  If blank,
                          status returns "NOT_CONFIGURED".
    status_ok_payload   : Substring whose presence in the status payload maps
                          to "RUNNING".  When blank the raw payload is returned
                          as-is (default "").
    interval_topics     : Comma-separated topics drained on interval.  These are
                          subscribed on connect and their latest messages are
                          reported by _read_interval_data.

    MQTT nuances
    ------------
    * QoS levels: 0 = at most once (fire and forget), 1 = at least once
      (may duplicate), 2 = exactly once (slowest, most reliable).  Higher QoS
      only helps if both the publisher and the broker honor it.
    * Retained messages: a broker stores the last retained message per topic
      and delivers it immediately on subscribe — this is what makes `read`
      able to return a value without waiting for a fresh publish.  If a topic
      has no retained message, `read` returns null unless a live message
      arrives within read_timeout.
    * Wildcards: subscriptions accept "+" (single level) and "#" (multi level,
      terminal only).  Publishes must be to concrete topics — no wildcards.
    * The broker connection is held open with a background network thread and
      reused across commands, so subscriptions persist between calls.  A
      dropped connection is re-established automatically on the next command.
    """

    def __init__(self, device: Device):
        super().__init__(device)

        self.meta_data            = device.metaData or {}
        self.host                 = self.meta_data.get("host", "")
        self.port                 = int(self.meta_data.get("port", 1883))
        self.client_id            = self.meta_data.get("client_id", "") or None
        self.username             = self.meta_data.get("username", "") or None
        self.password             = self.meta_data.get("password", "") or None
        self.keepalive            = int(self.meta_data.get("keepalive", 30))
        self.tls                  = (
            self.meta_data.get("tls", "false").strip().lower() == "true"
        )
        self.ca_certs             = self.meta_data.get("ca_certs", "") or None
        self.certfile             = self.meta_data.get("certfile", "") or None
        self.keyfile              = self.meta_data.get("keyfile", "") or None
        self.insecure_tls         = (
            self.meta_data.get("insecure_tls", "false").strip().lower() == "true"
        )
        self.timeout              = float(self.meta_data.get("timeout", 4.0))
        self.retry                = int(self.meta_data.get("retry", 2))
        self.retry_interval       = float(self.meta_data.get("retry_interval", 0.1))
        self.receive_queue_maxlen = int(self.meta_data.get("receive_queue_maxlen", 5000))
        self.decode_payloads      = (
            self.meta_data.get("decode_payloads", "true").strip().lower() == "true"
        )
        self.default_qos          = int(self.meta_data.get("default_qos", 0))
        self.default_retain       = (
            self.meta_data.get("default_retain", "false").strip().lower() == "true"
        )
        self.read_timeout         = float(self.meta_data.get("read_timeout", 2.0))
        self.write_topic_template = self.meta_data.get("write_topic_template", "{name}")
        self.status_topic         = self.meta_data.get("status_topic", "").strip()
        self.status_ok_payload    = self.meta_data.get("status_ok_payload", "").strip()
        self.subscribe_on_connect = [
            t.strip()
            for t in self.meta_data.get("subscribe_on_connect", "").split(",")
            if t.strip()
        ]
        self.interval_topics      = [
            t.strip()
            for t in self.meta_data.get("interval_topics", "").split(",")
            if t.strip()
        ]

        if not self.host:
            raise ValueError("GenericMQTT: metaData must contain 'host'")

        self._client: Optional[MQTT] = None
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
        Execute a named MQTT command.

        Supported commands
        ------------------
        connect
            Open the connection to the broker (and auto-subscribe any topics
            configured in metaData.subscribe_on_connect).
            Returns: {"status": "ok", "connected": true, "broker": "host:port"}

        disconnect
            Close the connection to the broker.
            Returns: {"status": "ok", "connected": false}

        publish
            Publish a message to a topic.
            Args: topic (str, required) — concrete topic (no wildcards)
                  payload (str|number|bool|object, optional) — message body;
                          objects/arrays are JSON-encoded automatically
                  qos (int, optional) — 0, 1, or 2 (default metaData.default_qos)
                  retain (bool, optional) — broker stores as the topic's retained
                          message (default metaData.default_retain)
            Returns: {"status": "ok", "topic": "...", "qos": n, "retain": bool}

        subscribe
            Subscribe to one or more topics so their messages flow into the
            receive buffer.  Wildcards "+" and "#" are allowed.
            Args: topics (str or array) — comma-separated topics or JSON array
                  qos (int, optional) — default metaData.default_qos
            Returns: {"status": "ok", "subscribed": ["...", ...], "qos": n}

        unsubscribe
            Stop receiving messages for one or more topics.
            Args: topics (str or array) — comma-separated topics or JSON array
            Returns: {"status": "ok", "unsubscribed": ["...", ...]}

        read
            Read the latest message from a topic.  Subscribes to the topic (so
            a retained message is delivered immediately), then waits up to
            `timeout` seconds for a matching message and returns the most
            recent one.  This is the common "read a value" convenience.
            Args: topic (str, required) — topic or wildcard filter to read
                  timeout (float, optional) — seconds to wait
                          (default metaData.read_timeout)
                  qos (int, optional) — subscription QoS (default default_qos)
            Returns: {"status": "ok", "topic": "...", "message": {...}|null}
                     message is {"topic","payload","qos","retain","timestamp"}
                     or null when no message arrived within the timeout.

        receive
            Drain buffered inbound messages without subscribing or waiting.
            Returns messages accumulated from prior subscriptions.
            Args: buffer_size (int, optional) — max messages to pop (default 10)
            Returns: {"status": "ok", "messages": [ {...}, ... ]}

        set_last_will
            Configure the Last Will and Testament the broker publishes if this
            client disconnects ungracefully.  Must be set before connect() to
            take effect — call this, then disconnect + connect.
            Args: topic (str, required), payload (str|object, optional),
                  qos (int, optional), retain (bool, optional)
            Returns: {"status": "ok", "will_topic": "..."}
        """
        args = json.loads(command_args) if command_args else {}
        self._info(message=f"Sending command: {command_name}")

        try:
            if command_name == "connect":
                self._connect()
                return json.dumps({
                    "status": "ok",
                    "connected": True,
                    "broker": f"{self.host}:{self.port}",
                })

            if command_name == "disconnect":
                self._disconnect()
                return json.dumps({"status": "ok", "connected": False})

            if command_name == "publish":
                topic = str(args.get("topic", ""))
                if not topic:
                    return self._err("Missing required field: topic")
                payload = args.get("payload", "")
                qos     = int(args.get("qos", self.default_qos))
                retain  = self._as_bool(args.get("retain", self.default_retain))
                self._ensure_connected()
                self._client.publish(topic, payload, qos=qos, retain=retain)
                return json.dumps({
                    "status": "ok",
                    "topic": topic,
                    "qos": qos,
                    "retain": retain,
                })

            if command_name == "subscribe":
                topics = self._parse_topics(args.get("topics", ""))
                if not topics:
                    return self._err("Missing required field: topics")
                qos = int(args.get("qos", self.default_qos))
                self._ensure_connected()
                self._client.subscribe(topics, qos=qos)
                return json.dumps({
                    "status": "ok",
                    "subscribed": topics,
                    "qos": qos,
                })

            if command_name == "unsubscribe":
                topics = self._parse_topics(args.get("topics", ""))
                if not topics:
                    return self._err("Missing required field: topics")
                self._ensure_connected()
                self._client.unsubscribe(topics)
                return json.dumps({"status": "ok", "unsubscribed": topics})

            if command_name == "read":
                topic = str(args.get("topic", ""))
                if not topic:
                    return self._err("Missing required field: topic")
                wait = float(args.get("timeout", self.read_timeout))
                qos  = int(args.get("qos", self.default_qos))
                message = self._read_topic(topic, wait, qos)
                return json.dumps({
                    "status": "ok",
                    "topic": topic,
                    "message": message,
                }, default=str)

            if command_name == "receive":
                buf_size = int(args.get("buffer_size", 10))
                self._ensure_connected()
                raw = self._client.receive(buffer_size=buf_size)
                return json.dumps({
                    "status": "ok",
                    "messages": json.loads(raw),
                }, default=str)

            if command_name == "set_last_will":
                topic = str(args.get("topic", ""))
                if not topic:
                    return self._err("Missing required field: topic")
                payload = args.get("payload", "")
                qos     = int(args.get("qos", self.default_qos))
                retain  = self._as_bool(args.get("retain", self.default_retain))
                self._ensure_connected()
                self._client.set_last_will(topic, payload, qos=qos, retain=retain)
                return json.dumps({"status": "ok", "will_topic": topic})

            return self._err(f"Unknown command: '{command_name}'")

        except Exception as e:
            self._error(message=f"Command '{command_name}' failed: {e}")
            return self._err(str(e))

    def _read_interval_data(self) -> str:
        """
        Periodic poll. Drains the receive buffer and reports the latest message
        seen for each topic in metaData.interval_topics.  Those topics are
        subscribed on connect, so their messages accumulate between polls.
        Falls back to _read_status when no interval_topics are configured.
        """
        if not self.interval_topics:
            return json.dumps({"status": self._read_status()})

        self._ensure_connected()
        latest = {t: None for t in self.interval_topics}
        try:
            raw = self._client.receive(buffer_size=self.receive_queue_maxlen)
            for msg in json.loads(raw):
                for t in self.interval_topics:
                    if self._topic_matches(t, msg.get("topic", "")):
                        latest[t] = msg
        except Exception as e:
            self._error(message=f"Interval read failed: {e}")
            return self._err(str(e))
        return json.dumps({"status": "ok", "values": latest}, default=str)

    def _read_status(self, function: str = None) -> str:
        """
        Read device status by reading metaData.status_topic and evaluating the
        payload against metaData.status_ok_payload.

        If no status_topic is configured, returns "NOT_CONFIGURED".

        When status_ok_payload is set, its presence in the payload maps to
        "RUNNING"; any other payload is returned verbatim.
        When status_ok_payload is blank, the raw payload is returned as-is.
        """
        if not self.status_topic:
            self.status = "NOT_CONFIGURED"
            return self.status

        try:
            message = self._read_topic(self.status_topic, self.read_timeout,
                                       self.default_qos)
            payload = "" if message is None else str(self._payload_of(message))
            if not payload:
                self.status = "UNAVAILABLE"
            elif self.status_ok_payload:
                self.status = "RUNNING" if self.status_ok_payload in payload else payload
            else:
                self.status = payload
        except Exception as e:
            self._error(message=f"Status read failed: {e}")
            self.status = "ERROR"

        return self.status

    def _read_variable(self, variable_name: str, function: str = None) -> str:
        """
        Read the latest payload from the topic named by variable_name.
        Subscribes to the topic and waits up to metaData.read_timeout for a
        message (a retained message arrives immediately).  Returns the payload
        as a string, or "" when nothing arrived.
        """
        try:
            message = self._read_topic(variable_name, self.read_timeout,
                                       self.default_qos)
            if message is None:
                return ""
            return str(self._payload_of(message))
        except Exception as e:
            self._error(message=f"Read variable '{variable_name}' failed: {e}")
            return ""

    def _write_variable(self, variable_name: str, variable_value: str,
                        function: str = None) -> str:
        """
        Publish variable_value to the topic derived from variable_name via
        metaData.write_topic_template.  Published with default_qos and
        default_retain.  Returns the published value.
        """
        try:
            topic = self.write_topic_template.format(name=variable_name)
            self._ensure_connected()
            self._client.publish(topic, variable_value,
                                  qos=self.default_qos, retain=self.default_retain)
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
        self._info(message=f"Connecting to MQTT broker: {self.host}:{self.port}")
        if self._connected and self._client is not None:
            return

        self._client = MQTT(
            host=self.host,
            port=self.port,
            client_id=self.client_id,
            username=self.username,
            password=self.password,
            keepalive=self.keepalive,
            tls=self.tls,
            ca_certs=self.ca_certs,
            certfile=self.certfile,
            keyfile=self.keyfile,
            insecure_tls=self.insecure_tls,
            timeout=self.timeout,
            retry=self.retry,
            retry_interval=self.retry_interval,
            receive_queue_maxlen=self.receive_queue_maxlen,
            decode_payloads=self.decode_payloads,
        )

        rc = self._client.connect()
        if rc != 0:
            self._connected = False
            raise ConnectionError(
                f"GenericMQTT: connect failed (broker={self.host}:{self.port})"
            )

        self._connected = True

        # Re-establish standing subscriptions so reads/interval polls see data.
        if self.subscribe_on_connect:
            self._client.subscribe(self.subscribe_on_connect, qos=self.default_qos)
        if self.interval_topics:
            self._client.subscribe(self.interval_topics, qos=self.default_qos)

        self._info(message=f"Connected to MQTT broker: {self.host}:{self.port}")

    def _disconnect(self) -> None:
        self._info(message=f"Disconnecting from MQTT broker: {self.host}:{self.port}")
        try:
            if self._client is not None:
                self._client.disconnect()
        finally:
            self._client = None
            self._connected = False

    def _ensure_connected(self) -> None:
        if not self._connected or self._client is None:
            self._connect()

    def _read_topic(self, topic: str, timeout: float, qos: int) -> Optional[dict]:
        """
        Subscribe to `topic`, then poll the receive buffer up to `timeout`
        seconds for a message matching the topic filter and return the most
        recent match (or None).  A retained message is delivered by the broker
        immediately on subscribe, so retained values return without waiting.

        Note: draining the shared buffer here also consumes messages for other
        topics; use `subscribe` + `receive` when you need to keep every message.
        """
        self._ensure_connected()
        self._client.subscribe(topic, qos=qos)

        latest: Optional[dict] = None
        deadline = time.time() + max(0.0, timeout)
        # Always make at least one pass so a retained message is not missed
        # even when timeout is 0.
        while True:
            raw = self._client.receive(buffer_size=self.receive_queue_maxlen)
            for msg in json.loads(raw):
                if self._topic_matches(topic, msg.get("topic", "")):
                    latest = msg
            if latest is not None or time.time() >= deadline:
                break
            time.sleep(0.02)
        return latest

    @staticmethod
    def _payload_of(message: dict):
        """Return the decoded payload from a receive() message entry."""
        if "payload" in message:
            return message["payload"]
        return message.get("payload_hex", "")

    @staticmethod
    def _parse_topics(raw) -> List[str]:
        """Accept a comma-separated string or JSON array of topic strings."""
        if isinstance(raw, list):
            return [str(t).strip() for t in raw if str(t).strip()]
        if isinstance(raw, str):
            stripped = raw.strip()
            if stripped.startswith("["):
                try:
                    return [str(t).strip() for t in json.loads(stripped) if str(t).strip()]
                except Exception:
                    pass
            return [t.strip() for t in stripped.split(",") if t.strip()]
        return []

    @staticmethod
    def _topic_matches(topic_filter: str, topic: str) -> bool:
        """
        MQTT topic-filter matching supporting "+" (single level) and "#"
        (multi level, terminal).  A concrete filter matches only itself.
        """
        if topic_filter == topic:
            return True
        f = topic_filter.split("/")
        t = topic.split("/")
        for i, part in enumerate(f):
            if part == "#":
                return True
            if i >= len(t):
                return False
            if part == "+":
                continue
            if part != t[i]:
                return False
        return len(f) == len(t)

    @staticmethod
    def _as_bool(value) -> bool:
        """Coerce metaData/command values to bool ('true'/'1' → True)."""
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in ("true", "1", "yes")

    def _err(self, message: str) -> str:
        try:
            self._error(message=message)
        except Exception:
            pass
        return json.dumps({"status": "error", "message": str(message)}, default=str)
