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
for n,w in [(4,2),(128,7)]:
 obj=ROOT/('reports/scheduler_obj_'+str(n))
 run([str(VER),'--cc','--compiler','msvc','--default-language','1364-2001','--top-module','afdx_bag_scheduler','--Mdir','reports/'+obj.name,'-GVL_COUNT='+str(n),'-GINDEX_W='+str(w),'rtl/afdx_bag_scheduler.v'],'scheduler_verilator_'+str(n)+'.log',env)
 cpp=list(obj.glob('*.cpp'))+[INC/'verilated.cpp',INC/'verilated_threads.cpp',ROOT/'tb/scheduler.cpp']
 args=['/nologo','/std:c++17','/EHsc','/MD','/O2','/DVL_TIME_CONTEXT','/DTEST_VL_COUNT='+str(n),'/I'+str(INC),'/I'+str(INC/'vltstd'),'/I'+str(ROOT/'reports'),'/I'+str(obj),'/Fe:'+str(obj/'scheduler.exe')]+list(map(str,cpp))
 (obj/'build.rsp').write_text('\n'.join('"'+x+'"' for x in args))
 run([str(cl),'@build.rsp'],'scheduler_msvc_'+str(n)+'.log',env,obj)
 run([str(obj/'scheduler.exe')],'scheduler_sim_'+str(n)+'.log',env)
 print((ROOT/('reports/scheduler_'+str(n)+'.json')).read_text())
