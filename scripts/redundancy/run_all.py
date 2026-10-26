"""RX-owned stage replay; never rewrites global regression or overall progress."""
from pathlib import Path
import hashlib,json,subprocess,sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from rtl_test_build import VER,VC,INC
ROOT=Path(__file__).resolve().parents[2]
def main():
    out=ROOT/'reports/rx_stage1';out.mkdir(parents=True,exist_ok=True)
    result=out/'summary.json'
    result.write_text(json.dumps({'status':'RUNNING','timing_verified':False},indent=2)+'\n')
    for name in ['run.py','run_dual.py']:
        try:
            subprocess.run([sys.executable,str(Path(__file__).with_name(name))],cwd=ROOT,check=True)
        except subprocess.CalledProcessError:
            result.write_text(json.dumps({'status':'FAIL','failed_suite':name,'timing_verified':False},indent=2)+'\n')
            raise
    sources=[ROOT/'rtl/rx/afdx_integrity_checker.v',ROOT/'rtl/rx/afdx_rx_merge.v',ROOT/'rtl/rx/afdx_rx_dual.v',ROOT/'rtl/eth_mac_rx.v',ROOT/'rtl/eth_crc32_byte.v',ROOT/'rtl/afdx_udp_rx.v',ROOT/'tb/rx_merge.cpp',ROOT/'tb/rx_dual.cpp']
    sources+=list(Path(__file__).parent.glob('*.py'))
    sources+=[ROOT/'scripts/rtl_test_build.py']
    sources+=[ROOT/'docs/rx/profile.md',ROOT/'docs/rx/stage1.md']
    data={'status':'PASS','profile':'development-v1','merge':json.loads((ROOT/'reports/rx_merge/result.json').read_text()),'dual':json.loads((ROOT/'reports/rx_dual/result.json').read_text()),'negative_controls':{n:json.loads((ROOT/f'reports/{n}/inputs.json').read_text())['negative_control_exit'] for n in ['rx_merge','rx_dual']},'sha256':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sources},'timing_verified':False,'phy_verified':False,'arinc_compliance_verified':False,'capacity_vls':8,'line_rate_verified':False}
    data['toolchain']={'verilator_version':subprocess.run([str(VER),'--version'],capture_output=True,text=True,check=True).stdout.strip(),'verilator_path':str(VER),'msvc_setup':str(VC),'msvc_setup_sha256':hashlib.sha256(VC.read_bytes()).hexdigest(),'python':sys.version,'verilog_language':'1364-2001','compiler':'msvc','parameters':{'rx_merge':{'VL_WIDTH':3,'TAG_WIDTH':32,'TIME_WIDTH':32,'SKEW_MAX':100},'rx_dual':{'VL_COUNT':8,'TIME_WIDTH':64,'SKEW_MAX':100000}},'runtime_include_root':str(INC),'runtime_sha256':{p.relative_to(INC).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(INC.rglob('*')) if p.is_file() and p.suffix in ('.h','.cpp','.in')},'build_response_sha256':{n:hashlib.sha256((ROOT/f'reports/{n}/obj/build.rsp').read_bytes()).hexdigest() for n in ['rx_merge','rx_dual']}}
    result.write_text(json.dumps(data,indent=2)+'\n');print(json.dumps(data['merge']));print(json.dumps(data['dual']))
if __name__=='__main__':main()
