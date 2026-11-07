#!/usr/bin/env python3
"""RTB1-specific cloud contracts, no GPU runtime or source compilation."""
import hashlib
import json
import re
import unittest
from pathlib import Path

import ci
import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


class Rtb1Tests(unittest.TestCase):
    def test_local_frozen_sources_and_object_are_bound_to_cloud_inputs(self):
        hashes = json.loads((HERE / 'inputs/local-final-hashes.json').read_text())
        provenance = json.loads((HERE / 'inputs/rtb1-provenance.json').read_text())
        self.assertEqual(ci.sha(HERE / 'inputs/local-upstream-to-rtb1.patch'), hashes['upstream_patch_sha256'])
        self.assertEqual(ci.sha(HERE / 'inputs/local-diag2-to-rtb1.patch'), hashes['incremental_patch_sha256'])
        self.assertEqual(ci.sha(HERE / 'inputs/source-changes.patch'), provenance['cloud_patch_sha256'])
        self.assertEqual(len(json.loads((HERE / 'inputs/source-files.json').read_text())), 8)
        self.assertEqual(provenance['source_commit'], ci.COMMIT)
        audit = json.loads((HERE / 'inputs/local-object-audit.json').read_text())
        self.assertTrue(audit['passed'])
        self.assertEqual(audit['object_sha256'], hashes['object_sha256'])

    def test_a_and_diag1_configurations_are_rejected(self):
        reference = (HERE / 'inputs/local-resolved.config').read_text()
        for suffix in ('-b1s-gpu-diag2', '-b1s-gpu-diag1'):
            with self.subTest(suffix=suffix), self.assertRaisesRegex(ValueError, 'Unexpected diagnostic setting'):
                ci.validate_config(reference, reference.replace('"-b1s-gpu-rtb1"', '"' + suffix + '"'))

    def test_boundary_helpers_keep_owner_budget_duplicate_and_read_limits(self):
        patch = (HERE / 'inputs/source-changes.patch').read_text()
        added = '\n'.join(line[1:] for line in patch.splitlines()
            if line.startswith('+') and not line.startswith('+++'))
        for token in ('atomic_cmpxchg(&d->state, 0, 1)', 'READ_ONCE(d->owner) != current',
                'if (s->present)', 'phase >= B1S_D2_PHASES',
                'atomic_read(&pfdev->b1s_diag_seq) >= PANFROST_B1S_DIAG_LIMIT',
                '#define PANFROST_B1S_DIAG_LIMIT 256', 's->raw = readl_relaxed', 's->fs = readl_relaxed'):
            self.assertIn(token, added)
        record = re.search(r'static inline void panfrost_diag2_record\(.*?\n\}', added, re.S).group(0)
        self.assertEqual(record.count('readl_relaxed('), 2)
        self.assertEqual(added.count('panfrost_diag2_mark(pfdev,'), 10)
        self.assertEqual(added.count('panfrost_diag2_polled(pfdev,'), 3)

    def test_irq_publication_and_ram_only_end(self):
        patch = (HERE / 'inputs/source-changes.patch').read_text()
        added = '\n'.join(line[1:] for line in patch.splitlines()
            if line.startswith('+') and not line.startswith('+++'))
        irq = re.search(r'static inline void panfrost_diag2_irq\(.*?\n\}', added, re.S).group(0)
        end = re.search(r'static inline void panfrost_diag2_end\(.*?\n\}', added, re.S).group(0)
        self.assertIn('atomic_set_release(&d->irq_state, 2)', irq)
        self.assertIn('atomic_read_acquire(&d->irq_state)', end)
        self.assertIn('if (irq_state == 2)', end)
        for text in (irq, end):
            self.assertNotRegex(text, r'\b(?:readl(?:_relaxed)?|gpu_read|gpu_write|writel)\s*\(')

    def test_rtb1_payload_requires_boundaries_external_calls_and_refuses_mixed_prefix(self):
        data = b'\0'.join((b'b1s-diag2[', b'parmtype=b1s_diag:bool', b'runtime-resume.resources-ready',
            b'boundary.begin', b'boundary.sample', b'boundary.irq', b'boundary.end',
            b'generic.before', b'vendor.after', b'l2.polled', b'shader.polled', b'tiler.polled',
            b'b1s_rtb1=%d', b'b1s-rtb1.resume-ext', b'b1s-rtb1.suspend-ext',
            b'calls=%02x deassert_rc=%d clock_rc=%d bus_rc=%d assert_rc=%d'))
        ci.diagnostic_payload(data)
        for token in data.split(b'\0'):
            with self.subTest(token=token), self.assertRaisesRegex(ValueError, 'diagnostic payload'):
                ci.diagnostic_payload(data.replace(token, b''))
        with self.assertRaisesRegex(ValueError, 'mixed'):
            ci.diagnostic_payload(data + b'b1s-diag1[')

    def test_cloud_helpers_execute_only_after_prepare_and_before_compile(self):
        workflow = yaml.safe_load((ROOT / '.github/workflows/build-b1s-gpu-rtb1.yml').read_text())
        steps = workflow['jobs']['kernel']['steps']
        runs = [step.get('run', '') for step in steps]
        prepare = next(i for i, text in enumerate(runs) if text.endswith('ci.py prepare'))
        simulation = next(i for i, text in enumerate(runs) if 'observation-test.py' in text)
        panfrost = next(i for i, text in enumerate(runs) if text.endswith('ci.py panfrost'))
        self.assertLess(prepare, simulation)
        self.assertLess(simulation, panfrost)
        self.assertIn('budget-test.py', runs[simulation])
        self.assertIn('runtime-test.py', runs[simulation])
        evidence = next(step for step in steps if step.get('name') == 'Upload build evidence, including on failure')
        self.assertIn('/validation/', evidence['with']['path'])

    def test_old_diag1_workflow_remains_isolated(self):
        path = ROOT / '.github/workflows/build-b1s-gpu-diag1.yml'
        provenance = json.loads((HERE / 'inputs/rtb1-provenance.json').read_text())
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), provenance['preserved_diag1_workflow_sha256'])
        workflow = yaml.safe_load(path.read_text())
        self.assertEqual(workflow.get('on', workflow.get(True))['push']['branches'], ['diag/b1s-gpu-diag1'])
        self.assertEqual(workflow['jobs']['kernel']['if'], "github.ref == 'refs/heads/diag/b1s-gpu-diag1'")

    def test_a_workflow_and_reference_are_preserved(self):
        provenance = json.loads((HERE / 'inputs/rtb1-provenance.json').read_text())
        self.assertEqual(ci.sha(ROOT / '.github/workflows/build-b1s-gpu-diag2.yml'),
                         provenance['preserved_diag2_workflow_sha256'])
        reference = (HERE / 'inputs/local-resolved.config').read_text()
        self.assertEqual(reference.replace('"-b1s-gpu-rtb1"', '"-b1s-gpu-diag2"'),
                         (HERE / 'inputs/a-diag2.config').read_text())
        frozen = json.loads((HERE / 'inputs/a-source-files.json').read_text())
        self.assertEqual(ci.sha(HERE / 'inputs/a-panfrost_device.c'),
                         frozen['drivers/gpu/drm/panfrost/panfrost_device.c']['sha256'])

    def test_b_delta_scope_is_only_three_panfrost_files(self):
        a = json.loads((HERE / 'inputs/a-source-files.json').read_text())
        b = json.loads((HERE / 'inputs/source-files.json').read_text())
        self.assertEqual(set(a), set(b))
        self.assertEqual({key for key in a if a[key] != b[key]}, {
            'drivers/gpu/drm/panfrost/panfrost_device.c',
            'drivers/gpu/drm/panfrost/panfrost_device.h',
            'drivers/gpu/drm/panfrost/panfrost_drv.c'})
        delta = (HERE / 'inputs/local-diag2-to-rtb1.patch').read_text()
        self.assertEqual(delta.count('+\tif (pfdev->comp->pm_features & BIT(GPU_PM_RT) || pfdev->b1s_rt_enabled)'), 4)
        self.assertIn('+\t\tof_machine_is_compatible("dangbei,b1s") &&', delta)
        self.assertNotIn('+\tpfdev->b1s_rt_enabled = b1s_diag', delta)

    def test_runtime_fixture_is_the_locally_verified_one(self):
        result = json.loads((HERE / 'inputs/local-runtime-validation.json').read_text())
        self.assertTrue(result['passed'])
        self.assertEqual(ci.sha(HERE / 'runtime-tests.c'), result['harness_sha256'])
        self.assertIn('3584 A/B callback cases', result['stdout'])


if __name__ == '__main__':
    unittest.main()
