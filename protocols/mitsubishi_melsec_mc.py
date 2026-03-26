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
    import pymcprotocol  # type: ignore
except Exception as e:  # pragma: no cover
    pymcprotocol = None  # type: ignore
    _PYMCPROTOCOL_IMPORT_ERROR = e

# ---------------------------------------------------------------------------
# Device type classification helpers
#
# Mitsubishi PLCs use named device areas rather than raw memory addresses.
# Each area has a prefix letter (or letters) that determines whether it is
# addressed in word-units or bit-units, and whether its address digits are
# decimal or hexadecimal.
#
# Bit devices  : X, Y, M, L, F, V, B, S – individual on/off points
# Word devices : D, W, R, ZR, TN, CN, SD, SW – 16-bit integer registers
#
# Note on X / Y addressing:
#   On Q-series and iQ-R PLCs the X and Y device numbers are hexadecimal
#   (X0, X1 … XF, X10 …).  Pass the address exactly as the PLC expects it,
#   e.g. "X0F" or "Y10".  pymcprotocol handles the hex conversion internally.
# ---------------------------------------------------------------------------

_BIT_DEVICE_PREFIXES: frozenset[str] = frozenset(
    {"X", "Y", "M", "L", "F", "V", "B", "S", "SC", "TC", "CC"}
)
_WORD_DEVICE_PREFIXES: frozenset[str] = frozenset(
    {"D", "W", "R", "ZR", "TN", "CN", "SD", "SW", "Z"}
)


def _device_prefix(device: str) -> str:
    """Return the alphabetic prefix of a device address (upper-cased)."""
    return "".join(c for c in device if c.isalpha()).upper()


def _infer_is_bit(device: str) -> bool:
    """
    Infer whether a device address uses bit-units or word-units from its
    prefix.  Returns True for bit devices, False for word devices.
    Raises ValueError if the prefix is not recognised.
    """
    prefix = _device_prefix(device)
    if prefix in _BIT_DEVICE_PREFIXES:
        return True
    if prefix in _WORD_DEVICE_PREFIXES:
        return False
    raise ValueError(
        f"Unknown Melsec device prefix '{prefix}' in '{device}'. "
        f"Specify is_bit explicitly."
    )


# ---------------------------------------------------------------------------
# Data-transfer descriptors
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MelsecWrite:
    """
    Descriptor for a single Melsec batch-write operation.

    :param device:  Head device address, e.g. ``"D100"``, ``"M0"``, ``"Y20"``.
    :param values:  Tuple of integer values to write starting at *device*.
                    For word devices each element is a 16-bit signed integer.
                    For bit devices each element is 0 (OFF) or 1 (ON).
    :param is_bit:  ``True`` = bit-unit write, ``False`` = word-unit write.
                    ``None`` (default) auto-infers from the device prefix.
    """
    device: str
    values: Tuple[int, ...]
    is_bit: Optional[bool] = None


@dataclass(frozen=True)
class MelsecRead:
    """
    Descriptor for a single Melsec batch-read operation.

    :param device:  Head device address, e.g. ``"D100"``, ``"M0"``.
    :param count:   Number of consecutive points to read.
    :param is_bit:  ``True`` = bit-unit read, ``False`` = word-unit read.
                    ``None`` (default) auto-infers from the device prefix.
    """
    device: str
    count: int = 1
    is_bit: Optional[bool] = None


# ---------------------------------------------------------------------------
# Protocol class
# ---------------------------------------------------------------------------

