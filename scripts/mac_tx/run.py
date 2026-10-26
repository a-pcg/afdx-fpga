"""Byte-level 100 Mbps MAC framing regression using independent zlib FCS."""
from pathlib import Path
import hashlib
import importlib.util
import json
import os
import random
import subprocess
import sys
import zlib

ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / 'reports/mac_tx'
OBJ = REPORT / 'obj'


def main():
    REPORT.mkdir(parents=True, exist_ok=True)
    OBJ.mkdir(exist_ok=True)
    spec = importlib.util.spec_from_file_location('crc_build_helper', ROOT / 'scripts/mac_crc/run.py')
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    helper.REPORT = REPORT
    vectors = ROOT / 'vectors/mac_tx'
    vectors.mkdir(parents=True, exist_ok=True)
    rng = random.Random(20261017)
    lengths = [60, 61, 62, 63, 64, 511, 512, 513, 1513, 1514]
    lengths += [rng.randrange(60, 1515) for _ in range(200)]
    bodies = [bytes(rng.randrange(256) for _ in range(n)) for n in lengths]
    text = '\n'.join(b.hex() + ' ' +
                     (bytes([0x55])*7 + b'\xd5' + b + zlib.crc32(b).to_bytes(4, 'little')).hex()
                     for b in bodies) + '\n'
    (vectors / 'cases.txt').write_text(text, encoding='ascii')
    files = ['rtl/mac/afdx_eth_tx_mac.v', 'rtl/mac/afdx_eth_crc32.v', 'tb/mac_tx.cpp']
    hashes = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in files}
    hashes['golden_vectors'] = hashlib.sha256(text.encode('ascii')).hexdigest()
    (REPORT / 'inputs.json').write_text(json.dumps(hashes, indent=2) + '\n')
    inc = helper.INC
    header = (inc / 'verilated_config.h.in').read_text().replace('@PACKAGE_NAME@', 'Verilator')
    header = header.replace('@PACKAGE_VERSION@', '5.006').replace('@VERILATOR_VERSION_INTEGER@', '5006000')
    (REPORT / 'verilated_config.h').write_text(header)
    env = {k.upper(): v for k, v in os.environ.items()}
    env['VERILATOR_ROOT'] = str(inc.parent).replace('\\', '/')
    helper.run([helper.VER, '--cc', '--compiler', 'msvc', '--default-language', '1364-2001',
                '--top-module', 'afdx_eth_tx_mac', '--Mdir', OBJ.relative_to(ROOT).as_posix(),
                *files[:2]], 'verilator.log', env)
    setup = subprocess.run('call "' + str(helper.VC) + '" >nul && set', shell=True,
                           capture_output=True, text=True, encoding='utf-8', errors='replace', check=True)
    for line in setup.stdout.splitlines():
        if '=' in line:
            k, v = line.split('=', 1)
            if k:
                env[k.upper()] = v
    compiler = next(Path(p)/'cl.exe' for p in env['PATH'].split(';') if (Path(p)/'cl.exe').exists())
    sources = list(OBJ.glob('*.cpp')) + [inc/'verilated.cpp', inc/'verilated_threads.cpp', ROOT/files[2]]
    args = ['/nologo', '/std:c++17', '/EHsc', '/MD', '/O2', '/DVL_TIME_CONTEXT',
            '/I'+str(inc), '/I'+str(inc/'vltstd'), '/I'+str(REPORT), '/I'+str(OBJ),
            '/Fe:'+str(OBJ/'mac_tx.exe')] + [str(x) for x in sources]
    (OBJ/'build.rsp').write_text('\n'.join('"'+x+'"' for x in args), encoding='utf-8')
    helper.run([compiler, '@build.rsp'], 'msvc.log', env, OBJ)
    helper.run([OBJ/'mac_tx.exe'], 'simulation.log', env)
    result = json.loads((REPORT/'result.json').read_text())
    if result.get('status') != 'PASS' or result.get('frames') != len(bodies)+1:
        raise RuntimeError('TX MAC corpus did not complete')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
