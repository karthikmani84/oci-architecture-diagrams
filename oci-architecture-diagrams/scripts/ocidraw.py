#!/usr/bin/env python3
"""ocidraw - build Oracle-style OCI architecture diagrams (.drawio) from the
official "OCI Style Guide for Drawio" toolkit.

Usage (Python):
    from ocidraw import Diagram
    d = Diagram()                                   # finds the toolkit automatically
    reg = d.group("region", "OCI Region - UAE East (Dubai)", 200, 40, 900, 600)
    vcn = d.group("vcn", "Prod VCN\n10.0.0.0/16", 30, 50, 840, 520, parent=reg)
    sn  = d.group("subnet", "App Subnet (private)\n10.0.1.0/24", 30, 60, 380, 200, parent=vcn)
    vm  = d.icon("Virtual Machine", 100, 110, parent=sn, label="App VM")   # x,y = icon CENTRE
    db  = d.icon("Autonomous Database", 280, 110, parent=sn, label="ATP")
    d.connect(vm, db, label="SQL*Net 1522")
    print(d.check())                                 # layout warnings
    d.save("out.drawio"); d.render("out.png")

CLI:
    python ocidraw.py icons [query]      list icon names (fuzzy filter)
    python ocidraw.py catalog out.drawio render every icon on one sheet
"""
import base64, collections, copy, glob, html, json, os, re, shutil, subprocess, sys
import urllib.parse, zipfile, zlib
import xml.etree.ElementTree as ET

# ----------------------------------------------------------------------------
# Toolkit loading
# ----------------------------------------------------------------------------
TOOLKIT_GLOBS = ["**/*OCI*Style*Guide*Drawio*.zip", "**/*OCI*Style*Guide*.zip",
                 "**/OCI Architecture Diagram Toolkit*.drawio", "**/*Diagram*Toolkit*.drawio",
                 "**/OCI Library.xml", "**/oci-toolkit*.xml", "**/oci-toolkit*.drawio"]
_HERE = os.path.dirname(os.path.abspath(__file__))
SEARCH_ROOTS = [os.path.join(_HERE, "..", "toolkit"), _HERE, os.path.join(_HERE, ".."), ".", "/mnt/skills", "/mnt/user-data/uploads", "/mnt/data", "/mnt/user-data", "/root/.claude/uploads",
                os.path.expanduser("~"), "/tmp"]
CACHE = os.path.expanduser("~/.cache/ocidraw/icons_v1.json")


def _inflate(s):
    return urllib.parse.unquote(zlib.decompress(base64.b64decode(s), -15).decode("utf-8"))


def _pages(drawio_text):
    """{page name: mxGraphModel xml string}"""
    out = {}
    for m in re.finditer(r"<diagram ([^>]*)>(.*?)</diagram>", drawio_text, re.S):
        nm = re.search(r'name="([^"]*)"', m.group(1))
        body = m.group(2).strip()
        out[nm.group(1) if nm else str(len(out))] = body if body.startswith("<") else _inflate(body)
    return out


def _plain(v):
    if not v:
        return ""
    v = re.sub(r"<br\s*/?>", " ", v)
    v = re.sub(r"<[^>]+>", "", v)
    v = html.unescape(v).replace("\xa0", " ")
    return re.sub(r"\s+", " ", v).strip()


def _nodes(model_xml):
    root = ET.fromstring(model_xml).find("root")
    nodes = []
    for ch in root:
        if ch.tag == "mxCell":
            cell, label = ch, ch.get("value")
        else:  # UserObject / object wrappers
            cell, label = ch.find("mxCell"), ch.get("label")
            if cell is None:
                continue
        nodes.append(dict(id=ch.get("id"), parent=cell.get("parent"), label=label or "",
                          style=cell.get("style") or "", cell=cell,
                          vertex=cell.get("vertex") == "1", edge=cell.get("edge") == "1"))
    return nodes


def _geom(cell):
    g = cell.find("mxGeometry")
    if g is None:
        return 0.0, 0.0, 0.0, 0.0
    f = lambda k: float(g.get(k) or 0)
    return f("x"), f("y"), f("width"), f("height")


def _find_toolkit(path=None):
    cands = []
    if path:
        cands.append(path)
    if os.environ.get("OCI_TOOLKIT"):
        cands.append(os.environ["OCI_TOOLKIT"])
    for r in SEARCH_ROOTS:
        if not os.path.isdir(r):
            continue
        for g in TOOLKIT_GLOBS:
            try:
                cands += sorted(glob.glob(os.path.join(r, g), recursive=True))
            except Exception:
                pass
    for c in cands:
        if os.path.exists(c):
            return c
    raise FileNotFoundError(
        "OCI toolkit not found. Provide the 'OCI Style Guide for Drawio' zip, the "
        "'OCI Architecture Diagram Toolkit v24.x.drawio' file, or set OCI_TOOLKIT=/path.")