class MelsecMC(AbstractProtocol):
    """
    Mitsubishi MELSEC MC Protocol (3E / 1E frame) client via *pymcprotocol*.

    Supports Q-Series, iQ-R, iQ-F, and L-Series PLCs over TCP.

    Default port  : 5007  (3E frame)
                    5002  (1E frame, legacy FX/Q)

    Frame selection
    ---------------
    ``frame_type="3E"``  — recommended for Q, iQ-R, iQ-F, L series (default)
    ``frame_type="1E"``  — legacy frame; limited command support

    PLC type selection (3E only)
    ----------------------------
    ``plc_type="Q"``    — Q series (default)
    ``plc_type="iQ-R"`` — iQ-R series
    ``plc_type="iQ-F"`` — iQ-F (FX5U etc.)
    ``plc_type="L"``    — L series
    ``plc_type="QnA"``  — QnA legacy

    Protocol nuances vs other industrial protocols
    -----------------------------------------------
    * **Device-based addressing** — registers are named (D100, M0, Y1F) not
      raw byte offsets.  Batch reads/writes operate on contiguous ranges
      starting at a head device.
    * **Bit vs word units** — the protocol has separate commands for bit
      devices (X, Y, M …) and word devices (D, W, R …).  This class
      auto-infers the unit type from the device prefix when ``is_bit=None``.
    * **Hex X/Y addresses** — on Q/iQ-R PLCs the X and Y input/output
      numbers are hexadecimal.  Pass them as the PLC displays them, e.g.
      ``"X0F"`` or ``"Y10"``.
    * **Random read/write** — supports non-contiguous device access in a
      single round-trip (mix of word and bit devices allowed).
    * **Network routing** — ``network``, ``pc_number``, ``m_unit_io_number``,
      and ``m_unit_station_number`` identify the target PLC when multiple
      PLCs share a Melsec network.

    AbstractProtocol mapping
    ------------------------
    ``send()``    → batch write (or random write) to one or more devices.
    ``receive()`` → last read result serialised as a JSON string.
    ``connect()`` → open TCP socket to the PLC.
    ``disconnect()`` → close TCP socket.

    Helpers
    -------
    ``read_words(device, count)``
    ``read_bits(device, count)``
    ``write_words(device, values)``
    ``write_bits(device, values)``
    ``random_read(word_devices, bit_devices)``
    ``random_write(word_devices, word_values, bit_devices, bit_values)``
    """

    def __init__(
        self,
        ip_address: str,
        port: int = 5007,
        frame_type: str = "3E",
        plc_type: str = "Q",
        timeout: float = 2.0,
        retry: int = 2,
        retry_interval: float = 0.1,
        auto_connect: bool = True,
        network: int = 0,
        pc_number: int = 0xFF,
        m_unit_io_number: int = 0x3FF,
        m_unit_station_number: int = 0,
    ):
        if pymcprotocol is None:  # pragma: no cover
            raise ImportError(
                f"pymcprotocol is not installed or failed to import: "
                f"{_PYMCPROTOCOL_IMPORT_ERROR}"
            )

        super().__init__()

        self.__ip_address = ip_address
        self.__port = int(port)
        self.__frame_type = frame_type.upper()
        self.__plc_type = plc_type
        self.__timeout = timeout
        self.__retry = retry
        self.__retry_interval = retry_interval
        self.__auto_connect = auto_connect
        self.__network = network
        self.__pc_number = pc_number
        self.__m_unit_io_number = m_unit_io_number
        self.__m_unit_station_number = m_unit_station_number

        self.__attempts: int = 0
        self.__connected: bool = False
        self.__client: Optional[Any] = None  # pymcprotocol.Type3E or Type1E
        self.__last_payload: Dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Dunder helpers
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        return (
            f"<{self.__class__.__name__}"
            f"({self.__ip_address}:{self.__port}, "
            f"frame={self.__frame_type}, plc={self.__plc_type}, "
            f"timeout={self.__timeout}, retry={self.__retry})>"
        )

    def __del__(self):
        try:
            self.disconnect()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # AbstractProtocol — connect / disconnect
    # ------------------------------------------------------------------

    def connect(self) -> int:
        """
        Open a TCP connection to the Melsec PLC.

        Returns 0 on success, 1 on failure (after exhausting retries).
        """
        self.__attempts = 0
        last_err: Optional[Exception] = None

        while self.__attempts < self.__retry:
            try:
                if self.__frame_type == "3E":
                    client = pymcprotocol.Type3E(plctype=self.__plc_type)
                elif self.__frame_type == "1E":
                    client = pymcprotocol.Type1E(plctype=self.__plc_type)
                else:
                    raise ValueError(
                        f"Unsupported frame_type '{self.__frame_type}'. "
                        f"Use '3E' or '1E'."
                    )

                # Apply network routing parameters (3E supports all four;
                # 1E uses a subset — pymcprotocol ignores unsupported ones).
                client.network = self.__network
                client.pc = self.__pc_number
                client.unit_io = self.__m_unit_io_number
                client.unit_station = self.__m_unit_station_number

                # pymcprotocol exposes a TCP-level timeout as a socket option.
                # Set it before calling connect so the handshake itself obeys it.
                client.set_access_opt(msg_wait_timeout=self.__timeout)

                client.connect(self.__ip_address, self.__port)

                self.__client = client
                self.__connected = True
                self._debug(self, f"{self!r} connected")
                return 0

            except Exception as e:
                last_err = e
                self.__connected = False
                self.__client = None
                self._warn(
                    self,
                    f"{self!r} connection failed ({e}). Retrying "
                    f"({self.__attempts + 1}/{self.__retry}).",
                )
                self.__attempts += 1
                time.sleep(self.__retry_interval)

        self._error(self, f"{self!r} unable to connect after {self.__retry} attempts ({last_err})")
        return 1

    def disconnect(self) -> int:
        """
        Close the TCP connection to the PLC.

        Returns 0 on success, 1 on error.
        """
        try:
            if self.__client is not None:
                try:
                    self.__client.close()
                except Exception:
                    pass
            self.__client = None
            self.__connected = False
            self._debug(self, f"{self!r} disconnected")
            return 0
        except Exception as e:
            self._error(self, f"{self!r} disconnect error ({e})")
            return 1

    # ------------------------------------------------------------------
    # AbstractProtocol — send / receive
    # ------------------------------------------------------------------

    def send(
        self,
        data: Union[
            bytes,
            Dict[str, Any],
            MelsecWrite,
            List[Union[Dict[str, Any], MelsecWrite]],
        ],
    ) -> int:
        """
        Write one or more device values to the PLC.

        Accepts:

        * ``MelsecWrite`` dataclass instance (or list of them)
        * ``dict`` / ``bytes`` → dict with keys ``device``, ``values``,
          and optionally ``is_bit``
        * List mixing both forms

        Returns the number of successful write operations.

        Example::

            # Single word write: D100 = 42
            protocol.send(MelsecWrite(device="D100", values=(42,)))

            # Batch bit write: turn M0…M2 ON/OFF/ON
            protocol.send(MelsecWrite(device="M0", values=(1, 0, 1), is_bit=True))

            # Multiple writes in one call
            protocol.send([
                MelsecWrite("D0", (100, 200, 300)),
                MelsecWrite("M10", (1,), is_bit=True),
            ])
        """
        self._ensure_connected()

        # --- normalise input to List[MelsecWrite] -----------------------
        if isinstance(data, (bytes, bytearray)):
            data = json.loads(data.decode("utf-8"))

        writes: List[MelsecWrite] = []
        if isinstance(data, MelsecWrite):
            writes.append(data)
        elif isinstance(data, dict):
            writes.append(self.__dict_to_write(data))
        elif isinstance(data, list):
            for item in data:
                if isinstance(item, MelsecWrite):
                    writes.append(item)
                elif isinstance(item, dict):
                    writes.append(self.__dict_to_write(item))
                else:
                    raise TypeError(f"Unsupported write item type: {type(item)}")
        else:
            raise TypeError(f"Unsupported send() payload type: {type(data)}")

        # --- execute each write -----------------------------------------
        ok = 0
        results: List[Dict[str, Any]] = []
        for w in writes:
            try:
                is_bit = w.is_bit if w.is_bit is not None else _infer_is_bit(w.device)
                values_list = list(w.values)
                if is_bit:
                    self.__client.batchwrite_bitunits(
                        headdevice=w.device, values=values_list
                    )
                else:
                    self.__client.batchwrite_wordunits(
                        headdevice=w.device, values=values_list
                    )
                results.append({"device": w.device, "status": "Success"})
                ok += 1
            except Exception as e:
                self._warn(self, f"write to '{w.device}' failed: {e}")
                results.append({"device": w.device, "error": str(e)})

        self.__last_payload = {"writes": results}
        return ok

    def receive(self, buffer_size: int = 1) -> str:
        """
        Return the last read result as a JSON string.

        ``buffer_size`` is accepted for interface compatibility but is not
        used — Melsec read responses are synchronous and returned directly
        from the helper methods (``read_words``, ``read_bits``,
        ``random_read``).  Call those helpers first, then call
        ``receive()`` to retrieve the cached result.

        Returns a JSON object with a ``reads`` key, e.g.::

            {"reads": [{"device": "D100", "count": 3, "values": [1, 2, 3]}]}
        """
        return json.dumps(self.__last_payload, default=str)

    # ------------------------------------------------------------------
    # High-level read/write helpers
    # ------------------------------------------------------------------

    def read_words(self, device: str, count: int = 1) -> List[int]:
        """
        Batch-read *count* consecutive word-unit devices starting at *device*.

        Returns a list of integer values.

        Example::

            values = protocol.read_words("D100", count=10)
            # → [0, 1, 2, 3, 4, 5, 6, 7, 8, 9]
        """
        self._ensure_connected()
        _, values = self.__client.batchread_wordunits(
            headdevice=device, readsize=count
        )
        self.__last_payload = {
            "reads": [{"device": device, "count": count, "values": values}]
        }
        self._debug(self, f"read_words({device!r}, {count}) → {values}")
        return values

    def read_bits(self, device: str, count: int = 1) -> List[int]:
        """
        Batch-read *count* consecutive bit-unit devices starting at *device*.

        Returns a list of 0/1 integer values.

        Example::

            bits = protocol.read_bits("M0", count=8)
            # → [1, 0, 1, 1, 0, 0, 0, 1]
        """
        self._ensure_connected()
        _, values = self.__client.batchread_bitunits(
            headdevice=device, readsize=count
        )
        self.__last_payload = {
            "reads": [{"device": device, "count": count, "values": values}]
        }
        self._debug(self, f"read_bits({device!r}, {count}) → {values}")
        return values

    def write_words(self, device: str, values: List[int]) -> None:
        """
        Batch-write *values* to consecutive word-unit devices starting at
        *device*.

        Example::

            protocol.write_words("D100", [10, 20, 30])
        """
        self._ensure_connected()
        self.__client.batchwrite_wordunits(headdevice=device, values=values)
        self.__last_payload = {
            "writes": [{"device": device, "values": values, "status": "Success"}]
        }
        self._debug(self, f"write_words({device!r}, {values})")

    def write_bits(self, device: str, values: List[int]) -> None:
        """
        Batch-write *values* (0 or 1) to consecutive bit-unit devices
        starting at *device*.

        Example::

            protocol.write_bits("M0", [1, 0, 1])
        """
        self._ensure_connected()
        self.__client.batchwrite_bitunits(headdevice=device, values=values)
        self.__last_payload = {
            "writes": [{"device": device, "values": values, "status": "Success"}]
        }
        self._debug(self, f"write_bits({device!r}, {values})")

    def random_read(
        self,
        word_devices: Optional[List[str]] = None,
        bit_devices: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Read non-contiguous devices in a single Melsec round-trip.

        Both word and bit device lists may be provided in the same call.
        Returns a dict with ``word`` and ``bit`` keys::

            {
                "word": {"D0": 100, "D10": 200},
                "bit":  {"M0": 1, "X1": 0},
            }

        Example::

            result = protocol.random_read(
                word_devices=["D0", "D10", "W5"],
                bit_devices=["M0", "X1"],
            )
        """
        self._ensure_connected()
        word_devices = word_devices or []
        bit_devices = bit_devices or []

        word_vals, bit_vals = self.__client.randomread(
            word_devices=word_devices, dword_devices=[], bit_devices=bit_devices
        )

        result: Dict[str, Any] = {
            "word": dict(zip(word_devices, word_vals)),
            "bit":  dict(zip(bit_devices, bit_vals)),
        }
        self.__last_payload = {"reads": [result]}
        self._debug(self, f"random_read → {result}")
        return result

    def random_write(
        self,
        word_devices: Optional[List[str]] = None,
        word_values: Optional[List[int]] = None,
        bit_devices: Optional[List[str]] = None,
        bit_values: Optional[List[int]] = None,
    ) -> None:
        """
        Write non-contiguous devices in a single Melsec round-trip.

        Example::

            protocol.random_write(
                word_devices=["D0", "D10"],
                word_values=[100, 200],
                bit_devices=["M0"],
                bit_values=[1],
            )
        """
        self._ensure_connected()
        word_devices = word_devices or []
        word_values  = word_values  or []
        bit_devices  = bit_devices  or []
        bit_values   = bit_values   or []

        self.__client.randomwrite(
            word_devices=word_devices,
            word_values=word_values,
            dword_devices=[],
            dword_values=[],
            bit_devices=bit_devices,
            bit_values=bit_values,
        )
        self.__last_payload = {
            "writes": [
                {
                    "word": dict(zip(word_devices, word_values)),
                    "bit":  dict(zip(bit_devices, bit_values)),
                    "status": "Success",
                }
            ]
        }
        self._debug(
            self,
            f"random_write word={dict(zip(word_devices, word_values))} "
            f"bit={dict(zip(bit_devices, bit_values))}",
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ensure_connected(self) -> None:
        """Connect if not already connected (when auto_connect is True)."""
        if not self.__connected or self.__client is None:
            if not self.__auto_connect:
                raise ConnectionError(f"{self!r}: not connected")
            rc = self.connect()
            if rc != 0:
                raise ConnectionError(f"{self!r}: unable to connect")

    @staticmethod
    def __dict_to_write(d: Dict[str, Any]) -> MelsecWrite:
        """Convert a plain dict to a MelsecWrite descriptor."""
        device = str(d["device"])
        raw_values = d.get("values", [])
        values: Tuple[int, ...] = tuple(int(v) for v in raw_values)
        is_bit: Optional[bool] = d.get("is_bit")
        return MelsecWrite(device=device, values=values, is_bit=is_bit)
