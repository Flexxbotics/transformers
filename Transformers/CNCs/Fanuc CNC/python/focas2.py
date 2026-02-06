"""
    Copyright 2022–2024 Flexxbotics, Inc.

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
import os
import ctypes
import bisect
from ctypes import (
    c_short,
    c_ushort,
    c_long,
    c_ulong,
    c_int,
    c_char,
    c_char_p,
    c_float,
    c_double,
    POINTER,
    byref,
)
import shutil
import threading
import time
from transformers.abstract_device import AbstractDevice
import csv
from datetime import datetime, timezone

import queue
from collections import deque


# -------------------------------
# ctypes models
# -------------------------------

class _ODBST(ctypes.Structure):
    _fields_ = [
        ("hdck", c_short),
        ("tmmode", c_short),
        ("aut", c_short),
        ("run", c_short),
        ("motion", c_short),
        ("mstb", c_short),
        ("emergency", c_short),
        ("alarm", c_short),
        ("edit", c_short),
    ]


class _ODBM(ctypes.Structure):
    _fields_ = [
        ("datano", c_short),
        ("dummy", c_short),
        ("mcr_val", c_int),
        ("dec_val", c_short),
    ]
Name4 = c_char * 4  # char name[4]

class _ODBAXDT(ctypes.Structure):
    _fields_ = [
        ("name", c_char * 4),          # char name[4]
        ("data", ctypes.c_int32),      # <-- IMPORTANT (4 bytes)
        ("dec",  c_short),
        ("unit", c_short),
        ("flag", c_short),
        ("reserve", c_short),
    ]

    def _axis_name(self) -> str:
        return bytes(self.name).split(b"\x00", 1)[0].decode("ascii", "replace")

    def value(self) -> float:
        raw = int(self.data)
        dec = int(self.dec)
        # guard against nonsense decimals
        if dec < 0 or dec > 9:
            return float(raw)
        return raw / (10 ** dec) if dec else float(raw)

class _IODBPMCUnion(ctypes.Union):
    _fields_ = [
        ("cdata", c_char),
        ("idata", c_short),
        ("ldata", ctypes.c_int32),
        ("fdata", c_float),
        ("dfdata", c_double),
    ]


    def get_by_dtype(self, data_type: int):
        # data_type mapping: 0=char, 1=short, 2=long, 3=float, 4=double
        if data_type == 0:
            return self.cdata
        if data_type == 1:
            return self.idata
        if data_type == 2:
            return self.ldata
        if data_type == 3:
            return self.fdata
        if data_type == 4:
            return self.dfdata
        raise ValueError(f"Unsupported data_type={data_type}")

    def set_by_dtype(self, data_type: int, value):
        if data_type == 0:
            self.cdata = c_char(int(value))
        elif data_type == 1:
            self.idata = c_short(int(value))
        elif data_type == 2:
            self.ldata = ctypes.c_int32(int(value))
        elif data_type == 3:
            self.fdata = c_float(float(value))
        elif data_type == 4:
            self.dfdata = c_double(float(value))
        else:
            raise ValueError(f"Unsupported data_type={data_type}")


class _IODBPMC(ctypes.Structure):
    _fields_ = [
        ("type_a", c_short),
        ("type_d", c_short),
        ("datano_s", c_short),
        ("datano_e", c_short),
        ("u", _IODBPMCUnion),
    ]


class _PRGDIR(ctypes.Structure):
    _fields_ = [("prg_data", c_char * 8192)]


# --------------------------------------------------------------------------------------
# FOCAS2 driver class
# --------------------------------------------------------------------------------------

class FOCAS2(AbstractDevice):

    def __init__(self, device: Device):
        """
        Template device class. Inherits AbstractDevice class.

        :param Device:
                    the device object

        :return:    a new instance
        """
        try:
            super().__init__(device)
            # Get meta data of the device from its attributes, this contains information such as: ip address, ports, etc
            self.meta_data = device.metaData
            self.address = self.meta_data["ip_address"]
            self.port = int(self.meta_data.get("port", 8193))

            # FOCAS state
            self._fwlib = None
            self._handle = c_ushort(0)
            self._connected = False

            # Load the correct shared library for the platform (linux/amd64 in your Docker build)
            self._load_fwlib()
            self._define_fwlib_prototypes()

            self._rec_thread = None
            self._rec_stop_event = threading.Event()
            self._rec_lock = threading.Lock()
            self._rec_samples = []
            self._rec_config = {}

            self._spc_queue = queue.Queue(maxsize=10000)
            self._spc_thread = None
            self._spc_stop_event = threading.Event()
            self._spc_events = []  # list of {"ts":..., "event":..., "detail":...}
            self._spc_lock = threading.Lock()

        except Exception as e:
            self._error(str(e))
            raise RuntimeError(f"FOCAS2 init failed: {e}") from e

    def __del__(self):
        try:
            if getattr(self, "_connected", False):
                self._disconnect()
        except Exception:
            # Never raise in destructor
            pass

    # ############################################################################## #
    # DEVICE COMMUNICATION METHODS
    # ############################################################################## #

    def _execute_command_v2(self, command_name: str, command_args: str) -> str:
        """
        Executes the command sent to the device.

        :param command_name:
                    the command to be executed
        :param command_args:
                    json with the arguments for the command

        :return:    the response after execution of command.
        """
        # Parse the command from the incoming request
        args = json.loads(command_args) if command_args else {}
        args = json.loads(args["value"])
        try:
            # ---- Connection management ----
            if command_name == "connect":
                timeout_s = int(args.get("timeout_s", 10))
                self._connect(timeout_s=timeout_s)
                return self._ok()

            if command_name == "disconnect":
                self._disconnect()
                return self._ok()

            # ---- Status / wait ----
            if command_name == "read_status":
                # Returns a dict of common status fields.
                self._ensure_connected()
                status = self._read_cnc_status()
                return str(status["run"])

            if command_name == "read_status_field":
                # args: {"field": "run"}  (field in status dict)
                self._ensure_connected()
                field = args.get("field")
                if not field:
                    return self._err("Missing required arg: field")
                status = self._read_cnc_status()
                if field not in status:
                    return self._err(f"Unknown status field: {field}")
                return str(status[field])

            if command_name == "wait_for_cnc":
                # Wait until run==0 OR alarm==1 OR emergency==1
                # args: {"poll_s": 0.4, "timeout_s": 43200}
                import time

                self._ensure_connected()
                poll_s = float(args.get("poll_s", 0.4))
                timeout_s = int(args.get("timeout_s", 43200))
                start = time.time()

                while True:
                    status = self._read_cnc_status()
                    if status.get("alarm", 0) == 1:
                        return self._err("CNC alarm detected", data=status)
                    if status.get("emergency", 0) == 1:
                        return self._err("E-stop detected", data=status)
                    if status.get("run", 0) == 0:
                        return self._ok(status)

                    if (time.time() - start) >= timeout_s:
                        return self._err("Timed out waiting for CNC", data=status)

                    time.sleep(poll_s)

            # ---- Macro read/write ----
            if command_name == "read_macro":
                # args: {"macro": 651, "length": 10}
                self._ensure_connected()
                mcr_number = args.get("macro")
                if mcr_number is None:
                    return self._err("Missing required arg: macro")
                length = int(args.get("length", 10))
                mcr_val, dec_val, joined = self._read_macro(int(mcr_number), length=length)
                return str(joined)

            if command_name == "write_macro":
                # args: {"macro": 651, "mcr_val": 123, "dec_val": 2}
                self._ensure_connected()
                mcr_number = args.get("macro")
                mcr_val = args.get("mcr_val")
                dec_val = args.get("dec_val", 0)
                if mcr_number is None or mcr_val is None:
                    return self._err("Missing required args: macro, mcr_val (and optional dec_val)")
                self._write_macro(int(mcr_number), int(mcr_val), int(dec_val))
                return self._ok()

            # ---- PMC read/write ----
            if command_name == "read_pmc_range":
                # args: {"section": 0, "data_type": 2, "start": 0, "end": 0, "length": 12}
                self._ensure_connected()
                required = ["section", "data_type", "start", "end", "length"]
                missing = [k for k in required if k not in args]
                if missing:
                    return self._err(f"Missing required args: {', '.join(missing)}")
                val = self._read_pmc_range(
                    section=int(args["section"]),
                    data_type=int(args["data_type"]),
                    start=int(args["start"]),
                    end=int(args["end"]),
                    length=int(args["length"]),
                )
                # ensure JSON serializable
                return str(val)

            if command_name == "write_pmc_range":
                # args: {"length": 12, "section": 0, "data_type": 2, "start": 0, "end": 0, "value": 123}
                self._ensure_connected()
                required = ["length", "section", "data_type", "start", "end", "value"]
                missing = [k for k in required if k not in args]
                if missing:
                    return self._err(f"Missing required args: {', '.join(missing)}")
                self._write_pmc_range(
                    length=int(args["length"]),
                    section=int(args["section"]),
                    data_type=int(args["data_type"]),
                    start=int(args["start"]),
                    end=int(args["end"]),
                    value=args["value"],
                )
                return self._ok()

            # ---- Program file operations (directory + upload/download) ----
            if command_name == "get_filenames":
                # args (optional): {"directory": "//CNC_MEM/USER/PATH1/PART_PROGRAMS"}
                self._ensure_connected()
                directory = args.get("directory", "//CNC_MEM/USER/PATH1/PART_PROGRAMS")
                # Keep behaviour consistent with the legacy driver: set dir before listing
                self._set_current_directory(directory)
                names = self._get_filenames()
                return self._ok(names)

            if command_name == "get_current_directory":
                self._ensure_connected()
                d = self._get_current_directory()
                return str(d)

            if command_name == "set_current_directory":
                # args: {"directory": "//CNC_MEM/USER/PATH1/PART_PROGRAMS"}
                self._ensure_connected()
                directory = args.get("directory")
                if not directory:
                    return self._err("Missing required arg: directory")
                self._set_current_directory(str(directory))
                return self._ok()

            if command_name == "program_upload":
                # args: {"program_path": "//CNC_MEM/USER/PATH1/PART_PROGRAMS/O0010", "buffer_size": 1024}
                self._ensure_connected()
                program_path = args.get("program_path")
                if not program_path:
                    return self._err("Missing required arg: program_path")
                buffer_size = int(args.get("buffer_size", 1024))
                b64 = self._program_upload(str(program_path), buffer_size=buffer_size)
                return b64

            if command_name == "program_download":
                # args: {"program_path": "//CNC_MEM/USER/PATH1/PART_PROGRAMS/O0010", "program_data_b64": "..."}
                self._ensure_connected()
                program_path = args.get("program_path")
                program_data_b64 = args.get("program_data_base64")
                if not program_path or not program_data_b64:
                    return self._err("Missing required args: program_path, program_data_b64")
                raw = base64.b64decode(program_data_b64)
                self._program_download(str(program_path), raw)
                return self._ok()

            # ---- Axis position read ----
            if command_name == "read_axis_data":
                # args (optional):
                # {
                #   "cls": 1,
                #   "types": [0,1,2,3],
                #   "max_axis": 32
                # }
                self._ensure_connected()

                cls = int(args.get("cls", 1))
                types = args.get("types", [0, 1, 2, 3])
                max_axis = args.get("max_axis")  # allow None

                # validate types is list of ints
                try:
                    types = tuple(int(t) for t in types)
                except Exception:
                    return self._err("types must be a list of integers, e.g. [0,1,2,3]")

                try:
                    data = self._read_axis_data(cls=cls, types=types,
                                                max_axis=(int(max_axis) if max_axis is not None else None))
                    return self._ok(data)
                except Exception as e:
                    return self._err(f"read_axis_data failed: {e}")

            if command_name == "start_recording":
                # args example: {"axis":"X","pos_type":0,"poll_s":0.25}
                self._ensure_connected()
                axis = str(args.get("axis", "X"))
                pos_type = int(args.get("pos_type", 0))
                poll_s = float(args.get("poll_s", 0.25))
                self._start_recording(axis=axis, pos_type=pos_type, poll_s=poll_s)
                return self._ok({"recording": True, "axis": axis, "pos_type": pos_type, "poll_s": poll_s})

            if command_name == "stop_recording":
                samples = self._stop_recording()
                return self._ok({"recording": False, "samples": samples, "count": len(samples)})

            if command_name == "get_recording_data":
                data = self._get_recording_data()
                return self._ok({


                    "recording": self._rec_thread is not None and self._rec_thread.is_alive(),
                    "count": len(data),
                    "samples": data,
                })

            if command_name == "save_recording_csv":
                # args optional: {"file_path": "/tmp/myfile.csv"}
                file_path = args.get("file_path")
                try:
                    written = self._save_recording_csv(file_path=str(file_path) if file_path else None)
                    return self._ok({"file_path": written})
                except Exception as e:
                    return self._err(f"save_recording_csv failed: {e}")

            if command_name == "get_spc_events":
                with self._spc_lock:
                    ev = list(self._spc_events)
                return self._ok({"count": len(ev), "events": ev[-50:]})

            # ---- Unknown command ----
            return self._err(f"Unknown command_name: {command_name}")

        except Exception as e:
            # Convert any exception into an error response string
            return self._err(str(e))

    def _read_interval_data(self) -> str:
        """
        Method to read the status of the device on an interval
        """
        pass

    def _read_status(self, function: str = None) -> str:
        status = ""
        if function is None:
            status = self._execute_command_v2(command_name="read_status", command_args='{"value": "{}"}')
        elif function == "":  # Some string
            pass
        else:
            pass
        return status

    def _read_variable(self, variable_name: str, function: str = None) -> str:
        value = ""
        if function is None:
            value_dict = {"macro": variable_name}
            args = {
                "value": json.dumps(value_dict)
            }
            return self._execute_command_v2(
                command_name="read_macro",
                command_args=json.dumps(args)
            )
        elif function == "":  # Some string
            pass
        else:
            pass
        return value

    def _write_variable(self, variable_name: str, variable_value: str, function: str = None) -> str:
        value = ""
        if function is None:
            value_dict = {"macro": variable_name,
                          "mcr_val": variable_value,
                          "dec_val": 0}
            args = {
                "value": json.dumps(value_dict)
            }
            self._execute_command_v2(
                command_name="write_macro",
                command_args=json.dumps(args)
            )

            value_dict = {"macro": variable_name}
            args = {
                "value": json.dumps(value_dict)
            }
            return self._execute_command_v2(
                command_name="read_macro",
                command_args=json.dumps(args)
            )
        elif function == "":  # Some string
            pass
        else:
            pass
        return value

    def _write_parameter(self, parameter_name: str, parameter_value: str, function: str = None) -> str:
        value = ""
        if function is None:
            pass
        elif function == "":  # Some string
            pass
        else:
            pass
        return value

    def _read_parameter(self, parameter_name: str, function: str = None) -> str:
        value = ""
        if function is None:
            pass
        elif function == "":  # Some string
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

    def _load_fwlib(self) -> None:
        """
        Loads the FANUC fwlib shared library via ctypes.

        For linux/amd64 Docker builds, this should be the x64.
        """
        data_dir = "/app/dlls"
        so_link = os.path.join(data_dir, "libfwlib32.so")
        so_versioned = os.path.join(data_dir, "libfwlib32-linux-x64.so.1.0.5")

        # Ensure directory is on loader path (helps dependency resolution)
        os.environ["LD_LIBRARY_PATH"] = (
            f"{data_dir}:" + os.environ.get("LD_LIBRARY_PATH", "")
            if os.environ.get("LD_LIBRARY_PATH") else data_dir
        )

        # Perform linkage: create/refresh libfwlib32.so symlink
        if not os.path.exists(so_link):
            if os.path.exists(so_versioned):
                try:
                    os.symlink(so_versioned, so_link)
                except FileExistsError:
                    pass
                except OSError:
                    # Fallback: copy if symlinks are not allowed
                    try:
                        shutil.copy2(so_versioned, so_link)
                    except Exception:
                        pass

        # Attempt to load (prefer stable name)
        candidates = [so_link, so_versioned]
        last_err = None

        for path in candidates:
            if not os.path.exists(path):
                continue
            try:
                self._fwlib = ctypes.CDLL(path, mode=ctypes.RTLD_GLOBAL)
                self._fwlib_path = path
                return
            except OSError as e:
                last_err = e

        raise FileNotFoundError(
            "Unable to load FANUC FOCAS fwlib shared library from /app/dlls/. "
            "Ensure libfwlib32-linux-x64.so.1.0.5 is present and matches linux/amd64. "
            f"Tried: {candidates}. "
            f"Last error: {last_err}"
        )

    def _define_fwlib_prototypes(self) -> None:
        """
        Define only the fwlib entrypoints we use in _execute_command_v2.
        """
        fw = self._fwlib

        # cnc_startupprocess(ushort, char*) -> short
        fw.cnc_startupprocess.argtypes = [c_ushort, c_char_p]
        fw.cnc_startupprocess.restype = c_short

        # cnc_allclibhndl3(char*, ushort, long, ushort*) -> short
        fw.cnc_allclibhndl3.argtypes = [c_char_p, c_ushort, c_long, POINTER(c_ushort)]
        fw.cnc_allclibhndl3.restype = c_short

        # cnc_freelibhndl(ushort) -> short
        fw.cnc_freelibhndl.argtypes = [c_ushort]
        fw.cnc_freelibhndl.restype = c_short

        # cnc_statinfo(ushort, ODBST*) -> short
        fw.cnc_statinfo.argtypes = [c_ushort, POINTER(_ODBST)]
        fw.cnc_statinfo.restype = c_short

        # pmc_rdpmcrng(ushort, short, short, ushort, ushort, ushort, IODBPMC*) -> short
        fw.pmc_rdpmcrng.argtypes = [c_ushort, c_short, c_short, c_ushort, c_ushort, c_ushort, POINTER(_IODBPMC)]
        fw.pmc_rdpmcrng.restype = c_short

        # pmc_wrpmcrng(ushort, short, IODBPMC*) -> short
        fw.pmc_wrpmcrng.argtypes = [c_ushort, c_short, POINTER(_IODBPMC)]
        fw.pmc_wrpmcrng.restype = c_short

        # cnc_rdmacro(ushort, short, short, ODBM*) -> short
        fw.cnc_rdmacro.argtypes = [c_ushort, c_short, c_short, POINTER(_ODBM)]
        fw.cnc_rdmacro.restype = c_short

        # cnc_wrmacro(ushort, short, short, long, short) -> short
        fw.cnc_wrmacro.argtypes = [c_ushort, c_short, c_short, c_long, c_short]
        fw.cnc_wrmacro.restype = c_short

        # Program / dir APIs used by the legacy driver:
        fw.cnc_upstart4.argtypes = [c_ushort, c_short, c_char_p]
        fw.cnc_upstart4.restype = c_short

        fw.cnc_upload4.argtypes = [c_ushort, POINTER(c_long), c_char_p]
        fw.cnc_upload4.restype = c_short

        fw.cnc_upend4.argtypes = [c_ushort]
        fw.cnc_upend4.restype = c_short

        fw.cnc_dwnstart4.argtypes = [c_ushort, c_short, c_char_p]
        fw.cnc_dwnstart4.restype = c_short

        fw.cnc_download4.argtypes = [c_ushort, POINTER(c_long), c_char_p]
        fw.cnc_download4.restype = c_short

        fw.cnc_dwnend4.argtypes = [c_ushort]
        fw.cnc_dwnend4.restype = c_short

        fw.cnc_wrpdf_curdir.argtypes = [c_ushort, c_short, c_char_p]
        fw.cnc_wrpdf_curdir.restype = c_short

        fw.cnc_rdpdf_curdir.argtypes = [c_ushort, c_short, c_char_p]
        fw.cnc_rdpdf_curdir.restype = c_short

        fw.cnc_rdprogdir.argtypes = [c_ushort, c_short, c_long, c_long, c_ushort, POINTER(_PRGDIR)]
        fw.cnc_rdprogdir.restype = c_short

    def _ok(self, data=None) -> str:
        payload = {"success": True}
        if data is not None:
            payload["data"] = data
        return json.dumps(payload)

    def _err(self, message: str, *, data=None, code=None) -> str:
        payload = {"success": False, "error": message}
        if code is not None:
            payload["code"] = code
        if data is not None:
            payload["data"] = data
        return json.dumps(payload)

    def _ret_check(self, ret: int, function: str) -> None:
        # In FOCAS, 0 is generally EW_OK.
        if int(ret) != 0:
            raise Exception(f"Failed - {function}. Error code: {int(ret)}")

    # --------------------------------------------------------------------------------------
    # Private: connection + primitives
    # --------------------------------------------------------------------------------------

    def _connect(self, *, timeout_s: int = 10) -> None:
        # Create log file if supported. Use a stable location in container.
        # If FANUC fwlib doesn't support logging on a given build, it will return an error code.
        try:
            ret_log = self._fwlib.cnc_startupprocess(0, b"focas.log")
            # Treat non-zero as non-fatal for startup log creation (some builds may not support)
            # but still allow connect to proceed.
        except Exception:
            pass

        # Free previous handle if present
        if self._connected and int(self._handle.value) != 0:
            try:
                self._fwlib.cnc_freelibhndl(self._handle)
            except Exception:
                pass

        self._handle = c_ushort(0)
        ret = self._fwlib.cnc_allclibhndl3(
            self.address.encode("utf-8"),
            c_ushort(self.port),
            c_long(timeout_s),
            byref(self._handle),
        )
        if ret != 0:
            detail = self._get_detail_error(handle.value)
            raise RuntimeError(f"cnc_allclibhndl3 failed rc={rc}, detail={detail}")
        self._ret_check(ret, "cnc_allclibhndl3")
        self._connected = True

    def _disconnect(self) -> None:
        if not self._connected:
            return
        ret = self._fwlib.cnc_freelibhndl(self._handle)
        self._ret_check(ret, "cnc_freelibhndl")
        self._connected = False
        self._handle = c_ushort(0)

    def _ensure_connected(self) -> None:
        if not self._connected:
            # Auto-connect to match typical driver expectations.
            self._connect(timeout_s=10)

    def _read_cnc_status(self) -> dict:
        self._connect()
        stat = _ODBST()
        ret = self._fwlib.cnc_statinfo(self._handle, byref(stat))
        self._ret_check(ret, "cnc_statinfo")
        self._disconnect()
        return {
            "hdck": int(stat.hdck),
            "tmmode": int(stat.tmmode),
            "aut": int(stat.aut),
            "run": int(stat.run),
            "motion": int(stat.motion),
            "mstb": int(stat.mstb),
            "emergency": int(stat.emergency),
            "alarm": int(stat.alarm),
            "edit": int(stat.edit),
        }

    def _read_macro(self, mcr_number: int, *, length: int = 10):
        self._connect()
        md = _ODBM()
        ret = self._fwlib.cnc_rdmacro(self._handle, c_short(mcr_number), c_short(length), byref(md))
        self._ret_check(ret, "cnc_rdmacro")

        mcr_val = int(md.mcr_val)
        dec_val = int(md.dec_val)
        joined = self._join_decimal(mcr_val, dec_val)
        self._disconnect()
        return mcr_val, dec_val, joined

    def _write_macro(self, mcr_number: int, mcr_val: int, dec_val: int):
        self._connect()
        ret = self._fwlib.cnc_wrmacro(self._handle, c_short(mcr_number), c_short(10), c_long(mcr_val), c_short(dec_val))
        self._disconnect()
        self._ret_check(ret, "cnc_wrmacro")

    def _join_decimal(self, mcr_val: int, dec_val: int):
        # FANUC macros often represent value as (mcr_val * 10^-dec_val).
        try:
            if dec_val <= 0:
                return float(mcr_val)
            return float(mcr_val) / (10 ** dec_val)
        except Exception:
            # Fallback to a string-ish representation if something unexpected happens.
            return f"{mcr_val}e-{dec_val}"

    def _read_pmc_range(self, *, section: int, data_type: int, start: int, end: int, length: int):
        self._connect()
        pmc_data = _IODBPMC()
        ret = self._fwlib.pmc_rdpmcrng(
            self._handle,
            c_short(section),
            c_short(data_type),
            c_ushort(start),
            c_ushort(end),
            c_ushort(length),
            byref(pmc_data),
        )
        self._ret_check(ret, "pmc_rdpmcrng")
        self._disconnect()
        return pmc_data.u.get_by_dtype(int(data_type))

    def _write_pmc_range(self, *, length: int, section: int, data_type: int, start: int, end: int, value):
        self._connect()
        u = _IODBPMCUnion()
        u.set_by_dtype(int(data_type), value)
        pmc_data = _IODBPMC(c_short(section), c_short(data_type), c_short(start), c_short(end), u)
        ret = self._fwlib.pmc_wrpmcrng(self._handle, c_short(length), byref(pmc_data))
        self._ret_check(ret, "pmc_wrpmcrng")
        self._disconnect()

    def _get_filenames(self):
        # Mirrors legacy behaviour: cnc_rdprogdir returns a blob of directory data in prg_data.
        self._connect()
        dir_data = _PRGDIR()
        ret = self._fwlib.cnc_rdprogdir(self._handle, c_short(1), c_long(1), c_long(9999), c_ushort(256000), byref(dir_data))
        self._ret_check(ret, "cnc_rdprogdir")
        decoded = bytes(dir_data.prg_data).decode("utf-8", errors="ignore")
        # Legacy driver drops leading/trailing '%' and splits on newline.
        decoded = decoded.strip("\x00")
        if decoded.startswith("%"):
            decoded = decoded[1:]
        if decoded.endswith("%"):
            decoded = decoded[:-1]
        names = [line for line in decoded.split("\n") if line.strip()]
        self._disconnect()
        return names

    def _get_current_directory(self) -> str:
        self._connect()
        buf = ctypes.create_string_buffer(4096)
        ret = self._fwlib.cnc_rdpdf_curdir(self._handle, c_short(1), buf)
        self._ret_check(ret, "cnc_rdpdf_curdir")
        self._disconnect()
        return buf.value.decode("utf-8", errors="ignore")

    def _set_current_directory(self, directory: str) -> None:
        self._connect()
        buf = ctypes.create_string_buffer(directory.encode("utf-8"))
        ret = self._fwlib.cnc_wrpdf_curdir(self._handle, c_short(1), buf)
        self._ret_check(ret, "cnc_wrpdf_curdir")
        self._disconnect()

    def _program_upload(self, program_path: str, *, buffer_size: int = 1024) -> str:
        import time

        self._connect()
        # Ensure any prior upload is ended
        try:
            self._fwlib.cnc_upend4(self._handle)
        except Exception:
            pass

        ret = self._fwlib.cnc_upstart4(self._handle, c_short(0), c_char_p(program_path.encode("utf-8")))
        self._ret_check(ret, "cnc_upstart4")

        chunks = []
        while True:
            size = c_long(buffer_size)
            # FANUC examples often allocate large buffers; we keep it manageable.
            buf = ctypes.create_string_buffer(buffer_size)
            time.sleep(0.05)
            ret_u = self._fwlib.cnc_upload4(self._handle, byref(size), buf)
            # EW_BUFFER or non-zero can indicate end-of-data; we still append any received bytes.
            raw = buf.raw[: int(size.value)] if int(size.value) > 0 else b""
            if raw:
                chunks.append(raw)
            if int(ret_u) != 0:
                break
            # Some controllers include '%' terminator; stop if seen
            if raw and b"%" in raw:
                break

        ret_end = self._fwlib.cnc_upend4(self._handle)
        self._ret_check(ret_end, "cnc_upend4")

        data = b"".join(chunks)
        # Trim null padding
        data = data.rstrip(b"\x00")
        self._disconnect()

        return base64.b64encode(data).decode("ascii")

    def _program_download(self, program_path: str, program_data: bytes, *, max_chunk: int = 1400) -> None:
        self._connect()
        # Ensure any prior download is ended
        try:
            self._fwlib.cnc_dwnend4(self._handle)
        except Exception:
            pass

        ret = self._fwlib.cnc_dwnstart4(self._handle, c_short(0), c_char_p(program_path.encode("utf-8")))
        self._ret_check(ret, "cnc_dwnstart4")

        # The legacy driver chunks the payload to 1400 bytes.
        for i in range(0, len(program_data), max_chunk):
            chunk = program_data[i : i + max_chunk]
            size = c_long(len(chunk))
            # ctypes wants a char*; create_string_buffer produces mutable char[].
            buf = ctypes.create_string_buffer(chunk)
            ret_d = self._fwlib.cnc_download4(self._handle, byref(size), buf)
            self._ret_check(ret_d, "cnc_download4")

        ret_end = self._fwlib.cnc_dwnend4(self._handle)
        self._ret_check(ret_end, "cnc_dwnend4")
        self._disconnect()

    def _read_axis_data(self, cls: int = 1, types=(0, 1, 2, 3), max_axis: int | None = None):
        self._connect()
        try:
            num = len(types)
            if max_axis is None:
                max_axis = self.MAX_AXIS

            AxArray = _ODBAXDT * (num * max_axis)
            axdata = AxArray()

            TypesArray = c_short * num
            type_arr = TypesArray(*types)

            length = c_short(max_axis)

            ret = self._fwlib.cnc_rdaxisdata(
                self._handle,
                c_short(cls),
                type_arr,
                c_short(num),
                byref(length),
                axdata
            )
            self._ret_check(ret, "cnc_rdaxisdata")

            active_axes = int(length.value)

            labels = {0: "ABSOLUTE", 1: "MACHINE", 2: "RELATIVE", 3: "DISTANCE_TO_GO"}

            out = {}
            for t_index, t in enumerate(types):
                label = labels.get(int(t), f"TYPE_{int(t)}")
                base = t_index * max_axis
                rows = []
                for i in range(active_axes):
                    item = axdata[base + i]
                    rows.append({
                        "axis": item._axis_name(),
                        "raw": int(item.data),
                        "value": item.value(),
                        "dec": int(item.dec),
                        "unit": int(item.unit),
                        "flag": int(item.flag),
                    })
                out[label] = rows

            return out
        finally:
            self._disconnect()

    def _read_x_abs(self) -> float | None:
        data = self._read_axis_data(cls=1, types=(0,), max_axis=32)  # ABSOLUTE only
        rows = data.get("ABSOLUTE", [])
        for r in rows:
            if r.get("axis") == "X":
                return float(r.get("value"))
        return None

    def _start_recording(self, *, axis="X", pos_type=0, poll_s=0.25):
        if self._rec_thread and self._rec_thread.is_alive():
            raise Exception("Recording already in progress")

        with self._rec_lock:
            self._rec_samples = []
        with self._spc_lock:
            self._spc_events = []

        # clear queue
        while not self._spc_queue.empty():
            try:
                self._spc_queue.get_nowait()
            except Exception:
                break

        self._rec_config = {"axis": axis, "pos_type": int(pos_type), "poll_s": float(poll_s)}
        self._rec_stop_event.clear()

        self._spc_stop_event.clear()
        self._spc_thread = threading.Thread(target=self._spc_loop, daemon=True)
        self._spc_thread.start()

        self._rec_thread = threading.Thread(target=self._record_loop, daemon=True)
        self._rec_thread.start()

    def _stop_recording(self):
        if not self._rec_thread:
            return []

        self._rec_stop_event.set()
        self._rec_thread.join(timeout=5)
        self._rec_thread = None

        self._spc_stop_event.set()
        if self._spc_thread:
            self._spc_thread.join(timeout=2)
        self._spc_thread = None

        with self._rec_lock:
            samples = list(self._rec_samples)
        return samples

    def _get_recording_data(self):
        with self._rec_lock:
            return list(self._rec_samples)

    def _save_recording_csv(self, file_path: str | None, *, tolerance_s: float | None = None):
        # snapshot
        with self._rec_lock:
            samples = list(self._rec_samples)
        with self._spc_lock:
            events = list(self._spc_events)

        if not file_path:
            # pick your default path logic here; this is just an example
            file_path = "/tmp/focas_recording.csv"

        # choose tolerance based on poll_s if not provided
        if tolerance_s is None:
            poll_s = float(self._rec_config.get("poll_s", 0.25))
            tolerance_s = 0.6 * poll_s  # slightly generous

        # Prep arrays for fast nearest lookup
        sample_ts = [s.get("ts") for s in samples]
        # filter out None (shouldn't happen, but be safe)
        valid = [(i, t) for i, t in enumerate(sample_ts) if isinstance(t, (int, float))]
        if not valid:
            raise Exception("No valid timestamps in samples")

        idxs, ts_list = zip(*valid)  # ts_list is sorted if samples were appended in time order

        # init fields
        for s in samples:
            s.setdefault("spc_event", "")
            s.setdefault("spc_detail", "")

        # Attach each event to the nearest sample
        for e in events:
            et = e.get("ts")
            if not isinstance(et, (int, float)):
                continue

            # find insertion point
            j = bisect.bisect_left(ts_list, et)

            # candidate nearest neighbors
            candidates = []
            if j > 0:
                candidates.append(j - 1)
            if j < len(ts_list):
                candidates.append(j)

            # choose nearest in time
            best = None
            best_dt = None
            for c in candidates:
                dt = abs(ts_list[c] - et)
                if best_dt is None or dt < best_dt:
                    best_dt = dt
                    best = c

            if best is None or best_dt is None or best_dt > tolerance_s:
                # event too far from any sample, skip
                continue

            sample_index = idxs[best]
            # if multiple events land on same sample, append
            if samples[sample_index]["spc_event"]:
                samples[sample_index]["spc_event"] += f"|{e.get('event', '')}"
                samples[sample_index]["spc_detail"] += f"|{e.get('detail', '')}"
            else:
                samples[sample_index]["spc_event"] = e.get("event", "")
                samples[sample_index]["spc_detail"] = e.get("detail", "")

        # write
        with open(file_path, "w", newline="") as f:
            w = csv.DictWriter(
                f,
                fieldnames=["ts", "axis", "value", "spc_event", "spc_detail", "error"]
            )
            w.writeheader()
            for s in samples:
                w.writerow({
                    "ts": s.get("ts", ""),
                    "axis": s.get("axis", ""),
                    "value": s.get("value", ""),
                    "spc_event": s.get("spc_event", ""),
                    "spc_detail": s.get("spc_detail", ""),
                    "error": s.get("error", ""),
                })

        return file_path

    def _record_loop(self):
        poll_s = float(self._rec_config.get("poll_s", 0.25))
        axis = self._rec_config.get("axis", "X")
        pos_type = int(self._rec_config.get("pos_type", 0))

        labels = {0: "ABSOLUTE", 1: "MACHINE", 2: "RELATIVE", 3: "DISTANCE_TO_GO"}
        key = labels.get(pos_type, "ABSOLUTE")

        while not self._rec_stop_event.is_set():
            ts = time.time()
            try:
                data = self._read_axis_data(cls=1, types=(pos_type,), max_axis=32)
                rows = data.get(key, [])
                val = None
                for r in rows:
                    if r.get("axis") == axis:
                        val = r.get("value")
                        break

                sample = {"ts": ts, "axis": axis, "value": val}

                with self._rec_lock:
                    self._rec_samples.append(sample)

                # fire-and-forget to SPC thread (never block sampling)
                if val is not None:
                    try:
                        self._spc_queue.put_nowait((ts, float(val)))
                    except queue.Full:
                        # Drop SPC analysis if overloaded; sampling continues.
                        pass

            except Exception as e:
                with self._rec_lock:
                    self._rec_samples.append({"ts": ts, "axis": axis, "error": str(e)})

            time.sleep(poll_s)

    def _spc_loop(self):
        window = deque(maxlen=50)  # tune
        trend_n = 7
        shift_n = 8

        def spc_check(vals):
            if len(vals) < max(trend_n, shift_n):
                return None, None
            v = list(vals)

            last_t = v[-trend_n:]
            if all(last_t[i] < last_t[i+1] for i in range(trend_n - 1)):
                return "TREND_UP", f"n={trend_n}"
            if all(last_t[i] > last_t[i+1] for i in range(trend_n - 1)):
                return "TREND_DOWN", f"n={trend_n}"

            mean = sum(v) / len(v)
            last_s = v[-shift_n:]
            if all(x > mean for x in last_s):
                return "SHIFT_UP", f"n={shift_n},mean={mean:.6f}"
            if all(x < mean for x in last_s):
                return "SHIFT_DOWN", f"n={shift_n},mean={mean:.6f}"

            return None, None

        while not self._spc_stop_event.is_set():
            try:
                ts, val = self._spc_queue.get(timeout=0.25)
            except Exception:
                continue

            window.append(val)
            event, detail = spc_check(window)
            if event:
                with self._spc_lock:
                    self._spc_events.append({"ts": ts, "event": event, "detail": detail})






