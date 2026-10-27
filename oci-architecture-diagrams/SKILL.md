---
name: oci-architecture-diagrams
description: Draw OCI architecture diagrams (physical or logical) as editable draw.io files using Oracle's official OCI Architecture Diagram Toolkit and style guide, then render a PNG to check. Use for any OCI topology, network or solution diagram.
---

# OCI architecture diagrams (Oracle toolkit, draw.io)

Output is always an **editable `.drawio` file** built from Oracle's *OCI Architecture Diagram Toolkit v24.2* icons, plus a **PNG render**. Never hand-draw icons, never invent an icon, never use generic cloud clip-art.

## 1. Tools

This skill folder contains:
- `scripts/ocidraw.py`: the diagram builder
- `toolkit/`: Oracle's toolkit files. They are **not** shipped with this skill; the user downloads them from Oracle (see `toolkit/README.md`).

Find the skill folder (the directory holding this SKILL.md, often `/mnt/skills/user/oci-architecture-diagrams/`) and:
```bash
SKILL=/mnt/skills/user/oci-architecture-diagrams   # adjust if different
export OCI_TOOLKIT=$SKILL/toolkit
python3 $SKILL/scripts/ocidraw.py icons drg        # list/search icon names
```
In Python: `import sys; sys.path.insert(0, f"{SKILL}/scripts"); from ocidraw import Diagram`.

**If the toolkit is missing** (`FileNotFoundError: OCI toolkit not found`), check the conversation uploads for Oracle's "OCI Style Guide for Drawio" zip or the toolkit `.drawio` file; `ocidraw` finds them automatically. If there is none, stop and ask the user to attach it, linking https://docs.oracle.com/en-us/iaas/Content/General/Reference/graphicsfordiagrams.htm. Do not fall back to other icons.

PNG rendering needs the draw.io desktop CLI. If `render()` reports it missing, install it (Linux: the drawio-desktop `.deb` from GitHub releases, plus `xvfb`) or deliver the `.drawio` only and say so.

## 2. API

```python
d = Diagram(title="Hub-spoke")
d.text("Customer X - OCI landing zone", 0, 0, 700, 24, size=16, bold=True)
op  = d.group("onprem", "On-Premises DC", 0, 40, 180, 360)
cpe = d.icon("cpe", 90, 180, parent=op, label="Edge router\nASN 65001")
reg = d.group("region", "OCI Region - UAE East (Dubai)", 240, 40, 760, 360)
drg = d.icon("drg", 60, 180, parent=reg, label="DRG", label_pos="top")
hub = d.group("vcn", "hub-vcn\n10.0.0.0/24", 140, 40, 280, 290, parent=reg)
fw_sn = d.group("subnet", "fw subnet\n10.0.0.0/26", 20, 60, 240, 150, parent=hub)
fw  = d.icon("firewall", 120, 85, parent=fw_sn, label="OCI Network Firewall")
d.connect(cpe, drg, kind="fastconnect", label="FastConnect 2x10 Gbps")
d.connect(drg, fw, both=True)
print(d.check()); d.save("hub.drawio"); d.render("hub.png")
```

- `group(kind, label, x, y, w, h, parent=None)`: x,y = top-left relative to the parent. Kinds: `region, realm, ad, fd, vcn, subnet, compartment, tenancy, onprem, internet, thirdparty, osn, tier, usergroup, other, component, atomic, onprem_component, thirdparty_component, optional, note`. For `vcn`/`subnet`, put the CIDR on the second line.
- `icon(name, cx, cy, parent=None, label=None, size=None, border=False, label_pos="bottom")`: x,y = icon **centre**. Names resolve fuzzily plus aliases (`drg, igw, nat, sgw, lpg, adb, atp, exadata, oke, waf, vault, bastion, lb, fss`...). `border=True` for gateways sitting on a VCN edge. Move `label_pos` away from where connectors attach.
- `box(label, x, y, w, h, kind="component", html_label=False)`: logical component boxes and notes.
- `connect(a, b, label="", kind="solid", both=False, waypoints=None)`: kinds `solid` (data/physical), `dashed` (user interaction), `fastconnect | vpn | rpc` (special connectors with badge), `plain`.
- `marker(n, cx, cy)`: numbered flow circle. `text(...)`: free text.
- `check()`: returns layout warnings (overlaps, icons outside groups, labels under headers). Fix every warning before delivering.

## 3. Style rules

- Nest properly: realm/region > AD/FD (only if relevant) > VCN > subnet > resources. On-premises, internet and third parties sit outside the region.
- Gateways (IGW, NAT, SGW, DRG, LPG) sit on or next to the VCN edge, not inside subnets.
- Services with **no Oracle icon** (OCVS/VMware, Veeam, Palo Alto, Fortinet and similar) are drawn as labelled `component` / `thirdparty_component` boxes. Never use a stand-in icon.
- Optional or TBC parts use the `optional` (dashed) group.
- Missing CIDRs or regions are drawn as marked placeholders (e.g. `10.x.x.x/24 (TBC)`), never invented as if real.
- Keep left-to-right flow: users/on-prem on the left, OCI in the middle, Oracle Services Network or third parties on the right.
- Physical diagrams show VCNs/subnets; logical diagrams ("logical diagram" requested) show capability boxes and numbered flows.

## 4. Workflow

1. Restate the topology briefly; list anything assumed.
2. Write one Python script that builds the diagram. Run `check()` and fix warnings.
3. Render the PNG and **look at it** before delivering; fix overlaps, cramped labels and crossing lines.

## 5. Delivering

- Deliver the `.drawio` and the `.png` together. Say in one line what the diagram shows and list any assumptions or placeholders (CIDRs, regions) so the user can correct them.
- For revisions, edit the same script and re-render. Don't patch the XML by hand.
- If the user wants it in PowerPoint, embed the PNG. The `.drawio` remains the editable source.
