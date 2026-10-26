"""Isolated Verilog-2001/Verilator/MSVC test builds for new project modules."""
from pathlib import Path
import os
import subprocess

ROOT=Path(__file__).resolve().parents[1]
INC=ROOT/'third_party/verilator-5.006/include'
VER=Path(r'C:\Program Files (x86)\eHiWay\eLinx3.0\verilator\verilator.exe')
VC=Path(r'C:\Program Files\Microsoft Visual Studio\18\Community\VC\Auxiliary\Build\vcvars64.bat')


def execute(args,log,env,cwd=ROOT,timeout=180):
    with log.open('wb') as stream:
        r=subprocess.run([str(x) for x in args],cwd=cwd,env=env,stdout=stream,stderr=subprocess.STDOUT,timeout=timeout)
    if r.returncode:
        raise RuntimeError('RTL tool failed: '+str(log))


def build(top,sources,harness,report,parameters=None):
    report.mkdir(parents=True,exist_ok=True)
    obj=report/'obj';obj.mkdir(exist_ok=True)
    h=(INC/'verilated_config.h.in').read_text().replace('@PACKAGE_NAME@','Verilator').replace('@PACKAGE_VERSION@','5.006').replace('@VERILATOR_VERSION_INTEGER@','5006000')
    (report/'verilated_config.h').write_text(h)
    env={k.upper():v for k,v in os.environ.items()};env['VERILATOR_ROOT']=str(INC.parent).replace('\\','/')
    args=[VER,'--cc','--compiler','msvc','--default-language','1364-2001','--top-module',top,'--Mdir',obj.relative_to(ROOT).as_posix()]
    args += ['-G'+k+'='+str(v) for k,v in (parameters or {}).items()]
    args += [p.relative_to(ROOT).as_posix() for p in sources]
    execute(args,report/'verilator.log',env)
    setup=subprocess.run('call "'+str(VC)+'" >nul && set',shell=True,capture_output=True,text=True,encoding='utf-8',errors='replace',check=True)
    for line in setup.stdout.splitlines():
        if '=' in line:
            k,v=line.split('=',1)
            if k:env[k.upper()]=v
    compiler=next(Path(x)/'cl.exe' for x in env['PATH'].split(';') if (Path(x)/'cl.exe').is_file())
    cpp=list(obj.glob('*.cpp'))+[INC/'verilated.cpp',INC/'verilated_threads.cpp',harness]
    executable=obj/'test.exe'
    args=['/nologo','/std:c++17','/EHsc','/MD','/O2','/DVL_TIME_CONTEXT','/I'+str(INC),'/I'+str(INC/'vltstd'),'/I'+str(report),'/I'+str(obj),'/Fe:'+str(executable)]+[str(x) for x in cpp]
    (obj/'build.rsp').write_text('\n'.join('"'+x+'"' for x in args),encoding='utf-8')
    execute([compiler,'@build.rsp'],report/'msvc.log',env,obj)
    return executable,env
