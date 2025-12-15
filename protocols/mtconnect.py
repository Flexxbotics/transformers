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

