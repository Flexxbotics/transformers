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

# Adjust sys.path to import abstract_script if needed
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import ttkbootstrap as ttk
from ttkbootstrap.constants import *
import tkinter.font as tkfont
from tkinter import PhotoImage
import time
import requests


# ------------------------
# Core Communications
# ------------------------
class FlexxCoreClient:

    def __init__(self, flask_port):
        self.flask_host = os.getenv("FLASK_CONTAINER", "http://127.0.0.1:" + str(flask_port))
        print("FLASK HOST: " + self.flask_host)

        self.api_base_url = self.flask_host + "/api/v2e"
        self.request_timeout = 60

    def send_get_request(self, endpoint, params):
        endpoint = self.api_base_url + endpoint
        print (endpoint)
        response_raw = requests.get(url=endpoint, params=params, timeout=self.request_timeout)

        return response_raw

    def send_post_request(self, endpoint, body):
        endpoint = self.api_base_url + endpoint
        print (endpoint)
        headers = {"Content-Type": "application/json"}
        response_raw = requests.post(url=endpoint, json=body, timeout=self.request_timeout, headers=headers)

        return response_raw

    def send_patch_request(self, endpoint, body):
        endpoint = self.api_base_url + endpoint
        print (endpoint)
        headers = {"Content-Type": "application/json"}
        response_raw = requests.patch(url=endpoint, json=body, timeout=self.request_timeout, headers=headers)

        return response_raw

    def execute_command(self, device_id, command_name, args):
        endpoint = "/devices/"+device_id+"/execute_command/"+command_name
        body = args
        res = self.send_post_request(endpoint=endpoint, body=body)

        return res

    def set_variable_latest_value(self, device_id, variable_name, value):
        endpoint = "/variables/latestValue/devices/"+device_id
        body = {"variable_name" : variable_name, "latest_value": value}
        res = self.send_patch_request(endpoint=endpoint, body=body)

        return res

    def get_variable_latest_value(self, device_id, variable_name):
        endpoint = "/variables/latestValue/devices/"+device_id
        params = {"name" : variable_name}
        res = self.send_get_request(endpoint=endpoint, params=params)

        return res

    def get_selected_part_index(self):
        endpoint = "/infeeds/selectedPart"
        params = {"partIndex": "True"}
        res = self.send_get_request(endpoint=endpoint, params=params)

        return res

    def get_selected_shelf_index(self):
        endpoint = "/infeeds/selectedPart"
        params = {"shelfIndex": "True"}
        res = self.send_get_request(endpoint=endpoint, params=params)

        return res

    def get_selected_part_property(self, property):
        endpoint = "/infeeds/selectedPart"
        params = {property: ""}
        res = self.send_get_request(endpoint=endpoint, params=params)

        return res

    def get_part_property(self, infeed_idx, shelf_idx, part_idx, property):
        endpoint = "/parts/infeeds/"+str(infeed_idx)+"/shelf/"+str(shelf_idx)+"/parts/"+str(part_idx)+"/properties/"+property
        params = {}
        res = self.send_get_request(endpoint=endpoint, params=params)

        return res

    def get_part_index_exists(self, infeed_idx, shelf_idx, part_idx):
        endpoint = "/parts/infeeds/"+str(infeed_idx)+"/shelf/"+str(shelf_idx)+"/parts/"+str(part_idx)
        params = {}
        res = self.send_get_request(endpoint=endpoint, params=params)

        return res

    def load_program(self, device_id, program_name):
        endpoint = "/devices/"+device_id+"/files/load_file_to_memory/"+program_name
        params = {}
        res = self.send_get_request(endpoint=endpoint, params=params)

        return res

    def read_status(self, device_id):
        endpoint = "/devices/"+device_id+"/status"
        params = {}
        res = self.send_get_request(endpoint=endpoint, params=params)

        return res

    def read_input(self, device_id, input_number):
        endpoint = "/devices/"+device_id+"/io/di/"+input_number
        params = {}
        res = self.send_get_request(endpoint=endpoint, params=params)

        return res

    def set_output(self, device_id, output_number, state):
        endpoint = "/devices/" + device_id + "/io/do/" + output_number
        body = {"values": state}
        res = self.send_post_request(endpoint=endpoint, body=body)

        return res

    def get_workcell_status(self):
        endpoint = "/workCell/status"
        params = {}
        res = self.send_get_request(endpoint=endpoint, params=params)

        return res

