"""System-level RX capacity and full-pool behaviour: VL0..7 x 4 frames + full pool."""
from pathlib import Path
import hashlib
import json
import sys
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts'))
from rtl_test_build import build,execute
NAMES=['rtl/system/afdx_system_top.v','rtl/afdx_shared_tx.v','rtl/afdx_udp_tx_shared.v',
 'rtl/afdx_bag_scheduler.v','rtl/afdx_bag_scheduler64.v','rtl/eth_mac_tx.v','rtl/eth_crc32_byte.v',
 'rtl/buffer/afdx_buffered_vl.v','rtl/buffer/afdx_vl_queue.v','rtl/buffer/afdx_packet_pool.v',
 'rtl/rx/afdx_rx_dual.v','rtl/rx/afdx_rx_merge.v','rtl/rx/afdx_integrity_checker.v',
 'rtl/afdx_udp_rx.v','rtl/eth_mac_rx.v']
TB='tb/system_capacity_rx.cpp'

def main():
    report=ROOT/'reports/system_capacity_rx'
    report.mkdir(parents=True,exist_ok=True)
    (report/'result.json').write_text(json.dumps({'status':'RUNNING'})+'\n')
    exe,env=build('afdx_system_top',[ROOT/n for n in NAMES],ROOT/TB,report,{})
    (report/'inputs.json').write_text(json.dumps({
        'test':'B: system RX capacity (VL0..7 x4 frames) + 64-slot full-pool behaviour',
        'parameters':{'VL_WIDTH':7,'SLOT_WIDTH':6,'MAX_PER_VL':4,'BAG_exp':0,'rx_vls':8},
        'files':{n:{'sha256':hashlib.sha256((ROOT/n).read_bytes()).hexdigest(),'bytes':(ROOT/n).stat().st_size} for n in NAMES+[TB]},
        'note':('Phase 1 uses VL0..7 only (the fixed 8-VL RX subset). Phase 2 fills all 64 pool slots with '
                'VL0..15 while run=0 (VL8..15 are dropped by the RX by design) and then probes one extra '
                'admission on the otherwise empty VL16 queue to isolate the pool-full response.')},indent=2)+'\n')
    execute([exe,report/'result.json'],report/'simulation.log',env)
    result=json.loads((report/'result.json').read_text())
    assert result['status']=='PASS'
    print(json.dumps(result),flush=True)

if __name__=='__main__':main()
