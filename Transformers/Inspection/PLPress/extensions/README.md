# PL Press Report Server

A standalone JSON-RPC server that runs on the PL Press Windows 11 PC. It scans
a directory of PL Press measurement CSV files, filters them by file modified
time, parses each into a structured dictionary, and returns the results as JSON
over HTTP. A Flexx device driver connects to it and calls `get_reports` to pull
inspection data.

```
 Flexx device driver  ──HTTP/JSON-RPC──▶  pl_press_server.py
 (HttpRest protocol)                      (on the PL Press PC)
                                          ├─ RpcHandler / dispatch
                                          └─ PLPressReportParser (stdlib csv)
                                                 │
                                                 ▼
                                    C:\PLPress\reports\*.txt
```

Each CSV file has a header row and one row per press cycle; all rows share one
job/part, so a file maps to a single report with a header block plus a list of
measurement rows:

```
JobNumber, SapPartNumber, TimeStamp, Height, Taper Left, Taper Right, Total Taper, Serial Number
2300652494, 100035902, 6/22/2026 7:04:23 PM, 4.25979, -0.00032, -0.00030, -0.00062, 0000
```

## Deploy on the PL Press PC (packaged executable)

The server ships as a single self-contained Windows executable
(`pl_press_server.exe`) that bundles Python. **No Python install is required on
the target PC** — it runs on any Windows 11 machine.

1. Copy two files to the PL Press PC, keeping them **in the same folder** — e.g.
   `C:\FlexxPLPress\`:
   - `pl_press_server.exe`
   - `config.json`
2. Edit `config.json` — set `directory` to the folder the press writes CSVs to,
   and set `port`, `lookback_hours`, etc. (see below). Windows paths need
   escaped backslashes (`\\`).
3. Double-click the exe, or from a terminal:
   ```powershell
   cd C:\FlexxPLPress
   .\pl_press_server.exe
   ```
   It prints the bind address and loaded settings, then serves until closed.
4. Allow the port through Windows Firewall so the Flexx host can reach it:
   ```powershell
   New-NetFirewallRule -DisplayName "PL Press Report Server" -Direction Inbound `
     -Protocol TCP -LocalPort 8757 -Action Allow
   ```
5. (Optional) Run it as an always-on Windows service with
   [NSSM](https://nssm.cc/) so it starts on boot and restarts on failure:
   ```powershell
   nssm install PLPressReportServer "C:\FlexxPLPress\pl_press_server.exe"
   nssm set     PLPressReportServer AppDirectory "C:\FlexxPLPress"
   nssm start   PLPressReportServer
   ```
   Setting `AppDirectory` matters — the exe looks for `config.json` in its own
   folder.

Verify it's up:  `http://<press-pc>:8757/health` (or run `client_example.py`
from another machine — see below).

## Run from Python source (development)

Requires Python 3.10+. The server has **no third-party runtime dependencies**.

```powershell
cd Transformers\Inspection\PLPress\extensions

# Edit config.json (directory, port, look-back...), then just:
python pl_press_server.py
```

## Configuration file

Settings live in **`config.json`** next to the executable (or script),
auto-loaded on startup. Point at a different file with `--config <path>` or the
`PLPRESS_CONFIG` env var. Windows paths must use escaped backslashes (`\\`) in
JSON.

```jsonc
{
  "directory": "C:\\PLPress\\reports",  // folder holding the CSV files (required)
  "host": "0.0.0.0",                     // bind address (127.0.0.1 = local only)
  "port": 8757,                          // bind port
  "pattern": "*.txt",                    // glob for report files
  "recursive": false,                    // recurse into subfolders
  "token": null,                         // optional X-Auth-Token shared secret
  "lookback_hours": 1440                 // default look-back window; null/0 = parse all files
}
```

`lookback_hours` is the **default look-back period**: when a caller invokes
`get_reports`/`list_reports` *without* its own time filter, only files modified
within the last N hours are parsed. A caller can always override it per-request
with `since`, `until`, or `modified_within_hours`.

### Command-line overrides

Any config value can be overridden per-run on the command line
(**CLI > config file > built-in default**):

| Flag | Config key | Default | Meaning |
|------|------------|---------|---------|
| `--config`, `-c` | — | `config.json` beside the exe/script | Path to the JSON config file |
| `--directory`, `-d` | `directory` | *(required)* | Folder containing the CSV files |
| `--host` | `host` | `0.0.0.0` | Bind address |
| `--port`, `-p` | `port` | `8757` | Bind port |
| `--pattern` | `pattern` | `*.txt` | Glob for report files |
| `--recursive` / `--no-recursive` | `recursive` | off | Recurse into subfolders |
| `--lookback-hours` | `lookback_hours` | none | Default look-back window in hours |
| `--token` | `token` | `$PLPRESS_RPC_TOKEN` | Optional `X-Auth-Token` shared secret |

