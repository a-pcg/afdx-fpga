"""T15 resource check: synthesize the E5 single top with NUM_MACS=1 and NUM_MACS=2.

Runs the same Classic frontend -> VQM -> native EQ6HL130 pack flow the E5 pack uses
(NO route, no E5 timing), and reports the native synthesis resources. The hard gate
is NUM_MACS=2 <= 60K LUTs.
"""
from pathlib import Path
import importlib.util, json, re, shutil
ROOT=Path(r'C:\Users\zhang\Desktop\test\afdx')
BASE=ROOT/'reports/t15_run/synth'
WRAP=ROOT/'elinx/integration/sys_e5/src/afdx_sys_e5_top.v'
SOURCES=['rtl/system/afdx_system_top.v','rtl/afdx_shared_tx.v','rtl/afdx_udp_tx_shared.v',
 'rtl/afdx_bag_scheduler64.v','rtl/eth_mac_tx.v','rtl/eth_crc32_byte.v','rtl/buffer/afdx_buffered_vl.v',
 'rtl/buffer/afdx_vl_queue.v','rtl/buffer/afdx_packet_pool.v','rtl/rx/afdx_rx_dual.v','rtl/rx/afdx_rx_merge.v',
 'rtl/rx/afdx_integrity_checker.v','rtl/afdx_udp_rx.v','rtl/eth_mac_rx.v']
LIMIT_LUT=60000

def load_native():
    spec=importlib.util.spec_from_file_location('native_readonly',ROOT/'scripts/m0_native/run.py')
    n=importlib.util.module_from_spec(spec); spec.loader.exec_module(n); return n

def resources(native_text):
    stats={}
    for line in native_text.splitlines():
        m=re.match(r'^(Total [^:]+)\s*:\s*(\d+)\s*$',line)
        if m: stats[m.group(1).strip()]=int(m.group(2))
    return stats

def one(n,name,wrap_path):
    files=[ROOT/s for s in SOURCES]+[wrap_path]
    result=n.check_case(name,'afdx_sys_e5_top',files)
    return result

def main():
    BASE.mkdir(parents=True,exist_ok=True)
    n=load_native(); n.BASE=BASE
    out={}
    # NUM_MACS=2 (the submitted candidate): current wrapper
    res2=one(n,'t15_synth_dual',WRAP)
    out['num_macs_2']={'native_resources':res2['native_resources'],'checkpoint':res2['checkpoint'],
                       'checkpoint_sha256':res2['checkpoint_sha256'],'status':res2['status']}
    # NUM_MACS=1 control: same wrapper with the localparam flipped
    tdir=BASE/'single_src'; tdir.mkdir(parents=True,exist_ok=True)
    text=WRAP.read_text(encoding='utf-8')
    text1=text.replace('localparam NUM_MACS=2;','localparam NUM_MACS=1;')
    if text1==text: raise RuntimeError('could not flip NUM_MACS in the wrapper copy')
    (tdir/'afdx_sys_e5_top.v').write_text(text1,encoding='utf-8')
    res1=one(n,'t15_synth_single',tdir/'afdx_sys_e5_top.v')
    out['num_macs_1']={'native_resources':res1['native_resources'],'checkpoint':res1['checkpoint'],
                       'checkpoint_sha256':res1['checkpoint_sha256'],'status':res1['status']}
    lut2=out['num_macs_2']['native_resources'].get('Total LUTs')
    lut1=out['num_macs_1']['native_resources'].get('Total LUTs')
    out['gate']={'limit_lut':LIMIT_LUT,'num_macs_2_lut':lut2,'num_macs_1_lut':lut1,
                 'delta_lut':(lut2-lut1) if (lut2 is not None and lut1 is not None) else None,
                 'pass':bool(lut2 is not None and lut2<=LIMIT_LUT)}
    (BASE.parent/'resource.json').write_text(json.dumps(out,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(out['num_macs_1']['native_resources'],indent=1))
    print(json.dumps(out['num_macs_2']['native_resources'],indent=1))
    print('GATE',out['gate'])
    if not out['gate']['pass']: raise SystemExit(3)
if __name__=='__main__':main()
