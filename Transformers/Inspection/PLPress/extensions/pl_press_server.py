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

Standalone JSON-RPC 2.0 server that runs on the PL Press Windows 11 PC.

It scans a directory of PL Press measurement CSV files, filters them by file
modified time, parses each into a structured dict, and returns the results
over HTTP as JSON. A Flexx device driver connects to it (e.g. via the HttpRest
protocol) and calls the ``get_reports`` method to pull inspection data.

This server uses only Python's standard library — no third-party dependencies
are required to run or to package it.

Configuration comes from a JSON config file (``config.json`` beside this
program by default, or ``--config``/``$PLPRESS_CONFIG``) and can be overridden
per-run with command-line arguments. It controls the report directory, the
bind host/port, the file glob, recursion, an optional auth token, and the
default look-back window (in hours) applied when a caller does not pass its own
time filter.

Run:
    python pl_press_server.py                        # uses config.json
    python pl_press_server.py -d "C:\\PLPress\\reports" -p 8757

JSON-RPC methods:
    ping()
    list_reports(since=?, until=?, modified_within_hours=?, pattern=?, recursive=?)
    get_reports(since=?, until=?, modified_within_hours=?, pattern=?,
                recursive=?, include_measurements=True)
    get_report(file=<name or absolute path>)
"""
from __future__ import annotations

import argparse
import csv
import glob
import hashlib
import json
import os
import sys
import traceback
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# PL Press report parser
# ---------------------------------------------------------------------------
class PLPressReportParser:
    """
    Parser for PL Press measurement CSV files.

    Each file has a header row followed by one row per press cycle, e.g.::

        JobNumber, SapPartNumber, TimeStamp, Height, Taper Left, Taper Right, Total Taper, Serial Number
        2300652494, 100035902, 6/22/2026 7:04:23 PM, 4.25979, -0.00032, -0.00030, -0.00062, 0000

    All rows in a file share one job/part, so a file maps to a single report
    with a small header block plus a list of measurement rows.

    ``parse_csv`` returns a plain ``dict`` (JSON-serializable) so it can be
    returned directly over the JSON-RPC transport.
    """

    # Timestamp formats seen in the files (with and without seconds).
    _TIMESTAMP_FORMATS = ("%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y %I:%M %p")

    # Maps a lower-cased header cell to (normalized_key, kind). "kind" drives
    # value coercion: "float" -> float, "datetime" -> ISO-8601 string, else str.
    _FIELD_MAP = {
        "jobnumber": ("job_number", "str"),
        "sappartnumber": ("sap_part_number", "str"),
        "timestamp": ("timestamp", "datetime"),
        "height": ("height", "float"),
        "taper left": ("taper_left", "float"),
        "taper right": ("taper_right", "float"),
        "total taper": ("total_taper", "float"),
        "serial number": ("serial_number", "str"),
    }

    @staticmethod
    def _to_float(value: Optional[str]) -> Any:
        try:
            return float(value)
        except (TypeError, ValueError):
            return value

    @classmethod
    def _to_datetime(cls, value: Optional[str]) -> Optional[str]:
        if not value:
            return None
        for fmt in cls._TIMESTAMP_FORMATS:
            try:
                return datetime.strptime(value, fmt).isoformat()
            except ValueError:
                continue
        return value  # keep raw string if it doesn't match a known format

    @staticmethod
    def _slug(header_cell: str) -> str:
        """Fallback normalized key for an unrecognized column."""
        return "_".join(header_cell.lower().split()) or "column"

    @classmethod
    def _column_spec(cls, header: List[str]) -> List[Tuple[str, str]]:
        """Return [(normalized_key, kind)] aligned to the header cells."""
        spec: List[Tuple[str, str]] = []
        for cell in header:
            key, kind = cls._FIELD_MAP.get(cell.strip().lower(), (cls._slug(cell), "str"))
            spec.append((key, kind))
        return spec

    @classmethod
    def _coerce(cls, kind: str, raw: str) -> Any:
        if kind == "float":
            return cls._to_float(raw)
        if kind == "datetime":
            return cls._to_datetime(raw)
        return raw

    @classmethod
    def build_report_id(cls, header: Dict[str, Any], source_file: str) -> str:
        """
        Build a stable, unique identifier for a report.

        Prefers the intrinsic identity of the run (job number + SAP part number
        + first measurement time) so the same file is deduplicated even if it is
        copied or renamed. Falls back to the source filename when those fields
        cannot be parsed.
        """
        key_parts = [
            header.get("job_number"),
            header.get("sap_part_number"),
            header.get("first_timestamp"),
        ]
        if all(key_parts):
            raw = "|".join(str(p) for p in key_parts)
        else:
            raw = source_file
        digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
        return f"plpress-{digest}"

    @classmethod
    def parse_csv(cls, path: str) -> Dict[str, Any]:
        """
        Parse a single PL Press CSV file into a structured dict.

        :raises ValueError: if the file has no header or no measurement rows
            (i.e. it does not look like a PL Press report).
        """
        with open(path, "r", encoding="utf-8-sig", newline="") as fh:
            reader = csv.reader(fh, skipinitialspace=True)
            rows = [row for row in reader if any(cell.strip() for cell in row)]

        if not rows:
            raise ValueError(f"Empty file: '{path}'")

        header_cells = [cell.strip() for cell in rows[0]]
        spec = cls._column_spec(header_cells)

        measurements: List[Dict[str, Any]] = []
        for row in rows[1:]:
            record: Dict[str, Any] = {}
            for (key, kind), raw in zip(spec, row):
                record[key] = cls._coerce(kind, raw.strip())
            measurements.append(record)

        if not measurements:
            raise ValueError(f"No PL Press measurement rows found in '{path}'")

        timestamps = [m.get("timestamp") for m in measurements if m.get("timestamp")]
        first = measurements[0]
        header = {
            "job_number": first.get("job_number"),
            "sap_part_number": first.get("sap_part_number"),
            "serial_number": first.get("serial_number"),
            "columns": header_cells,
            "first_timestamp": min(timestamps) if timestamps else None,
            "last_timestamp": max(timestamps) if timestamps else None,
        }

        return {
            "report_id": cls.build_report_id(header, path),
            "header": header,
            "measurements": measurements,
            "measurement_count": len(measurements),
        }


# ---------------------------------------------------------------------------
# JSON-RPC 2.0 error codes
# ---------------------------------------------------------------------------
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
SERVER_ERROR = -32000  # implementation-defined server errors


class RpcError(Exception):
    def __init__(self, code: int, message: str, data: Any = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
# Built-in defaults. These are overridden by values in the JSON config file,
# which are in turn overridden by command-line arguments (CLI > file > default).
DEFAULT_CONFIG: Dict[str, Any] = {
    "directory": None,       # folder holding the PL Press CSV files (required)
    "host": "0.0.0.0",       # bind address
    "port": 8757,            # bind port
    "pattern": "*.txt",      # glob for report files
    "recursive": False,      # recurse into subfolders
    "token": None,           # optional X-Auth-Token shared secret
    "lookback_hours": None,  # default look-back window; None/0 = parse all files
}


class Config:
    """Effective runtime configuration (populated in main(), read by handlers)."""

    directory: Optional[str] = None
    host: str = "0.0.0.0"
    port: int = 8757
    pattern: str = "*.txt"
    recursive: bool = False
    token: Optional[str] = None
    default_lookback_hours: Optional[float] = None


CONFIG = Config()


def _app_dir() -> str:
    """
    Directory to look in for the default config.json.

    When frozen by PyInstaller, ``__file__`` points at a temporary extraction
    directory, so use the directory of the running executable instead. When
    running as a normal script, use the directory of this source file.
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def load_config_file(path: Optional[str], required: bool) -> Dict[str, Any]:
    """
    Load a JSON config file into a dict.

    Returns ``{}`` when ``path`` is falsy or the file is absent and not
    ``required``. Exits with a clear message on malformed JSON or a bad type.
    """
    if not path:
        return {}
    if not os.path.isfile(path):
        if required:
            raise SystemExit(f"Config file not found: {os.path.abspath(path)}")
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (ValueError, OSError) as exc:
        raise SystemExit(f"Failed to read config file '{path}': {exc}")
    if not isinstance(data, dict):
        raise SystemExit(f"Config file '{path}' must contain a JSON object")
    unknown = set(data) - set(DEFAULT_CONFIG)
    if unknown:
        print(f"Warning: ignoring unknown config keys: {', '.join(sorted(unknown))}")
    return data


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------
def _coerce_epoch(value: Any) -> Optional[float]:
    """
    Accept a bound as epoch seconds (int/float) or an ISO-8601 string and
    return epoch seconds. ``None`` means "no bound".
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        # Allow a trailing 'Z' for UTC.
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            raise RpcError(INVALID_PARAMS, f"Invalid datetime: {value!r}")
        if dt.tzinfo is None:
            # Naive timestamps are interpreted in the server's local time,
            # matching how file mtimes are reported to callers.
            return dt.timestamp()
        return dt.timestamp()
    raise RpcError(INVALID_PARAMS, f"Unsupported time value: {value!r}")


def _iso_local(epoch: float) -> str:
    return datetime.fromtimestamp(epoch).isoformat()


# ---------------------------------------------------------------------------
# File discovery
# ---------------------------------------------------------------------------
def _resolve_directory(directory: Optional[str]) -> str:
    return os.path.abspath(directory or CONFIG.directory)


def _discover_files(
    directory: Optional[str],
    since: Any,
    until: Any,
    modified_within_hours: Optional[float],
    pattern: Optional[str],
    recursive: Optional[bool],
) -> List[Tuple[str, float]]:
    """
    Return a list of (absolute_path, mtime_epoch) for files matching the glob
    pattern whose modified time falls within the requested window, newest
    first.
    """
    base = _resolve_directory(directory)
    if not os.path.isdir(base):
        raise RpcError(SERVER_ERROR, f"Directory not found: {base}")

    glob_pattern = pattern or CONFIG.pattern
    use_recursive = CONFIG.recursive if recursive is None else bool(recursive)
    if use_recursive:
        search = os.path.join(base, "**", glob_pattern)
        candidates = glob.glob(search, recursive=True)
    else:
        candidates = glob.glob(os.path.join(base, glob_pattern))

    # Apply the configured default look-back window only when the caller gave
    # no explicit time filter of its own.
    if (
        since is None
        and until is None
        and modified_within_hours is None
        and CONFIG.default_lookback_hours
    ):
        modified_within_hours = CONFIG.default_lookback_hours

    since_epoch = _coerce_epoch(since)
    until_epoch = _coerce_epoch(until)
    if modified_within_hours is not None:
        window_start = datetime.now(timezone.utc).timestamp() - float(modified_within_hours) * 3600.0
        since_epoch = window_start if since_epoch is None else max(since_epoch, window_start)

    results: List[Tuple[str, float]] = []
    for path in candidates:
        if not os.path.isfile(path):
            continue
        mtime = os.path.getmtime(path)
        if since_epoch is not None and mtime < since_epoch:
            continue
        if until_epoch is not None and mtime > until_epoch:
            continue
        results.append((path, mtime))

    results.sort(key=lambda item: item[1], reverse=True)
    return results


# ---------------------------------------------------------------------------
# RPC methods
# ---------------------------------------------------------------------------
def rpc_ping(**_: Any) -> Dict[str, Any]:
    return {
        "ok": True,
        "service": "pl-press-report-server",
        "directory": os.path.abspath(CONFIG.directory) if CONFIG.directory else None,
        "port": CONFIG.port,
        "default_lookback_hours": CONFIG.default_lookback_hours,
        "server_time": datetime.now().isoformat(),
    }


def rpc_list_reports(
    since: Any = None,
    until: Any = None,
    modified_within_hours: Optional[float] = None,
    pattern: Optional[str] = None,
    recursive: Optional[bool] = None,
    directory: Optional[str] = None,
    **_: Any,
) -> Dict[str, Any]:
    """Lightweight listing (no CSV parsing) of files in the window."""
    files = _discover_files(directory, since, until, modified_within_hours, pattern, recursive)
    return {
        "directory": _resolve_directory(directory),
        "count": len(files),
        "files": [
            {
                "source_file": os.path.basename(path),
                "path": path,
                "file_modified": _iso_local(mtime),
                "file_modified_epoch": mtime,
                "size_bytes": os.path.getsize(path),
            }
            for path, mtime in files
        ],
    }


def rpc_get_reports(
    since: Any = None,
    until: Any = None,
    modified_within_hours: Optional[float] = None,
    pattern: Optional[str] = None,
    recursive: Optional[bool] = None,
    directory: Optional[str] = None,
    include_measurements: bool = True,
    **_: Any,
) -> Dict[str, Any]:
    """
    Parse every report in the window and return them keyed by a stable
    report_id. Duplicate reports (same intrinsic id) are collapsed, keeping the
    most recently modified file.
    """
    files = _discover_files(directory, since, until, modified_within_hours, pattern, recursive)

    reports: Dict[str, Any] = {}
    errors: List[Dict[str, str]] = []

    # files are newest-first; the first occurrence of a report_id wins.
    for path, mtime in files:
        try:
            report = PLPressReportParser.parse_csv(path)
        except Exception as exc:  # noqa: BLE001 - report per-file, keep going
            errors.append({"source_file": os.path.basename(path), "path": path, "error": str(exc)})
            continue

        report_id = report["report_id"]
        if report_id in reports:
            continue  # older duplicate; skip

        report["source_file"] = os.path.basename(path)
        report["path"] = path
        report["file_modified"] = _iso_local(mtime)
        report["file_modified_epoch"] = mtime
        if not include_measurements:
            report.pop("measurements", None)
        reports[report_id] = report

    return {
        "directory": _resolve_directory(directory),
        "count": len(reports),
        "reports": reports,
        "errors": errors,
    }


def rpc_get_report(file: Optional[str] = None, directory: Optional[str] = None, **_: Any) -> Dict[str, Any]:
    """Parse a single report by filename (within the directory) or absolute path."""
    if not file:
        raise RpcError(INVALID_PARAMS, "Missing required parameter 'file'")

    path = file if os.path.isabs(file) else os.path.join(_resolve_directory(directory), file)
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        raise RpcError(SERVER_ERROR, f"File not found: {path}")

    # Contain path traversal: the resolved file must live under the base dir
    # unless an absolute path was explicitly provided by the caller.
    if not os.path.isabs(file):
        base = _resolve_directory(directory)
        if os.path.commonpath([base, path]) != base:
            raise RpcError(INVALID_PARAMS, "Resolved path escapes the report directory")

    report = PLPressReportParser.parse_csv(path)
    mtime = os.path.getmtime(path)
    report["source_file"] = os.path.basename(path)
    report["path"] = path
    report["file_modified"] = _iso_local(mtime)
    report["file_modified_epoch"] = mtime
    return report


METHODS = {
    "ping": rpc_ping,
    "list_reports": rpc_list_reports,
    "get_reports": rpc_get_reports,
    "get_report": rpc_get_report,
}


# ---------------------------------------------------------------------------
# JSON-RPC dispatch
# ---------------------------------------------------------------------------
def _dispatch_single(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    req_id = payload.get("id") if isinstance(payload, dict) else None

    def error_response(code: int, message: str, data: Any = None) -> Dict[str, Any]:
        err: Dict[str, Any] = {"code": code, "message": message}
        if data is not None:
            err["data"] = data
        return {"jsonrpc": "2.0", "error": err, "id": req_id}

    if not isinstance(payload, dict) or payload.get("jsonrpc") != "2.0" or "method" not in payload:
        return error_response(INVALID_REQUEST, "Invalid JSON-RPC 2.0 request")

    method = payload.get("method")
    params = payload.get("params", {})
    if params is None:
        params = {}
    if isinstance(params, list):
        return error_response(INVALID_PARAMS, "Positional params are not supported; use an object")
    if not isinstance(params, dict):
        return error_response(INVALID_PARAMS, "params must be an object")

    func = METHODS.get(method)
    if func is None:
        return error_response(METHOD_NOT_FOUND, f"Unknown method: {method}")

    try:
        result = func(**params)
    except RpcError as exc:
        return error_response(exc.code, exc.message, exc.data)
    except TypeError as exc:
        return error_response(INVALID_PARAMS, str(exc))
    except Exception as exc:  # noqa: BLE001
        return error_response(INTERNAL_ERROR, str(exc), traceback.format_exc())

    # Notification (no id) -> no response per spec.
    if "id" not in payload:
        return None
    return {"jsonrpc": "2.0", "result": result, "id": req_id}


def handle_rpc(body: bytes) -> Optional[Any]:
    try:
        payload = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return {"jsonrpc": "2.0", "error": {"code": PARSE_ERROR, "message": "Parse error"}, "id": None}

    if isinstance(payload, list):
        if not payload:
            return {"jsonrpc": "2.0", "error": {"code": INVALID_REQUEST, "message": "Empty batch"}, "id": None}
        responses = [r for r in (_dispatch_single(item) for item in payload) if r is not None]
        return responses or None
    return _dispatch_single(payload)


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------
class RpcHandler(BaseHTTPRequestHandler):
    server_version = "PLPressReportServer/1.0"

    def _write_json(self, status: int, payload: Any) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _check_auth(self) -> bool:
        if not CONFIG.token:
            return True
        return self.headers.get("X-Auth-Token") == CONFIG.token

    def do_GET(self) -> None:  # noqa: N802
        # Convenience health check for browsers / load balancers.
        if self.path.rstrip("/") in ("", "/health", "/ping"):
            self._write_json(200, rpc_ping())
        else:
            self._write_json(404, {"error": "Not found. POST JSON-RPC to /rpc"})

    def do_POST(self) -> None:  # noqa: N802
        if not self._check_auth():
            self._write_json(401, {"jsonrpc": "2.0", "error": {"code": SERVER_ERROR, "message": "Unauthorized"}, "id": None})
            return

        length = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(length) if length else b""
        response = handle_rpc(body)
        if response is None:
            # All notifications: HTTP 204, no body.
            self.send_response(204)
            self.end_headers()
            return
        self._write_json(200, response)

    def log_message(self, fmt: str, *args: Any) -> None:
        # Keep console output concise; override to route to a logger if desired.
        print(f"[{self.log_date_time_string()}] {self.address_string()} {fmt % args}")


def main() -> None:
    parser = argparse.ArgumentParser(description="PL Press report JSON-RPC server")
    # CLI defaults are None so we can tell whether the user actually passed a
    # value (which overrides the config file) versus left it to the config.
    parser.add_argument("--config", "-c", default=os.environ.get("PLPRESS_CONFIG"),
                        help="Path to a JSON config file (default: config.json beside this program)")
    parser.add_argument("--directory", "-d", help="Directory containing PL Press CSV files")
    parser.add_argument("--host", help="Bind address (default: 0.0.0.0)")
    parser.add_argument("--port", "-p", type=int, help="Bind port (default: 8757)")
    parser.add_argument("--pattern", help="Glob pattern for report files (default: *.txt)")
    parser.add_argument("--recursive", action=argparse.BooleanOptionalAction, default=None,
                        help="Recurse into subdirectories (--recursive / --no-recursive)")
    parser.add_argument("--lookback-hours", type=float, dest="lookback_hours",
                        help="Default look-back window in hours when a caller passes no time filter")
    parser.add_argument("--token",
                        help="Optional shared secret; if set, clients must send it as the X-Auth-Token header")
    args = parser.parse_args()

    # Resolve the config file: explicit --config/$PLPRESS_CONFIG is required to
    # exist; the implicit config.json beside the program is optional.
    explicit_config = args.config
    config_path = explicit_config or os.path.join(_app_dir(), "config.json")
    file_cfg = load_config_file(config_path, required=bool(explicit_config))
    config_loaded = bool(file_cfg) or os.path.isfile(config_path)

    def resolve(cli_value: Any, key: str) -> Any:
        if cli_value is not None:
            return cli_value
        if file_cfg.get(key) is not None:
            return file_cfg[key]
        return DEFAULT_CONFIG[key]

    CONFIG.directory = resolve(args.directory, "directory")
    CONFIG.host = resolve(args.host, "host")
    CONFIG.port = int(resolve(args.port, "port"))
    CONFIG.pattern = resolve(args.pattern, "pattern")
    CONFIG.recursive = bool(resolve(args.recursive, "recursive"))
    CONFIG.default_lookback_hours = resolve(args.lookback_hours, "lookback_hours")
    # Token precedence: CLI > config file > environment.
    CONFIG.token = args.token or file_cfg.get("token") or os.environ.get("PLPRESS_RPC_TOKEN")

    if not CONFIG.directory:
        raise SystemExit(
            "No report directory configured. Set 'directory' in the config file "
            "or pass --directory."
        )
    if not os.path.isdir(CONFIG.directory):
        raise SystemExit(f"Directory does not exist: {os.path.abspath(CONFIG.directory)}")

    lb = CONFIG.default_lookback_hours
    httpd = ThreadingHTTPServer((CONFIG.host, CONFIG.port), RpcHandler)
    print(f"PL Press report server listening on http://{CONFIG.host}:{CONFIG.port}/rpc")
    if config_loaded:
        print(f"  config file        : {os.path.abspath(config_path)}")
    print(f"  report directory   : {os.path.abspath(CONFIG.directory)}")
    print(f"  file pattern       : {CONFIG.pattern} (recursive={CONFIG.recursive})")
    print(f"  default look-back  : {f'{lb} h' if lb else 'none (all files)'}")
    print(f"  auth token         : {'enabled' if CONFIG.token else 'disabled'}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
