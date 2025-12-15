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
        stopbits: int,
        parity: ParityType,
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
            parity=ParityType[parity].value,
            xonxoff=xonxoff,
            rtscts=rtscts,
            dsrdtr=dsrdtr,
            write_timeout=write_timeout,
        )
        super().__init__()

    def connect(self) -> int:
        try:
            self.__client.open()
        except SerialException as e:
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

    def receive(self, buffer_size) -> str:
        return super().receive(buffer_size)

    def disconnect(self):
        return super().disconnect()
