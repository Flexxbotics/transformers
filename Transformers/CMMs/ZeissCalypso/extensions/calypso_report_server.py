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

Standalone JSON-RPC 2.0 server that runs on the ZEISS CMM Windows 11 PC.

It scans a directory of ZEISS CALYPSO report PDFs, filters them by file
modified time, parses each into a structured dict, and returns the results
over HTTP as JSON. A Flexx device driver connects to it (e.g. via the HttpRest
protocol) and calls the ``get_reports`` method to pull inspection data.

Only Python's standard library is required to run the server; pdfplumber is
required for the actual PDF parsing (see requirements.txt).

Configuration comes from a JSON config file (``config.json`` beside this
script by default, or ``--config``/``$CALYPSO_CONFIG``) and can be overridden
per-run with command-line arguments. It controls the report directory, the
bind host/port, the file glob, recursion, an optional auth token, and the
default look-back window (in hours) applied when a caller does not pass its own
time filter.

Run:
    python calypso_report_server.py                       # uses config.json
    python calypso_report_server.py -d "C:\\CALYPSO\\reports" -p 8756

JSON-RPC methods:
    ping()
    list_reports(since=?, until=?, modified_within_hours=?, pattern=?, recursive=?)
    get_reports(since=?, until=?, modified_within_hours=?, pattern=?,
                recursive=?, include_characteristics=True)
    get_report(file=<name or absolute path>)
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import re
import traceback
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional, Tuple

import pdfplumber


