"""Concurrent RX acceptance suite (test-only; RTL behaviour selected by -G).

Compliance gate (mode 0): same-VL frames at a legal AFDX BAG (100000 cycles =
12500 byte slots, the value enforced by tb/system_loopback.cpp), readiness on/off
and optional network skew. Requires N/N delivery, zero same-VL order violations,
zero IC-sequence rejects, zero drops, exact payloads and exact SFD timestamps.

Capacity characterisation (mode 1): line-rate trains (12-byte IFG) that violate
the legal BAG. Whole-frame drops are allowed and must reconcile exactly; duplicate
delivery, payload/timestamp corruption and accounting mismatch stay fatal.

L01 characterisation (mode 2): with RM_EXPECTED_SN=0 it reproduces the documented
late-copy re-delivery; with RM_EXPECTED_SN=1 the late copy must be rejected with
reason 4 and re-delivery must disappear (l01_reproduced_expected_sn=false).

History is preserved; the default output directory is
reports/rx_concurrent_20261020b and an explicit --out keeps runs separate.
"""
from pathlib import Path
import argparse, hashlib, json, sys
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'scripts' / 'redundancy'))
from rtl_test_build import build, execute
from run_dual import wire

DEFAULT_OUT = ROOT / 'reports/rx_concurrent_20261020b'
NAMES = ['rtl/rx/afdx_rx_dual.v', 'rtl/rx/afdx_rx_merge.v', 'rtl/rx/afdx_integrity_checker.v',
         'rtl/eth_mac_rx.v', 'rtl/eth_crc32_byte.v', 'rtl/afdx_udp_rx.v']
LENS = [1, 16, 17, 18, 1471, 63]
BAG_SLOTS = 12500          # 100,000 cycles at 8 cycles/byte, same value as the system TB
IFG_SLOTS = 12             # minimum Ethernet IFG used by the line-rate stress trains
L01_GAP = 1000             # 8,000 cycles between A frames (inside the 100,000-cycle RM window)
L01_SKEW = 1500            # B starts 12,000 cycles late, i.e. after A's second frame is accepted


def payload(i, vl):
    n = LENS[i % 6]
    return bytes((i * 29 + k * 17 + vl) % 256 for k in range(n))


