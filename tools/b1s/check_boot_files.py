#!/usr/bin/env python3
"""Check B1S removable-media boot files without executing U-Boot scripts.

The factory eMMC U-Boot loads s905_autoscript from FAT. A nonzero raw-sector
bootloader is not required for this chain and is not a bootability test.
These are static prerequisites, not a substitute for a real boot test.
"""
import argparse
import hashlib
import json
import shlex
import struct
import subprocess
import zlib
from pathlib import Path


def legacy_payload(path, image_type):
    data = path.read_bytes()
    if len(data) < 64 or data[:4] != b'\x27\x05\x19\x56':
        raise ValueError(f'{path.name}: invalid legacy uImage header')
    header = bytearray(data[:64])
    expected_crc = struct.unpack_from('>I', header, 4)[0]
    header[4:8] = b'\0' * 4
    if zlib.crc32(header) != expected_crc:
        raise ValueError(f'{path.name}: header CRC mismatch')
    size = struct.unpack_from('>I', data, 12)[0]
    payload = data[64:]
    if len(payload) != size or zlib.crc32(payload) != struct.unpack_from('>I', data, 24)[0]:
        raise ValueError(f'{path.name}: payload size/CRC mismatch')
    if data[30] != image_type:
        raise ValueError(f'{path.name}: unexpected uImage type {data[30]}')
    return payload


def script_text(path):
    payload = legacy_payload(path, 6)  # IH_TYPE_SCRIPT
    if len(payload) < 8:
        raise ValueError(f'{path.name}: missing script length table')
    size, terminator = struct.unpack_from('>II', payload)
    if terminator != 0 or size != len(payload) - 8:
        raise ValueError(f'{path.name}: invalid script length table')
    text = payload[8:]
    source = path.with_suffix('.cmd') if path.name == 'boot.scr' else path.with_name(path.name + '.cmd')
    if source.exists() and source.read_bytes() != text:
        raise ValueError(f'{path.name}: compiled script differs from {source.name}')
    return text.decode('ascii')


def check(boot, expected_uuid):
    boot = Path(boot).resolve()
    values = {}
    for line in (boot / 'uEnv.txt').read_text().splitlines():
        if line.strip() and not line.lstrip().startswith('#') and '=' in line:
            key, value = line.split('=', 1)
            if key in values:
                raise ValueError(f'Duplicate uEnv setting: {key}')
            values[key] = value.strip()
    selected = {}
    for key in ('LINUX', 'INITRD', 'FDT'):
        path = (boot / values[key].lstrip('/')).resolve()
        if not path.is_relative_to(boot) or not path.is_file():
            raise ValueError(f'{key}: missing file or path outside boot partition')
        selected[key] = path
    roots = [word for word in shlex.split(values['APPEND']) if word.startswith('root=')]
    if roots != ['root=UUID=' + expected_uuid]:
        raise ValueError('uEnv root UUID does not match the image root partition')
    text = script_text(boot / 's905_autoscript')
    for token in ('uEnv.txt', '${LINUX}', '${INITRD}', '${FDT}', 'booti', 'usb'):
        if token not in text:
            raise ValueError(f's905_autoscript: missing boot-chain token {token}')
    scripts = ['s905_autoscript']
    for name in ('boot.scr', 'aml_autoscript'):
        if (boot / name).exists():
            script_text(boot / name)
            scripts.append(name)
    kernel = selected['LINUX']
    with kernel.open('rb') as stream:
        header = stream.read(64)
    if len(header) != 64 or header[56:60] != b'ARM\x64':
        raise ValueError('LINUX must be an uncompressed ARM64 Image (not gzip)')
    offset, size = struct.unpack_from('<QQ', header, 8)
    # image_size is the in-memory span, including BSS; it may exceed file size.
    route = 'factory U-Boot direct booti'
    if offset != 0x1080000:
        overload = boot / 'u-boot-s905x-s912.bin'
        ext = boot / 'u-boot.ext'
        if not ext.is_file() or not overload.is_file() or ext.stat().st_size == 0 or ext.read_bytes() != overload.read_bytes():
            raise ValueError('Kernel lacks B1S TEXT_OFFSET: matching u-boot.ext overload required')
        route = 'factory U-Boot -> u-boot.ext -> booti (static prerequisites only)'
    legacy_payload(selected['INITRD'], 3)  # IH_TYPE_RAMDISK
    result = subprocess.run(['fdtget', '-t', 's', str(selected['FDT']), '/', 'compatible'],
                            text=True, capture_output=True, check=True)
    if 'dangbei,b1s' not in result.stdout.split():
        raise ValueError('FDT is not compatible with dangbei,b1s')
    files = {'uEnv.txt': boot / 'uEnv.txt', **{str(p.relative_to(boot)): p for p in selected.values()}}
    files.update({name: boot / name for name in scripts})
    for name in ('u-boot.ext', 'u-boot-s905x-s912.bin', 'u-boot.sd', 'u-boot.usb'):
        if (boot / name).is_file():
            files[name] = boot / name
    hashes = {}
    for name, path in files.items():
        with path.open('rb') as stream:
            hashes[name] = hashlib.file_digest(stream, 'sha256').hexdigest()
    return {'passed': True, 'scope': 'static prerequisites; real boot still required',
            'boot_route': route, 'root_uuid': expected_uuid, 'kernel_text_offset': hex(offset),
            'kernel_memory_size': size, 'kernel_file_size': kernel.stat().st_size,
            'fdt_compatible': result.stdout.strip(), 'sha256': hashes}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('boot', type=Path)
    parser.add_argument('root_uuid')
    args = parser.parse_args()
    try:
        report = check(args.boot, args.root_uuid)
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        print(json.dumps({'passed': False, 'error': str(exc)}, ensure_ascii=False, indent=2))
        raise SystemExit(1)
    print(json.dumps(report, ensure_ascii=False, indent=2))