# ---------------------------------------------------------------------------
# ZEISS CALYPSO report parser
# ---------------------------------------------------------------------------
class CalypsoReportParser:
    """
    Parser for ZEISS CALYPSO CMM measurement report PDFs.

    Each report contains a header block (part name, ident, operator, CMM info,
    measurement time) followed by a table of measured characteristics with the
    columns: Name, Measured value, Nominal value, +Tol, -Tol, Deviation, and an
    optional out-of-tolerance (+/-) amount that only appears on failing
    features.

    ``parse_pdf`` returns a plain ``dict`` (JSON-serializable) so it can be
    returned directly over the JSON-RPC transport.
    """

    # One measured-characteristic row, e.g.
    #   "#2_Diameter_Dowel 0.5036inch 0.5000 0.0100 -0.0100 0.0036"
    #   "#3_7DEG-Angle_of_Dowel 7.0351° 7.0000 0.5000 -0.5000 0.0351"
    #   "#5.4_...BotL_3d 0.4465inch 0.4340 0.0100 -0.0100 0.0125 0.0025"  (last col = out of tol)
    _ROW_RE = re.compile(
        r"^(?P<name>\S+)\s+"
        r"(?P<measured>-?\d+(?:\.\d+)?)(?P<unit>inch|mm|°)\s+"
        r"(?P<nominal>-?\d+(?:\.\d+)?)\s+"
        r"(?P<tol_plus>-?\d+(?:\.\d+)?)\s+"
        r"(?P<tol_minus>-?\d+(?:\.\d+)?)\s+"
        r"(?P<deviation>-?\d+(?:\.\d+)?)"
        r"(?:\s+(?P<out_of_tol>-?\d+(?:\.\d+)?))?\s*$"
    )

    # The Time/Date field renders with date and time smashed together, e.g.
    # "6/18/202610:48AM".
    _DATETIME_RE = re.compile(
        r"Time/Date\s+(\d{1,2}/\d{1,2}/\d{4})\s*(\d{1,2}:\d{2}\s*[AP]M)"
    )

    _UNIT_NAMES = {"°": "deg", "inch": "inch", "mm": "mm"}

    # When a header field is blank, extract_text() collapses columns so the
    # next field's label lands where the value would be. Reject those so a
    # blank field reads as None instead of bleeding in the following label.
    _FIELD_LABELS = {
        "Partname", "Drawingnumber", "Ordernumber", "Variant", "Company",
        "Department", "CMMType", "CMMNo.", "Operator", "Text", "Name",
        "Partident", "Time/Date", "Last1measurements", "Numbermeasuredvalues",
        "Numbervalues:red", "MeasurementDuration", "Run",
    }

    @classmethod
    def _search(cls, pattern: str, text: str, reject_labels: bool = False) -> Optional[str]:
        m = re.search(pattern, text)
        if not m:
            return None
        value = m.group(1).strip()
        if reject_labels and value in cls._FIELD_LABELS:
            return None
        return value

    @staticmethod
    def _to_int(value: Optional[str]) -> Optional[int]:
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _to_float(value: Optional[str]) -> Optional[float]:
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    @classmethod
    def _parse_measured_datetime(cls, text: str) -> Optional[str]:
        m = cls._DATETIME_RE.search(text)
        if not m:
            return None
        stamp = f"{m.group(1)} {m.group(2).replace(' ', '')}"
        try:
            return datetime.strptime(stamp, "%m/%d/%Y %I:%M%p").isoformat()
        except ValueError:
            return None

    @classmethod
    def parse_header(cls, text: str) -> Dict[str, Any]:
        """Extract the report header fields from the full document text."""
        version = None
        version_match = re.search(r"ZEISS\s+CALYPSO\s+([\d.]+)", text)
        if version_match:
            version = version_match.group(1)

        return {
            "software": "ZEISS CALYPSO" if "ZEISS" in text else None,
            "version": version,
            "part_name": cls._search(r"Partname\s+(\S+)", text, reject_labels=True),
            "drawing_number": cls._search(r"Drawingnumber\s+(\S+)", text, reject_labels=True),
            "order_number": cls._search(r"Ordernumber\s+(\d+)", text),
            "part_ident": cls._search(r"Partident\s+(\S+)", text, reject_labels=True),
            "cmm_type": cls._search(r"CMMType\s+(\S+)", text, reject_labels=True),
            "cmm_no": cls._search(r"CMMNo\.\s+(\d+)", text),
            "operator": cls._search(r"Operator\s+(\S+)", text, reject_labels=True),
            "measured_datetime": cls._parse_measured_datetime(text),
            "num_measured_values": cls._to_int(cls._search(r"Numbermeasuredvalues\s+(\d+)", text)),
            "num_values_red": cls._to_int(cls._search(r"Numbervalues:red\s+(\d+)", text)),
            "measurement_duration": cls._search(r"MeasurementDuration\s+([\d:.]+)", text),
        }

    @classmethod
    def parse_characteristics(cls, text: str) -> List[Dict[str, Any]]:
        """Extract every measured-characteristic row from the document text."""
        rows: List[Dict[str, Any]] = []
        for line in text.splitlines():
            m = cls._ROW_RE.match(line.strip())
            if not m:
                continue
            g = m.groupdict()
            out_of_tol = cls._to_float(g["out_of_tol"])
            rows.append(
                {
                    "name": g["name"],
                    "measured_value": cls._to_float(g["measured"]),
                    "unit": cls._UNIT_NAMES.get(g["unit"], g["unit"]),
                    "nominal_value": cls._to_float(g["nominal"]),
                    "tol_plus": cls._to_float(g["tol_plus"]),
                    "tol_minus": cls._to_float(g["tol_minus"]),
                    "deviation": cls._to_float(g["deviation"]),
                    "out_of_tol": out_of_tol,
                    "in_tolerance": out_of_tol is None,
                }
            )
        return rows

    @classmethod
    def build_report_id(cls, header: Dict[str, Any], source_file: str) -> str:
        """
        Build a stable, unique identifier for a report.

        Prefers the intrinsic identity of the measurement (part ident + part
        name + measurement time) so the same report is deduplicated even if the
        file is copied or renamed. Falls back to the source filename when those
        fields cannot be parsed.
        """
        key_parts = [
            header.get("part_ident"),
            header.get("part_name"),
            header.get("measured_datetime"),
        ]
        if all(key_parts):
            raw = "|".join(str(p) for p in key_parts)
        else:
            raw = source_file
        digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
        return f"calypso-{digest}"

    @classmethod
    def parse_pdf(cls, path: str) -> Dict[str, Any]:
        """
        Parse a single ZEISS CALYPSO report PDF into a structured dict.

        :raises ValueError: if no measured characteristics can be found (i.e.
            the file does not look like a CALYPSO report).
        """
        text_parts: List[str] = []
        with pdfplumber.open(path) as pdf:
            page_count = len(pdf.pages)
            for page in pdf.pages:
                text_parts.append(page.extract_text() or "")
        full_text = "\n".join(text_parts)

        header = cls.parse_header(full_text)
        characteristics = cls.parse_characteristics(full_text)

        if not characteristics:
            raise ValueError(f"No CALYPSO measurement rows found in '{path}'")

        return {
            "report_id": cls.build_report_id(header, path),
            "page_count": page_count,
            "header": header,
            "characteristics": characteristics,
            "characteristic_count": len(characteristics),
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
    "directory": None,       # folder holding the report PDFs (required)
    "host": "0.0.0.0",       # bind address
    "port": 8756,            # bind port
    "pattern": "*.pdf",      # glob for report files
    "recursive": False,      # recurse into subfolders
    "token": None,           # optional X-Auth-Token shared secret
    "lookback_hours": None,  # default look-back window; None/0 = parse all files
}


