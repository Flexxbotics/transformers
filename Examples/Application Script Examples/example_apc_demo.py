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
import random
import matplotlib.pyplot as plt


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
        print (response_raw.text)
        return response_raw.text

    def send_post_request(self, endpoint, body):
        endpoint = self.api_base_url + endpoint
        print (endpoint)
        headers = {"Content-Type": "application/json"}
        response_raw = requests.post(url=endpoint, json=body, timeout=self.request_timeout, headers=headers)
        print (response_raw.text)
        return response_raw

    def send_patch_request(self, endpoint, body):
        endpoint = self.api_base_url + endpoint
        print (endpoint)
        headers = {"Content-Type": "application/json"}
        response_raw = requests.patch(url=endpoint, json=body, timeout=self.request_timeout, headers=headers)
        print (response_raw.text)
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
        res = self.send_get_request(endpoint=endpoint, params=params).strip()

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
    
    def set_device_status(self, device_id, status):
        endpoint = "/devices/"+device_id+"/status/"+status
        body = {}
        res = self.send_patch_request(endpoint=endpoint, body=body)

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
    
    def set_workcell_status(self, status):
        endpoint = "/workCell/status/"+status
        body = {}
        res = self.send_patch_request(endpoint=endpoint, body=body)

        return res
    
    def pick_event(self, device_id, infeed_idx=0, shelf_idx=0, part_idx=0, fixture_idx=0, suppress_cycle=True):
        endpoint = "/runRecords/events/pick"
        body = {"device_id": device_id, "fixture_index": fixture_idx, "infeed_index": infeed_idx, "shelf_index": shelf_idx, "part_index": part_idx, "suppress_cycle": suppress_cycle}
        res = self.send_post_request(endpoint=endpoint, body=body)

        return res
    
    def count_event(self, device_id, infeed_idx=0, shelf_idx=0, part_idx=0):
        endpoint = "/runRecords/events/partCount"
        body = {"device_id": device_id, "infeed_index": infeed_idx, "shelf_index": shelf_idx, "part_index": part_idx}
        res = self.send_post_request(endpoint=endpoint, body=body)

        return res
    
    def reset_parts(self, device_id, infeed_idx=0, shelf_idx=0, part_idx=0):
        endpoint = "/parts/serializedPart/reset"
        body = {"device_id": device_id, "infeed_index": infeed_idx, "shelf_index": shelf_idx, "part_index": part_idx}
        res = self.send_patch_request(endpoint=endpoint, body=body)
    
    def analog_variable_event(self, device_id, infeed_idx=0, shelf_idx=0, part_idx=0, variable_name="", variable_value=""):
        endpoint = "/runRecords/events/analog"
        body = {"device_id": device_id, "infeed_index": infeed_idx, "shelf_index": shelf_idx, "part_index": part_idx, "variable_name": variable_name, "value": variable_value}
        res = self.send_post_request(endpoint=endpoint, body=body)

        return res
    
    def contextual_event(self, event_type, metadata, monitoring_profile, name):
        endpoint = "/runRecords/events/contextualEvent"
        body = {
                "event_type": event_type,
                "metadata": metadata,
                "monitoring_profile": monitoring_profile,
                "name": name
            }
        res = self.send_post_request(endpoint=endpoint, body=body)

        return res
    

# -----------------------
# FlexxGUI Implementation
# -----------------------
class FlexxGUI:
    _root_instance = None  # Singleton root

    def __init__(self, fullscreen=True):
        self.fullscreen = fullscreen

        if FlexxGUI._root_instance is None:
            self.root = ttk.Window(themename="darkly")
            FlexxGUI._root_instance = self.root
            self._configure_root()
        else:
            self.root = FlexxGUI._root_instance
            self.clear_content()
            # Optionally update fullscreen state on reuse:
            self._apply_fullscreen_setting()

        self._setup_frames()
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.after(1000, lambda: self.root.attributes("-topmost", False))

    def _configure_root(self):
        self.root.geometry("1200x800")
        self.root.title("Flexx GUI")
        self.root.configure(bg="#132231")

        # Decide fullscreen vs 75% window
        self._apply_fullscreen_setting()

        default_font = tkfont.nametofont("TkDefaultFont")
        default_font.configure(family="Roboto", size=10)
        self.root.option_add("*Font", "Roboto 10")

        style = ttk.Style()
        style.configure("TFrame", background="#132231")
        style.configure("TLabel", background="#132231", foreground="white")

    def _apply_fullscreen_setting(self):
        if self.fullscreen:
            # Fullscreen
            self.root.attributes("-fullscreen", True)
        else:
            # Exit fullscreen and use ~75% of the screen, centered
            self.root.attributes("-fullscreen", False)

            screen_w = self.root.winfo_screenwidth()
            screen_h = self.root.winfo_screenheight()

            width = int(screen_w * 0.75)
            height = int(screen_h * 0.75)
            x = (screen_w - width) // 2
            y = (screen_h - height) // 2

            self.root.geometry(f"{width}x{height}+{x}+{y}")

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
    
    def temp_withdraw(self):
        """End mainloop but KEEP the root alive for reuse."""
        try:
            self.root.quit()       # exits mainloop
            self.root.withdraw()   # hide the window
        except Exception:
            pass  # just in case, but usually not needed
        # Note: DO NOT destroy the root here, and DO NOT reset _root_instance


