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

import json

from data_models.device import Device
from protocols.http_rest import HttpRest
from transformers.abstract_device import AbstractDevice


class ZeissCalypso(AbstractDevice):
    """
    Transformer for a ZEISS CALYPSO CMM.

    Rather than talking to the CMM directly, this transformer talks to the
    standalone Calypso report server (see
    FlexxConnectServer/.../adapters/zeiss_calypso_report_server) running on the
    CMM's Windows PC. That server scans a directory of CALYPSO report PDFs and
    exposes them over JSON-RPC 2.0. This transformer forwards commands to it via
    the HttpRest protocol and returns the structured inspection data.
    """

    def __init__(self, device: Device):
        """
        :param Device:
                    the device object

        :return:    a new instance
        """
        super().__init__(device)

        # Connection metadata: the host/port the report server is bound to, and
        # an optional shared secret (X-Auth-Token) if the server requires one.
        self.meta_data = device.metaData
        self.address = self.meta_data["ip_address"]
        self.port = self.meta_data["port"]
        self.token = self.meta_data.get("token") or None

        self.base_url = f"http://{self.address}:{self.port}"

        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["X-Auth-Token"] = self.token

        self.client = HttpRest(base_url=self.base_url, headers=headers, timeout=30.0)
        self._rpc_id = 0

    def __del__(self):
        pass

    # ############################################################################## #
    # DEVICE COMMUNICATION METHODS
    # ############################################################################## #

    def _execute_command_v2(self, command_name: str, command_args: str) -> str:
        """
        Executes the command sent to the device.

        Each supported command maps to one JSON-RPC method on the Calypso report
        server. The arguments object is forwarded as the RPC params, so callers
        can pass the same time filters the server understands
        (``since``, ``until``, ``modified_within_hours``, ``pattern``,
        ``recursive``, ``directory``, ``include_characteristics``, ``file``).

        :param command_name:
                    the command to be executed:
                    "ping", "list_reports", "get_reports", or "get_report"
        :param command_args:
                    json string with the arguments for the command

        :return:    the RPC result encoded as a JSON string.

        :author:    tylerjm@flexxbotics.com
        """
        args = json.loads(command_args) if command_args else {}
        self._info(message="Sending command: " + command_name)

        try:
            if command_name == "ping":
                # Liveness/config check for the report server.
                result = self._rpc("ping")

            elif command_name == "list_reports":
                # Lightweight file listing (no PDF parsing) within the window.
                result = self._rpc("list_reports", args)

            elif command_name == "get_reports":
                # Parse every report in the window into structured records.
                result = self._rpc("get_reports", args)

            elif command_name == "get_report":
                # Parse a single report by filename or absolute path.
                result = self._rpc("get_report", args)

            else:
                self._error(message="Unknown command: " + command_name)
                return "Error executing command"

            return json.dumps(result)

        except Exception as e:
            self._error(message="Failed to send command: " + str(e))
            raise Exception(
                "Error when sending command, did not get response from device: "
                + command_name
            )

    # ############################################################################## #
    # INTERFACE HELPER METHODS
    # ############################################################################## #

    def _rpc(self, method: str, params: dict = None) -> dict:
        """
        Send a single JSON-RPC 2.0 request to the Calypso report server and
        return the ``result`` object.

        :param method:
                    the JSON-RPC method name
        :param params:
                    the params object (defaults to empty)

        :return:    the RPC ``result`` value

        :author:    tylerjm@flexxbotics.com
        """
        self._rpc_id += 1
        envelope = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params or {},
            "id": self._rpc_id,
        }

        raw = self.client.post("/rpc", body=envelope)
        if not raw:
            raise Exception(f"Empty response from Calypso report server for '{method}'")

        data = json.loads(raw)
        if "error" in data:
            error = data["error"]
            raise Exception(
                f"RPC error {error.get('code')}: {error.get('message')}"
            )

        return data.get("result")
