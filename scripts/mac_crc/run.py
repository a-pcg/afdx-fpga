"""Independent zlib golden vectors + Verilator/MSVC execution of CRC RTL."""
from pathlib import Path
import hashlib
import json
import os
import random
import subprocess
import sys
import zlib

ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / 'reports/mac_crc'
OBJ = REPORT / 'obj'
INC = ROOT / 'third_party/verilator-5.006/include'
VER = Path(r'C:\Program Files (x86)\eHiWay\eLinx3.0\verilator\verilator.exe')
VC = Path(r'C:\Program Files\Microsoft Visual Studio\18\Community\VC\Auxiliary\Build\vcvars64.bat')


def run(args, name, env, cwd=ROOT):
    with (REPORT / name).open('wb') as log:
        result = subprocess.run([str(x) for x in args], cwd=cwd, env=env,
                                stdout=log, stderr=subprocess.STDOUT, timeout=90)
    if result.returncode:
        raise RuntimeError(name + ' failed; see ' + str(REPORT / name))


def main():
    REPORT.mkdir(parents=True, exist_ok=True)
    OBJ.mkdir(exist_ok=True)
    vectors = ROOT / 'vectors/mac_crc'
    vectors.mkdir(parents=True, exist_ok=True)
    rng = random.Random(20261017)
    payloads = [b'', b'123456789', bytes(60), bytes([255])*1514,
                bytes(range(256))*5]
    payloads += [bytes(rng.randrange(256) for _ in range(n)) for n in range(65)]
    payloads += [bytes(rng.randrange(256) for _ in range(rng.randrange(1515)))
                 for _ in range(500)]
    assert zlib.crc32(b'123456789') == 0xcbf43926
    text = '\n'.join((p.hex() or '-') + ' ' + format(zlib.crc32(p), '08x')
                     for p in payloads) + '\n'
    (vectors / 'cases.txt').write_text(text, encoding='ascii')
    source = ROOT / 'rtl/mac/afdx_eth_crc32.v'
    manifest = {'rtl_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
                'testbench_sha256': hashlib.sha256((ROOT / 'tb/mac_crc.cpp').read_bytes()).hexdigest(),
                'golden_sha256': hashlib.sha256(text.encode('ascii')).hexdigest(),
                'golden_engine': 'Python zlib.crc32', 'frames': len(payloads)}
    (REPORT / 'inputs.json').write_text(json.dumps(manifest, indent=2) + '\n')
    template = (INC / 'verilated_config.h.in').read_text()
    template = template.replace('@PACKAGE_NAME@', 'Verilator').replace('@PACKAGE_VERSION@', '5.006')
    template = template.replace('@VERILATOR_VERSION_INTEGER@', '5006000')
    (REPORT / 'verilated_config.h').write_text(template)
    env = {k.upper(): v for k, v in os.environ.items()}
    env['VERILATOR_ROOT'] = str(INC.parent).replace('\\', '/')
    run([VER, '--cc', '--compiler', 'msvc', '--default-language', '1364-2001',
         '--top-module', 'afdx_eth_crc32', '--Mdir', OBJ.relative_to(ROOT).as_posix(),
         source.relative_to(ROOT).as_posix()], 'verilator.log', env)
    result = subprocess.run('call "' + str(VC) + '" >nul && set', shell=True,
                            capture_output=True, text=True, encoding='utf-8',
                            errors='replace', check=True)
    for line in result.stdout.splitlines():
        if '=' in line:
            key, value = line.split('=', 1)
            if key:
                env[key.upper()] = value
    compiler = next((Path(x) / 'cl.exe' for x in env['PATH'].split(';')
                      if (Path(x) / 'cl.exe').is_file()), None)
    if compiler is None:
        raise RuntimeError('MSVC compiler missing after vcvars64')
    sources = list(OBJ.glob('*.cpp')) + [INC / 'verilated.cpp',
                                       INC / 'verilated_threads.cpp', ROOT / 'tb/mac_crc.cpp']
    args = ['/nologo', '/std:c++17', '/EHsc', '/MD', '/O2', '/DVL_TIME_CONTEXT',
            '/I' + str(INC), '/I' + str(INC / 'vltstd'), '/I' + str(REPORT),
            '/I' + str(OBJ), '/Fe:' + str(OBJ / 'mac_crc.exe')] + [str(x) for x in sources]
    (OBJ / 'build.rsp').write_text('\n'.join('"' + x + '"' for x in args), encoding='utf-8')
    run([compiler, '@build.rsp'], 'msvc.log', env, OBJ)
    run([OBJ / 'mac_crc.exe'], 'simulation.log', env)
    result = json.loads((REPORT / 'result.json').read_text())
    if result.get('status') != 'PASS' or result.get('frames') != len(payloads):
        raise RuntimeError('RTL CRC regression did not complete all golden frames')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
