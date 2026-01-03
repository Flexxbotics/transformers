"""
    :copyright: (c) 2022-2024, Flexxbotics, a Delaware corporation (the "COMPANY")
        All rights reserved.

        THIS SOFTWARE IS PROVIDED BY THE COMPANY ''AS IS'' AND ANY
        EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
        WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
        DISCLAIMED. IN NO EVENT SHALL THE COMPANY BE LIABLE FOR ANY
        DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
        (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
        LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
        ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
        (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
        SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
"""
import socket
import json
import requests
import threading
import time
import logging
import os
import sys
import signal
from contextlib import suppress

import inspect
import logging
import os
from logging.handlers import RotatingFileHandler


class ResettingFileHandler(logging.FileHandler):
    def __init__(self, filename, maxBytes=1_000_000, **kwargs):
        super().__init__(filename, mode="a", **kwargs)
        self.maxBytes = maxBytes

    def emit(self, record):
        try:
            if os.path.exists(self.baseFilename) and os.path.getsize(self.baseFilename) > self.maxBytes:
                # Close, clear, and reopen the file
                self.close()
                with open(self.baseFilename, "w"):  # truncate
                    pass
                self.stream = self._open()
            super().emit(record)
        except Exception:
            self.handleError(record)

class Logger:
    """
        This is a logger for the controller.
        TODO with a bit more work it might could become a class to encapsulate all loggers;
         however, for the moment it is controller oriented.

        :author:    johnc@flexxbotics.com
        :since:     KEYSTONE.IP (7.1.11.5)
    """
    __logger = None
    indentation = 0

    CRITICAL = 50
    FATAL = CRITICAL
    ERROR = 40
    WARNING = 30
    WARN = WARNING
    INFO = 20
    DEBUG = 10
    NOTSET = 0
    TRACE = 8

    def __init__(self, level, logfile, name):
        """
            Construct a new logger.

            :param level:
                the log level for the logger.

            :author:    johnc@flexxbotics.com
            :since:     KEYSTONE.IP (7.1.11.5)
        """

        # Configure the handler for the logger.
        # formatter = logging.Formatter('%(asctime)s - %(levelname)-8s - %(message)s', "%Y-%m-%d %H:%M:%S")
        # handler = RotatingFileHandler(logfile, maxBytes=1000000, backupCount=10)
        # handler.setFormatter(formatter)
        #
        # # Configure the common logger.
        # self.__logger = logging.getLogger(name)
        # logging.addLevelName(self.TRACE, "TRACE")
        # self.__logger.setLevel(level)
        # self.__logger.addHandler(handler)

        formatter = logging.Formatter('%(asctime)s - %(levelname)-8s - %(message)s', "%Y-%m-%d %H:%M:%S")
        handler = ResettingFileHandler(logfile, maxBytes=1_000_000, encoding="utf-8")
        handler.setFormatter(formatter)

        self.__logger = logging.getLogger(name)
        logging.addLevelName(self.TRACE, "TRACE")
        self.__logger.setLevel(level)
        self.__logger.addHandler(handler)

    def send_directional_message(self, level, entering, area):
        """
            This method is used to build a message for the logger.

            :param level:
                        the logging level for this logger.

            :param entering:
                        Entering true; otherwise, false

            :param area:
                        The area of interest typically either "controller endpoint" or "service method."

            :return:    the message.

            :author:    johnc@flexxbotics.com
            :since:     KEYSTONE.IP (7.1.11.5)
        """

        # Assume that it is exiting

        if self.__logger.isEnabledFor(level):
            # If it is entering...
            if entering:
                direction_chevron = ">"
            else:
                self.indentation -= 1
                direction_chevron = "<"

            # Get the current
            current_frame = inspect.currentframe()
            direction = "Exiting"
            if entering:
                direction = "Entering"

            # Get the frame of interest
            frame = current_frame.f_back.f_back

            # Inspect the frame to get the method/function name
            frame_inspected = inspect.getouterframes(frame, 1)[0]
            method_name = frame_inspected[3]

            # Inspect the frame for argument values: name and value.
            method_args = inspect.getargvalues(frame)
            method_arg_names = method_args.args

            # Get a dict of local frame values by name
            locals = method_args.locals

            # Build a string of arguments with values
            method_arg_text = ""
            for method_arg_name in method_arg_names:
                if method_arg_name is "self":
                    continue

                method_arg_value = locals[method_arg_name]
                if "" is not method_arg_text:
                    method_arg_text += ","

                method_arg_text += f"{method_arg_name}={method_arg_value}"

            # Build the message
            message = f"{direction_chevron} {direction} {area} {method_name}({method_arg_text})"

            # Log the message
            if level is self.TRACE:
                self.trace(message)
            elif level is self.DEBUG:
                self.debug(message)
            elif level is self.INFO:
                self.info(message)
            elif level is self.WARN:
                self.warn(message)
            elif level is self.ERROR:
                self.error(message)
            elif level is self.CRITICAL:
                self.critical(message)

            if entering:
                self.indentation += 1

    def trace(self, message):
        """
            Log a TRACE leval message.

            :param message:
                the message to log.

            :author:    johnc@flexxbotics.com
            :since:     KEYSTONE.IP (7.1.11.5)
        """
        if self.__logger.isEnabledFor(self.TRACE):
            # Pad the message
            message = " " * (self.indentation * 2) + message

            # Log the message
            #            self.__logger.debug(message)
            self.__logger.log(self.TRACE, message)

            # Print the message to the console
            print(message)

    def debug(self, message):
        """
            Log a DEBUG leval message.

            :param message:
                the message to log.

            :author:    johnc@flexxbotics.com
            :since:     KEYSTONE.IP (7.1.11.5)
        """
        if self.__logger.isEnabledFor(self.DEBUG):
            # Pad the message
            message = " " * (self.indentation * 2) + message

            # Log the message
