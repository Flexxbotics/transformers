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
class IndexCarouselWorkflowApp:
    def __init__(self):
        self.gui = FlexxGUI()
        self.client = FlexxCoreClient(flask_port=7081)
        self.progress_bar = None

        self.robot_id = "688c5fb834dd9e275c2674a7"
        self.torque_controller_id = "68a1f8958ee549d814213745"
        self.wago_id = "68921f6094bc3d988cb889c1"
        self.carousel_id = "68a46f6a1fcd229d3442fc8f"

    def main_entry_menu(self):
        self.container = self.gui.create_centered_container()
        self.select_index_label = self.gui.create_label(text="SELECT INDEX", parent=self.container)
        self.index_drop_down = self.gui.create_dropdown(options=["0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "12", "13", "14"], parent=self.container)
        self.spacing_label = self.gui.create_label(text="", parent=self.container)
        self.index_carousel = self.gui.create_button("Index Carousel", "#25BC9F",
                                                          command=self.index_carousel, parent=self.container)
        self.abort_btn = self.gui.create_button("Abort", "#FF0000", command=self.on_complete_workflow,
                                                parent=self.container)
        self.gui.start()

    def index_carousel(self):

        index = self.index_drop_down.get()
        print ("Index: " + index)
        print("Indexing carousel...")
        args = {"index": int(index)}
        self.client.execute_command(device_id=self.carousel_id, command_name="set_carousel_index", args=args)


    def on_complete_workflow(self):
        self.gui.close()


# -----------------------
# Entry Point
# -----------------------
if __name__ == "__main__":
    app = IndexCarouselWorkflowApp()
    app.main_entry_menu()
