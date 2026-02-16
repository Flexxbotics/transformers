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

from data_models.device import Device
import json
import base64
from datetime import datetime, timezone
from transformers.abstract_device import AbstractDevice
from protocols.beckhoff_ads_twincat import ADS
from protocols.s3 import S3Protocol
import pyads
import time

# --------------------------------------------------------------------------------------
# Holzman Beckhoff driver class
# --------------------------------------------------------------------------------------

class HolzmanBeckhoff(AbstractDevice):

    def __init__(self, device: Device):
        """
        Beckhoff ADS/TwinCAT device class.

        Expects device.metaData to contain (at minimum):
          - ams_net_id: "x.x.x.x.x.x" (TwinCAT AMS Net ID of the target)
          - ip_address: "a.b.c.d" (optional for pyads, but typically provided)
          - port / ams_port: int (TwinCAT 2 often 801; TwinCAT 3 often 851)
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
            self.ams_port = int(self.meta_data.get("ams_port", 801))
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
            raise RuntimeError(f"HolzmanBeckhoff init failed: {e}") from e

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
          - read   (reads an ADS symbol)

        read command args (JSON inside args["value"]):
          {
            "symbol": "AchsStatus.Status",
            "plc_type": null
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
            
            if command_name == "list_symbols":
                prefix = args.get("prefix")
                limit = int(args.get("limit", 200))
                names = self._list_symbols(prefix=prefix, limit=limit)
                return json.dumps({"status":"ok","count":len(names),"symbols":names}, default=str)


            # ---- Unknown command ----
            return "UNKNOWN_COMMAND"

        except Exception as e:
            self._logger.error(str(e))
            return str(e)

    def _read_interval_data(self) -> str:
        """
        Method to read the status of the device on an interval
        """
        self._ensure_connected()
        self._log_key_plc_values()

    def _read_status(self, function: str = None) -> str:
        status = ""
        self._ensure_connected()
        assert self.client is not None
        if function is None:
            status = self.client.read(symbol=".ACHSSTATUS", plc_type=self._plc_type_from_args("INT"))
            if str(status) == "0":
                return "IDLE"
            if str(status) != "0":
                return "RUNNING"
                
        elif function == "":
            pass
        else:
            pass
        return status

    def _read_variable(self, variable_name: str, function: str = None) -> str:
        value = ""
        if function is None:
            response = self._execute_command_v2(command_name="read", command_args='{"value": {"symbol": "'+variable_name+'"}')
            return response.get("value", "ERROR")

        elif function == "":
            pass
        else:
            pass
        return value

    def _write_variable(self, variable_name: str, variable_value: str, function: str = None) -> str:
        value = ""
        if function is None:
            pass
        elif function == "":
            pass
        else:
            pass
        return value

    def _write_parameter(self, parameter_name: str, parameter_value: str, function: str = None) -> str:
        value = ""
        if function is None:
            pass
        elif function == "":
            pass
        else:
            pass
        return value

    def _read_parameter(self, parameter_name: str, function: str = None) -> str:
        value = ""
        if function is None:
            pass
        elif function == "":
            pass
        else:
            pass
        return value

    def _read_file_names(self) -> list:
        self.programs = []
        return self.programs

    def _read_file(self, file_name: str) -> str:
        file_data = ""
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
        self._logger.info("Connecting to Holzman via Beckhoff...")
        if getattr(self, "_connected", False) and self.client is not None:
            return
        
        pyads.set_local_address(self.local_ams_net_id)

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
                f"HolzmanBeckhoff: ADS connect failed (ams_net_id={self.ams_net_id}, "
                f"ams_port={self.ams_port}, ip={self.address})"
            )

        self._logger.info("Connected to Holzman via Beckhoff!")
        self._connected = True

    def _disconnect(self) -> None:
        """
        Close ADS connection and release client.
        """
        self._logger.info("Disconnecting from Holzman via Beckhoff...")
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
        
        self._logger.info("Disconnected from Holzman via Beckhoff!")

    def _ensure_connected(self) -> None:
        if not self._connected:
            self._connect(timeout_s=10)

    def _log_key_plc_values(self) -> None:
        """
        Reads important PLC variables and logs their values.
        """

        self._ensure_connected()

        if self.client is None:
            self._logger.error("ADS client not initialized")
            return

        SYMBOLS = [
            ".PROGRAMMLAEUFT",
            ".ERRORHSANL",
            ".DBERROR",
            ".DBERRORCODE",
            ".SONDERFEHLER",
            ".OWARNUNG",
            ".MANUELL",
            ".PNRNM",
            ".CADINFO",
            ".CUTINFO",
            ".MPRO1",
            ".MPRO2",
            ".MPRO3",
            ".MPRO5",
            ".MPRO7",
            ".DBZAEHLER",
            ".TIMEPROGENDE",
            ".ZEITPROGZYKLSTART",
        ]

        for symbol in SYMBOLS:
            try:
                sym_obj = self.client._ADS__conn.get_symbol(symbol)  # access pyads symbol

                plc_type = sym_obj.plc_type

                # -----------------------------
                # Primitive types
                # -----------------------------
                if plc_type in (None,):
                    # For unknown / struct types
                    value = self.client.read(symbol)

                elif plc_type.__name__.startswith("c_bool_Array"):
                    # Convert array of bools to Python list
                    raw = self.client.read(symbol)
                    value = list(raw)

                else:
                    # Normal primitive
                    value = self.client.read(symbol, plc_type)

                self._logger.info(f"{symbol} = {value}")

            except Exception as e:
                self._logger.error(f"{symbol} read failed: {e}")


    def _open_raw_conn(self):
        # must match what you use for ADS protocol
        local_ams = self.meta_data.get("local_ams_net_id")
        if local_ams:
            pyads.set_local_address(str(local_ams))
        c = pyads.Connection(str(self.ams_net_id), int(self.ams_port), str(self.address))
        c.set_timeout(int(self.timeout * 1000))
        c.open()
        return c

    def _list_symbols(self, prefix: Optional[str] = None, limit: int = 200) -> List[str]:
        c = self._open_raw_conn()
        try:
            syms = c.get_all_symbols()   # works on many pyads versions
            names = [s.name for s in syms]
            if prefix:
                names = [n for n in names if n.startswith(prefix)]
            return names[: max(1, int(limit))]
        finally:
            try: c.close()
            except Exception: pass

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
            try:
                import pyads
            except Exception:
                return None
            
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