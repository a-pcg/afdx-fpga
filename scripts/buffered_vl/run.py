"""Pool and descriptor queues connected in synthesizable RTL, with a byte scoreboard."""
from pathlib import Path
import sys,json,hashlib
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'scripts'))
from rtl_test_build import build,execute
def main():
 for vw,sw in [(2,3),(7,6)]:
  report=ROOT/'reports'/('buffered_vl_'+str(1<<vw));names=['afdx_buffered_vl.v','afdx_vl_queue.v','afdx_packet_pool.v']
  rtl=[ROOT/'rtl/buffer'/n for n in names];tb=ROOT/'tb/buffered_vl.cpp';parameters={'VL_WIDTH':vw,'SLOT_WIDTH':sw}
  report.mkdir(parents=True,exist_ok=True)
  (report/'result.json').write_text(json.dumps({'status':'RUNNING'})+'\n')
  exe,env=build('afdx_buffered_vl',rtl,tb,report,parameters)
  manifest={str(p.relative_to(ROOT)).replace('\\','/'):hashlib.sha256(p.read_bytes()).hexdigest() for p in rtl+[tb]}
  (report/'inputs.json').write_text(json.dumps({'parameters':parameters,'seed':20261020,'sources':manifest},indent=2)+'\n')
  execute([exe,1<<vw,1<<sw,report/'result.json'],report/'simulation.log',env)
  print((report/'result.json').read_text(),flush=True)
if __name__=='__main__':main()
