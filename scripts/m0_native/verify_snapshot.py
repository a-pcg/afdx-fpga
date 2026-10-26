"""Run existing independent RTL tests against the exact native-packed snapshot.

Copies the authored regression harness and matching Verilator runtime into an
isolated workspace. Does not rewrite active RTL, vectors, reports or scripts.
"""
from pathlib import Path
import hashlib
import json
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
PACK = ROOT / 'elinx/m0_probe/native_baseline'
WORK = PACK / 'verification'


def main():
    source_manifest = json.loads((PACK / 'sources.json').read_text())
    for folder in ['rtl', 'scripts', 'tb', 'reports', 'vectors']:
        (WORK / folder).mkdir(parents=True, exist_ok=True)
    recorded = {}
    for name, expected in source_manifest.items():
        data = (PACK / 'src' / name).read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if digest != expected['sha256']:
            raise RuntimeError('Native snapshot changed: ' + name)
        (WORK / 'rtl' / name).write_bytes(data)
        recorded['rtl/' + name] = digest
    for name in ['scripts/run_regression.py', 'scripts/generate_vectors.py', 'tb/baseline.cpp']:
        data = (ROOT / name).read_bytes()
        (WORK / name).write_bytes(data)
        recorded[name] = hashlib.sha256(data).hexdigest()
    upstream = ROOT / 'third_party/verilator-5.006'
    runtime = WORK / 'third_party/verilator-5.006'
    shutil.copytree(upstream / 'include', runtime / 'include', dirs_exist_ok=True)
    shutil.copyfile(upstream / 'LICENSE', runtime / 'LICENSE')
    (WORK / 'inputs.json').write_text(json.dumps(recorded, indent=2) + '\n')
    command = [sys.executable, str(WORK / 'scripts/run_regression.py')]
    with (WORK / 'regression_console.log').open('wb') as log:
        result = subprocess.run(command, cwd=WORK, stdout=log, stderr=subprocess.STDOUT, timeout=180)
    if result.returncode:
        raise RuntimeError('Snapshot RTL verification failed; see ' + str(WORK / 'regression_console.log'))
    regression = json.loads((WORK / 'reports/regression.json').read_text())
    if regression.get('status') != 'PASS' or regression.get('runs') != 10:
        raise RuntimeError('Regression did not produce ten passing seeds')
    record = {'status': 'PASS', 'rtl_sha256': source_manifest,
              'verification_inputs_sha256': recorded,
              'regression': regression,
              'scope': 'MAC-client single-VL IPv4/UDP RTL; no IC/RM or physical timing proof'}
    (PACK / 'snapshot_verification.json').write_text(json.dumps(record, indent=2) + '\n')
    print('PASS: exact native-packed RTL snapshot; ten independent-vector RTL runs; checks=' + str(regression['checks']))


if __name__ == '__main__':
    main()
