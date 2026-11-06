#!/usr/bin/env python3
"""Pinned cloud-only ARM64 diagnostic build; no release, install or GPU load."""
import difflib
import hashlib
import io
import json
import os
import re
import signal
import shutil
import struct
import subprocess
import sys
import tarfile
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
COMMIT = "cea7acd4c5be4710f197426f017b56006e01367a"
RELEASE = "6.18.60-b1s-gpu-diag1"
HEADER = "drivers/gpu/drm/panfrost/panfrost_diag.h"
REQUIRED = ("PM", "PM_OPP", "PM_DEVFREQ", "DEVFREQ_THERMAL", "DEVFREQ_GOV_SIMPLE_ONDEMAND",
    "DRM_PANFROST", "THERMAL", "THERMAL_OF", "USB_GADGET", "USB_DWC2", "USB_DWC3",
    "USB_ROLE_SWITCH", "PHY_MESON_GXL_USB2", "USB_DWC2_DUAL_ROLE", "USB_DWC3_DUAL_ROLE",
    "DEBUG_INFO", "DEBUG_INFO_DWARF5", "DEBUG_INFO_BTF", "DEBUG_INFO_BTF_MODULES")
DISK_FLOOR = 2 * 1024**3


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def config_values(text):
    values = {}
    for line in text.splitlines():
        if line.startswith("CONFIG_") and "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
        elif re.fullmatch(r"# CONFIG_\w+ is not set", line):
            values[line.split()[1]] = "n"
    return values


def validate_config(reference, actual):
    old, new = config_values(reference), config_values(actual)
    for symbol in REQUIRED:
        key = "CONFIG_" + symbol
        if new.get(key) not in ("y", "m") or new.get(key) != old.get(key):
            raise ValueError(f"Required feature changed: {key}")
    for key, value in {"CONFIG_LOCALVERSION": '"-b1s-gpu-diag1"',
            "CONFIG_LOCALVERSION_AUTO": "n", "CONFIG_DEBUG_INFO_COMPRESSED_ZLIB": "y",
            "CONFIG_DEBUG_INFO_REDUCED": "n", "CONFIG_DEBUG_INFO_SPLIT": "n"}.items():
        if new.get(key, "n") != value:
            raise ValueError(f"Unexpected diagnostic setting: {key}")
    changes = {key: [old.get(key, "n"), new.get(key, "n")]
        for key in sorted(old.keys() | new.keys()) if old.get(key, "n") != new.get(key, "n")}
    tool_prefixes = ("CONFIG_CC_", "CONFIG_GCC_", "CONFIG_AS_", "CONFIG_LD_", "CONFIG_PAHOLE_")
    tool_keys = {"CONFIG_TOOLS_SUPPORT_RELR", "CONFIG_RELR", "CONFIG_KSTACK_ERASE",
        "CONFIG_RANDSTRUCT_FULL", "CONFIG_RANDSTRUCT_PERFORMANCE", "CONFIG_DEBUG_INFO_COMPRESSED_ZSTD"}
    unexpected = {key: value for key, value in changes.items()
        if not key.startswith(tool_prefixes) and key not in tool_keys}
    if unexpected:
        raise ValueError(f"Unreviewed cloud config changes: {unexpected}")
    return changes


def resources(work):
    fs = os.statvfs(work)
    mem = {line.split(":")[0]: int(line.split(":")[1].split()[0]) * 1024
        for line in Path("/proc/meminfo").read_text().splitlines()}
    return {"disk_available": fs.f_bavail * fs.f_frsize,
        "mem_available": mem["MemAvailable"], "swap_free": mem["SwapFree"]}


def guard(values):
    if values["disk_available"] < DISK_FLOOR:
        return "available disk below 2GiB"
    if values["mem_available"] < 256 * 1024**2 and values["swap_free"] < 512 * 1024**2:
        return "memory and swap below safety floors"
    return None


