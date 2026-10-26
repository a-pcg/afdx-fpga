"""P2: config-driven sustained TX throughput / BAG feasibility test.

One run config (scripts/system_integration/run_config.py) drives the Verilator
elaboration parameters, the generated run_config.h consumed by tb/system_tput_cfg.cpp,
BAG expectations, window lengths and unit conversion.
"""
from pathlib import Path
import argparse, hashlib, json, sys
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts')); sys.path.insert(0,str(ROOT/'scripts/system_integration'))
from rtl_test_build import build,execute
import run_config
NAMES=['rtl/system/afdx_system_top.v','rtl/afdx_shared_tx.v','rtl/afdx_udp_tx_shared.v',
 'rtl/afdx_bag_scheduler.v','rtl/afdx_bag_scheduler64.v','rtl/eth_mac_tx.v','rtl/eth_crc32_byte.v',
 'rtl/buffer/afdx_buffered_vl.v','rtl/buffer/afdx_vl_queue.v','rtl/buffer/afdx_packet_pool.v',
 'rtl/rx/afdx_rx_dual.v','rtl/rx/afdx_rx_merge.v','rtl/rx/afdx_integrity_checker.v',
 'rtl/afdx_udp_rx.v','rtl/eth_mac_rx.v']
TB='tb/system_tput_cfg.cpp'

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--config',default='compat100',choices=sorted(run_config.CONFIGS))
    ap.add_argument('--bag-exp',type=int,default=0)
    ap.add_argument('--report',type=Path)
    ap.add_argument('--timeout',type=int,default=1800)
    a=ap.parse_args()
    cfg=run_config.config(a.config)
    report=a.report or (ROOT/'reports/t11_50m'/cfg['tag']/'tput')
    report=Path(report)
    if not report.is_absolute(): report=ROOT/report
    report.mkdir(parents=True,exist_ok=True)
    (report/'result.json').write_text(json.dumps({'status':'RUNNING'})+'\n')
    header=run_config.write_header(cfg,report/'run_config.h')
    params=run_config.verilator_parameters(cfg)
    (report/'run_params.json').write_text(json.dumps({
        'config':run_config.describe(cfg),'bag_exp':a.bag_exp,'verilator_parameters':params,
        'test':'P2 sustained 128 VL throughput / BAG feasibility',
        'window':'>=100 ms AND >=1000 frames, after a 64-frame warm-up',
        'files':{n:{'sha256':hashlib.sha256((ROOT/n).read_bytes()).hexdigest(),'bytes':(ROOT/n).stat().st_size} for n in NAMES+[TB]},
        'generated_header':{'path':str(header.relative_to(ROOT)).replace('\\','/'),'sha256':hashlib.sha256(header.read_bytes()).hexdigest()},
    },indent=2)+'\n')
    exe,env=build('afdx_system_top',[ROOT/n for n in NAMES],ROOT/TB,report,params)
    execute([exe,report/'result.json',str(a.bag_exp)],report/'simulation.log',env,timeout=a.timeout)
    result=json.loads((report/'result.json').read_text())
    assert result['status']=='RUNNING'
    print(json.dumps(result,indent=1))

if __name__=='__main__':main()
