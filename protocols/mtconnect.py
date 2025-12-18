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
from flask import current_app, g
import requests
import xml.etree.ElementTree as ET

class MTConnect():

    def __init__(self, ip_address, port, path):
        """
        Template device class.

        :param ip address:
                    the ip address of the target
        :param port:
                    the port of the target
        :param path:
                    the path of the target

        :return:    a new instance
        """
        self.address = ip_address
        self.port = port
        self.path = path

        self.url = "http://" + str(self.address) + ":" + str(self.port) + self.path
        self._logger = current_app.config["logger"]

    def __del__(self):
        pass

    def _get_data(self):
        r = requests.get(self.url, timeout=10)
        r.raise_for_status()
        b = r.content
        # find likely start of XML
        start = b.find(b'<?xml')
        if start == -1:
            start = b.find(b'<MTConnectStreams')
        if start == -1:
            start = b.find(b'<')
        xml_bytes = b[start:] if start != -1 else b
        return ET.ElementTree(ET.fromstring(xml_bytes))

    def read_tag(self, component_stream=None, sub_stream_type=None, tag=None):
        """
        Find elements by their dataItemId (e.g. 'aalarms' or 'ncprog') or by tag name as a fallback.
        Returns a list of dicts with tag, text, and attrib.
        """
        tree = self._get_data()
        root = tree.getroot() if isinstance(tree, ET.ElementTree) else tree
        if tag is None:
            return []

        tag_lower = tag.lower()
        results = []

        # choose scope
        if component_stream:
            comp_nodes = []
            for cs in root.iter():
                local = cs.tag.split('}')[-1]
                if local == 'ComponentStream':
                    if any(component_stream.lower() == cs.attrib.get(k, '').lower()
                           for k in ('component', 'componentId', 'name')):
                        comp_nodes.append(cs)
            search_parents = comp_nodes
            if not comp_nodes:
                return []
        else:
            search_parents = [root]

        for parent in search_parents:
            for elem in parent.iter():
                local_tag = elem.tag.split('}')[-1]
                dataitem = elem.attrib.get('dataItemId') or elem.attrib.get('id') or elem.attrib.get('name')
                if dataitem and dataitem.lower() == tag_lower:
                    alarm_details = []
                    # look for nested <Alarm> elements inside this message
                    for alarm_elem in elem.iter():
                        if alarm_elem.tag.split('}')[-1] == "Alarm":
                            alarm_details.append({
                                "alarmNumber": alarm_elem.attrib.get("alarmNumber"),
                                "timestamp": alarm_elem.attrib.get("timestamp"),
                                "text": (alarm_elem.text or "").strip()
                            })

                    results.append({
                        "matched_by": "dataItemId",
                        "tag": local_tag,
                        "text": (elem.text or "").strip(),
                        "attrib": dict(elem.attrib),
                        "alarms": alarm_details
                    })

        return results

# if __name__ == "__main__":
#     client = MTConnect(ip_address="192.168.0.181", port=8082, path="/current")
#     alarm_status = client.read_tag(tag="aalarms")
#     print ("Alarm: ")
#     print (alarm_status[0]["text"])
#     active_program = client.read_tag(tag="ncprog")
#     print ("Program: ")
#     print (active_program[0]["text"])
#     spinde_speed = client.read_tag(tag="sspeed")
#     print ("Spindle Speed:")
#     print (float(spinde_speed[0]["text"]))

