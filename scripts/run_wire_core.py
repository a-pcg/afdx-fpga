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

run([__import__('sys').executable,'scripts/generate_wire_vectors.py'],'wire_vectors.log',env)
obj=ROOT/'reports/wire_obj'
run([str(VER),'--cc','--compiler','msvc','--default-language','1364-2001','--top-module','afdx_wire_core','--Mdir','reports/wire_obj','rtl/afdx_wire_core.v','rtl/afdx_baseline_core.v','rtl/afdx_udp_tx.v','rtl/afdx_udp_rx.v','rtl/eth_crc32_byte.v','rtl/eth_mac_tx.v','rtl/eth_mac_rx.v'],'wire_verilator.log',env)
cpp=list(obj.glob('*.cpp'))+[INC/'verilated.cpp',INC/'verilated_threads.cpp',ROOT/'tb/wire_core.cpp']
args=['/nologo','/std:c++17','/EHsc','/MD','/O2','/DVL_TIME_CONTEXT','/I'+str(INC),'/I'+str(INC/'vltstd'),'/I'+str(ROOT/'reports'),'/I'+str(obj),'/Fe:'+str(obj/'wire.exe')]+list(map(str,cpp))
(obj/'build.rsp').write_text('\n'.join('"'+x+'"' for x in args))
run([str(cl),'@build.rsp'],'wire_msvc.log',env,obj)
print(run([str(obj/'wire.exe')],'wire_simulation.log',env))
