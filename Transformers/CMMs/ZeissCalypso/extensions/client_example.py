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

Example client for the ZEISS CALYPSO report server.

Two ways to call it are shown:

1. ``CalypsoReportClient`` - a dependency-free client using ``requests``
   directly. Useful for quick tests or non-Flexx callers.

2. ``get_reports_via_httprest`` - how a Flexx device driver would call the
   server using the existing ``protocols.http_rest.HttpRest`` protocol, so the
   call goes through the same protocol layer as every other device.
"""
from __future__ import annotations

import itertools
from typing import Any, Dict, Optional

# ---------------------------------------------------------------------------
# 1. Standalone client (requests)
# ---------------------------------------------------------------------------
import requests

_id_counter = itertools.count(1)


class CalypsoReportClient:
    def __init__(self, base_url: str, token: Optional[str] = None, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.headers = {"Content-Type": "application/json"}
        if token:
            self.headers["X-Auth-Token"] = token

    def call(self, method: str, **params: Any) -> Any:
        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
            "id": next(_id_counter),
        }
        resp = requests.post(
            f"{self.base_url}/rpc", json=payload, headers=self.headers, timeout=self.timeout
        )
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            raise RuntimeError(f"RPC error {data['error']['code']}: {data['error']['message']}")
        return data["result"]

    def ping(self) -> Dict[str, Any]:
        return self.call("ping")

    def list_reports(self, **filters: Any) -> Dict[str, Any]:
        return self.call("list_reports", **filters)

    def get_reports(self, **filters: Any) -> Dict[str, Any]:
        return self.call("get_reports", **filters)

    def get_report(self, file: str) -> Dict[str, Any]:
        return self.call("get_report", file=file)


# ---------------------------------------------------------------------------
# 2. Via the Flexx HttpRest protocol (how a device driver would do it)
# ---------------------------------------------------------------------------
def get_reports_via_httprest(base_url: str, modified_within_hours: float = 24.0) -> Dict[str, Any]:
    """
    Call the report server through the Flexx HttpRest protocol. Run this from
    within the Flask app context (HttpRest reads current_app.config['logger']).
    """
    import json

    from protocols.http_rest import HttpRest  # noqa: WPS433 (import inside fn is intentional)

    client = HttpRest(base_url=base_url, timeout=30.0)
    client.connect()
    try:
        body = {
            "jsonrpc": "2.0",
            "method": "get_reports",
            "params": {"modified_within_hours": modified_within_hours},
            "id": 1,
        }
        raw = client.post("/rpc", body=body)
        data = json.loads(raw)
        if "error" in data:
            raise RuntimeError(f"RPC error {data['error']['code']}: {data['error']['message']}")
        return data["result"]
    finally:
        client.disconnect()


if __name__ == "__main__":
    import argparse
    import json

    ap = argparse.ArgumentParser(description="Query the ZEISS CALYPSO report server")
    ap.add_argument("--url", default="http://localhost:8756", help="Base URL of the report server")
    ap.add_argument("--token", default=None, help="X-Auth-Token if the server requires one")
    ap.add_argument("--hours", type=float, default=None, help="Only reports modified within the last N hours")
    ap.add_argument("--summary", action="store_true",
                    help="Print only a one-line summary per report instead of the full JSON")
    args = ap.parse_args()

    c = CalypsoReportClient(args.url, token=args.token)
    print("ping:", json.dumps(c.ping(), indent=2))

    filters: Dict[str, Any] = {}
    if args.hours is not None:
        filters["modified_within_hours"] = args.hours
    result = c.get_reports(**filters)
    print(f"\n{result['count']} report(s) found in {result['directory']}")

    # One-line summary per report.
    for report_id, report in result["reports"].items():
        header = report["header"]
        print(
            f"  {report_id}  part={header.get('part_name')}  "
            f"ident={header.get('part_ident')}  chars={report['characteristic_count']}  "
            f"red={header.get('num_values_red')}"
        )

    # Full JSON structure of each record (default; suppress with --summary).
    if not args.summary:
        for report_id, report in result["reports"].items():
            print(f"\n===== {report_id} =====")
            print(json.dumps(report, indent=2))
