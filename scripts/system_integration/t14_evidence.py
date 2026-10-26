"""T14 evidence: dual-network TX A/B (NUM_MACS parameter, shared encoder)."""
from pathlib import Path
import hashlib, json
ROOT=Path(__file__).resolve().parents[2]
DEST=ROOT/'reports/t14_run'
BASELINE={
 'rtl/afdx_shared_tx.v':'f01ad6f901a9eec273406e2e594b8603fd433e7b3376c5f6c2b4ec667cc698b3',
 'rtl/eth_mac_tx.v':'21f34ce7eedfc9ed1f031943f5176a3b6bf18fec8d625cd732b241a2236fb9fe',
 'rtl/afdx_bag_scheduler64.v':'022319b5b90694270a3707ca9c252262b879274fd9dda09a38d293d7a35b9b22',
 'rtl/buffer/afdx_buffered_vl.v':'5d912fe7a78be1bbc7d41716b98c4e7569571ae2f323b0a410c8e677588b9c73',
 'rtl/buffer/afdx_packet_pool.v':'38b2cb1094d0a42c09325ecba45ceb2c9e7f945f527e8cbac45be580d3dbbe8f',
 'rtl/system/afdx_system_top.v':'820f0c54d9a30273dfe43fe5939aacebe5866a7a2b1516c09c060dc21fcab53f',
 'elinx/integration/sys_e5/src/afdx_sys_e5_top.v':'a3ffa246549581295fd7cf927c9e932422fca8a4ac022340374a72ac1640cbbf',
}
CHANGED=['rtl/afdx_shared_tx.v','rtl/system/afdx_system_top.v','elinx/integration/sys_e5/src/afdx_sys_e5_top.v']
TESTS=['tb/system_dual.cpp','tb/system_loopback.cpp','tb/system_tput_cfg.cpp','tb/tb_afdx_system.v',
       'scripts/system_integration/run.py','scripts/system_integration/tput_cfg.py','scripts/system_integration/t14_msim.py']

def sha(rel): return hashlib.sha256((ROOT/rel).read_bytes()).hexdigest()
def load(rel):
    p=ROOT/rel
    return json.loads(p.read_text(encoding='utf-8-sig')) if p.is_file() else None
def phases(rel):
    d=load(rel)
    if not d: return None
    return {ph['phase']:{k:ph[k] for k in ('window_ms','frames','line_mbps','payload_mbps','line_idle_fraction',
            'mac_idle','bag_intervals','bag_violations','bag_min_cycles','pool_peak','starve_cycles')} for ph in d.get('phases',[])}

def main():
    DEST.mkdir(parents=True,exist_ok=True)
    gates={}
    for name,rel in [('scheduler64_4','reports/shared_tx_guard/scheduler64_4/result.json'),
                     ('scheduler64_3','reports/shared_tx_guard/scheduler64_3/result.json'),
                     ('guard','reports/shared_tx_guard/result.json'),('shared_tx_4','reports/shared_tx_4/result.json'),
                     ('shared_tx_128','reports/shared_tx_128/result.json'),('buffered_vl_4','reports/buffered_vl_4/result.json'),
                     ('buffered_vl_128','reports/buffered_vl_128/result.json'),('buffer_suite','reports/buffer_verification.json'),
                     ('system_loopback_num_macs_1','reports/system_loopback/result.json'),
                     ('system_dual_num_macs_2','reports/t14_run/system_dual/result.json')]:
        d=load(rel); gates[name]={'status':d.get('status') if d else 'missing','path':rel}
    msim={}
    for tag in ('compat100','cand50'):
        m=load('reports/t14_run/%s/modelsim/manifest.json'%tag)
        msim[tag]={'stage':m.get('stage'),'result':m.get('result'),
                   'elaboration_parameters':m.get('elaboration_parameters'),
                   'transcript':m.get('transcript'),'transcript_sha256':m.get('transcript_sha256')} if m else None
    dual=load('reports/t14_run/system_dual/result.json')
    ev={
      'stage':'T14','date':'2026-10-21',
      'title':'Dual-network transmit A/B (NUM_MACS parameter): two MACs behind one shared encoder, line B byte-locked to line A',
      'baseline':BASELINE,
      'changes':{rel:{'baseline_sha256':BASELINE[rel],'candidate_sha256':sha(rel)} for rel in CHANGED},
      'unchanged':{rel:sha(rel) for rel in ['rtl/eth_mac_tx.v','rtl/afdx_bag_scheduler64.v',
            'rtl/buffer/afdx_buffered_vl.v','rtl/buffer/afdx_packet_pool.v']},
      'design':{
        'parameter':'afdx_shared_tx/afdx_system_top NUM_MACS: 1 = old single line (default), 2 = dual line A/B',
        'dual_path':('MAC A is instantiated unconditionally (SFD/time/SN/BAG anchor); with NUM_MACS=2 a second eth_mac_tx (mac_b) '
                     'is generated. Both MACs receive the same encoder data/valid/sop/eop and byte_ce, and the encoder handshake '
                     'requires s_ready = mac_a_ready && mac_b_ready, so the two lines are byte- and cycle-identical. '
                     'mac_free requires a free slot in both MACs (min(slots_used_a, slots_used_b) != 8).'),
        'ports':'afdx_shared_tx: +line_b_data/line_b_valid; afdx_system_top: +tx_line_b_data/tx_line_b_valid; E5 wrapper: NUM_MACS=2 + the 9 pin B-line port pair',
        'unchanged':'pool/encoder RAM structures, frame format/FCS/IFG/SN/BAG/guard/app responses, cfg_ready quiescence, RX',
      },
      'throughput':{'cand50_bag0':phases('reports/t14_run/cand50_bag0/tput/result.json')},
      'dual_line_assertions':dual.get('dual_line') if dual else None,
      'system_num_macs_1':{k:load('reports/system_loopback/result.json').get(k) for k in
                           ('frames_sent','delivered','dropped_by_fault_injection','max_sfd_time_delta','checks','cycles')} if load('reports/system_loopback/result.json') else None,
      'system_num_macs_2':dual,
      'gates':gates,'modelsim':msim,
      'tests':{rel:sha(rel) for rel in TESTS},
      'doc':{'path':'docs/t14_dual_tx.md','sha256':sha('docs/t14_dual_tx.md')},
      'rollback':{'dir':'reports/t14_snapshot/','files':{p.name:hashlib.sha256(p.read_bytes()).hexdigest()
                   for p in sorted((ROOT/'reports/t14_snapshot').glob('*.v'))}},
      'rollback_note':'reports/t14_snapshot/ holds the T13 8-slot baseline sources (shared_tx f01ad6f9, eth_mac_tx 21f34ce7, scheduler64 022319b5, buffered_vl 5d912fe7, pool 38b2cb10, system_top 820f0c54, wrapper a3ffa246).',
      'limits':['behavioural simulation only; no E5/synthesis/route in this build',
                'NUM_MACS=2 doubles the MAC frame RAM (2 x 16 KiB) and adds a second MAC; resource/timing impact is for the Lead E5 run',
                'the joint ModelSim cases still elaborate NUM_MACS=1 (line B parked, asserted by the testbench)',
                'RX unchanged (8 VL development subset)'],
    }
    (DEST/'manifest.json').write_text(json.dumps(ev,indent=2)+'\n',encoding='utf-8')
    print('manifest:',(DEST/'manifest.json').relative_to(ROOT),'bytes',(DEST/'manifest.json').stat().st_size)
    for rel in CHANGED: print('hash',rel,sha(rel))
if __name__=='__main__':main()
