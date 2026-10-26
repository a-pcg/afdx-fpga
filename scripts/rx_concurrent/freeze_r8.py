from pathlib import Path
import hashlib,json,shutil
ROOT=Path(__file__).resolve().parents[2]
RTL=['rtl/rx/afdx_rx_dual.v','rtl/rx/afdx_rx_merge.v','rtl/rx/afdx_integrity_checker.v','rtl/eth_mac_rx.v','rtl/eth_crc32_byte.v','rtl/afdx_udp_rx.v']
def main():
 out=ROOT/'reports/rx_r4/r8_frozen';out.mkdir(parents=True,exist_ok=True)
 expected={'rtl/rx/afdx_rx_dual.v':'e146762b','rtl/rx/afdx_rx_merge.v':'b520fb9f','rtl/afdx_udp_rx.v':'1eac9f75'}
 for name,prefix in expected.items():assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest().startswith(prefix),name
 names=RTL+['scripts/rtl_test_build.py','scripts/redundancy/run_all.py','scripts/redundancy/run.py','scripts/redundancy/run_dual.py','scripts/redundancy/reference.py','tb/rx_merge.cpp','tb/rx_dual.cpp','docs/coordination/session_handoff_20261019_lead.md']
 for folder in ['rx_stage1','rx_merge','rx_dual','system_loopback']:
  for filename in ['summary.json','inputs.json','result.json','acceptance.json','simulation.log']:
   p=ROOT/f'reports/{folder}/{filename}'
   if p.is_file():names.append(p.relative_to(ROOT).as_posix())
 manifest={}
 for name in names:
  src=ROOT/name;target=out/name;h=hashlib.sha256(src.read_bytes()).hexdigest()
  if target.exists():assert hashlib.sha256(target.read_bytes()).hexdigest()==h,'existing freeze differs: '+name
  else:target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(src,target)
  manifest[name]=h
 (out/'manifest.json').write_text(json.dumps({'snapshot':'adopted-r8-before-concurrency-and-arming-change','sha256':manifest,'rtl':RTL,'original_artifact_records_copied_not_rerun':True,'timing_status':'99.483MHz WNS -0.052ns historical adopted r8; not 100MHz PASS'},indent=2)+'\n')
 print('Frozen r8:',len(names),'inputs/evidence files')
if __name__=='__main__':main()
