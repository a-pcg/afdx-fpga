"""Run named implementation suites; PASS applies only to selected suites.

The source mapping records regression inputs, not statement coverage. This entry
does not establish ARINC compliance, timing, full capacity or native packing.
"""
from pathlib import Path
import argparse
import hashlib
import json
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
MAC = ['rtl/eth_mac_tx.v', 'rtl/eth_mac_rx.v', 'rtl/eth_crc32_byte.v']
BASE = ['rtl/afdx_baseline_core.v', 'rtl/afdx_udp_tx.v', 'rtl/afdx_udp_rx.v']
TX = ['rtl/afdx_shared_tx.v', 'rtl/afdx_udp_tx_shared.v',
      'rtl/afdx_bag_scheduler64.v', 'rtl/buffer/*.v']
RX = ['rtl/rx/*.v', 'rtl/afdx_udp_rx.v']
COMMON = ['scripts/rtl_test_build.py', 'scripts/verify_all.py']
SUITES = {
    'legacy': {
        'steps': ['run_regression.py', 'run_timed_tb.py', 'run_scheduler.py',
                  'run_scheduled_tx.py', 'run_mac.py', 'run_wire_core.py',
                  'check_elinx.py', 'check_classic.py'],
        'sources': BASE + MAC + ['rtl/afdx_bag_scheduler.v',
            'rtl/afdx_scheduled_tx.v', 'rtl/afdx_wire_core.v',
            'rtl/afdx_env_probe.v', 'tb/mac_pair.v', 'tb/tb_afdx_baseline.v',
            'tb/baseline.cpp', 'tb/timed_main.cpp', 'tb/scheduler.cpp',
            'tb/scheduled_tx.cpp', 'tb/mac.cpp', 'tb/wire_core.cpp',
            'scripts/run_regression.py', 'scripts/run_timed_tb.py',
            'scripts/run_scheduler.py', 'scripts/run_scheduled_tx.py',
            'scripts/run_mac.py', 'scripts/run_wire_core.py',
            'scripts/check_elinx.py', 'scripts/check_classic.py',
            'scripts/generate*.py', 'constraints/*', '*.epr'],
    },
    'buffers': {
        'steps': ['verify_buffers.py'],
        'sources': ['rtl/buffer/*.v', 'tb/packet_pool.cpp', 'tb/vl_queue.cpp',
            'tb/buffered_vl.cpp', 'scripts/verify_buffers.py',
            'scripts/packet_pool/*.py', 'scripts/vl_queue/*.py',
            'scripts/buffered_vl/*.py'],
    },
    'tx': {
        'steps': ['shared_tx/scheduler64.py', 'shared_tx/guard.py', 'shared_tx/run.py'],
        'sources': TX + [MAC[0], MAC[2], 'tb/scheduler64.cpp',
            'tb/shared_tx*.cpp', 'scripts/shared_tx/*.py', 'scripts/generate_vectors.py'],
    },
    'rx': {
        'steps': ['redundancy/run_all.py', 'redundancy/run_startup_review.py'],
        'sources': RX + MAC[1:] + ['tb/rx*.cpp', 'tb/rx*.v',
            'scripts/redundancy/*.py', 'vectors/rx_merge/anchors.json'],
    },
    'system': {
        'steps': ['system_integration/run.py'],
        'sources': TX + RX + MAC + ['rtl/system/*.v', 'rtl/afdx_bag_scheduler.v',
            'tb/system*.cpp', 'scripts/system_integration/*.py'],
    },
}


def snapshot(root, patterns):
    paths = set()
    for pattern in patterns:
        matches = {p for p in root.glob(pattern) if p.is_file()}
        if not matches:
            raise FileNotFoundError('required source mapping matched no files: ' + pattern)
        paths.update(matches)
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(paths)}


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suite', action='append', choices=list(SUITES),
                        help='Repeat to select suites; default: all five suites.')
    parser.add_argument('--list', action='store_true', help='Show plan only; does not execute or PASS.')
    parser.add_argument('--output', type=Path, default=ROOT / 'reports/verification.json')
    args = parser.parse_args(argv)
    selected = list(dict.fromkeys(args.suite or SUITES))
    plan = {name: {**SUITES[name], 'sources': SUITES[name]['sources'] + COMMON}
            for name in selected}
    if args.list:
        print(json.dumps({'status': 'PLAN_ONLY', 'selected_suites': plan}, indent=2))
        return 0
    result = {'status': 'RUNNING', 'selected_suites': selected, 'suites': {},
              'scope': 'Only selected suite commands on recorded inputs; no repository-wide coverage claim.',
              'coherent_snapshot': False,
              'timing_verified': False, 'arinc_compliance_verified': False,
              'full_capacity_verified': False}
    save(args.output, result)
    try:
        all_patterns = list(dict.fromkeys(p for spec in plan.values() for p in spec['sources']))
        result['selected_sources_before'] = snapshot(ROOT, all_patterns)
        save(args.output, result)
        for name, spec in plan.items():
            record = {'status': 'RUNNING', 'steps': [], 'source_patterns': spec['sources'],
                      'sources_before': snapshot(ROOT, spec['sources'])}
            result['suites'][name] = record
            save(args.output, result)
            try:
                for step in spec['steps']:
                    started = time.monotonic()
                    subprocess.run([sys.executable, str(ROOT / 'scripts' / step)], cwd=ROOT, check=True)
                    record['steps'].append({'script': step, 'seconds': round(time.monotonic() - started, 2)})
                    save(args.output, result)
                record['sources_after'] = snapshot(ROOT, spec['sources'])
                if record['sources_before'] != record['sources_after']:
                    raise RuntimeError(name + ': mapped inputs changed during execution; stable snapshot required')
                record['status'] = 'PASS'
            except Exception:
                record['status'] = 'FAIL'
                raise
            save(args.output, result)
        result['selected_sources_after'] = snapshot(ROOT, all_patterns)
        if result['selected_sources_before'] != result['selected_sources_after']:
            raise RuntimeError('selected-suite source union changed across groups; no coherent snapshot')
        result['coherent_snapshot'] = True
        result['status'] = 'SELECTED_SUITES_PASS'
    except Exception as error:
        result['status'] = 'FAIL'
        result['error'] = str(error)
        save(args.output, result)
        print(result['error'], file=sys.stderr)
        return 1
    save(args.output, result)
    print('PASS selected suites: ' + ', '.join(selected) + '; no timing or full-system compliance claim')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
