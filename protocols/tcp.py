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

import socket
import select
import time
from flask import current_app
from marshmallow.fields import Boolean

from protocols.abstract_protocol import AbstractProtocol
from typing import Union


class TCP(AbstractProtocol):

    def __init__(
        self,
        address: str,
        port: str | int,
        timeout: float = 2,
        retry: int = 2,
        retry_interval: float = 0.1,
    ):
        super().__init__()
        self.__address = address
        self.__port = int(port)
        self.__timeout = timeout
        self.__retry = retry
        self.__retry_interval = retry_interval
        self.__attempts = 0

        self._logger = current_app.config["logger"]

    def __repr__(self):
        return f"<{self.__class__.__name__}({self.__address, self.__port, self.__timeout, self.__retry})>"

    def __del__(self):
        self._info(self, f"{self.__repr__}: deleted")
        self.disconnect()

    def connect(self) -> int:
        # Enter retry loop
        self.__client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.__client.settimeout(self.__timeout)
        # self._logger.info("Connecting to: " + str(self.__address) + ":" + str(self.__port))
        while self.__attempts < self.__retry:
            try:
                ret = self.__client.connect_ex((self.__address, self.__port))
                if ret == 0:
                    self.__attempts = 0
                    # self._logger.info("Connected to: " + str(self.__address) + ":" + str(self.__port))
                    return ret
                self._warn(
                    self,
                    message=f"{self.__repr__}: connection failed, returned error code '{ret}'. Retrying...",
                )
                # self._logger.info("Failed to connect to: " + str(self.__address) + ":" + str(self.__port))
            except socket.error as e:
                # self._logger.info("Failed to connect to: " + str(self.__address) + ":" + str(self.__port))
                # self._logger.info(str(self.__address) + ":" + str(self.__port) + ": " + str(e))
                self._warn(self, message=f"{str(e)}, Retrying...")

            # Bottom of loop means we failed
            self.__attempts += 1
            time.sleep(self.__retry_interval)

        # Log failure
        self._error(
            self, f"{self.__repr__}: unable to connect, max retry limit reached"
        )

        # Reset attempts and return final error code
        self.__attempts = 0
        return ret

    def send(
        self,
        data: Union[str,bytes],
        buffer_size: int = 1024,
        encoding: str = "utf-8",
        response_time: float = 0.1,
        close_connection: bool = True,
    ) -> str:

        try:
            self.connect()
            self._clear_socket_buffer()
            if isinstance(data, str):
                self.__client.send(data.encode(encoding))
            else:
                self.__client.sendall(data)
            time.sleep(response_time)
            response = self.receive(buffer_size=buffer_size)
            self._logger.debug(f"Response: {str(response)}")
            response = (
                response.strip()
                .replace(">", "")
                .replace("\r", "")
                .replace("\n", "")
                .replace(" ", "")
                .replace("\x02", "")
                .replace("\x17", "")
            )
            if close_connection:
                self.disconnect()
        except Exception as e:
            self._logger.error(f"TCP Error: {str(e)}")
            self.disconnect()

        return response

    def receive(self, buffer_size: int, encoding: str = "utf-8") -> str:
        # Init recv data buffer
        data = ""
        self.__attempts = 0
        # Enter receive loop
        while self.__attempts < self.__retry:
            raw_response = self.__client.recv(buffer_size)
            if raw_response:
                data += str(raw_response.decode(encoding=encoding))
                print("Response: " + data)
                break
            else:
                self.__attempts += 1
                print("attempts: " + str(self.__attempts))
            time.sleep(self.__retry_interval)

        return data

    def _clear_socket_buffer(self):
        while True:
            ready = select.select([self.__client], [], [], 0)
            if not ready[0]:
                break
            try:
                self.__client.recv(4096)  # Adjust buffer size as needed
            except socket.error:
                break

    def disconnect(self):
        self.__client.close()

    def send_without_connect(        self,
        data: Union[str,bytes],
        buffer_size: int = 1024,
        encoding: str = "utf-8",
        response_time: float = 0.1,
        receive: bool = True) -> str:

        response = ""
        try:

            self._clear_socket_buffer()
            if isinstance(data, str):
                self.__client.send(data.encode(encoding))
            else:
                self.__client.sendall(data)
                print("sending", data)
            time.sleep(response_time)
            if receive:
                response = self.receive(buffer_size=buffer_size)
                self._logger.debug(f"Response: {str(response)}")
                response = (
                    response.strip()
                    .replace(">", "")
                    .replace("\r", "")
                    .replace("\n", "")
                    .replace(" ", "")
                    .replace("\x02", "")
                    .replace("\x17", "")
                )

        except Exception as e:
            self._logger.error(f"TCP Error: {str(e)}")
            self.disconnect()

        return response

    def regular_receive(self):
        print("checking for response")
        response = self.__client.recv(1024)
        if response:
            return response