def jobs(values, cpus):
    return max(1, min(cpus, 8, int(values["mem_available"] / (1536 * 1024**2))))


class Build:
    def __init__(self):
        if os.environ.get("GITHUB_ACTIONS") != "true":
            raise ValueError("Build stages run only in GitHub Actions; do not restart local compilation")
        self.work = Path(os.environ["B1S_DIAG_WORK"]).resolve()
        temp = Path(os.environ["RUNNER_TEMP"]).resolve()
        if self.work == temp or temp not in self.work.parents:
            raise ValueError("Work must be a child of RUNNER_TEMP")
        self.src, self.out = self.work / "source", self.work / "build"
        self.ev = self.work / "evidence"
        self.inputs = HERE / "inputs"
        self.env = os.environ.copy()
        for key in list(self.env):
            if key.startswith("KBUILD_") or key in {"MAKEFLAGS", "MFLAGS", "KCFLAGS", "KAFLAGS",
                    "KCPPFLAGS", "CFLAGS_KERNEL", "CFLAGS_MODULE", "LDFLAGS_vmlinux"}:
                del self.env[key]
        self.env.update(KBUILD_BUILD_TIMESTAMP="2026-11-06 00:00:00 UTC", KBUILD_BUILD_USER="b1s",
            KBUILD_BUILD_HOST="builder", KBUILD_BUILD_VERSION="1", LC_ALL="C")
        self.make = ["make", "-C", str(self.src), f"O={self.out}", "ARCH=arm64",
            "CROSS_COMPILE=aarch64-linux-gnu-", "CC=aarch64-linux-gnu-gcc-12",
            "HOSTCC=gcc-12", "HOSTCXX=g++-12", "LD=aarch64-linux-gnu-ld", "LOCALVERSION="]

    def run(self, args, cwd=None):
        result = subprocess.run(args, cwd=cwd, env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        if self.ev.exists():
            with (self.ev / "commands.log").open("a") as log:
                log.write(json.dumps(args) + "\n" + result.stdout + "\n")
        if result.returncode:
            raise ValueError(f"Command failed ({result.returncode}): {args}\n{result.stdout[-8000:]}")
        return result.stdout

    def write(self, name, value):
        (self.ev / name).write_text(json.dumps(value, indent=2) + "\n")

    def verify_inputs(self):
        for line in (self.inputs / "SHA256SUMS").read_text().splitlines():
            digest, name = line.split(maxsplit=1)
            path = self.inputs / name
            if path.resolve().parent != self.inputs.resolve() or sha(path) != digest:
                raise ValueError(f"Input digest mismatch: {name}")

    def patch(self):
        tracked = subprocess.check_output(["git", "diff", "HEAD", "--binary", "--no-ext-diff"], cwd=self.src)
        untracked = self.run(["git", "ls-files", "--others", "--exclude-standard"], self.src).splitlines()
        if untracked != [HEADER]:
            raise ValueError(f"Unexpected untracked files: {untracked}")
        added = subprocess.run(["git", "diff", "--no-index", "--binary", "--no-ext-diff", "--",
            "/dev/null", HEADER], cwd=self.src, capture_output=True)
        if added.returncode != 1:
            raise ValueError(added.stderr.decode())
        return tracked + added.stdout

    def verify(self):
        self.verify_inputs()
        if self.run(["git", "rev-parse", "HEAD"], self.src).strip() != COMMIT:
            raise ValueError("Source commit changed")
        if self.patch() != (self.inputs / "source-changes.patch").read_bytes():
            raise ValueError("Source patch changed")
        if (self.out / ".config").read_bytes() != (self.ev / "resolved.config").read_bytes():
            raise ValueError("Build config changed")

    def prepare(self):
        self.verify_inputs()
        self.work.mkdir(exist_ok=False)
        self.ev.mkdir()
        self.out.mkdir()
        initial = resources(self.work)
        self.write("initial-resources.json", initial)
        if initial["disk_available"] < 12 * 1024**3:
            raise ValueError("Less than 12GiB free before source fetch; refusing broad runner cleanup")
        versions = {}
        for name, cmd in {"gcc": ["aarch64-linux-gnu-gcc-12", "--version"],
                "ld": ["aarch64-linux-gnu-ld", "--version"], "pahole": ["pahole", "--version"],
                "host_gcc": ["gcc-12", "--version"], "make": ["make", "--version"]}.items():
            versions[name] = self.run(cmd).strip()
        tool_hashes = {}
        for cmd in ("aarch64-linux-gnu-gcc-12", "aarch64-linux-gnu-ld", "aarch64-linux-gnu-as",
                "aarch64-linux-gnu-objcopy", "gcc-12", "g++-12", "pahole", "make"):
            path = Path(shutil.which(cmd) or "")
            if not path.is_file():
                raise ValueError(f"Missing tool: {cmd}")
            tool_hashes[str(path)] = sha(path)
        for compiler, backend in (("aarch64-linux-gnu-gcc-12", "cc1"),
                ("aarch64-linux-gnu-gcc-12", "collect2"), ("gcc-12", "cc1"), ("g++-12", "cc1plus")):
            path = Path(self.run([compiler, "-print-prog-name=" + backend]).strip())
            if not path.is_file():
                raise ValueError(f"Missing compiler backend: {path}")
            tool_hashes[str(path)] = sha(path)
        self.write("provenance.json", {"source_commit": COMMIT, "release": RELEASE,
            "container_manifest": "sha256:c67e92c8099ba22853ab4cfda7cfb494d253d6012ca82dee98ba8976772af018",
            "versions": versions, "tool_sha256": tool_hashes,
            "github": {key: os.environ.get(key) for key in
                ("GITHUB_SHA", "GITHUB_REF", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "ImageVersion")},
            "limits": ["not a byte-for-byte historical release rebuild", "no runtime acceptance"]})
        (self.ev / "packages.tsv").write_text(self.run(["dpkg-query", "-W", "-f=${binary:Package}\t${Version}\n"]))
        self.src.mkdir()
        self.run(["git", "init", "."], self.src)
        self.run(["git", "remote", "add", "origin", "https://github.com/ophub/linux-6.18.y.git"], self.src)
        self.run(["git", "fetch", "--no-tags", "--depth=1", "origin", COMMIT], self.src)
        self.run(["git", "checkout", "--detach", COMMIT], self.src)
        if self.run(["git", "status", "--porcelain"], self.src):
            raise ValueError("Fresh pinned source is not clean")
        self.run(["git", "apply", "--check", str(self.inputs / "source-changes.patch")], self.src)
        self.run(["git", "apply", str(self.inputs / "source-changes.patch")], self.src)
        self.run(["git", "diff", "--check"], self.src)
        if self.patch() != (self.inputs / "source-changes.patch").read_bytes():
            raise ValueError("Applied patch does not reproduce local source changes")
        (self.out / ".config").write_bytes((self.inputs / "release.config").read_bytes())
        self.run([str(self.src / "scripts/config"), "--file", str(self.out / ".config"),
            "--set-str", "LOCALVERSION", "-b1s-gpu-diag1", "--disable", "LOCALVERSION_AUTO",
            "--disable", "DEBUG_INFO_COMPRESSED_NONE", "--enable", "DEBUG_INFO_COMPRESSED_ZLIB"])
        (self.ev / "olddefconfig.log").write_text(self.run(self.make + ["olddefconfig"]))
        actual = (self.out / ".config").read_text()
        (self.ev / "resolved.config").write_text(actual)
        for name in ("release.config", "local-resolved.config"):
            old = (self.inputs / name).read_text()
            (self.ev / (name + ".diff")).write_text("".join(difflib.unified_diff(
                old.splitlines(True), actual.splitlines(True), fromfile=name, tofile="cloud-resolved.config")))
        changes = validate_config((self.inputs / "local-resolved.config").read_text(), actual)
        self.write("config-check.json", {"passed": True, "cloud_vs_local_tool_changes": changes})
        if self.run(self.make + ["-s", "kernelrelease"]).strip() != RELEASE:
            raise ValueError("Wrong kernelrelease")
        self.write("prepared.json", {"source_commit": COMMIT, "patch_sha256": sha(self.inputs / "source-changes.patch"),
            "config_sha256": sha(self.ev / "resolved.config"), "ready": True})

    def build(self, stage):
        self.verify()
        initial = resources(self.work)
        if reason := guard(initial):
            raise ValueError(reason)
        count = jobs(initial, os.cpu_count() or 1)
        targets = ["drivers/gpu/drm/panfrost/"] if stage == "panfrost" else ["Image", "modules"]
        cmd = self.make + [f"-j{count}"] + targets
        status = {"state": "running", "command": cmd, "initial": initial, "jobs": count,
            "started_utc": datetime.now(timezone.utc).isoformat()}
        self.write(stage + ".json", status)
        print(f"Starting {stage}, {count} jobs, logs in evidence/{stage}.log", flush=True)
        interrupted = []
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, lambda signum, frame: interrupted.append(signum))
        stop = None
        with (self.ev / (stage + ".log")).open("x") as log, (self.ev / (stage + ".resources.jsonl")).open("x") as monitor:
            process = subprocess.Popen(cmd, env=self.env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            try:
                while process.poll() is None:
                    values = resources(self.work)
                    monitor.write(json.dumps({"utc": datetime.now(timezone.utc).isoformat(), **values}) + "\n")
                    monitor.flush()
                    stop = f"signal {interrupted[0]}" if interrupted else guard(values)
                    if stop:
                        os.killpg(process.pid, signal.SIGTERM)
                        try:
                            process.wait(timeout=20)
                        except subprocess.TimeoutExpired:
                            os.killpg(process.pid, signal.SIGKILL)
                            process.wait()
                        break
                    time.sleep(10)
                code = process.wait()
            finally:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGTERM)
                    process.wait()
        status.update(state="stopped" if stop else "passed" if code == 0 else "failed", returncode=code,
            stop_reason=stop, final=resources(self.work), completed_utc=datetime.now(timezone.utc).isoformat())
        self.write(stage + ".json", status)
        print(json.dumps(status, indent=2), flush=True)
        if stop or code:
            print((self.ev / (stage + ".log")).read_text()[-16000:])
            raise ValueError(f"{stage} did not pass")

    def audit(self):
        self.verify()
        if json.loads((self.ev / "full.json").read_text())["state"] != "passed":
            raise ValueError("Full build did not pass")
        image, vmlinux = self.out / "arch/arm64/boot/Image", self.out / "vmlinux"
        with image.open("rb") as stream:
            header = stream.read(64)
        if header[56:60] != b"ARM\x64" or not 0 < struct.unpack_from("<Q", header, 16)[0] <= image.stat().st_size:
            raise ValueError("Bad ARM64 Image")
        if (self.out / "include/config/kernel.release").read_text().strip() != RELEASE:
            raise ValueError("Wrong final kernel release")
        if "AArch64" not in self.run(["aarch64-linux-gnu-readelf", "-h", str(vmlinux)]):
            raise ValueError("vmlinux not AArch64")
        modules = sorted(self.out.rglob("*.ko"))
        expected = [str(Path(name).with_suffix(".ko")) for name in (self.out / "modules.order").read_text().splitlines()]
        if not modules or sorted(str(p.relative_to(self.out)) for p in modules) != sorted(expected):
            raise ValueError("Missing or unexpected modules")
        vermagics = set()
        for offset in range(0, len(modules), 100):
            batch = modules[offset:offset + 100]
            values = self.run(["modinfo", "-F", "vermagic", *(str(p) for p in batch)]).splitlines()
            if len(values) != len(batch) or any(not v.startswith(RELEASE + " ") for v in values):
                raise ValueError("Module vermagic mismatch")
            vermagics.update(values)
        panfrost = self.out / "drivers/gpu/drm/panfrost/panfrost.ko"
        if not all(token in panfrost.read_bytes() for token in
                (b"b1s-diag1[", b"parmtype=b1s_diag:bool", b"runtime-resume.resources-ready")):
            raise ValueError("Missing diagnostic payload")
        for path in (vmlinux, panfrost):
            sections = self.run(["aarch64-linux-gnu-readelf", "-SW", str(path)])
            if not all(name in sections for name in (".BTF", ".debug_info", ".debug_line")):
                raise ValueError(f"Missing BTF/DWARF: {path.name}")
            line = next(line for line in sections.splitlines() if ".debug_info " in line)
            if not re.search(r"\bC\b", line):
                raise ValueError(f"Uncompressed debug info: {path.name}")
        output = self.work / "artifacts"
        output.mkdir(exist_ok=False)
        for name, path in {"Image": image, "System.map": self.out / "System.map",
                "Module.symvers": self.out / "Module.symvers", "resolved.config": self.ev / "resolved.config"}.items():
            shutil.copyfile(path, output / name)
        module_hashes = {str(p.relative_to(self.out)): sha(p) for p in modules}
        (output / "modules-SHA256SUMS").write_text("".join(f"{h}  {p}\n" for p, h in module_hashes.items()))
        with tarfile.open(output / "modules.tar.gz", "w:gz", compresslevel=1) as archive:
            prefix = f"lib/modules/{RELEASE}/"
            for path in modules:
                if reason := guard(resources(self.work)):
                    raise ValueError(reason)
                archive.add(path, arcname=prefix + "kernel/" + str(path.relative_to(self.out)), recursive=False)
            for name in ("modules.builtin", "modules.builtin.modinfo"):
                archive.add(self.out / name, arcname=prefix + name)
            order = "".join("kernel/" + name + "\n" for name in expected).encode()
            info = tarfile.TarInfo(prefix + "modules.order")
            info.size, info.mtime = len(order), 1793923200
            archive.addfile(info, io.BytesIO(order))
        if reason := guard(resources(self.work)):
            raise ValueError(reason)
        self.run(["tar", "-C", str(self.out), "-I", "xz -T2 -3", "-cf", str(output / "vmlinux-debug.tar.xz"), "vmlinux"])
        report = {"result": "local-only artifacts audited; NOT installed/boot-tested", "release": RELEASE,
            "source_commit": COMMIT, "module_count": len(modules), "vermagic": sorted(vermagics),
            "image_bytes": image.stat().st_size, "vmlinux_sha256": sha(vmlinux),
            "panfrost_sha256": sha(panfrost), "modules": module_hashes,
            "provenance": json.loads((self.ev / "provenance.json").read_text())}
        (output / "audit.json").write_text(json.dumps(report, indent=2) + "\n")
        files = sorted(output.iterdir())
        (output / "SHA256SUMS").write_text("".join(f"{sha(p)}  {p.name}\n" for p in files))
        self.write("artifact-audit.json", {key: value for key, value in report.items() if key != "modules"})
        print(f"Audited {RELEASE}, {len(modules)} modules; no installation or release", flush=True)


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in ("prepare", "panfrost", "full", "audit"):
        raise SystemExit("Usage: ci.py prepare|panfrost|full|audit")
    build = Build()
    try:
        stage = sys.argv[1]
        if stage in ("panfrost", "full"):
            build.build(stage)
        else:
            getattr(build, stage)()
    except Exception as error:
        if build.ev.exists():
            build.write("failure-" + sys.argv[1] + ".json", {"error": str(error), "type": type(error).__name__})
        raise


if __name__ == "__main__":
    main()
