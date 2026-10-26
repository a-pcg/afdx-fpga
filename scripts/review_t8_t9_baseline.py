"""Independent, single-thread review; never overwrites implementation reports."""
from pathlib import Path
import hashlib
import importlib.util
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from rtl_test_build import build, execute

OUT = ROOT / 'reports/independent_t8_t9_review'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(name, value):
    (OUT / name).write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    summary = {'status': 'RUNNING', 'tests': {}, 'timing': [],
               'method': 'Read original implementation evidence and independently replay existing RTL tests; no new routing or independent protocol model.',
               'timing_verified': False}
    save('review.json', summary)
    try:
        for tag in ['e5_t8_r9c_p10', 'e5_t8_r9c_p20',
                    'e5_t9_1_r9c_p10', 'e5_t9_1_r9c_p20']:
            path = ROOT / ('reports/elinx_integration/sys_e5_route_' + tag + '.json')
            j = json.loads(path.read_text())
            assert j['status'] == 'ROUTE_COMPLETE' and j['returncode'] == 0
            assert j['constraint_parse_observed'] and j['route_completed']
            # argv's -f points into the exact original route directory.
            route = Path(j['argv'][j['argv'].index('-f') + 1]).parent
            slacks = list(route.rglob('*slack*.rpt'))
            assert slacks, 'No original slack report in ' + str(route)
            c = next(c for c in j['slack_report']['clocks'] if c['name'] == 'clk')
            summary['timing'].append({'tag': tag, 'period_ns': j['period_ns'],
                'clock': c, 'record_sha256': digest(path),
                'pool_sha256': j['input_hashes']['rtl/buffer/afdx_packet_pool.v'],
                'original_slack_reports': {str(p.relative_to(ROOT)): digest(p) for p in slacks},
                'original_slack_text': {str(p.relative_to(ROOT)): p.read_text(errors='replace') for p in slacks}})
        pins = json.loads((ROOT / 'reports/current_source_pins.json').read_text(encoding='utf-8-sig'))
        summary['pins_mismatches'] = [n for n, h in pins['current_hashes'].items()
                                     if digest(ROOT / n) != h]
        assert not summary['pins_mismatches'], summary['pins_mismatches']
        pool = ROOT / 'rtl/buffer/afdx_packet_pool.v'
        assert digest(pool) == '38b2cb1094d0a42c09325ecba45ceb2c9e7f945f527e8cbac45be580d3dbbe8f'
        spec = importlib.util.spec_from_file_location('system_runner', ROOT / 'scripts/system_integration/run.py')
        runner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runner)
        names = runner.NAMES + ['tb/system_loopback.cpp', 'tb/packet_pool.cpp',
            'scripts/rtl_test_build.py', 'scripts/system_integration/run.py',
            'scripts/review_t8_t9_baseline.py']
        before = {n: digest(ROOT / n) for n in names}
        save('inputs.json', {'sources': before, 'python': sys.version})
        for width in [3, 6]:
            name = 'pool_' + str(1 << width)
            folder = OUT / name
            exe, env = build('afdx_packet_pool', [pool], ROOT / 'tb/packet_pool.cpp', folder, {'SLOT_WIDTH': width})
            execute([exe, 1 << width, folder / 'result.json'], folder / 'simulation.log', env)
            r = json.loads((folder / 'result.json').read_text())
            assert r['status'] == 'PASS'
            summary['tests'][name] = r
            save('review.json', summary)
            print(name + ' PASS', flush=True)
        folder = OUT / 'system'
        exe, env = build('afdx_system_top', [ROOT / n for n in runner.NAMES],
                         ROOT / 'tb/system_loopback.cpp', folder)
        execute([exe, folder / 'result.json'], folder / 'simulation.log', env)
        r = json.loads((folder / 'result.json').read_text())
        assert r['status'] == 'PASS' and r['max_sfd_time_delta'] == 0
        summary['tests']['system'] = r
        after = {n: digest(ROOT / n) for n in names}
        assert before == after, 'Inputs changed during independent replay'
        summary['sources_stable'] = True
        summary['decision'] = 'KEEP_T8_BASELINE_REJECT_T9_1_TIMING_REGRESSION'
        summary['status'] = 'BASELINE_REVIEW_PASS_TIMING_NOT_MET'
    except Exception as error:
        summary['status'] = 'FAIL_OR_INCOMPLETE'
        summary['error'] = str(error)
        raise
    finally:
        save('review.json', summary)
    print(json.dumps({'status': summary['status'], 'decision': summary['decision'], 'tests': summary['tests']}), flush=True)


if __name__ == '__main__':
    main()
