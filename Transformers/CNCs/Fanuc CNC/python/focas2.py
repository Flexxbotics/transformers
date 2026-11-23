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
from werkzeug.exceptions import HTTPException
from transformers.abstract_device import AbstractDevice
from exceptions.flexxCoreExceptions import (
    FlexxCoreException,
    BadRequestException,
    ServerErrorException,
    ServiceUnavailableException,
)


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
        super().__init__(device)
        # Get meta data of the device from its attributes, this contains information such as: ip address, ports, etc
        self.meta_data = device.metaData or {}
        self.address = self.meta_data.get("ip_address", "")
        self.port = int(self.meta_data.get("port", 8193))
        self.fileshare_path = self.meta_data.get("fileshare_path", "/")
        self.fileshare_username = self.meta_data.get("fileshare_username", "")
        self.fileshare_password = self.meta_data.get("fileshare_password", "")
        if not self.address:
            raise ValueError("FOCAS2: metaData must contain 'ip_address'")

        # FOCAS state
        self._fwlib = None
        self._handle = c_ushort(0)
        self._handle_thread = None
        self._connected = False

        try:
            # Load the correct shared library for the platform (linux/amd64 in your Docker build)
            self._load_fwlib()
            self._define_fwlib_prototypes()

        except Exception as e:
            self._error(str(e))
            raise RuntimeError(f"FOCAS2 init failed: {e}") from e

    def __del__(self):
        try:
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

        Every command returns {"status": "ok", ...} JSON; a command that reads
        something returns it in "value":
            connect / disconnect     -> {"status": "ok", "connected": true|false}
            read_status              -> "value": ODBST.run (int)
            read_status_field        -> "field", "value": that ODBST field (int)
            wait_for_cnc             -> "value": the ODBST status dict
            read_macro               -> "value": the macro value (float)
            read_pmc_range           -> "value": the PMC value
            get_filenames            -> "value": list of program names
            get_current_directory    -> "value": the directory
            program_upload           -> "value": the program as base64
            write_macro, write_pmc_range, set_current_directory,
            program_download         -> {"status": "ok"}

        Errors raise: 400 for missing/invalid args or an unknown command,
        503 when the CNC does not respond, 500 for a FOCAS error code (and for
        wait_for_cnc: alarm, e-stop or timeout).
        """
        # Parse the command from the incoming request
        args = self._parse_args(command_args)
        self._info(message=f"Sending command: {command_name}")
        try:
            # ---- Connection management ----
            if command_name == "connect":
                timeout_s = int(args.get("timeout_s") or 10)
                self._connect(timeout_s=timeout_s)
                return json.dumps({"status": "ok", "connected": True})

            if command_name == "disconnect":
                self._disconnect()
                return json.dumps({"status": "ok", "connected": False})

            # ---- Status / wait ----
            if command_name == "read_status":
                # Returns the ODBST "run" field.
                self._ensure_connected()
                status = self._read_cnc_status()
                return json.dumps({"status": "ok", "value": status["run"]})

            if command_name == "read_status_field":
                # args: {"field": "run"}  (field in status dict)
                field = self._require(args, "field")
                self._ensure_connected()
                status = self._read_cnc_status()
                if field not in status:
                    raise BadRequestException(f"Unknown status field: {field}")
                return json.dumps({"status": "ok", "field": field, "value": status[field]})

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
                        raise ServerErrorException(f"CNC alarm detected (status={status})")
                    if status.get("emergency", 0) == 1:
                        raise ServerErrorException(f"E-stop detected (status={status})")
                    if status.get("run", 0) == 0:
                        return json.dumps({"status": "ok", "value": status})

                    if (time.time() - start) >= timeout_s:
                        raise ServerErrorException(f"Timed out waiting for CNC (status={status})")

                    time.sleep(poll_s)

            # ---- Macro read/write ----
            if command_name == "read_macro":
                # args: {"macro": 651, "length": 10}
                mcr_number = self._require(args, "macro")
                length = int(args.get("length") or 10)
                self._ensure_connected()
                mcr_val, dec_val, joined = self._read_macro(int(mcr_number), length=length)
                return json.dumps({"status": "ok", "value": joined})

            if command_name == "write_macro":
                # args: {"macro": 651, "mcr_val": 123, "dec_val": 2}
                mcr_number = self._require(args, "macro")
                mcr_val = self._require(args, "mcr_val")
                dec_val = args.get("dec_val") or 0
                self._ensure_connected()
                self._write_macro(int(mcr_number), int(mcr_val), int(dec_val))
                return json.dumps({"status": "ok"})

            # ---- PMC read/write ----
            if command_name == "read_pmc_range":
                # args: {"section": 0, "data_type": 2, "start": 0, "end": 0, "length": 12}
                required = ["section", "data_type", "start", "end", "length"]
                missing = [k for k in required if args.get(k) in (None, "")]
                if missing:
                    raise BadRequestException(f"Missing required args: {', '.join(missing)}")
                self._ensure_connected()
                val = self._read_pmc_range(
                    section=int(args["section"]),
                    data_type=int(args["data_type"]),
                    start=int(args["start"]),
                    end=int(args["end"]),
                    length=int(args["length"]),
                )
                return json.dumps({"status": "ok", "value": val}, default=str)

            if command_name == "write_pmc_range":
                # args: {"length": 12, "section": 0, "data_type": 2, "start": 0, "end": 0, "value": 123}
                required = ["length", "section", "data_type", "start", "end", "value"]
                missing = [k for k in required if args.get(k) in (None, "")]
                if missing:
                    raise BadRequestException(f"Missing required args: {', '.join(missing)}")
                self._ensure_connected()
                self._write_pmc_range(
                    length=int(args["length"]),
                    section=int(args["section"]),
                    data_type=int(args["data_type"]),
                    start=int(args["start"]),
                    end=int(args["end"]),
                    value=args["value"],
                )
                return json.dumps({"status": "ok"})

            # ---- Program file operations (directory + upload/download) ----
            if command_name == "get_filenames":
                # args (optional): {"directory": "//CNC_MEM/USER/PATH1/PART_PROGRAMS"}
                self._ensure_connected()
                directory = args.get("directory") or "//CNC_MEM/USER/PATH1/PART_PROGRAMS"
                # Keep behaviour consistent with the legacy driver: set dir before listing
                self._set_current_directory(directory)
                names = self._get_filenames()
                return json.dumps({"status": "ok", "value": names})

            if command_name == "get_current_directory":
                self._ensure_connected()
                d = self._get_current_directory()
                return json.dumps({"status": "ok", "value": d})

            if command_name == "set_current_directory":
                # args: {"directory": "//CNC_MEM/USER/PATH1/PART_PROGRAMS"}
                directory = self._require(args, "directory")
                self._ensure_connected()
                self._set_current_directory(str(directory))
                return json.dumps({"status": "ok"})

            if command_name == "program_upload":
                # args: {"program_path": "//CNC_MEM/USER/PATH1/PART_PROGRAMS/O0010", "buffer_size": 1024}
                program_path = self._require(args, "program_path")
                buffer_size = int(args.get("buffer_size") or 1024)
                self._ensure_connected()
                b64 = self._program_upload(str(program_path), buffer_size=buffer_size)
                return json.dumps({"status": "ok", "value": b64})

            if command_name == "program_download":
                # args: {"program_path": "//CNC_MEM/USER/PATH1/PART_PROGRAMS/O0010", "program_data_base64": "..."}
                program_path = self._require(args, "program_path")
                program_data_b64 = self._require(args, "program_data_base64")
                try:
                    raw = base64.b64decode(program_data_b64)
                except ValueError:
                    raise BadRequestException("program_data_base64 is not valid base64")
                self._ensure_connected()
                self._program_download(str(program_path), raw)
                return json.dumps({"status": "ok"})

        except Exception as e:
            self._raise_device_error(f"Command '{command_name}'", e)

        raise BadRequestException(f"Unknown command '{command_name}'")

    def _read_interval_data(self) -> str:
        """
        Method to read the status of the device on an interval
        """
        pass

    def _read_status(self, function: str = None) -> str:
        """
        CNC run state: the ODBST "run" field as a string (e.g. "0" = reset/stopped,
        "3" = cycle running). Returns "ERROR" when the CNC cannot be read.
        """
        try:
            status = self._read_cnc_status()
            return str(status["run"])
        except Exception as e:
            self._error(message=f"Status read failed: {e}")
            self._connected = False
            return "ERROR"

    def _read_variable(self, variable_name: str, function: str = None) -> str:
        """
        Read a custom macro variable. variable_name is the macro number (e.g. "500").
        Returns the value as a string. Raises on failure.
        """
        try:
            mcr_val, dec_val, joined = self._read_macro(int(variable_name))
            return str(joined)
        except Exception as e:
            self._raise_device_error(f"Read variable '{variable_name}'", e)

    def _write_variable(self, variable_name: str, variable_value: str, function: str = None) -> str:
        """
        Write a custom macro variable as an integer (dec_val 0), then read it back.
        variable_name is the macro number (e.g. "500"). Returns the value read back
        as a string. Raises on failure, including a non-zero cnc_wrmacro return code.
        """
        try:
            try:
                mcr_val = int(variable_value)
            except (TypeError, ValueError):
                raise BadRequestException(f"Macro value must be an integer, got {variable_value!r}")
            self._write_macro(int(variable_name), mcr_val, 0)
            mcr_val, dec_val, joined = self._read_macro(int(variable_name))
            return str(joined)
        except Exception as e:
            self._raise_device_error(f"Write variable '{variable_name}'", e)

    def _read_file_names(self) -> list:
        """
        Method to get a list of filenames from the device via FOCAS2.

        :return:    list of filenames

        :author:    tylerjm@flexxbotics.com
        :since:     KEYSTONE.4 (7.1.11.4)
        """
        self.programs = []
        self._info(message="Reading file names from Fanuc CNC memory")
        try:
            self._ensure_connected()
            self.programs = self._get_filenames()
            self._info(message="Found " + str(len(self.programs)) + " file(s) in Fanuc CNC memory")
        except Exception as e:
            self._error(message="Error reading file names from Fanuc CNC memory: " + str(e))
            self._raise_device_error("Read file names from Fanuc CNC memory", e)

        return self.programs

    def _read_file(self, file_name: str) -> str:
        """
        Method to read a file from the device via FOCAS2.

        :param file_name:
                    the name of the file to read.

        :return:    the file's data as base64 string.

        :author:    tylerjm@flexxbotics.com
        :since:     KEYSTONE.4 (7.1.11.4)
        """
        self._info(message="Reading file from Fanuc CNC memory: " + file_name)
        try:
            self._ensure_connected()
            program_path = self.fileshare_path.rstrip("/") + "/" + file_name
            b64 = self._program_upload(program_path)
            self._info(message="Successfully read file from Fanuc CNC memory: " + file_name)
        except Exception as e:
            self._error(message="Error reading file from Fanuc CNC memory: " + str(e))
            self._raise_device_error(f"Read file '{file_name}' from Fanuc CNC memory", e)

        return b64

    def _write_file(self, file_name: str, file_data: str):
        """
        Method to write a file to the device via FOCAS2.

        :param file_name:
                    the name of the file to write.
        :param file_data:
                    the data of the file to write as base64 string.

        :author:    tylerjm@flexxbotics.com
        :since:     ODOULS.3 (7.1.15.3)
        """
        self._info(message="Writing file to Fanuc CNC memory: " + file_name)
        try:
            self._ensure_connected()
            program_path = self.fileshare_path.rstrip("/") + "/" + file_name
            raw = base64.b64decode(file_data)
            self._program_download(program_path, raw)
            self._info(message="Successfully wrote file to Fanuc CNC memory: " + file_name)
        except Exception as e:
            self._error(message="Error writing file to Fanuc CNC memory: " + str(e))
            self._raise_device_error(f"Write file '{file_name}' to Fanuc CNC memory", e)

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

    # FOCAS return codes that mean the library handle is no longer usable.
    _EW_HANDLE = -8
    _EW_SOCKET = -16

    def _ret_check(self, ret: int, function: str) -> None:
        # In FOCAS, 0 is generally EW_OK.
        if int(ret) in (self._EW_HANDLE, self._EW_SOCKET):
            # Handle/socket lost (e.g. the CNC power-cycled): reconnect on the next call.
            self._connected = False
            raise ServiceUnavailableException(f"Failed - {function}. Lost connection to CNC, error code: {int(ret)}")
        if int(ret) != 0:
            raise ServerErrorException(f"Failed - {function}. Error code: {int(ret)}")

    # --------------------------------------------------------------------------------------
    # Private: connection + primitives
    # --------------------------------------------------------------------------------------

    def _connect(self, *, timeout_s: int = 10) -> None:
        if self._connected:
            return

        # Create log file if supported. Use a stable location in container.
        # If FANUC fwlib doesn't support logging on a given build, it will return an error code.
        try:
            ret_log = self._fwlib.cnc_startupprocess(0, b"focas.log")
            # Treat non-zero as non-fatal for startup log creation (some builds may not support)
            # but still allow connect to proceed.
        except Exception:
            pass

        # Free the handle of a dropped connection if present
        if int(self._handle.value) != 0:
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
            raise ServiceUnavailableException(f"cnc_allclibhndl3 failed rc={ret}")
        self._handle_thread = threading.get_ident()
        self._connected = True

    def _disconnect(self) -> None:
        try:
            if int(self._handle.value) != 0:
                self._fwlib.cnc_freelibhndl(self._handle)
        finally:
            self._connected = False
            self._handle = c_ushort(0)
            self._handle_thread = None

    def _ensure_connected(self) -> None:
        # A FOCAS library handle is only valid in the thread that allocated it, and the
        # runtime calls this transformer from different request and poll threads (one at
        # a time, under the device lock). When the calling thread changes, free the old
        # handle and allocate a new one in this thread.
        if self._connected and self._handle_thread != threading.get_ident():
            self._disconnect()
        if not self._connected:
            self._connect(timeout_s=10)

    def _read_cnc_status(self) -> dict:
        self._ensure_connected()
        stat = _ODBST()
        ret = self._fwlib.cnc_statinfo(self._handle, byref(stat))
        self._ret_check(ret, "cnc_statinfo")
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
        self._ensure_connected()
        md = _ODBM()
        ret = self._fwlib.cnc_rdmacro(self._handle, c_short(mcr_number), c_short(length), byref(md))
        self._ret_check(ret, "cnc_rdmacro")

        mcr_val = int(md.mcr_val)
        dec_val = int(md.dec_val)
        joined = self._join_decimal(mcr_val, dec_val)
        return mcr_val, dec_val, joined

    def _write_macro(self, mcr_number: int, mcr_val: int, dec_val: int):
        self._ensure_connected()
        ret = self._fwlib.cnc_wrmacro(self._handle, c_short(mcr_number), c_short(10), c_long(mcr_val), c_short(dec_val))
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
        self._ensure_connected()
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
        return pmc_data.u.get_by_dtype(int(data_type))

    def _write_pmc_range(self, *, length: int, section: int, data_type: int, start: int, end: int, value):
        self._ensure_connected()
        u = _IODBPMCUnion()
        u.set_by_dtype(int(data_type), value)
        pmc_data = _IODBPMC(c_short(section), c_short(data_type), c_short(start), c_short(end), u)
        ret = self._fwlib.pmc_wrpmcrng(self._handle, c_short(length), byref(pmc_data))
        self._ret_check(ret, "pmc_wrpmcrng")

    def _get_filenames(self):
        # Mirrors legacy behaviour: cnc_rdprogdir returns a blob of directory data in prg_data.
        self._ensure_connected()
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
        return names

    def _get_current_directory(self) -> str:
        self._ensure_connected()
        buf = ctypes.create_string_buffer(4096)
        # type 0 = NC memory (//CNC_MEM/...), 1 = memory card, 2 = data server, 3 = USB
        ret = self._fwlib.cnc_rdpdf_curdir(self._handle, c_short(0), buf)
        self._ret_check(ret, "cnc_rdpdf_curdir")
        return buf.value.decode("utf-8", errors="ignore")

    def _set_current_directory(self, directory: str) -> None:
        self._ensure_connected()
        buf = ctypes.create_string_buffer(directory.encode("utf-8"))
        # type 0 = NC memory (//CNC_MEM/...), 1 = memory card, 2 = data server, 3 = USB
        ret = self._fwlib.cnc_wrpdf_curdir(self._handle, c_short(0), buf)
        self._ret_check(ret, "cnc_wrpdf_curdir")

    def _program_upload(self, program_path: str, *, buffer_size: int = 1024) -> str:
        import time

        self._ensure_connected()
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

        return base64.b64encode(data).decode("ascii")

    def _program_download(self, program_path: str, program_data: bytes, *, max_chunk: int = 1400) -> None:
        self._ensure_connected()
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