def _read_sources(path):
    """Return (toolkit_drawio_text or None, library_xml_text or None) from a zip/dir/file."""
    tk = lib = None
    files = {}
    if os.path.isdir(path):
        for f in glob.glob(os.path.join(path, "**/*"), recursive=True):
            if os.path.isfile(f):
                files[f] = lambda f=f: open(f, encoding="utf-8").read()
    elif path.lower().endswith(".zip"):
        z = zipfile.ZipFile(path)
        for n in z.namelist():
            files[n] = lambda n=n: z.read(n).decode("utf-8")
        # also look beside the zip for loose files
    else:
        files[path] = lambda: open(path, encoding="utf-8").read()
        d = os.path.dirname(os.path.abspath(path))
        for f in glob.glob(os.path.join(d, "*")):
            if f != path and os.path.isfile(f) and (f.endswith(".xml") or f.endswith(".drawio")):
                files[f] = lambda f=f: open(f, encoding="utf-8").read()
    for name, rd in files.items():
        b = os.path.basename(name).lower()
        if b.endswith(".drawio") and "toolkit" in b and tk is None:
            tk = rd()
        elif b.endswith(".xml") or b.endswith(".drawio"):
            txt = rd()
            if txt.lstrip().startswith("<mxlibrary") and lib is None:
                lib = txt
            elif tk is None and 'name="Icons"' in txt:
                tk = txt
    return tk, lib


def _extract_icons(model_xml):
    """Split the toolkit 'Icons' page into individual icons -> {name: {w,h,cells:[(style,x,y,w,h)]}}"""
    nodes = _nodes(model_xml)
    by = collections.defaultdict(list)
    m = {}
    for n in nodes:
        by[n["parent"]].append(n)
        m[n["id"]] = n
    layers = [n["id"] for n in nodes if n["parent"] == "0"] or ["1"]

    absxy = {}
    def ab(nid):
        if nid in absxy:
            return absxy[nid]
        n = m[nid]
        x, y, w, h = _geom(n["cell"])
        p = n["parent"]
        if p in m and p not in layers and m[p]["vertex"]:
            px, py = ab(p)
            x, y = x + px, y + py
        absxy[nid] = (x, y)
        return x, y

    def drawable(n):
        s = n["style"]
        if not n["vertex"] or s.startswith("group") or s.startswith("text;"):
            return False
        return "shape=" in s or "fillColor=none" not in s or ("strokeColor=none" not in s)

    items = []   # dict(glyphs=[...], labels=[(y,x,text)], bbox)
    orphans = []
    for lay in layers:
        for top in by[lay]:
            glyphs, labels, st = [], [], [top["id"]]
            while st:
                nid = st.pop(0)
                n = m[nid]
                x, y = ab(nid)
                _, _, w, h = _geom(n["cell"])
                t = _plain(n["label"])
                if t:
                    labels.append((y, x, t, n["style"]))
                elif drawable(n) and w > 0 and h > 0:
                    glyphs.append((n["style"], x, y, w, h))
                st[0:0] = [k["id"] for k in by[nid]]
            if glyphs:
                xs = [g[1] for g in glyphs]; ys = [g[2] for g in glyphs]
                xe = [g[1] + g[3] for g in glyphs]; ye = [g[2] + g[4] for g in glyphs]
                bb = [min(xs), min(ys), max(xe), max(ye)]
                if bb[2] - bb[0] > 220 or bb[3] - bb[1] > 220:
                    continue  # backgrounds / screenshots
                items.append(dict(glyphs=glyphs, labels=[l for l in labels], bbox=bb))
            elif labels and not top["style"].startswith("text;"):
                orphans += labels
    # merge loose glyph fragments (no labels) that touch each other
    merged = True
    while merged:
        merged = False
        for i, a in enumerate(items):
            for j, b in enumerate(items):
                if i >= j or (a["labels"] and b["labels"]):
                    continue
                A, B = a["bbox"], b["bbox"]
                if A[0] <= B[2] + 2 and B[0] <= A[2] + 2 and A[1] <= B[3] + 2 and B[1] <= A[3] + 2:
                    a["glyphs"] += b["glyphs"]; a["labels"] += b["labels"]
                    a["bbox"] = [min(A[0], B[0]), min(A[1], B[1]), max(A[2], B[2]), max(A[3], B[3])]
                    items.pop(j); merged = True
                    break
            if merged:
                break
    # attach orphan label fragments to the nearest glyph above them
    for (y, x, t, s) in orphans:
        best, bd = None, 1e9
        for it in items:
            bb = it["bbox"]
            cx = (bb[0] + bb[2]) / 2
            lab_bottom = max([l[0] for l in it["labels"]] + [bb[3]])
            dy = y - lab_bottom
            if -5 <= dy <= 45 and abs(cx - (x + 50)) < 90:
                d = dy + abs(cx - (x + 50)) / 3
                if d < bd:
                    best, bd = it, d
        if best:
            best["labels"].append((y, x, t, s))
    icons = {}
    for it in items:
        if not it["labels"]:
            continue
        # glyph bbox only from glyph cells above the text
        labs = sorted(it["labels"], key=lambda l: (round(l[0]), l[1]))
        name = " ".join(l[2] for l in labs)
        name = re.sub(r"\s+", " ", name.replace("Managment", "Management")).strip()
        bb = it["bbox"]
        cells = [(s, round(x - bb[0], 2), round(y - bb[1], 2), round(w, 2), round(h, 2))
                 for (s, x, y, w, h) in it["glyphs"]]
        base, k = name, 2
        while name in icons:
            name = f"{base} {k}"; k += 1
        icons[name] = dict(w=round(bb[2] - bb[0], 2), h=round(bb[3] - bb[1], 2), cells=cells)
    return icons


