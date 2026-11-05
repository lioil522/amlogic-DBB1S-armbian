#!/usr/bin/env python3
"""Read-only B1S evidence collection, before/after each hardware test."""
import argparse
import glob
import hashlib
import json
import platform
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="new evidence directory")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    report = {"time": datetime.now(timezone.utc).isoformat(),
              "kernel": platform.uname()._asdict(), "files": {}, "commands": {}}
    patterns = [
        "/proc/cmdline", "/proc/uptime", "/boot/uEnv.txt",
        "/sys/class/usb_role/*/role", "/sys/class/udc/*/state",
        "/sys/class/phy/*/power/runtime_status", "/sys/class/phy/*/power/control",
        "/sys/class/phy/*/power/runtime_usage",
        "/sys/kernel/debug/regmap/d00780*/registers",
        "/sys/class/drm/card*/device/uevent", "/sys/class/drm/card*-*/status",
        "/sys/class/drm/card*-*/enabled", "/sys/class/drm/card*-*/edid",
        "/sys/class/devfreq/*gpu*/cur_freq", "/sys/class/devfreq/*gpu*/available_frequencies",
        "/sys/class/devfreq/*gpu*/device/power/control",
        "/sys/class/devfreq/*gpu*/device/power/runtime_status",
        "/sys/class/thermal/thermal_zone*/temp", "/sys/class/thermal/thermal_zone*/type",
        "/sys/class/thermal/thermal_zone*/cdev*_trip_point",
        "/sys/class/thermal/cooling_device*/type", "/sys/class/thermal/cooling_device*/cur_state",
        "/sys/class/rc/rc*/protocols", "/sys/class/bluetooth/hci*/address",
    ]
    for pattern in patterns:
        for name in sorted(glob.glob(pattern)):
            path = Path(name)
            try:
                data = path.read_bytes()
                record = {"sha256": hashlib.sha256(data).hexdigest()}
                if path.name == "edid":
                    record.update(size=len(data), hex=data.hex())
                else:
                    record["text"] = data.decode(errors="replace")
                report["files"][name] = record
            except OSError as error:
                report["files"][name] = {"error": str(error)}
    commands = {"dmesg": ["dmesg"], "usb": ["lsusb", "-t"],
                "ir": ["ir-keytable"], "bluetooth": ["bluetoothctl", "--timeout", "5", "show"],
                "hci": ["hciconfig", "-a"],
                "input": ["cat", "/proc/bus/input/devices"]}
    for name, command in commands.items():
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=15)
            report["commands"][name] = {"argv": command, "returncode": result.returncode,
                                        "stdout": result.stdout, "stderr": result.stderr}
        except (OSError, subprocess.TimeoutExpired) as error:
            report["commands"][name] = {"argv": command, "error": str(error)}
    destination = args.output / "snapshot.json"
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(destination)


if __name__ == "__main__":
    main()
