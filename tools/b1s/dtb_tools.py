#!/usr/bin/env python3
"""FDT inspection: compare references by node path, never by phandle number."""
import argparse
import json
import re
import struct
from pathlib import Path

ARRAYS = {
    "clocks": "#clock-cells", "assigned-clocks": "#clock-cells",
    "assigned-clock-parents": "#clock-cells", "resets": "#reset-cells",
    "power-domains": "#power-domain-cells", "phys": "#phy-cells",
    "pwms": "#pwm-cells", "io-channels": "#io-channel-cells",
    "sound-dai": "#sound-dai-cells", "thermal-sensors": "#thermal-sensor-cells",
    "cooling-device": "#cooling-cells", "dmas": "#dma-cells",
    "interrupts-extended": "#interrupt-cells",
}
SINGLES = {
    "interrupt-parent", "next-level-cache", "cpu", "trip", "phy-handle",
    "remote-endpoint", "mmc-pwrseq", "operating-points-v2", "hdmi-phandle",
    "amlogic,canvas", "amlogic,hhi-sysctrl", "amlogic,ao-sysctrl",
    "amlogic,secure-monitor", "amlogic,ao-secure", "syscon",
    "mdio-parent-bus", "secure-monitor",
    "mboxes",  # handled separately below: this platform has #mbox-cells
}
ARRAYS["mboxes"] = "#mbox-cells"
LISTS = {"interrupt-affinity", "audio-aux-devs", "nvmem-cells", "memory-region", "shmem"}


def cells(data):
    if len(data) % 4:
        raise ValueError("Not a cell array")
    return list(struct.unpack(f">{len(data) // 4}I", data))


class Tree:
    def __init__(self, filename):
        data = Path(filename).read_bytes()
        magic, total, off, strings, reserve = struct.unpack_from(">5I", data)
        if magic != 0xD00DFEED or total > len(data):
            raise ValueError("Invalid FDT header")
        self.reservations = []
        while True:
            address, size = struct.unpack_from(">QQ", data, reserve)
            reserve += 16
            if not address and not size:
                break
            self.reservations.append((address, size))
        self.nodes = {}
        stack = []
        while True:
            token = struct.unpack_from(">I", data, off)[0]
            off += 4
            if token == 1:
                end = data.index(b"\0", off)
                stack.append(data[off:end].decode())
                path = "/".join(stack) or "/"
                self.nodes[path] = {}
                off = (end + 4) & ~3
            elif token == 2:
                stack.pop()
            elif token == 3:
                length, nameoff = struct.unpack_from(">II", data, off)
                off += 8
                nameend = data.index(b"\0", strings + nameoff)
                name = data[strings + nameoff:nameend].decode()
                self.nodes["/".join(stack) or "/"][name] = data[off:off + length]
                off = (off + length + 3) & ~3
            elif token == 4:
                continue
            elif token == 9:
                break
            else:
                raise ValueError(f"Unknown FDT token {token}")
        self.handles = {cells(props["phandle"])[0]: path for path, props in self.nodes.items() if "phandle" in props}
        self.labels = {value.rstrip(b"\0").decode(): name for name, value in self.nodes.get("/__symbols__", {}).items()}
        for handle, path in self.handles.items():
            self.labels.setdefault(path, f"b1s_ref_{handle:x}")

    def references(self, name, data):
        """Return {cell index: target path}; reject broken provider references."""
        provider = ARRAYS.get(name)
        fixed_args = 3 if name == "gpio-ranges" else None
        if name in ("gpio", "gpios") or name.endswith("-gpios"):
            provider = "#gpio-cells"
        single = name in SINGLES or name.endswith("-supply")
        sequence = name in LISTS or re.fullmatch(r"pinctrl-\d+", name)
        if not (provider or single or sequence or fixed_args is not None):
            return {}
        values = cells(data)
        refs = {}
        i = 0
        while i < len(values):
            handle = values[i]
            if not handle:
                i += 1
                continue
            path = self.handles[handle]
            refs[i] = path
            count = fixed_args if fixed_args is not None else (cells(self.nodes[path][provider])[0] if provider else 0)
            i += 1 + count
        if i != len(values):
            raise ValueError(f"Malformed {name}: {values}")
        return refs

    def canonical(self):
        result = {}
        for path, props in self.nodes.items():
            if path == "/__symbols__":
                continue
            result[path] = {}
            for name, data in props.items():
                if name in ("phandle", "linux,phandle"):
                    continue
                refs = self.references(name, data)
                result[path][name] = [refs.get(i, value) for i, value in enumerate(cells(data))] if refs else data.hex()
        return {"reservations": self.reservations, "nodes": result}

    def property_dts(self, name, data):
        if not data:
            return name + ";"
        refs = self.references(name, data)
        if refs:
            value = "<" + " ".join("&" + self.labels[refs[i]] if i in refs else hex(v) for i, v in enumerate(cells(data))) + ">"
        elif data.endswith(b"\0") and all(x == 0 or 32 <= x < 127 for x in data) and data.strip(b"\0"):
            value = ", ".join(json.dumps(x.decode()) for x in data[:-1].split(b"\0"))
        elif len(data) % 4 == 0:
            value = "<" + " ".join(hex(v) for v in cells(data)) + ">"
        else:
            value = "[" + " ".join(f"{v:02x}" for v in data) + "]"
        return f"{name} = {value};"

    def render(self):
        def node(path, depth):
            indent = "\t" * depth
            label = self.labels.get(path)
            out = [indent + (label + ": " if label else "") + (path.rsplit("/", 1)[-1] or "/") + " {"]
            for name, data in self.nodes[path].items():
                if name not in ("phandle", "linux,phandle"):
                    out.append(indent + "\t" + self.property_dts(name, data))
            children = [p for p in self.nodes if p != "/" and p != "/__symbols__" and (p.rsplit("/", 1)[0] or "/") == path]
            for child in children:
                out.extend([""] + node(child, depth + 1))
            out.append(indent + "};")
            return out
        reserves = [f"/memreserve/ {a:#x} {s:#x};" for a, s in self.reservations]
        return "\n".join(["/dts-v1/;", ""] + reserves + node("/", 0)) + "\n"


def differences(left, right):
    a, b = left.canonical(), right.canonical()
    changes = []
    if a["reservations"] != b["reservations"]:
        changes.append({"path": "/memreserve/", "old": a["reservations"], "new": b["reservations"]})
    for path in sorted(a["nodes"].keys() | b["nodes"].keys()):
        p, q = a["nodes"].get(path, {}), b["nodes"].get(path, {})
        if path not in a["nodes"] or path not in b["nodes"]:
            changes.append({"path": path, "node": "added" if path in b["nodes"] else "removed"})
        for name in sorted(p.keys() | q.keys()):
            if p.get(name) != q.get(name):
                changes.append({"path": path, "property": name, "old": p.get(name), "new": q.get(name)})
    return changes


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["compare", "decompile"])
    parser.add_argument("dtb")
    parser.add_argument("other", nargs="?")
    args = parser.parse_args()
    tree = Tree(args.dtb)
    if args.command == "decompile":
        print(tree.render(), end="")
    else:
        delta = differences(tree, Tree(args.other))
        print(json.dumps(delta, indent=2))
        raise SystemExit(bool(delta))
