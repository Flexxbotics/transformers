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
from transformers.abstract_device import AbstractDevice
from protocols.beckhoff_ads_twincat import ADS

# --------------------------------------------------------------------------------------
# Holzman Beckhoff driver class
# --------------------------------------------------------------------------------------

class HolzmanBeckhoff(AbstractDevice):

    def __init__(self, device: Device):
        """
        Beckhoff ADS/TwinCAT device class.

        Expects device.metaData to contain (at minimum):
          - ams_net_id: "x.x.x.x.x.x" (TwinCAT AMS Net ID of the target)
          - ip_address: "a.b.c.d" (optional for pyads, but typically provided)
          - port: int (optional; defaults to 851 or 8193 depending on your setup)
          - timeout: float seconds (optional)
          - retry: int (optional)
          - retry_interval: float seconds (optional)
        """
        try:
            super().__init__(device)

            self.meta_data = device.metaData or {}

            # Common metadata keys
            self.address = self.meta_data.get("ip_address") or self.meta_data.get("address")
            self.port = int(self.meta_data.get("port", 851))  # 851 is common for TwinCAT PLC runtime 1

            # Beckhoff ADS specifics
            self.ams_net_id = self.meta_data.get("ams_net_id")
            self.ams_port = int(self.meta_data.get("ams_port", self.port))

            if not self.ams_net_id:
                raise ValueError("Missing required metaData field: 'ams_net_id'")

            # Connection tuning
            self.timeout = float(self.meta_data.get("timeout", 2.0))
            self.retry = int(self.meta_data.get("retry", 2))
            self.retry_interval = float(self.meta_data.get("retry_interval", 0.1))
            self.auto_connect = bool(self.meta_data.get("auto_connect", True))

            # Beckhoff client (pyads wrapper)
            self.client: ADS | None = None

            # Track state
            self._connected = False

        except Exception as e:
            self._error(str(e))
            raise RuntimeError(f"Holzman init failed: {e}") from e

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
            if command_name == "connect":
                self._connect(timeout_s=10)
            # ---- Unknown command ----
            return "UNKNOWN_COMMAND"

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
            pass
        elif function == "":  # Some string
            pass
        else:
            pass
        return status

    def _read_variable(self, variable_name: str, function: str = None) -> str:
        value = ""
        if function is None:
            pass
        elif function == "":  # Some string
            pass
        else:
            pass
        return value

    def _write_variable(self, variable_name: str, variable_value: str, function: str = None) -> str:
        value = ""
        if function is None:
            pass
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

    def _connect(self, *, timeout_s: int = 10) -> None:
        """
        Create ADS client and open connection.

        timeout_s is accepted to match AbstractDevice expectations; we pass
        the effective timeout into ADS (seconds).
        """
        # If already connected, no-op
        if getattr(self, "_connected", False) and self.client is not None:
            return

        # Build a new ADS client instance
        self.client = ADS(
            ams_net_id=str(self.ams_net_id),
            ams_port=int(self.ams_port),
            ip_address=str(self.address) if self.address else None,
            timeout=float(self.timeout if self.timeout is not None else timeout_s),
            retry=int(self.retry),
            retry_interval=float(self.retry_interval),
            auto_connect=self.auto_connect,
        )

        rc = self.client.connect()
        if rc != 0:
            self._connected = False
            raise ConnectionError(
                f"Holzman: ADS connect failed (ams_net_id={self.ams_net_id}, "
                f"ams_port={self.ams_port}, ip={self.address})"
            )

        self._connected = True

    def _disconnect(self) -> None:
        """
        Close ADS connection and release client.
        """
        if not getattr(self, "_connected", False):
            # Still clear client if present
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

    def _ensure_connected(self) -> None:
        if not self._connected:
            # Auto-connect to match typical driver expectations.
            self._connect(timeout_s=10)