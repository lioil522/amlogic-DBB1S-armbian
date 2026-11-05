#!/usr/bin/env python3
"""Check changed Bash entry points and embedded workflow shell syntax."""
import re
import subprocess
from pathlib import Path

import yaml

root = Path(__file__).resolve().parents[2]
board = root / "build-armbian/armbian-files/different-files/s912-b1s"
shells = [root / "rebuild", root / "build-armbian/armbian-files/common-files/usr/sbin/armbian-update",
          root / "tools/detect-armbian-edition.sh", *board.glob("*.sh"), *board.glob("rootfs/usr/lib/b1s/*.sh")]
for path in shells:
    subprocess.run(["bash", "-n", str(path)], check=True)
subprocess.run(["sh", "-n", str(board / "rootfs/etc/initramfs-tools/hooks/b1s-firmware")], check=True)
for path in board.glob("rootfs/usr/local/sbin/b1s-*"):
    subprocess.run(["sh", "-n", str(path)], check=True)


def walk(item):
    if isinstance(item, dict):
        for key, value in item.items():
            if key == "run" and isinstance(value, str):
                text = re.sub(r"\$\{\{.*?\}\}", "test_value", value)
                subprocess.run(["bash", "-n"], input=text, text=True, check=True)
            else:
                walk(value)
    elif isinstance(item, list):
        for child in item:
            walk(child)


for path in (root / "action.yml", root / ".github/workflows/build-armbian-using-releases-files.yml",
             root / ".github/workflows/check-b1s-adaptation.yml"):
    walk(yaml.safe_load(path.read_text()))
    print("YAML and shell syntax OK:", path.relative_to(root))
