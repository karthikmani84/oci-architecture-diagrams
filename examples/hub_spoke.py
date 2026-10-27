"""Hub-and-spoke example. Run from the repo root with OCI_TOOLKIT set."""
import os, sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "oci-architecture-diagrams", "scripts"))
from ocidraw import Diagram

out = os.path.dirname(os.path.abspath(__file__))
d = Diagram(title="Hub-spoke")
d.text("Example - OCI hub-and-spoke landing zone", 0, 0, 700, 24, size=16, bold=True)
op = d.group("onprem", "On-Premises DC", 0, 40, 180, 360)
cpe = d.icon("cpe", 90, 180, parent=op, label="Edge router\nASN 65001")
reg = d.group("region", "OCI Region - UAE East (Dubai)", 240, 40, 760, 360)
drg = d.icon("drg", 60, 180, parent=reg, label="DRG", label_pos="top")
hub = d.group("vcn", "hub-vcn\n10.0.0.0/24", 140, 40, 280, 290, parent=reg)
fw_sn = d.group("subnet", "fw subnet\n10.0.0.0/26", 20, 60, 240, 150, parent=hub)
fw = d.icon("firewall", 120, 85, parent=fw_sn, label="OCI Network Firewall")
spoke = d.group("vcn", "app-vcn\n10.1.0.0/16", 450, 40, 290, 290, parent=reg)
app_sn = d.group("subnet", "app subnet (private)\n10.1.1.0/24", 20, 60, 250, 150, parent=spoke)
vm = d.icon("vm", 70, 85, parent=app_sn, label="App VMs")
db = d.icon("atp", 180, 85, parent=app_sn, label="ATP")
d.connect(cpe, drg, kind="fastconnect", label="FastConnect 2x10 Gbps")
d.connect(drg, fw, both=True)
d.connect(fw, vm, both=True)
d.connect(vm, db, label="1522")
print(d.check())
d.save(os.path.join(out, "hub-spoke.drawio"))
print(d.render(os.path.join(out, "hub-spoke.png")))
