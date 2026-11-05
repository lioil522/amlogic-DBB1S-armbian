#!/usr/bin/env python3
"""Run GPU readback and include delayed kernel faults in the result."""
import argparse
import json
import re
import subprocess
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binary", type=Path)
    parser.add_argument("--output", type=Path, required=True, help="new evidence directory")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    before = subprocess.check_output(["dmesg"], text=True).splitlines()
    try:
        result = subprocess.run([str(args.binary.resolve())], capture_output=True,
                                text=True, timeout=60)
        returncode, stdout, stderr = result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired as error:
        returncode = 124
        stdout = (error.stdout or b"").decode(errors="replace")
        stderr = "GPU check exceeded 60 seconds\n" + (error.stderr or b"").decode(errors="replace")
    except OSError as error:
        returncode, stdout, stderr = 127, "", str(error)
    # Include the idle autosuspend following process exit, not just draw time.
    time.sleep(2)
    after = subprocess.check_output(["dmesg"], text=True).splitlines()
    complete = after[:len(before)] == before
    new = after[len(before):] if complete else after
    faults = [line for line in new if "panfrost" in line.lower() and
              re.search(r"fault|timeout|stuck|error", line, re.IGNORECASE)]
    passed = returncode == 0 and complete and not faults
    report = {"passed": passed, "returncode": returncode,
              "stdout": stdout, "stderr": stderr,
              "kernel_log_complete": complete, "gpu_faults": faults}
    for name, lines in (("before.dmesg", before), ("after.dmesg", after)):
        (args.output / name).write_text("\n".join(lines) + "\n")
    (args.output / "result.json").write_text(json.dumps(report, indent=2) + "\n")
    print(stdout, end="")
    print("GPU draw + idle check:", "PASS" if passed else "FAIL", flush=True)
    for line in faults:
        print(line)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
