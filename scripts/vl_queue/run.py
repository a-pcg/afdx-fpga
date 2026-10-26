"""Exercise real linked-list RTL against an independent deque model."""
from pathlib import Path
import sys,json,hashlib
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'scripts'))
from rtl_test_build import build,execute
def main():
 for vw,nw in [(2,3),(7,6)]:
  report=ROOT/'reports'/('vl_queue_'+str(1<<vw));rtl=ROOT/'rtl/buffer/afdx_vl_queue.v';tb=ROOT/'tb/vl_queue.cpp'
  parameters={'VL_WIDTH':vw,'NODE_WIDTH':nw}
  report.mkdir(parents=True,exist_ok=True)
  (report/'result.json').write_text(json.dumps({'status':'RUNNING'})+'\n')
  exe,env=build('afdx_vl_queue',[rtl],tb,report,parameters)
  (report/'inputs.json').write_text(json.dumps({'parameters':parameters,'seed':20261019,'rtl_sha256':hashlib.sha256(rtl.read_bytes()).hexdigest(),'testbench_sha256':hashlib.sha256(tb.read_bytes()).hexdigest()},indent=2)+'\n')
  execute([exe,1<<vw,1<<nw,report/'result.json'],report/'simulation.log',env)
  print((report/'result.json').read_text(),flush=True)
if __name__=='__main__':main()
