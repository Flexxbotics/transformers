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

from serial import (
    Serial,
    SerialException,
    PARITY_NONE,
    PARITY_EVEN,
    PARITY_ODD,
    PARITY_MARK,
    PARITY_SPACE,
    SerialTimeoutException,
)
from enum import Enum
from protocols.abstract_protocol import AbstractProtocol
import time


class ParityType(Enum):
    PARITY_NONE = PARITY_NONE
    PARITY_EVEN = PARITY_EVEN
    PARITY_ODD = PARITY_ODD
    PARITY_MARK = PARITY_MARK
    PARITY_SPACE = PARITY_SPACE

    def get_parity(self, parity_type : str):
        if parity_type == "none":
            return PARITY_NONE
        elif parity_type == "even":
            return PARITY_EVEN
        elif parity_type == "odd":
            return PARITY_ODD
        elif parity_type == "mark":
            return PARITY_MARK
        elif parity_type == "space":
            return PARITY_SPACE
        else:
            return PARITY_NONE



class Serial(AbstractProtocol):
    def __init__(
        self,
        port: str,
        baudrate: int,
        bytesize: int,
        stopbits: float,
        parity: str,
        xonxoff: bool,
        rtscts: bool,
        dsrdtr: bool,
        write_timeout: float = 10,
    ):
        self.__client = Serial(
            port=port,
            baudrate=baudrate,
            bytesize=bytesize,
            stopbits=stopbits,
            parity=parity,
            xonxoff=xonxoff,
            rtscts=rtscts,
            dsrdtr=dsrdtr,
            write_timeout=write_timeout,
        )
        super().__init__()

    def connect(self) -> int:
        try:
            if self.__client.is_open:
                self.__client.flush()
                self.__client.close()
            self.__client.open()
        except SerialException as e:
            self._error(self, message=str(e))
            return 1
        except Exception as e:
            self._error(self, message=str(e))
            return 1
        return 0

    def send(self, data : str, buffer_size : int = 1024, encoding: str = "utf-8", response_time : float = 0.1, ) -> int:
        try:
            self.connect()
            self.__client.write(data.encode(encoding))
            time.sleep(response_time)
            data = self.receive(buffer_size=buffer_size)
            self.disconnect()
            return data
        except SerialTimeoutException as e:
            self.disconnect()
            self._error(self, message=str(e))
            return -1
        except Exception as e:
            self._error(self, message=str(e))
            return -1

    def receive(self, buffer_size : int = 1024) -> str:
        try:
            return (
                self.__client.read_all()
                .decode("utf-8")
                .replace(">", "")
                .replace("\r", "")
                .replace("\n", "")
                .replace(" ", "")
                .replace("\x02", "")
                .replace("\x17", "")
                .split(",")
            )
        except Exception as e:
            self._error(self, message=str(e))

    def disconnect(self):
        try:
            self.__client.close()
        except Exception as e:
            self._error(self, message=str(e))
