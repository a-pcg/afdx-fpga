from pathlib import Path
import argparse,hashlib,json,subprocess,sys,shutil
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts'))
from rtl_test_build import build,execute
from vectors import image
RTL=['rtl/rx/afdx_rx_dual.v','rtl/rx/afdx_rx_merge.v','rtl/rx/afdx_integrity_checker.v','rtl/eth_mac_rx.v','rtl/eth_crc32_byte.v','rtl/afdx_udp_rx.v']
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--tag',choices=['r9c','fixed'],required=True);ap.add_argument('--enable',default='11',choices=['00','01','10','11']);a=ap.parse_args()
 report=ROOT/f'reports/rx_lane_arming_{a.tag}_{a.enable}';report.mkdir(parents=True,exist_ok=True)
 vec=ROOT/'vectors/rx_lane_arming';vec.mkdir(parents=True,exist_ok=True)
 rows=[]
 for sn,n in enumerate([1,16,17,18,1471]):
  p=bytes((sn*31+i*13)%256 for i in range(n));rows.append(f'{n} {sn} {p.hex()} {image(0,sn,p).hex()}')
 for sn in [42,43]:
  p=bytes((sn+i)%256 for i in range(17));rows.append(f'17 {sn} {p.hex()} {image(0,sn,p).hex()}')
 cases=vec/'frames.txt';cases.write_text('\n'.join(rows)+'\n')
 files=[ROOT/'tb/rx_lane_probe.v']+[ROOT/n for n in RTL];tb=ROOT/'tb/rx_lane_arming.cpp'
 if a.tag=='r9c':
  assert a.enable=='11'
  frozen=json.loads((ROOT/'reports/rx_s07/r9c_frozen/manifest.json').read_text())
  for name in RTL:assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==frozen['sha256'][name]
  params={}
 else:params={'LANE_ENABLE':"2'b"+a.enable}
 paths=files+[tb,Path(__file__),Path(__file__).with_name('vectors.py'),ROOT/'scripts/rtl_test_build.py',cases]
 manifest={'tag':a.tag,'parameters':params,'LANE_ENABLE':a.enable,'byte_ce_phases':[0,3],'sha256':{p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}}
 (report/'inputs_before.json').write_text(json.dumps(manifest,indent=2)+'\n')
 for p in paths:
  target=report/'source_snapshot'/p.relative_to(ROOT);target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,target)
 exe,env=build('rx_lane_probe',files,tb,report,params)
 execute([exe,cases,report/'result.json',report/'trace.log',int(a.tag=='fixed'),int(a.enable,2)],report/'simulation.log',env)
 if a.tag=='r9c':
  with (report/'independent_service_RED.log').open('wb') as f:
   fail=subprocess.run([str(exe),str(cases),str(report/'RED_unexpected.json'),str(report/'RED_trace.log'),'1','3'],env=env,stdout=f,stderr=subprocess.STDOUT)
  assert fail.returncode!=0 and 'healthy lane service blocked' in (report/'independent_service_RED.log').read_text()
  (report/'red_control.json').write_text(json.dumps({'expected_failure_exit':fail.returncode,'expected_requirement':'single healthy lane service while other valid stuck','rtl_modified':False},indent=2)+'\n')
 print((report/'result.json').read_text())
if __name__=='__main__':main()
