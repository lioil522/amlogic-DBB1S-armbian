#!/usr/bin/env python3
"""Offline cloud build contracts; no kernel compilation or GPU operations."""
import os
import json
import re
import shutil
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

import ci

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
REFERENCE = (HERE / "inputs/local-resolved.config").read_text()


class ConfigTests(unittest.TestCase):
    def test_reference_accepted(self):
        self.assertEqual(ci.validate_config(REFERENCE, REFERENCE), {})

    def test_required_features_cannot_be_disabled_or_changed(self):
        for symbol in ci.REQUIRED:
            with self.subTest(symbol=symbol):
                key = "CONFIG_" + symbol
                value = ci.config_values(REFERENCE)[key]
                broken = REFERENCE.replace(key + "=" + value, "# " + key + " is not set")
                with self.assertRaisesRegex(ValueError, "Required feature"):
                    ci.validate_config(REFERENCE, broken)

    def test_toolchain_text_is_recorded(self):
        new = re.sub(r'^CONFIG_CC_VERSION_TEXT=.*$', 'CONFIG_CC_VERSION_TEXT="cross-gcc12"', REFERENCE, flags=re.M)
        result = ci.validate_config(REFERENCE, new)
        self.assertEqual(result["CONFIG_CC_VERSION_TEXT"][1], '"cross-gcc12"')

    def test_cross_link_capability_must_stay_enabled(self):
        self.assertIn("CC_CAN_LINK", ci.REQUIRED)
        self.assertIn("CONFIG_CC_CAN_LINK=y", REFERENCE)
        with self.assertRaisesRegex(ValueError, "Required feature changed: CONFIG_CC_CAN_LINK"):
            ci.validate_config(REFERENCE, REFERENCE.replace("CONFIG_CC_CAN_LINK=y", "# CONFIG_CC_CAN_LINK is not set"))

    def test_unrelated_driver_change_rejected(self):
        with self.assertRaisesRegex(ValueError, "Unreviewed"):
            ci.validate_config(REFERENCE, REFERENCE + "\nCONFIG_UNREVIEWED_DRIVER=y\n")

    def test_suffix_auto_and_debug_policy_rejected(self):
        for old, new in (("CONFIG_LOCALVERSION=\"-b1s-gpu-diag2\"", 'CONFIG_LOCALVERSION="-ophub"'),
                ("# CONFIG_LOCALVERSION_AUTO is not set", "CONFIG_LOCALVERSION_AUTO=y"),
                ("CONFIG_DEBUG_INFO_COMPRESSED_ZLIB=y", "# CONFIG_DEBUG_INFO_COMPRESSED_ZLIB is not set"),
                ("# CONFIG_DEBUG_INFO_REDUCED is not set", "CONFIG_DEBUG_INFO_REDUCED=y"),
                ("# CONFIG_DEBUG_INFO_SPLIT is not set", "CONFIG_DEBUG_INFO_SPLIT=y")):
            with self.subTest(old=old), self.assertRaises(ValueError):
                self.assertIn(old, REFERENCE)
                ci.validate_config(REFERENCE, REFERENCE.replace(old, new))

    def test_cloud_cannot_change_aux_bridge(self):
        self.assertIn("CONFIG_DRM_AUX_BRIDGE=m", REFERENCE)
        with self.assertRaisesRegex(ValueError, "Unreviewed"):
            ci.validate_config(REFERENCE, REFERENCE.replace("CONFIG_DRM_AUX_BRIDGE=m", "CONFIG_DRM_AUX_BRIDGE=y"))


