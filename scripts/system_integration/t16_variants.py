"""T16 variant pack runs: synthesize each DUAL_LINE variant (Classic -> VQM -> native pack).

Each variant only differs in rtl/eth_mac_tx.v; shared_tx/system_top/wrapper are the
current DUAL_LINE implementation. No route / no E5 (Lead runs the 20 ns route).
Outputs: reports/t16_variants/<name>/{eth_mac_tx.v, wrapper.v, resource.json, manifest.json}
"""
from pathlib import Path
import hashlib, importlib.util, json, re, shutil, sys
ROOT=Path(r'C:\Users\zhang\Desktop\test\afdx')
OUT=ROOT/'reports/t16_variants'
BASE=ROOT/'reports/t16_run/synth'
SOURCES=['rtl/system/afdx_system_top.v','rtl/afdx_shared_tx.v','rtl/afdx_udp_tx_shared.v',
 'rtl/afdx_bag_scheduler64.v','rtl/eth_mac_tx.v','rtl/eth_crc32_byte.v','rtl/buffer/afdx_buffered_vl.v',
 'rtl/buffer/afdx_vl_queue.v','rtl/buffer/afdx_packet_pool.v','rtl/rx/afdx_rx_dual.v','rtl/rx/afdx_rx_merge.v',
 'rtl/rx/afdx_integrity_checker.v','rtl/afdx_udp_rx.v','rtl/eth_mac_rx.v']
WRAP=ROOT/'elinx/integration/sys_e5/src/afdx_sys_e5_top.v'
LIMIT=53000

def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def load_native():
    spec=importlib.util.spec_from_file_location('native_ro',ROOT/'scripts/m0_native/run.py')
    n=importlib.util.module_from_spec(spec); spec.loader.exec_module(n); return n

def resources(text):
    out={}
    for line in text.splitlines():
        m=re.match(r'^(Total [^:]+)\s*:\s*(\d+)\s*$',line)
        if m: out[m.group(1).strip()]=int(m.group(2))
    return out

def variant(name, mac_src, wrapper_src=None, note=''):
    vdir=OUT/name; vdir.mkdir(parents=True,exist_ok=True)
    mac=Path(mac_src); shutil.copyfile(mac, vdir/'eth_mac_tx.v')
    wrap=Path(wrapper_src) if wrapper_src else WRAP
    shutil.copyfile(wrap, vdir/'wrapper.v')
    files=[]
    for rel in SOURCES:
        files.append(vdir/'eth_mac_tx.v' if rel=='rtl/eth_mac_tx.v' else ROOT/rel)
    files.append(vdir/'wrapper.v')
    n=load_native(); n.BASE=BASE
    result=n.check_case('t16_'+name,'afdx_sys_e5_top',files)
    res=resources((BASE/'t16_'+name/'t16_'+name+'.runs/synth_1/t16_'+name+'_utilization_synth.rpt').read_text(errors='replace'))
    # fall back to the summary text the checker already parsed
    res=result['native_resources'] or res
    lut=res.get('Total LUTs'); ff=res.get('Total FFs'); mem=res.get('Total Memorys')
    ok=(lut is not None and lut<=LIMIT)
    ev={'variant':name,'date':'2026-10-23','note':note,
        'resources':res,'limit_lut':LIMIT,'pass':ok,
        'sources':{'eth_mac_tx.v':sha(vdir/'eth_mac_tx.v'),'wrapper.v':sha(vdir/'wrapper.v'),
                   'shared_tx':sha(ROOT/'rtl/afdx_shared_tx.v'),'system_top':sha(ROOT/'rtl/system/afdx_system_top.v')},
        'elaboration':{'top':'afdx_sys_e5_top','DUAL_LINE':1 if 'DUAL_LINE=0' not in name else 0,
                       'flow':'Classic frontend -> VQM -> native EQ6HL130 pack (no route/E5)'},
        'checkpoint':result.get('checkpoint'),'checkpoint_sha256':result.get('checkpoint_sha256')}
    (vdir/'resource.json').write_text(json.dumps(ev,indent=2)+'\n',encoding='utf-8')
    print(name,'LUT',lut,'FF',ff,'MEM',mem,'pass',ok)
    return ev

def main():
    import argparse
    ap=argparse.ArgumentParser()
    ap.add_argument('--variant',required=True,choices=['v0_second_crc','v1_shared_crc','v2_preserve','dual0_control'])
    ap.add_argument('--mac',required=True)
    ap.add_argument('--wrapper')
    ap.add_argument('--note',default='')
    a=ap.parse_args()
    OUT.mkdir(parents=True,exist_ok=True)
    variant(a.variant,a.mac,a.wrapper,a.note)
if __name__=='__main__':main()