def _extract_badges(lib_text):
    """Small images used on VCN/subnet borders and on special connectors."""
    arr = json.loads(lib_text[lib_text.index("["): lib_text.rindex("]") + 1])
    want = {"Physical - Grouping - VCN": "vcn", "Physical - Grouping - Subnet": "subnet",
            "Physical - Special Connectors - FastConnect - Horizontal": "fastconnect",
            "Physical - Special Connectors - Site-to-site-VPN - Vertical": "vpn",
            "Physical - Special Connectors - Remote Peering - Horizontal": "rpc"}
    out = {}
    for e in arr:
        key = want.get(e.get("title"))
        if not key:
            continue
        x = e["xml"]
        x = x if x.startswith("<") else _inflate(x)
        imgs = []
        for n in _nodes(x):
            if "shape=image" in n["style"]:
                _, _, w, h = _geom(n["cell"])
                imgs.append((n["style"], w, h))
        out[key] = imgs
    return out


_ICON_CACHE = {}


def load_toolkit(path=None):
    path = _find_toolkit(path)
    if path in _ICON_CACHE:
        return _ICON_CACHE[path]
    key = f"{os.path.abspath(path)}:{os.path.getsize(path)}"
    try:
        c = json.load(open(CACHE))
        if c.get("key") == key:
            _ICON_CACHE[path] = c
            return c
    except Exception:
        pass
    tk, lib = _read_sources(path)
    icons, badges = {}, {}
    if tk:
        pages = _pages(tk)
        icon_page = pages.get("Icons")
        if icon_page:
            icons = _extract_icons(icon_page)
    if lib:
        badges = _extract_badges(lib)
        if not icons:  # older toolkit: icons only in the library
            icons = _icons_from_library(lib)
    if not icons:
        raise RuntimeError(f"No icons found in {path}")
    data = dict(key=key, source=path, icons=icons, badges=badges)
    try:
        os.makedirs(os.path.dirname(CACHE), exist_ok=True)
        json.dump(data, open(CACHE, "w"))
    except Exception:
        pass
    _ICON_CACHE[path] = data
    return data


def _icons_from_library(lib_text):
    arr = json.loads(lib_text[lib_text.index("["): lib_text.rindex("]") + 1])
    icons = {}
    for e in arr:
        t = _plain(e.get("title") or "")
        if not t or t.startswith(("Logical", "Physical")) or t == "None":
            continue
        name = t.split(" - ", 1)[-1]
        x = e["xml"]; x = x if x.startswith("<") else _inflate(x)
        model = "<mxGraphModel>" + x[x.index("<root>"):]
        sub = _extract_icons(model)
        if sub:
            v = list(sub.values())[0]
            icons[name] = v
    return icons


# ----------------------------------------------------------------------------
# Name resolution
# ----------------------------------------------------------------------------
ALIASES = {
    # compute
    "vm": "Virtual Machine", "compute": "OCI Compute", "instance": "Virtual Machine",
    "bare metal": "OCI Compute", "bm": "OCI Compute", "gpu": "Virtual Machine GPU",
    "instance pool": "Instance Pools", "functions": "OCI Functions", "fn": "OCI Functions",
    # storage
    "block volume": "Block Storage", "block storage": "Block Storage", "boot volume": "Block Storage",
    "object storage": "OCI Object Storage", "bucket": "OCI Object Storage Buckets",
    "buckets": "OCI Object Storage Buckets", "fss": "File Storage", "file storage": "File Storage",
    "mount target": "File Storage Mount Target", "backup": "Back Up/ Restore",
    # network
    "lb": "Load Balancer", "load balancer": "Load Balancer", "flexible load balancer": "Flexible Load Balancer",
    "nlb": "Load Balancer", "network load balancer": "Load Balancer",
    "drg": "DRG", "dynamic routing gateway": "DRG", "igw": "Internet Gateway", "internet gateway": "Internet Gateway",
    "nat": "NAT Gateway", "natgw": "NAT Gateway", "nat gateway": "NAT Gateway",
    "sgw": "Service Gateway", "service gateway": "Service Gateway", "lpg": "Local Peering Gateway",
    "local peering gateway": "Local Peering Gateway", "cpe": "Customer Premises Equipment",
    "router": "Customer Premises Equipment", "on-premises": "On-premises data center",
    "on premises": "On-premises data center", "datacenter": "On-premises data center",
    "data center": "On-premises data center", "vcn": "VCN (Region Identifier)",
    "route table": "Route Table", "switch": "Network Switch", "osn": "Oracle Services Network",
    "oracle services network": "Oracle Services Network",
    # db
    "adb": "Oracle Autonomous Database", "autonomous database": "Oracle Autonomous Database",
    "atp": "Oracle Autonomous Transaction Processing ATP", "adw": "Oracle Autonomous Data Warehouse",
    "exadata": "Oracle Exadata Database Service", "exacs": "Oracle Exadata Database Service",
    "exacc": "Exadata Cloud@Customer", "base database": "Oracle Base Database", "dbcs": "Oracle Base Database",
    "db system": "Oracle Base Database", "mysql": "MySQL Database System", "heatwave": "MySQL HeatWave",
    "postgresql": "OCI Database with PostgreSQL", "postgres": "OCI Database with PostgreSQL",
    "goldengate": "GoldenGate", "data guard": "Data Guard", "nosql": "NoSQL Database",
    "zdlra": "Zero Data Loss Recovery Appliance", "opensearch": "OpenSearch",
    # dev / containers
    "oke": "OCI Container Engine for Kubernetes", "kubernetes": "OCI Container Engine for Kubernetes",
    "ocir": "OCI Container Registry", "container registry": "OCI Container Registry",
    "api gateway": "API Gateway", "apigw": "API Gateway", "oic": "Integration", "integration": "Integration",
    "container instances": "Container Instances", "devops": "DevOps", "resource manager": "Resource Manager",
    "terraform": "Resource Manager",
    # security
    "waf": "WAF", "firewall": "Firewall", "network firewall": "Firewall", "vault": "Vault", "kms": "Key Management",
    "bastion": "Bastion", "cloud guard": "Cloud Guard", "iam": "IAM", "nsg": "NSG",
    "security list": "Security Lists", "identity domain": "Oracle Identity Cloud Service",
    "idcs": "Oracle Identity Cloud Service", "user": "User", "users": "User Group", "certificates": "Certificates",
    "ad": "Active Directory", "active directory": "Active Directory", "ddos": "DDoS Protection",
    # o&m
    "logging": "Logging", "monitoring": "Monitoring", "alarms": "Alarms", "events": "Events",
    "notifications": "Notifications", "apm": "Application Performance Monitoring", "audit": "Auditing",
    "flow logs": "VCN Flow Logs", "fsdr": "Fullstack Disaster Recovery", "full stack dr": "Fullstack Disaster Recovery",
    "streaming": "OCI Streaming", "connector hub": "Service Connector Hub", "queue": "OCI Queue",
    # other
    "genai": "OCI Generative AI", "generative ai": "OCI Generative AI", "oac": "Oracle Analytics Cloud",
    "analytics": "Oracle Analytics Cloud", "data science": "OCI Data Science", "internet": "Internet",
    "multicloud": "OCI Multicloud", "dedicated region": "Dedicated Region", "cdn": "Content Delivery Network Service",
    "dns": "DNS", "ebs": "E-Business Suite", "apex": "APEX", "marketplace": "Marketplace",
}