class ImageTests(unittest.TestCase):
    def setUp(self):
        self.header = bytearray(64)
        struct.pack_into("<QQQ", self.header, 8, 0x01080000, 0x30000, 10)
        self.header[56:60] = b"ARM\x64"
        # Last field may contain a valid PE/COFF offset, not reserved zeros.
        struct.pack_into("<I", self.header, 60, 0x80)
        self.symbols = "ffff800000000000 T _text\nffff800000020000 D _edata\nffff800000030000 B _end\n"
        self.file_bytes = 0x20000

    def test_effective_memory_can_exceed_file_size(self):
        info = ci.image_header_check(self.header, self.file_bytes, self.symbols)
        self.assertEqual(info["effective_memory_bytes"], 0x30000)
        self.assertEqual(info["zero_init_and_reserved_tail_bytes"], 0x10000)
        self.assertEqual(info["text_offset"], 0x01080000)
        self.assertEqual(info["flags"], 10)

    def test_effective_memory_can_equal_file_size(self):
        struct.pack_into("<Q", self.header, 16, self.file_bytes)
        info = ci.image_header_check(self.header, self.file_bytes, self.symbols.replace("030000 B _end", "020000 B _end"))
        self.assertEqual(info["zero_init_and_reserved_tail_bytes"], 0)

    def test_short_header_and_bad_magic_rejected(self):
        for length in (0, 16, 56, 63):
            with self.subTest(length=length), self.assertRaisesRegex(ValueError, "header/magic"):
                ci.image_header_check(self.header[:length], self.file_bytes, self.symbols)
        self.header[56:60] = b"BAD!"
        with self.assertRaisesRegex(ValueError, "header/magic"):
            ci.image_header_check(self.header, self.file_bytes, self.symbols)

    def test_zero_small_and_inconsistent_memory_size_rejected(self):
        for size in (0, 63, self.file_bytes, 0x30001):
            struct.pack_into("<Q", self.header, 16, size)
            with self.subTest(size=size), self.assertRaisesRegex(ValueError, "boundaries mismatch"):
                ci.image_header_check(self.header, self.file_bytes, self.symbols)

    def test_truncated_and_extended_file_rejected(self):
        for size in (0, 63, self.file_bytes - 1, self.file_bytes + 1, 0x30000):
            with self.subTest(size=size), self.assertRaisesRegex(ValueError, "boundaries mismatch"):
                ci.image_header_check(self.header, size, self.symbols)

    def test_missing_duplicate_and_invalid_symbols_rejected(self):
        for name in ("_text", "_edata", "_end"):
            missing = "\n".join(line for line in self.symbols.splitlines() if not line.endswith(name))
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "Missing"):
                ci.image_header_check(self.header, self.file_bytes, missing)
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            ci.image_header_check(self.header, self.file_bytes, self.symbols + self.symbols.splitlines()[0] + "\n")
        with self.assertRaises(ValueError):
            ci.image_header_check(self.header, self.file_bytes, self.symbols.replace("ffff800000000000", "invalid"))

    def test_invalid_symbol_boundaries_rejected(self):
        for symbols in (self.symbols.replace("020000 D _edata", "000020 D _edata"),
                self.symbols.replace("020000 D _edata", "040000 D _edata"),
                self.symbols.replace("000000 T _text", "040000 T _text")):
            with self.subTest(symbols=symbols), self.assertRaisesRegex(ValueError, "boundaries mismatch"):
                ci.image_header_check(self.header, self.file_bytes, symbols)

    def test_each_reserved_byte_must_be_zero(self):
        for offset in range(32, 56):
            header = self.header.copy()
            header[offset] = 1
            with self.subTest(offset=offset), self.assertRaisesRegex(ValueError, "reserved"):
                ci.image_header_check(header, self.file_bytes, self.symbols)


