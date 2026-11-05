#!/usr/bin/env python3
"""Offline image integration and mocked sysfs: no live role/service changes."""
import os
import shutil
import subprocess
import tempfile
import tomllib
import unittest
from pathlib import Path

from check_runtime import ASSETS, COMMANDS, MODULES, PACKAGES, SERVICES, check, installed_packages, rooted

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
        self.assertLess(loop.index("install-runtime-dependencies.sh"), loop.index("install-runtime.sh"))
        self.assertIn('${B1S_INSTALL_RUNTIME_PACKAGES:-no}', loop)
        self.assertLess(loop.index("install-runtime.sh"), loop.index("check_boot_files.py"))
        self.assertLess(loop.index("check_boot_files.py"), loop.index("clean_tmp"))
        self.assertIn('cp -af --no-preserve=ownership ${different_rootfs}/. ${tag_rootfs}', script)
        manifest = (BOARD / "install-dtb.sh").read_text()
        self.assertIn("find rootfs -type f", manifest)
        self.assertIn("install-runtime-dependencies.sh", manifest)
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


class DependencyInstallerTests(unittest.TestCase):
    """No real apt/chroot/mount: allowlisted tools plus explicitly mocked commands."""

    def setUp(self):
        self.workspace = tempfile.TemporaryDirectory(prefix="b1s-deps-")
        self.addCleanup(self.workspace.cleanup)
        self.work = Path(self.workspace.name)
        self.root = self.work / "root"
        for directory in ("etc", "usr/bin", "usr/sbin", "var/lib/dpkg", "dev", "proc"):
            (self.root / directory).mkdir(parents=True, exist_ok=True)
        self.status = self.root / "var/lib/dpkg/status"
        self.status.write_text("".join(f"Package: {name}\nStatus: install ok installed\nVersion: 1-test\n\n"
                                       for name in PACKAGES if name != "ir-keytable"))
        apt = self.root / "usr/bin/apt-get"
        apt.write_text("must never execute target programs in tests\n")
        apt.chmod(0o755)
        self.dns = self.root / "etc/resolv.conf"
        self.policy = self.root / "usr/sbin/policy-rc.d"
        self.dns.write_text("original DNS\n")
        self.policy.write_text("#!/bin/sh\nexit 0\n")
        self.policy.chmod(0o750)
        self.log = self.work / "calls.log"
        self.registration = self.work / "qemu-aarch64"
        self.registration.write_text("enabled\nflags: POCF\n")
        self.host_dns = self.work / "host-dns"
        self.host_dns.write_text("nameserver 192.0.2.1\n")
        self.script = self.work / "installer.sh"
        text = (BOARD / "install-runtime-dependencies.sh").read_text()
        text = text.replace('repo_dir="$(cd -- "${board_dir}/../../../.." && pwd)"', f'repo_dir="{ROOT}"')
        text = text.replace("/proc/sys/fs/binfmt_misc/qemu-aarch64", str(self.registration))
        text = text.replace("cp -L -- /etc/resolv.conf", f'cp -L -- "{self.host_dns}"')
        self.script.write_text(text)
        tools = self.work / "tools"
        tools.mkdir()
        # A missing mock cannot fall through to host mount/chroot/apt.
        for command in ("python3", "dirname", "realpath", "grep", "mktemp", "mkdir", "mv", "rm", "cp", "chmod", "rmdir"):
            (tools / command).symlink_to(shutil.which(command))
        self.env = {**os.environ, "PATH": str(tools), "CALL_LOG": str(self.log),
                    "MOCK_UID": "0", "MOCK_ARCH": "x86_64", "FAIL_AT": ""}
        self.bash = shutil.which("bash")
        for command, body in {
            "id": 'echo "${MOCK_UID}"',
            "uname": 'echo "${MOCK_ARCH}"',
            "mountpoint": '[ "${FAIL_AT}" = premounted ]',
            "mount": 'printf "mount %s\\n" "$*" >>"$CALL_LOG"\n'
                     'case "$*" in\n--bind*) [ "$FAIL_AT" != mount-dev ] ;;\n'
                     '--make-private*) [ "$FAIL_AT" != private-dev ] ;;\n'
                     '*) [ "$FAIL_AT" != mount-proc ] ;;\nesac',
            "umount": 'printf "umount %s\\n" "$*" >>"$CALL_LOG"\n[ "$FAIL_AT" != umount ]',
            "chroot": 'python3 - "$@" <<\'PY\'\n'
                      'import os, sys\nfrom pathlib import Path\n'
                      'root = Path(sys.argv[1]); args = sys.argv[2:]\n'
                      'assert (root / "usr/sbin/policy-rc.d").read_text() == "#!/bin/sh\\nexit 101\\n"\n'
                      'assert (root / "usr/sbin/policy-rc.d").stat().st_mode & 0o777 == 0o755\n'
                      'assert (root / "etc/resolv.conf").read_text() == "nameserver 192.0.2.1\\n"\n'
                      'with open(os.environ["CALL_LOG"], "a") as stream: stream.write("chroot " + " ".join(args) + "\\n")\n'
                      'stage = "update" if args[-1] == "update" else "install"\n'
                      'if os.environ["FAIL_AT"] == stage: sys.exit(42)\n'
                      'if stage == "install":\n'
                      '    assert args[args.index("install") + 1:] == ["ir-keytable"]\n'
                      '    if os.environ["FAIL_AT"] != "still-missing":\n'
                      '        with (root / "var/lib/dpkg/status").open("a") as stream:\n'
                      '            stream.write("Package: ir-keytable\\nStatus: install ok installed\\nVersion: 2-test\\n\\n")\n'
                      'PY',
        }.items():
            path = tools / command
            path.write_text(f"#!{self.bash}\n" + body + "\n")
            path.chmod(0o755)

    def install(self, **env):
        return run(self.bash, str(self.script), str(self.root), env={**self.env, **env})

    def assert_restored(self):
        self.assertEqual(self.dns.read_text(), "original DNS\n")
        self.assertEqual(self.policy.read_text(), "#!/bin/sh\nexit 0\n")
        self.assertEqual(self.policy.stat().st_mode & 0o777, 0o750)
        self.assertFalse(list(self.work.glob(".b1s-deps.*")))

    def test_missing_packages_and_held_installed_offline_query(self):
        self.status.write_text(self.status.read_text().replace("install ok installed", "hold ok installed"))
        self.assertEqual(set(installed_packages(self.root)), set(PACKAGES) - {"ir-keytable"})
        result = run("python3", str(ROOT / "tools/b1s/check_runtime.py"), "--missing-packages", str(self.root))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "ir-keytable\n")
        self.status.write_text(self.status.read_text().replace("hold ok installed", "install ok unpacked"))
        self.assertEqual(installed_packages(self.root), {})

    def test_install_missing_only_and_restore(self):
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(set(installed_packages(self.root)), set(PACKAGES))
        calls = self.log.read_text().splitlines()
        self.assertEqual(sum(line.startswith("chroot ") for line in calls), 2)
        self.assertTrue(calls[-2].endswith("/proc"))
        self.assertTrue(calls[-1].endswith("/dev"))
        self.assertNotIn("/sys", self.log.read_text())
        self.assert_restored()

    def test_already_installed_needs_no_root_binfmt_or_chroot(self):
        with self.status.open("a") as stream:
            stream.write("Package: ir-keytable\nStatus: hold ok installed\nVersion: 2-test\n\n")
        self.registration.unlink()
        (self.root / "usr/bin/apt-get").unlink()
        result = self.install(MOCK_UID="1000")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("no chroot needed", result.stdout)
        self.assertFalse(self.log.exists())
        self.assert_restored()

    def test_apt_and_mount_failures_restore(self):
        for failure, unmounts in (("update", 2), ("install", 2), ("still-missing", 2),
                                  ("mount-dev", 0), ("private-dev", 1), ("mount-proc", 1), ("umount", 2)):
            with self.subTest(failure=failure):
                if self.log.exists():
                    self.log.unlink()
                original = self.status.read_text()
                result = self.install(FAIL_AT=failure)
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(sum(line.startswith("umount ") for line in self.log.read_text().splitlines()), unmounts)
                self.assert_restored()
                self.status.write_text(original)

    def test_dns_copy_failure_restores_without_mounting(self):
        self.host_dns.unlink()
        result = self.install()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.log.exists())
        self.assert_restored()

    def test_symlink_and_absent_leaf_files_preserved(self):
        self.dns.unlink()
        self.policy.unlink()
        self.dns.symlink_to("/run/systemd/resolve/stub-resolv.conf")
        self.policy.symlink_to("../../lib/policy-original")
        for failure in ("install", ""):
            result = self.install(FAIL_AT=failure)
            self.assertEqual(result.returncode == 0, not failure, result.stdout + result.stderr)
            self.assertEqual(str(self.dns.readlink()), "/run/systemd/resolve/stub-resolv.conf")
            self.assertEqual(str(self.policy.readlink()), "../../lib/policy-original")
            self.assertFalse(list(self.work.glob(".b1s-deps.*")))
        self.dns.unlink()
        self.policy.unlink()
        self.status.write_text(self.status.read_text().split("Package: ir-keytable")[0])
        self.assertEqual(self.install().returncode, 0)
        self.assertFalse(self.dns.exists())
        self.assertFalse(self.policy.exists())

    def test_binfmt_privilege_mount_and_live_path_guards(self):
        self.registration.unlink()
        self.assertIn("binfmt", self.install().stderr)
        for content in ("", "disabled\nflags: F\n", "enabled\nflags: OC\n"):
            self.registration.write_text(content)
            result = self.install()
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("binfmt", result.stderr)
            self.assertFalse(self.log.exists())
        self.registration.write_text("enabled\nflags: F\n")
        self.assertIn("requires root", self.install(MOCK_UID="1000").stderr)
        self.assertIn("pre-mounted", self.install(FAIL_AT="premounted").stderr)
        for root in ("/", "/root", "/etc", "/usr", "/boot", "/var"):
            result = run(self.bash, str(self.script), root, env=self.env)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("refuses live", result.stderr)
        link = self.work / "live-root-link"
        link.symlink_to("/")
        self.assertIn("refuses live", run(self.bash, str(self.script), str(link), env=self.env).stderr)
        (self.root / "dev").rmdir()
        (self.root / "dev").symlink_to("/dev")
        self.assertIn("Unsafe staged", self.install().stderr)
        self.assertFalse(self.log.exists())
        self.assert_restored()

    def test_native_arm64_and_missing_target_apt(self):
        apt = self.root / "usr/bin/apt-get"
        apt.chmod(0o644)
        self.assertIn("apt-get is missing", self.install().stderr)
        self.assertFalse(self.log.exists())
        apt.chmod(0o755)
        self.registration.unlink()
        result = self.install(MOCK_ARCH="aarch64")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_restored()

    def test_ci_opt_in_and_offline_defaults(self):
        import yaml
        action = yaml.safe_load((ROOT / "action.yml").read_text())
        self.assertEqual(action["inputs"]["install_b1s_dependencies"]["default"], "false")
        self.assertIn('B1S_INSTALL_RUNTIME_PACKAGES="${b1s_install_packages}"', (ROOT / "action.yml").read_text())
        workflow = (ROOT / ".github/workflows/build-armbian-using-releases-files.yml").read_text()
        self.assertIn('install_b1s_dependencies: "true"', workflow)
        self.assertIn("install qemu-user-static binfmt-support", workflow)
        self.assertIn("update-binfmts --enable qemu-aarch64", workflow)


if __name__ == "__main__":
    unittest.main(verbosity=2)
