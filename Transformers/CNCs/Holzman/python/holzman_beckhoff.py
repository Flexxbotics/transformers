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

from werkzeug.exceptions import HTTPException

from data_models.device import Device
from data_models.part_count_event import PartCountEvent
from data_models.run_record import RunRecord
import json
import base64
from datetime import datetime, timezone
from transformers.abstract_device import AbstractDevice
from exceptions.flexxCoreExceptions import (
    FlexxCoreException,
    BadRequestException,
    ServerErrorException,
    ServiceUnavailableException,
)
from protocols.beckhoff_ads_twincat import ADS
# from protocols.s3 import S3Protocol
import pyads
import time

# --------------------------------------------------------------------------------------
# Holzman Beckhoff driver class
# --------------------------------------------------------------------------------------

class HolzmanBeckhoff(AbstractDevice):

    def __init__(self, device: Device):
        """
        Holzman saw device class: talks to the saw's Beckhoff TwinCAT PLC over ADS
        (a copy of the BeckhoffTwinCAT driver, not a subclass, plus Holzman status,
        program and part-count logic).

        Expects device.metaData to contain (at minimum):
          - ams_net_id: "x.x.x.x.x.x" (TwinCAT AMS Net ID of the target)
          - ip_address: "a.b.c.d" (optional for pyads, but typically provided)
          - ams_port: int (default 801; TwinCAT 2 often 801, TwinCAT 3 often 851)
          - local_ams_net_id: "x.x.x.x.x.x" (AMS Net ID of this host; _connect
            passes it to pyads.set_local_address unconditionally, so connecting
            fails without it)
          - timeout: float seconds (optional)
          - retry: int (optional)
          - retry_interval: float seconds (optional)

        Raises RuntimeError (wrapping the cause) when ams_net_id is missing or a
        value cannot be parsed.
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
            self.auto_connect = self._as_bool(self.meta_data.get("auto_connect", True), True)

            # Beckhoff client (protocol wrapper)
            self.client: ADS | None = None

            # Track state
            self._connected = False
            self._last_programmlaeuft = False

        except Exception as e:
            self._error(message=str(e))
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

        Commands (each returns a JSON string):
          - connect       -> {"status": "ok", "connected": true}
          - disconnect    -> {"status": "ok", "connected": false}
          - read   (reads an ADS symbol)
                          -> {"status": "ok", "symbol": ..., "plc_type": ..., "value": ...}
          - list_symbols  (enumerates the PLC symbols on the target, over a
                          separate raw pyads connection)
                          -> {"status": "ok", "count": n, "symbols": [...]}

        Args are a JSON object, or the same object wrapped as {"value": "<json>"}.

        read command args:
          {
            "symbol": "AchsStatus.Status",   # required
            "plc_type": "INT"                # optional, default "INT"
          }

        list_symbols command args:
          {
            "prefix": ".",   # optional
            "limit": 200      # optional, default 200
          }

        Errors raise: 400 for invalid JSON args, a missing symbol, an
        unsupported plc_type or an unknown command; 503 when the ADS client
        fails to open; 500 otherwise, including ADS errors from a PLC that
        does not answer (pyads raises ADSError, which is not a network error).
        """
        args = self._parse_args(command_args)
        self._info(message=f"Sending command: {command_name}")

        try:
            if command_name == "connect":
                self._connect(timeout_s=10)
                return json.dumps({"status": "ok", "connected": True})

            if command_name == "disconnect":
                self._disconnect()
                return json.dumps({"status": "ok", "connected": False})

            if command_name == "read":
                symbol = self._require(args, "symbol")
                plc_type = args.get("plc_type", "INT")
                plc_type_val = self._plc_type_from_args(plc_type)
                if plc_type_val is None:
                    raise BadRequestException(f"Unsupported plc_type: {plc_type}")
                self._ensure_connected()
                assert self.client is not None
                value = self.client.read(symbol, plc_type=plc_type_val)
                return json.dumps({
                    "status": "ok",
                    "symbol": symbol,
                    "plc_type": plc_type,
                    "value": value
                }, default=str)

            if command_name == "list_symbols":
                prefix = args.get("prefix")
                limit = int(args.get("limit") or 200)
                names = self._list_symbols(prefix=prefix, limit=limit)
                return json.dumps({"status":"ok","count":len(names),"symbols":names}, default=str)

        except Exception as e:
            self._raise_device_error(f"Command '{command_name}'", e)

        raise BadRequestException(f"Unknown command '{command_name}'")

    def _read_interval_data(self) -> str:
        """
        Called on every poll: opens the ADS connection if needed, logs the key
        PLC symbols (_log_key_plc_values) and updates the active program and
        part count (_check_program_and_part_count).  Returns None.  Errors are
        logged; never raises.
        """
        try:
            self._ensure_connected()
            self._log_key_plc_values()
            self._check_program_and_part_count()
        except Exception as e:
            self._error(message=f"Interval read failed: {e}")

    def _check_program_and_part_count(self) -> None:
        """
        Detects the active program number and part count events.

        Active program  : .PNRNM (DINT) — the numeric program ID loaded on the saw.
                          0 or a failed read is recorded as "NOT_REPORTED".  It is
                          written as partNumber to get_run_records()[0] on every
                          call; partCount is reset to 0 when it changes.
        Part count      : falling edge of .PROGRAMMLAEUFT (True → False) signals
                          that a program cycle completed and a part was produced;
                          a PartCountEvent is created for this device.

        Does nothing when there is no ADS client.  Errors are logged, never raised.
        """
        if self.client is None:
            return

        bool_type = self._plc_type_from_args("BOOL")
        dint_type = self._plc_type_from_args("DINT")

        try:
            # --- Active program ---
            try:
                pnrnm = self.client.read(symbol=".PNRNM", plc_type=dint_type)
                active_program = str(pnrnm) if pnrnm else "NOT_REPORTED"
            except Exception:
                active_program = "NOT_REPORTED"

            self._logger.info(f"Active program: {active_program}")

            # Get the most recent run record
            run_record: RunRecord = self._run_record_service.get_run_records()[0]

            # Reset part count on program change
            if run_record.partNumber != active_program:
                run_record.partCount = 0

            # Set the active program on the run record
            if active_program != "NOT_REPORTED":
                run_record.partNumber = active_program
            else:
                run_record.partNumber = "NOT_REPORTED"
            self._run_record_service.update_run_record(run_record=run_record)

            # --- Part count (falling edge: PROGRAMMLAEUFT True → False) ---
            try:
                programmlaeuft = bool(self.client.read(symbol=".PROGRAMMLAEUFT", plc_type=bool_type))
            except Exception:
                programmlaeuft = None

            if programmlaeuft is not None:
                if self._last_programmlaeuft is True and programmlaeuft is False:
                    self._logger.info("Part count detected (PROGRAMMLAEUFT cycle complete)")
                    event: PartCountEvent = PartCountEvent()
                    event.deviceId = self.device_id
                    self._run_record_service.create_event(event=event)
                    self._logger.info("Part count event created")
                self._last_programmlaeuft = programmlaeuft

        except Exception as e:
            self._error(message=f"_check_program_and_part_count failed: {e}")

    def _read_status(self, function: str = None) -> str:
        """
        Read the saw's status from its PLC flags: "FAULT_<label>", "RUNNING" or
        "IDLE".  Returns "MACHINE_NOT_CONNECTED_OR_POWERED_OFF" when the ADS
        connection cannot be opened, and "ERROR" when the PLC does not answer
        (every symbol read in the call failed) or the read fails unexpectedly.
        A single symbol that cannot be read is skipped.  When `function` is
        given, returns "" (nothing to report) once connected, without reading
        any symbol.  Never raises.
        """
        try:
            self._ensure_connected()
        except (ConnectionError, Exception) as e:
            self._error(message=f"Status read failed: {e}")
            return "MACHINE_NOT_CONNECTED_OR_POWERED_OFF"

        if function is not None:
            return ""

        try:
            return self._read_plc_status()
        except Exception as e:
            self._error(message=f"Status read failed: {e}")
            self._connected = False
            return "ERROR"

    def _read_plc_status(self) -> str:
        """
        Status from the PLC symbols (see _read_status).  Raises ConnectionError
        when every symbol read failed, i.e. the PLC is not answering.
        """
        assert self.client is not None

        bool_type = self._plc_type_from_args("BOOL")
        int_type  = self._plc_type_from_args("INT")

        faults = []
        reads_ok = 0  # symbol reads that succeeded; still 0 at the end = PLC not answering

        # ------------------------------------------------------------------ #
        # 1. Boolean fault flags — True = fault active                        #
        # ------------------------------------------------------------------ #

        # DBERROR read separately so we can append the error code
        try:
            if self.client.read(symbol=".DBERROR", plc_type=bool_type):
                msg = "DATABASE_ERROR"
                try:
                    code = self.client.read(symbol=".DBERRORCODE", plc_type=int_type)
                    msg += f"_CODE_{code}"
                except Exception:
                    pass
                faults.append(msg)
            reads_ok += 1
        except Exception:
            pass

        BOOL_FAULT_TAGS = [
            (".ERRORHSANL",                   "HS_SYSTEM_ERROR"),
            (".OWARNUNG",                      "OPERATOR_WARNING"),
            (".SONDERFEHLER",                  "SPECIAL_FAULT"),
            (".E_SPANNUNGSAUSFALL",            "POWER_FAILURE"),
            (".SWPOSSCHLEPPFEHLER",            "SAW_AXIS_FOLLOWING_ERROR"),
            (".SWSCHLEPPFEHLER",               "SAW_FOLLOWING_ERROR"),
            (".PRSPOSSCHLEPPFEHLER",           "PRS_AXIS_FOLLOWING_ERROR"),
            (".GAPOSSCHLEPPFEHLER",            "GA_AXIS_FOLLOWING_ERROR"),
            (".LAWSCHLEPPFEHLER",              "LAW_AXIS_FOLLOWING_ERROR"),
            (".VVSPOSSCHLEPPFEHLER",           "VVS_AXIS_FOLLOWING_ERROR"),
            (".VACPOSSCHLEPPFEHLER",           "VAC_AXIS_FOLLOWING_ERROR"),
            (".VACSCHLEPPFEHLER",              "VAC_FOLLOWING_ERROR"),
            (".DREHVPOSSCHLEPPFEHLER",         "ROTATION_AXIS_FOLLOWING_ERROR"),
            (".ZANGENERROR",                   "CLAMP_GRIPPER_ERROR"),
            (".OERRORTIMEOUT",                 "MOTION_TIMEOUT_ERROR"),
            (".OHTANERROR",                    "STACKER_ERROR"),
            ("VAC2EINS.OERROR",                "VAC2_INSERT_ERROR"),
            ("TBP_200.OZAEHLRICHTUNGSFEHLER",  "TBP200_ENCODER_DIRECTION_ERROR"),
            (".OPRSERRORSOLLOVERMAXLIMIT",      "PRS_POSITION_OVER_MAX_LIMIT"),
            (".OPRSERRORSOLLOVERMINLIMIT",      "PRS_POSITION_UNDER_MIN_LIMIT"),
            (".OROLLERROR",                    "CONVEYOR_ROLL_ERROR"),
            (".E_HSSBLATTVERLAUF",             "HS_BLADE_RUNOUT_FAULT"),
            (".E_HSSBLATTVERLAUF2",            "HS_BLADE_RUNOUT_FAULT_2"),
            (".E_BERTUER1OFFEN",               "SAFETY_DOOR_1_OPEN"),
            (".E_BERTUER2OFFEN",               "SAFETY_DOOR_2_OPEN"),
        ]

        for symbol, label in BOOL_FAULT_TAGS:
            try:
                if self.client.read(symbol=symbol, plc_type=bool_type):
                    faults.append(label)
                reads_ok += 1
            except Exception:
                pass

        # ------------------------------------------------------------------ #
        # 2. Drive ready signals — False = drive tripped/faulted              #
        # ------------------------------------------------------------------ #

        DRIVE_READY_TAGS = [
            (".SW_REGLERBEREIT",   "SAW_DRIVE_NOT_READY"),
            (".PRS_REGLERBEREIT",  "PRS_DRIVE_NOT_READY"),
        ]

        for symbol, label in DRIVE_READY_TAGS:
            try:
                if not self.client.read(symbol=symbol, plc_type=bool_type):
                    faults.append(label)
                reads_ok += 1
            except Exception:
                pass

        # NOTE: KEB inverter status words (KEB_HS/SW/PRS/LKT.UMRICHTERSTATUS) are
        # logged via _log_key_plc_values for observation but NOT used for fault
        # detection here. The bit mapping for each drive's status word needs to be
        # confirmed before using it as a fault trigger — e.g. KEB_LKT consistently
        # reads 0x4F even on a healthy idle machine. Use REGLERBEREIT flags above
        # as the reliable drive-level fault signal instead.

        if faults:
            return "FAULT_" + faults[0]

        # ------------------------------------------------------------------ #
        # 4. Running check                                                    #
        # ------------------------------------------------------------------ #

        try:
            if self.client.read(symbol=".PROGRAMMLAEUFT", plc_type=bool_type):
                return "RUNNING"
            reads_ok += 1
        except Exception:
            pass

        # Fallback: axis status register (0 = idle, non-zero = running)
        try:
            if str(self.client.read(symbol=".ACHSSTATUS", plc_type=int_type)) != "0":
                return "RUNNING"
            reads_ok += 1
        except Exception:
            pass

        if reads_ok == 0:
            # Every symbol read failed: the PLC is not answering, so it is not IDLE.
            raise ConnectionError("no PLC symbol could be read")

        return "IDLE"

    def _read_variable(self, variable_name: str, function: str = None) -> str:
        """
        Read a PLC symbol by name and return its value as a plain string.
        variable_name is the TwinCAT symbol path, e.g. ".PNRNM" or
        "AchsStatus.Status"; its declared type is read from the PLC.
        A None value is returned as "".  `function` is ignored.  Raises on
        failure: 503 when the ADS client fails to open, 500 otherwise
        (including ADS errors from a PLC that does not answer).
        """
        try:
            self._ensure_connected()
            assert self.client is not None
            value = self.client.read(variable_name)
            return str(value) if value is not None else ""
        except Exception as e:
            self._raise_device_error(f"Read variable '{variable_name}'", e)

    def _read_parameter(self, parameter_name: str, function: str = None) -> str:
        return self._read_variable(parameter_name, function)

    def _read_file_names(self) -> list:
        self.programs = []
        return self.programs

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
            raise ServiceUnavailableException(
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
            self._error(message="ADS client not initialized")
            return

        SYMBOLS = [
            # --- Machine state ---
            ".PROGRAMMLAEUFT",
            ".ACHSSTATUS",
            ".PNRNM",
            ".ZEITPROGZYKLSTART",
            ".TIMEPROGENDE",
            ".DBZAEHLER",
            # --- Active program / cut info ---
            ".CADINFO",
            ".CUTINFO",
            ".MPRO1",
            ".MPRO2",
            ".MPRO3",
            ".MPRO5",
            ".MPRO7",
            # --- Fault flags ---
            ".ERRORHSANL",
            ".DBERROR",
            ".DBERRORCODE",
            ".SONDERFEHLER",
            ".OWARNUNG",
            ".ZANGENERROR",
            ".OERRORTIMEOUT",
            ".OHTANERROR",
            "VAC2EINS.OERROR",
            "TBP_200.OZAEHLRICHTUNGSFEHLER",
            ".OPRSERRORSOLLOVERMAXLIMIT",
            ".OPRSERRORSOLLOVERMINLIMIT",
            ".OROLLERROR",
            ".E_SPANNUNGSAUSFALL",
            # --- Blade condition ---
            ".E_HSSBLATTVERLAUF",
            ".E_HSSBLATTVERLAUF2",
            "LS18.OHS1ABW",
            # --- Safety doors ---
            ".E_BERTUER1OFFEN",
            ".E_BERTUER2OFFEN",
            # --- Drive ready signals ---
            ".SW_REGLERBEREIT",
            ".PRS_REGLERBEREIT",
            # --- Servo following errors ---
            ".SWPOSSCHLEPPFEHLER",
            ".SWSCHLEPPFEHLER",
            ".PRSPOSSCHLEPPFEHLER",
            ".GAPOSSCHLEPPFEHLER",
            ".LAWSCHLEPPFEHLER",
            ".VVSPOSSCHLEPPFEHLER",
            ".VACPOSSCHLEPPFEHLER",
            ".VACSCHLEPPFEHLER",
            ".DREHVPOSSCHLEPPFEHLER",
            # --- KEB inverter status & load ---
            "KEB_HS.UMRICHTERSTATUS",
            "KEB_HS.ISTFREQUENZ",
            "KEB_HS.AKTUELLEAUSLASTUNG",
            "KEB_SW.SWUMRICHTERSTATUS",
            "KEB_SW_MATYP180.SWUMRICHTERSTATUS",
            "KEB_PRS.UMRICHTERSTATUS",
            "KEB_PRS.ISTDREHMOMENT",
            "KEB_LKT.UMRICHTERSTATUS",
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
                self._error(message=f"{symbol} read failed: {e}")


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
          - integer (returned unchanged, assumed to be a PLCTYPE value)
          - string like "INT", "BOOL", etc.
        Returns None for None, an unknown name, or any other type.
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

    # ############################################################################## #
    # STANDARD HELPERS (identical in every transformer)
    # ############################################################################## #

    def _execute_command(self, command) -> str:
        """
        Legacy command path, used by Edge HMI control buttons
        (POST /devices/<id>/execute_command). Hands off to _execute_command_v2.
        commandJson may be {"command": "x", "args": {...}} or {"command": "x", <args>}.
        """
        raw = command.get("commandJson") or "{}"
        command_json = json.loads(raw) if isinstance(raw, str) else raw
        args = command_json.get("args")
        if not isinstance(args, dict):
            args = {k: v for k, v in command_json.items() if k != "command"}
        return self._execute_command_v2(command_json.get("command", ""), json.dumps(args))

    @staticmethod
    def _parse_args(command_args) -> dict:
        """
        Parse command_args (the raw request body). Accepts empty, a JSON object, or the
        documented wrapper {"value": "<JSON object string>"}. The wrapper is only unwrapped
        when "value" is the only key, so a real "value" arg is left alone.
        """
        if not command_args:
            return {}
        try:
            args = json.loads(command_args) if isinstance(command_args, (str, bytes)) else command_args
            if isinstance(args, dict) and list(args) == ["value"]:
                inner = args["value"]
                if isinstance(inner, str) and inner.strip().startswith("{"):
                    inner = json.loads(inner)
                if isinstance(inner, dict):
                    return inner
        except ValueError as e:
            raise BadRequestException(f"Command args are not valid JSON: {e}")
        if not isinstance(args, dict):
            raise BadRequestException("Command args must be a JSON object")
        return args

    @staticmethod
    def _require(args: dict, key: str):
        """Return args[key], or raise BadRequestException (HTTP 400) when it is missing or blank."""
        if args.get(key) in (None, ""):
            raise BadRequestException(f"Missing required arg '{key}'")
        return args[key]

    @staticmethod
    def _as_bool(value, default: bool = False) -> bool:
        """metaData / arg booleans: accepts True/False, "true"/"false" (any case), "1"/"0", "yes"/"no"."""
        if isinstance(value, bool):
            return value
        if value is None or str(value).strip() == "":
            return default
        return str(value).strip().lower() in ("1", "true", "yes", "on")

    def _raise_device_error(self, action: str, error: Exception):
        """
        Re-raise a failed device call as the runtime exception the API turns into an HTTP error:
        runtime/HTTP exceptions pass through unchanged; network errors -> 503 (and the transformer
        is marked disconnected so the next call reconnects); anything else -> 500.
        """
        if isinstance(error, (FlexxCoreException, HTTPException)):
            raise error
        if isinstance(error, (OSError, TimeoutError, ConnectionError)):
            self._connected = False
            raise ServiceUnavailableException(f"{action}: device did not respond ({error})") from error
        raise ServerErrorException(f"{action} failed: {error}") from error