class ImageObjcopyTests(unittest.TestCase):
    """Small file-only ARM64 fixture; no kernel build or runtime operations."""
    @classmethod
    def setUpClass(cls):
        for tool in ("as", "ld", "nm", "objcopy"):
            if not shutil.which("aarch64-linux-gnu-" + tool):
                raise unittest.SkipTest("ARM64 binutils fixture tools unavailable")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="b1s-image-fixture.",
            dir=os.environ.get("RUNNER_TEMP") or ("/tmp/opencode" if Path("/tmp/opencode").is_dir() else None))
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        build = self.build = ci.Build.__new__(ci.Build)
        build.out, build.ev = base / "build", base / "evidence"
        build.env = os.environ.copy()
        (build.out / "arch/arm64/boot").mkdir(parents=True)
        build.ev.mkdir()
        asm = base / "fixture.S"
        asm.write_text('.section .head,"ax"\n.long 0, 0\n.quad 0x01080000, _kernel_size_le, 10\n'
            '.quad 0, 0, 0\n.ascii "ARM\\x64"\n.long 0x80\n.section .data,"aw"\n.byte 1, 2, 3\n'
            '.section .bss,"aw",@nobits\n.skip 4096\n')
        script = base / "fixture.lds"
        script.write_text('SECTIONS { . = 0xffff800000000000; _text = .; .head : { *(.head) } '
            '.data : { *(.data) . = ALIGN(512); } _edata = .; .bss (NOLOAD) : { *(.bss) } '
            '. = ALIGN(65536); _end = .; _kernel_size_le = _end - _text; }\n')
        obj = base / "fixture.o"
        build.run(["aarch64-linux-gnu-as", "-o", str(obj), str(asm)])
        build.run(["aarch64-linux-gnu-ld", "-T", str(script), "-o", str(build.out / "vmlinux"), str(obj)])
        (build.out / "System.map").write_text(build.run(["aarch64-linux-gnu-nm", "-n", str(build.out / "vmlinux")]))
        self.image = build.out / "arch/arm64/boot/Image"
        build.run(["aarch64-linux-gnu-objcopy", "-O", "binary", "-R", ".note", "-R", ".note.gnu.build-id",
            "-R", ".comment", "-S", str(build.out / "vmlinux"), str(self.image)])

    def test_real_objcopy_matches_symbol_boundaries_and_reconstruction(self):
        info = self.build.check_image()
        self.assertEqual(info["file_bytes"], 512)
        self.assertEqual(info["effective_memory_bytes"], 65536)
        self.assertTrue(json.loads((self.build.ev / "image-check.json").read_text())["passed"])
        self.assertEqual(self.image.read_bytes(), (self.build.ev / "Image.from-vmlinux").read_bytes())

    def test_payload_corruption_fails_reconstruction_and_keeps_evidence(self):
        data = bytearray(self.image.read_bytes())
        data[64] ^= 1
        self.image.write_bytes(data)
        with self.assertRaisesRegex(ValueError, "objcopy reconstruction"):
            self.build.check_image()
        self.assertFalse(json.loads((self.build.ev / "image-reconstruction.json").read_text())["matches"])
        self.assertTrue((self.build.ev / "Image.header.bin").is_file())
        self.assertFalse((self.build.ev / "image-check.json").exists())

    def test_bad_header_keeps_raw_size_evidence_without_passing(self):
        self.image.write_bytes(self.image.read_bytes()[:63])
        with self.assertRaisesRegex(ValueError, "header/magic"):
            self.build.check_image()
        self.assertEqual(json.loads((self.build.ev / "image-precheck.json").read_text())["file_bytes"], 63)
        self.assertEqual((self.build.ev / "Image.header.bin").stat().st_size, 63)
        self.assertFalse((self.build.ev / "image-check.json").exists())