def _norm(s):
    return re.sub(r"[^a-z0-9@]+", " ", s.lower()).strip()


def resolve(name, icons):
    if name in icons:
        return name
    n = _norm(name)
    for k in icons:
        if _norm(k) == n:
            return k
    if n in ALIASES and ALIASES[n] in icons:
        return ALIASES[n]
    # token match: all query tokens contained in icon name
    toks = n.split()
    hits = [k for k in icons if all(t in _norm(k).split() or t in _norm(k) for t in toks)]
    if hits:
        return sorted(hits, key=len)[0]
    raise KeyError(f"No OCI icon matches '{name}'. Try: python ocidraw.py icons {toks[0] if toks else ''}")


# ----------------------------------------------------------------------------
# Styles (from the Oracle toolkit, Physical & Logical pages)
# ----------------------------------------------------------------------------
FONT = "fontFamily=Oracle Sans,Arial,Helvetica,sans-serif;"
INK = "#312D2A"
GROUP_STYLES = {
    "tenancy": "whiteSpace=wrap;html=1;dashed=1;align=left;verticalAlign=top;fillColor=none;fontColor=#312D2A;strokeColor=#9E9892;fontSize=12;spacingLeft=5;fontStyle=1;",
    "compartment": "whiteSpace=wrap;html=1;dashed=1;dashPattern=1 1;align=left;verticalAlign=top;fillColor=none;fontColor=#AE562C;strokeColor=#AE562C;fontSize=12;spacingLeft=5;fontStyle=1;",
    "region": "whiteSpace=wrap;html=1;align=left;verticalAlign=top;fillColor=#F5F4F2;rounded=1;arcSize=2;absoluteArcSize=0;strokeColor=#9E9892;fontColor=#312D2A;fontSize=12;fontStyle=1;spacingLeft=6;",
    "realm": "whiteSpace=wrap;html=1;align=left;verticalAlign=top;fillColor=#E4E1DD;rounded=1;arcSize=2;strokeColor=#9E9892;fontColor=#312D2A;fontSize=12;fontStyle=1;spacingLeft=6;",
    "ad": "whiteSpace=wrap;html=1;align=center;verticalAlign=top;rounded=1;arcSize=3;fillColor=#DFDCD8;strokeColor=#9E9892;fontColor=#312D2A;fontSize=12;fontStyle=1;",
    "fd": "whiteSpace=wrap;html=1;align=center;verticalAlign=top;rounded=1;arcSize=3;fillColor=#FCFBFA;strokeColor=#9E9892;fontColor=#312D2A;fontSize=12;fontStyle=1;",
    "vcn": "whiteSpace=wrap;html=1;strokeWidth=2;dashed=1;align=left;verticalAlign=top;fillColor=none;fontColor=#AE562C;strokeColor=#AE562C;fontSize=12;spacingLeft=5;spacingTop=2;",
    "subnet": "whiteSpace=wrap;html=1;strokeWidth=1;dashed=1;align=left;verticalAlign=top;fillColor=none;fontColor=#AE562C;strokeColor=#AE562C;fontSize=12;spacingLeft=5;",
    "osn": "whiteSpace=wrap;html=1;strokeWidth=2;dashed=1;align=left;verticalAlign=top;fillColor=none;fontColor=#312D2A;strokeColor=#6F6964;fontSize=12;spacingLeft=5;fontStyle=1;",
    "tier": "whiteSpace=wrap;html=1;dashed=1;dashPattern=1 1;align=center;verticalAlign=top;fillColor=none;fontColor=#312D2A;strokeColor=#9E9892;fontSize=12;",
    "onprem": "whiteSpace=wrap;html=1;rounded=1;arcSize=3;align=center;verticalAlign=top;fillColor=#F5F4F2;strokeColor=#9E9892;fontColor=#312D2A;fontSize=12;fontStyle=1;",
    "internet": "whiteSpace=wrap;html=1;rounded=1;arcSize=3;align=center;verticalAlign=top;fillColor=#F5F4F2;strokeColor=#9E9892;fontColor=#312D2A;fontSize=12;fontStyle=1;",
    "thirdparty": "whiteSpace=wrap;html=1;rounded=1;arcSize=3;align=center;verticalAlign=top;fillColor=#F5F4F2;strokeColor=#9E9892;fontColor=#312D2A;fontSize=12;fontStyle=1;",
    "usergroup": "whiteSpace=wrap;html=1;rounded=1;arcSize=3;align=center;verticalAlign=top;fillColor=#F5F4F2;strokeColor=#9E9892;fontColor=#312D2A;fontSize=11;",
    "other": "whiteSpace=wrap;html=1;rounded=1;arcSize=3;dashed=1;align=center;verticalAlign=top;fillColor=none;strokeColor=#312D2A;fontColor=#312D2A;fontSize=12;fontStyle=1;",
    # logical-diagram components
    "component": "whiteSpace=wrap;html=1;rounded=1;arcSize=10;fillColor=#FCFBFA;strokeColor=#759C6C;fontColor=#161513;fontSize=11;",
    "atomic": "whiteSpace=wrap;html=1;rounded=1;arcSize=10;fillColor=#759C6C;strokeColor=#759C6C;fontColor=#FCFBFA;fontSize=11;",
    "onprem_component": "whiteSpace=wrap;html=1;rounded=1;arcSize=10;fillColor=#FCFBFA;strokeColor=#C74634;fontColor=#161513;fontSize=11;",
    "thirdparty_component": "whiteSpace=wrap;html=1;rounded=1;arcSize=10;fillColor=#FCFBFA;strokeColor=#312D2A;fontColor=#161513;fontSize=11;",
    "optional": "whiteSpace=wrap;html=1;rounded=1;arcSize=10;dashed=1;fillColor=#FCFBFA;strokeColor=#9E9892;fontColor=#161513;fontSize=11;",
    "note": "whiteSpace=wrap;html=1;align=left;verticalAlign=top;fillColor=#FCFBFA;strokeColor=#9E9892;fontColor=#312D2A;fontSize=10;spacing=6;",
}
GROUP_ALIASES = {"on-premises": "onprem", "on_prem": "onprem", "oci region": "region", "availability domain": "ad",
                 "fault domain": "fd", "3rdparty": "thirdparty", "3rd party": "thirdparty",
                 "oracle services network": "osn", "metro": "realm"}
