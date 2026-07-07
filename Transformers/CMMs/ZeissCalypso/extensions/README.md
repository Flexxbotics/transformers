# ZEISS CALYPSO Report Server

A standalone JSON-RPC server that runs on the ZEISS CMM Windows 11 PC. It scans
a directory of ZEISS CALYPSO report PDFs, filters them by file modified time,
parses each into a structured dictionary, and returns the results as JSON over
HTTP. A Flexx device driver connects to it and calls `get_reports` to pull
inspection data.

```
 Flexx device driver  ──HTTP/JSON-RPC──▶  calypso_report_server.py
 (HttpRest protocol)                      (on the CMM PC)
                                          ├─ RpcHandler / dispatch
                                          └─ CalypsoReportParser (pdfplumber)
                                                 │
                                                 ▼
                                    C:\CALYPSO\reports\*.pdf
```

## Install & run (on the CMM PC)

Requires Python 3.10+.

```powershell
cd FlexxConnectServer\src\main\python\adapters\zeiss_calypso_report_server
pip install -r requirements.txt

# Edit config.json (directory, port, look-back...), then just:
python calypso_report_server.py
```

## Configuration file

Settings live in **`config.json`** next to the script (auto-loaded). Point at a
different file with `--config <path>` or the `CALYPSO_CONFIG` env var. Windows
paths must use escaped backslashes (`\\`) in JSON.

```jsonc
{
  "directory": "C:\\CALYPSO\\reports",  // folder holding the report PDFs (required)
  "host": "0.0.0.0",                     // bind address (127.0.0.1 = local only)
  "port": 8756,                          // bind port
  "pattern": "*.pdf",                    // glob for report files
  "recursive": false,                    // recurse into subfolders
  "token": null,                         // optional X-Auth-Token shared secret
  "lookback_hours": 24                   // default look-back window; null/0 = parse all files
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
| `--config`, `-c` | — | `config.json` beside script | Path to the JSON config file |
| `--directory`, `-d` | `directory` | *(required)* | Folder containing the report PDFs |
| `--host` | `host` | `0.0.0.0` | Bind address |
| `--port`, `-p` | `port` | `8756` | Bind port |
| `--pattern` | `pattern` | `*.pdf` | Glob for report files |
| `--recursive` / `--no-recursive` | `recursive` | off | Recurse into subfolders |
| `--lookback-hours` | `lookback_hours` | none | Default look-back window in hours |
| `--token` | `token` | `$CALYPSO_RPC_TOKEN` | Optional `X-Auth-Token` shared secret |

```powershell
# One-off run overriding the configured port and look-back:
python calypso_report_server.py --port 9000 --lookback-hours 8
```

Open Windows Firewall for the chosen port so the Flask host can reach it.
To keep it running across reboots, register it as a Windows service (e.g. with
[NSSM](https://nssm.cc/)) pointing at `python calypso_report_server.py`.

Health check: `GET http://<pc>:8756/health`.

## JSON-RPC API

`POST /rpc` with a JSON-RPC 2.0 envelope. Params are always an **object**
(named), never positional.

### `ping()`
Liveness/config check. Returns the configured directory, port, default
look-back window, and server time.

### `list_reports(...)`
Lightweight listing — file metadata only, **no PDF parsing**. Good for polling.
Filters (all optional):

- `since` — only files modified on/after this time (epoch seconds or ISO-8601)
- `until` — only files modified on/before this time
- `modified_within_hours` — only files modified in the last N hours
- `pattern` — override the glob for this call
- `recursive` — override recursion for this call
- `directory` — override the scanned directory for this call

### `get_reports(...)`
Same filters as above, plus `include_characteristics` (default `true`). Parses
every report in the window and returns them keyed by a stable `report_id`.
Reports with the same intrinsic identity (part ident + part name + measurement
time) are de-duplicated, keeping the newest file. Per-file parse failures are
collected in `errors` rather than failing the whole call.

### `get_report(file=...)`
Parse a single report by filename (within the directory) or an absolute path.

## Response shape

```jsonc
{
  "directory": "C:\\CALYPSO\\reports",
  "count": 2,
  "reports": {
    "calypso-b30b19f0d202edb2": {
      "report_id": "calypso-b30b19f0d202edb2",
      "source_file": "82409 (1).pdf",
      "path": "C:\\CALYPSO\\reports\\82409 (1).pdf",
      "file_modified": "2026-06-18T10:48:00",
      "file_modified_epoch": 1783436446.94,
      "page_count": 1,
      "header": {
        "software": "ZEISS CALYPSO",
        "version": "8.0.08",
        "part_name": "82409-AP_Ver_01.07.26",
        "drawing_number": null,
        "order_number": null,
        "part_ident": "2300657064#3",
        "cmm_type": "DURAMAX",
        "cmm_no": "735425100963",
        "operator": "Master",
        "measured_datetime": "2026-06-18T10:48:00",
        "num_measured_values": 14,
        "num_values_red": 2,
        "measurement_duration": "00:06:57.0"
      },
      "characteristic_count": 14,
      "characteristics": [
        {
          "name": "#1_Free_Spread_Top_3d",
          "measured_value": 7.6271,
          "unit": "inch",
          "nominal_value": 7.626,
          "tol_plus": 0.02,
          "tol_minus": 0.0,
          "deviation": 0.0011,
          "out_of_tol": null,
          "in_tolerance": true
        }
        // ...
      ]
    }
  },
  "errors": []
}
```

`out_of_tol` is non-null only for characteristics that fell outside tolerance
(the red values in the report); `in_tolerance` is a convenience boolean.

## Calling from the Flexx side

`flexx_client_example.py` shows two clients:

1. `CalypsoReportClient` — dependency-free (`requests`), for quick tests:

   ```powershell
   python flexx_client_example.py --url http://<cmm-pc>:8756 --hours 24
   ```

2. `get_reports_via_httprest(base_url)` — routes the call through the existing
   `protocols.http_rest.HttpRest` protocol, so a device driver pulls reports
   through the same protocol layer as every other device. Run it inside the
   Flask app context (HttpRest reads `current_app.config['logger']`).

## Notes

- Only the standard library is needed to *run* the server; `pdfplumber` is used
  only when a PDF is actually parsed.
- Time filtering is based on the file's OS modified time on the CMM PC. Naive
  ISO timestamps are interpreted in the server's local time zone (matching the
  `file_modified` values returned to callers).
- The parser targets the ZEISS CALYPSO PDF layout. To support other export
  formats later, add a parser module and dispatch on file type in
  `report_server.py`.
