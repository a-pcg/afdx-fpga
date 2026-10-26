from pathlib import Path
import hashlib,json,shutil
ROOT=Path(__file__).resolve().parents[2]
RTL=['rtl/rx/afdx_rx_dual.v','rtl/rx/afdx_rx_merge.v','rtl/rx/afdx_integrity_checker.v','rtl/eth_mac_rx.v','rtl/eth_crc32_byte.v','rtl/afdx_udp_rx.v']
def main():
 out=ROOT/'reports/rx_s07/r9c_frozen';out.mkdir(parents=True,exist_ok=True)
 assert hashlib.sha256((ROOT/RTL[0]).read_bytes()).hexdigest().startswith('83ca7196')
 names=RTL+['scripts/rtl_test_build.py','scripts/rx_concurrent/vectors.py','tb/rx_concurrent.cpp','tb/rx_concurrent_probe.v','docs/coordination/session_handoff_20261020_codex.md','reports/current_source_pins.json','tb/system_loopback.cpp']
 for folder in ['rx_stage1','rx_merge','rx_dual','system_loopback']:
  for n in ['summary.json','inputs.json','result.json','acceptance.json','simulation.log']:
   p=ROOT/f'reports/{folder}/{n}'
   if p.is_file():names.append(p.relative_to(ROOT).as_posix())
 manifest={}
 for name in names:
  src=ROOT/name;target=out/name;h=hashlib.sha256(src.read_bytes()).hexdigest()
  if target.exists():assert hashlib.sha256(target.read_bytes()).hexdigest()==h,name
  else:target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(src,target)
  manifest[name]=h
 (out/'manifest.json').write_text(json.dumps({'snapshot':'r9c-before-S07-S09','sha256':manifest,'rtl':RTL,'historical_evidence_copied_not_rerun':True,'current_source_pins_copied_not_modified':True},indent=2)+'\n')
 print('Frozen current r9c:',len(names),'source/evidence files')
if __name__=='__main__':main()