CONTAINER_TAIL = "container=1;collapsible=0;recursiveResize=0;" + FONT
EDGE_BASE = ("html=1;endArrow=open;endFill=0;startFill=0;rounded=0;endSize=6;startSize=6;strokeColor=#312D2A;"
             "fontSize=10;fontColor=#312D2A;labelBackgroundColor=#FFFFFF;" + FONT)
ICON_WRAP = ("html=1;fillColor=none;strokeColor=none;container=1;collapsible=0;recursiveResize=0;"
             "verticalLabelPosition=bottom;verticalAlign=top;align=center;labelPosition=center;"
             "whiteSpace=nowrap;fontSize=10;fontColor=#312D2A;spacingTop=-2;" + FONT)


LABEL_POS = {
    "right": "labelPosition=right;verticalLabelPosition=middle;align=left;verticalAlign=middle;spacingTop=0;spacingLeft=4;",
    "left": "labelPosition=left;verticalLabelPosition=middle;align=right;verticalAlign=middle;spacingTop=0;spacingRight=4;",
    "top": "verticalLabelPosition=top;verticalAlign=bottom;spacingTop=0;spacingBottom=2;",
}


def _esc(s):
    return html.escape(str(s), quote=True)


def _htmltext(s):
    return "<br>".join(html.escape(p) for p in str(s).split("\n"))


class Node:
    def __init__(self, d, id, kind, x, y, w, h, parent, label="", name=None):
        self.d, self.id, self.kind = d, id, kind
        self.x, self.y, self.w, self.h = x, y, w, h
        self.parent, self.label, self.name = parent, label, name

    @property
    def abs(self):
        x, y = self.x, self.y
        p = self.parent
        while p is not None:
            x, y = x + p.x, y + p.y
            p = p.parent
        return x, y

    def center(self):
        x, y = self.abs
        return x + self.w / 2, y + self.h / 2

    def __repr__(self):
        return f"<{self.kind} {self.label or self.name!r} @{self.abs} {self.w}x{self.h}>"


