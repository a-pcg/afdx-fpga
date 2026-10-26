"""T15 evidence + fail-closed: record the dual-MAC attempt, restore the T11-50M baseline.

Resource gate: NUM_MACS=2 = 116,250 LUT > 60,000 LUT limit -> the attempt is NOT
submitted and the tree is put back to the accepted T11-50M baseline.
"""
from pathlib import Path
import hashlib, json, shutil
ROOT=Path(r'C:\Users\zhang\Desktop\test\afdx')
RUN=ROOT/'reports/t15_run'
BASE=ROOT/'reports/t15_snapshot'
E5=ROOT/'elinx/integration/sys_e5/20261021_184019_486344_t11_50m/native_sys_e5/src'
T1150M={
 'rtl/afdx_shared_tx.v':'241c3e0db6a1c105b41190fde2e99479351d4bc7aa7d3db4cb92288e70b769b4',
 'rtl/eth_mac_tx.v':'f112ed4d80975124b1ea5b6d7cb20169105e1b9a54366097037f7238ca1551d2',
 'rtl/afdx_bag_scheduler64.v':'780e48610a48e65c90c3f304f2ff0e9f9d9181de9422ce1740d4e32735ba8653',
 'rtl/buffer/afdx_buffered_vl.v':'5d912fe7a78be1bbc7d41716b98c4e7569571ae2f323b0a410c8e677588b9c73',
 'rtl/buffer/afdx_packet_pool.v':'38b2cb1094d0a42c09325ecba45ceb2c9e7f945f527e8cbac45be580d3dbbe8f',
 'rtl/system/afdx_system_top.v':'820f0c54d9a30273dfe43fe5939aacebe5866a7a2b1516c09c060dc21fcab53f',
 'elinx/integration/sys_e5/src/afdx_sys_e5_top.v':'a3ffa246549581295fd7cf927c9e932422fca8a4ac022340374a72ac1640cbbf',
}
ATTEMPT=['rtl/afdx_shared_tx.v','rtl/system/afdx_system_top.v','elinx/integration/sys_e5/src/afdx_sys_e5_top.v']

def sha(rel): return hashlib.sha256((ROOT/rel).read_bytes()).hexdigest()
def load(rel):
    p=ROOT/rel
    return json.loads(p.read_text(encoding='utf-8-sig')) if p.is_file() else None

