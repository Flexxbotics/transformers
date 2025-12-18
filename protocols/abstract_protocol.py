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

from abc import ABC, abstractmethod
from flask import current_app


class AbstractProtocol(ABC):
    """
    Abstract base class for network protocol implementations.
    Subclasses must implement the methods for connecting, sending, and receiving data.
    """

    def __init__(self):
        self._logger = current_app.config["logger"]

    def _trace(self, obj: object, message: str):
        """
        Log a TRACE level message.

        :param message:
            the message to log.

        :author:    cadenc@flexxbotics.com
        :since:     ODOULS.IP (7.1.15.2)
        """
        protocol = obj.__class__.__name__
        self._logger.trace(f"Protocol '{protocol}': {message}")

    def _debug(self, obj: object, message: str):
        """
        Log a DEBUG level message.

        :param message:
            the message to log.

        :author:    cadenc@flexxbotics.com
        :since:     ODOULS.IP (7.1.15.2)
        """
        protocol = obj.__class__.__name__
        self._logger.debug(f"Device '{protocol}': {message}")

    def _info(self, obj: object, message: str):
        """
        Log an INFO level message.

        :param message:
            the message to log.

        :author:    cadenc@flexxbotics.com
        :since:     ODOULS.IP (7.1.15.2)
        """
        protocol = obj.__class__.__name__
        self._logger.info(f"Device '{protocol}': {message}")

    def _warn(self, obj: object, message: str):
        """
        Log an WARN level message.

        :param message:
            the message to log.

        :author:    cadenc@flexxbotics.com
        :since:     ODOULS.IP (7.1.15.2)
        """
        protocol = obj.__class__.__name__
        self._logger.warn(f"Protocol '{protocol}': {message}")

    def _error(self, obj: object, message: str):
        """
        Log an ERROR level message.

        :param message:
            the message to log.

        :author:    cadenc@flexxbotics.com
        :since:     ODOULS.IP (7.1.15.2)
        """
        protocol = obj.__class__.__name__
        self._logger.error(f"Protocol '{protocol}': {message}")

    def _critical(self, obj: object, message: str):
        """
        Log an CRITICAL level message.

        :param message:
            the message to log.

        :author:    cadenc@flexxbotics.com
        :since:     ODOULS.IP (7.1.15.2)
        """
        protocol = obj.__class__.__name__
        self._logger.critical(f"Protocol '{protocol}': {message}")

    @abstractmethod
    def connect(self) -> int:
        """
        Establish a connection to a remote server.
        """
        raise NotImplementedError("Protocol subclass must implement connect method")

    @abstractmethod
    def disconnect(self) -> int:
        """
        Disconnect device.
        """
        raise NotImplementedError("Protocol subclass must implement disconnect method")

    @abstractmethod
    def send(self, data: bytes) -> int:
        """
        Send data to the remote server.

        :param data: The data to send as bytes.

        :author:    cadenc@flexxbotics.com
        :since:     ODOULS.IP (7.1.15.2)
        """
        raise NotImplementedError("Protocol subclass must implement send method")

    @abstractmethod
    def receive(self, buffer_size: int) -> str:
        """
        Receive data from the remote server.

        :param buffer_size: The maximum amount of data to receive.
        :return: The received data as str decoded as utf-8.
        """
        raise NotImplementedError("Protocol subclass must implement receive method")
