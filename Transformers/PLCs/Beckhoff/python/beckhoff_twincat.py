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
from protocols.beckhoff_ads_twincat import ADS
import pyads


# --------------------------------------------------------------------------------------
# Beckhoff TwinCAT (generic ADS) driver class
# --------------------------------------------------------------------------------------

class BeckhoffTwinCAT(AbstractDevice):
    """
    Generic transformer for Beckhoff Industrial PCs running TwinCAT 2 or TwinCAT 3.

    Unlike traditional PLCs, Beckhoff devices are standard Industrial PCs (IPCs)
    with TwinCAT software providing the real-time PLC/motion runtime.  The
    hardware and runtime version are selected independently via the model JSON
    and metaData configuration.

    This is a generic ADS driver: it exposes connect/disconnect/read/write and
    symbol enumeration.  It intentionally makes no assumptions about the PLC
    program running on the target, so `_read_status` is left unimplemented (see
    the TODO in that method).  A machine-specific subclass (see the Holzman
    Beckhoff transformer for an example) is where you wire status, part counts,
    and fault detection to the actual PLC symbols exported by your project.

    Supported hardware (all run TwinCAT via ADS):
      CX Series  : CX5120, CX5130, CX5140, CX5630, CX5640  (DIN-rail embedded PCs)
                   CX2020, CX2030, CX2040  (high-performance DIN-rail)
                   CX9020  (ARM-based, budget)
      C6xxx      : C6015, C6025, C6030, C6040  (cabinet-mount IPCs)

    Connection parameters (all in metaData):
      ams_net_id        : AMS Net ID of the target TwinCAT runtime (required)
                          Format: "192.168.1.1.1.1" (IP + ".1.1" for local runtime)
      ams_port          : AMS port of the runtime task (default 851)
                          TwinCAT 3: 851 (runtime 1), 852 (runtime 2), …
                          TwinCAT 2: 801 (runtime 1), 811 (runtime 2), …
      ip_address        : IP address of the IPC (optional — used for ADS routing)
      local_ams_net_id  : AMS Net ID of the machine running this transformer
                          (required when not on the same TwinCAT subnet)
      timeout           : ADS timeout in seconds (default 2.0)
      retry             : connection retry attempts (default 2)
      retry_interval    : delay between retries in seconds (default 0.1)
      auto_connect      : whether the ADS wrapper connects on construction
                          (default True)

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
    """

    # ############################################################################## #
    # INSTANTIATION
    # ############################################################################## #

    def __init__(self, device: Device):
        """
        Beckhoff ADS/TwinCAT device class.

        Expects device.metaData to contain (at minimum):
          - ams_net_id: "x.x.x.x.x.x" (TwinCAT AMS Net ID of the target)
          - ip_address: "a.b.c.d" (optional for pyads, but typically provided)
          - ams_port: int (TwinCAT 2 often 801; TwinCAT 3 often 851)
          - local_ams_net_id: "x.x.x.x.x.x" (AMS Net ID of this host)
          - timeout: float seconds (optional)
          - retry: int (optional)
          - retry_interval: float seconds (optional)
        """
        try:
            super().__init__(device)

            self.meta_data = device.metaData or {}

            # Common metadata keys
            self.address = self.meta_data.get("ip_address") or self.meta_data.get("address")

            # Beckhoff ADS specifics
            self.ams_net_id = self.meta_data.get("ams_net_id")
            self.ams_port = int(self.meta_data.get("ams_port", 851))
            self.local_ams_net_id = self.meta_data.get("local_ams_net_id")

            if not self.ams_net_id:
                raise ValueError("Missing required metaData field: 'ams_net_id'")

            # Connection tuning
            self.timeout = float(self.meta_data.get("timeout", 2.0))
            self.retry = int(self.meta_data.get("retry", 2))
            self.retry_interval = float(self.meta_data.get("retry_interval", 0.1))
            self.auto_connect = bool(self.meta_data.get("auto_connect", True))

            # Beckhoff client (protocol wrapper)
            self.client: ADS | None = None

            # Track state
            self._connected = False

        except Exception as e:
            self._logger.error(str(e))
            raise RuntimeError(f"BeckhoffTwinCAT init failed: {e}") from e

    def __del__(self):
        try:
            if getattr(self, "_connected", False):
                self._disconnect()
        except Exception:
            pass

    # ############################################################################## #
    # DEVICE COMMUNICATION METHODS
    # ############################################################################## #

    def _execute_command_v2(self, command_name: str, command_args: str) -> str:
        """
        Executes the command sent to the device.

        Commands:
          - connect
          - disconnect
          - read          (reads an ADS symbol)
          - write         (writes an ADS symbol)
          - list_symbols  (enumerates the PLC symbols on the target)

        read command args (JSON inside args["value"]):
          {
            "symbol": "MAIN.bConveyorRunning",
            "plc_type": "BOOL"
          }

        write command args (JSON inside args["value"]):
          {
            "symbol": "MAIN.nSetpoint",
            "value": 42,
            "plc_type": "INT"
          }

        list_symbols command args:
          {
            "prefix": "MAIN.",   # optional
            "limit": 200          # optional
          }
        """
        # Parse the command from the incoming request
        args = json.loads(command_args) if command_args else {}
        args = json.loads(args["value"]) if isinstance(args, dict) and "value" in args else (args or {})

        try:
            if command_name == "connect":
                self._connect(timeout_s=10)
                return json.dumps({"status": "ok", "connected": True})

            if command_name == "disconnect":
                self._disconnect()
                return json.dumps({"status": "ok", "connected": False})

            if command_name == "read":
                self._ensure_connected()
                assert self.client is not None
                symbol = args.get("symbol")
                plc_type = args.get("plc_type", "INT")
                if not symbol:
                    return self._err("Missing required field: symbol")
                plc_type_val = self._plc_type_from_args(plc_type)
                if plc_type_val is None:
                    return self._err(f"Unsupported plc_type: {plc_type}")
                try:
                    value = self.client.read(symbol, plc_type=plc_type_val)
                    return json.dumps({
                        "status": "ok",
                        "symbol": symbol,
                        "plc_type": plc_type,
                        "value": value
                    }, default=str)
                except Exception as e:
                    return self._err(str(e))

            if command_name == "write":
                self._ensure_connected()
                assert self.client is not None
                symbol = args.get("symbol")
                value = args.get("value")
                plc_type = args.get("plc_type", "INT")
                if not symbol:
                    return self._err("Missing required field: symbol")
                plc_type_val = self._plc_type_from_args(plc_type)
                if plc_type_val is None:
                    return self._err(f"Unsupported plc_type: {plc_type}")
                try:
                    self.client.write(symbol, value, plc_type=plc_type_val)
                    return json.dumps({
                        "status": "ok",
                        "symbol": symbol,
                        "value": value
                    }, default=str)
                except Exception as e:
                    return self._err(str(e))

            if command_name == "list_symbols":
                prefix = args.get("prefix")
                limit = int(args.get("limit", 200))
                names = self._list_symbols(prefix=prefix, limit=limit)
                return json.dumps({"status": "ok", "count": len(names), "symbols": names}, default=str)

            # ---- Unknown command ----
            return "UNKNOWN_COMMAND"

        except Exception as e:
            self._logger.error(str(e))
            return str(e)

    def _read_interval_data(self) -> str:
        """
        Method to read the status of the device on an interval.

        Generic driver: this simply ensures the ADS connection is alive.  A
        machine-specific subclass should read the PLC symbols it cares about
        here (machine state, active program, part counts, faults) and forward
        them to the run-record / event services, as the Holzman Beckhoff
        transformer does.
        """
        self._ensure_connected()

    def _read_status(self, function: str = None) -> str:
        """
        Method to read the status of the device.

        Left unimplemented in the generic driver — TwinCAT does not expose a
        universal "machine status" over ADS; status lives in whatever symbols
        the PLC program happens to define, and those differ per machine.

        TODO: implement by reading the PLC symbols that represent status on
        your target and mapping them to a status string, e.g.:

            self._ensure_connected()
            bool_type = self._plc_type_from_args("BOOL")

            # Fault flags (True = fault active)
            if self.client.read(symbol="MAIN.bFault", plc_type=bool_type):
                return "FAULT"

            # Running flag
            if self.client.read(symbol="MAIN.bRunning", plc_type=bool_type):
                return "RUNNING"

            return "IDLE"

        Use `list_symbols` to discover which symbols the PLC project exports,
        then pick the ones that describe running / idle / fault state.
        """
        status = ""
        # TODO: read the machine's status symbol(s) here (see docstring above).
        return status

    def _read_variable(self, variable_name: str, function: str = None) -> str:
        """
        Read a PLC symbol by name.  variable_name is the fully-qualified
        TwinCAT symbol path, e.g. "MAIN.bConveyorRunning" or ".ACHSSTATUS".
        """
        value = ""
        try:
            if function is None:
                self._ensure_connected()
                assert self.client is not None
                # plc_type omitted → the ADS wrapper reads the symbol's declared
                # type from the PLC (read_by_name auto-detect). Only the command
                # API, where a caller can name a type, passes an explicit one.
                value = self.client.read(variable_name)
                value = str(value) if value is not None else ""
        except Exception as e:
            self._logger.error(f"Read variable '{variable_name}' failed: {e}")
            value = ""
        return value

    def _write_variable(self, variable_name: str, variable_value: str, function: str = None) -> str:
        """
        Write a value to a PLC symbol.  Coerces the string value to bool → int →
        float → str before writing.
        """
        value = ""
        try:
            if function is None:
                self._ensure_connected()
                assert self.client is not None
                # plc_type omitted → the ADS wrapper writes using the symbol's
                # declared type from the PLC (write_by_name auto-detect).
                self.client.write(variable_name, self._coerce_value(variable_value))
                value = variable_value
        except Exception as e:
            self._logger.error(f"Write variable '{variable_name}' failed: {e}")
            value = ""
        return value

    def _read_parameter(self, parameter_name: str, function: str = None) -> str:
        return self._read_variable(parameter_name, function)

    def _write_parameter(self, parameter_name: str, parameter_value: str, function: str = None) -> str:
        return self._write_variable(parameter_name, parameter_value, function)

    def _read_file_names(self) -> list:
        self.programs = []
        return self.programs

    def _read_file(self, file_name: str) -> str:
        file_data = b""
        return base64.b64encode(file_data)

    def _write_file(self, file_name: str, file_data: str):
        pass

    def _load_file(self, file_name: str):
        pass

    # ############################################################################## #
    # INTERFACE HELPER METHODS
    # ############################################################################## #

    def _connect(self, *, timeout_s: int = 10) -> None:
        """
        Create ADS client and open connection.
        """
        self._logger.info("Connecting to Beckhoff TwinCAT...")
        if getattr(self, "_connected", False) and self.client is not None:
            return

        # Set local AMS Net ID so pyads knows how to route responses back.
        if self.local_ams_net_id:
            pyads.set_local_address(str(self.local_ams_net_id))

        self.client = ADS(
            ams_net_id=str(self.ams_net_id),
            ams_port=int(self.ams_port),
            ip_address=str(self.address) if self.address else None,
            timeout=float(self.timeout if self.timeout is not None else timeout_s),
            retry=int(self.retry),
            retry_interval=float(self.retry_interval),
            auto_connect=self.auto_connect,
        )

        self._logger.info("Waiting for connection from Beckhoff...")
        rc = self.client.connect()
        self._logger.info("Got connection from Beckhoff!")
        if rc != 0:
            self._connected = False
            raise ConnectionError(
                f"BeckhoffTwinCAT: ADS connect failed (ams_net_id={self.ams_net_id}, "
                f"ams_port={self.ams_port}, ip={self.address})"
            )

        self._logger.info("Connected to Beckhoff TwinCAT!")
        self._connected = True

    def _disconnect(self) -> None:
        """
        Close ADS connection and release client.
        """
        self._logger.info("Disconnecting from Beckhoff TwinCAT...")
        if not getattr(self, "_connected", False):
            if self.client is not None:
                try:
                    self.client.disconnect()
                except Exception:
                    pass
                self.client = None
            return

        try:
            if self.client is not None:
                self.client.disconnect()
        finally:
            self.client = None
            self._connected = False

        self._logger.info("Disconnected from Beckhoff TwinCAT!")

    def _ensure_connected(self) -> None:
        if not self._connected:
            self._connect(timeout_s=10)

    def _open_raw_conn(self):
        """
        Open a raw pyads Connection (bypassing the ADS protocol wrapper).
        Must use the same AMS routing as the ADS protocol client.
        """
        local_ams = self.meta_data.get("local_ams_net_id")
        if local_ams:
            pyads.set_local_address(str(local_ams))
        c = pyads.Connection(str(self.ams_net_id), int(self.ams_port), str(self.address))
        c.set_timeout(int(self.timeout * 1000))
        c.open()
        return c

    def _list_symbols(self, prefix: Optional[str] = None, limit: int = 200) -> List[str]:
        """
        List all symbols in the TwinCAT project, optionally filtered by prefix.
        Opens a fresh raw pyads connection to avoid interfering with the main
        ADS client session.
        """
        c = self._open_raw_conn()
        try:
            syms = c.get_all_symbols()  # works on many pyads versions
            names = [s.name for s in syms]
            if prefix:
                names = [n for n in names if n.startswith(prefix)]
            return names[: max(1, int(limit))]
        finally:
            try:
                c.close()
            except Exception:
                pass

    def _plc_type_from_args(self, plc_type):
        """
        Convert incoming plc_type argument into a pyads PLCTYPE_* constant.

        Accepts:
          - None
          - integer (already a PLCTYPE value)
          - string like "INT", "BOOL", etc.
        """
        if plc_type is None:
            return None

        # If already numeric, assume valid
        if isinstance(plc_type, int):
            return plc_type

        if isinstance(plc_type, str):
            key = plc_type.strip().upper()

            mapping = {
                "BOOL": pyads.PLCTYPE_BOOL,
                "BYTE": pyads.PLCTYPE_BYTE,
                "WORD": pyads.PLCTYPE_WORD,
                "DWORD": pyads.PLCTYPE_DWORD,
                "SINT": pyads.PLCTYPE_SINT,
                "USINT": pyads.PLCTYPE_USINT,
                "INT": pyads.PLCTYPE_INT,
                "UINT": pyads.PLCTYPE_UINT,
                "DINT": pyads.PLCTYPE_DINT,
                "UDINT": pyads.PLCTYPE_UDINT,
                "REAL": pyads.PLCTYPE_REAL,
                "LREAL": pyads.PLCTYPE_LREAL,
                "STRING": pyads.PLCTYPE_STRING,
            }

            return mapping.get(key)

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
        """
        Standardized error response.
        """
        try:
            self._error(message)  # log via AbstractDevice logger
        except Exception:
            pass

        return json.dumps(
            {
                "status": "error",
                "message": str(message),
            },
            default=str,
        )