class Diagram:
    def __init__(self, toolkit=None, title=None):
        self.tk = load_toolkit(toolkit)
        self.icons = self.tk["icons"]
        self.badges = self.tk.get("badges", {})
        self.cells = []      # xml strings (vertices), emitted in order
        self.edges = []
        self.nodes = []
        self._n = 0
        self.title_text = title

    # ---- helpers
    def _id(self):
        self._n += 1
        return f"n{self._n}"

    def _pid(self, parent):
        return parent.id if parent is not None else "1"

    def _vertex(self, id, value, style, x, y, w, h, parent_id, extra=""):
        self.cells.append(
            f'<mxCell id="{id}" value="{_esc(value)}" style="{_esc(style)}" vertex="1" parent="{parent_id}"{extra}>'
            f'<mxGeometry x="{x:.2f}" y="{y:.2f}" width="{w:.2f}" height="{h:.2f}" as="geometry"/></mxCell>')

    # ---- public API
    def group(self, kind, label, x, y, w, h, parent=None, style_extra="", html_label=False):
        """Container box. kind: region, realm, ad, fd, vcn, subnet, compartment, tenancy, onprem,
        internet, thirdparty, osn, tier, usergroup, other, component, atomic, onprem_component,
        thirdparty_component, optional (dashed = optional/TBC component), note.  x,y = top-left relative to parent."""
        k = GROUP_ALIASES.get(kind.lower(), kind.lower())
        if k not in GROUP_STYLES:
            raise KeyError(f"Unknown group kind '{kind}'. Use one of {sorted(GROUP_STYLES)}")
        id = self._id()
        lines = str(label).split("\n")
        if k in ("vcn", "subnet") and len(lines) > 1:   # first line bold, CIDR in dark ink
            val = f"<b>{html.escape(lines[0])}</b><br><font color=\"{INK}\">" + "<br>".join(html.escape(l) for l in lines[1:]) + "</font>"
        elif k in ("vcn", "subnet"):
            val = f"<b>{html.escape(lines[0])}</b>"
        else:
            val = label if html_label else _htmltext(label)
        container = k not in ("component", "atomic", "onprem_component", "thirdparty_component", "note", "optional")
        style = GROUP_STYLES[k] + (CONTAINER_TAIL if container else FONT) + style_extra
        self._vertex(id, val, style, x, y, w, h, self._pid(parent))
        node = Node(self, id, "group:" + k, x, y, w, h, parent, label=label)
        self.nodes.append(node)
        # border badges (VCN icon / subnet route-table + security-list icons)
        if k == "vcn" and self.badges.get("vcn"):
            st, bw, bh = self.badges["vcn"][0]
            self._vertex(self._id(), "", st, w - bw - 8, -bh / 2, bw, bh, id)
        if k == "subnet" and self.badges.get("subnet"):
            bx = w - 6
            for st, bw, bh in reversed(self.badges["subnet"]):
                bw, bh = bw * 0.8, bh * 0.8
                bx -= bw + 2
                self._vertex(self._id(), "", st, bx, -bh / 2, bw, bh, id)
        return node

    def icon(self, name, cx, cy, parent=None, label=None, size=None, border=False, label_pos="bottom"):
        """Place an OCI icon with its CENTRE at (cx, cy) relative to parent. label=None -> service
        name; label='' -> no label; '\\n' for line breaks. size = max glyph dimension (default native ~40-50px).
        border=True: icon deliberately sits ON its parent's edge (e.g. IGW/DRG on a VCN border); check() skips it.
        label_pos: bottom (default) | right | left | top - move the label away from where connectors attach."""
        key = resolve(name, self.icons)
        ic = self.icons[key]
        big = max(ic["w"], ic["h"])
        s = size / big if size else (48 / big if big > 56 else 1.0)
        w, h = ic["w"] * s, ic["h"] * s
        id = self._id()
        lab = key if label is None else label
        st = ICON_WRAP + LABEL_POS.get(label_pos, "")
        self._vertex(id, _htmltext(lab), st, cx - w / 2, cy - h / 2, w, h, self._pid(parent))
        for (st, x, y, cw, ch) in ic["cells"]:
            self._vertex(self._id(), "", st, x * s, y * s, cw * s, ch * s, id, ' connectable="0"')
        node = Node(self, id, "icon", cx - w / 2, cy - h / 2, w, h, parent, label=lab, name=key)
        node.border = border
        node.label_pos = label_pos
        self.nodes.append(node)
        return node

    def box(self, label, x, y, w, h, parent=None, kind="component", html_label=False):
        """Logical component box (component/atomic/onprem_component/thirdparty_component/note).
        html_label=True lets you pass simple HTML (<b>, <br>, <i>) for notes."""
        return self.group(kind, label, x, y, w, h, parent=parent, html_label=html_label)

    def text(self, label, x, y, w=200, h=20, parent=None, size=11, bold=False, align="left", color=INK):
        id = self._id()
        st = (f"text;html=1;whiteSpace=wrap;strokeColor=none;fillColor=none;align={align};verticalAlign=middle;"
              f"fontSize={size};fontColor={color};{FONT}" + ("fontStyle=1;" if bold else ""))
        self._vertex(id, _htmltext(label), st, x, y, w, h, self._pid(parent))
        n = Node(self, id, "text", x, y, w, h, parent, label=label)
        self.nodes.append(n)
        return n

    def marker(self, n, cx, cy, parent=None):
        """Numbered circle (style guide 'ordered action'), centre at cx,cy."""
        id = self._id()
        st = ("ellipse;html=1;fillColor=#312D2A;strokeColor=none;fontColor=#FFFFFF;fontSize=9;fontStyle=1;"
              "aspect=fixed;" + FONT)
        self._vertex(id, str(n), st, cx - 8, cy - 8, 16, 16, self._pid(parent))
        node = Node(self, id, "marker", cx - 8, cy - 8, 16, 16, parent, label=str(n))
        self.nodes.append(node)
        return node

    def connect(self, a, b, label="", kind="solid", both=False, waypoints=None, straight=False,
                exit=None, entry=None, label_offset=None):
        """Connector. kind: solid (data flow / physical), dashed (user interaction),
        fastconnect | vpn | rpc  (special connectors: double arrow + badge + label),
        plain (no arrows).  waypoints: list of absolute (x,y) bend points.
        exit/entry: (fx, fy) fractions on the shape perimeter, e.g. (1,0.5) = right middle."""
        id = self._id()
        st = EDGE_BASE
        special = kind in ("fastconnect", "vpn", "rpc")
        if kind == "dashed":
            st += "dashed=1;"
        if kind == "plain":
            st += "endArrow=none;"
        if both or special:
            st += "startArrow=open;"
        if not straight and not special and not waypoints:
            st += "edgeStyle=orthogonalEdgeStyle;"
        if exit is None and not waypoints:
            exit = self._port(a, b)
        if entry is None and not waypoints:
            entry = self._port(b, a)
        if exit:
            st += f"exitX={exit[0]};exitY={exit[1]};exitDx=0;exitDy=0;"
        if entry:
            st += f"entryX={entry[0]};entryY={entry[1]};entryDx=0;entryDy=0;"
        pts = ""
        if waypoints:
            pts = '<Array as="points">' + "".join(f'<mxPoint x="{x}" y="{y}"/>' for x, y in waypoints) + "</Array>"
        lbl = label
        off = ""
        badge = None
        if special:
            imgs = self.badges.get(kind)
            if imgs:
                badge = imgs[0]
            if not lbl:
                lbl = {"fastconnect": "FastConnect", "vpn": "Site-to-Site VPN", "rpc": "Remote Peering"}[kind]
            if badge:
                off = f'<mxPoint x="0" y="{badge[2] / 2 + 10:.1f}" as="offset"/>'
        if label_offset:
            off = f'<mxPoint x="{label_offset[0]}" y="{label_offset[1]}" as="offset"/>'
        xml = (f'<mxCell id="{id}" value="{_esc(_htmltext(lbl))}" style="{_esc(st)}" edge="1" parent="1" '
               f'source="{a.id}" target="{b.id}"><mxGeometry relative="1" as="geometry">{pts}{off}</mxGeometry></mxCell>')
        self.edges.append(xml)
        if badge:
            bst, bw, bh = badge
            s = 26 / bh
            bw, bh = bw * s, bh * s
            self.edges.append(
                f'<mxCell id="{self._id()}" value="" style="{_esc(bst + ";labelBackgroundColor=#FFFFFF;fillColor=#FFFFFF;")}" '
                f'vertex="1" connectable="0" parent="{id}"><mxGeometry x="0" y="0" width="{bw:.1f}" height="{bh:.1f}" '
                f'relative="1" as="geometry"><mxPoint x="{-bw / 2:.1f}" y="{-bh / 2:.1f}" as="offset"/></mxGeometry></mxCell>')
        return id

    SIDES = {"right": (1, 0.5), "left": (0, 0.5), "bottom": (0.5, 1), "top": (0.5, 0)}

    def _port(self, n, other):
        """Pick the side of icon n facing `other`, never the side carrying n's label."""
        if n.kind != "icon" or not n.label:
            return None
        (ax, ay), (bx, by) = n.center(), other.center()
        dx, dy = bx - ax, by - ay
        h = "right" if dx >= 0 else "left"
        v = "bottom" if dy >= 0 else "top"
        order = [h, v] if abs(dx) >= abs(dy) else [v, h]
        order += [s for s in ("top", "right", "left", "bottom") if s not in order]
        lp = getattr(n, "label_pos", "bottom")
        for s in order:
            if s != lp:
                return self.SIDES[s]

    # ---- output
    def _bounds(self):
        xs, ys, xe, ye = [], [], [], []
        for n in self.nodes:
            x, y = n.abs
            xs.append(x); ys.append(y); xe.append(x + n.w); ye.append(y + n.h + (26 if n.kind == "icon" else 0))
        return (min(xs), min(ys), max(xe), max(ye)) if xs else (0, 0, 800, 600)

    def xml(self):
        x0, y0, x1, y1 = self._bounds()
        body = "".join(self.cells) + "".join(self.edges)
        return (f'<mxfile host="ocidraw" type="device"><diagram id="oci" name="{_esc(self.title_text or "Architecture")}">'
                f'<mxGraphModel dx="{int(x1)}" dy="{int(y1)}" grid="1" gridSize="10" guides="1" tooltips="1" connect="1" '
                f'arrows="1" fold="1" page="0" pageScale="1" pageWidth="{int(x1 + 40)}" pageHeight="{int(y1 + 40)}" '
                f'math="0" shadow="0" background="#FFFFFF"><root><mxCell id="0"/><mxCell id="1" parent="0"/>{body}'
                f'</root></mxGraphModel></diagram></mxfile>')

    def save(self, path):
        with open(path, "w", encoding="utf-8") as f:
            f.write(self.xml())
        return path

    def render(self, png_path, scale=2, src=None):
        """Render to PNG with the draw.io desktop CLI (headless). Returns path or None."""
        src = src or os.path.splitext(png_path)[0] + ".drawio"
        if not os.path.exists(src):
            self.save(src)
        return render(src, png_path, scale)

    # ---- quality checks
    def check(self, margin=4):
        """Return a list of layout problems: overlaps, children escaping parents, labels on headers."""
        warn = []
        def rect(n, with_label=True):
            x, y = n.abs
            w, h = n.w, n.h
            if n.kind == "icon" and with_label and n.label:
                lines = str(n.label).split("\n")
                lw = max(len(l) for l in lines) * 5.2
                lh = 13 * len(lines) + 2
                cx, cy = x + w / 2, y + h / 2
                pos = getattr(n, "label_pos", "bottom")
                if pos == "right":
                    return (x, min(y, cy - lh / 2), x + w + 4 + lw, max(y + h, cy + lh / 2))
                if pos == "left":
                    return (x - 4 - lw, min(y, cy - lh / 2), x + w, max(y + h, cy + lh / 2))
                if pos == "top":
                    return (min(x, cx - lw / 2), y - lh, max(x + w, cx + lw / 2), y + h)
                return (min(x, cx - lw / 2), y, max(x + w, cx + lw / 2), y + h + lh)
            return (x, y, x + w, y + h)
        def inter(a, b):
            return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]
        items = [n for n in self.nodes if n.kind in ("icon", "marker", "text") or n.kind.startswith("group:")]
        for n in items:
            if n.parent is None or getattr(n, "border", False):
                continue
            r, p = rect(n), rect(n.parent)
            if r[0] < p[0] + margin or r[2] > p[2] - margin or r[1] < p[1] or r[3] > p[3] - margin:
                warn.append(f"OUTSIDE: {n.label or n.name!r} spills out of {n.parent.label!r}")
            header = 34 if n.parent.kind in ("group:vcn", "group:subnet") else 24
            if n.kind == "icon" and r[1] < p[1] + header:
                warn.append(f"HEADER: {n.label or n.name!r} overlaps the title area of {n.parent.label!r}")
        icons = [n for n in items if n.kind in ("icon", "text", "marker")]
        for i, a in enumerate(icons):
            for b in icons[i + 1:]:
                if inter(rect(a), rect(b)):
                    warn.append(f"OVERLAP: {a.label or a.name!r} and {b.label or b.name!r}")
        groups = [n for n in items if n.kind.startswith("group:")]
        for i, a in enumerate(groups):
            for b in groups[i + 1:]:
                if a.parent is b.parent and inter(rect(a), rect(b)):
                    warn.append(f"GROUP OVERLAP: {a.label!r} and {b.label!r}")
        return warn