# -----------------------
# FlexxGUI Implementation
# -----------------------
class FlexxGUI:
    _root_instance = None  # Singleton root

    def __init__(self):
        if FlexxGUI._root_instance is None:
            self.root = ttk.Window(themename="darkly")
            FlexxGUI._root_instance = self.root
            self._configure_root()
        else:
            self.root = FlexxGUI._root_instance
            self.clear_content()

        self._setup_frames()
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.after(1000, lambda: self.root.attributes("-topmost", False))

    def _configure_root(self):
        self.root.geometry("1200x800")
        self.root.title("Flexx GUI")
        self.root.configure(bg="#132231")

        # Make fullscreen
        self.root.attributes("-fullscreen", True)

        default_font = tkfont.nametofont("TkDefaultFont")
        default_font.configure(family="Roboto", size=10)
        self.root.option_add("*Font", "Roboto 10")

        style = ttk.Style()
        style.configure("TFrame", background="#132231")
        style.configure("TLabel", background="#132231", foreground="white")

    def _setup_frames(self):
        for child in self.root.winfo_children():
            child.destroy()

        self.border_frame = ttk.Frame(self.root, style="TFrame", bootstyle=SECONDARY)
        self.border_frame.pack(fill="both", expand=True, padx=10, pady=10)

        self.inner_frame = ttk.Frame(self.border_frame, style="TFrame")
        self.inner_frame.pack(fill="both", expand=True, padx=5, pady=5)

    def clear_content(self):
        if hasattr(self, "inner_frame"):
            for widget in self.inner_frame.winfo_children():
                widget.destroy()

    def create_centered_container(self):
        container = ttk.Frame(self.inner_frame, style="TFrame")
        container.pack(expand=True)
        return container

    def create_label(self, text, parent=None):
        if parent is None:
            parent = self.inner_frame
        label = ttk.Label(
            parent,
            text=text,
            font=("Roboto", 24),
            anchor="center",
            justify="center",
            background="#132231",
            foreground="white"
        )
        label.pack(pady=(0, 20))
        return label

    def create_button(self, text, color, command=None, parent=None):
        if parent is None:
            parent = self.inner_frame
        style_name = f"{text.replace(' ', '')}.TButton"
        ttk.Style().configure(
            style_name,
            background=color,
            foreground="black",
            font=("Roboto", 12),
            padding=(15, 25),
            relief="flat"
        )
        btn = ttk.Button(parent, text=text.upper(), style=style_name, command=command)
        btn.configure(width=22)
        btn.pack(pady=5)
        return btn
    def flash_background(self, color="#00FF00", duration=300):
        """Flash the inner_frame background to a color, then reset."""
        original_color = "#132231"  # your normal background

        # Change immediately
        self.inner_frame.configure(style="Flash.TFrame")
        style = ttk.Style()
        style.configure("Flash.TFrame", background=color)

        # Reset after duration
        self.root.after(duration, lambda: style.configure("Flash.TFrame", background=original_color))

    def start(self):
        self.root.deiconify()
        self.root.mainloop()

    def close(self):
        self.root.destroy()

