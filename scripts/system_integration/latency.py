"""System-level end-to-end latency/jitter measurement (RTL frozen).

Runs tb/system_latency.cpp against the joint AFDX top: 4 VL x 25 frames per group,
legal BAG (bag_exp=0 -> 100000 cycles), two groups (rx_ready always 1 and
application backpressure 19 cycles low per 97). Writes reports/system_latency/
result.json (raw per-frame samples + min/avg/max/P50/P99 + jitter) and
inputs.json with every input hash. tb/system_loopback.cpp and the RTL are
untouched.
"""
from pathlib import Path
import hashlib, json, math, sys
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
from rtl_test_build import build, execute

OUT = ROOT / 'reports/system_latency'
NAMES = ['rtl/system/afdx_system_top.v', 'rtl/afdx_shared_tx.v', 'rtl/afdx_udp_tx_shared.v',
         'rtl/afdx_bag_scheduler.v', 'rtl/afdx_bag_scheduler64.v', 'rtl/eth_mac_tx.v',
         'rtl/eth_crc32_byte.v', 'rtl/buffer/afdx_buffered_vl.v', 'rtl/buffer/afdx_vl_queue.v',
         'rtl/buffer/afdx_packet_pool.v', 'rtl/rx/afdx_rx_dual.v', 'rtl/rx/afdx_rx_merge.v',
         'rtl/rx/afdx_integrity_checker.v', 'rtl/afdx_udp_rx.v', 'rtl/eth_mac_rx.v']


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'result.json').write_text('{"status":"RUNNING"}\n')
    sources = [ROOT / n for n in NAMES]
    tb = ROOT / 'tb/system_latency.cpp'
    exe, env = build('afdx_system_top', sources, tb, OUT)
    execute([exe, OUT / 'result.json'], OUT / 'simulation.log', env)
    result = json.loads((OUT / 'result.json').read_text())

    def stats(values):
        n = len(values)
        if not n:
            return {'count': 0}
        ordered = sorted(values)
        rank = lambda p: ordered[min(n - 1, max(0, math.ceil(p * n) - 1))]
        mean = sum(values) / n
        diffs = [abs(values[i] - values[i - 1]) for i in range(1, n)]
        return {'count': n, 'min': ordered[0], 'avg': round(mean, 3), 'max': ordered[-1],
                'p50': rank(0.50), 'p99': rank(0.99),
                'jitter_max': max(diffs) if diffs else 0,
                'jitter_mean': round(sum(diffs) / len(diffs), 3) if diffs else 0,
                'jitter_stddev': round(math.sqrt(sum((x - mean) ** 2 for x in values) / n), 3)}

    for group in result.get('groups', []):
        by_len = {}
        for sample in group.get('samples', []):
            by_len.setdefault(sample['len'], []).append(sample['latency'])
        group['stats_by_len'] = [dict(len=length, **stats(by_len[length])) for length in sorted(by_len)]
        group['stats_by_len_note'] = 'grouped by payload length: the rotating 1/17/63/1471 lengths dominate the mixed-stream jitter'
    (OUT / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    inputs = {
        'files': {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in sources + [tb, Path(__file__)]},
        'measurement_basis': 'Verilator simulation cycles; TX sfd_pulse/sfd_time -> RX app rx_last&&rx_ready',
        'vl_count': 4, 'frames_per_vl': 25, 'bag_cycles': 100000,
        'groups': ['min_pipeline (rx_ready=1)', 'app_backpressure (rx_ready low 19 of 97 cycles)'],
        'boundaries': ['simulation time, not hardware or ModelSim sign-off',
                       '4 VL configured subset only; not full rate, not 128/4096 VL',
                       'no RTL change, no E4/E5 run'],
        'timing_verified': False,
    }
    (OUT / 'inputs.json').write_text(json.dumps(inputs, indent=2) + '\n')
    print(json.dumps({'status': result.get('status'), 'groups_passed': result.get('groups_passed'),
                      'groups_total': result.get('groups_total'), 'checks': result.get('checks'),
                      'cycles': result.get('cycles')}))


if __name__ == '__main__':
    main()