def _drawio_bin():
    for b in ("drawio", "draw.io", "/opt/drawio/drawio", "/Applications/draw.io.app/Contents/MacOS/draw.io"):
        p = shutil.which(b) or (b if os.path.exists(b) else None)
        if p:
            return p
    return None


def render(src, png_path, scale=2):
    b = _drawio_bin()
    if not b:
        print("draw.io CLI not found - skipping PNG render (install drawio-desktop .deb to enable).")
        return None
    cmd = [b, "-x", "-f", "png", "-s", str(scale), "-b", "20", "-o", png_path, src, "--no-sandbox", "--disable-gpu"]
    if sys.platform.startswith("linux") and not os.environ.get("DISPLAY") and shutil.which("xvfb-run"):
        cmd = ["xvfb-run", "-a"] + cmd
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=180)
    return png_path if os.path.exists(png_path) else None


def catalog(out, toolkit=None, cols=10):
    d = Diagram(toolkit, title="OCI icon catalog")
    names = sorted(d.icons)
    cw, ch = 130, 120
    for i, n in enumerate(names):
        r, c = divmod(i, cols)
        d.icon(n, 60 + c * cw, 50 + r * ch, label=n.replace(" ", "\n", 2) if len(n) > 18 else n)
    d.save(out)
    return out


if __name__ == "__main__":
    a = sys.argv[1:]
    if not a or a[0] == "icons":
        tk = load_toolkit(None)
        q = _norm(" ".join(a[1:])) if len(a) > 1 else ""
        names = sorted(tk["icons"])
        for n in names:
            if not q or all(t in _norm(n) for t in q.split()):
                print(n)
        if q:
            print("\naliases:", {k: v for k, v in ALIASES.items() if q in k})
    elif a[0] == "catalog":
        p = catalog(a[1] if len(a) > 1 else "oci_catalog.drawio")
        print(p)
