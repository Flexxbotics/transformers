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
import base64
from data_models.device import Device
from transformers.abstract_device import AbstractDevice
from protocols.allen_bradley_eip_logix import EIPLogix, EIPWrite


class AllenBradleyLogix(AbstractDevice):
    """
    Transformer for Allen-Bradley Logix PLCs using EtherNet/IP (CIP) via pycomm3.

    Supported series (all share the same tag-based interface):
      ControlLogix 5570  : 1756-L71/L72/L73/L74/L75  — chassis-based, slot routing required
      ControlLogix 5580  : 1756-L81E/L82E/L83E/L84E/L85E — current flagship, slot routing required
      GuardLogix 5580    : 1756-L81ES/L82ES — safety-rated, same interface as 5580
      CompactLogix 5370  : 1769-L30ER/L33ER/L36ERM — no slot needed
      CompactLogix 5380  : 5069-L306ER/L310ER/L320ER/L330ER — no slot needed

    Connection parameters (all in metaData):
      ip_address   : PLC IP address
      slot         : CPU backplane slot (required for ControlLogix/GuardLogix; omit for CompactLogix)
      timeout      : connection timeout in seconds (default 2.0)
      retry        : connection retry attempts (default 2)
      status_tag   : controller tag to probe for _read_status (default "")

    Allen-Bradley / Logix-specific nuances
    ---------------------------------------
    * **Tag-based access**: Unlike Modbus or S7 (address/offset based), Logix
      uses symbolic tag names defined in Studio 5000.  Tags reference named
      variables directly (e.g. "ConveyorRunning", "Recipe.Speed",
      "Program:Main.PartCount").
    * **Slot routing (ControlLogix only)**: The CPU lives in a chassis backplane.
      The path must include the CPU slot: metaData.slot = 0 → path becomes
      "192.168.1.1/0".  CompactLogix CPUs are standalone — leave slot blank.
    * **Scope — controller vs program tags**:
      Controller-scoped tags: "TagName"
      Program-scoped tags: "Program:ProgramName.TagName"
    * **Structured tags (UDTs and arrays)**:
      UDT member:   "Drive.Fault"
      Array element: "Conveyor[2]"
      Nested:        "Station[1].Recipe.Setpoint"
    * **pycomm3 batch reads**: read_tags() sends all tags in one CIP request,
      dramatically reducing round-trip overhead vs individual reads.
    * **Controller must have EtherNet/IP enabled**: Verify in Studio 5000 that
      the controller's Ethernet port is enabled and the correct IP is set.
    * **No PUT/GET setting needed**: Unlike Siemens S7-1200/1500, Logix PLCs
      do not require a special "allow external access" toggle — EtherNet/IP
      is the native communication mechanism.
    * **BOOL array elements**: Addressed as "BoolArray[0]", not bit-level offsets.
    * **Run/Program mode writes**: Tags can be read in both Run and Program mode,
      but write access to some tags may require the controller to be in Run mode.
    """

    def __init__(self, device: Device):
        super().__init__(device)

        self.meta_data  = device.metaData or {}
        self.ip_address = self.meta_data.get("ip_address", "")
        self.timeout    = float(self.meta_data.get("timeout", 2.0))
        self.retry      = int(self.meta_data.get("retry", 2))
        self.status_tag = self.meta_data.get("status_tag", "")

        # slot is optional — only needed for ControlLogix chassis routing
        raw_slot = self.meta_data.get("slot", "")
        self.slot = int(raw_slot) if str(raw_slot).strip() != "" else None

        self._client = EIPLogix(
            ip_address=self.ip_address,
            timeout=self.timeout,
            retry=self.retry,
            slot=self.slot,
            auto_connect=True,
        )

        self.status = "transformer Initiated"

    def __del__(self):
        try:
            self._client.disconnect()
        except Exception:
            pass

    # ############################################################################## #
    # DEVICE COMMUNICATION METHODS
    # ############################################################################## #

    def _execute_command(self, command: str) -> str:
        """Legacy v1 interface — delegates to _execute_command_v2."""
        command_string = command["commandJson"]
        command_json   = json.loads(command_string)
        command_name   = command_json.get("command", "")
        command_args   = json.dumps({k: v for k, v in command_json.items() if k != "command"})
        self._info(message=f"Sending command: {command_string}")
        return self._execute_command_v2(command_name, command_args)

    def _execute_command_v2(self, command_name: str, command_args: str) -> str:
        """
        Execute a named command against the Allen-Bradley Logix PLC.

        Supported commands
        ------------------
        connect
            Explicitly open EtherNet/IP connection.

        disconnect
            Close EtherNet/IP connection.

        read_tag
            Read a single controller or program tag.
            Args: tag (str)  e.g. "ConveyorRunning", "Program:Main.PartCount"
            Returns: {"tag": "...", "value": <value>, "status": "..."}

        read_tags
            Read multiple tags in a single CIP round-trip.
            Args: tags ([str])
            Returns: {"tags": {"TagA": <val>, "TagB": <val>, ...}}

        write_tag
            Write a value to a single tag.
            Args: tag (str), value (<any>)
            Returns: {"tag": "...", "value": <value>, "status": "..."}

        write_tags
            Write multiple tags in a single CIP round-trip.
            Args: writes ([{"tag": str, "value": <any>}, ...])
            Returns: {"writes": [{"tag": "...", "status": "..."}, ...]}

        get_tag_list
            Retrieve the controller tag list (controller-scoped tags only).
            Args: prefix (str, optional) — filter tags starting with this string
            Returns: {"tags": ["TagA", "TagB", ...]}
        """
        args = json.loads(command_args) if command_args else {}
        self._info(message=f"Sending command: {command_name}")

        try:
            if command_name == "connect":
                rc = self._client.connect()
                return json.dumps({"status": "ok" if rc == 0 else "error", "rc": rc})

            if command_name == "disconnect":
                rc = self._client.disconnect()
                return json.dumps({"status": "ok" if rc == 0 else "error", "rc": rc})

            if command_name == "read_tag":
                tag   = str(args["tag"])
                value = self._client.read_tag(tag)
                return json.dumps({"status": "ok", "tag": tag, "value": value},
                                  default=str)

            if command_name == "read_tags":
                tags   = [str(t) for t in args["tags"]]
                result = self._client.read_tags(tags)
                return json.dumps({"status": "ok", "tags": result}, default=str)

            if command_name == "write_tag":
                tag   = str(args["tag"])
                value = args["value"]
                self._client.write_tag(tag, value)
                return json.dumps({"status": "ok", "tag": tag, "value": value},
                                  default=str)

            if command_name == "write_tags":
                writes = [(str(w["tag"]), w["value"]) for w in args["writes"]]
                self._client.write_tags(writes)
                results = [{"tag": t, "status": "ok"} for t, _ in writes]
                return json.dumps({"status": "ok", "writes": results}, default=str)

            if command_name == "get_tag_list":
                prefix = args.get("prefix", "")
                tags   = self._get_tag_list(prefix=prefix)
                return json.dumps({"status": "ok", "tags": tags}, default=str)

            raise ValueError(f"Unknown command: {command_name}")

        except Exception as e:
            self._error(message=f"Command '{command_name}' failed: {e}")
            raise Exception(f"Error executing command '{command_name}': {e}")

    def _read_interval_data(self) -> str:
        """
        Periodic poll. Reads metaData.status_tag if configured; otherwise
        returns current connection status without a PLC round-trip.
        """
        if self.status_tag:
            return self._execute_command_v2(
                "read_tag", json.dumps({"tag": self.status_tag})
            )
        return json.dumps({"status": self.status})

    def _read_status(self, function: str = None) -> str:
        """
        Check PLC reachability by reading metaData.status_tag.

        If no status_tag is configured, attempts a connection to verify the
        PLC is online and returns "RUNNING" on success.

        Note: Logix PLCs do not expose a dedicated run/stop register over
        EtherNet/IP.  Configure a heartbeat or mode-indicator tag in
        Studio 5000 and set it in metaData.status_tag for reliable status
        differentiation.
        """
        try:
            if self.status_tag:
                self._client.read_tag(self.status_tag)
            else:
                self._client.connect()
            self.status = "RUNNING"
        except Exception as e:
            self._error(message=f"Status check failed: {e}")
            self.status = "ERROR"
        return self.status

    def _read_variable(self, variable_name: str, function: str = None) -> str:
        """
        Read a tag by name.  variable_name is a Logix tag string
        e.g. "ConveyorRunning", "Drive.Speed", "Program:Main.Counter".
        """
        try:
            value = self._client.read_tag(variable_name)
            return str(value) if value is not None else ""
        except Exception as e:
            self._error(message=f"Read variable '{variable_name}' failed: {e}")
            return ""

    def _write_variable(self, variable_name: str, variable_value: str,
                        function: str = None) -> str:
        """
        Write a value to a tag.  Attempts to preserve native type by trying
        int → float → string coercion in that order.
        """
        try:
            value = self._coerce_value(variable_value)
            self._client.write_tag(variable_name, value)
            return variable_value
        except Exception as e:
            self._error(message=f"Write variable '{variable_name}' failed: {e}")
            return ""

    def _read_parameter(self, parameter_name: str, function: str = None) -> str:
        return self._read_variable(parameter_name, function)

    def _write_parameter(self, parameter_name: str, parameter_value: str,
                         function: str = None) -> str:
        return self._write_variable(parameter_name, parameter_value, function)

    def _run_program(self, function: str = None):
        """Not applicable — Logix PLCs execute programs autonomously in tasks."""
        self._info(message="run_program called — Logix PLCs run programs autonomously")
        return ""

    def _read_file_names(self) -> list:
        """Return controller tag names as a proxy for 'file' listing."""
        return self._get_tag_list()

    def _read_file(self, file_name: str) -> str:
        return base64.b64encode(b"").decode("utf-8")

    def _write_file(self, file_name: str, file_data: str):
        pass

    def _load_file(self, file_name: str):
        pass

    # ############################################################################## #
    # INTERFACE HELPER METHODS
    # ############################################################################## #

    def _get_tag_list(self, prefix: str = "") -> list:
        """
        Return controller-scoped tag names from the PLC.
        Filtered by prefix when provided.
        """
        try:
            self._client.connect()
            # pycomm3 LogixDriver exposes get_tag_list() on the underlying driver
            # Access it via the protocol's internal driver through read_tags workaround:
            # We use the driver attribute path that pycomm3 exposes publicly.
            driver = getattr(self._client, "_EIPLogix__driver", None)
            if driver is None:
                return []
            tags = driver.get_tag_list()
            names = [t.tag_name for t in tags] if tags else []
            if prefix:
                names = [n for n in names if n.startswith(prefix)]
            return names
        except Exception as e:
            self._error(message=f"get_tag_list failed: {e}")
            return []

    @staticmethod
    def _coerce_value(raw: str):
        """Try int, then float, then bool keyword, then return as-is string."""
        lower = raw.strip().lower()
        if lower == "true":
            return True
        if lower == "false":
            return False
        try:
            return int(raw)
        except ValueError:
            pass
        try:
            return float(raw)
        except ValueError:
            pass
        return raw