# -----------------------
# FlexxWorkflowApp Script
# -----------------------
class FlexxTorqueWorkflowApp:
    def __init__(self):
        self.gui = FlexxGUI()
        self.client = FlexxCoreClient(flask_port=7081)
        self.progress_bar = None

        self.robot_id = "688c5fb834dd9e275c2674a7"
        self.torque_controller_id = "68a1f8958ee549d814213745"
        self.wago_id = "68921f6094bc3d988cb889c1"
        self.selected_part_idx = -1
        self.selected_shelf_idx = -1

    def main_entry_menu(self):

        self.selected_part_idx = self.client.get_selected_part_index().text
        self.selected_shelf_idx = self.client.get_selected_shelf_index().text
        self.client.set_variable_latest_value(device_id=self.robot_id, variable_name="replace_part", value=False)

        print ("Selected Part: " + str(self.selected_part_idx))
        print ("Selected Shelf: " + str(self.selected_shelf_idx))

        self.container = self.gui.create_centered_container()
        self.status_label = self.gui.create_label("Select Workflow", parent=self.container)

        part_exists = self.client.get_part_index_exists(infeed_idx="0", shelf_idx=self.selected_shelf_idx, part_idx=self.selected_part_idx).text.strip().lower()
        print(part_exists)
        print(type(part_exists))
        if part_exists == "true":
            self.container.after(200, self.drop_off_sequence)
        else:
            self.torque_workflow_btn = self.gui.create_button("Stage Part w/ Torque", "#25BC9F", command=self.torque_workflow, parent=self.container)
            self.pickup_btn = self.gui.create_button("Stage Empty Workholding", "#25BC9F", command=self.torque_complete,
                                                    parent=self.container)
            self.dropoff_btn = self.gui.create_button("Force Robot Drop Off", "#25BC9F", command=self.force_drop_off_sequence, parent=self.container)
            self.abort_btn = self.gui.create_button("Abort", "#FF0000", command=self.abort,
                                                    parent=self.container)

        self.gui.start()

    def force_drop_off_sequence(self):
        self.client.set_variable_latest_value(device_id=self.robot_id, variable_name="force_dropoff",
                                              value=True).text
        self.drop_off_sequence()

    def drop_off_sequence(self):
        self.gui.clear_content()
        self.container = self.gui.create_centered_container()
        self.status_label = self.gui.create_label("Close door to operator...", parent=self.container)
        self.abort_btn = self.gui.create_button("Abort", "#FF0000", command=self.abort,
                                                parent=self.container)
        print("Waiting for door state...")
        self.container.after(500, self.wait_door_facing_robot)

    def wait_door_facing_robot(self):
        door_state = self.client.read_input(device_id=self.wago_id, input_number="43").text.strip()
        print("Door state: " + door_state)
        if door_state == "1":
            # Check again in 1000 ms (1 second)
            print("in loop")
            self.container.after(1000, self.wait_door_facing_robot)
        else:
            # TODO execute command to restart robot
            print("got door state")
            self.show_waiting_for_robot_dropoff()
            self.client.execute_command(device_id=self.robot_id, command_name="RESTART_ROBOT", args={})

    def show_waiting_for_robot_dropoff(self):
        # time.sleep(1)
        self.gui.clear_content()
        self.container = self.gui.create_centered_container()

        #self.workcell_status_label = "Workcell Status: "
        self.status_label = self.gui.create_label("Waiting for robot to drop off part...", parent=self.container)

        self.progress_bar = ttk.Progressbar(self.container, mode="indeterminate", bootstyle="info-strip")
        self.progress_bar.pack(pady=(0, 20))
        self.progress_bar.start(10)
        self.resume_btn = self.gui.create_button("Resume", "#25BC9F", command=self.resume,
                                                parent=self.container)
        self.abort_btn = self.gui.create_button("Abort", "#FF0000", command=self.abort,
                                                parent=self.container)


        self.container.after(200, self.set_robot_dropoff)

    def set_robot_dropoff(self):
        print ("Setting robot drop off")
        self.client.set_variable_latest_value(device_id=self.robot_id, variable_name="robot_dropoff_op",
                                              value=True).text
        #print("Resuming robot program")
        # self.client.execute_command(device_id=self.robot_id, command_name="RESTART_ROBOT", args={})
        self.check_robot_dropoff()

    def check_robot_dropoff(self):
        value = self.client.get_variable_latest_value(
            device_id=self.robot_id, variable_name="robot_dropoff_op"
        ).text.strip()
        print("Robot drop off operator station: " + value)
        #workcell_status = self.client.get_workcell_status()
        #self.workcell_status_label.config(text=f"Workcell Status: {workcell_status}")
        if value == "true":
            # Check again in 1000 ms (1 second)
            self.container.after(1000, self.check_robot_dropoff)
        else:
            self.show_robot_retrieved_part()


    def show_robot_retrieved_part(self):
        self._stop_and_remove_progress_bar()
        self.gui.clear_content()
        self.container = self.gui.create_centered_container()

        self.status_label = self.gui.create_label("Robot completed drop off. Request to enter then open door to present part.", parent=self.container)
        self.waiting_label = self.gui.create_label("Waiting for door to be presented to operator...", parent=self.container)

        self.progress_bar = ttk.Progressbar(self.container, mode="indeterminate", bootstyle="info-strip")
        self.progress_bar.pack(pady=(0, 20))
        self.progress_bar.start(10)

        self.abort_btn = self.gui.create_button("Abort", "#FF0000", command=self.abort,
                                                parent=self.container)

        self.check_door_facing_operator()

        # self.gui.root.after(10_000, self.ready_for_part_interaction)

    def check_door_facing_operator(self):
        door_state = self.client.read_input(device_id=self.wago_id, input_number="42").text.strip()
        print("Door state: " + door_state)
        if door_state == "1":
            # Check again in 1000 ms (1 second)
            self.container.after(1000, self.check_door_facing_operator)
        else:
            # TODO execute command to restart robot
            self.ready_for_part_interaction()
            self.client.execute_command(device_id=self.robot_id, command_name="RESTART_ROBOT", args={})

    def ready_for_part_interaction(self):
        self._stop_and_remove_progress_bar()
        self.gui.clear_content()
        self.container = self.gui.create_centered_container()

        self.status_label = self.gui.create_label(
            "Door is facing operator. Select workflow option.", parent=self.container)

        self.replace_part_btn = self.gui.create_button("Stage Part w/ Torque", "#25BC9F", command=self.torque_workflow,
                                                       parent=self.container)
        self.pickup_btn = self.gui.create_button("Stage Empty Workholding", "#25BC9F", command=self.torque_complete,
                                                   parent=self.container)
        self.unclamp_btn = self.gui.create_button("Unclamp", "#25BC9F", command=self._unclamp,
                                                 parent=self.container)
        self.clamp_btn = self.gui.create_button("Clamp", "#25BC9F", command=self._clamp,
                                                 parent=self.container)
        self.complete_btn = self.gui.create_button("Done", "#25BC9F", command=self.on_complete_workflow,
                                                   parent=self.container)

    def torque_workflow(self):
        self.gui.clear_content()
        self.container = self.gui.create_centered_container()
        self.status_label = self.gui.create_label("Request for door facing operator", parent=self.container)

        self.progress_bar = ttk.Progressbar(self.container, mode="indeterminate", bootstyle="info-strip")
        self.progress_bar.pack(pady=(0, 20))
        self.progress_bar.start(10)

        self.abort_btn = self.gui.create_button("Abort", "#FF0000", command=self.abort,
                                                parent=self.container)

        self.check_door_facing_operator_torque()

        # self.gui.root.after(10_000, self.torque_complete)

    def show_waiting_for_torques(self):
        self.gui.clear_content()
        self.container = self.gui.create_centered_container()
        self.status_label = self.gui.create_label("Waiting For Torques", parent=self.container)

        self.progress_bar = ttk.Progressbar(self.container, mode="indeterminate", bootstyle="info-strip")
        self.progress_bar.pack(pady=(0, 20))
        self.progress_bar.start(10)

        self.spacing_label = self.gui.create_label("", parent=self.container)
        self.tool_label = self.gui.create_label("Tool: --", parent=self.container)
        self.tool_type_label = self.gui.create_label("Type: --", parent=self.container)
        self.batch_counter_label = self.gui.create_label("Batch Count: --", parent=self.container)
        self.batch_size_label = self.gui.create_label("Batch Size: --", parent=self.container)

        # Tool image placeholder
        self.tool_image_label = self.gui.create_label("", parent=self.container)

        self.unclamp_btn = self.gui.create_button("Unclamp", "#25BC9F", command=self._unclamp,
                                                 parent=self.container)
        self.clamp_btn = self.gui.create_button("Clamp", "#25BC9F", command=self._clamp,
                                                 parent=self.container)

        self.abort_btn = self.gui.create_button("Abort", "#FF0000", command=self.abort,
                                                parent=self.container)

        self.container.after(100, self.start_torque_program)

    def start_torque_program(self):
        self.client.set_variable_latest_value(device_id=self.robot_id, variable_name="replace_part", value=True)
        torque_program = self.client.get_selected_part_property(property="properties.torque_program").text.strip()
        self.torque_override = self.client.get_selected_part_property(property="properties.Override_Torque").text.strip()
        self.tool_type = self.client.get_selected_part_property(property="properties.Faster soccket size").text.strip()

        if self.torque_override == "True":
            self.torque_complete()
        else:
            print ("Loading torque program...")
            print (torque_program)
            self.client.load_program(device_id=self.torque_controller_id, program_name=torque_program)
            print("Torque program loaded")
            self.check_torque_complete()


    def check_door_facing_operator_torque(self):
        door_state = self.client.read_input(device_id=self.wago_id, input_number="42").text.strip()
        print("Door state: " + door_state)
        if door_state == "1":
            # Check again in 1000 ms (1 second)
            self.container.after(1000, self.check_door_facing_operator_torque)
        else:
            # TODO execute command to restart robot
            self.show_waiting_for_torques()
            # self.client.execute_command(device_id=self.robot_id, command_name="RESTART_ROBOT", args={})



    def check_torque_complete(self):
        torque_status = self.client.read_status(device_id=self.torque_controller_id).text.strip()
        print("Torque status: " + torque_status)
        current_tool = self.client.execute_command(device_id=self.torque_controller_id, command_name="GET_CURRENT_TOOL",
                                                   args={}).text.strip().strip('"')
        batch_counter = self.client.execute_command(device_id=self.torque_controller_id, command_name="READ_BATCH_COUNTER",
                                                   args={}).text.strip().strip('"')
        current_batch_size = self.client.execute_command(device_id=self.torque_controller_id, command_name="READ_CURRENT_BATCH_SIZE",
                                                   args={}).text.strip().strip('"')

        if current_tool == "0001":
            current_tool = "20 ft/lb"
        if current_tool == "0002":
            current_tool = "100 in/lb"
        if current_tool == "0003":
            current_tool = "45 ft/lb"
        if current_tool == "0004":
            current_tool = "70 ft/lb"


        if not hasattr(self, "_last_batch_counter"):
            self._last_batch_counter = batch_counter

        if batch_counter != self._last_batch_counter:
            # Flash green when batch count changes
            self.gui.flash_background("#00FF00", duration=300)

        self._last_batch_counter = batch_counter

        self.tool_label.config(text=f"Tool: {current_tool}")
        print("Tool: " + str(current_tool))
        self.tool_type_label.config(text=f"Type: {self.tool_type}")
        print("Tool Type: " + str(self.tool_type))
        self.batch_counter_label.config(text=f"Batch Count: {batch_counter}")
        print("Batch Counter: " + str(batch_counter))
        self.batch_size_label.config(text=f"Batch Size: {current_batch_size}")
        print("Batch Size: " + str(current_batch_size))

        # Update tool image based on tool number
        script_dir = os.path.dirname(os.path.abspath(__file__))
        image_path = os.path.join(script_dir, f"tool_{current_tool}.png")

        if os.path.exists(image_path):
            self.tool_img = PhotoImage(file=image_path).subsample(4, 4)  # shrink to 1/4 size
            # keep reference
            self.tool_image_label.config(image=self.tool_img, text="")  # clear text when image shown
        else:
            self.tool_image_label.config(image="", text=f"No image for tool {current_tool}")

        if torque_status == "RUNNING":
            # Check again in 1000 ms (1 second)
            self.container.after(1000, self.check_torque_complete)
        else:
            self.torque_complete()

    def torque_complete(self):
        self._stop_and_remove_progress_bar()
        self.gui.clear_content()
        self.container = self.gui.create_centered_container()

        self.status_label = self.gui.create_label("Request to enter then present part to robot", parent=self.container)
        self.waiting_label = self.gui.create_label("Waiting for door to be presented to robot...", parent=self.container)

        self.progress_bar = ttk.Progressbar(self.container, mode="indeterminate", bootstyle="info-strip")
        self.progress_bar.pack(pady=(0, 20))
        self.progress_bar.start(10)

        if self.torque_override == "True":
            self.unclamp_btn = self.gui.create_button("Unclamp", "#25BC9F", command=self._unclamp,
                                                      parent=self.container)
            self.clamp_btn = self.gui.create_button("Clamp", "#25BC9F", command=self._clamp,
                                                    parent=self.container)

        self.abort_btn = self.gui.create_button("Abort", "#FF0000", command=self.abort,
                                                parent=self.container)

        self.check_door_facing_robot()

        # self.gui.root.after(10_000, self.call_robot_pickup)

    def check_door_facing_robot(self):
        door_state = self.client.read_input(device_id=self.wago_id, input_number="43").text.strip()
        print("Door state: " + door_state)
        if door_state == "1":
            # Check again in 1000 ms (1 second)
            print("waiting")
            self.container.after(1000, self.check_door_facing_robot)
        else:
            # TODO execute command to restart robot
            self.show_waiting_for_robot_pickup()
            # self.client.execute_command(device_id=self.robot_id, command_name="RESTART_ROBOT", args={})

    def call_robot_pickup(self):
        self._stop_and_remove_progress_bar()
        self.gui.clear_content()
        self.container = self.gui.create_centered_container()

        self.status_label = self.gui.create_label("Door is shut, call robot to pick part", parent=self.container)
        self.complete_btn = self.gui.create_button("Call Robot Pickup", "#25BC9F", command=self.show_waiting_for_robot_pickup,
                                                   parent=self.container)
        self.abort_btn = self.gui.create_button("Abort", "#FF0000", command=self.abort,
                                                parent=self.container)

    def show_waiting_for_robot_pickup(self):
        # time.sleep(1)
        self.gui.clear_content()
        self.container = self.gui.create_centered_container()

        # self.workcell_status_label = "Workcell Status: "
        self.status_label = self.gui.create_label("Waiting for robot to pick up part...", parent=self.container)

        self.progress_bar = ttk.Progressbar(self.container, mode="indeterminate", bootstyle="info-strip")
        self.progress_bar.pack(pady=(0, 20))
        self.progress_bar.start(10)

        self.resume_btn = self.gui.create_button("Resume", "#25BC9F", command=self.resume,
                                                 parent=self.container)
        self.abort_btn = self.gui.create_button("Abort", "#FF0000", command=self.abort,
                                                parent=self.container)

        self.set_robot_pickup()

        self.check_robot_pickup()

        # self.gui.root.after(10_000, self.on_complete_workflow)

    def set_robot_pickup(self):
        # print ("Resuming robot program")
        # self.client.execute_command(device_id=self.robot_id, command_name="RESTART_ROBOT")
        print ("Setting robot pick ip true...")
        self.client.set_variable_latest_value(device_id=self.robot_id, variable_name="robot_pickup_op",
                                              value=True)

    def check_robot_pickup(self):
        value = self.client.get_variable_latest_value(
            device_id=self.robot_id, variable_name="robot_pickup_op"
        ).text.strip()
        # workcell_status = self.client.get_workcell_status()
        # self.workcell_status_label.config(text=f"Workcell Status: {workcell_status}")
        print("Robot pick up operator station: " + value)
        if value == "true":
            # Check again in 1000 ms (1 second)
            self.container.after(1000, self.check_robot_pickup)
        else:
            self.on_complete_workflow()

    def on_complete_workflow(self):
        self.gui.close()

    def abort(self):
        self.client.set_variable_latest_value(device_id=self.robot_id, variable_name="robot_pickup_op",
                                              value=False)
        self.client.set_variable_latest_value(device_id=self.robot_id, variable_name="robot_dropoff_op",
                                              value=False)
        self.client.set_variable_latest_value(device_id=self.robot_id, variable_name="force_dropoff",
                                              value=False).text
        self.gui.close()

    def resume(self):
        self.client.execute_command(device_id=self.robot_id, command_name="RESTART_ROBOT", args={})

    def _unclamp(self):
        self.client.set_output(device_id=self.wago_id, output_number="43", state="1")

    def _clamp(self):
        self.client.set_output(device_id=self.wago_id, output_number="43", state="0")

    def _stop_and_remove_progress_bar(self):
        if self.progress_bar:
            self.progress_bar.stop()
            self.progress_bar.destroy()
            self.progress_bar = None


# -----------------------
# Entry Point
# -----------------------
if __name__ == "__main__":
    app = FlexxTorqueWorkflowApp()
    app.main_entry_menu()
