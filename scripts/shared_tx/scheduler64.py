"""Independent injected-time unit test for the TX-private 64-bit BAG scheduler (T4/S06)."""
from pathlib import Path
import sys,json,hashlib
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'scripts'))
from rtl_test_build import build,execute

def main():
 sources=[ROOT/'rtl/afdx_bag_scheduler64.v'];tb=ROOT/'tb/scheduler64.cpp'
 for n,w in [(4,2),(3,2)]:
  report=ROOT/'reports/shared_tx_guard'/('scheduler64_'+str(n));report.mkdir(parents=True,exist_ok=True)
  (report/'result.json').write_text('{"status":"RUNNING"}\n')
  parameters={'VL_COUNT':n,'INDEX_W':w}
  exe,env=build('afdx_bag_scheduler64',sources,tb,report,parameters)
  manifest={str(p.relative_to(ROOT)).replace('\\','/'):hashlib.sha256(p.read_bytes()).hexdigest() for p in sources+[tb]}
  (report/'inputs.json').write_text(json.dumps({'parameters':parameters,'sources':manifest,'time_source':'injected time_now values; no wall-clock or 2^31-cycle wait','scope':'direct scheduler unit test; not a wrapper or line-level run'},indent=2)+'\n')
  execute([exe,str(n),report/'result.json'],report/'simulation.log',env)
  print((report/'result.json').read_text(),flush=True)
if __name__=='__main__':main()
