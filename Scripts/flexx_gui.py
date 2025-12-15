import ttkbootstrap as ttk
from ttkbootstrap.constants import *
import tkinter.font as tkfont

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

        default_font = tkfont.nametofont("TkDefaultFont")
        default_font.configure(family="Roboto", size=10)
        self.root.option_add("*Font", "Roboto 10")

        style = ttk.Style()
        style.configure("TFrame", background="#132231")
        style.configure("TLabel", background="#132231", foreground="white")

    def _setup_frames(self):
        # Remove old frames if they exist (safeguard)
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

    def start(self):
        self.root.deiconify()  # Make sure the window is visible
        self.root.mainloop()

    def close(self):
        self.root.destroy()  # Destroys window
