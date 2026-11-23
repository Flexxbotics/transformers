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

# 3rd party python library imports
from pymodbus.pdu import ModbusPDU
from werkzeug.exceptions import HTTPException

from data_models.device import Device

# Flexxbotics class imports
from protocols.modbus import ModbusTCP
from transformers.abstract_device import AbstractDevice
from exceptions.flexxCoreExceptions import (
    FlexxCoreException,
    BadRequestException,
    ServerErrorException,
    ServiceUnavailableException,
)
import json


class WagoModbusTCP(AbstractDevice):
    """
    The device transformer for the Wago Interface.
    """

    def __init__(self, device: Device):
        """
        Wago device class. Inherits AbstractDevice class.  Reads metaData "ip_address"
        (required; ValueError when missing) and "port" (default 502).  Does not connect.
        :param device:
                    the device object

        :return:    a new instance
        """
        # Pass the attributes to the superclass.
        super().__init__(device)

        # Setup specifics to the device interface
        self.meta_data = device.metaData or {}
        self.address = self.meta_data.get("ip_address", "")
        self.port = int(self.meta_data.get("port") or 502)  # default is 502
        if not self.address:
            raise ValueError("WagoModbusTCP: metaData must contain 'ip_address'")

        # The connection and I/O discovery run in _connect (no device I/O here)
        self._client = None
        self._connected = False

        self._connection_retry_limit = 10

    def __del__(self):
        try:
            self._disconnect()
        except Exception:
            pass

    ####
    # Data Model Functions
    ####

    def _execute_command_v2(self, command_name: str, command_args: str) -> str:
        """
        Executes the command sent to the device.

        connect / disconnect
            Connect and read the I/O map / drop the client.
            Returns {"status": "ok", "connected": true|false}.
        get_io_states
            Reads every available digital output and input.
            Returns {"status": "ok", "do": {"<n>": 0|1, ...}, "di": {"<n>": 0|1, ...}}.
        set_output
            Args: output (int, required), state ("high" or "low", required).
            Returns {"status": "ok", "output": <n>, "state": "high"|"low"}.

        Errors raise: 400 for an unknown command or missing, non-JSON or invalid args (an output
        number beyond the coupler's outputs gives 500), 503 when the connection and I/O discovery
        fail or on a raw socket error, 500 otherwise (pymodbus reports a coupler lost after
        connecting, or a timeout, as a ModbusException).
        """
        args = self._parse_args(command_args)
        self._info(message=f"Sending command: {command_name}")
        try:
            if command_name == "connect":
                self._connect()
                return json.dumps({"status": "ok", "connected": True})

            if command_name == "disconnect":
                self._disconnect()
                return json.dumps({"status": "ok", "connected": False})

            if command_name == "get_io_states":
                self._ensure_connected()
                do = self._read_digital_output(start=0, count=self.available_do) if self.available_do else {}
                di = self._read_digital_input(start=0, count=self.available_di) if self.available_di else {}
                return json.dumps({"status": "ok", "do": do, "di": di}, default=str)

            if command_name == "set_output":
                output = self._require(args, "output")
                state = self._require(args, "state")
                try:
                    output = int(output)
                except (TypeError, ValueError):
                    raise BadRequestException(f"Arg 'output' must be an integer, got {output!r}")
                if state == "high":
                    toggle = True
                elif state == "low":
                    toggle = False
                else:
                    raise BadRequestException(f"Arg 'state' must be 'high' or 'low', got {state!r}")
                self._set_digital_output(start=output, values=[toggle])
                return json.dumps({"status": "ok", "output": output, "state": state})

        except Exception as e:
            self._raise_device_error(f"Command '{command_name}'", e)

        raise BadRequestException(f"Unknown command '{command_name}'")

    def _read_status(self, function: str = None) -> str:
        try:
            self._ensure_connected()
            status: ModbusPDU = self._get_status()
            return "RUNNING" if not status.registers[0] else "FAULT"
        except Exception as e:
            self._error(message=f"Status read failed: {e}")
            self._connected = False
            return "ERROR"

    def _read_interval_data(self) -> str:
        pass

    def _read_digital_output(self, start: int, count: int) -> dict[int, bool]:
        self._ensure_connected()
        response = self._get_digital_output(start=start, count=count)
        staged = {x: int(response.bits[x - start]) for x in range(start, start + count)}
        self._update_io_mapping(staged=staged, key="do")
        return staged

    def _read_digital_input(self, start: int, count: int) -> dict[int, bool]:
        self._ensure_connected()
        response = self._get_digital_input(start=start, count=count)
        staged = {x: int(response.bits[x - start]) for x in range(start, start + count)}
        self._update_io_mapping(staged=staged, key="di")
        return staged

    def _read_multiple_inputs(self, inputs_list: list) -> str:
        self._ensure_connected()
        input_values = ""
        for input in inputs_list:
            input_value = str(self._read_digital_input(start=int(input), count=1)).split(":")[1].strip("}").strip("{").strip()
            input_values = input_values + input_value + ","
        input_values = input_values[:-1]
        return input_values

    def _set_available_io(self) -> None:
        io_map = self._get_available_io()
        self._reset_io_mapping()
        self.io_mapping = io_map
        self.available_ai = len(io_map["ai"])
        self.available_ao = len(io_map["ao"])
        self.available_di = len(io_map["di"])
        self.available_do = len(io_map["do"])

    def _set_digital_output(self, start: int, values: list[bool]) -> None:
        self._ensure_connected()
        if len(values) + start > self.available_do:
            raise ValueError("Digital I/O value out of expected range")
        self._info(
            message="Setting digital output: " + str(start) + " to " + str(values)
        )
        ret = self._write_digital_output(start=start, values=values)

        if not ret:
            raise ServerErrorException

        staged = {x + start: values[x] for x in range(len(values))}
        self._update_io_mapping(staged=staged, key="do")

    ####
    # Connection
    ####
    def _connect(self) -> None:
        """Connect and read the coupler's I/O map (the I/O methods need it)."""
        if self._connected and self._client is not None:
            return
        try:
            self._client = ModbusTCP(ip_address=self.address, port=self.port)
            if not self._client.connect():
                raise ConnectionError("Modbus connect failed")

            # Instantiate available I/O
            self._set_available_io()
        except Exception as e:
            self._client = None
            self._connected = False
            raise ServiceUnavailableException(
                f"WagoModbusTCP: connect failed ({self.address}:{self.port}): {e}"
            ) from e
        self._connected = True

    def _disconnect(self) -> None:
        try:
            if self._client is not None:
                self._client.disconnect()
        finally:
            self._client = None
            self._connected = False

    def _ensure_connected(self) -> None:
        if not self._connected or self._client is None:
            self._connect()

    ####
    # Request Functions
    # TODO: Abstract this into AbstractDevicetransformer and only overide what is needed
    ####
    def _get_status(self) -> ModbusPDU:
        return self._client.read_holding_register(0x1020, 1)

    def _get_available_io(self) -> dict[str, dict[int, bool]]:
        ao_len = self._client.read_holding_register(0x1022, 1)
        ai_len = self._client.read_holding_register(0x1023, 1)
        do_len = self._client.read_holding_register(0x1024, 1)
        di_len = self._client.read_holding_register(0x1025, 1)

        # TODO: Update this to handle analog
        do_signals = self._get_digital_output(0, do_len.registers[0])
        di_signals = self._get_digital_input(0, di_len.registers[0])

        return {
            "ao": {i: False for i in range(ao_len.registers[0])},
            "ai": {i: False for i in range(ai_len.registers[0])},
            "do": {i: bit for i, bit in enumerate(do_signals.bits)},
            "di": {i: bit for i, bit in enumerate(di_signals.bits)},
        }

    def _get_digital_output(self, start: int, count: int) -> ModbusPDU:
        return self._client.read_coils(address=start + 0x0200, count=count)

    def _get_digital_input(self, start: int, count: int) -> ModbusPDU:
        return self._client.read_discrete_inputs(address=start, count=count)

    def _write_digital_output(self, start: int, values: list[bool]) -> bool:
        if len(values) > 1:
            response = self._client.write_multiple_coils(address=start, values=values)
            return response.address == start and response.count == len(values)
        else:
            response = self._client.write_single_coil(address=start, value=values[0])
            return response.address == start and response.bits == values

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
