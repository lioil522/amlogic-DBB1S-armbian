#!/usr/bin/env python3
"""Read-only IIO channel-0 sampling for one held B1S button or idle state."""
import argparse
import csv
import json
import statistics
import time
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("label", help="Physical button name, or idle")
parser.add_argument("--device", type=Path, default=Path("/sys/bus/iio/devices/iio:device0"))
parser.add_argument("--seconds", type=float, default=5)
parser.add_argument("--output", type=Path, required=True, help="New CSV path (never overwrite)")
args = parser.parse_args()
if args.seconds <= 0:
    parser.error("--seconds must be positive")
scale_file = args.device / "in_voltage_scale"
if not scale_file.exists():
    scale_file = args.device / "in_voltage0_scale"
scale = float(scale_file.read_text())  # IIO voltage scale is mV / count
offset_file = args.device / "in_voltage_offset"
offset = float(offset_file.read_text()) if offset_file.exists() else 0
samples = []
with args.output.open("x", newline="") as stream:
    writer = csv.writer(stream)
    writer.writerow(["label", "monotonic_seconds", "raw", "microvolt"])
    end = time.monotonic() + args.seconds
    while time.monotonic() < end:
        raw = int((args.device / "in_voltage0_raw").read_text())
        uv = (raw + offset) * scale * 1000
        samples.append(uv)
        writer.writerow([args.label, time.monotonic(), raw, uv])
        time.sleep(0.02)
print(json.dumps({"label": args.label, "samples": len(samples), "min_uv": min(samples),
                  "median_uv": statistics.median(samples), "max_uv": max(samples),
                  "scale_mv_per_count": scale}, indent=2))
