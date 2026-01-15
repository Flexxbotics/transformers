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

from data_models.device import Device
import json
from transformers.abstract_device import AbstractDevice
from protocols import tcp
import time
import threading

class Global400(AbstractDevice):
    class JobModel:
        def __init__(self):
            self.batch_size = 0
            self.current_job = ""
            self.job_status = 0
            self.steps = []
            self.tool_order = []
            self.current_tool = ""
            self.job_batch_counter = "0"
            self.job_batch_mode = ""
        def reset(self):
            self.__init__()
    def __init__(self, device: Device):
        """
        Template device class. Inherits AbstractDevice class.

        :param Device:
                    the device object

        :return:    a new instance
        """
        super().__init__(device)
        self.meta_data = device.metaData

        self.CONTROLLER_IP = self.meta_data["ip_address"]
        self.CONTROLLER_PORT = self.meta_data["port"]
        self.thread_timeout = 1

        self._client = tcp.TCP(self.CONTROLLER_IP, self.CONTROLLER_PORT, timeout=self.thread_timeout, retry=500)

        self.job_model = self.JobModel()

        self.monitoring_active = False
        self.status_thread = None

        # connect to socket
        self._client.connect()

    def __del__(self):
        pass

    # ############################################################################## #
    # DEVICE COMMUNICATION METHODS
    # ############################################################################## #

    def _execute_command_v2(self, command_name : str, command_args : str) -> str:
        """
        Executes the command sent to the device.

        :param command_name:
                    the command to be executed
        :param command_args:
                    json with the arguments for the command

        :return:    the response after execution of command.

        :author:    tylerjm@flexxbotics.com
        :since:     ODOULS.3 (7.1.15.3)
        """
        # Parse the command from the incoming request
        args = json.loads(command_args)
        response = ""

        self._info(message="Sending command: " + command_name)
        try:
            if command_name == "GET_CURRENT_TOOL":
                response = self.read_current_tool()
            if command_name == "READ_BATCH_COUNTER":
                response = self.read_batch_counter()
            if command_name == "READ_CURRENT_BATCH_SIZE":
                response = self.read_current_batch_size()
        except Exception as e:
            raise Exception(
                "Error when sending command, did not get response from device: "
                + command_name
            )

        finally:
            pass

        if "ERROR" in response:
            raise Exception("Error returned from device... " + command_name)

        return response

    def _read_interval_data(self) -> str:
        """
        Method to read the status of the device on an interval

        :return:    status - string

        :author:    tylerjm@flexxbotics.com
        :author:    sanua@flexxbotics.com

        :since:     ODOULS.3 (7.1.15.3)
        """
        pass

    def _read_status(self, function : str=None) -> str:
        """
        Method to read the status of the device

        :param function:
                    Optional parameter to provide the name of a function to run - string

        :return:    status - string

        :author:    tylerjm@flexxbotics.com
        :since:     ODOULS.3 (7.1.15.3)
        """
        status = ""
        if function is None:
            # Write standard read status statements
            if self.job_model.current_job != "":

                status_raw = self.job_model.job_status
                if status_raw == "0":
                    status = "RUNNING"
                elif status_raw == "1":
                    status = "" \
                             "DONE"
                else:
                    status = "ERROR"
            else:
                return "PENDING_JOB"

        elif function == "": # Some string
            # Write specific function call to read status
            pass
        else:
            pass

        return str(status)

    def _read_variable(self, variable_name : str, function : str=None) -> str:
        """
        Method to read the specified variable from the device

        :param variable_name:
                    The name of the variable to read - string

        :param function:
                    Optional parameter to provide the name of a function to run - string

        :return:    value - string

        :author:    tylerjm@flexxbotics.com
        :since:     ODOULS.3 (7.1.15.3)
        """
        value = ""
        if function is None:
            # Write standard read variable statements
            if variable_name == "current_tool":
                print(self.job_model.tool_order)
                print(self.job_model.job_batch_counter)
                value = self.job_model.current_tool
        elif function == "": # Some string
            # Write specific function call to read variable
            pass
        else:
            pass

        return value

    def _read_file_names(self) -> list:
        """
        Method to get a list of filenames from the device

        :return:    list of filenames

        :author:    tylerjm@flexxbotics.com
        :since:     KEYSTONE.4 (7.1.11.4)
        """

        # Return list of available filenames from the device
        self.programs = []

        return self.programs #TODO is this the actual response object we want?

    def _load_file(self, file_name : str):
        """
        Loads a file into memory on the device

        :param file_name: the name of the file to load into memory
        :return: the file name

        :author:    zacc@flexxbotics.com
        :since:     MODELO.3 (7.1.13.3)
        """
        self.job_model.reset()
        self.job_model.job_status = "0"
        self.job_model.current_job = file_name

        if self.monitoring_active:
            self.monitoring_active = False
            time.sleep(self.thread_timeout + 0.5)

        # initiate open protocol
        self._initiate_coms()

        # abort any running jobs
        job_off_cmd = self._build_open_protocol_message(mid="0130", revision="001", data="")
        self._client.send_without_connect(job_off_cmd)


        # load job
        # print("this is the file name", file_name)
        load_job_command = self._build_open_protocol_message(mid="0038",revision="001",data=file_name)

        response = self._client.send_without_connect(load_job_command)
        self._logger.info("global400 - load job response: " + str(response))




        # get job info and batch count
        get_job_info_cmd = self._build_open_protocol_message(mid="0032", revision="001", data=self.job_model.current_job)
        job_info_response = self._client.send_without_connect(get_job_info_cmd)
        self._parse_mid_0033(job_info_response)

        # subscribe to job info
        subscribe_job_cmd = self._build_open_protocol_message(mid="0034", revision="001", data="00000000000")
        self._client.send_without_connect(subscribe_job_cmd)
        self.status_thread = threading.Thread(target=self._monitor_job_status, daemon=True)
        self.monitoring_active = True
        self.status_thread.start()

        return self.job_model.batch_size

    # ############################################################################## #
    # INTERFACE HELPER METHODS
    #
    # These are private methods with naming convention _some_method(self). These are used to faciliate
    # any specific functions that are needed to communicate via the transformer. For example,
    # connection methods, read/write methods, specific functions, etc.
    # ############################################################################## #

    def _build_open_protocol_message(self, mid: str, revision: str, data: str) -> bytes:
        # Ensure data is an 11-digit zero-padded string
        if mid == "0038":
            data = str(data).zfill(11)

        body = f"{mid}{revision}{data}"
        length = len(body) + 4
        msg = f"{length:04d}{body}".encode('ascii') + b'\x00'
        # print("this is the build message", msg)
        return msg

    def _initiate_coms(self):

        try:
            self._client.connect()
        except Exception as e:
            self._logger.info("Global400 already connected")
            pass

        initial_msg = self._build_open_protocol_message("0001", "001", "000000000")
        response = self._client.send_without_connect(initial_msg)
        return response

    def _close_coms(self):
        close_msg = self._build_open_protocol_message("0003", "001", "000000000")
        response = self._client.send_without_connect(close_msg)
        return response

    def _parse_mid_0033(self, response_bytes):
        msg = response_bytes#.decode()#rstrip(b'\x00')#.decode()
        data = msg[20:]

        # Parse job metadata
        job_id = data[2:4]
        job_name = data[6:27].strip()
        step_data = data[37:]
        self.job_model.steps = step_data.split(';')

        for i, step in enumerate(self.job_model.steps):
            if not step.strip():
                continue
            try:
                parts = step.split(':')

                tool = parts[1]
                batch_count = parts[3]
                current_tool_steps = [tool] * int(batch_count)
                self.job_model.tool_order = self.job_model.tool_order + current_tool_steps
                print(self.job_model.tool_order)
                self.job_model.batch_size = self.job_model.batch_size + int(batch_count)
            except Exception as e:
                print(f"Failed to parse step {i + 1}: {step} ({e})")
            self.job_model.current_tool = self.job_model.tool_order[0]

    def _parse_mid_0035(self, message):
        # Assuming the message is a bytes object
        message = message.decode('ascii').strip()  # Decode and remove any unwanted characters
        self._logger.info("global400 - message: " + str(message))
        # Extract each parameter based on the byte positions defined in the documentation
        # job_id = message[22:24]  # Bytes 23-24: Job ID
        job_status = message[26]  # Bytes 25-26: Job Status
        self._logger.info("global400 - status: " + str(job_status))
        self.job_model.job_status = job_status
        self.job_model.job_batch_mode = message[30]  # Byte 28: Job Batch Mode
        # self.job_model.job_batch_size = message[32:36]  # Bytes 31-34: Job Batch Size (4 ASCII characters)
        self.job_model.job_batch_counter = message[38:42]  # Bytes 37-40: Job Batch Counter (4 ASCII characters)
        self.job_model.timestamp = message[44:63]  # Bytes 43-61: Timestamp (19 characters)

        self.job_model.current_tool = self.job_model.tool_order[int(self.job_model.job_batch_counter)-1]

    def _monitor_job_status(self):
        while self.monitoring_active:
            self._logger.info("global400 - monitoring job status")
            try:
                response = self._client.regular_receive()
                self._logger.info("global4000 - raw response: " + str(response))
                if response:

                    self._parse_mid_0035(response)
                    acknowledge_0035_cmd = self._build_open_protocol_message(mid="0036", revision="001", data="00000000000")
                    self._client.send_without_connect(acknowledge_0035_cmd, receive=False)
                if self.job_model.job_status == "1":
                    unsubscribe_job_cmd = self._build_open_protocol_message(mid="0037", revision="001", data="00000000000")
                    self._client.send_without_connect(data=unsubscribe_job_cmd)
                    self.monitoring_active = False
                time.sleep(0.5)
            except socket.timeout:
                continue

    def read_current_tool(self):
        return str(self.job_model.current_tool)

    def read_current_batch_size(self):
        return str(self.job_model.batch_size)

    def read_batch_counter(self):
        return str(self.job_model.job_batch_counter)