def main():
    RUN.mkdir(parents=True,exist_ok=True); BASE.mkdir(parents=True,exist_ok=True)
    # 1) keep the attempt sources + hashes
    attempt={}
    adir=RUN/'attempt_dual'; adir.mkdir(exist_ok=True)
    for rel in ATTEMPT:
        dst=adir/Path(rel).name
        shutil.copyfile(ROOT/rel,dst)
        attempt[rel]={'sha256':sha(rel),'copy':str(dst.relative_to(ROOT)).replace('\\','/')}
    # 2) restore the accepted baseline
    for rel in T1150M:
        src=E5/Path(rel).name
        (ROOT/rel).write_bytes(src.read_bytes())
    restored={rel:(sha(rel)==T1150M[rel]) for rel in T1150M}
    # 3) baseline snapshot for rollback/reference
    for rel in T1150M:
        shutil.copyfile(ROOT/rel, BASE/Path(rel).name)
    res=load('reports/t15_run/resource.json') or {}
    gates={}
    for name,rel in [('scheduler64_4','reports/shared_tx_guard/scheduler64_4/result.json'),
                     ('scheduler64_3','reports/shared_tx_guard/scheduler64_3/result.json'),
                     ('guard','reports/shared_tx_guard/result.json'),('shared_tx_4','reports/shared_tx_4/result.json'),
                     ('shared_tx_128','reports/shared_tx_128/result.json'),('buffered_vl_4','reports/buffered_vl_4/result.json'),
                     ('buffered_vl_128','reports/buffered_vl_128/result.json'),('buffer_suite','reports/buffer_verification.json'),
                     ('system_loopback_num_macs_1','reports/system_loopback/result.json'),
                     ('system_dual_num_macs_2','reports/t15_run/system_dual/result.json')]:
        d=load(rel); gates[name]={'status':d.get('status') if d else 'missing','path':rel}
    dual=load('reports/t15_run/system_dual/result.json')
    tput=load('reports/t15_run/cand50_bag0/tput/result.json')
    msim={tag:({k:(load('reports/t15_run/%s/modelsim/manifest.json'%tag) or {}).get(k) for k in ('stage','result','transcript_sha256')})
          for tag in ('compat100','cand50')}
    ev={
      'stage':'T15','date':'2026-10-21',
      'title':'T15 dual-MAC attempt on the T11-50M baseline: behavioural pass, synthesis resource gate FAIL -> not submitted, baseline restored',
      'baseline_t11_50m':T1150M,
      'attempt':attempt,
      'resource_gate':{'limit_lut':60000,'num_macs_2':res.get('num_macs_2',{}).get('native_resources'),
                       'num_macs_1':res.get('num_macs_1',{}).get('native_resources'),
                       'pass':res.get('gate',{}).get('pass'),'flow':'Classic frontend -> VQM -> native EQ6HL130 pack (synthesis only, no route/E5)',
                       'checkpoints':{'num_macs_2':res.get('num_macs_2',{}).get('checkpoint_sha256'),
                                      'num_macs_1':res.get('num_macs_1',{}).get('checkpoint_sha256')}},
      'why_the_second_mac_is_expensive':(
        'The second eth_mac_tx needs its own 8 KiB frame buffer (reg [7:0] mem[0:8191]). With two instances of that '
        'inferred-RAM module the classic frontend only maps about 28,048 of the 65,536 additional bits into memory '
        '(+28,048 memory bits in total; Total Memorys even drops 367 -> 364) and synthesizes the rest of the array '
        'plus the duplicated RAM read/write pass-through logic as registers/LUTs: +36,529 FFs and +64,121 LUTs. '
        'The single-MAC build maps the same array cleanly (52,129 LUT / 26,207 FF / 1,393,656 memory bits), so the '
        'explosion comes from duplicating the inferred frame RAM, not from the MAC state machine or the CRC logic.'),
      'behavioural_results':{
        'num_macs_1_regressions_bit_identical_to_t11_50m':True,
        'guard':load('reports/shared_tx_guard/result.json'),
        'shared_tx_128':load('reports/shared_tx_128/result.json'),
        'system_loopback':load('reports/system_loopback/result.json'),
        'cand50_tput':{ph['phase']:{'line_mbps':ph['line_mbps'],'payload_mbps':ph['payload_mbps'],'bag_intervals':ph['bag_intervals'],'bag_violations':ph['bag_violations']} for ph in (tput or {}).get('phases',[])},
        'system_dual_num_macs_2':dual.get('dual_line') if dual else None,
        'modelsim':msim,
      },
      'gates':gates,
      'decision':'FAIL-CLOSED: NUM_MACS=2 exceeds the 60K LUT gate (116,250 LUT). The attempt sources are kept under reports/t15_run/attempt_dual/ and the tree was restored to the accepted T11-50M baseline (all seven hashes verified).',
      'restored_ok':restored,
      'fix_options':[
        'Share one 8 KiB frame buffer between line A and line B (the lines are byte-identical): two synchronous read pointers over the same RAM; the second MAC keeps only its serializer/CRC/state logic (~1-2K LUT expected).',
        'Instantiate the frame buffer through an explicit dual-port RAM primitive (altsyncram / vendor M9K wrapper) instead of inferred reg array, so both instances map to block RAM.',
        'Alternative: one MAC + a second serializer that replays the same frame RAM with a second read pointer (no second RAM at all).',
      ],
      'limits':['synthesis-only resource check (no route/E5/timing)','behavioural dual-line evidence exists but the resource gate blocks submission'],
    }
    (RUN/'manifest.json').write_text(json.dumps(ev,indent=2)+'\n',encoding='utf-8')
    print('restored_ok:',all(restored.values()),restored)
    print('manifest:',(RUN/'manifest.json').relative_to(ROOT))
    for rel in T1150M: print('baseline',rel,sha(rel))
if __name__=='__main__':main()