#            self.__logger.debug(message)
            self.__logger.log(self.DEBUG, message)

            # Print the message to the console
            print(message)

    def info(self, message):
        """
            Log a INFO leval message.

            :param message:
                the message to log.

            :author:    johnc@flexxbotics.com
            :since:     KEYSTONE.IP (7.1.11.5)
        """
        if self.__logger.isEnabledFor(self.INFO):
            # Pad the message
            message = " " * (self.indentation * 2) + message

            # Log the message
            self.__logger.info(message)

            # Print the message to the console
            print(message)

    def warn(self, message):
        """
            Log an WARN leval message.

            :param message:
                the message to log.

            :author:    johnc@flexxbotics.com
            :since:     KEYSTONE.IP (7.1.11.5)
        """
        if self.__logger.isEnabledFor(self.WARN):
            # Pad the message
            message = " " * (self.indentation * 2) + message

            # Log the message
            self.__logger.warning(message)

            # Print the message to the console
            print(message)

    def error(self, message):
        """
            Log an ERROR leval message.

            :param message:
                the message to log.

            :author:    johnc@flexxbotics.com
            :since:     KEYSTONE.IP (7.1.11.5)
        """
        if self.__logger.isEnabledFor(self.ERROR):
            # Pad the message
            message = " " * (self.indentation * 2) + message

            # Log the message
            self.__logger.error(message)

            # Print the message to the console
            print(message)

    def critical(self, message):
        """
            Log a CRITICAL leval message.

            :param message:
                the message to log.

            :author:    johnc@flexxbotics.com
            :since:     KEYSTONE.IP (7.1.11.5)
        """
        if self.__logger.isEnabledFor(self.CRITICAL):
            # Pad the message
            message = " " * (self.indentation * 2) + message

            # Log the message
            self.__logger.critical(message)

            # Print the message to the console
            print(message)

