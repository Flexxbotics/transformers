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
from protocols.beckhoff_ads_twincat import ADS, ADSWrite

try:
    import pyads  # type: ignore
except Exception:   # pragma: no cover
    pyads = None    # type: ignore


# ADS state codes returned by pyads read_state()
_ADS_STATE = {
    0:  "INVALID",
    1:  "IDLE",
    2:  "RESET",
    3:  "INIT",
    4:  "START",
    5:  "RUN",
    6:  "STOP",
    7:  "SAVECFG",
    8:  "LOADCFG",
    9:  "POWERFAILURE",
    10: "POWERGOOD",
    11: "ERROR",
    16: "SHUTDOWN",
}


class BeckhoffTwinCAT(AbstractDevice):
    """
    Transformer for Beckhoff Industrial PCs running TwinCAT 2 or TwinCAT 3.

    Unlike traditional PLCs, Beckhoff devices are standard Industrial PCs (IPCs)
    with TwinCAT software providing the real-time PLC/motion runtime.  The
    hardware and runtime version are selected independently via the model JSON
    and metaData configuration.

    Supported hardware (all run TwinCAT via ADS):
      CX Series  : CX5120, CX5130, CX5140, CX5630, CX5640  (DIN-rail embedded PCs)
                   CX2020, CX2030, CX2040  (high-performance DIN-rail)
                   CX9020  (ARM-based, budget)
      C6xxx      : C6015, C6025, C6030, C6040  (cabinet-mount IPCs)

    Connection parameters (all in metaData):
      ams_net_id        : AMS Net ID of the target TwinCAT runtime (required)
                          Format: "192.168.1.1.1.1" (IP + ".1.1" for local runtime)
      ams_port          : AMS port of the runtime task (required)
                          TwinCAT 3: 851 (runtime 1), 852 (runtime 2), …
                          TwinCAT 2: 801 (runtime 1), 811 (runtime 2), …
      ip_address        : IP address of the IPC (optional — used for ADS routing)
      local_ams_net_id  : AMS Net ID of the machine running this transformer (required
                          when not on the same TwinCAT subnet)
      timeout           : ADS timeout in seconds (default 2.0)
      retry             : connection retry attempts (default 2)
      status_symbol     : PLC symbol to probe for _read_status
                          (default "" — uses ADS state read instead)
      interval_symbols  : comma-separated list of symbols to read on interval
                          (default "")

    Beckhoff-specific nuances
    -------------------------
    * **IPC, not a dedicated PLC**: TwinCAT runs as a real-time extension on
      Windows or as a BSD-based OS (TC/BSD on TC3.1 Build 4026+).  The IPC
      hardware (C6xxx, CX series) determines compute power; TwinCAT provides
      the deterministic runtime.
    * **AMS Net ID routing**: Every TwinCAT node has a unique AMS Net ID
      (6-octet address, e.g. "192.168.1.1.1.1").  The host running this
      transformer must have an ADS route configured to the target IPC — either
      via the TwinCAT router (Windows) or by adding a static route entry.
      Without a route, all ADS connections will be refused at the OS level.
    * **local_ams_net_id**: pyads requires knowing the local AMS Net ID when
      running outside a TwinCAT environment.  Set this to the AMS Net ID
      assigned to the host machine (check TwinCAT System Manager or run
      `pyads.open_port(); pyads.get_local_address()`).
    * **AMS port vs TCP port**: ADS uses port 48898 (TCP/UDP) for transport.
      The ams_port (801, 851, etc.) is an application-layer port inside the
      ADS protocol — it selects which runtime task to address, not a TCP port.
    * **TwinCAT 3 symbol names**: Symbols are dot-qualified paths as defined in
      the TwinCAT PLC project, e.g. "MAIN.bConveyorRunning", "GVL.nCounter".
      TwinCAT 2 symbols use a flat namespace, e.g. ".ACHSSTATUS".
    * **Optimised block access / struct types**: Reading struct symbols returns
      raw bytes.  Use specific typed reads (BOOL, INT, REAL, etc.) for
      individual primitives.
    * **Notifications**: ADS supports change-driven push notifications — more
      efficient than polling for high-frequency signals.  Use add_notification
      command to subscribe; receive() drains the notification queue.
    * **ADS state vs device state**: `read_state` returns two values —
      the ADS state (runtime status: RUN=5, STOP=6) and the device state
      (hardware-level status).  ADS state 5 = RUN is the normal operating state.
    """

    def __init__(self, device: Device):
        super().__init__(device)

        self.meta_data        = device.metaData or {}
        self.ip_address       = self.meta_data.get("ip_address")
        self.ams_net_id       = self.meta_data.get("ams_net_id", "")
        self.ams_port         = int(self.meta_data.get("ams_port", 851))
        self.local_ams_net_id = self.meta_data.get("local_ams_net_id", "")
        self.timeout          = float(self.meta_data.get("timeout", 2.0))
        self.retry            = int(self.meta_data.get("retry", 2))
        self.retry_interval   = float(self.meta_data.get("retry_interval", 0.1))
        self.status_symbol    = self.meta_data.get("status_symbol", "")
        self.interval_symbols = [
            s.strip()
            for s in self.meta_data.get("interval_symbols", "").split(",")
            if s.strip()
        ]

        if not self.ams_net_id:
            raise ValueError("Missing required metaData field: 'ams_net_id'")

        self._client: Optional[ADS] = None
        self._connected = False
        self.status = "transformer Initiated"

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
        """Legacy v1 interface — delegates to _execute_command_v2."""
        command_string = command["commandJson"]
        command_json   = json.loads(command_string)
        command_name   = command_json.get("command", "")
        command_args   = json.dumps({k: v for k, v in command_json.items() if k != "command"})
        self._info(message=f"Sending command: {command_string}")
        return self._execute_command_v2(command_name, command_args)

    def _execute_command_v2(self, command_name: str, command_args: str) -> str:
        """
        Execute a named command against the Beckhoff TwinCAT runtime.

        Supported commands
        ------------------
        connect
            Open ADS connection to the TwinCAT runtime.

        disconnect
            Close ADS connection.

        read
            Read a single PLC symbol by name.
            Args: symbol (str), plc_type (str, default "INT")
                  plc_type values: BOOL, BYTE, WORD, DWORD, SINT, USINT, INT,
                                   UINT, DINT, UDINT, REAL, LREAL, STRING
            Returns: {"symbol": "...", "plc_type": "...", "value": <value>}

        write
            Write a value to a PLC symbol.
            Args: symbol (str), value (<any>), plc_type (str, default "INT")
            Returns: {"symbol": "...", "status": "ok"}

        read_state
            Read the ADS state of the TwinCAT runtime.
            Returns: {"ads_state": 5, "ads_state_name": "RUN",
                      "device_state": 0}

        add_notification
            Subscribe to symbol value-change notifications.
            Args: symbol (str), plc_type (str), cycle_time_ms (int, default 100)
            Returns: {"symbol": "...", "handle": <int>}

        list_symbols
            List all PLC symbols, optionally filtered by prefix.
            Args: prefix (str, optional), limit (int, default 200)
            Returns: {"count": <int>, "symbols": ["MAIN.bRun", ...]}
        """
        args = json.loads(command_args) if command_args else {}
        # Holzman-style: unwrap "value" key if present
        if isinstance(args, dict) and "value" in args and isinstance(args["value"], (str, dict)):
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
            if command_name == "connect":
                self._connect()
                return json.dumps({"status": "ok", "connected": True})

            if command_name == "disconnect":
                self._disconnect()
                return json.dumps({"status": "ok", "connected": False})

            if command_name == "read":
                self._ensure_connected()
                symbol   = str(args.get("symbol", ""))
                plc_type = args.get("plc_type", "INT")
                if not symbol:
                    return self._err("Missing required field: symbol")
                plc_type_val = self._plc_type_from_str(plc_type)
                if plc_type_val is None and plc_type:
                    return self._err(f"Unsupported plc_type: '{plc_type}'")
                value = self._client.read(symbol, plc_type=plc_type_val)
                return json.dumps({
                    "status": "ok", "symbol": symbol,
                    "plc_type": plc_type, "value": value,
                }, default=str)

            if command_name == "write":
                self._ensure_connected()
                symbol   = str(args.get("symbol", ""))
                value    = args.get("value")
                plc_type = args.get("plc_type", "INT")
                if not symbol:
                    return self._err("Missing required field: symbol")
                plc_type_val = self._plc_type_from_str(plc_type)
                self._client.write(symbol, value, plc_type=plc_type_val)
                return json.dumps({"status": "ok", "symbol": symbol, "value": value},
                                  default=str)

            if command_name == "read_state":
                self._ensure_connected()
                ads_state, device_state = self._read_ads_state()
                return json.dumps({
                    "status": "ok",
                    "ads_state": ads_state,
                    "ads_state_name": _ADS_STATE.get(ads_state, "UNKNOWN"),
                    "device_state": device_state,
                })

            if command_name == "add_notification":
                self._ensure_connected()
                symbol       = str(args.get("symbol", ""))
                plc_type     = args.get("plc_type", "INT")
                cycle_ms     = int(args.get("cycle_time_ms", 100))
                if not symbol:
                    return self._err("Missing required field: symbol")
                plc_type_val = self._plc_type_from_str(plc_type)
                handle = self._client.add_notification(
                    symbol, plc_type=plc_type_val, cycle_time_ms=cycle_ms
                )
                return json.dumps({"status": "ok", "symbol": symbol, "handle": handle})

            if command_name == "list_symbols":
                prefix = args.get("prefix")
                limit  = int(args.get("limit", 200))
                names  = self._list_symbols(prefix=prefix, limit=limit)
                return json.dumps({"status": "ok", "count": len(names),
                                   "symbols": names}, default=str)

            return self._err(f"Unknown command: '{command_name}'")

        except Exception as e:
            self._error(message=f"Command '{command_name}' failed: {e}")
            return self._err(str(e))

    def _read_interval_data(self) -> str:
        """
        Periodic poll. Reads each symbol in metaData.interval_symbols and
        returns their values as a JSON map. Falls back to _read_status when
        no interval symbols are configured.
        """
        if not self.interval_symbols:
            return json.dumps({"status": self._read_status()})

        self._ensure_connected()
        results = {}
        for symbol in self.interval_symbols:
            try:
                value = self._client.read(symbol)
                results[symbol] = value
            except Exception as e:
                results[symbol] = f"ERROR: {e}"
        return json.dumps({"status": "ok", "values": results}, default=str)

    def _read_status(self, function: str = None) -> str:
        """
        Read TwinCAT runtime status via ADS state.

        ADS state 5 (RUN)  → "RUNNING"
        ADS state 6 (STOP) → "STOPPED"
        Any other state     → the state name string (INIT, ERROR, etc.)

        If metaData.status_symbol is set, reads that symbol instead and
        returns "RUNNING" / "STOPPED" based on truthiness of the value —
        useful when the PLC program maintains its own status register.
        """
        try:
            self._ensure_connected()

            if self.status_symbol:
                value = self._client.read(self.status_symbol)
                self.status = "RUNNING" if value else "STOPPED"
                return self.status

            ads_state, _ = self._read_ads_state()
            if ads_state == 5:
                self.status = "RUNNING"
            elif ads_state == 6:
                self.status = "STOPPED"
            else:
                self.status = _ADS_STATE.get(ads_state, f"ADS_STATE_{ads_state}")

        except Exception as e:
            self._error(message=f"Status check failed: {e}")
            self.status = "ERROR"

        return self.status

    def _read_variable(self, variable_name: str, function: str = None) -> str:
        """
        Read a PLC symbol by name.  variable_name is the fully-qualified
        TwinCAT symbol path, e.g. "MAIN.bConveyorRunning" or ".ACHSSTATUS".
        """
        try:
            self._ensure_connected()
            value = self._client.read(variable_name)
            return str(value) if value is not None else ""
        except Exception as e:
            self._error(message=f"Read variable '{variable_name}' failed: {e}")
            return ""

    def _write_variable(self, variable_name: str, variable_value: str,
                        function: str = None) -> str:
        """
        Write a value to a PLC symbol.  Coerces the string value to int,
        float, or bool before writing (same priority order as Holzman pattern).
        """
        try:
            self._ensure_connected()
            value = self._coerce_value(variable_value)
            self._client.write(variable_name, value)
            return variable_value
        except Exception as e:
            self._error(message=f"Write variable '{variable_name}' failed: {e}")
            return ""

    def _read_parameter(self, parameter_name: str, function: str = None) -> str:
        return self._read_variable(parameter_name, function)

    def _write_parameter(self, parameter_name: str, parameter_value: str,
                         function: str = None) -> str:
        return self._write_variable(parameter_name, parameter_value, function)

    def _run_program(self, function: str = None):
        """Not applicable — TwinCAT programs run cyclically in their tasks."""
        self._info(message="run_program called — TwinCAT programs run cyclically in tasks")
        return ""

    def _read_file_names(self) -> list:
        """Return PLC symbol names as a proxy for available 'files'."""
        return self._list_symbols()

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
        """Create ADS client, configure local AMS Net ID, and open connection."""
        self._info(message=f"Connecting to Beckhoff TwinCAT {self.ams_net_id}:{self.ams_port}")

        if self._connected and self._client is not None:
            return

        # Set local AMS Net ID so pyads knows how to route responses back
        if self.local_ams_net_id and pyads is not None:
            try:
                pyads.set_local_address(self.local_ams_net_id)
            except Exception as e:
                self._warn(message=f"set_local_address failed: {e}")

        self._client = ADS(
            ams_net_id=str(self.ams_net_id),
            ams_port=int(self.ams_port),
            ip_address=str(self.ip_address) if self.ip_address else None,
            timeout=self.timeout,
            retry=self.retry,
            retry_interval=self.retry_interval,
            auto_connect=False,  # we control the connect call explicitly
        )

        rc = self._client.connect()
        if rc != 0:
            self._connected = False
            raise ConnectionError(
                f"BeckhoffTwinCAT: ADS connect failed "
                f"(ams_net_id={self.ams_net_id}, ams_port={self.ams_port}, "
                f"ip={self.ip_address})"
            )

        self._connected = True
        self._info(message="Connected to Beckhoff TwinCAT runtime")

    def _disconnect(self) -> None:
        """Close ADS connection and release client."""
        self._info(message="Disconnecting from Beckhoff TwinCAT")
        try:
            if self._client is not None:
                self._client.disconnect()
        finally:
            self._client = None
            self._connected = False

    def _ensure_connected(self) -> None:
        if not self._connected or self._client is None:
            self._connect()

    def _read_ads_state(self):
        """
        Read the ADS state and device state from the TwinCAT runtime.
        Returns (ads_state: int, device_state: int).

        Accesses the underlying pyads Connection via the protocol's private
        attribute (_ADS__conn) — the same pattern used in the Holzman transformer.
        """
        assert self._client is not None
        conn = getattr(self._client, "_ADS__conn", None)
        if conn is None:
            raise RuntimeError("ADS connection object not accessible")
        ads_state, device_state = conn.read_state()
        return int(ads_state), int(device_state)

    def _list_symbols(self, prefix: Optional[str] = None, limit: int = 200) -> List[str]:
        """
        List all symbols in the TwinCAT project, optionally filtered by prefix.
        Opens a fresh raw pyads connection to avoid interfering with the main
        ADS client session.
        """
        if pyads is None:
            return []

        if self.local_ams_net_id:
            try:
                pyads.set_local_address(self.local_ams_net_id)
            except Exception:
                pass

        conn = pyads.Connection(
            str(self.ams_net_id),
            int(self.ams_port),
            str(self.ip_address) if self.ip_address else None,
        )
        try:
            conn.set_timeout(int(self.timeout * 1000))
            conn.open()
            syms  = conn.get_all_symbols()
            names = [s.name for s in syms]
            if prefix:
                names = [n for n in names if n.startswith(prefix)]
            return names[:max(1, int(limit))]
        finally:
            try:
                conn.close()
            except Exception:
                pass

    @staticmethod
    def _plc_type_from_str(plc_type: Optional[str]):
        """
        Convert a string type name to the corresponding pyads PLCTYPE_* constant.
        Returns None for unknown types (pyads will then auto-detect from symbol info).
        """
        if plc_type is None or pyads is None:
            return None
        mapping = {
            "BOOL":   pyads.PLCTYPE_BOOL,
            "BYTE":   pyads.PLCTYPE_BYTE,
            "WORD":   pyads.PLCTYPE_WORD,
            "DWORD":  pyads.PLCTYPE_DWORD,
            "SINT":   pyads.PLCTYPE_SINT,
            "USINT":  pyads.PLCTYPE_USINT,
            "INT":    pyads.PLCTYPE_INT,
            "UINT":   pyads.PLCTYPE_UINT,
            "DINT":   pyads.PLCTYPE_DINT,
            "UDINT":  pyads.PLCTYPE_UDINT,
            "LINT":   pyads.PLCTYPE_LINT,
            "ULINT":  pyads.PLCTYPE_ULINT,
            "REAL":   pyads.PLCTYPE_REAL,
            "LREAL":  pyads.PLCTYPE_LREAL,
            "STRING": pyads.PLCTYPE_STRING,
        }
        return mapping.get(str(plc_type).strip().upper())

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
        """Standardised error response JSON."""
        try:
            self._error(message=message)
        except Exception:
            pass
        return json.dumps({"status": "error", "message": str(message)}, default=str)
