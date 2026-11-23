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
from werkzeug.exceptions import HTTPException
from exceptions.flexxCoreExceptions import (
    FlexxCoreException,
    BadRequestException,
    ServerErrorException,
    ServiceUnavailableException,
)

class SturtevantGlobal400(AbstractDevice):
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
        self.meta_data = device.metaData or {}

        self.CONTROLLER_IP = self.meta_data.get("ip_address", "")
        self.CONTROLLER_PORT = str(self.meta_data.get("port", ""))
        if not self.CONTROLLER_IP:
            raise ValueError("SturtevantGlobal400: metaData must contain 'ip_address'")
        if not self.CONTROLLER_PORT:
            raise ValueError("SturtevantGlobal400: metaData must contain 'port'")
        self.thread_timeout = 1

        # The socket is opened by _connect() (on load_file or the connect command), not here
        self._client = None
        self._connected = False

        self.job_model = self.JobModel()

        self.monitoring_active = False
        self.status_thread = None
        # Set when the job load or the status monitor fails, so status reports ERROR instead of a stale RUNNING
        self._job_error = None

    def __del__(self):
        # Stop the monitor thread first, then close the socket (which also unblocks its receive)
        try:
            self.monitoring_active = False
            self._disconnect()
        except Exception:
            pass
        try:
            self._stop_monitor(timeout=self.thread_timeout + 0.5)
        except Exception:
            pass

    # ############################################################################## #
    # DEVICE COMMUNICATION METHODS
    # ############################################################################## #

    def _execute_command_v2(self, command_name : str, command_args : str) -> str:
        """
        Executes the command sent to the device.

        Supported commands
        ------------------
        connect / disconnect
            Returns: {"status": "ok", "connected": true|false}
        GET_CURRENT_TOOL / READ_BATCH_COUNTER / READ_CURRENT_BATCH_SIZE
            Values tracked from the loaded job. Returns: {"status": "ok", "value": "<value>"}

        An unknown command raises BadRequestException (400).

        :param command_name:
                    the command to be executed
        :param command_args:
                    json with the arguments for the command

        :return:    the response after execution of command.

        :author:    tylerjm@flexxbotics.com
        :since:     ODOULS.3 (7.1.15.3)
        """
        # Parse the command from the incoming request
        args = self._parse_args(command_args)

        self._info(message="Sending command: " + command_name)
        try:
            if command_name == "connect":
                self._connect()
                return json.dumps({"status": "ok", "connected": True})
            if command_name == "disconnect":
                self._disconnect()
                return json.dumps({"status": "ok", "connected": False})
            if command_name == "GET_CURRENT_TOOL":
                return json.dumps({"status": "ok", "value": self.read_current_tool()})
            if command_name == "READ_BATCH_COUNTER":
                return json.dumps({"status": "ok", "value": self.read_batch_counter()})
            if command_name == "READ_CURRENT_BATCH_SIZE":
                return json.dumps({"status": "ok", "value": self.read_current_batch_size()})
        except Exception as e:
            self._raise_device_error(f"Command '{command_name}'", e)

        raise BadRequestException(f"Unknown command '{command_name}'")

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
                    Ignored

        :return:    status - "PENDING_JOB", "RUNNING", "DONE", or "ERROR" (job error, or the
                    job load / status monitor failed)

        :author:    tylerjm@flexxbotics.com
        :since:     ODOULS.3 (7.1.15.3)
        """
        status = ""
        try:
            # Write standard read status statements
            if self.job_model.current_job != "":

                status_raw = self.job_model.job_status
                if status_raw == "0":
                    # A failed load or a dead monitor would otherwise leave a stale RUNNING
                    status = "ERROR" if self._job_error else "RUNNING"
                elif status_raw == "1":
                    status = "" \
                             "DONE"
                else:
                    status = "ERROR"
            else:
                return "PENDING_JOB"
        except Exception as e:
            self._error(message=f"Status read failed: {e}")
            self._connected = False
            return "ERROR"

        return str(status)

    def _read_variable(self, variable_name : str, function : str=None) -> str:
        """
        Method to read the specified variable from the device

        :param variable_name:
                    "current_tool" (the only variable tracked) - string

        :param function:
                    Ignored

        :return:    value - string. Raises BadRequestException (400) for any other name.

        :author:    tylerjm@flexxbotics.com
        :since:     ODOULS.3 (7.1.15.3)
        """
        if variable_name == "current_tool":
            return str(self.job_model.current_tool)
        raise BadRequestException(f"Unknown variable '{variable_name}'")

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
        :return: the job's batch size - string

        :author:    zacc@flexxbotics.com
        :since:     MODELO.3 (7.1.13.3)
        """
        self.job_model.reset()
        self.job_model.job_status = "0"
        self.job_model.current_job = file_name
        self._job_error = None

        # stop the previous job's status monitor
        self._stop_monitor(timeout=self.thread_timeout + 1.0)

        try:
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
        except Exception as e:
            self._job_error = f"Load job '{file_name}' failed: {e}"
            self._raise_device_error(f"Load file '{file_name}'", e)

        return str(self.job_model.batch_size)

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

        # Every job load starts a fresh Open Protocol session (MID 0001) on a new connection, as before
        self._disconnect()
        self._ensure_connected()

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
                self.job_model.batch_size = self.job_model.batch_size + int(batch_count)
            except Exception as e:
                self._error(message=f"Failed to parse step {i + 1}: {step} ({e})")
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
        me = threading.current_thread()
        while self.monitoring_active and self.status_thread is me:
            self._logger.info("global400 - monitoring job status")
            try:
                response = self._client.regular_receive()
                self._logger.info("global4000 - raw response: " + str(response))
                if response:

                    self._parse_mid_0035(response)
                    acknowledge_0035_cmd = self._build_open_protocol_message(mid="0036", revision="001", data="00000000000")
                    self._client.send_without_connect(acknowledge_0035_cmd, receive=False)
                else:
                    # recv() returns nothing only when the controller has closed the connection
                    raise ConnectionError("controller closed the connection")
                if self.job_model.job_status == "1":
                    unsubscribe_job_cmd = self._build_open_protocol_message(mid="0037", revision="001", data="00000000000")
                    self._client.send_without_connect(data=unsubscribe_job_cmd)
                    self.monitoring_active = False
                time.sleep(0.5)
            except socket.timeout:
                continue
            except Exception as e:
                if not self.monitoring_active or self.status_thread is not me:
                    break  # stopped on purpose (teardown or a new job load)
                self._error(message=f"global400 - job status monitor stopped: {e}")
                self._job_error = f"Job status monitor failed: {e}"
                self._connected = False
                self.monitoring_active = False

    def _stop_monitor(self, timeout: float) -> None:
        """Stop the job status monitor thread and wait (bounded) for it to exit."""
        self.monitoring_active = False
        thread = self.status_thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout)

    def read_current_tool(self):
        return str(self.job_model.current_tool)

    def read_current_batch_size(self):
        return str(self.job_model.batch_size)

    def read_batch_counter(self):
        return str(self.job_model.job_batch_counter)

    def _connect(self) -> None:
        if self._connected and self._client is not None:
            return
        self._client = tcp.TCP(self.CONTROLLER_IP, self.CONTROLLER_PORT, timeout=self.thread_timeout, retry=2)
        try:
            rc = self._client.connect()
        except Exception as e:  # protocols.tcp raises instead of returning a code on some socket errors
            rc = repr(e)
        if rc != 0:
            self._connected = False
            raise ServiceUnavailableException(
                f"SturtevantGlobal400: connect failed ({self.CONTROLLER_IP}:{self.CONTROLLER_PORT}, {rc})"
            )
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
