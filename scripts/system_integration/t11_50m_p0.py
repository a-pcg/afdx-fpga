"""P0/P1/P2 evidence bundler for the T11 50 MHz candidate.

Creates/updates reports/t11_50m/<run_id>/ : sources, scripts, SDC, manifest with the
frozen T11 baseline, the candidate diff, both legal run configs, tool versions, the
compatibility-regression numbers and the 50 MHz/4 throughput/BAG evidence.
Never touches reports/frozen_20261020, the T11/T12 reports or the central current pins.
"""
from pathlib import Path
import hashlib, json, subprocess, sys, shutil
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'scripts/system_integration'))
import run_config

RUN_ID='p1p2_20261021'
DEST=ROOT/'reports/t11_50m'/RUN_ID
T11_BASELINE={
 'rtl/afdx_shared_tx.v':'37a4636b7fef2eb10b52ee8982d285990906236ebdb81ede230c36ef369e5505',
 'rtl/eth_mac_tx.v':'f112ed4d80975124b1ea5b6d7cb20169105e1b9a54366097037f7238ca1551d2',
 'rtl/afdx_bag_scheduler64.v':'b8dccf0852d4a01329da4e3546c8757df116a55bb86f18a1f6d78a721baac867',
 'rtl/buffer/afdx_buffered_vl.v':'5d912fe7a78be1bbc7d41716b98c4e7569571ae2f323b0a410c8e677588b9c73',
 'rtl/buffer/afdx_packet_pool.v':'38b2cb1094d0a42c09325ecba45ceb2c9e7f945f527e8cbac45be580d3dbbe8f',
 'rtl/system/afdx_system_top.v':'b6d251aec2a7b420d6fb24a4ed30017955d583a64aa8866a4555629b926c0333',
 'rtl/afdx_udp_tx_shared.v':'2949772d2317e2b31b2bedbfcdf20a5aa646d083b4d8b47f02bc33d57bcf23ac',
}
RTL_CHANGED=['rtl/afdx_shared_tx.v','rtl/afdx_bag_scheduler64.v','rtl/system/afdx_system_top.v',
             'elinx/integration/sys_e5/src/afdx_sys_e5_top.v']
SCRIPTS=['scripts/system_integration/run_config.py','scripts/system_integration/tput_cfg.py',
         'scripts/system_integration/t11_50m_p0.py','scripts/system_integration/t11_50m_msim.py',
         'scripts/rtl_test_build.py','scripts/elinx_integration/run_sys_e5.py',
         'scripts/elinx_integration/route_sys_e5.py','tb/system_tput_cfg.cpp']

def sha(rel):
    return hashlib.sha256((ROOT/rel).read_bytes()).hexdigest()

def load(rel):
    p=ROOT/rel
    return json.loads(p.read_text(encoding='utf-8-sig')) if p.is_file() else None

def tool_versions():
    out={}
    try:
        out['verilator']=subprocess.run([r'C:\Program Files (x86)\eHiWay\eLinx3.0\verilator\verilator.exe','--version'],
            capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=60).stdout.strip()
    except Exception as e:
        out['verilator']='unavailable: %s'%e
    out['python']=sys.version
    return out

def phase_summary(res):
    if not res: return None
    out={}
    for ph in res.get('phases',[]):
        out[ph['phase']]={k:ph[k] for k in ('window_ms','frames','line_mbps','payload_mbps','payload_share',
            'line_idle_fraction','mac_idle','mac_gap','gap_min','gap_max','gap_mean','gap_over_ifg',
            'bag_intervals','bag_violations','bag_min_cycles','bag_expected_cycles','pool_peak',
            'pending_vls_min','pending_vls_max','starve_cycles','enq_cycles_mean','full_retries','sfd_total')}
    return {'config':res['config'],'core_clk_hz':res['core_clk_hz'],'byte_cycles':res['byte_cycles'],
            'cycles_per_ms':res['cycles_per_ms'],'phases':out}