class Config:
    """Effective runtime configuration (populated in main(), read by handlers)."""

    directory: Optional[str] = None
    host: str = "0.0.0.0"
    port: int = 8756
    pattern: str = "*.pdf"
    recursive: bool = False
    token: Optional[str] = None
    default_lookback_hours: Optional[float] = None


CONFIG = Config()


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
        "service": "zeiss-calypso-report-server",
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
    """Lightweight listing (no PDF parsing) of files in the window."""
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
    include_characteristics: bool = True,
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
            report = CalypsoReportParser.parse_pdf(path)
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
        if not include_characteristics:
            report.pop("characteristics", None)
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

    report = CalypsoReportParser.parse_pdf(path)
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
    server_version = "ZeissCalypsoReportServer/1.0"

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
    parser = argparse.ArgumentParser(description="ZEISS CALYPSO report JSON-RPC server")
    # CLI defaults are None so we can tell whether the user actually passed a
    # value (which overrides the config file) versus left it to the config.
    parser.add_argument("--config", "-c", default=os.environ.get("CALYPSO_CONFIG"),
                        help="Path to a JSON config file (default: config.json beside this script)")
    parser.add_argument("--directory", "-d", help="Directory containing CALYPSO report PDFs")
    parser.add_argument("--host", help="Bind address (default: 0.0.0.0)")
    parser.add_argument("--port", "-p", type=int, help="Bind port (default: 8756)")
    parser.add_argument("--pattern", help="Glob pattern for report files (default: *.pdf)")
    parser.add_argument("--recursive", action=argparse.BooleanOptionalAction, default=None,
                        help="Recurse into subdirectories (--recursive / --no-recursive)")
    parser.add_argument("--lookback-hours", type=float, dest="lookback_hours",
                        help="Default look-back window in hours when a caller passes no time filter")
    parser.add_argument("--token",
                        help="Optional shared secret; if set, clients must send it as the X-Auth-Token header")
    args = parser.parse_args()

    # Resolve the config file: explicit --config/$CALYPSO_CONFIG is required to
    # exist; the implicit config.json beside the script is optional.
    explicit_config = args.config
    config_path = explicit_config or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "config.json"
    )
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
    CONFIG.token = args.token or file_cfg.get("token") or os.environ.get("CALYPSO_RPC_TOKEN")

    if not CONFIG.directory:
        raise SystemExit(
            "No report directory configured. Set 'directory' in the config file "
            "or pass --directory."
        )
    if not os.path.isdir(CONFIG.directory):
        raise SystemExit(f"Directory does not exist: {os.path.abspath(CONFIG.directory)}")

    lb = CONFIG.default_lookback_hours
    httpd = ThreadingHTTPServer((CONFIG.host, CONFIG.port), RpcHandler)
    print(f"ZEISS CALYPSO report server listening on http://{CONFIG.host}:{CONFIG.port}/rpc")
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
