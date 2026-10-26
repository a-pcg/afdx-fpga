"""T13 evidence bundler: 8 MAC slots / 8 in-flight grants for mixed-frame throughput."""
from pathlib import Path
import hashlib, json, sys
ROOT=Path(__file__).resolve().parents[2]
DEST=ROOT/'reports/t13_run'
BASELINE={
 'rtl/afdx_shared_tx.v':'241c3e0db6a1c105b41190fde2e99479351d4bc7aa7d3db4cb92288e70b769b4',
 'rtl/eth_mac_tx.v':'f112ed4d80975124b1ea5b6d7cb20169105e1b9a54366097037f7238ca1551d2',
 'rtl/afdx_bag_scheduler64.v':'780e48610a48e65c90c3f304f2ff0e9f9d9181de9422ce1740d4e32735ba8653',
 'rtl/buffer/afdx_buffered_vl.v':'5d912fe7a78be1bbc7d41716b98c4e7569571ae2f323b0a410c8e677588b9c73',
 'rtl/buffer/afdx_packet_pool.v':'38b2cb1094d0a42c09325ecba45ceb2c9e7f945f527e8cbac45be580d3dbbe8f',
 'rtl/system/afdx_system_top.v':'820f0c54d9a30273dfe43fe5939aacebe5866a7a2b1516c09c060dc21fcab53f',
 'elinx/integration/sys_e5/src/afdx_sys_e5_top.v':'a3ffa246549581295fd7cf927c9e932422fca8a4ac022340374a72ac1640cbbf',
}
CHANGED=['rtl/afdx_shared_tx.v','rtl/eth_mac_tx.v','rtl/afdx_bag_scheduler64.v']

def sha(rel): return hashlib.sha256((ROOT/rel).read_bytes()).hexdigest()
def load(rel):
    p=ROOT/rel
    return json.loads(p.read_text(encoding='utf-8-sig')) if p.is_file() else None

def phases(rel):
    d=load(rel)
    if not d: return None
    return {ph['phase']:{k:ph[k] for k in ('window_ms','frames','line_mbps','payload_mbps','line_idle_fraction',
            'mac_idle','mac_gap','gap_min','gap_max','gap_mean','bag_intervals','bag_violations','bag_min_cycles',
            'bag_expected_cycles','pool_peak','starve_cycles','enq_cycles_mean','full_retries')} for ph in d.get('phases',[])}

def main():
    DEST.mkdir(parents=True,exist_ok=True)
    gates={}
    for name,rel in [('scheduler64_4','reports/shared_tx_guard/scheduler64_4/result.json'),
                     ('scheduler64_3','reports/shared_tx_guard/scheduler64_3/result.json'),
                     ('guard','reports/shared_tx_guard/result.json'),('shared_tx_4','reports/shared_tx_4/result.json'),
                     ('shared_tx_128','reports/shared_tx_128/result.json'),('buffered_vl_4','reports/buffered_vl_4/result.json'),
                     ('buffered_vl_128','reports/buffered_vl_128/result.json'),('buffer_suite','reports/buffer_verification.json'),
                     ('system_loopback','reports/system_loopback/result.json')]:
        d=load(rel); gates[name]={'status':d.get('status') if d else 'missing','path':rel}
    msim={}
    for tag in ('compat100','cand50'):
        m=load('reports/t13_run/%s/modelsim/manifest.json'%tag)
        msim[tag]={'stage':m.get('stage'),'result':m.get('result'),'elaboration_parameters':m.get('elaboration_parameters'),
                   'transcript':m.get('transcript'),'transcript_sha256':m.get('transcript_sha256')} if m else None
    ev={
      'stage':'T13',
      'date':'2026-10-21',
      'title':'8 MAC store-and-forward slots + 8 outstanding BAG grants: mixed-frame throughput from 73.70 to 96.41 Mbit/s at 50 MHz/4',
      'baseline':BASELINE,
      'changes':{rel:{'baseline_sha256':BASELINE[rel],'candidate_sha256':sha(rel)} for rel in CHANGED},
      'unchanged':{rel:sha(rel) for rel in ['rtl/buffer/afdx_buffered_vl.v','rtl/buffer/afdx_packet_pool.v',
            'rtl/system/afdx_system_top.v','elinx/integration/sys_e5/src/afdx_sys_e5_top.v']},
      'design':{
        'analysis':('mixed 73.70 Mbit/s had 26% line idle: a 1471 B frame needs READ+SEND ~3.0 k core cycles to load while '
                    'the 4-cycle/byte wire emits a 72-118 B short frame in 288-472 cycles; with only 3 frames of lookahead the '
                    'long frame after the short-frame run (63,18,17 pattern) was still loading when its turn came.'),
        'option_A':('eth_mac_tx 4 -> 8 store-and-forward slots (mem 8x2048 = 16 KiB, write/read_slot 3 bit, occupied[7:0], '
                    'slots_used[3:0]); afdx_shared_tx flight_cnt 0..8 (4 bit), SFD FIFO depth 8, need_pop requires a free MAC '
                    'slot (slots_used<8); afdx_bag_scheduler64 MAX_INFLIGHT 8 with a 4-bit in-flight counter.'),
        'unchanged':'pool/buffered_vl RAM structures, frame format/FCS/IFG/SN/BAG/guard/app responses, cfg_ready quiescence.',
      },
      'throughput':{
        'cand50_bag0':phases('reports/t13_run/cand50_bag0/tput/result.json'),
        'compat100_bag0':phases('reports/t13_run/compat100_bag0/tput/result.json'),
        'cand50_bag_exp3':phases('reports/t13_run/cand50_bag3/tput/result.json'),
      },
      'gates':gates,'modelsim':msim,
      'tests':{rel:sha(rel) for rel in ['tb/system_tput_cfg.cpp','scripts/system_integration/run_config.py',
                         'scripts/system_integration/tput_cfg.py','scripts/system_integration/t13_msim.py']},
      'doc':{'path':'docs/t13_8slot.md','sha256':sha('docs/t13_8slot.md')},
      'rollback':{'dir':'reports/t13_snapshot/','files':{p.name:hashlib.sha256(p.read_bytes()).hexdigest()
                   for p in sorted((ROOT/'reports/t13_snapshot').glob('*.v'))}},
      'rollback_note':('reports/t13_snapshot/ holds the T11-50MHz-candidate sources (shared_tx 241c3e0d, eth_mac_tx f112ed4d, '
                       'scheduler64 780e4861, buffered_vl 5d912fe7, pool 38b2cb10, system_top 820f0c54, E5 wrapper a3ffa246).'),
      'limits':['behavioural simulation only; no E5/synthesis/route in this build',
                'the MAC frame RAM doubles from 8 KiB to 16 KiB (4 -> 8 slots); resource/timing impact is for the Lead E5 run',
                'short-frame phases stay BAG-limited (~128 frames/ms for 128 VLs at 1 ms BAG) and are not held to the long-frame target',
                'RX unchanged (8 VL development subset)'],
    }
    (DEST/'manifest.json').write_text(json.dumps(ev,indent=2)+'\n',encoding='utf-8')
    print('manifest:',(DEST/'manifest.json').relative_to(ROOT),'bytes',(DEST/'manifest.json').stat().st_size)
    for rel in CHANGED: print('hash',rel,sha(rel))
if __name__=='__main__':main()
