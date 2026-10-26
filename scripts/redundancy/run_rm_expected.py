"""Merge-level unit test for the RM expected-SN dev profile (T1-T6)."""
from pathlib import Path
import hashlib, json, sys
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
from rtl_test_build import build, execute

OUT = ROOT / 'reports/rx_expected_sn/merge_unit'
PARAMS = {'TIME_WIDTH': 32, 'RM_EXPECTED_SN': 1, 'RM_GAP_TOLERANCE': 2,
          'RM_RESYNC_TIMEOUT': 50, 'RM_SN0_RESTART': 1}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    sources = [ROOT / 'rtl/rx/afdx_rx_merge.v', ROOT / 'rtl/rx/afdx_integrity_checker.v']
    tb = ROOT / 'tb/rm_expected.cpp'
    exe, env = build('afdx_rx_merge', sources, tb, OUT, parameters=PARAMS)
    execute([exe, OUT / 'result.json'], OUT / 'simulation.log', env)
    result = json.loads((OUT / 'result.json').read_text())
    (OUT / 'inputs.json').write_text(json.dumps({
        'files': {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in sources + [tb, Path(__file__)]},
        'parameters': PARAMS,
        'note': 'merge-level unit test; options forced by -G for fast resync coverage',
        'timing_verified': False}, indent=2) + '\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