def main():
    DEST.mkdir(parents=True,exist_ok=True)
    (DEST/'rtl').mkdir(exist_ok=True)
    (DEST/'scripts').mkdir(exist_ok=True)
    (DEST/'sdc').mkdir(exist_ok=True)
    files={}
    for rel in RTL_CHANGED:
        dst=DEST/'rtl'/Path(rel).name
        shutil.copyfile(ROOT/rel,dst)
        files[rel]={'baseline_sha256':T11_BASELINE.get(rel),'candidate_sha256':sha(rel),
                    'bytes':(ROOT/rel).stat().st_size,'copy':str(dst.relative_to(ROOT)).replace('\\','/')}
    for rel in SCRIPTS:
        dst=DEST/'scripts'/Path(rel).name
        shutil.copyfile(ROOT/rel,dst)
        files[rel]={'sha256':sha(rel),'bytes':(ROOT/rel).stat().st_size,'copy':str(dst.relative_to(ROOT)).replace('\\','/')}
    (DEST/'sdc/clock_20ns.sdc').write_text('create_clock -name clk -period 20.0 [get_ports {clk}]\n',encoding='utf-8')
    (DEST/'sdc/clock_10ns.sdc').write_text('create_clock -name clk -period 10.0 [get_ports {clk}]\n',encoding='utf-8')
    cfg_compat=run_config.config('compat100'); cfg_cand=run_config.config('cand50')
    p2_compat=load('reports/t11_50m/p1p2_20261021/compat100/tput/result.json')
    p2_cand=load('reports/t11_50m/p1p2_20261021/cand50/tput/result.json')
    gates={}
    for name,rel in [('guard','reports/shared_tx_guard/result.json'),('shared_tx_4','reports/shared_tx_4/result.json'),
                     ('shared_tx_128','reports/shared_tx_128/result.json'),('buffered_vl_4','reports/buffered_vl_4/result.json'),
                     ('buffered_vl_128','reports/buffered_vl_128/result.json'),('buffer_suite','reports/buffer_verification.json'),
                     ('system_loopback','reports/system_loopback/result.json'),
                     ('scheduler64_4','reports/shared_tx_guard/scheduler64_4/result.json'),
                     ('scheduler64_3','reports/shared_tx_guard/scheduler64_3/result.json')]:
        d=load(rel)
        gates[name]={'status':d.get('status'),'path':rel} if d else {'status':'missing','path':rel}
    msim={}
    for tag in ('compat100','cand50'):
        m=load('reports/t11_50m/p1p2_20261021/%s/modelsim/manifest.json'%tag)
        msim[tag]={'stage':m.get('stage'),'result':m.get('result'),'elaboration_parameters':m.get('elaboration_parameters'),
                   'transcript':m.get('transcript'),'transcript_sha256':m.get('transcript_sha256')} if m else None
    manifest={
      'run_id':RUN_ID,
      'purpose':'T11 50 MHz / 100 Mbps candidate: P0 candidate isolation, P1 time base + byte rhythm parameterization, P2 behavioural throughput/BAG feasibility. P3 timing convergence not started (E5 runs by the Lead).',
      'date':'2026-10-21',
      'baseline':{'stage':'T11 frozen (T12 rejected)','rtl':T11_BASELINE},
      'candidate':{'rtl':files,'sdc':{'20ns':'sdc/clock_20ns.sdc','10ns':'sdc/clock_10ns.sdc'},
                   'note':('only afdx_shared_tx.v (divider/guard/scheduler CYCLES_PER_MS), '
                           'afdx_bag_scheduler64.v (CYCLES_PER_MS deadline), afdx_system_top.v (parameter pass-through '
                           'and physical-time RX defaults) and the E5 wrapper changed; pool/MAC/buffered_vl/RX untouched')},
      'run_configs':{'compat100':run_config.describe(cfg_compat),'cand50':run_config.describe(cfg_cand)},
      'e5':{'wrapper_elaboration_parameters':run_config.parse_wrapper_parameters(ROOT/'elinx/integration/sys_e5/src/afdx_sys_e5_top.v'),
            'constraint':'20 ns SDC for the 50 MHz/4 candidate (route_sys_e5.py refuses a period that does not match the elaborated core clock)',
            'scripts':['scripts/elinx_integration/run_sys_e5.py','scripts/elinx_integration/route_sys_e5.py'],
            'status':'E5 runs are executed by the Lead, not by this candidate build'},
      'tools':tool_versions(),
      'p1_p2_results':{'compat100_tput':phase_summary(p2_compat),'cand50_tput':phase_summary(p2_cand),
                       'compat_gates':gates,'modelsim':msim},
      'summary_doc':{'path':'docs/t11_50m_p1p2.md','sha256':sha('docs/t11_50m_p1p2.md')},
      'evidence_files':['compat100/tput/result.json','compat100/tput/run_params.json','compat100/tput/run_config.h',
                        'cand50/tput/result.json','cand50/tput/run_params.json','cand50/tput/run_config.h',
                        'compat100/modelsim/manifest.json','cand50/modelsim/manifest.json',
                        'compat100/modelsim.log','cand50/modelsim.log'],
      'limits':['behavioural simulation only; no synthesis/route/E5 in this build',
                'P3 (20 ns timing convergence, incl. the tag->FSM path) is not started',
                'long frames are the graded saturation case; short/mixed phases are reported separately (BAG-limited '
                'and, for mixed at 4 cycles/byte, limited by the cycle-based LOAD path against a halved wire time)',
                'RX is unchanged and only the 8-VL development subset is exercised; 128 VL RX is not claimed'],
      'not_overwritten':['reports/frozen_20261020/','reports/t8_snapshot/','reports/t9_snapshot/','reports/t10_snapshot/',
                         'reports/t11_snapshot/','reports/t12_snapshot/','central current pins'],
    }
    (DEST/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
    print('manifest:',(DEST/'manifest.json').relative_to(ROOT),'bytes',(DEST/'manifest.json').stat().st_size)
    print('candidate hashes:',{k:v.get('candidate_sha256') or v.get('sha256') for k,v in files.items() if 'afdx_' in k or 'tb/' in k})
if __name__=='__main__':main()
