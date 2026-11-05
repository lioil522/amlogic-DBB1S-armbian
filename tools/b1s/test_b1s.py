#!/usr/bin/env python3
"""Off-device regression checks for DTB, packaging and update contracts."""
import gzip
import hashlib
import json
import subprocess
import struct
import sys
import tarfile
import tempfile
import unittest
import zlib
from pathlib import Path

from dtb_tools import Tree, cells, differences
from check_boot_files import check as check_boot_files

ROOT = Path(__file__).resolve().parents[2]
BOARD = ROOT / "build-armbian/armbian-files/different-files/s912-b1s"
GOLDEN = Path(__file__).parent / "fixtures/known-good.dtb"
CONFIG = (Path(__file__).parent / "fixtures/runtime.config").read_text()


def run(*args, **kwargs):
    return subprocess.run(args, text=True, capture_output=True, **kwargs)


class B1STests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workspace = tempfile.TemporaryDirectory(prefix="b1s-test-")
        cls.path = Path(cls.workspace.name)
        cls.config = cls.path / "config"
        cls.config.write_text(CONFIG)
        cls.profiles = {}
        for profile in ("baseline", "power-key", "gpu", "hdmi", "core", "stage1", "usb-vbus", "no-adc-keys"):
            output = cls.path / (profile + ".dtb")
            result = run("bash", str(BOARD / "build-dtb.sh"), profile, str(output), str(cls.config))
            if result.returncode:
                raise RuntimeError(result.stdout + result.stderr)
            cls.profiles[profile] = Tree(output)

    @classmethod
    def tearDownClass(cls):
        cls.workspace.cleanup()

    def test_baseline_semantics(self):
        self.assertEqual(hashlib.sha256(GOLDEN.read_bytes()).hexdigest(), "7a8e99758a54d22c670737d5226058f37056b360d4ea29f556cda19eda7280a0")
        self.assertEqual(differences(Tree(GOLDEN), self.profiles["baseline"]), [])

    def test_changes_are_isolated(self):
        allowed = {
            "power-key": ["/gpio-keys", "/soc/bus@c8100000/pinctrl@14/power-key-pins"],
            "gpu": ["/soc/apb@d0000000/gpu@c0000", "/thermal-zones/cpu-thermal/cooling-maps/map-gpu"],
            "hdmi": ["/regulator-hdmi-5v"],
            "no-adc-keys": ["/adc-keys"],
            "usb-vbus": ["/regulator-usb-vbus", "/soc/usb@d0078080"],
        }
        for profile, paths in allowed.items():
            delta = differences(self.profiles["baseline"], self.profiles[profile])
            self.assertTrue(delta)
            for change in delta:
                self.assertTrue(any(change["path"] == p or change["path"].startswith(p + "/") for p in paths), change)
        single_changes = {json.dumps(c, sort_keys=True) for p in ("power-key", "gpu", "hdmi", "no-adc-keys", "usb-vbus") for c in differences(self.profiles["baseline"], self.profiles[p])}
        combined = {json.dumps(c, sort_keys=True) for c in differences(self.profiles["baseline"], self.profiles["stage1"])}
        self.assertEqual(single_changes, combined)

    def test_driver_properties(self):
        tree = self.profiles["stage1"]
        self.assertEqual(differences(Tree(BOARD / "bootfs/dtb/amlogic/meson-gxm-dangbei-b1s.dtb"), tree), [])
        nodes = tree.canonical()["nodes"]
        self.assertEqual(tree.nodes["/gpio-keys"]["compatible"], b"gpio-keys-polled\0")
        self.assertEqual(cells(tree.nodes["/gpio-keys"]["poll-interval"]), [20])
        self.assertEqual(nodes["/gpio-keys/key-power"]["gpios"][1:], [2, 1])
        self.assertEqual(nodes["/regulator-hdmi-5v"]["gpio"][1:], [19, 1])
        self.assertNotIn("enable-active-high", tree.nodes["/regulator-hdmi-5v"])
        self.assertEqual(tree.nodes["/adc-keys"]["status"], b"disabled\0")
        cooling = nodes["/thermal-zones/cpu-thermal/cooling-maps/map-gpu"]
        self.assertEqual(cooling["trip"], ["/thermal-zones/cpu-thermal/trips/cpu-passive"])
        self.assertEqual(cooling["cooling-device"], ["/soc/apb@d0000000/gpu@c0000", 0xffffffff, 0xffffffff])

    def test_usb_formal_and_isolated_preserve_device_mode(self):
        for profile in ("usb-vbus", "stage1"):
            tree = self.profiles[profile]
            nodes = tree.canonical()["nodes"]
            self.assertEqual(tree.nodes["/soc/usb@d0078080"]["dr_mode"], b"otg\0")
            self.assertEqual(tree.nodes["/soc/usb@d0078080/usb@c9100000"]["dr_mode"], b"peripheral\0")
            regulator = tree.nodes["/regulator-usb-vbus"]
            self.assertNotIn("regulator-always-on", regulator)
            self.assertNotIn("regulator-boot-on", regulator)
            self.assertIn("enable-active-high", regulator)
            self.assertEqual(nodes["/regulator-usb-vbus"]["gpio"][1:], [5, 0])
            self.assertEqual(nodes["/soc/usb@d0078080"]["vbus-supply"], ["/regulator-usb-vbus"])

    def test_missing_driver_and_uncalibrated_adc_rejected(self):
        for profile, content in (("stage1", CONFIG.replace("CONFIG_KEYBOARD_GPIO_POLLED=y", "# CONFIG_KEYBOARD_GPIO_POLLED is not set")), ("baseline", CONFIG + "CONFIG_KEYBOARD_ADC=y\n")):
            config = self.path / "bad-config"
            config.write_text(content)
            result = run("bash", str(BOARD / "build-dtb.sh"), profile, str(self.path / "bad.dtb"), str(config))
            self.assertNotEqual(result.returncode, 0)
        result = run("bash", str(BOARD / "check-kernel-config.sh"), str(self.config), "adc")
        self.assertNotEqual(result.returncode, 0)

    def test_disabled_adc_accepts_adc_enabled_kernel(self):
        config = self.path / "adc-enabled-config"
        config.write_text(CONFIG + "CONFIG_KEYBOARD_ADC=y\n")
        for profile in ("stage1", "no-adc-keys"):
            output = self.path / (profile + "-adc-enabled.dtb")
            result = run("bash", str(BOARD / "build-dtb.sh"), profile, str(output), str(config))
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(Tree(output).nodes["/adc-keys"]["status"], b"disabled\0")

    def test_removable_boot_files_and_negative_cases(self):
        def legacy(payload, image_type):
            header = bytearray(64)
            struct.pack_into('>I', header, 0, 0x27051956)
            struct.pack_into('>I', header, 12, len(payload))
            struct.pack_into('>I', header, 24, zlib.crc32(payload))
            header[30] = image_type
            struct.pack_into('>I', header, 4, zlib.crc32(header))
            return bytes(header) + payload

        with tempfile.TemporaryDirectory() as work:
            boot = Path(work)
            config = 'LINUX=/zImage\nINITRD=/uInitrd\nFDT=/b1s.dtb\nAPPEND=root=UUID=test-root rw\n'
            (boot / 'uEnv.txt').write_text(config)
            image = bytearray(64)
            # BSS makes the declared memory span larger than the on-disk Image.
            struct.pack_into('<QQ', image, 8, 0x1080000, 128)
            image[56:60] = b'ARM\x64'
            (boot / 'zImage').write_bytes(image)
            (boot / 'b1s.dtb').write_bytes(GOLDEN.read_bytes())
            script = b'fatload usb 0 0 uEnv.txt; booti ${LINUX} ${INITRD} ${FDT}\n'
            compiled = legacy(struct.pack('>II', len(script), 0) + script, 6)
            (boot / 's905_autoscript').write_bytes(compiled)
            initrd = legacy(b'fixture initrd', 3)
            (boot / 'uInitrd').write_bytes(initrd)
            self.assertTrue(check_boot_files(boot, 'test-root')['passed'])
            with self.assertRaises(ValueError):
                check_boot_files(boot, 'wrong-root')
            (boot / 's905_autoscript').unlink()
            with self.assertRaises(FileNotFoundError):
                check_boot_files(boot, 'test-root')
            (boot / 's905_autoscript').write_bytes(compiled[:-1] + b'x')
            with self.assertRaises(ValueError):
                check_boot_files(boot, 'test-root')
            (boot / 's905_autoscript').write_bytes(compiled)
            (boot / 's905_autoscript.cmd').write_bytes(script + b'changed source')
            with self.assertRaises(ValueError):
                check_boot_files(boot, 'test-root')
            (boot / 's905_autoscript.cmd').write_bytes(script)
            (boot / 'zImage').write_bytes(gzip.compress(image))
            with self.assertRaises(ValueError):
                check_boot_files(boot, 'test-root')
            image[8:16] = b'\0' * 8
            (boot / 'zImage').write_bytes(image)
            with self.assertRaises(ValueError):
                check_boot_files(boot, 'test-root')
            (boot / 'u-boot.ext').write_bytes(b'overload')
            (boot / 'u-boot-s905x-s912.bin').write_bytes(b'overload')
            self.assertTrue(check_boot_files(boot, 'test-root')['passed'])
            (boot / 'uInitrd').write_bytes(initrd[:-1])
            with self.assertRaises(ValueError):
                check_boot_files(boot, 'test-root')
            (boot / 'uInitrd').write_bytes(initrd)
            (boot / 'b1s.dtb').write_bytes(GOLDEN.read_bytes().replace(b'dangbei,b1s', b'invalid,b1s'))
            with self.assertRaises(ValueError):
                check_boot_files(boot, 'test-root')
            (boot / 'b1s.dtb').write_bytes(GOLDEN.read_bytes())
            (boot / 'uEnv.txt').write_text(config.replace('LINUX=/zImage', 'LINUX=/../outside'))
            with self.assertRaises(ValueError):
                check_boot_files(boot, 'test-root')

    def test_board_dtb_wins_over_kernel_archive(self):
        with tempfile.TemporaryDirectory() as work:
            work = Path(work)
            boot, root, kernel = work / "boot", work / "root", work / "kernels/stable/6.18.48"
            for path in (boot, root / "usr/lib/modules", kernel):
                path.mkdir(parents=True)
            name = "6.18.48-ophub"
            contents = {
                "boot": {f"config-{name}": CONFIG.encode(), f"vmlinuz-{name}": b"kernel", f"uInitrd-{name}": b"initrd"},
                "dtb-amlogic": {"meson-gxm-dangbei-b1s.dtb": GOLDEN.read_bytes(), "another-board.dtb": GOLDEN.read_bytes()},
                "header": {"include/test.h": b""},
                "modules": {f"{name}/kernel/test.ko": b""},
            }
            for kind, files in contents.items():
                source = work / ("archive-" + kind)
                source.mkdir()
                for file, data in files.items():
                    dest = source / file
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(data)
                with tarfile.open(kernel / f"{kind}-{name}.tar.gz", "w:gz") as archive:
                    for child in source.iterdir():
                        archive.add(child, arcname=child.name)
            script = '''source ./rebuild
current_path="$PWD"
kernel_path="$1/kernels"
kernel=6.18.48
conf_kernel_tags=stable
PLATFORM=amlogic
board=s912-b1s
tag_bootfs="$1/boot"
tag_rootfs="$1/root"
different_files="$PWD/build-armbian/armbian-files/different-files"
different_bootfs="$different_files/$board/bootfs"
adjust_kernel_files=no
replace_kernel
'''
            result = run("bash", "-c", script, "test", str(work), cwd=ROOT)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(differences(Tree(boot / "dtb/amlogic/meson-gxm-dangbei-b1s.dtb"), self.profiles["stage1"]), [])
            self.assertTrue((root / "usr/lib/b1s/build-manifest.txt").is_file())

    def test_same_name_update_and_firmware_restoration(self):
        with tempfile.TemporaryDirectory() as work:
            work = Path(work)
            boot, firmware, snapshot = work / "boot", work / "firmware", work / "snapshot"
            target = boot / "dtb/amlogic/meson-gxm-dangbei-b1s-core.dtb"
            target.parent.mkdir(parents=True)
            target.write_bytes((self.path / "core.dtb").read_bytes())
            firmware.mkdir()
            hcd = firmware / "BCM4345C0.hcd"
            hcd.write_bytes(b"selected-patch")
            helper = BOARD / "rootfs/usr/lib/b1s/preserve-assets.sh"
            args = [str(boot), str(firmware), str(snapshot), target.name]
            self.assertEqual(run("bash", str(helper), "save", *args).returncode, 0)
            target.write_bytes(b"kernel replacement")
            hcd.write_bytes(b"replacement patch")
            result = run("bash", str(helper), "restore", *args)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(target.read_bytes(), (self.path / "core.dtb").read_bytes())
            self.assertEqual(hcd.read_bytes(), b"selected-patch")
            self.assertEqual((snapshot / "kernel-provided.dtb").read_bytes(), b"kernel replacement")
            (snapshot / target.name).write_bytes(b"corrupted backup")
            self.assertNotEqual(run("bash", str(helper), "restore", *args).returncode, 0)

    def test_edition_selection(self):
        releases = []
        for edition, date in (("server", "2026-11-01"), ("desktop", "2026-11-02")):
            releases.append({"tag_name": f"Armbian_bookworm_arm64_{edition}_2026.11", "assets": [{"name": f"Armbian-trunk_{edition}.img.gz", "url": edition, "updated_at": date}]})
        for edition in ("server", "desktop"):
            result = run("jq", "-er", "--arg", "release", "bookworm", "--arg", "edition", edition, "-f", str(ROOT / "tools/select-armbian-release.jq"), input=json.dumps(releases))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["url"], edition)
        result = run("jq", "-er", "--arg", "release", "trixie", "--arg", "edition", "server", "-f", str(ROOT / "tools/select-armbian-release.jq"), input=json.dumps(releases))
        self.assertNotEqual(result.returncode, 0)

    def test_rootfs_edition_detection(self):
        with tempfile.TemporaryDirectory() as work:
            work = Path(work)
            (work / "etc").mkdir()
            helper = ROOT / "tools/detect-armbian-edition.sh"
            self.assertEqual(run("bash", str(helper), str(work)).stdout.strip(), "server")
            (work / "etc/armbian-release").write_text("BUILD_DESKTOP='yes'\n")
            self.assertEqual(run("bash", str(helper), str(work)).stdout.strip(), "desktop")
            (work / "etc/armbian-release").unlink()
            session = work / "usr/share/wayland-sessions/test.desktop"
            session.parent.mkdir(parents=True)
            session.touch()
            self.assertEqual(run("bash", str(helper), str(work)).stdout.strip(), "desktop")

    def test_artifact_manifest_empty_corrupt_and_valid(self):
        with tempfile.TemporaryDirectory() as work:
            work = Path(work)
            tool = str(ROOT / "tools/verify-armbian-output.py")
            manifest = work / "artifacts.tsv"
            header = "board\trelease\tkernel\tedition\tfile\tsha256\n"
            manifest.write_text(header)
            (work / "old-input.img").write_bytes(b"input must not count as output")
            self.assertNotEqual(run(sys.executable, tool, str(work)).returncode, 0)
            name = "Armbian_26.05.0_amlogic_s912-b1s_bookworm_6.18.48_server_2026.11.02.img.gz"
            image = work / name
            image.write_bytes(gzip.compress(b"fixture disk contents"))
            digest = hashlib.sha256(image.read_bytes()).hexdigest()
            row = f"s912-b1s\tbookworm\t6.18.48\tserver\t{name}\t{digest}\n"
            manifest.write_text(header + row)
            result = run(sys.executable, tool, str(work))
            self.assertEqual(result.returncode, 0, result.stderr)
            manifest.write_text(header + row.replace("\tserver\t", "\tdesktop\t"))
            self.assertNotEqual(run(sys.executable, tool, str(work)).returncode, 0)
            manifest.write_text(header + row)
            image.write_bytes(image.read_bytes()[:-8])
            self.assertNotEqual(run(sys.executable, tool, str(work)).returncode, 0)
            truncated_digest = hashlib.sha256(image.read_bytes()).hexdigest()
            manifest.write_text(header + row.replace(digest, truncated_digest))
            self.assertNotEqual(run(sys.executable, tool, str(work)).returncode, 0)

    def test_ambiguous_inputs_and_reused_outputs_rejected(self):
        with tempfile.TemporaryDirectory() as work:
            work = Path(work)
            inputs, output = work / "input", work / "output"
            inputs.mkdir()
            original = inputs / "Armbian_26.05.0-trunk_bookworm.img"
            original.write_bytes(b"input image")
            script = '''source ./rebuild
armbian_inputpath="$1"
armbian_outputpath="$2"
find_armbian
'''
            args = ("bash", "-c", script, "test", str(inputs), str(output))
            self.assertEqual(run(*args, cwd=ROOT).returncode, 0)
            self.assertNotEqual(run(*args, cwd=ROOT).returncode, 0)
            self.assertEqual(original.read_bytes(), b"input image")
            (inputs / "Armbian_26.05.0-trunk_other.img").write_bytes(b"second image")
            self.assertNotEqual(run(*args, cwd=ROOT).returncode, 0)

    def test_all_builds_skipped_is_failure(self):
        script = '''source ./rebuild
build_armbian=(s912-b1s)
confirm_version() { build_kernel=(6.18.48); }
df() { printf 'Filesystem 1024-blocks Used Available Capacity Mounted\\nfixture 100 99 1 99%% /\\n'; }
loop_rebuild
'''
        result = run("bash", "-c", script, cwd=ROOT)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Built 0/1", result.stdout)

    def test_sfe_headers_use_source_not_output_tree(self):
        with tempfile.TemporaryDirectory() as work:
            work = Path(work)
            source, output = work / "source", work / "output"
            relative = Path("drivers/net/ethernet/qualcomm/sfe/fast-classifier")
            classifier = source / relative
            headers = classifier.parent / "shortcut-fe"
            classifier.mkdir(parents=True)
            headers.mkdir()
            (output / relative).mkdir(parents=True)
            makefile = classifier / "Makefile"
            makefile.write_text('''#
## Makefile for Fast classifier
#
obj-m += fast-classifier.o

ifeq ($(CONFIG_SFE_SUPPORT_IPV6),y)
    ccflags-$(CONFIG_SFE_SUPPORT_IPV6) += -DSFE_SUPPORT_IPV6
endif

ccflags-y += -I$(obj)/../shortcut-fe
ccflags-y += -Werror -Wall -Wno-unused-but-set-parameter
''')
            (headers / "sfe_backport.h").write_text("#define SFE_HEADER_FOUND 1\n")
            (classifier / "probe.c").write_text("#include <sfe_backport.h>\nint probe(void) { return SFE_HEADER_FOUND; }\n")
            wrapper = output / "probe.mk"
            wrapper.write_text('''include $(src)/Makefile
.PHONY: probe
probe:
	$(CC) $(ccflags-y) -fsyntax-only $(src)/probe.c
''')
            args = ("make", "-f", str(wrapper), "probe", f"src={classifier}",
                    f"obj={relative}", f"srctree={source}", "CONFIG_SFE_SUPPORT_IPV6=y")
            self.assertNotEqual(run(*args, cwd=output).returncode, 0)
            patch = BOARD / "kernel/patches/0002-sfe-fast-classifier-use-source-include-path.patch"
            reference = json.loads((BOARD / "kernel/reference.json").read_text())
            recorded = next(item for item in reference["patches"] if item["file"] == "patches/" + patch.name)
            self.assertEqual(hashlib.sha256(patch.read_bytes()).hexdigest(), recorded["sha256"])
            result = run("patch", "--batch", "--forward", "--fuzz=0", "-p1", "-d", str(source), input=patch.read_text())
            self.assertEqual(result.returncode, 0, result.stderr)
            result = run(*args, cwd=output)
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
