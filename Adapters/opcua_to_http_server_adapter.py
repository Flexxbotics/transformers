import asyncio
import json
import os
from dataclasses import dataclass
from typing import Any, Dict, Optional

import requests
from asyncua import ua, Server


@dataclass(frozen=True)
class RestRoute:
    """Optional: define named UA methods that map to known endpoints."""
    browse_name: str             # UA method name
    request_type: str            # "GET" | "POST" | "PATCH" | "DEL"
    endpoint: str                # e.g. "/devices"  (relative to /api/v2e)
    # If you want to pre-fill required body keys, put them here:
    default_body: Optional[Dict[str, Any]] = None


class RestAdapter:
    """
    Implements the same REST call behavior as tcp_to_http_server_adapter_standalone.py:
    - command dict: {"type": str, "endpoint": str, "body": json}
    - api_base_url = <flask_host> + "/api/v2e"
    - special endpoint token replacement for infeed_index/shelf_index/part_index
    - load_file_to_memory endpoint replacement for <string:file_name>
    - returns string responses
    """
    def __init__(self, flask_host: str, timeout_s: int = 60):
        self.flask_host = flask_host
        self.api_base_url = flask_host.rstrip("/") + "/api/v2e"
        self.timeout_s = timeout_s

    def _apply_endpoint_rules(self, full_url: str, body: Dict[str, Any]) -> str:
        # Mirrors token replacement logic in your TCP adapter :contentReference[oaicite:6]{index=6}
        if any(tok in full_url for tok in ("infeed_index", "shelf_index", "part_index")):
            for k, v in body.items():
                if k in ("infeed_index", "shelf_index", "part_index"):
                    full_url = full_url.replace(k, str(v).strip())

        if "load_file_to_memory" in full_url and "program_name" in body:
            # TCP adapter replaces "<string:file_name>" with program_name :contentReference[oaicite:7]{index=7}
            full_url = full_url.replace("<string:file_name>", str(body["program_name"]))
        return full_url

    def call(self, request_type: str, endpoint: str, body: Dict[str, Any]) -> str:
        request_type = request_type.upper().strip()
        endpoint = endpoint.strip()

        # Build full URL like the TCP adapter :contentReference[oaicite:8]{index=8}
        full_url = self.api_base_url + endpoint
        full_url = self._apply_endpoint_rules(full_url, body)

        try:
            if request_type == "GET":
                r = requests.get(url=full_url, params=body, timeout=self.timeout_s)
                if r.status_code == 200:
                    # TCP adapter sometimes wraps; here we return raw text (common) like >text< pattern
                    return r.text
                return "ERROR"

            if request_type == "POST":
                r = requests.post(
                    url=full_url,
                    json=body,
                    timeout=self.timeout_s,
                    headers={"Content-Type": "application/json"},
                )
                if r.status_code in (201, 204, 200):
                    return r.text if r.text else "OK"
                return "ERROR"

            if request_type == "PATCH":
                r = requests.patch(
                    url=full_url,
                    json=body,
                    timeout=self.timeout_s,
                    headers={"Content-Type": "application/json"},
                )
                if r.status_code in (200, 201):
                    return r.text if r.text else "OK"
                return "ERROR"

            if request_type in ("DEL", "DELETE"):
                r = requests.delete(url=full_url, params=body, timeout=self.timeout_s)
                if r.status_code == 204:
                    return "OK"
                return "ERROR"

            return f"ERROR: unsupported request type {request_type}"
        except Exception as e:
            return f"ERROR: {e}"


async def main():
    # Match your TCP adapter's default idea: FLASK_CONTAINER, fallback localhost (you can change)
    flask_host = os.getenv("FLASK_CONTAINER", "http://127.0.0.1:7081")
    rest = RestAdapter(flask_host=flask_host, timeout_s=60)

    server = Server()
    await server.init()
    server.set_endpoint("opc.tcp://0.0.0.0:4840/flexx/rest-adapter/")
    server.set_server_name("Flexx REST Adapter OPC UA Server")

    idx = await server.register_namespace("urn:flexxbotics:rest-adapter")

    objects = server.nodes.objects
    rest_obj = await objects.add_object(idx, "REST")

    #
    # Generic method: Call(type, endpoint, bodyJson) -> responseText
    #
    async def ua_call(parent, request_type, endpoint, body_json):
        try:
            body = json.loads(body_json) if body_json else {}
        except Exception:
            body = {}
        resp = rest.call(request_type, endpoint, body)
        return [resp]

    await rest_obj.add_method(
        idx,
        "Call",
        ua_call,
        [
            ua.VariantType.String,  # request_type
            ua.VariantType.String,  # endpoint (relative to /api/v2e)
            ua.VariantType.String,  # body JSON
        ],
        [ua.VariantType.String],   # response string
    )

    #
    # Optional: add “named” UA methods that map to specific endpoints
    #
    routes = [
        RestRoute(browse_name="Devices_Get", request_type="GET", endpoint="/devices"),
        # Add more once you have the endpoint catalog you want to expose
    ]

    async def make_route_handler(route: RestRoute):
        async def _handler(parent, body_json):
            try:
                body = json.loads(body_json) if body_json else {}
            except Exception:
                body = {}
            if route.default_body:
                merged = dict(route.default_body)
                merged.update(body)
                body = merged
            resp = rest.call(route.request_type, route.endpoint, body)
            return [resp]
        return _handler

    for route in routes:
        handler = await make_route_handler(route)
        await rest_obj.add_method(
            idx,
            route.browse_name,
            handler,
            [ua.VariantType.String],      # body JSON (single arg for convenience)
            [ua.VariantType.String],
        )

    print("OPC UA REST adapter running.")
    async with server:
        while True:
            await asyncio.sleep(1)


if __name__ == "__main__":
    asyncio.run(main())
