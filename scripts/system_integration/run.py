"""First joint-top end-to-end loopback: shared TX -> wire -> dual-network RX."""
from pathlib import Path
import argparse,os,sys,json,hashlib
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'scripts'))
from rtl_test_build import build,execute
NL=chr(10)
NAMES=['rtl/system/afdx_system_top.v','rtl/afdx_shared_tx.v','rtl/afdx_udp_tx_shared.v',
       'rtl/afdx_bag_scheduler.v','rtl/afdx_bag_scheduler64.v','rtl/eth_mac_tx.v','rtl/eth_crc32_byte.v',
       'rtl/buffer/afdx_buffered_vl.v','rtl/buffer/afdx_vl_queue.v','rtl/buffer/afdx_packet_pool.v',
       'rtl/rx/afdx_rx_dual.v','rtl/rx/afdx_rx_merge.v','rtl/rx/afdx_integrity_checker.v',
       'rtl/afdx_udp_rx.v','rtl/eth_mac_rx.v']
def digest(path):
    return {'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'bytes':path.stat().st_size}
def rel(path):
    return str(path.relative_to(ROOT)).replace(chr(92),'/')
def main(report=None,dual=False):
 sources=[ROOT/n for n in NAMES];tb=ROOT/('tb/system_dual.cpp' if dual else 'tb/system_loopback.cpp')
 report=Path(report).resolve() if report is not None else ROOT/('reports/system_dual' if dual else 'reports/system_loopback');report.mkdir(parents=True,exist_ok=True)
 # Use the RTL defaults (VL_WIDTH=7, SLOT_WIDTH=6, MAX_PER_VL=4). Passing the
 # same values as -G overrides makes Verilator treat MAX_PER_VL as untyped and
 # emit a width warning that is fatal under this tool build.
 parameters={'RM_EXPECTED_SN':int(os.environ.get('RM_EXPECTED_SN','0'))} if os.environ.get('RM_EXPECTED_SN') else {}
 if dual: parameters['NUM_MACS']=2
 (report/'result.json').write_text(json.dumps({'status':'RUNNING'})+NL)
 exe,env=build('afdx_system_top',sources,tb,report,parameters)
 manifest={'stage':'SYS-1','phase':'behavior','parameters':parameters,
           'files':{rel(p):digest(p) for p in sources+[tb,ROOT/'scripts/rtl_test_build.py',ROOT/'scripts/system_integration/run.py']},
           'dual':dual,
           'python':{'executable':sys.executable,'version':sys.version}}
 (report/'inputs.json').write_text(json.dumps(manifest,indent=2)+NL)
 execute([exe,report/'result.json'],report/'simulation.log',env)
 print((report/'result.json').read_text(),flush=True)
if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('--report',type=Path)
 parser.add_argument('--dual',action='store_true')
 args=parser.parse_args()
 try:main(args.report,args.dual)
 except Exception:
  report=args.report if args.report is not None else ROOT/('reports/system_dual' if args.dual else 'reports/system_loopback')
  report.mkdir(parents=True,exist_ok=True);(report/'result.json').write_text('{"status":"FAIL"}\n')
  raise