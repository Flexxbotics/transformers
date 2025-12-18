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
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union

from protocols.abstract_protocol import AbstractProtocol

try:
    # pycomm3 supports EtherNet/IP CIP and Rockwell Logix (ControlLogix/CompactLogix)
    from pycomm3 import LogixDriver  # type: ignore
except Exception as e:  # pragma: no cover
    LogixDriver = None  # type: ignore
    _PYCOMM3_IMPORT_ERROR = e


@dataclass(frozen=True)
class EIPWrite:
    tag: str
    value: Any


class EIPLogix(AbstractProtocol):
    """
    EtherNet/IP (CIP) client for Rockwell Logix PLCs using pycomm3.LogixDriver.

    Pattern notes:
      - connect()/disconnect() follow TCP-style retry loop.
      - send() is mapped to WRITE operations (single or batch).
      - receive() returns the most recent READ/WRITE response payload as JSON str.

    Primary helpers:
      - read_tag(tag) / read_tags([tags])
      - write_tag(tag, value) / write_tags([...])
    """

    def __init__(
        self,
        ip_address: str,
        timeout: float = 2.0,
        retry: int = 2,
        retry_interval: float = 0.1,
        slot: Optional[int] = None,
        auto_connect: bool = True,
    ):
        if LogixDriver is None:  # pragma: no cover
            raise ImportError(f"pycomm3 is not installed or failed to import: {_PYCOMM3_IMPORT_ERROR}")

        super().__init__()
        self.__ip_address = ip_address
        self.__timeout = timeout
        self.__retry = retry
        self.__retry_interval = retry_interval
        self.__attempts = 0
        self.__slot = slot
        self.__auto_connect = auto_connect

        self.__driver: Optional[LogixDriver] = None
        self.__connected = False
        self.__last_payload: Dict[str, Any] = {}

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}({self.__ip_address}, timeout={self.__timeout}, retry={self.__retry})>"

    def __del__(self):
        try:
            self._info(self, f"{self.__repr__}: deleted")
            self.disconnect()
        except Exception:
            pass

    def connect(self) -> int:
        self.__attempts = 0
        last_err: Optional[Exception] = None

        while self.__attempts < self.__retry:
            try:
                path = self.__ip_address if self.__slot is None else f"{self.__ip_address}/{self.__slot}"
                self.__driver = LogixDriver(path, timeout=self.__timeout)
                self.__driver.open()
                self.__connected = True
                self._debug(self, f"{self.__repr__}: connected")
                return 0
            except Exception as e:
                last_err = e
                self.__connected = False
                self._warn(self, message=f"{self.__repr__}: connection failed ({e}). Retrying.")
                self.__attempts += 1
                time.sleep(self.__retry_interval)

        self._error(self, f"{self.__repr__}: unable to connect, max retry limit reached ({last_err})")
        return 1

    def disconnect(self) -> int:
        try:
            if self.__driver is not None:
                self.__driver.close()
            self.__driver = None
            self.__connected = False
            self._debug(self, f"{self.__repr__}: disconnected")
            return 0
        except Exception as e:
            self._error(self, f"{self.__repr__}: disconnect error ({e})")
            return 1

    def send(self, data: Union[bytes, Dict[str, Any], EIPWrite, List[Union[Dict[str, Any], EIPWrite]]]) -> int:
        """
        Generic 'send' = write tag(s).

        Accepts:
          - bytes: JSON payload {"tag": "...", "value": ...} or [{"tag": "...","value":...}, ...]
          - dict: {"tag": "...", "value": ...}
          - EIPWrite(tag, value)
          - list of dict/EIPWrite

        Returns: number of successful writes.
        """
        self._ensure_connected()

        if isinstance(data, (bytes, bytearray)):
            data = json.loads(data.decode("utf-8"))

        writes: List[Tuple[str, Any]] = []
        if isinstance(data, EIPWrite):
            writes.append((data.tag, data.value))
        elif isinstance(data, dict):
            writes.append((str(data["tag"]), data.get("value")))
        elif isinstance(data, list):
            for item in data:
                if isinstance(item, EIPWrite):
                    writes.append((item.tag, item.value))
                elif isinstance(item, dict):
                    writes.append((str(item["tag"]), item.get("value")))
                else:
                    raise TypeError(f"Unsupported write item type: {type(item)}")
        else:
            raise TypeError(f"Unsupported send() payload type: {type(data)}")

        ok = 0
        results = []
        for tag, value in writes:
            try:
                r = self.write_tag(tag, value)
                results.append({"tag": tag, "status": getattr(r, "status", None), "value": value})
                ok += 1 if getattr(r, "status", "Success") in ("Success", True, 0) else 0
            except Exception as e:
                results.append({"tag": tag, "error": str(e)})

        self.__last_payload = {"writes": results}
        return ok

    def receive(self, buffer_size: int = 0) -> str:
        """
        Returns last response payload as JSON.
        buffer_size is ignored (kept for AbstractProtocol compatibility).
        """
        return json.dumps(self.__last_payload, default=str)

    # --------- Helpers ---------

    def read_tag(self, tag: str) -> Any:
        self._ensure_connected()
        assert self.__driver is not None
        r = self.__driver.read(tag)
        payload = {
            "reads": [{"tag": tag, "status": getattr(r, "status", None), "value": getattr(r, "value", None)}]
        }
        self.__last_payload = payload
        return getattr(r, "value", None)

    def read_tags(self, tags: Iterable[str]) -> Dict[str, Any]:
        self._ensure_connected()
        assert self.__driver is not None
        # pycomm3 supports reading multiple tags in one call
        rlist = self.__driver.read(*list(tags))
        out: Dict[str, Any] = {}
        reads_payload = []
        for r in rlist:
            reads_payload.append({"tag": getattr(r, "tag", None), "status": getattr(r, "status", None), "value": getattr(r, "value", None)})
            out[str(getattr(r, "tag", ""))] = getattr(r, "value", None)
        self.__last_payload = {"reads": reads_payload}
        return out

    def write_tag(self, tag: str, value: Any):
        self._ensure_connected()
        assert self.__driver is not None
        return self.__driver.write((tag, value))

    def write_tags(self, writes: Iterable[Tuple[str, Any]]):
        self._ensure_connected()
        assert self.__driver is not None
        return self.__driver.write(*list(writes))

    # --------- Internal ---------

    def _ensure_connected(self) -> None:
        if not self.__connected or self.__driver is None:
            if not self.__auto_connect:
                raise ConnectionError(f"{self.__repr__}: not connected")
            rc = self.connect()
            if rc != 0:
                raise ConnectionError(f"{self.__repr__}: not connected")