# -----------------------
# FlexxWorkflowApp Script
# -----------------------
class OffsetVerificationWorkflow:

    def __init__(self, part_idx, dimension, offset_dim, tool_to_offset):
        self.gui = FlexxGUI(fullscreen=False)
        self.client = FlexxCoreClient(flask_port=7081)
        self.progress_bar = None

        self.client = FlexxCoreClient(flask_port="7081")
        self.workcell_id = "692f40578f37baa7415c8c8f"
        self.robot_id = "692c3d2570d56c4d326cc510"
        self.cnc_id = "692da1fd70d56c4d326d20b4"
        self.probe_id = "692da27fcaab7003bdd0910a"

        self.part_idx = part_idx
        self.dimension = dimension
        self.offset_dim = offset_dim
        self.tool_to_offset = tool_to_offset
        self.confirmed = False

    def run(self):

        self.container = self.gui.create_centered_container()
        self.confirm_label = self.gui.create_label("Confirm the following tool offset", parent=self.container)
        self.dimension_label = self.gui.create_label("Dimension: " + self.dimension, parent=self.container)
        self.offset_label = self.gui.create_label("Offset: " + str(self.offset_dim), parent=self.container)
        self.tool_label = self.gui.create_label("Tool: " + self.tool_to_offset, parent=self.container)
        self.confirm_btn = self.gui.create_button("Confirm Offset", "#25BC9F", command=self.confirm, parent=self.container)
        self.reject_btn = self.gui.create_button("Reject Offet", "#FF0000", command=self.reject, parent=self.container)

        self.gui.start()

        return self.confirmed
    
    def confirm(self):
        self.confirmed = True
        self.gui.temp_withdraw()
    
    def reject(self):
        self.confirmed = False
        self.gui.temp_withdraw()

