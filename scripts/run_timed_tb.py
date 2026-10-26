from pathlib import Path
import os,subprocess,json
from run_regression import ROOT,VER,VC,INC,run
# Use the same validated native Windows runtime as the baseline.
s=(INC/'verilated_config.h.in').read_text().replace('@PACKAGE_NAME@','Verilator').replace('@PACKAGE_VERSION@','5.006').replace('@VERILATOR_VERSION_INTEGER@','5006000')
(ROOT/'reports/verilated_config.h').write_text(s)
env={k.upper():v for k,v in os.environ.items()};env['VERILATOR_ROOT']=str(INC.parent).replace('\\','/')
r=subprocess.run('call "'+str(VC)+'" >nul && set',shell=True,capture_output=True,text=True,encoding='utf-8',errors='replace',check=True)
for line in r.stdout.splitlines():
 if '=' in line:
  k,v=line.split('=',1)
  if k:env[k.upper()]=v
cl=next(Path(x)/'cl.exe' for x in env['PATH'].split(';') if (Path(x)/'cl.exe').exists())

obj=ROOT/'reports/timed_obj'
run([str(VER),'--cc','--timing','--compiler','msvc','--top-module','tb_afdx_baseline','--Mdir','reports/timed_obj','-Wno-WIDTH','rtl/afdx_baseline_core.v','rtl/afdx_udp_tx.v','rtl/afdx_udp_rx.v','tb/tb_afdx_baseline.v'],'timed_verilator.log',env)
cpp=list(obj.glob('*.cpp'))+[INC/'verilated.cpp',INC/'verilated_threads.cpp',INC/'verilated_timing.cpp',ROOT/'tb/timed_main.cpp']
args=['/nologo','/std:c++20','/EHsc','/MD','/O2','/DVL_TIME_CONTEXT','/I'+str(INC),'/I'+str(INC/'vltstd'),'/I'+str(ROOT/'reports'),'/I'+str(obj),'/Fe:'+str(obj/'timed.exe')]+list(map(str,cpp))
(obj/'build.rsp').write_text('\n'.join('"'+x+'"' for x in args))
run([str(cl),'@build.rsp'],'timed_msvc.log',env,obj)
output=run([str(obj/'timed.exe')],'timed_simulation.log',env)
if 'PASS tb_afdx_baseline failures=0' not in output:raise RuntimeError('testbench did not report success')
print(output)
