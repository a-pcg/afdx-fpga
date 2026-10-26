"""T7 directed backpressure test for the grouped pool control pipeline."""
from pathlib import Path
import sys,json,hashlib
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'scripts'))
from rtl_test_build import build,execute
def main():
 rtl=ROOT/'rtl/buffer/afdx_packet_pool.v';tb=ROOT/'tb/pool_backpressure.cpp'
 report=ROOT/'reports/packet_pool_backpressure';report.mkdir(parents=True,exist_ok=True)
 (report/'result.json').write_text('{"status":"RUNNING"}\n')
 parameters={'SLOT_WIDTH':3}
 exe,env=build('afdx_packet_pool',[rtl],tb,report,parameters)
 (report/'inputs.json').write_text(json.dumps({'parameters':parameters,'rtl_sha256':hashlib.sha256(rtl.read_bytes()).hexdigest(),'testbench_sha256':hashlib.sha256(tb.read_bytes()).hexdigest(),'seed':20261020,'model':'read/write commit latency +1; five-field lockstep; random ready; reset with full pipeline; same-cycle write/read'},indent=2)+'\n')
 execute([exe,report/'result.json'],report/'simulation.log',env)
 print((report/'result.json').read_text(),flush=True)
if __name__=='__main__':main()
