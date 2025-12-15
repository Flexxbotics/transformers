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

    def set_selected_part_index(self):
        endpoint = "/infeeds/selectedPart"
        params = {"partIndex": "True"}
        res = self.send_get_request(endpoint=endpoint, params=params)

        return res

    def get_selected_shelf_index(self):
        endpoint = "/infeeds/selectedPart"
        params = {"shelfIndex": "True"}
        res = self.send_get_request(endpoint=endpoint, params=params)

        return res

    def set_selected_shelf_index(self):
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

    def create_dropdown(self, options, parent=None, default=None, command=None):
        if parent is None:
            parent = self.inner_frame

        style_name = "Flexx.TCombobox"
        style = ttk.Style()
        style.configure(
            style_name,
            padding=(15, 25),
            font=("Roboto", 16),  # bigger font
            arrowsize=18,  # bigger dropdown arrow
        )

        combo = ttk.Combobox(
            parent,
            values=options,
            state="readonly",
            style=style_name,
            font=("Roboto", 16),  # force entry text larger
            justify="center",  # center text inside
            width=20
        )

        if default:
            combo.set(default)

        combo.pack(pady=5)

        # apply font to dropdown menu (OS-dependent hack)
        try:
            combo.option_add("*TCombobox*Listbox*Font", "Roboto 16")
        except:
            pass

        if command:
            combo.bind("<<ComboboxSelected>>", lambda e: command(combo.get()))

        return combo

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
class RemoveCarouselPartWorkflowApp:
    def __init__(self):
        self.gui = FlexxGUI()
        self.client = FlexxCoreClient(flask_port=7081)
        self.progress_bar = None

        self.robot_id = "688c5fb834dd9e275c2674a7"
        self.torque_controller_id = "68a1f8958ee549d814213745"
        self.wago_id = "68921f6094bc3d988cb889c1"

    def main_entry_menu(self):
        self.container = self.gui.create_centered_container()
        self.shelf_label = self.gui.create_label(text="SELECT SHELF:", parent=self.container)
        self.shelf_drop_down = self.gui.create_dropdown(options=["1", "2", "3", "4", "5", "6"], parent=self.container)
        self.spacing_label = self.gui.create_label(text="", parent=self.container)


        self.part_label = self.gui.create_label(text="SELECT PART:", parent=self.container)
        self.part_drop_down = self.gui.create_dropdown(options=["1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "12", "13", "14", "15"], parent=self.container)
        self.spacing_label_2 = self.gui.create_label(text="", parent=self.container)

        self.execute_dropoff = self.gui.create_button("Execute Dropoff", "#25BC9F",
                                                          command=self.execute_dropoff, parent=self.container)
        self.abort_btn = self.gui.create_button("Abort", "#FF0000", command=self.abort,
                                                parent=self.container)
        self.gui.start()

    def execute_dropoff(self):
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

        self.status_label = self.gui.create_label("Waiting for robot to drop off part...", parent=self.container)

        self.progress_bar = ttk.Progressbar(self.container, mode="indeterminate", bootstyle="info-strip")
        self.progress_bar.pack(pady=(0, 20))
        self.progress_bar.start(10)
        self.abort_btn = self.gui.create_button("Abort", "#FF0000", command=self.abort,
                                                parent=self.container)


        self.container.after(200, self.set_robot_dropoff)

    def set_robot_dropoff(self):
        print ("Setting robot drop off")
        self.shelf_index = self.shelf_drop_down.get()
        self.part_index = self.part_drop_down.get()
        #TODO need to set the selected part index and selected shelf index

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
            # self.client.execute_command(device_id=self.robot_id, command_name="RESTART_ROBOT", args={})

    def ready_for_part_interaction(self):
        self._stop_and_remove_progress_bar()
        self.gui.clear_content()
        self.container = self.gui.create_centered_container()

        self.status_label = self.gui.create_label(
            "Door is facing operator. Press Complete.", parent=self.container)
        self.complete_btn = self.gui.create_button("Done", "#25BC9F", command=self.on_complete_workflow,
                                                   parent=self.container)

    def on_complete_workflow(self):
        self.gui.close()

    def abort(self):
        self.client.set_variable_latest_value(device_id=self.robot_id, variable_name="robot_pickup_op",
                                              value=False)
        self.client.set_variable_latest_value(device_id=self.robot_id, variable_name="robot_dropoff_op",
                                              value=False)
        self.gui.close()

    def _stop_and_remove_progress_bar(self):
        if self.progress_bar:
            self.progress_bar.stop()
            self.progress_bar.destroy()
            self.progress_bar = None


# -----------------------
# Entry Point
# -----------------------
if __name__ == "__main__":
    app = RemoveCarouselPartWorkflowApp()
    app.main_entry_menu()
