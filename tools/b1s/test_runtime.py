#!/usr/bin/env python3
"""Offline image integration and mocked sysfs: no live role/service changes."""
import os
import shutil
import subprocess
import tempfile
import tomllib
import unittest
from pathlib import Path

from check_runtime import ASSETS, COMMANDS, MODULES, PACKAGES, SERVICES, check, rooted

ROOT = Path(__file__).resolve().parents[2]
BOARD = ROOT / "build-armbian/armbian-files/different-files/s912-b1s"
CONFIG = (Path(__file__).parent / "fixtures/runtime.config").read_text()


def run(*args, **kwargs):
    return subprocess.run(args, text=True, capture_output=True, **kwargs)


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.workspace = tempfile.TemporaryDirectory(prefix="b1s-runtime-")
        self.addCleanup(self.workspace.cleanup)
        self.work = Path(self.workspace.name)
        self.root, self.boot = self.work / "root", self.work / "boot"
        self.root.mkdir()
        self.boot.mkdir()
        self.config = self.boot / "config-test-kernel"
        self.config.write_text(CONFIG)

    def fixture(self):
        for name in PACKAGES:
            path = self.root / "var/lib/dpkg/status"
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a") as stream:
                stream.write(f"Package: {name}\nStatus: install ok installed\nVersion: 1-test\n\n")
        for paths in COMMANDS.values():
            path = self.root / paths[0]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("fixture executable; must never run\n")
            path.chmod(0o755)
        modules = self.root / "usr/lib/modules/test-kernel"
        modules.mkdir(parents=True)
        index = []
        for option, name in MODULES.items():
            if f"CONFIG_{option}=m\n" in CONFIG:
                relative = "kernel/test/" + name + ".ko"
                path = modules / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"fixture module; must never load")
                index.append(relative + ":")
        (modules / "modules.dep").write_text("\n".join(index) + "\n")

    def install(self):
        return run("bash", str(BOARD / "install-runtime.sh"), str(self.root), str(self.boot), "test-kernel")

    def test_runtime_install_enable_hashes_and_order(self):
        self.fixture()
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = check(self.root, self.config, BOARD / "rootfs")
        self.assertTrue(report["passed"])
        self.assertEqual(report["default_usb_role"], "host")
        self.assertFalse(report["gadget_autoload"])
        self.assertEqual(set(report["packages"]), set(PACKAGES))
        for relative in ASSETS:
            self.assertEqual((self.root / relative).read_bytes(), (BOARD / "rootfs" / relative).read_bytes())
        sums = self.root / "usr/lib/b1s/runtime-assets.sha256"
        self.assertEqual(run("sha256sum", "-c", str(sums), cwd=self.root).returncode, 0)
        self.assertEqual((self.boot / "b1s-runtime-audit.json").read_bytes(),
                         (self.root / "usr/lib/b1s/runtime-audit.json").read_bytes())
        for name in SERVICES:
            text = (self.root / "etc/systemd/system" / name).read_text()
            self.assertIn("TimeoutStartSec=15s", text)
            self.assertNotIn("After=multi-user.target", text)
        script = (ROOT / "rebuild").read_text()
        loop = script[script.index("                    refactor_rootfs"):]
        self.assertLess(loop.index("install-runtime.sh"), loop.index("check_boot_files.py"))
        self.assertLess(loop.index("check_boot_files.py"), loop.index("clean_tmp"))
        self.assertIn('cp -af --no-preserve=ownership ${different_rootfs}/. ${tag_rootfs}', script)
        manifest = (BOARD / "install-dtb.sh").read_text()
        self.assertIn("find rootfs -type f", manifest)
        uenv = ROOT / "build-armbian/armbian-files/platform-files/amlogic/bootfs/uEnv.txt"
        self.assertIn("video=HDMI-A-1:1920x1080@60e", uenv.read_text())

    def test_missing_packages_executables_and_modules_rejected(self):
        self.fixture()
        status = self.root / "var/lib/dpkg/status"
        content = status.read_text()
        status.write_text(content.replace("Package: ir-keytable", "Package: absent"))
        result = self.install()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ir-keytable", result.stderr)
        status.write_text(content)
        amixer = self.root / "usr/bin/amixer"
        amixer.chmod(0o644)
        self.assertIn("amixer", self.install().stderr)
        amixer.chmod(0o755)
        module = self.root / "usr/lib/modules/test-kernel/kernel/test/g_mass_storage.ko"
        module.unlink()
        result = self.install()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("g_mass_storage", result.stderr)
        self.assertFalse((self.boot / "b1s-runtime-audit.json").exists())

    def test_modified_disabled_and_autoload_assets_rejected(self):
        self.fixture()
        self.assertEqual(self.install().returncode, 0)
        asset = self.root / ASSETS[0]
        content = asset.read_bytes()
        asset.write_bytes(content + b"# altered\n")
        with self.assertRaisesRegex(ValueError, "asset"):
            check(self.root, self.config, BOARD / "rootfs")
        asset.write_bytes(content)
        link = self.root / "etc/systemd/system/multi-user.target.wants" / SERVICES[0]
        link.unlink()
        with self.assertRaisesRegex(ValueError, "not enabled"):
            check(self.root, self.config, BOARD / "rootfs")
        link.symlink_to("../" + SERVICES[0])
        (self.root / "etc/modules").write_text("g_mass_storage\n")
        with self.assertRaisesRegex(ValueError, "autoload"):
            check(self.root, self.config, BOARD / "rootfs")

    def test_config_dependencies_and_host_only_rejected(self):
        required = list(MODULES) + ["USB_GADGET", "USB_DWC3_DUAL_ROLE"]
        for name in required:
            original = next(line for line in CONFIG.splitlines() if line.startswith(f"CONFIG_{name}="))
            self.config.write_text(CONFIG.replace(original, f"# CONFIG_{name} is not set"))
            result = run("bash", str(BOARD / "check-kernel-config.sh"), str(self.config), "stage1")
            self.assertNotEqual(result.returncode, 0, name)
        for extra in ("CONFIG_USB_DWC2_HOST=y\n",):
            self.config.write_text(CONFIG + extra)
            self.assertNotEqual(run("bash", str(BOARD / "check-kernel-config.sh"), str(self.config), "stage1").returncode, 0)
        self.config.write_text(CONFIG.replace("CONFIG_USB_MASS_STORAGE=m", "CONFIG_USB_MASS_STORAGE=y"))
        self.assertNotEqual(run("bash", str(BOARD / "check-kernel-config.sh"), str(self.config), "stage1").returncode, 0)
        self.config.write_text(CONFIG.replace("CONFIG_USB_DWC2_DUAL_ROLE=y", "# CONFIG_USB_DWC2_DUAL_ROLE is not set"))
        self.assertNotEqual(run("bash", str(BOARD / "check-kernel-config.sh"), str(self.config), "stage1").returncode, 0)

    def test_module_dependency_closure_and_live_root_guard(self):
        self.fixture()
        self.assertEqual(self.install().returncode, 0)
        index = self.root / "usr/lib/modules/test-kernel/modules.dep"
        index.write_text(index.read_text().replace("g_mass_storage.ko:", "g_mass_storage.ko: kernel/missing.ko"))
        with self.assertRaisesRegex(ValueError, "module dependency"):
            check(self.root, self.config, BOARD / "rootfs")
        result = run("bash", str(BOARD / "install-runtime.sh"), "/", str(self.boot), "test-kernel")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("refuses live", result.stderr)

    def test_image_absolute_symlinks_never_use_host_binaries(self):
        path = self.root / "usr/bin"
        path.mkdir(parents=True)
        (self.root / "bin").symlink_to("/usr/bin")
        (path / "modprobe").symlink_to("/bin/kmod")
        self.assertEqual(rooted(self.root, "bin/modprobe"), path / "kmod")
        (path / "bad").symlink_to("../../../../etc/passwd")
        with self.assertRaises(ValueError):
            rooted(self.root, "usr/bin/bad")

    def test_remote_exact_twelve_keys(self):
        data = tomllib.loads((BOARD / "rootfs/etc/rc_keymaps/b1s.toml").read_text())
        protocol, = data["protocols"]
        self.assertEqual((protocol["protocol"], protocol["variant"]), ("nec", "necx"))
        names = ("POWER", "VOICECOMMAND", "UP", "LEFT", "OK", "RIGHT", "DOWN", "BACK", "HOME", "MENU", "VOLUMEUP", "VOLUMEDOWN")
        self.assertEqual(protocol["scancodes"], {hex(0x810201 + i): "KEY_" + name for i, name in enumerate(names)})

    def mock_script(self, name):
        # Rewrite only a test copy, never expose real sysfs to role tests.
        script = self.work / name
        text = (BOARD / "rootfs/usr/local/sbin" / name).read_text()
        text = text.replace("/sys/", str(self.work / "sys") + "/").replace("/etc/rc_keymaps/", str(self.work / "keymaps") + "/")
        script.write_text(text)
        tools = self.work / "tools"
        tools.mkdir(exist_ok=True)
        self.log = self.work / "calls.log"
        for command in ("sh", "cat", "readlink", "basename"):
            dest = tools / command
            if not dest.exists():
                dest.symlink_to(shutil.which(command))
        (tools / "id").write_text("#!/bin/sh\necho 0\n")
        (tools / "sleep").write_text("#!/bin/sh\nexit 0\n")
        for command in ("id", "sleep"):
            (tools / command).chmod(0o755)
        self.env = {**os.environ, "PATH": str(tools), "CALL_LOG": str(self.log)}
        return script

    def usb_fixture(self):
        script = self.mock_script("b1s-usb-role")
        role = self.work / "sys/class/usb_role/d0078080.usb-role-switch/role"
        role.parent.mkdir(parents=True)
        role.write_text("host\n")
        for driver in ("dwc2", "dwc3"):
            path = self.work / "sys/bus/platform/drivers" / driver
            path.mkdir(parents=True)
            (path / "bind").touch()
            (path / "unbind").touch()
        udc = self.work / "sys/class/udc/c9100000.usb"
        udc.mkdir(parents=True)
        (udc / "function").write_text("")
        return script, role, udc

    def test_usb_status_confirmation_and_storage_safety(self):
        script, role, udc = self.usb_fixture()
        self.assertEqual(run("sh", str(script), env=self.env).stdout, "role=host\nudc=c9100000.usb \n")
        self.assertNotEqual(run("sh", str(script), "device", env=self.env).returncode, 0)
        self.assertEqual(role.read_text(), "host\n")
        block = self.work / "sys/class/block"
        block.mkdir(parents=True)
        attached = self.work / "sys/devices/d0078080.usb/sda"
        attached.mkdir(parents=True)
        (block / "sda").symlink_to(attached)
        result = run("sh", str(script), "device", "--confirm-disconnected", env=self.env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("storage is attached", result.stderr)
        self.assertEqual(role.read_text(), "host\n")
        (block / "sda").unlink()
        self.assertEqual(run("sh", str(script), "device", "--confirm-disconnected", env=self.env).returncode, 0)
        self.assertEqual(role.read_text(), "device\n")
        self.assertEqual((self.work / "sys/bus/platform/drivers/dwc2/bind").read_text(), "")

    def test_usb_gadget_guard_and_explicit_host_recovery(self):
        script, role, udc = self.usb_fixture()
        role.write_text("device\n")
        (udc / "function").write_text("Mass Storage Gadget\n")
        result = run("sh", str(script), "host", "--confirm-disconnected", env=self.env)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(role.read_text(), "device\n")
        (udc / "function").write_text("")
        module = self.work / "sys/module/g_mass_storage"
        module.mkdir(parents=True)
        self.assertNotEqual(run("sh", str(script), "host", "--confirm-disconnected", env=self.env).returncode, 0)
        module.rmdir()
        self.assertEqual(run("sh", str(script), "host", "--confirm-disconnected", env=self.env).returncode, 0)
        xdrv = self.work / "sys/bus/platform/drivers/dwc3"
        self.assertEqual((xdrv / "unbind").read_text(), "")
        result = run("sh", str(script), "host", "--confirm-disconnected", "--recover-xhci", env=self.env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((xdrv / "unbind").read_text(), "c9000000.usb\n")
        self.assertEqual((xdrv / "bind").read_text(), "c9000000.usb\n")
        # A write failure must stop and be visible, never be hidden by || true.
        (xdrv / "bind").unlink()
        (xdrv / "bind").mkdir()
        self.assertNotEqual(run("sh", str(script), "host", "--confirm-disconnected", "--recover-xhci", env=self.env).returncode, 0)

    def test_usb_bind_unbind_failures_and_no_auto_gadget(self):
        script, role, udc = self.usb_fixture()
        (udc / "function").unlink()
        udc.rmdir()
        drv = self.work / "sys/bus/platform/drivers/dwc2"
        (drv / "bind").unlink()
        (drv / "bind").mkdir()
        result = run("sh", str(script), "device", "--confirm-disconnected", env=self.env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("bind failed", result.stderr)
        self.assertEqual(role.read_text(), "device\n")
        (drv / "bind").rmdir()
        (drv / "bind").touch()
        result = run("sh", str(script), "device", "--confirm-disconnected", env=self.env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("did not appear", result.stderr)
        udc.mkdir()
        (drv / "unbind").unlink()
        (drv / "unbind").mkdir()
        self.assertNotEqual(run("sh", str(script), "host", "--confirm-disconnected", env=self.env).returncode, 0)
        self.assertEqual(role.read_text(), "device\n")
        self.assertNotIn("modprobe", script.read_text())
        self.assertNotIn("|| true", script.read_text())

    def test_ir_dynamic_device_and_audio_wait_retry(self):
        ir = self.mock_script("b1s-ir-keymap.sh")
        keymap = self.work / "keymaps/b1s.toml"
        keymap.parent.mkdir()
        keymap.write_bytes((BOARD / "rootfs" / ASSETS[0]).read_bytes())
        driver = self.work / "sys/bus/platform/drivers/meson-ir"
        driver.mkdir(parents=True)
        rc = self.work / "sys/class/rc/rc7/device"
        rc.mkdir(parents=True)
        (rc / "driver").symlink_to(driver)
        tools = self.work / "tools"
        (tools / "ir-keytable").write_text('#!/bin/sh\nprintf "%s\\n" "$*" >"$CALL_LOG"\n')
        (tools / "ir-keytable").chmod(0o755)
        result = run("sh", str(ir), env=self.env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("-s rc7 -p nec -w", self.log.read_text())
        (rc / "driver").unlink()
        self.assertNotEqual(run("sh", str(ir), env=self.env).returncode, 0)
        audio = self.mock_script("b1s-hdmi-audio.sh")
        amixer = tools / "amixer"
        amixer.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >>"$CALL_LOG"\n[ "$3" != sget ] || [ -f "$CALL_LOG.ready" ] || { : >"$CALL_LOG.ready"; exit 1; }\n')
        amixer.chmod(0o755)
        self.assertEqual(run("sh", str(audio), env=self.env).returncode, 0)
        self.assertIn("-c 0 sset AIU HDMI CTRL SRC I2S", self.log.read_text())
        amixer.write_text('#!/bin/sh\nexit 1\n')
        self.assertNotEqual(run("sh", str(audio), env=self.env).returncode, 0)

    def test_runtime_scripts_missing_commands_fail_clearly(self):
        for name, message in (("b1s-ir-keymap.sh", "install ir-keytable"),
                              ("b1s-hdmi-audio.sh", "install alsa-utils")):
            script = self.mock_script(name)
            result = run("sh", str(script), env=self.env)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(message, result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
