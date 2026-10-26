"""P2 experiment: can the T8 joint TX/RX loopback sustain one line byte every 4 clocks
(50 MHz / 4 = 100 Mbit/s) instead of the frozen 8-clock (100 MHz / 8 = 100 Mbit/s) rate?

The frozen RTL is not touched: the experiment compiles a copy of
elinx/m0_probe/byte_rate4/afdx_shared_tx.v whose byte divider is 2-bit (0..3).
The baseline compiles the frozen rtl/afdx_shared_tx.v (divider 0..7) with the same
TB and cadence=8, so both runs are comparable. Results go to reports/t8_byte_rate4/.
"""
from pathlib import Path
import hashlib
import json
import sys
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts'))
from rtl_test_build import build,execute
BASE_NAMES=['rtl/system/afdx_system_top.v','rtl/afdx_shared_tx.v','rtl/afdx_udp_tx_shared.v',
 'rtl/afdx_bag_scheduler.v','rtl/afdx_bag_scheduler64.v','rtl/eth_mac_tx.v','rtl/eth_crc32_byte.v',
 'rtl/buffer/afdx_buffered_vl.v','rtl/buffer/afdx_vl_queue.v','rtl/buffer/afdx_packet_pool.v',
 'rtl/rx/afdx_rx_dual.v','rtl/rx/afdx_rx_merge.v','rtl/rx/afdx_integrity_checker.v',
 'rtl/afdx_udp_rx.v','rtl/eth_mac_rx.v']
EXPERIMENT_TX='elinx/m0_probe/byte_rate4/afdx_shared_tx.v'
TB='tb/system_byte_rate.cpp'

def digest(path):
 return {'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'bytes':path.stat().st_size}

def run(label,sources,cadence,note):
 report=ROOT/'reports/t8_byte_rate4'/label
 report.mkdir(parents=True,exist_ok=True)
 (report/'result.json').write_text(json.dumps({'status':'RUNNING','cadence':cadence})+'\n')
 exe,env=build('afdx_system_top',[ROOT/n for n in sources],ROOT/TB,report,{})
 (report/'inputs.json').write_text(json.dumps({
   'experiment':'P2 byte-rate4 (50MHz/4 = 100Mbps)',
   'label':label,'cadence':cadence,'testbench':TB,
   'files':{n:digest(ROOT/n) for n in sources+[TB]},
   'frozen_rtl_untouched':True,
   'note':note},indent=2)+'\n')
 execute([exe,report/'result.json',cadence],report/'simulation.log',env)
 result=json.loads((report/'result.json').read_text())
 print(json.dumps(result),flush=True)
 return result

def main():
 frozen=ROOT/'rtl/afdx_shared_tx.v'
 copy=ROOT/EXPERIMENT_TX
 assert copy.is_file(),'run reports/t8_byte_rate4/make_copy.py first'
 baseline=run('baseline_cadence8',BASE_NAMES,8,
   'frozen T8 RTL (rtl/afdx_shared_tx.v divider 0..7), 8 clocks per line byte = 100MHz/8')
 rate4_names=[EXPERIMENT_TX if n=='rtl/afdx_shared_tx.v' else n for n in BASE_NAMES]
 rate4=run('rate4_cadence4',rate4_names,4,
   'experiment copy '+EXPERIMENT_TX+' (divider 0..3), 4 clocks per line byte = 50MHz/4')
 summary={'experiment':'P2 byte-rate4','frozen_rtl':str(frozen.relative_to(ROOT)),
   'frozen_rtl_sha256':digest(frozen)['sha256'],
   'experiment_copy':EXPERIMENT_TX,'experiment_copy_sha256':digest(copy)['sha256'],
   'baseline':baseline,'rate4':rate4,
   'verdict':{
     'rate4_functional': rate4['status']=='PASS',
     'delivered': rate4['delivered'],
     'max_sfd_time_delta': rate4['max_sfd_time_delta'],
     'wire_bytes_per_cycle_exact': (rate4['min_intra_byte_gap']==4 and rate4['max_intra_byte_gap']==4),
     'wire_MBps_at_50MHz': rate4['wire_MBps_at_50MHz'],
     'rx_full_drops': rate4['rx_full_drops'],
     'rx_bad_fcs': rate4['rx_bad_fcs'],
   }}
 out=ROOT/'reports/t8_byte_rate4/byte_rate4_summary.json'
 out.write_text(json.dumps(summary,indent=2)+'\n')
 print('summary ->',out.relative_to(ROOT),flush=True)

if __name__=='__main__':main()