class BoundaryTests(unittest.TestCase):
    def test_source_hash_scope_and_permission_checks_are_mandatory(self):
        with tempfile.TemporaryDirectory(prefix="b1s-source-check.") as directory:
            base = Path(directory)
            build = ci.Build.__new__(ci.Build)
            build.src, build.inputs, build.ev = base / "source", base / "inputs", base / "evidence"
            for path in (build.src, build.inputs, build.ev):
                path.mkdir()
            names = ["x", ci.HEADER]
            for name in names:
                path = build.src / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("audited\n")
            frozen = b"index 01234567..abcdef01 100644\n+audited\n"
            build.patch = lambda: frozen.replace(b"01234567..abcdef01", b"0123456..abcdef0")
            build.run = lambda args, cwd: "x\n"
            (build.inputs / "source-changes.patch").write_bytes(frozen)
            manifest = {name: {"sha256": ci.sha(build.src / name),
                "mode": (build.src / name).stat().st_mode & 0o777} for name in names}
            (build.inputs / "source-files.json").write_text(json.dumps(manifest))
            build.check_source()
            (build.src / "x").write_text("changed\n")
            with self.assertRaisesRegex(ValueError, "content/mode"):
                build.check_source()
            (build.src / "x").write_text("audited\n")
            (build.src / "x").chmod(manifest["x"]["mode"] ^ 0o100)
            with self.assertRaisesRegex(ValueError, "content/mode"):
                build.check_source()
            (build.src / "x").chmod(manifest["x"]["mode"])
            build.run = lambda args, cwd: "x\nrogue\n"
            with self.assertRaisesRegex(ValueError, "file set"):
                build.check_source()

    def test_git_index_abbreviation_is_not_source_content(self):
        long = b"diff --git a/x b/x\nindex 01234567..abcdef01 100644\n@@ -1 +1 @@\n-old\n+new\n"
        short = long.replace(b"01234567..abcdef01", b"0123456..abcdef0")
        self.assertNotEqual(long, short)
        self.assertEqual(ci.canonical_patch(long), ci.canonical_patch(short))

    def test_patch_hunks_paths_and_modes_are_still_checked(self):
        patch = b"diff --git a/x b/x\nindex 01234567..abcdef01 100644\n@@ -1 +1 @@\n-old\n+new\n"
        for changed in (patch.replace(b"+new", b"+bad"), patch.replace(b"b/x", b"b/y"),
                patch.replace(b"100644", b"100755"), patch.replace(b"@@ -1 +1 @@", b"@@ -2 +2 @@")):
            with self.subTest(changed=changed):
                self.assertNotEqual(ci.canonical_patch(patch), ci.canonical_patch(changed))

    def test_local_build_execution_is_refused(self):
        with patch.dict(os.environ, {}, clear=True), self.assertRaisesRegex(ValueError, "GitHub Actions"):
            ci.Build()

    def test_runner_temp_parent_cannot_be_work(self):
        with patch.dict(os.environ, {"GITHUB_ACTIONS": "true", "RUNNER_TEMP": "/tmp/opencode",
                "B1S_DIAG_WORK": "/tmp/opencode"}, clear=True), self.assertRaisesRegex(ValueError, "child"):
            ci.Build()

    def test_resource_floors(self):
        high = {"disk_available": 20 * 1024**3, "mem_available": 10 * 1024**3, "swap_free": 0}
        self.assertIsNone(ci.guard(high))
        self.assertIn("disk", ci.guard({**high, "disk_available": ci.DISK_FLOOR - 1}))
        self.assertIn("memory", ci.guard({**high, "mem_available": 0}))
        self.assertIsNone(ci.guard({**high, "mem_available": 0, "swap_free": 2 * 1024**3}))

    def test_concurrency_bounded(self):
        self.assertEqual(ci.jobs({"mem_available": 64 * 1024**3}, 128), 8)
        self.assertEqual(ci.jobs({"mem_available": 2 * 1024**3}, 8), 1)
        self.assertEqual(ci.jobs({"mem_available": 0}, 8), 1)
        self.assertEqual(ci.jobs({"mem_available": 64 * 1024**3}, 2), 2)

    def test_input_sha256(self):
        inputs = HERE / "inputs"
        names = set()
        for line in (inputs / "SHA256SUMS").read_text().splitlines():
            digest, name = line.split(maxsplit=1)
            self.assertEqual(ci.sha(inputs / name), digest)
            names.add(name)
        self.assertEqual(names, {p.name for p in inputs.iterdir()} - {"SHA256SUMS"})

    def test_workflow_only_diagnostic_push_and_read(self):
        text = (ROOT / ".github/workflows/build-b1s-gpu-diag2.yml").read_text()
        workflow = yaml.safe_load(text)
        trigger = workflow.get("on", workflow.get(True))
        self.assertEqual(set(trigger), {"push"})
        self.assertEqual(trigger["push"]["branches"], ["diag/b1s-gpu-diag2"])
        self.assertEqual(workflow["permissions"], {"contents": "read"})
        self.assertFalse(workflow["concurrency"]["cancel-in-progress"])
        self.assertNotIn("upload-to-releases", text)
        self.assertNotIn("repository_dispatch", text)
        for step in workflow["jobs"]["kernel"]["steps"]:
            if "run" in step:
                subprocess.run(["bash", "-n"], input=step["run"], text=True, check=True)
        self.assertIn("@sha256:", workflow["jobs"]["kernel"]["container"]["image"])

    def test_runner_path_is_set_at_step_not_job_env(self):
        workflow = yaml.safe_load((ROOT / ".github/workflows/build-b1s-gpu-diag2.yml").read_text())
        job = workflow["jobs"]["kernel"]
        self.assertNotIn("runner.", str(job["env"]))
        first = job["steps"][0]["run"]
        self.assertIn('echo "B1S_DIAG_WORK=${RUNNER_TEMP}/b1s-gpu-diag2" >> "$GITHUB_ENV"', first)

    def test_cross_libc_dependency_and_failed_audit_retention(self):
        workflow = yaml.safe_load((ROOT / ".github/workflows/build-b1s-gpu-diag2.yml").read_text())
        steps = workflow["jobs"]["kernel"]["steps"]
        self.assertIn("libc6-dev-arm64-cross", steps[0]["run"])
        compile_step = next(step for step in steps if step.get("id") == "compile")
        self.assertTrue(compile_step["run"].endswith("ci.py full"))
        raw = next(step for step in steps if "UNAUDITED" in step.get("with", {}).get("name", ""))
        self.assertEqual(raw["if"], "failure() && steps.compile.outcome == 'success'")
        for name in ("build/arch/arm64/boot/Image", "build/vmlinux", "build/System.map",
                "build/Module.symvers", "build/modules.order", "build/modules.builtin*", "build/**/*.ko"):
            self.assertIn(name, raw["with"]["path"])
        self.assertEqual(raw["with"]["if-no-files-found"], "error")
        audited = next(step for step in steps if step.get("name") == "Upload audited kernel artifacts")
        self.assertEqual(audited["if"], "success()")
        evidence = next(step for step in steps if step.get("name") == "Upload build evidence, including on failure")
        self.assertEqual(evidence["if"], "always()")

    def test_patch_keeps_writes_pm_and_original_errors(self):
        text = (HERE / "inputs/source-changes.patch").read_text()
        added = "\n".join(line[1:] for line in text.splitlines() if line.startswith("+") and not line.startswith("+++"))
        removed = "\n".join(line[1:] for line in text.splitlines() if line.startswith("-") and not line.startswith("---"))
        pattern = r"\b(?:gpu_write|mmu_write|job_write|writel(?:_relaxed)?|pm_runtime_\w+|dev_err|dev_warn)\s*\("
        self.assertEqual(re.findall(pattern, added), re.findall(pattern, removed))
        for call in re.findall(r"panfrost_diag_(?:log|regs)\([^;]+;", added):
            for value in re.findall(r'"((?:\\.|[^"\\])*)"', call):
                self.assertNotRegex(value.lower(), "fault|timeout|stuck|error")
        self.assertIn('of_machine_is_compatible("dangbei,b1s")', added)
        self.assertIn('of_device_is_compatible(pdev->dev.of_node, "amlogic,meson-gxm-mali")', added)
        self.assertIn("atomic_fetch_add_unless", added)


if __name__ == "__main__":
    unittest.main()
