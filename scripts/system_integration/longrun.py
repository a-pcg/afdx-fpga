"""Long-run stability test on the joint AFDX top (current RTL, no TX/wrapper change).

4 VL, mixed payload lengths 1/17/63/1471 B, legal BAG spacing (bag_exp=0 = 1 ms at
100 MHz), configurable frame count. Writes reports/system_longrun/result.json with
delivered/rejected/full_drops/bad_fcs/max_sfd_time_delta/SN-continuity plus wall
clock timing and a projection for larger frame counts.
"""
from pathlib import Path
import argparse, hashlib, json, sys, time
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
from rtl_test_build import build, execute

NAMES = ['rtl/system/afdx_system_top.v', 'rtl/afdx_shared_tx.v', 'rtl/afdx_udp_tx_shared.v',
         'rtl/afdx_bag_scheduler.v', 'rtl/afdx_bag_scheduler64.v', 'rtl/eth_mac_tx.v',
         'rtl/eth_crc32_byte.v', 'rtl/buffer/afdx_buffered_vl.v', 'rtl/buffer/afdx_vl_queue.v',
         'rtl/buffer/afdx_packet_pool.v', 'rtl/rx/afdx_rx_dual.v', 'rtl/rx/afdx_rx_merge.v',
         'rtl/rx/afdx_integrity_checker.v', 'rtl/afdx_udp_rx.v', 'rtl/eth_mac_rx.v']
CORE_CLK_HZ = 100000000.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--frames', type=int, default=10000, help='total frames (4 VLs, rounded down to a multiple of 4)')
    ap.add_argument('--out', default='reports/system_longrun')
    args = ap.parse_args()
    per_vl = max(1, args.frames // 4)
    total = per_vl * 4
    out = ROOT / args.out
    out.mkdir(parents=True, exist_ok=True)
    sources = [ROOT / n for n in NAMES]
    tb = ROOT / 'tb/system_longrun.cpp'
    t_build = time.perf_counter()
    exe, env = build('afdx_system_top', sources, tb, out)
    build_s = time.perf_counter() - t_build
    t_sim = time.perf_counter()
    import subprocess
    with open(out / 'simulation.log', 'w') as logf:
        proc = subprocess.run([str(exe), str(per_vl), str(out / 'result.json')],
                              stdout=logf, stderr=subprocess.STDOUT, timeout=1800, env=env)
    if proc.returncode != 0:
        raise SystemExit('longrun simulation failed, see simulation.log')
    sim_s = time.perf_counter() - t_sim
    result = json.loads((out / 'result.json').read_text())
    sim_time_s = result['cycles'] / CORE_CLK_HZ
    result['simulated_time_s'] = round(sim_time_s, 4)
    result['wall_seconds'] = {'build': round(build_s, 1), 'simulation': round(sim_s, 1)}
    if sim_s > 0:
        result['projection'] = {
            'frames_per_wall_second': round(total / sim_s, 1),
            'wall_minutes_for_10000_frames': round(10000 / (total / sim_s) / 60.0, 1),
            'wall_minutes_for_10s_simulated': round(10.0 * CORE_CLK_HZ / (result['cycles'] / sim_s) / 60.0, 1),
        }
    (out / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    (out / 'inputs.json').write_text(json.dumps({
        'files': {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in sources + [tb, Path(__file__)]},
        'frames_requested': args.frames, 'frames_total': total, 'frames_per_vl': per_vl,
        'vl_count': 4, 'payload_lengths': [1, 17, 63, 1471], 'bag_cycles': 100000,
        'core_clk_hz': CORE_CLK_HZ, 'parameters': {},
        'note': 'current afdx_system_top with default single-line loopback (NUM_MACS default); '
                'TX and 50 MHz/E5 wrapper untouched',
        'timing_verified': False}, indent=2) + '\n')
    print(json.dumps({'status': result['status'], 'frames': total, 'per_vl': per_vl,
                      'cycles': result['cycles'], 'delivered': result['delivered'],
                      'wall_simulation_s': round(sim_s, 1),
                      'simulated_time_s': result['simulated_time_s'],
                      'projection': result.get('projection')}))


if __name__ == '__main__':
    main()
