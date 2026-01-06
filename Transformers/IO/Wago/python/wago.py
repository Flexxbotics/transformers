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

from data_models.device import Device

# Flexxbotics class imports
from protocols.modbus import ModbusTCP
from transformers.abstract_device import AbstractDevice
from exceptions.flexxCoreExceptions import ServerErrorException
import json


class Wago(AbstractDevice):
    """
    The device transformer for the Wago Interface.

    :author:    cadenc@flexxbotics.com

    :since:     PBR.3 (7.1.16.3)
    """

    def __init__(self, device: Device):
        """
        Wago device class. Inherits AbstractDevice class.
        :param attributes:
                    attributes of the instance.

        :return:    a new instance
        """
        # Pass the attributes to the superclass.
        super().__init__(device)

        # Setup specifics to the device interface
        self.meta_data = device.metaData
        self.address = self.meta_data["ip_address"]
        self.port = self.meta_data["port"]  # default is 502
        self._client = ModbusTCP(ip_address=self.address, port=self.port)

        # Initialize connection
        # TODO: Maybe extract this out to get better error handling
        self._client.connect()

        # Instantiate available I/O
        self._set_available_io()

        self._connection_retry_limit = 10

    def __del__(self):
        pass

    ####
    # Data Model Functions
    ####

    def _execute_command(self, command: str) -> str:
        # Parse the command from the incoming request
        command_string = command["commandJson"]
        command_json = json.loads(command_string)
        command_name = command_json["command"]
        response = ""
        if command_name == "set_output":
            output = command_json["output"]
            state = command_json["state"]
            if state == "high":
                toggle = True
            elif state == "low":
                toggle = False
            self._set_digital_output(start=int(output), values=[toggle])

    def _read_status(self, function: str = None) -> str:
        status: ModbusPDU = self._get_status()
        return "RUNNING" if not status.registers[0] else "FAULT"

    def _read_interval_data(self) -> str:
        pass

    def _read_digital_output(self, start: int, count: int) -> dict[int, bool]:
        response = self._get_digital_output(start=start, count=count)
        staged = {x: int(response.bits[x - start]) for x in range(start, start + count)}
        self._update_io_mapping(staged=staged, key="do")
        return staged

    def _read_digital_input(self, start: int, count: int) -> dict[int, bool]:
        response = self._get_digital_input(start=start, count=count)
        staged = {x: int(response.bits[x - start]) for x in range(start, start + count)}
        self._update_io_mapping(staged=staged, key="di")
        return staged

    def _read_multiple_inputs(self, inputs_list: list) -> str:
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