# -----------------------
# FlexxWorkflowApp Script
# -----------------------
class ModigToolingWorkflow:

    def __init__(self):
        self.client = FlexxCoreClient(flask_port="7081")
        self.workcell_id = "692f40578f37baa7415c8c8f"
        self.robot_id = "692c3d2570d56c4d326cc510"
        self.cnc_id = "692da1fd70d56c4d326d20b4"
        self.probe_id = "692da27fcaab7003bdd0910a"

    def run(self):
        print ("Modig cell demo starting")
        self.client.set_device_status(device_id=self.workcell_id, status="IDLE")
        self.client.set_device_status(device_id=self.robot_id, status="IDLE")
        self.client.set_device_status(device_id=self.cnc_id, status="IDLE")
        self.client.set_device_status(device_id=self.probe_id, status="IDLE")
        part_idxs = range(25)
        for i in part_idxs:
            self.client.reset_parts(device_id=self.workcell_id, part_idx=i)
        dim_1_value = 0
        dim_2_value = 0
        dim_3_value = 0
        dim_4_value = 0
        dim_5_value = 0
        dim_6_value = 0
        dim_7_value = 0
        dim_8_value = 0
        dim_9_value = 0
        dim_10_value = 0
        dim_1_offset_value = 0
        dim_2_offset_value = 0
        dim_3_offset_value = 0
        dim_4_offset_value = 0
        dim_5_offset_value = 0
        dim_6_offset_value = 0
        dim_7_offset_value = 0
        dim_8_offset_value = 0
        dim_9_offset_value = 0
        dim_10_offset_value = 0
        total_parts = 24
        part_idx = 0
        spindle_load = random.uniform(40, 50)    # starts in the normal range
        feed_rate = 75                      # example constant feed
        spindle_speed = 25000                    # example constant RPM
        trending_up = True                      # state flag
        running = True

        while running:
            print ("WAITING FOR CYCLE START")
            #self.client.set_device_status(device_id=self.workcell_id, status="WAITING_FOR_CYCLE")
            time.sleep(1)

            print ("RUNNING")
            self.client.set_device_status(device_id=self.workcell_id, status="RUNNING")
            self.client.set_device_status(device_id=self.cnc_id, status="RUNNING")
            self.client.set_device_status(device_id=self.robot_id, status="RUNNING")
            self.client.pick_event(device_id=self.workcell_id, part_idx=part_idx)
            time.sleep(3)

            print ("LOAD_TOOL_1")
            # self.client.set_device_status(device_id=self.workcell_id, status="LOAD_TOOL_25")
            #self.client.set_device_status(device_id=self.robot_id, status="LOAD_TOOL_25")
            time.sleep(1)
            self.client.set_variable_latest_value(device_id=self.cnc_id, variable_name="active_tool", value=25)
            #self.client.set_device_status(device_id=self.robot_id, status="IDLE")

            print ("RUNNING")
            self.client.set_device_status(device_id=self.workcell_id, status="RUNNING")
            time.sleep(3)

            print ("LOAD_TOOL_2")
            # self.client.set_device_status(device_id=self.workcell_id, status="LOAD_TOOL_17")
            #self.client.set_device_status(device_id=self.robot_id, status="LOAD_TOOL_17")
            time.sleep(1)
            self.client.set_variable_latest_value(device_id=self.cnc_id, variable_name="active_tool", value=17)
            #self.client.set_device_status(device_id=self.robot_id, status="IDLE")

            print ("RUNNING")
            self.client.set_device_status(device_id=self.workcell_id, status="RUNNING")
            time.sleep(3)

            print ("LOAD_TOOL_3")
            #self.client.set_device_status(device_id=self.workcell_id, status="LOAD_TOOL_18")
            #self.client.set_device_status(device_id=self.robot_id, status="LOAD_TOOL_18")
            time.sleep(1)
            self.client.set_variable_latest_value(device_id=self.cnc_id, variable_name="active_tool", value=18)
            #self.client.set_device_status(device_id=self.robot_id, status="IDLE")

            print ("RUNNING")
            self.client.set_device_status(device_id=self.workcell_id, status="RUNNING")
            time.sleep(3)

            print ("LOAD_TOOL_4")
            #self.client.set_device_status(device_id=self.workcell_id, status="LOAD_TOOL_51")
            #self.client.set_device_status(device_id=self.robot_id, status="LOAD_TOOL_51")
            time.sleep(1)
            self.client.set_variable_latest_value(device_id=self.cnc_id, variable_name="active_tool", value=51)
            #self.client.set_device_status(device_id=self.robot_id, status="IDLE")

            print ("RUNNING")
            self.client.set_device_status(device_id=self.workcell_id, status="RUNNING")
            time.sleep(3)

            print ("LOAD_TOOL_5")
            # self.client.set_device_status(device_id=self.workcell_id, status="LOAD_TOOL_19")
            #self.client.set_device_status(device_id=self.robot_id, status="LOAD_TOOL_19")
            time.sleep(1)
            self.client.set_variable_latest_value(device_id=self.cnc_id, variable_name="active_tool", value=19)
            #self.client.set_device_status(device_id=self.robot_id, status="IDLE")

            print ("RUNNING")
            self.client.set_device_status(device_id=self.workcell_id, status="RUNNING")
            time.sleep(3)

            print ("LOAD_TOOL_20")
            # ACTIVEself.client.set_device_status(device_id=self.workcell_id, status="LOAD_TOOL_20")
            #self.client.set_device_status(device_id=self.robot_id, status="LOAD_TOOL_20")
            time.sleep(1)
            self.client.set_variable_latest_value(device_id=self.cnc_id, variable_name="active_tool", value=20)
            #self.client.set_device_status(device_id=self.robot_id, status="IDLE")

            print ("RUNNING")
            self.client.set_device_status(device_id=self.workcell_id, status="RUNNING")
            time.sleep(3)

            print ("LOAD_TOOL_80")
            # self.client.set_device_status(device_id=self.workcell_id, status="LOAD_TOOL_80")
            #self.client.set_device_status(device_id=self.robot_id, status="LOAD_TOOL_80")
            time.sleep(1)
            self.client.set_variable_latest_value(device_id=self.cnc_id, variable_name="active_tool", value=80)
            #self.client.set_device_status(device_id=self.robot_id, status="IDLE")

            print ("RUNNING")
            self.client.set_device_status(device_id=self.workcell_id, status="RUNNING")
            time.sleep(3)

            print ("LOAD_TOOL_24")
            # self.client.set_device_status(device_id=self.workcell_id, status="LOAD_TOOL_24")
            #self.client.set_device_status(device_id=self.robot_id, status="LOAD_TOOL_24")
            time.sleep(1)
            self.client.set_variable_latest_value(device_id=self.cnc_id, variable_name="active_tool", value=24)
            #self.client.set_device_status(device_id=self.robot_id, status="IDLE")

            print ("RUNNING")
            self.client.set_device_status(device_id=self.workcell_id, status="RUNNING")
            time.sleep(3)

            print ("LOAD_TOOL_9")
            # self.client.set_device_status(device_id=self.workcell_id, status="LOAD_TOOL_9")
            #self.client.set_device_status(device_id=self.robot_id, status="LOAD_TOOL_9")
            time.sleep(1)
            self.client.set_variable_latest_value(device_id=self.cnc_id, variable_name="active_tool", value=9)
            #self.client.set_device_status(device_id=self.robot_id, status="IDLE")

            print ("RUNNING TOOL 9")
            show_graph = self.client.get_variable_latest_value(device_id=self.cnc_id, variable_name="spindle_load_graph")
            print ("show spindle graph: " + str(show_graph))
            if show_graph == "True" or show_graph == "true" or show_graph == True:
                show_graph = True
            else:
                show_graph = False
            peak_spindle_load, avg_spindle_load, feed_rate, spindle_speed = self.spindle_load_T9(part_idx, show_graph=show_graph)
            self.client.set_device_status(device_id=self.workcell_id, status="RUNNING")
            time.sleep(3)

            print ("RUNNING_PROBE")
            # self.client.set_device_status(device_id=self.workcell_id, status="RUNNING_PROBE")
            self.client.set_device_status(device_id=self.probe_id, status="RUNNING_PROBE")
            time.sleep(3)
            self.client.set_device_status(device_id=self.probe_id, status="IDLE")

            print ("CYCLE_END_DETECTED")
            # self.client.set_device_status(device_id=self.workcell_id, status="CYCLE_END_DETECTED")
            self.client.set_device_status(device_id=self.cnc_id, status="IDLE")
            self.client.set_device_status(device_id=self.robot_id, status="IDLE")
            self.client.count_event(device_id=self.workcell_id, part_idx=part_idx)
            time.sleep(1)

            print ("READING MACROS")
            # Critical dimension 1, Hole Diameter, 501, +/- .02
            new_value = round(random.uniform(0, .01), 5)
            dim_1_value = dim_1_value + new_value
            self.client.set_variable_latest_value(device_id=self.cnc_id, variable_name="501", value=round(dim_1_value, 5))
            self.client.analog_variable_event(device_id=self.cnc_id, part_idx=part_idx, variable_name="501", variable_value=round(dim_1_value, 5))
            if abs(dim_1_value) > 0.015:
                dim_1_offset = True
                dim_1_offset_value = dim_1_offset_value + dim_1_value
                verify = self.client.get_variable_latest_value(device_id=self.cnc_id, variable_name="offset_verification")
                print ("dim1 verify: " + str(verify))
                if verify == "true" or verify == "True" or verify == True:
                    offset_workflow = OffsetVerificationWorkflow(part_idx=part_idx, dimension="Hole Diameter +/- .020in", offset_dim=dim_1_offset_value, tool_to_offset="T25")
                    offset_confirmed = offset_workflow.run()
                    if offset_confirmed:
                        dim_1_value = 0
                    else:
                        dim_1_offset_value = dim_1_offset_value - dim_1_value
                else:
                    dim_1_value = 0
                    offset_confirmed = True

            self.client.set_variable_latest_value(device_id=self.cnc_id, variable_name="516", value=round(dim_1_offset_value, 5))
            self.client.analog_variable_event(device_id=self.cnc_id, part_idx=part_idx, variable_name="516", variable_value=round(dim_1_offset_value, 5))

            # Critical dimenation 2, Slot Width, 502, +/- .01
            new_value = round(random.uniform(0, .001), 5)
            dim_2_value = dim_2_value + new_value
            self.client.set_variable_latest_value(device_id=self.cnc_id, variable_name="502", value=round(dim_2_value, 5))
            self.client.analog_variable_event(device_id=self.cnc_id, part_idx=part_idx, variable_name="502", variable_value=round(dim_2_value, 5))
            if abs(dim_2_value) > 0.0075:
                dim_2_offset = True
                dim_2_offset_value = dim_2_offset_value + dim_2_value
                verify = self.client.get_variable_latest_value(device_id=self.cnc_id, variable_name="offset_verification")
                if verify == "true" or verify == "True" or verify == True:
                    offset_workflow = OffsetVerificationWorkflow(part_idx=part_idx, dimension="Slot Width +/- .010in", offset_dim=dim_2_offset_value, tool_to_offset="T17")
                    offset_confirmed = offset_workflow.run()
                    if offset_confirmed:
                        dim_2_value = 0
                    else:
                        dim_2_offset_value = dim_2_offset_value - dim_2_value
                else:
                    dim_2_value = 0
                    offset_confirmed = True

            self.client.set_variable_latest_value(device_id=self.cnc_id, variable_name="512", value=round(dim_2_offset_value, 5))
            self.client.analog_variable_event(device_id=self.cnc_id, part_idx=part_idx, variable_name="512", variable_value=round(dim_2_offset_value, 5))

            # Critical dimension, Boss Height, 503, +/- .0075
            new_value = round(random.uniform(0, .002), 5)
            dim_3_value = dim_3_value + new_value
            self.client.set_variable_latest_value(device_id=self.cnc_id, variable_name="503", value=dim_3_value)
            self.client.analog_variable_event(device_id=self.cnc_id, part_idx=part_idx, variable_name="503", variable_value=dim_3_value)
            if abs(dim_3_value) > 0.005:
                dim_3_offset = True
                dim_3_offset_value = dim_3_offset_value + dim_3_value
                verify = self.client.get_variable_latest_value(device_id=self.cnc_id, variable_name="offset_verification")
                if verify == "true" or verify == "True" or verify == True:
                    offset_workflow = OffsetVerificationWorkflow(part_idx=part_idx, dimension="Boss Height +/- .0075in", offset_dim=dim_3_offset_value, tool_to_offset="T18")
                    offset_confirmed = offset_workflow.run()
                    if offset_confirmed:
                        dim_3_value = 0
                    else:
                        dim_3_offset_value = dim_3_offset_value - dim_3_value
                else:
                    dim_3_value = 0
                    offset_confirmed = True

            self.client.set_variable_latest_value(device_id=self.cnc_id, variable_name="513", value=dim_3_offset_value)
            self.client.analog_variable_event(device_id=self.cnc_id, part_idx=part_idx, variable_name="513", variable_value=dim_3_offset_value)

            # Critical dimension 4, Lug Thickness, 504, +/- .02
            new_value = round(random.uniform(0, .005), 5)
            self.client.set_variable_latest_value(device_id=self.cnc_id, variable_name="504", value=dim_4_value)
            self.client.analog_variable_event(device_id=self.cnc_id, part_idx=part_idx, variable_name="504", variable_value=dim_4_value)
            if abs(dim_4_value) > 0.015:
                dim_4_offset = True
                dim_4_offset_value = dim_4_offset_value + dim_4_value
                verify = self.client.get_variable_latest_value(device_id=self.cnc_id, variable_name="offset_verification")
                if verify == "true" or verify == "True" or verify == True:
                    offset_workflow = OffsetVerificationWorkflow(part_idx=part_idx, dimension="Lug Thickness +/- .020in", offset_dim=dim_4_offset_value, tool_to_offset="T51")
                    offset_confirmed = offset_workflow.run()
                    if offset_confirmed:
                        dim_4_value = 0
                    else:
                        dim_4_offset_value = dim_4_offset_value - dim_4_value
                else:
                    dim_4_value = 0
                    offset_confirmed = True

            self.client.set_variable_latest_value(device_id=self.cnc_id, variable_name="517", value=dim_4_offset_value)
            self.client.analog_variable_event(device_id=self.cnc_id, part_idx=part_idx, variable_name="517", variable_value=dim_4_offset_value)


            # Critical dimenstion 5, Web Thickness, 505, +/- .015
            new_value = round(random.uniform(0, .005), 5)
            dim_5_value = dim_5_value + new_value
            self.client.set_variable_latest_value(device_id=self.cnc_id, variable_name="505", value=dim_5_value)
            self.client.analog_variable_event(device_id=self.cnc_id, part_idx=part_idx, variable_name="505", variable_value=dim_5_value)
            if abs(dim_5_value) > 0.01:
                dim_5_offset = True
                dim_5_offset_value = dim_5_offset_value + dim_5_value
                verify = self.client.get_variable_latest_value(device_id=self.cnc_id, variable_name="offset_verification")
                if verify == "true" or verify == "True" or verify == True:
                    offset_workflow = OffsetVerificationWorkflow(part_idx=part_idx, dimension="Web Thickness +/- .015in", offset_dim=dim_5_offset_value, tool_to_offset="T19")
                    offset_confirmed = offset_workflow.run()
                    if offset_confirmed:
                        dim_5_value = 0
                    else:
                        dim_5_offset_value = dim_5_offset_value - dim_5_value
                else:
                    dim_5_value = 0
                    offset_confirmed = True

            self.client.set_variable_latest_value(device_id=self.cnc_id, variable_name="519", value=dim_5_offset_value)
            self.client.analog_variable_event(device_id=self.cnc_id, part_idx=part_idx, variable_name="519", variable_value=dim_5_offset_value)

            # Critical dimension 1, length, 506, +/- .03
            new_value = round(random.uniform(0, .005), 5)
            dim_6_value = dim_6_value + new_value
            self.client.set_variable_latest_value(device_id=self.cnc_id, variable_name="506", value=dim_6_value)
            self.client.analog_variable_event(device_id=self.cnc_id, part_idx=part_idx, variable_name="506", variable_value=dim_6_value)
            if abs(dim_6_value) > 0.024:
                dim_6_offset = True
                dim_6_offset_value = dim_6_offset_value + dim_6_value
                verify = self.client.get_variable_latest_value(device_id=self.cnc_id, variable_name="offset_verification")
                if verify == "true" or verify == "True" or verify == True:
                    offset_workflow = OffsetVerificationWorkflow(part_idx=part_idx, dimension="Length +/- .030in", offset_dim=dim_2_offset_value, tool_to_offset="T20")
                    offset_confirmed = offset_workflow.run()
                    if offset_confirmed:
                        dim_6_value = 0
                    else:
                        dim_6_offset_value = dim_6_offset_value - dim_6_value
                else:
                    dim_6_value = 0
                    offset_confirmed = True

            self.client.set_variable_latest_value(device_id=self.cnc_id, variable_name="514", value=dim_6_offset_value)
            self.client.analog_variable_event(device_id=self.cnc_id, part_idx=part_idx, variable_name="514", variable_value=dim_6_offset_value)

            # Critical dimenation 2, width, 507, +/- .03
            new_value = round(random.uniform(0, .005), 5)
            dim_7_value = dim_7_value + new_value
            self.client.set_variable_latest_value(device_id=self.cnc_id, variable_name="507", value=dim_7_value)
            self.client.analog_variable_event(device_id=self.cnc_id, part_idx=part_idx, variable_name="507", variable_value=dim_7_value)
            if abs(dim_7_value) > 0.024:
                dim_7_offset = True
                dim_7_offset_value = dim_7_offset_value + dim_7_value
                verify = self.client.get_variable_latest_value(device_id=self.cnc_id, variable_name="offset_verification")
                if verify == "true" or verify == "True" or verify == True:
                    offset_workflow = OffsetVerificationWorkflow(part_idx=part_idx, dimension="Width +/- .030in", offset_dim=dim_7_offset_value, tool_to_offset="T20")
                    offset_confirmed = offset_workflow.run()
                    if offset_confirmed:
                        dim_7_value = 0
                    else:
                        dim_7_offset_value = dim_7_offset_value - dim_7_value
                else:
                    dim_7_value = 0
                    offset_confirmed = True

            self.client.set_variable_latest_value(device_id=self.cnc_id, variable_name="514", value=dim_7_offset_value)
            self.client.analog_variable_event(device_id=self.cnc_id, part_idx=part_idx, variable_name="514", variable_value=dim_7_offset_value)

            # Critical dimension, corner radius, 508, +/- .015
            new_value = round(random.uniform(0, .003), 5)
            dim_8_value = dim_8_value + new_value
            self.client.set_variable_latest_value(device_id=self.cnc_id, variable_name="508", value=dim_8_value)
            self.client.analog_variable_event(device_id=self.cnc_id, part_idx=part_idx, variable_name="508", variable_value=dim_8_value)
            if abs(dim_8_value) > 0.01:
                dim_8_offset = True
                dim_8_offset_value = dim_8_offset_value + dim_8_value
                verify = self.client.get_variable_latest_value(device_id=self.cnc_id, variable_name="offset_verification")
                if verify == "true" or verify == "True" or verify == True:
                    offset_workflow = OffsetVerificationWorkflow(part_idx=part_idx, dimension="Corner Radius +/- .015in", offset_dim=dim_8_offset_value, tool_to_offset="T80")
                    offset_confirmed = offset_workflow.run()
                    if offset_confirmed:
                        dim_8_value = 0
                    else:
                        dim_8_offset_value = dim_8_offset_value - dim_8_value
                else:
                    dim_8_value = 0
                    offset_confirmed = True

            self.client.set_variable_latest_value(device_id=self.cnc_id, variable_name="518", value=dim_8_offset_value)
            self.client.analog_variable_event(device_id=self.cnc_id, part_idx=part_idx, variable_name="518", variable_value=dim_8_offset_value)

            # Critical dimension 4, datum hole, 509, +/- .015
            new_value = round(random.uniform(0, .003), 5)
            dim_9_value = dim_9_value + new_value
            self.client.set_variable_latest_value(device_id=self.cnc_id, variable_name="509", value=dim_9_value)
            self.client.analog_variable_event(device_id=self.cnc_id, part_idx=part_idx, variable_name="509", variable_value=dim_9_value)
            if abs(dim_9_value) > 0.01:
                dim_9_offset = True
                dim_9_offset_value = dim_9_offset_value + dim_9_value
                verify = self.client.get_variable_latest_value(device_id=self.cnc_id, variable_name="offset_verification")
                if verify == "true" or verify == "True" or verify == True:
                    offset_workflow = OffsetVerificationWorkflow(part_idx=part_idx, dimension="Datum Hole +/- .015in", offset_dim=dim_9_offset_value, tool_to_offset="T24")
                    offset_confirmed = offset_workflow.run()
                    if offset_confirmed:
                        dim_9_value = 0
                    else:
                        dim_9_offset_value = dim_9_offset_value - dim_9_value
                else:
                    dim_9_value = 0
                    offset_confirmed = True

            self.client.set_variable_latest_value(device_id=self.cnc_id, variable_name="515", value=dim_9_offset_value)
            self.client.analog_variable_event(device_id=self.cnc_id, part_idx=part_idx, variable_name="515", variable_value=dim_9_offset_value)

            # Critical dimenstion 5, flatness, 510, +/- .015
            new_value = round(random.uniform(0, .003), 5)
            dim_10_value = dim_10_value + new_value
            self.client.set_variable_latest_value(device_id=self.cnc_id, variable_name="510", value=dim_10_value)
            self.client.analog_variable_event(device_id=self.cnc_id, part_idx=part_idx, variable_name="510", variable_value=dim_10_value)
            if abs(dim_10_value) > 0.01:
                dim_10_offset = True
                dim_10_offset_value = dim_10_offset_value + dim_10_value
                verify = self.client.get_variable_latest_value(device_id=self.cnc_id, variable_name="offset_verification")
                if verify == "true" or verify == "True" or verify == True:
                    offset_workflow = OffsetVerificationWorkflow(part_idx=part_idx, dimension="Flatness +/- .015in", offset_dim=dim_10_offset_value, tool_to_offset="T9")
                    offset_confirmed = offset_workflow.run()
                    if offset_confirmed:
                        dim_10_value = 0
                    else:
                        dim_10_offset_value = dim_10_offset_value - dim_10_value
                else:
                    dim_10_value = 0
                    offset_confirmed = True

            self.client.set_variable_latest_value(device_id=self.cnc_id, variable_name="511", value=dim_10_offset_value)
            self.client.analog_variable_event(device_id=self.cnc_id, part_idx=part_idx, variable_name="511", variable_value=dim_10_offset_value)

            # Contextual event
            event_type = "normal_cycle",
            metadata = {"measurement_from_nominal": dim_10_value, "tool_offset": dim_10_offset_value, "peak_spindle_load": peak_spindle_load, "avg_spindle_load": avg_spindle_load,"feed_rate": feed_rate, "spindle_speed": spindle_speed}
            monitoring_profile = "tool_monitor_profile"
            name = "T9 Tool"
            self.client.contextual_event(event_type=event_type, metadata=metadata, monitoring_profile=monitoring_profile, name=name)

            part_idx += 1
            if part_idx == 24:
                part_idxs = range(25)
                for i in part_idxs:
                    self.client.reset_parts(device_id=self.workcell_id, part_idx=i)
    
        print ("WAITING FOR CYCLE START")
        # self.client.set_device_status(device_id=self.workcell_id, status="WAITING_FOR_CYCLE")
        # self.client.set_workcell_status(status="WAITING_FOR_CYCLE")
    
    def spindle_load_T9(self, part_idx: int, show_graph: bool = False):
        """
        Simulate T9 spindle behavior:

        - 4–5 internal mini-cycles while T9 is active.
        - First 3–4 cycles: spindle load ramps up/down but stays < 70%.
        - On the 4th or 5th cycle: load ramps up, crosses 70%,
          then feed rate and spindle speed are reduced to bring load
          back down to ~55%.
        - Streams values via FlexxCore to variables:
            550 = spindle load (%)
            551 = feed rate
            552 = spindle speed

        If show_graph=True:
            Opens a realtime matplotlib window styled like the FlexxGUI:
              - Dark blue background (#132231)
              - White text/axes
              - Bright line colors
            Brings the window to the front and auto-closes it at the end.
        """

        # ---------- Base conditions for T9 ----------
        spindle_load = random.uniform(40.0, 50.0)   # starting load (%)
        base_feed = 75.0                            # starting feed rate
        base_rpm = 25000.0                          # starting spindle speed

        feed_rate = base_feed
        spindle_speed = base_rpm

        overload_cycle = random.randint(4, 5)       # which mini-cycle will exceed 70%
        steps_per_cycle = 10                        # steps in one mini-cycle
        step_dt = 0.15                              # seconds per step
        max_total_time = 15.0                       # safety cap

        cycle_index = 1
        start_time = time.time()

        spindle_load_samples = []

        # ---------- Optional realtime plot setup ----------
        fig = None
        ax = None
        if show_graph:
            plt.ion()
            fig, ax = plt.subplots()

            # Try to bring window to front (TkAgg-style)
            try:
                win = fig.canvas.manager.window
                win.deiconify()
                win.lift()
                win.focus_force()
                # Make it topmost briefly so it pops in front
                win.attributes("-topmost", True)
            except Exception:
                pass  # backend might not be TkAgg; fail gracefully

            fig.canvas.manager.set_window_title("T9 Spindle / Feed / RPM")
            plt.show(block=False)

            # --- Styling to match FlexxGUI ---
            bg_color = "#132231"
            text_color = "white"

            fig.patch.set_facecolor(bg_color)
            ax.set_facecolor(bg_color)

            for spine in ax.spines.values():
                spine.set_color(text_color)

            ax.tick_params(colors=text_color)
            ax.xaxis.label.set_color(text_color)
            ax.yaxis.label.set_color(text_color)

            ax.grid(True, color="#335577")

            # --- 70% Threshold Line (dashed) ---
            ax.axhline(
                y=70,
                color="#FF6666",       # bright red/pink to pop on dark bg
                linestyle="--",
                linewidth=1.6,
                label="Overload Threshold (70%)"
            )

            t_values = []
            load_values = []
            feed_pct_values = []
            rpm_pct_values = []

            # Bright lines that pop on dark blue
            line_load, = ax.plot([], [], label="Spindle Load (%)", color="#00E5FF")
            line_feed, = ax.plot([], [], label="Feed (% of base)", color="#FFD54F")
            line_rpm,  = ax.plot([], [], label="RPM (% of base)",  color="#FF4081")

            legend = ax.legend(loc="upper left")
            legend.get_frame().set_facecolor(bg_color)
            legend.get_frame().set_edgecolor(text_color)
            for text in legend.get_texts():
                text.set_color(text_color)

            ax.set_xlabel("Time (s)")
            ax.set_ylabel("Percent")
            ax.set_ylim(0, 110)

        # ---------- Main sim loop ----------
        while True:
            if time.time() - start_time > max_total_time:
                break

            is_overload_cycle = (cycle_index == overload_cycle)
            print(f"T9 SIM | Starting cycle {cycle_index} "
                  f"{'(overload)' if is_overload_cycle else ''}")

            over_threshold = False

            for step in range(steps_per_cycle):
                now = time.time() - start_time

                if is_overload_cycle:
                    # --- Overload cycle ---
                    if not over_threshold:
                        # Trend up aggressively until we cross 70%
                        increment = random.uniform(0.8, 1.6)
                        spindle_load += increment

                        feed_rate = base_feed
                        spindle_speed = base_rpm

                        if spindle_load >= 70.0:
                            over_threshold = True
                    else:
                        # Over 70%: reduce feed & RPM, load drops
                        feed_rate -= random.uniform(1.0, 3.0)
                        spindle_speed -= random.uniform(80.0, 200.0)
                        spindle_load -= random.uniform(1.0, 2.0)

                        feed_rate = max(feed_rate, base_feed * 0.6)
                        spindle_speed = max(spindle_speed, base_rpm * 0.7)

                        if spindle_load <= 55.0:
                            spindle_load = random.uniform(53.0, 57.0)
                else:
                    # --- Normal cycles (<70%) ---
                    half = steps_per_cycle // 2

                    if step < half:
                        # Ramp up into the 60s
                        target_peak = min(60.0 + cycle_index * 1.5, 68.0)
                        spindle_load += (target_peak - spindle_load) * 0.35
                    else:
                        # Ramp back down toward rising baseline
                        baseline = 40.0 + cycle_index * 1.0
                        spindle_load += (baseline - spindle_load) * 0.35

                    feed_rate = base_feed
                    spindle_speed = base_rpm

                spindle_load = max(0.0, min(spindle_load, 100.0))

                # ---------- Debug print ----------
                print(
                    f"  Cycle {cycle_index:02d} Step {step:02d} | "
                    f"Load: {spindle_load:6.2f}% | "
                    f"Feed: {feed_rate:7.2f} | "
                    f"RPM: {spindle_speed:7.0f}"
                )

                spindle_load_samples.append(spindle_load)

                # ---------- Push values into FlexxCore ----------
                self.client.set_variable_latest_value(
                    device_id=self.cnc_id, variable_name="550", value=round(spindle_load, 3)
                )
                self.client.analog_variable_event(
                    device_id=self.cnc_id,
                    part_idx=part_idx,
                    variable_name="550",
                    variable_value=round(spindle_load, 3),
                )

                self.client.set_variable_latest_value(
                    device_id=self.cnc_id, variable_name="551", value=round(feed_rate, 3)
                )
                self.client.analog_variable_event(
                    device_id=self.cnc_id,
                    part_idx=part_idx,
                    variable_name="551",
                    variable_value=round(feed_rate, 3),
                )

                self.client.set_variable_latest_value(
                    device_id=self.cnc_id, variable_name="552", value=round(spindle_speed, 3)
                )
                self.client.analog_variable_event(
                    device_id=self.cnc_id,
                    part_idx=part_idx,
                    variable_name="552",
                    variable_value=round(spindle_speed, 3),
                )

                # ---------- Realtime graph update / timing ----------
                if show_graph and fig is not None and ax is not None:
                    t_values.append(now)
                    load_values.append(spindle_load)
                    feed_pct_values.append(feed_rate / base_feed * 100.0)
                    rpm_pct_values.append(spindle_speed / base_rpm * 100.0)

                    line_load.set_data(t_values, load_values)
                    line_feed.set_data(t_values, feed_pct_values)
                    line_rpm.set_data(t_values, rpm_pct_values)

                    ax.set_xlim(0, max(5.0, now))
                    ax.set_ylim(0, 110)

                    fig.canvas.draw_idle()
                    plt.pause(step_dt)  # sleep + process GUI events
                else:
                    time.sleep(step_dt)

                # If overload cycle and we've recovered, end sim
                if is_overload_cycle and over_threshold and spindle_load <= 57.0:
                    if show_graph and fig is not None:
                        plt.ioff()
                        plt.close(fig)
                    return

            cycle_index += 1
            if cycle_index > overload_cycle:
                break

        if show_graph and fig is not None:
            plt.ioff()
            plt.close(fig)
        
        peak_spindle_load = round(max(spindle_load_samples), 3)
        avg_spindle_load = round(sum(spindle_load_samples) / len(spindle_load_samples), 3)
        
        return peak_spindle_load, avg_spindle_load, feed_rate, spindle_speed



# -----------------------
# Entry Point
# -----------------------

if __name__ == "__main__":
    workflow = ModigToolingWorkflow()
    workflow.run()