```powershell
# One-off run overriding the configured port and look-back:
.\pl_press_server.exe --port 9000 --lookback-hours 8
# (from source:  python pl_press_server.py --port 9000 --lookback-hours 8)
```

Health check: `GET http://<pc>:8757/health`.

## Building the executable yourself

The exe is produced with [PyInstaller](https://pyinstaller.org/) on a Windows
machine. The build config is checked in as `pl_press_server.spec`, so a rebuild
is just:

```powershell
cd Transformers\Inspection\PLPress\extensions
pip install pyinstaller

pyinstaller --clean --noconfirm pl_press_server.spec
```

The standalone exe lands in `dist\pl_press_server.exe`. Because the server uses
only the standard library, the build is small and needs no `--collect-all`
data-collection flags.

Notes:
- Build on the **oldest** Windows version you need to support (an exe built on
  Win11 runs on Win11+).
- `config.json` is intentionally **not** bundled — deploy it beside the exe.
- First launch is slightly slower than subsequent ones: a onefile exe unpacks
  to a temp directory on startup.

## JSON-RPC API

`POST /rpc` with a JSON-RPC 2.0 envelope. Params are always an **object**
(named), never positional.

### `ping()`
Liveness/config check. Returns the configured directory, port, default
look-back window, and server time.

### `list_reports(...)`
Lightweight listing — file metadata only, **no CSV parsing**. Good for polling.
Filters (all optional):

- `since` — only files modified on/after this time (epoch seconds or ISO-8601)
- `until` — only files modified on/before this time
- `modified_within_hours` — only files modified in the last N hours
- `pattern` — override the glob for this call
- `recursive` — override recursion for this call
- `directory` — override the scanned directory for this call

### `get_reports(...)`
Same filters as above, plus `include_measurements` (default `true`). Parses
every file in the window and returns them keyed by a stable `report_id`.
Reports with the same intrinsic identity (job number + SAP part number + first
measurement time) are de-duplicated, keeping the newest file. Per-file parse
failures are collected in `errors` rather than failing the whole call.

### `get_report(file=...)`
Parse a single report by filename (within the directory) or an absolute path.

## Response shape

```jsonc
{
  "directory": "C:\\PLPress\\reports",
  "count": 1,
  "reports": {
    "plpress-1a2b3c4d5e6f7890": {
      "report_id": "plpress-1a2b3c4d5e6f7890",
      "source_file": "2300652494-2050-PL (1).txt",
      "path": "C:\\PLPress\\reports\\2300652494-2050-PL (1).txt",
      "file_modified": "2026-06-23T10:36:46",
      "file_modified_epoch": 1782823006.0,
      "header": {
        "job_number": "2300652494",
        "sap_part_number": "100035902",
        "serial_number": "0000",
        "columns": ["JobNumber", "SapPartNumber", "TimeStamp", "Height",
                    "Taper Left", "Taper Right", "Total Taper", "Serial Number"],
        "first_timestamp": "2026-06-22T19:04:23",
        "last_timestamp": "2026-06-23T10:36:46"
      },
      "measurement_count": 296,
      "measurements": [
        {
          "job_number": "2300652494",
          "sap_part_number": "100035902",
          "timestamp": "2026-06-22T19:04:23",
          "height": 4.25979,
          "taper_left": -0.00032,
          "taper_right": -0.0003,
          "total_taper": -0.00062,
          "serial_number": "0000"
        }
        // ...
      ]
    }
  },
  "errors": []
}
```

## Calling from the Flexx side

The `PLPress` transformer (in `../python/pl_press.py`) is the production
caller — it forwards its `execute_command_v2` commands to this server over the
`HttpRest` protocol.

`client_example.py` shows two standalone clients for testing:

1. `PLPressReportClient` — dependency-free (`requests`), for quick tests:

   ```powershell
   python client_example.py --url http://<press-pc>:8757 --hours 24
   ```

2. `get_reports_via_httprest(base_url)` — routes the call through the
   `protocols.http_rest.HttpRest` protocol, so a device driver pulls reports
   through the same protocol layer as every other device. Run it inside the
   Flask app context (HttpRest reads `current_app.config['logger']`).

## Notes

- The server and its CSV parser use only the Python standard library; the
  packaged exe needs no Python install on the target PC.
- Time filtering is based on the file's OS modified time on the PL Press PC.
  Naive ISO timestamps are interpreted in the server's local time zone
  (matching the `file_modified` values returned to callers).
- The parser maps the known PL Press columns to normalized keys and coerces the
  height/taper columns to numbers and the timestamp to ISO-8601. Unrecognized
  columns are kept under a slugified version of their header name.
