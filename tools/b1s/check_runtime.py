#!/usr/bin/env python3
"""Offline B1S runtime prerequisites; never chroot or execute target binaries."""
import argparse
import hashlib
import json
import re
from pathlib import Path

ASSETS = (
    "etc/rc_keymaps/b1s.toml",
    "etc/systemd/system/b1s-ir-keymap.service",
    "etc/systemd/system/b1s-hdmi-audio.service",
    "usr/local/sbin/b1s-ir-keymap.sh",
    "usr/local/sbin/b1s-hdmi-audio.sh",
    "usr/local/sbin/b1s-usb-role",
)
SERVICES = ("b1s-ir-keymap.service", "b1s-hdmi-audio.service")
PACKAGES = ("ir-keytable", "alsa-utils", "kmod", "systemd")
COMMANDS = {
    "ir-keytable": ("usr/bin/ir-keytable",), "amixer": ("usr/bin/amixer",),
    "modprobe": ("usr/sbin/modprobe", "sbin/modprobe"),
    "systemctl": ("usr/bin/systemctl", "bin/systemctl"),
    **{name: ("usr/bin/" + name, "bin/" + name)
       for name in ("sh", "sleep", "readlink", "basename", "cat", "id")},
}
MODULES = {
    "RC_CORE": "rc-core", "IR_MESON": "meson-ir", "IR_NEC_DECODER": "ir-nec-decoder",
    "SND_MESON_AIU": "snd-soc-meson-aiu", "SND_MESON_GX_SOUND_CARD": "snd-soc-meson-gx-sound-card",
    "REGULATOR_FIXED_VOLTAGE": "fixed", "PHY_MESON_GXL_USB2": "phy-meson-gxl-usb2",
    "USB_ROLE_SWITCH": "roles", "USB_XHCI_HCD": "xhci-hcd", "USB_XHCI_PLATFORM": "xhci-plat-hcd",
    "USB_DWC3": "dwc3", "USB_DWC3_MESON_G12A": "dwc3-meson-g12a", "USB_DWC2": "dwc2",
    "USB_LIBCOMPOSITE": "libcomposite", "USB_F_MASS_STORAGE": "usb_f_mass_storage",
    "USB_MASS_STORAGE": "g_mass_storage",
}


def rooted(root, relative):
    """Resolve image symlinks (including absolute ones) inside the image root."""
    pending = list(Path(relative).parts)
    parts = []
    links = 0
    while pending:
        part = pending.pop(0)
        if part in ("/", "."):
            continue
        if part == "..":
            if not parts:
                raise ValueError("path escapes rootfs: " + str(relative))
            parts.pop()
            continue
        candidate = root.joinpath(*parts, part)
        if candidate.is_symlink():
            links += 1
            if links > 40:
                raise ValueError("symlink loop: " + str(relative))
            target = candidate.readlink()
            if target.is_absolute():
                parts = []
            pending = list(target.parts) + pending
        else:
            parts.append(part)
    return root.joinpath(*parts)


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def installed_packages(root):
    root = Path(root).resolve()
    packages = {}
    status = rooted(root, "var/lib/dpkg/status").read_text()
    for paragraph in status.split("\n\n"):
        fields = dict(line.split(": ", 1) for line in paragraph.splitlines() if ": " in line and not line.startswith(" "))
        if fields.get("Package") in PACKAGES and fields.get("Status", "").split()[1:] == ["ok", "installed"]:
            packages[fields["Package"]] = fields.get("Version", "unknown")
    return packages


