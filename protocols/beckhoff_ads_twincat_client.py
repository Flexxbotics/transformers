"""
    Copyright 2025 Flexxbotics, Inc.

    Licensed under the Apache License, Version 2.0 (the "License");
    you may not use this file except in compliance with the License.
    You may obtain a copy of the License at

        http://www.apache.org/licenses/LICENSE-2.0
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Deque, Dict, List, Optional, Union
from collections import deque

from protocols.abstract_protocol import AbstractProtocol

try:
    import pyads  # type: ignore
except Exception as e:  # pragma: no cover
    pyads = None  # type: ignore
    _PYADS_IMPORT_ERROR = e


@dataclass(frozen=True)
class ADSWrite:
    symbol: str
    value: Any
    plc_type: Optional[int] = None  # pyads.PLCTYPE_*


class ADS(AbstractProtocol):
    """
    Beckhoff ADS / TwinCAT client using pyads.

    - Client only (initiates connections)
    - send() => write_by_name
    - receive() => queued notifications or last payload

    Helpers:
      - read(symbol, plc_type=None)
      - write(symbol, value, plc_type=None)
      - add_notification(...)
    """

    def __init__(
        self,
        ams_net_id: str,
        ams_port: int,
        ip_address: Optional[str] = None,
        timeout: float = 2.0,
        retry: int = 2,
        retry_interval: float = 0.1,
        auto_connect: bool = True,
        notification_queue_maxlen: int = 5000,
    ):
        if pyads is None:  # pragma: no cover
            raise ImportError(
                f"pyads is not installed or failed to import: {_PYADS_IMPORT_ERROR}"
            )

        super().__init__()
        self.__ams_net_id = ams_net_id
        self.__ams_port = int(ams_port)
        self.__ip_address = ip_address
        self.__timeout = timeout
        self.__retry = retry
        self.__retry_interval = retry_interval
        self.__attempts = 0
        self.__auto_connect = auto_connect

        self.__conn: Optional[pyads.Connection] = None
        self.__connected = False

        self.__last_payload: Dict[str, Any] = {}
        self.__notify_queue: Deque[Dict[str, Any]] = deque(maxlen=notification_queue_maxlen)
        self.__notify_handles: List[int] = []

    def __repr__(self) -> str:
        return (
            f"<{self.__class__.__name__}"
            f"({self.__ams_net_id}:{self.__ams_port}, timeout={self.__timeout}, retry={self.__retry})>"
        )

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
                self.__conn = pyads.Connection(
                    self.__ams_net_id, self.__ams_port, self.__ip_address
                )
                try:
                    self.__conn.set_timeout(int(self.__timeout * 1000))
                except Exception:
                    pass

                self.__conn.open()
                self.__connected = True
                self._debug(self, f"{self.__repr__}: connected")
                return 0

            except Exception as e:
                last_err = e
                self.__connected = False
                self._warn(self, f"{self.__repr__}: connection failed ({e}). Retrying.")
                self.__attempts += 1
                time.sleep(self.__retry_interval)

        self._error(self, f"{self.__repr__}: unable to connect ({last_err})")
        return 1

    def disconnect(self) -> int:
        try:
            if self.__conn is not None:
                for h in list(self.__notify_handles):
                    try:
                        self.__conn.del_device_notification(h)
                    except Exception:
                        pass
                self.__notify_handles.clear()

                try:
                    self.__conn.close()
                except Exception:
                    pass

            self.__conn = None
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
            Dict[str, Any],
            ADSWrite,
            List[Union[Dict[str, Any], ADSWrite]],
        ],
    ) -> int:
        """
        Generic 'send' = write_by_name(s).
        """
        self._ensure_connected()

        if isinstance(data, (bytes, bytearray)):
            data = json.loads(data.decode("utf-8"))

        writes: List[ADSWrite] = []
        if isinstance(data, ADSWrite):
            writes.append(data)
        elif isinstance(data, dict):
            writes.append(
                ADSWrite(
                    symbol=str(data["symbol"]),
                    value=data.get("value"),
                    plc_type=data.get("plc_type"),
                )
            )
        elif isinstance(data, list):
            for item in data:
                if isinstance(item, ADSWrite):
                    writes.append(item)
                elif isinstance(item, dict):
                    writes.append(
                        ADSWrite(
                            symbol=str(item["symbol"]),
                            value=item.get("value"),
                            plc_type=item.get("plc_type"),
                        )
                    )
                else:
                    raise TypeError(f"Unsupported write item type: {type(item)}")
        else:
            raise TypeError(f"Unsupported send() payload type: {type(data)}")

        ok = 0
        results = []
        for w in writes:
            try:
                self.write(w.symbol, w.value, plc_type=w.plc_type)
                results.append({"symbol": w.symbol, "status": "Success"})
                ok += 1
            except Exception as e:
                results.append({"symbol": w.symbol, "error": str(e)})

        self.__last_payload = {"writes": results}
        return ok

    def receive(self, buffer_size: int = 1) -> str:
        """
        Returns queued notification events (if any), otherwise last payload.
        """
        n = max(1, int(buffer_size))
        items: List[Dict[str, Any]] = []
        for _ in range(n):
            try:
                items.append(self.__notify_queue.popleft())
            except IndexError:
                break

        if items:
            return json.dumps(items, default=str)
        return json.dumps(self.__last_payload, default=str)

    # ----------------------------
    # ADS helpers
    # ----------------------------

    def read(self, symbol: str, plc_type: Optional[int] = None) -> Any:
        self._ensure_connected()
        assert self.__conn is not None

        if plc_type is None:
            value = self.__conn.read_by_name(symbol)
        else:
            value = self.__conn.read_by_name(symbol, plc_type)

        self.__last_payload = {"reads": [{"symbol": symbol, "value": value}]}
        return value

    def write(self, symbol: str, value: Any, plc_type: Optional[int] = None) -> None:
        self._ensure_connected()
        assert self.__conn is not None

        if plc_type is None:
            self.__conn.write_by_name(symbol, value)
        else:
            self.__conn.write_by_name(symbol, value, plc_type)

    def add_notification(
        self,
        symbol: str,
        plc_type: Optional[int] = None,
        cycle_time_ms: int = 100,
        max_delay_ms: int = 0,
    ) -> int:
        """
        Subscribe to symbol updates.
        """
        self._ensure_connected()
        assert self.__conn is not None

        def _cb(handle, name, timestamp, value):
            self.__notify_queue.append(
                {
                    "handle": handle,
                    "symbol": name,
                    "timestamp": timestamp,
                    "value": value,
                }
            )

        attrib = pyads.NotificationAttrib(
            length=pyads.size_of(plc_type) if plc_type else 0,
            trans_mode=pyads.ADSTRANS_SERVERONCHA,
            max_delay=max_delay_ms,
            cycle_time=cycle_time_ms,
        )

        if plc_type:
            h = self.__conn.add_device_notification(symbol, attrib, _cb, plc_type=plc_type)
        else:
            h = self.__conn.add_device_notification(symbol, attrib, _cb)

        self.__notify_handles.append(h)
        return h

    # ----------------------------
    # Internal
    # ----------------------------

    def _ensure_connected(self) -> None:
        if not self.__connected or self.__conn is None:
            if not self.__auto_connect:
                raise ConnectionError(f"{self.__repr__}: not connected")
            rc = self.connect()
            if rc != 0:
                raise ConnectionError(f"{self.__repr__}: not connected")
