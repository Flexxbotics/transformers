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
import time
import threading

from data_models.device import Device
from transformers.abstract_device import AbstractDevice


class WorkCell(AbstractDevice):
    """
    The transformer for the workcell

    :author:    tylerjm@flexxbotics.com

    :since:     Q.5 (7.1.17.5)
    """

    # ############################################################################## #
    # INSTANTIATION
    # ############################################################################## #

    def __init__(self, device: Device):
        """
        Workcell class.
        :param Device:
                    the device object

        :return:    a new instance
        """
        super().__init__(device)
        
        # Use self.core_services to access services for device communication
        self.device_service = self.core_services.device_service
        self.alarm_service = self.core_services.alarm_service
        
        # Set the id's of the devices to communicate with
        self.robot_id = ""
        self.cnc_id = ""
        self.cmm_id = ""
        self.io_id = ""
        
        # Initialize any other paramters to be used
        # Example: I/O mapping for stack light control
        self.GREEN_STACK_LIGHT = 9
        self.YELLOW_STACK_LIGHT = 11
        self.RED_STACK_LIGHT = 13
        self.CAROUSEL_RED_STACK_LIGHT = 52
        self.CAROUSEL_YELLOW_STACK_LIGHT = 53
        self.CAROUSEL_GREEN_STACK_LIGHT = 54
        self.CAROUSEL_BUZZER = 55

    def __del__(self):
        pass

    # ############################################################################## #
    # DEVICE COMMUNICATION METHODS
    # ############################################################################## #

    def _read_interval_data(self) -> str:
        """
        Method to read the status of the device on an interval

        :return:    status - string

        :author:    tylerjm@flexxbotics.com

        :since:     Q.5 (7.1.17.5)
        """
        pass

    def _read_status(self, function: str = None) -> str:
        """
        Method to determine and read the status of the workcell

        :param function:
                    Optional parameter to provide the name of a function to run - string

        :return:    status - string

        :author:    tylerjm@flexxbotics.com

        :since:     Q.5 (7.1.17.5)
        """
        # Example
        robot_status = self.device_service.read_status(device_id=self.robot_id)
        cnc_status = self.device_service.read_status(device_id=self.cnc_id)
        cmm_status = self.device_service.read_status(device_id=self.cmm_id)
        
        # Write logic to reconcile status
        status = "IDLE"

        # Clear robot alarms
        non_alarm_statuses = ["RUNNING", "IDLE", "OK", "TEACH_PENDANT_MODE"]
        if status in non_alarm_statuses:
            self.alarm_service.resolve_all_alarms()

        return status

    def _toggle_stack_light(self, status):
        if status == "RUNNING":
            # Stack lights green
            self.device_service.set_digital_output(device_id=self.io_id, start=self.GREEN_STACK_LIGHT, values=[1])
            self.device_service.set_digital_output(device_id=self.io_id, start=self.YELLOW_STACK_LIGHT, values=[0])
            self.device_service.set_digital_output(device_id=self.io_id, start=self.RED_STACK_LIGHT, values=[0])

            self.device_service.set_digital_output(device_id=self.io_id, start=self.CAROUSEL_GREEN_STACK_LIGHT,
                                                    values=[1])
            self.device_service.set_digital_output(device_id=self.io_id, start=self.CAROUSEL_YELLOW_STACK_LIGHT,
                                                    values=[0])
            self.device_service.set_digital_output(device_id=self.io_id, start=self.CAROUSEL_RED_STACK_LIGHT,
                                                    values=[0])
        elif status == "IDLE" or status == "TEACH_PENDANT_MODE":
            # Stack lights yellow
            self.device_service.set_digital_output(device_id=self.io_id, start=self.GREEN_STACK_LIGHT, values=[0])
            self.device_service.set_digital_output(device_id=self.io_id, start=self.YELLOW_STACK_LIGHT, values=[1])
            self.device_service.set_digital_output(device_id=self.io_id, start=self.RED_STACK_LIGHT, values=[0])

            self.device_service.set_digital_output(device_id=self.io_id, start=self.CAROUSEL_GREEN_STACK_LIGHT,
                                                    values=[0])
            self.device_service.set_digital_output(device_id=self.io_id, start=self.CAROUSEL_YELLOW_STACK_LIGHT,
                                                    values=[1])
            self.device_service.set_digital_output(device_id=self.io_id, start=self.CAROUSEL_RED_STACK_LIGHT,
                                                    values=[0])

        else:
            # Stack lights red
            self.device_service.set_digital_output(device_id=self.io_id, start=self.GREEN_STACK_LIGHT, values=[0])
            self.device_service.set_digital_output(device_id=self.io_id, start=self.YELLOW_STACK_LIGHT, values=[0])
            self.device_service.set_digital_output(device_id=self.io_id, start=self.RED_STACK_LIGHT, values=[1])

            self.device_service.set_digital_output(device_id=self.io_id, start=self.CAROUSEL_GREEN_STACK_LIGHT,
                                                    values=[0])
            self.device_service.set_digital_output(device_id=self.io_id, start=self.CAROUSEL_YELLOW_STACK_LIGHT,
                                                    values=[0])
            self.device_service.set_digital_output(device_id=self.io_id, start=self.CAROUSEL_RED_STACK_LIGHT,
                                                    values=[1])