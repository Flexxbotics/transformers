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

from pymodbus.pdu import ModbusPDU, ExceptionResponse
from pymodbus.pdu.file_message import FileRecord
from pymodbus.client import ModbusTcpClient, ModbusSerialClient
from exceptions.flexxCoreExceptions import ServerErrorException
from protocols.abstract_protocol import AbstractProtocol
from time import sleep


class ModbusBase(AbstractProtocol):
    def __init__(self, client):
        self.client: ModbusSerialClient | ModbusTcpClient = client
        super().__init__()

    ###################
    ## READ
    ###################

    def read_coils(self, address: int, count: int) -> ModbusPDU:
        return self.__check_response(
            self.client.read_coils, address=address, count=count
        )

    def read_discrete_inputs(self, address: int, count: int) -> ModbusPDU:
        return self.__check_response(
            self.client.read_discrete_inputs, address=address, count=count
        )

    def read_input_register(self, address: int, count: int = 1) -> ModbusPDU:
        return self.__check_response(
            self.client.read_input_registers, address=address, count=count
        )

    def read_holding_register(self, address: int, count: int = 1) -> ModbusPDU:
        return self.__check_response(
            self.client.read_holding_registers, address=address, count=count
        )

    def read_fifo_queue(self, address: int) -> ModbusPDU:
        return self.__check_response(self.client.read_fifo_queue, address=address)

    def read_file_record(self, records: list[FileRecord]) -> ModbusPDU:
        return self.__check_response(self.client.read_file_record, records=records)

    ###################
    ## WRITE
    ###################

    def write_single_coil(self, address: int, value: bool = False) -> ModbusPDU:
        return self.__check_response(
            self.client.write_coil, address=address, value=value
        )

    def write_multiple_coils(self, address: int, values: list[bool] = []) -> ModbusPDU:
        if not values:
            raise ValueError("Unable to write null values")
        return self.__check_response(
            self.client.write_coils, address=address, values=values
        )

    def write_single_register(self, address: int, value: bool = False) -> ModbusPDU:
        return self.__check_response(
            self.client.write_register, address=address, value=value
        )

    def write_multiple_registers(
        self, address: int, values: list[int] = []
    ) -> ModbusPDU:
        if not values:
            raise ValueError("Unable to write null values")
        return self.__check_response(
            self.client.write_registers, address=address, values=values
        )

    def mask_write_register(
        self, address: int, and_mask: int, or_mask: int
    ) -> ModbusPDU:
        return self.__check_response(
            self.client.mask_write_register, address, and_mask, or_mask
        )

    def write_file_record(self, records: list[FileRecord]) -> ModbusPDU:
        if not records:
            raise ValueError("Unable to write empty records")
        return self.__check_response(self.client.write_file_record, records)

    def __check_response(self, func, *args, **kwargs) -> ModbusPDU:
        self.client.connect()
        response = func(*args, **kwargs)
        if isinstance(response, ExceptionResponse):
            self.client.close()
            raise ServerErrorException
        self.client.close()
        return response


class ModbusTCP(ModbusBase):
    def __init__(self, ip_address: str, port: int = 502):
        super().__init__(client=ModbusTcpClient(host=ip_address, port=port))

    def connect(self):
        return self.client.connect()

    def disconnect(self):
        return self.client.close()

    def send(self, data):
        super().send(data)

    def receive(self, buffer_size):
        return super().receive(buffer_size)


class ModbusSerial(ModbusBase):
    def __init__(self, port: str, options: object):
        super().__init__(client=ModbusSerialClient(port=port, **options))