def scenario(name, mode, n, skew, ready, gap, same_vl):
    rows = []
    sn = [0] * 8
    for i in range(n):
        vl = 0 if same_vl else i % 8
        p = payload(i, vl)
        rows.append(f'{vl} {sn[vl]} {p.hex()} {wire(vl, sn[vl], p)}')
        sn[vl] += 1
    return f'{name} {mode} {n} {skew} {ready} {gap}\n' + '\n'.join(rows) + '\n'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default=None, help='output report directory (relative to repo root)')
    ap.add_argument('--rm-expected-sn', type=int, default=0,
                    help='0 = legacy acceptance-order RM (default), 1 = expected-SN dev profile')
    args = ap.parse_args()
    out = ROOT / args.out if args.out else DEFAULT_OUT
    parameters = {'RM_EXPECTED_SN': 1} if args.rm_expected_sn else None
    out.mkdir(parents=True, exist_ok=True)
    scenarios = [
        # --- compliance gate: legal BAG spacing ---
        scenario('bag_ready0_skew0', 0, 12, 0, 0, BAG_SLOTS, True),
        scenario('bag_ready1_skew0', 0, 12, 0, 1, BAG_SLOTS, True),
        scenario('bag_ready0_skew32', 0, 12, 32, 0, BAG_SLOTS, True),
        scenario('bag_ready1_skew32', 0, 12, 32, 1, BAG_SLOTS, True),
        # --- capacity characterisation: line rate / minimum IFG ---
        scenario('line_rate_ready0', 1, 24, 0, 0, IFG_SLOTS, True),
        scenario('mixed_line_rate_ready0', 1, 24, 0, 0, IFG_SLOTS, False),
        # --- L01 late-copy characterisation (independent of the compliance gate) ---
        scenario('l01_late_copy', 2, 4, L01_SKEW, 0, L01_GAP, True),
        # backpressure stretches the interleaving enough to reproduce L01 under load
        scenario('l01_line_rate_ready1', 2, 24, 0, 1, IFG_SLOTS, True),
    ]
    cases = out / 'cases.txt'
    cases.write_text(str(len(scenarios)) + '\n' + ''.join(scenarios))
    sources = [ROOT / n for n in NAMES]
    tb = ROOT / 'tb/rx_concurrent.cpp'
    exe, env = build('afdx_rx_dual', sources, tb, out, parameters=parameters)
    log = out / 'simulation.log'
    execute([exe, cases, out / 'tb_result.json'], log, env)
    raw = json.loads((out / 'tb_result.json').read_text())
    scenarios_out = raw['scenarios']
    compliant = [s for s in scenarios_out if s['mode'] == 0]
    capacity = [s for s in scenarios_out if s['mode'] == 1]
    l01 = [s for s in scenarios_out if s['mode'] == 2]
    compliant_ok = bool(compliant) and all(s['status'] == 'PASS' for s in compliant)
    l01_reproduced = any(s['late_copies'] > 0 for s in l01)
    fields = ('name', 'status', 'frames_sent', 'delivered', 'passed', 'ic_sequence_rejects',
              'rm_duplicate_rejects', 'late_rejects', 'gap_rejects', 'order_violations',
              'late_copies', 'missing', 'full_drops_net0', 'full_drops_net1', 'bad_fcs', 'cycles',
              'skew_bytes', 'ready_mode')
    overall = {
        'stage': 'CONCURRENT_ACCEPTANCE',
        'status': 'PASS' if compliant_ok else 'FAIL_COMPLIANCE',
        'rm_expected_sn': args.rm_expected_sn,
        'compliance_gate': {
            'bag_cycles': BAG_SLOTS * 8,
            'scenarios': [{k: s[k] for k in fields if k in s} for s in compliant],
            'all_pass': compliant_ok,
        },
        'capacity_characterisation': {
            'line_rate_signoff': False,
            'note': 'line-rate trains exceed the legal BAG; whole-frame drops are allowed and reconciled',
            'scenarios': [{k: s[k] for k in fields if k in s} for s in capacity],
        },
        'l01_characterisation': {
            'rm_late_copy_closed': False,
            'note': 'dev-profile default stays OFF; with --rm-expected-sn 1 the late copy is rejected (reason 4)',
            'reproduced': l01_reproduced,
            'reproduced_expected_sn': l01_reproduced if args.rm_expected_sn else None,
            'scenarios': [{k: s[k] for k in fields if k in s} for s in l01],
            'first_late': next((line.split('FIRST_LATE_COPY ', 1)[1].strip()
                                for line in log.read_text(errors='replace').splitlines()
                                if line.startswith('FIRST_LATE_COPY ')), ''),
        },
        'checks': raw.get('checks'),
        'cycles': raw.get('total_cycles'),
        'timing_verified': False,
        'files': {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in sources + [tb, Path(__file__), cases]},
    }
    (out / 'result.json').write_text(json.dumps(overall, indent=2) + '\n')
    (out / 'inputs.json').write_text(json.dumps({
        'files': overall['files'],
        'parameters': parameters or {},
        'coverage': {'compliant_bag_cycles': BAG_SLOTS * 8, 'line_rate_ifg_bytes': IFG_SLOTS,
                     'payload_lengths': [1, 16, 17, 18, 1471], 'skew_B_bytes': [0, 32],
                     'A_byte_phase': 0, 'B_byte_phase': 3,
                     'ready_modes': [0, 1], 'l01_gap_cycles': L01_GAP * 8, 'l01_skew_cycles': L01_SKEW * 8},
        'timing_verified': False}, indent=2) + '\n')
    print('SUMMARY status=' + overall['status'] + ' rm_expected_sn=' + str(args.rm_expected_sn) +
          ' compliant=' + str(len([s for s in compliant if s['status'] == 'PASS'])) + '/' + str(len(compliant)) +
          ' capacity=' + str(len(capacity)) + ' l01_reproduced=' + str(l01_reproduced) +
          ' checks=' + str(overall['checks']) + ' cycles=' + str(overall['cycles']))
    return 0 if compliant_ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
