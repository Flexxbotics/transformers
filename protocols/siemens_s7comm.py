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
from typing import Any, Dict, List, Optional, Tuple, Union

from protocols.abstract_protocol import AbstractProtocol

try:
    import snap7  # type: ignore
    from snap7.client import Client as Snap7Client  # type: ignore
except Exception as e:  # pragma: no cover
    snap7 = None  # type: ignore
    Snap7Client = None  # type: ignore
    _SNAP7_IMPORT_ERROR = e


@dataclass(frozen=True)
class S7DbRead:
    db_number: int
    start: int
    size: int


@dataclass(frozen=True)
class S7DbWrite:
    db_number: int
    start: int
    data: bytes


class S7(AbstractProtocol):
    """
    Siemens S7 client (S7comm) using python-snap7.

    Pattern notes:
      - connect()/disconnect() follow TCP-style retry loop.
      - send() maps to DB write operations.
      - receive() returns last payload as JSON (typically last read/write result).

    Primary helpers:
      - read_db(db, start, size) -> bytes
      - write_db(db, start, data)
    """

    def __init__(
        self,
        ip_address: str,
        rack: int = 0,
        slot: int = 1,
        tcp_port: int = 102,
        timeout: float = 2.0,
        retry: int = 2,
        retry_interval: float = 0.1,
        auto_connect: bool = True,
    ):
        if Snap7Client is None:  # pragma: no cover
            raise ImportError(f"python-snap7 is not installed or failed to import: {_SNAP7_IMPORT_ERROR}")

        super().__init__()
        self.__ip_address = ip_address
        self.__rack = rack
        self.__slot = slot
        self.__tcp_port = tcp_port
        self.__timeout = timeout
        self.__retry = retry
        self.__retry_interval = retry_interval
        self.__attempts = 0
        self.__auto_connect = auto_connect

        self.__client: Optional[Snap7Client] = None
        self.__connected = False
        self.__last_payload: Dict[str, Any] = {}

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}({self.__ip_address}, rack={self.__rack}, slot={self.__slot}, timeout={self.__timeout}, retry={self.__retry})>"

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
                self.__client = Snap7Client()
                # python-snap7 uses milliseconds in some places; set a reasonable socket timeout where available
                try:
                    self.__client.set_param(snap7.snap7types.RemotePort, self.__tcp_port)  # type: ignore[attr-defined]
                except Exception:
                    pass

                self.__client.connect(self.__ip_address, self.__rack, self.__slot, self.__tcp_port)
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
            if self.__client is not None:
                try:
                    self.__client.disconnect()
                except Exception:
                    pass
                try:
                    self.__client.destroy()
                except Exception:
                    pass
            self.__client = None
            self.__connected = False
            self._debug(self, f"{self.__repr__}: disconnected")
            return 0
        except Exception as e:
            self._error(self, f"{self.__repr__}: disconnect error ({e})")
            return 1

    def send(self, data: Union[bytes, Dict[str, Any], S7DbWrite, List[Union[Dict[str, Any], S7DbWrite]]]) -> int:
        """
        Generic 'send' = DB write(s).

        Accepts:
          - bytes: JSON payload {"db_number": 1, "start": 0, "data_b64": "..."} or list of writes
          - dict: {"db_number": int, "start": int, "data": <bytes or list[int]>}
          - S7DbWrite(db_number, start, data)
          - list of dict/S7DbWrite

        Returns: number of successful writes.
        """
        self._ensure_connected()

        if isinstance(data, (bytes, bytearray)):
            payload = json.loads(data.decode("utf-8"))
            data = payload

        writes: List[Tuple[int, int, bytes]] = []
        if isinstance(data, S7DbWrite):
            writes.append((data.db_number, data.start, data.data))
        elif isinstance(data, dict):
            dbn = int(data["db_number"])
            start = int(data["start"])
            raw = data.get("data", b"")
            if isinstance(raw, list):
                raw = bytes(raw)
            elif isinstance(raw, str):
                # allow hex string
                raw = bytes.fromhex(raw.replace(" ", ""))
            writes.append((dbn, start, raw))
        elif isinstance(data, list):
            for item in data:
                if isinstance(item, S7DbWrite):
                    writes.append((item.db_number, item.start, item.data))
                elif isinstance(item, dict):
                    dbn = int(item["db_number"])
                    start = int(item["start"])
                    raw = item.get("data", b"")
                    if isinstance(raw, list):
                        raw = bytes(raw)
                    elif isinstance(raw, str):
                        raw = bytes.fromhex(raw.replace(" ", ""))
                    writes.append((dbn, start, raw))
                else:
                    raise TypeError(f"Unsupported write item type: {type(item)}")
        else:
            raise TypeError(f"Unsupported send() payload type: {type(data)}")

        ok = 0
        results = []
        for dbn, start, raw in writes:
            try:
                self.write_db(dbn, start, raw)
                results.append({"db_number": dbn, "start": start, "size": len(raw), "status": "Success"})
                ok += 1
            except Exception as e:
                results.append({"db_number": dbn, "start": start, "size": len(raw), "error": str(e)})

        self.__last_payload = {"writes": results}
        return ok

    def receive(self, buffer_size: int = 0) -> str:
        return json.dumps(self.__last_payload, default=str)

    # --------- Helpers ---------

    def read_db(self, db_number: int, start: int, size: int) -> bytes:
        self._ensure_connected()
        assert self.__client is not None
        data = self.__client.db_read(int(db_number), int(start), int(size))
        self.__last_payload = {"reads": [{"db_number": int(db_number), "start": int(start), "size": int(size), "data_hex": data.hex()}]}
        return data

    def write_db(self, db_number: int, start: int, data: bytes) -> None:
        self._ensure_connected()
        assert self.__client is not None
        self.__client.db_write(int(db_number), int(start), data)

    # --------- Internal ---------

    def _ensure_connected(self) -> None:
        if not self.__connected or self.__client is None:
            if not self.__auto_connect:
                raise ConnectionError(f"{self.__repr__}: not connected")
            rc = self.connect()
            if rc != 0:
                raise ConnectionError(f"{self.__repr__}: not connected")
