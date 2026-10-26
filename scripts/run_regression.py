"""Build and run actual RTL using eLinx's Verilator 5.006 + local MSVC."""
from pathlib import Path
import subprocess,os,json,sys,time
ROOT=Path(__file__).resolve().parents[1]
VER=Path(r'C:\Program Files (x86)\eHiWay\eLinx3.0\verilator\verilator.exe')
VC=Path(r'C:\Program Files\Microsoft Visual Studio\18\Community\VC\Auxiliary\Build\vcvars64.bat')
INC=ROOT/'third_party/verilator-5.006/include'
def run(args,name,env=None,cwd=ROOT):
 t=time.time();r=subprocess.run(args,cwd=cwd,env=env,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,encoding='utf-8',errors='replace',timeout=180)
 (ROOT/'reports'/name).write_text(r.stdout,encoding='utf-8');print(name, 'exit',r.returncode, 'seconds',round(time.time()-t,2))
 if r.returncode: print(r.stdout[-8000:]);raise RuntimeError(name+' failed')
 return r.stdout
def main():
 run([sys.executable,'scripts/generate_vectors.py'],'vectors.log')
 # Header generated from the unchanged upstream .in template; runtime version matches binary.
 s=(INC/'verilated_config.h.in').read_text().replace('@PACKAGE_NAME@','Verilator').replace('@PACKAGE_VERSION@','5.006').replace('@VERILATOR_VERSION_INTEGER@','5006000')
 (ROOT/'reports/verilated_config.h').write_text(s)
 env={k.upper():v for k,v in os.environ.items()};env['VERILATOR_ROOT']=str(INC.parent).replace('\\','/')
 run([str(VER),'--cc','--compiler','msvc','--default-language','1364-2001','--top-module','afdx_baseline_core','--Mdir','reports/obj_dir','rtl/afdx_baseline_core.v','rtl/afdx_udp_tx.v','rtl/afdx_udp_rx.v'],'verilator.log',env)
 # vcvars is executed once to obtain local compiler environment; no machine settings changed.
 cmd='call "'+str(VC)+'" >nul && set'
 r=subprocess.run(cmd,shell=True,capture_output=True,text=True,encoding='utf-8',errors='replace',check=True)
 for line in r.stdout.splitlines():
  if '=' in line:
   k,v=line.split('=',1)
   if k:env[k.upper()]=v
 obj=ROOT/'reports/obj_dir';cpp=list(obj.glob('*.cpp'))+[INC/'verilated.cpp',INC/'verilated_threads.cpp',ROOT/'tb/baseline.cpp']
 cl=next((Path(x)/'cl.exe' for x in env.get('PATH',env.get('Path','')).split(';') if (Path(x)/'cl.exe').exists()),None)
 if cl is None:raise RuntimeError('MSVC compiler not found after vcvars64')
 args=['/nologo','/std:c++17','/EHsc','/MD','/O2','/DVL_TIME_CONTEXT','/I'+str(INC),'/I'+str(INC/'vltstd'),'/I'+str(ROOT/'reports'),'/I'+str(obj),'/Fe:'+str(obj/'baseline.exe')]+[str(x) for x in cpp]
 (obj/'build.rsp').write_text('\n'.join('"'+x+'"' for x in args),encoding='utf-8')
 run([str(cl),'@build.rsp'],'msvc.log',env,obj)
 results=[]
 for seed in range(20261017,20261027):
  run([str(obj/'baseline.exe'),str(seed)],'simulation_'+str(seed)+'.log',env)
  result=json.loads((ROOT/'reports/simulation.json').read_text());results.append(result)
  (ROOT/'reports'/('simulation_'+str(seed)+'.json')).write_text(json.dumps(result,indent=2)+'\n')
 summary={'status':'PASS','runs':len(results),'checks':sum(x['checks'] for x in results),'tx_frames':sum(x['tx_frames'] for x in results),'rx_frames':sum(x['rx_frames'] for x in results),'bad_frames':sum(x['bad_frames'] for x in results),'results':results}
 (ROOT/'reports/regression.json').write_text(json.dumps(summary,indent=2)+'\n')
 print(json.dumps({k:v for k,v in summary.items() if k!='results'}))
if __name__=='__main__':main()