def check(root, config, source):
    root, source = Path(root).resolve(), Path(source).resolve()
    packages = installed_packages(root)
    missing = set(PACKAGES) - packages.keys()
    if missing:
        raise ValueError("Missing installed target packages: " + ", ".join(sorted(missing)) +
                         "; prepare the base image with apt-get install ir-keytable alsa-utils kmod systemd")
    commands = {}
    for name, candidates in COMMANDS.items():
        for relative in candidates:
            path = rooted(root, relative)
            if path.is_file() and path.stat().st_mode & 0o111 and path.stat().st_size:
                commands[name] = relative
                break
        else:
            raise ValueError("Missing target executable: " + name)
    hashes = {}
    for relative in ASSETS:
        path = rooted(root, relative)
        if not path.is_file() or digest(path) != digest(source / relative):
            raise ValueError("Missing or modified B1S asset: " + relative)
        if relative.startswith("usr/local/sbin/") and not path.stat().st_mode & 0o111:
            raise ValueError("B1S script is not executable: " + relative)
        hashes[relative] = digest(path)
    services = {}
    for name in SERVICES:
        relative = "etc/systemd/system/multi-user.target.wants/" + name
        link = root / relative
        if not link.is_symlink() or rooted(root, relative) != rooted(root, "etc/systemd/system/" + name):
            raise ValueError("B1S service not enabled: " + name)
        services[name] = str(link.readlink())
    # Defaults must not create a gadget or take ownership of any storage.
    for relative in ("etc/modules", "etc/modules-load.d", "usr/lib/modules-load.d"):
        path = rooted(root, relative)
        files = path.glob("*.conf") if path.is_dir() else [path] if path.is_file() else []
        for file in files:
            for line in file.read_text().splitlines():
                if re.match(r"^g[-_]\w+\b", line.split("#", 1)[0].strip()):
                    raise ValueError("Gadget autoload conflicts with default host: " + str(file.relative_to(root)))
    settings = dict(re.findall(r"^CONFIG_(\w+)=([ym])$", Path(config).read_text(), re.M))
    kernel = Path(config).name.removeprefix("config-")
    module_root = rooted(root, "usr/lib/modules/" + kernel)
    dependencies = (module_root / "modules.dep").read_text().splitlines()
    indexed = {line.split(":", 1)[0]: line.split(":", 1)[1].split()
               for line in dependencies if ":" in line}
    checked = set()

    def check_module(relative):
        if relative in checked:
            return
        if Path(relative).is_absolute() or ".." in Path(relative).parts:
            raise ValueError("Invalid module index path: " + relative)
        path = module_root / relative
        if relative not in indexed or not path.is_file() or not path.stat().st_size:
            raise ValueError("Missing/unindexed module dependency: " + relative)
        checked.add(relative)
        for dependency in indexed[relative]:
            check_module(dependency)
    modules = {}
    for option, name in MODULES.items():
        if settings.get(option) == "y":
            modules[name] = "built-in"
            continue
        if settings.get(option) != "m":
            raise ValueError("Missing target CONFIG_" + option)
        matches = [relative for relative in indexed
                   if Path(relative).name in {name + ".ko" + suffix for suffix in ("", ".gz", ".xz", ".zst")}]
        if len(matches) != 1 or not (module_root / matches[0]).is_file() or not (module_root / matches[0]).stat().st_size:
            raise ValueError("Missing/unindexed target module: " + name)
        check_module(matches[0])
        modules[name] = {"path": matches[0], "sha256": digest(module_root / matches[0])}
    return {"passed": True, "scope": "offline prerequisites; final image boot still required",
            "kernel": kernel, "default_usb_role": "host", "gadget_autoload": False,
            "packages": packages, "commands": commands, "services": services,
            "modules": modules, "sha256": hashes}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("config", type=Path, nargs="?")
    parser.add_argument("source", type=Path, nargs="?")
    parser.add_argument("--missing-packages", action="store_true", help="List missing packages only; do not execute or install anything")
    args = parser.parse_args()
    try:
        if args.missing_packages:
            print("\n".join(sorted(set(PACKAGES) - installed_packages(args.root).keys())))
            raise SystemExit(0)
        if args.config is None or args.source is None:
            parser.error("config and source are required unless --missing-packages is set")
        report = check(args.root, args.config, args.source)
    except (OSError, ValueError) as exc:
        print(json.dumps({"passed": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        raise SystemExit(1)
    print(json.dumps(report, ensure_ascii=False, indent=2))
