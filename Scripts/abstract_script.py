from abc import ABC
# from flask import abort, current_app

# Tell python to search for imports in the parent directory of this script
import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

from data.scripts.flexx_gui import FlexxGUI


class AbstractScript(ABC):
    """
    Each class that extends AbstractScript is presumed to implement the methods herein to create a
    valid script.
    """

    # def __init__(self, current_app):
    #     self.current_app = current_app
    #     self.gui = FlexxGUI()
    #
    #     self.device_service: DeviceService = self.current_app.config["device_service"]
    #     self.variable_service: VariableService = self.current_app.config["variable_service"]
    #     self.run_record_service: RunRecordService = self.current_app.config["run_record_service"]

    def __init__(self):
        self.gui = FlexxGUI()