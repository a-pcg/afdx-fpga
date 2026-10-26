from pathlib import Path
import argparse,hashlib,json,sys
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts'))
from rtl_test_build import build,execute
from vectors import generate
RTL=['rtl/rx/afdx_rx_dual.v','rtl/rx/afdx_rx_merge.v','rtl/rx/afdx_integrity_checker.v','rtl/eth_mac_rx.v','rtl/eth_crc32_byte.v','rtl/afdx_udp_rx.v']
def main():
 a=argparse.ArgumentParser();a.add_argument('--tag',required=True);o=a.parse_args();assert o.tag in ['r8','independent_arming']
 report=ROOT/f'reports/rx_concurrent_{o.tag}';vec=ROOT/'vectors/rx_concurrent';report.mkdir(parents=True,exist_ok=True)
 generate(vec)
 if o.tag=='r8':
  frozen=json.loads((ROOT/'reports/rx_r4/r8_frozen/manifest.json').read_text())
  for name in RTL:assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==frozen['sha256'][name],name
 files=[ROOT/'tb/rx_concurrent_probe.v']+[ROOT/name for name in RTL];tb=ROOT/'tb/rx_concurrent.cpp'
 paths=files+[tb,Path(__file__),Path(__file__).with_name('vectors.py'),ROOT/'scripts/rtl_test_build.py']+list(vec.glob('*'))
 (report/'inputs_before.json').write_text(json.dumps({'tag':o.tag,'sha256':{p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in paths},'top':'rx_concurrent_probe','parameters':{'SKEW_MAX':100000,'TIME_WIDTH':64},'stimulus':'fixed-cycle independent lanes; no per-batch DUT drain','source_snapshot':'adopted-r8' if o.tag=='r8' else 'r8 plus independent MAC arming'},indent=2)+'\n')
 (report/'result.json').write_text('{"status":"RUNNING"}\n')
 exe,env=build('rx_concurrent_probe',files,tb,report)
 execute([exe,vec/'events.txt',vec/'plans.txt',report/'result.json',report/'trace.log'],report/'simulation.log',env)
 print((report/'result.json').read_text())
if __name__=='__main__':main()
