#!/usr/bin/env python3
"""Offline cloud build contracts; no kernel compilation or GPU operations."""
import os
import re
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

    def test_unrelated_driver_change_rejected(self):
        with self.assertRaisesRegex(ValueError, "Unreviewed"):
            ci.validate_config(REFERENCE, REFERENCE + "\nCONFIG_UNREVIEWED_DRIVER=y\n")

    def test_suffix_auto_and_debug_policy_rejected(self):
        for old, new in (("CONFIG_LOCALVERSION=\"-b1s-gpu-diag1\"", 'CONFIG_LOCALVERSION="-ophub"'),
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


class BoundaryTests(unittest.TestCase):
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
        text = (ROOT / ".github/workflows/build-b1s-gpu-diag1.yml").read_text()
        workflow = yaml.safe_load(text)
        trigger = workflow.get("on", workflow.get(True))
        self.assertEqual(set(trigger), {"push"})
        self.assertEqual(trigger["push"]["branches"], ["diag/b1s-gpu-diag1"])
        self.assertEqual(workflow["permissions"], {"contents": "read"})
        self.assertFalse(workflow["concurrency"]["cancel-in-progress"])
        self.assertNotIn("upload-to-releases", text)
        self.assertNotIn("repository_dispatch", text)
        for step in workflow["jobs"]["kernel"]["steps"]:
            if "run" in step:
                subprocess.run(["bash", "-n"], input=step["run"], text=True, check=True)
        self.assertIn("@sha256:", workflow["jobs"]["kernel"]["container"]["image"])

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
