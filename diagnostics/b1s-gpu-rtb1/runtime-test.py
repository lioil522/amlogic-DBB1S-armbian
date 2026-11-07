#!/usr/bin/env python3
"""Compile actual frozen A/B callback bodies against the verified host stubs."""
import json
import os
import re
import subprocess
from pathlib import Path

import ci

HERE = Path(__file__).resolve().parent
WORK = Path(os.environ['B1S_DIAG_WORK']).resolve()
PF = 'drivers/gpu/drm/panfrost/'


def main():
    source = WORK / 'source'
    for rel, entry in json.loads((HERE / 'inputs/source-files.json').read_text()).items():
        if ci.sha(source / rel) != entry['sha256'] or (source / rel).stat().st_mode & 0o777 != entry['mode']:
            raise ValueError('Unverified B source for runtime fixture: ' + rel)
    frozen_a = json.loads((HERE / 'inputs/a-source-files.json').read_text())
    if ci.sha(HERE / 'inputs/a-panfrost_device.c') != frozen_a[PF + 'panfrost_device.c']['sha256']:
        raise ValueError('A callback source mismatch')
    bdev = (source / PF / 'panfrost_device.c').read_text()
    adev = (HERE / 'inputs/a-panfrost_device.c').read_text()
    b = bdev[bdev.index('/* B1S_RTB1_TRACE_BEGIN */'):bdev.index('static int panfrost_device_resume(')]
    a = adev[adev.index('static int panfrost_device_runtime_resume('):adev.index('static int panfrost_device_resume(')]
    a = a.replace('panfrost_device_runtime_resume', 'a_runtime_resume').replace('panfrost_device_runtime_suspend', 'a_runtime_suspend')
    drv = (source / PF / 'panfrost_drv.c').read_text()
    gate = re.search(r'pfdev->b1s_rt_enabled =.*?;', drv, re.S)[0]
    gpu = (source / PF / 'panfrost_gpu.c').read_text()
    poweroff = gpu[gpu.index('void panfrost_gpu_power_off('):gpu.index('void panfrost_gpu_suspend_irq(')]
    out = WORK / 'validation/runtime'
    out.mkdir(parents=True)
    (out / 'extracted-runtime.h').write_text(poweroff + b + a +
        '\nstatic void extracted_probe_gate(struct panfrost_device *pfdev, struct platform_device *pdev) {\n' + gate + '\n}\n')
    command = ['gcc-12', '-std=gnu11', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter',
               '-O2', '-I' + str(out), str(HERE / 'runtime-tests.c'), '-o', str(out / 'runtime-tests')]
    subprocess.run(command, check=True)
    result = subprocess.check_output([str(out / 'runtime-tests')], text=True)
    if '3584 A/B callback cases' not in result:
        raise ValueError('Incomplete runtime fixture')
    report = {'passed': True, 'command': command, 'result': result,
              'scope': 'host stubs of actual callback source, not GPU/clock/reset hardware validation'}
    (WORK / 'validation/runtime-test.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