class TCPtoHTTPServer:

    def __init__(self, host, port, flask_port, attributes, logger):
        """
            This is a tcp socket server to handle tcp requests from a robot and route them to the flask server

            :param host:
                        ip address for the tcp server.
            :param port:
                        port for the tcp server.

            :author:    tylerjm@flexxbotics.com
            :since:     LEINENKUGEL.1 (7.1.12.1)
        """
        self.attributes = attributes
        self._logger = logger
        self.host = host
        self.port = port
        self.flask_host = os.getenv("FLASK_CONTAINER", "http://127.0.0.1:" + str(flask_port))
        print("FLASK HOST: " + self.flask_host)

        self.api_base_url = self.flask_host + "/api/v2e"
        self.request_timeout = 60
        self._shutdown = threading.Event()

    def connect(self):
        """
            Accept a socket connection from the robot.

            :author:    tylerjm@flexxbotics.com
            :since:     LEINENKUGEL.1 (7.1.12.1)
        """
        message = "listening for connection..."
        self._logger.debug(message=message)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((self.host, self.port))
        self.sock.listen(5)
        self.client, self.addr = self.sock.accept()
        message = "robot tcp server connected!"
        self._logger.debug(message=message)

    def disconnect(self):
        """
            Disconnect the socket connection from the robot.

            :author:    tylerjm@flexxbotics.com
            :since:     LEINENKUGEL.1 (7.1.12.1)
        """
        self.sock.close()
        message = "robot tcp server disconnected"
        self._logger.debug(message=message)

    def receive_command(self):
        """
            Receive tcp command bytes from the robot.

            :return command:
                        dictionary of the command - {"type": str, "endpoint": str, "body": json}

            :author:    tylerjm@flexxbotics.com
            :since:     LEINENKUGEL.1 (7.1.12.1)
        """
        # Receive first chunk
        data = self.client.recv(1024)

        # Check JSON delimiter
        while data.decode().find("}") < 0:
            packet = self.client.recv(1024)
            if not packet:
                break
            data += packet

        # Parse concated packets
        received = data.decode().replace("'", '"').strip()
        message = "Received: " + received
        self._logger.debug(message)
        command = json.loads(received)

        message = "Received command: " + received
        self._logger.debug(message)

        return command

    def send_response(self, response):
        """
            Sends request to flask server and returns the flask server response

            :param response:
                        response from the flask server as a String

            :author:    tylerjm@flexxbotics.com
            :since:     LEINENKUGEL.1 (7.1.12.1)
        """
        self.client.sendall(response.encode("utf-8"))

    def send_flask_request(self, command):
        """
            Sends request to flask server and returns the flask server response

            :param command:
                        dictionary of the command - {"type": str, "endpoint": str, "body": json}

            :return response:
                        response from the flask server as a String

            :author:    tylerjm@flexxbotics.com
            :since:     LEINENKUGEL.1 (7.1.12.1)
        """

        endpoint = self.api_base_url + command.get("endpoint")
        body = command.get("body")
        request_type = command.get("type")

        # TODO IF AUTHENTICATION IS NEEDED, THIS IS HOW IT IS IMPLEMENTED
        # self.headers = {'Authorization': 'Bearer ' + self.authToken}
        # response = requests.post(endpoint, json=body, headers=self.headers, timeout=self.request_timeout)
        # responseDict = json.loads(response.text)

        if request_type == "POST":
            message="Sending POST: " + endpoint
            self._logger.debug(message)

            headers = {"Content-Type": "application/json"}
            response_raw = requests.post(url=endpoint, json=body, timeout=self.request_timeout, headers=headers)
            message = "Response Code: " + str(response_raw.status_code) + " Response Text: " + str(response_raw.text)
            self._logger.debug(message)

            if response_raw.status_code == 201 or response_raw.status_code == 204:
                message = "Sending response: >OK<"
                self._logger.debug(message)
                if "read_profinet_bit" in endpoint:
                    return '>' + str(int(response_raw.text)) + '<'
                return ">OK<"
            else:
                message = "Sending response: >ERROR<"
                self._logger.debug(message)
                return ">ERROR<"

        elif request_type == "PATCH":
            message = "Sending PATCH: " + endpoint
            self._logger.debug(message)
            headers = {"Content-Type": "application/json"}
            response_raw = requests.patch(url=endpoint, json=body, timeout=self.request_timeout, headers=headers)
            message = "Response Code: " + str(response_raw.status_code) + " Response Text: " + str(response_raw.text)
            self._logger.debug(message)
            if response_raw.status_code == 201 or response_raw.status_code == 200:
                message = "Sending response: >OK<"
                self._logger.debug(message)
                return ">OK<"
            else:
                message = "Sending response: >ERROR<"
                self._logger.debug(message)
                return ">ERROR<"


        elif request_type == "GET":
            message = "Sending GET: " + endpoint
            self._logger.debug(message)
            if "infeed_index" in endpoint or "shelf_index" in endpoint or "part_index" in endpoint:
                body_elements = body.keys()
                for element in body_elements: # should be infeed_index, shelf_index, or part_index
                    element_value = body[element].strip()
                    endpoint = endpoint.replace(element, element_value)

            if "load_file_to_memory" in endpoint:
                program_name = body["program_name"]
                endpoint = endpoint.replace("<string:file_name>", program_name)

            response_raw = requests.get(url=endpoint, params=body, timeout=self.request_timeout)
            message = "Response Code: " + str(response_raw.status_code) + " Response Text: " + str(response_raw.text)
            self._logger.debug(message)

            if response_raw.status_code == 200:
                if endpoint == self.flask_host + "/api/v2e/devices":
                    message = "Sending response: " + '>' + '{"response": ' + response_raw.text + "}<"
                    self._logger.debug(message)
                    return '>' + '{"response": ' + response_raw.text + "}<"
                elif "get_device_enpoints" in endpoint:
                    message = "Sending response: >" + '{"response": ' + response_raw.text + "}<"
                    self._logger.debug(message)
                    return '>' + '{"response": ' + response_raw.text + "}<"
                else:
                    message = "Sending response: >"+response_raw.text+"<"
                    self._logger.debug(message)
                    return '>' + response_raw.text.strip().strip('"') + '<'
            else:
                message = "Sending response: >ERROR<"
                self._logger.debug(message)
                return ">ERROR<"

        elif request_type == "DEL":
            message = "Sending DELETE: " + endpoint
            self._logger.debug(message)
            if "infeed_index" in endpoint or "shelf_index" in endpoint or "part_index" in endpoint:
                body_elements = body.keys()
                for element in body_elements:  # should be infeed_index, shelf_index, or part_index
                    element_value = body[element].strip()
                    endpoint = endpoint.replace(element, element_value)

            response_raw = requests.delete(url=endpoint, params=body, timeout=self.request_timeout)
            message = "Response Code: " + str(response_raw.status_code) + " Response Text: " + str(response_raw.text)
            self._logger.debug(message)

            if response_raw.status_code == 204:
                message = "Sending response: >OK<"
                self._logger.debug(message)
                return ">OK<"
            else:
                message = "Sending response: >ERROR<"
                self._logger.debug(message)
                return ">ERROR<"


    def start_command_loop(self):
        """
            Starts a thread to run the robot tcp server command loop

            :author:    tylerjm@flexxbotics.com
            :since:     LEINENKUGEL.1 (7.1.12.1)
        """
        if hasattr(self, "command_loop_thread") and self.command_loop_thread.is_alive():
            self._logger.debug(message="Command loop already running. Skipping duplicate start.")
            return
        self.command_loop()

    def stop_command_loop(self):
        """
            Stops the command loop from running

            :author:    tylerjm@flexxbotics.com
            :since:     LEINENKUGEL.1 (7.1.12.1)
        """
        self.command_loop_running = True

    def command_loop(self):
        """
            Starts a while loop that processes and responds to a command

            :author:    tylerjm@flexxbotics.com
            :since:     MODELO.IP (7.1.13.5)
        """
        # Wait for initial connection
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.sock.bind((self.host, self.port))
            self.sock.listen(5)
            self.sock.settimeout(1.0)  # check shutdown every second

            while not self._shutdown.is_set():
                try:
                    # self._logger.debug("TCP server started and listening...")
                    self.client, self.addr = self.sock.accept()
                    with self.client:
                        self._logger.info("robot tcp server connected!")
                        connected = True
                        # Command loop
                        while connected:
                            try:
                                message = "waiting for command"
                                self._logger.debug(message=message)

                                command = self.receive_command()
                                response = self.send_flask_request(command=command)
                                self._logger.debug(message=response)
                                self.send_response(response=response)
                            except Exception as e:
                                message = str(e)
                                self._logger.error(message=message)

                                message = "failed to receive command"
                                self._logger.error(message=message)

                                self.send_response(response=">ERROR<")
                                # time.sleep(1)
                                connected=False

                except socket.timeout:
                    continue
                except Exception as e:
                    self._logger.error(f"Error in command loop: {e}")
                    time.sleep(1)

        finally:
            self._logger.info("Shutting down server...")
            with suppress(Exception):
                self.sock.close()

    def shutdown(self):
        self._logger.info("Shutdown signal received")
        self._shutdown.set()

if __name__ == "__main__":
    current_path = os.path.abspath(os.path.dirname(__file__))
    name = "robot_tcp_http_adapter_standalone"
    logfile = f"{current_path}/{name}.log"
    robot_adapter_logger = Logger(logging.DEBUG, logfile, name)
    attributes = {"name": "Universal Robot", "_id": "10000000001"}
    robot_tcp_server = TCPtoHTTPServer(host="0.0.0.0", port=7082, flask_port=7081, attributes=attributes,
                                       logger=robot_adapter_logger)

    def handle_sigterm(signum, frame):
        robot_adapter_logger.info("SIGTERM received, shutting down...")
        robot_tcp_server.shutdown()

    signal.signal(signal.SIGTERM, handle_sigterm)
    signal.signal(signal.SIGINT, handle_sigterm)  # Also allow Ctrl+C to shutdown

    robot_tcp_server.start_command_loop()
    print ("loop ended")


