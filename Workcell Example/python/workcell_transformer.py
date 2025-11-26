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

# 3rd party python library imports
import time
import threading

from data_models.device import Device
from transformers.abstract_device import AbstractDevice


class WorkcellTransformer(AbstractDevice):
    """
    This is an example of a workcell transformer which utilizes multiple devices to reconcile things like
    the workcell status, trigger restarts, trigger controls or perform other advanced tasks.
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

        self.wago_id = ""
        self.sick_plc_id = ""
        self.yaskawa_carousel_id = ""
        self._last_sick_plc_status = ""
        devices = self._device_service.get_devices()

        self.GREEN_STACK_LIGHT = 9
        self.YELLOW_STACK_LIGHT = 11
        self.RED_STACK_LIGHT = 13
        self.CAROUSEL_RED_STACK_LIGHT = 52
        self.CAROUSEL_YELLOW_STACK_LIGHT = 53
        self.CAROUSEL_GREEN_STACK_LIGHT = 54
        self.CAROUSEL_BUZZER = 55

        for device in devices:
            if device.transformer == "fanuc":
                self.robot_id = str(device.id)
            if device.transformer == "Wago":
                self.wago_id = str(device.id)
            if device.transformer == "FlexiCompact":
                self.sick_plc_id = str(device.id)
            if device.transformer == "YaskawaMP2600":
                self.yaskawa_carousel_id = str(device.id)

    def __del__(self):
        pass

    # ############################################################################## #
    # DEVICE COMMUNICATION METHODS
    # ############################################################################## #

    def _read_interval_data(self) -> str:
        """
        Method to read the status of the device on an interval

        :return:    status - string
        """
        pass

    def _read_status(self, function: str = None) -> str:
        """
        Method to determine and read the status of the workcell

        :param function:
                    Optional parameter to provide the name of a function to run - string

        :return:    status - string

        """
        # Robot status
        robot_status = self._device_service.read_status(device_id=self.robot_id)

        # Clear robot alarms
        non_alarm_statuses = ["RUNNING", "IDLE", "OK", "TEACH_PENDANT_MODE"]
        if robot_status in non_alarm_statuses:
            self._alarm_service.resolve_alarms_by_device_id(device_id=self.robot_id)

        try:
            # Check wash cycle
            wash_cycles = self._variable_service.get_variable_latest_value(device_id=self.robot_id, name="ultrasonic_cycles",
                                                                            machine_variable_name="ultrasonic_cycles",
                                                                            variable_id="")
            wash_cycles_count = int(wash_cycles)
            if wash_cycles_count % 20 == 0 and wash_cycles_count != 0:
                self._start_drain_then_fill()
        except Exception as e:
            self._error(str(e))

        if robot_status == "ROBOT_EMERGENCY_STOP":
            status = robot_status
            self._toggle_stack_light(status=status)
            return status

        try:
            # Sick PLC status
            self._sick_plc_status = self._device_service.read_status(device_id=self.sick_plc_id)

            # Clear plc alarms
            if self._sick_plc_status in non_alarm_statuses:
                self._alarm_service.resolve_alarms_by_device_id(device_id=self.sick_plc_id)

        except Exception as e:
            status = "SAFETY_PLC_READ_FAULT"
            self._error(str(e))
            self._toggle_stack_light(status)
            return status

        if self._sick_plc_status != "OK":
            status = self._sick_plc_status
        else:
            status = robot_status

        self._info(message="Safety PLC: " + self._sick_plc_status)
        self._info(message="Last Safety PLC: " + self._last_sick_plc_status)

        if self._sick_plc_status == "OK" and self._last_sick_plc_status == "OPERATOR_DOOR_OPEN":
            # perform auto restart
            self._info(message="Detected auto restart robot for operator station door")
            time.sleep(1)
            self._device_service.execute_command_v2(device_id=self.robot_id, command_name="RESTART_ROBOT", command_args="{}")

        # Changed this to dict with corresponding I/O for the CNC zones. Refer to I/O document
        door_restart_states = {"CNC_1_CABIN_DOOR_OPEN": 72, "CNC_2_CABIN_DOOR_OPEN": 73, "CNC_3_CABIN_DOOR_OPEN": 74,
                                "CNC_4_CABIN_DOOR_OPEN": 75, "CNC_5_CABIN_DOOR_OPEN": 76, "CNC_6_CABIN_DOOR_OPEN": 77}
        
        if self._sick_plc_status == "OK" and self._last_sick_plc_status in list(door_restart_states.keys()):
            # query cnc zone
            input_number = door_restart_states[self._last_sick_plc_status]
            input_reponse = self._device_service.execute_command_v2(device_id=self.robot_id, command_name="READ_DIGITAL_INPUT", command_args={"input_number": input_number}, receive_json=True)
            input_result = input_reponse["result"]
            # perform auto restart
            if robot_status == "FAULT" and input_result:
                self._info(message="Detected auto restart robot for stopped in cnc")
                time.sleep(1)
                self._device_service.execute_command_v2(device_id=self.robot_id, command_name="RESTART_ROBOT", command_args="{}")

        self._last_sick_plc_status = self._sick_plc_status

        # Carousel status
        try:
            self._carousel_status = self._device_service.read_status(device_id=self.yaskawa_carousel_id)
            if self._carousel_status == "FAULT":
                status = "CAROUSEL_FAULT"
        except Exception as e:
            status = "CAROUSEL_READ_FAULT"
            self._error(str(e))
            self._toggle_stack_light(status)
            return status

        try:
            self._toggle_stack_light(status)
        except Exception as e:
            status = "I/O_READ_FAULT"
            self._error(str(e))
            self._toggle_stack_light(status)
            return status

        if status in non_alarm_statuses:
            self._alarm_service.resolve_all_alarms()

        return status

    def _toggle_stack_light(self, status):
        if status == "RUNNING":
            # Stack lights green
            self._device_service.set_digital_output(device_id=self.wago_id, start=self.GREEN_STACK_LIGHT, values=[1])
            self._device_service.set_digital_output(device_id=self.wago_id, start=self.YELLOW_STACK_LIGHT, values=[0])
            self._device_service.set_digital_output(device_id=self.wago_id, start=self.RED_STACK_LIGHT, values=[0])

            self._device_service.set_digital_output(device_id=self.wago_id, start=self.CAROUSEL_GREEN_STACK_LIGHT,
                                                    values=[1])
            self._device_service.set_digital_output(device_id=self.wago_id, start=self.CAROUSEL_YELLOW_STACK_LIGHT,
                                                    values=[0])
            self._device_service.set_digital_output(device_id=self.wago_id, start=self.CAROUSEL_RED_STACK_LIGHT,
                                                    values=[0])
        elif status == "IDLE" or status == "TEACH_PENDANT_MODE":
            # Stack lights yellow
            self._device_service.set_digital_output(device_id=self.wago_id, start=self.GREEN_STACK_LIGHT, values=[0])
            self._device_service.set_digital_output(device_id=self.wago_id, start=self.YELLOW_STACK_LIGHT, values=[1])
            self._device_service.set_digital_output(device_id=self.wago_id, start=self.RED_STACK_LIGHT, values=[0])

            self._device_service.set_digital_output(device_id=self.wago_id, start=self.CAROUSEL_GREEN_STACK_LIGHT,
                                                    values=[0])
            self._device_service.set_digital_output(device_id=self.wago_id, start=self.CAROUSEL_YELLOW_STACK_LIGHT,
                                                    values=[1])
            self._device_service.set_digital_output(device_id=self.wago_id, start=self.CAROUSEL_RED_STACK_LIGHT,
                                                    values=[0])

        else:
            # Stack lights red
            self._device_service.set_digital_output(device_id=self.wago_id, start=self.GREEN_STACK_LIGHT, values=[0])
            self._device_service.set_digital_output(device_id=self.wago_id, start=self.YELLOW_STACK_LIGHT, values=[0])
            self._device_service.set_digital_output(device_id=self.wago_id, start=self.RED_STACK_LIGHT, values=[1])

            self._device_service.set_digital_output(device_id=self.wago_id, start=self.CAROUSEL_GREEN_STACK_LIGHT,
                                                    values=[0])
            self._device_service.set_digital_output(device_id=self.wago_id, start=self.CAROUSEL_YELLOW_STACK_LIGHT,
                                                    values=[0])
            self._device_service.set_digital_output(device_id=self.wago_id, start=self.CAROUSEL_RED_STACK_LIGHT,
                                                    values=[1])


    def _start_drain_then_fill(self):
        self.drain_fill_running = True
        self.drain_fill_thread = threading.Thread(target=self._drain_then_fill, daemon=True)
        self._logger.info(message="drain then fill started...")
        self.drain_fill_thread.start()

    def _drain_then_fill(self):
        # Drain
        self._device_service.set_digital_output(device_id=self.wago_id, start=57, values=[1])
        time.sleep(180)
        self._device_service.set_digital_output(device_id=self.wago_id, start=57, values=[0])

        # Fill
        self._device_service.set_digital_output(device_id=self.wago_id, start=58, values=[1])
        time.sleep(180)
        self._device_service.set_digital_output(device_id=self.wago_id, start=58, values=[0])