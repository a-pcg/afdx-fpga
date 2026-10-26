"""Posthoc dependency inventory only. Does not rebuild/rerun or edit frozen RTL."""
from pathlib import Path
from datetime import datetime,timezone
import hashlib,json,os,re,subprocess,sys
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts'))
from rtl_test_build import VER,VC,INC
OUT=ROOT/'reports/rx_r2'
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
    OUT.mkdir(parents=True,exist_ok=True)
    originals={};records={}
    for label,name in [('stage1_summary','reports/rx_stage1/summary.json'),('stage1_merge_inputs','reports/rx_merge/inputs.json'),('stage1_dual_inputs','reports/rx_dual/inputs.json'),('r2_inputs','reports/rx_r2/inputs.json')]:
        data=json.loads((ROOT/name).read_text());records[label]={'path':name,'sha256_now':digest(ROOT/name),'record_phase':'execution_completion_record; not a pre-execution immutable snapshot'}
        for p,h in data.get('sha256',{}).items():originals.setdefault(str((ROOT/p).resolve()),[]).append({'record':name,'sha256':h})
        if label=='stage1_summary':
            for p,h in data['toolchain']['runtime_sha256'].items():originals.setdefault(str((INC/p).resolve()),[]).append({'record':name+'#toolchain/runtime_sha256','sha256':h})
            originals.setdefault(str(VC.resolve()),[]).append({'record':name+'#toolchain/msvc_setup_sha256','sha256':data['toolchain']['msvc_setup_sha256']})
            for suite,h in data['toolchain']['build_response_sha256'].items():originals.setdefault(str((ROOT/f'reports/{suite}/obj/build.rsp').resolve()),[]).append({'record':name+'#toolchain/build_response_sha256','sha256':h})
    files={}
    def add(p,role):
        p=p.resolve()
        if not p.is_file():raise FileNotFoundError(p)
        key=str(p);h=digest(p);prior=originals.get(key,[])
        for old in prior:
            if old['sha256']!=h:raise RuntimeError('changed since original execution record: '+key)
        if key in files:
            if role not in files[key]['roles']:files[key]['roles'].append(role)
            return
        try:name=p.relative_to(ROOT).as_posix()
        except ValueError:name=p.as_posix()
        files[key]={'path':name,'sha256':h,'bytes':p.stat().st_size,'roles':[role],'provenance':'original_completion_hash_reverified_now' if prior else 'posthoc_hash_only_no_historical_identity_proof','original_records':prior}
    for p in (ROOT/'scripts/redundancy').glob('*.py'):add(p,'run/generate/reference/inventory script')
    add(ROOT/'scripts/rtl_test_build.py','shared compile/execute helper')
    rtl_names=['rtl/rx/afdx_rx_dual.v','rtl/rx/afdx_rx_merge.v','rtl/rx/afdx_integrity_checker.v','rtl/eth_mac_rx.v','rtl/eth_crc32_byte.v','rtl/afdx_udp_rx.v']
    for name in rtl_names:add(ROOT/name,'frozen recursive DUT RTL')
    for name in ['tb/rx_merge.cpp','tb/rx_dual.cpp','tb/rx_r2_characterize.cpp','tb/rx_r2_probe.v']:add(ROOT/name,'actual testbench/passive observer')
    for folder in ['rx_merge','rx_dual','rx_r2']:
        for p in (ROOT/'vectors'/folder).rglob('*'):
            if p.is_file():add(p,'actual stimulus/golden/anchor/negative-control vector')
        report=ROOT/'reports'/folder
        for pattern in ['*.json','*.log','*.jsonl','verilated_config.h']:
            for p in report.glob(pattern):
                if p.name in ['dependencies.json']:continue
                add(p,'execution record/generated build configuration')
        for pattern in ['*.cpp','*.h','*.dat','*.d','*.rsp','*.exe']:
            for p in (report/'obj').glob(pattern):add(p,'generated compiler input or executable artifact')
    add(ROOT/'reports/rx_stage1/summary.json','original aggregate completion record')
    add(ROOT/'docs/coordination/rx_handoff.md','shared RX handoff documentation')
    for p in (ROOT/'docs/rx').glob('*.md'):add(p,'profile/interface/limitation documentation')
    for p in INC.rglob('*'):
        if p.is_file() and p.suffix in ['.h','.cpp','.in']:add(p,'bundled Verilator runtime headers/sources')
    for p in [VER,VC,Path(sys.executable)]:add(p,'external tool/setup file observed posthoc')
    setup=subprocess.run('call "'+str(VC)+'" >nul && set',shell=True,capture_output=True,text=True,encoding='utf-8',errors='replace',check=True)
    env={k.upper():v for k,v in os.environ.items()}
    for line in setup.stdout.splitlines():
        if '=' in line:
            k,v=line.split('=',1)
            if k:env[k.upper()]=v
    toolpaths={}
    for name in ['cl.exe','link.exe','dumpbin.exe']:
        p=next(Path(x)/name for x in env['PATH'].split(';') if (Path(x)/name).is_file());add(p,'current external compiler/linker/inspector');toolpaths[name]=str(p)
    # Read-only direct dynamic dependency listing; do not claim a full historical OS image.
    dynamic={}
    for suite in ['rx_merge','rx_dual','rx_r2']:
        exe=ROOT/f'reports/{suite}/obj/test.exe'
        r=subprocess.run([toolpaths['dumpbin.exe'],'/dependents',str(exe)],capture_output=True,text=True,env=env,check=True)
        log=OUT/f'{suite}_dependents_posthoc.log';log.write_text(r.stdout);add(log,'posthoc read-only direct DLL dependency inspection')
        deps=[]
        for name in re.findall(r'^\s+([\w.\-]+\.dll)\s*$',r.stdout,re.I|re.M):
            candidates=[exe.parent/name,Path(env.get('SYSTEMROOT',r'C:\Windows'))/'System32'/name]+[Path(x)/name for x in env['PATH'].split(';') if x]
            p=next((p for p in candidates if p.is_file()),None)
            if p:add(p,'resolved current direct executable DLL dependency')
            deps.append({'name':name,'resolved_current_path':str(p) if p else None})
        dynamic[suite]=deps
    graph={'afdx_rx_dual':['eth_mac_rx','afdx_udp_rx','afdx_rx_merge'],'eth_mac_rx':['eth_crc32_byte'],'afdx_rx_merge':['afdx_integrity_checker'],'afdx_udp_rx':[],'afdx_integrity_checker':[],'eth_crc32_byte':[],'rx_r2_probe':['afdx_rx_dual']}
    includes={name:re.findall(r'`include\s+"([^"]+)"',(ROOT/name).read_text()) for name in rtl_names+['tb/rx_r2_probe.v']}
    manifest={'kind':'DEPENDENCY_INVENTORY_SUPPLEMENT','created_at_utc':datetime.now(timezone.utc).isoformat(),'historical_records_unchanged':records,'reexecution_performed':False,'rtl_modified':False,'inventory_entries':sorted(files.values(),key=lambda x:x['path']),'recursive_rtl_graph':graph,'rtl_include_directives':includes,'commands':{'stage1_original_replay':'python scripts/redundancy/run_all.py','new_directed_only':'python scripts/redundancy/run_r2.py','inventory_only':'python scripts/redundancy/collect_dependencies.py'},'parameters_and_configuration':{'stage1_merge':{'VL_WIDTH':3,'TAG_WIDTH':32,'TIME_WIDTH':32,'SKEW_MAX':100,'seed':20261018},'stage1_dual_and_r2':{'VL_COUNT':8,'TAG_WIDTH':32,'TIME_WIDTH':64,'SKEW_MAX':100000,'common_sync_reset':True,'byte_cycles':8,'DA_VLID_range':[1,8],'src_ip':'192.168.1.1','dst_ip':'192.168.1.2','udp_ports':[1000,1001],'IHL':5,'payload_range':[1,1471]},'external_config_files':[],'configuration_origin':'parameters/static RTL + generator/driver sources; no hidden config import'},'current_toolchain_observation':{'observed_posthoc':True,'python':sys.version,'verilator_version':subprocess.run([str(VER),'--version'],capture_output=True,text=True,check=True).stdout.strip(),'tools':toolpaths,'setup_environment':{k:env[k] for k in ['VCTOOLSINSTALLDIR','VCTOOLSVERSION','WINDOWSSDKDIR','WINDOWSSDKVERSION','INCLUDE','LIB','LIBPATH'] if k in env},'direct_executable_DLL_dependencies':dynamic},'provenance_limits':['Original stage1 records were made at execution completion, not before each invocation. Supplement does not retroactively strengthen them.','Unhashed stage1 negative vector/generated compiler files/tools are inventoried now; historical byte identity cannot be proven by a new hash.','Original source/runtime/build-response hashes are reverified against their original records; any difference stops this collector.','Compiler/Windows SDK header/library roots and versions are recorded; the full external MSVC/SDK/OS installation and transitive system DLL closure are not vendored or fully hashed. Reproduction requires compatible installed toolchain.','New R2 passive hierarchical observer is test-only; neither limitation result is a compliance PASS.']}
    (OUT/'dependencies.json').write_text(json.dumps(manifest,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    print('Dependency inventory entries:',len(files),'; original record hashes reverified; no simulation rerun.')
if __name__=='__main__':main()
