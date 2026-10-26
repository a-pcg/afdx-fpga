"""Isolated installed-tool compilation and fallback RTL simulation (not ModelSim)."""
from pathlib import Path
import os, subprocess, importlib.util, json, hashlib, shutil, sys

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'reports/elinx_integration'
SRC = ROOT / 'elinx/integration/minimal'
INSTALL = Path(r'C:/Program Files (x86)/eHiWay/eLinx3.0')

def run(argv, name, cwd=ROOT, env=None):
    with (OUT / name).open('wb') as f:
        r = subprocess.run([a.as_posix() if isinstance(a, Path) else str(a) for a in argv], cwd=cwd, env=env, stdout=f,
                           stderr=subprocess.STDOUT, timeout=180)
    if r.returncode:
        raise RuntimeError(f'{name}: exit {r.returncode}')
    return (OUT / name).read_text(errors='replace')

def main():
    OUT.mkdir(exist_ok=True)
    spec = importlib.util.spec_from_file_location('native_readonly', ROOT/'scripts/m0_native/run.py')
    native = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(native)
    native.BASE = ROOT/'elinx/integration'
    if '--sim-only' not in sys.argv:
        result = native.check_case('native_counter', 'counter', [SRC/'counter.v'])
        (OUT/'minimal_compile.json').write_text(json.dumps(result, indent=2))
    inc = ROOT/'third_party/verilator-5.006/include'
    obj = OUT/'counter_obj'
    obj.mkdir(exist_ok=True)
    (OUT/'verilated_config.h').write_text((inc/'verilated_config.h.in').read_text()
        .replace('@PACKAGE_NAME@','Verilator').replace('@PACKAGE_VERSION@','5.006')
        .replace('@VERILATOR_VERSION_INTEGER@','5006000'))
    env = {k.upper(): v for k,v in os.environ.items()}
    env['VERILATOR_ROOT'] = inc.parent.as_posix()
    vc = Path(r'C:/Program Files/Microsoft Visual Studio/18/Community/VC/Auxiliary/Build/vcvars64.bat')
    setup = subprocess.run('call "'+str(vc)+'" >nul && set', shell=True,
                           capture_output=True, text=True, check=True)
    for line in setup.stdout.splitlines():
        if '=' in line:
            k,v = line.split('=',1)
            if k: env[k.upper()] = v
    cl = next(Path(p)/'cl.exe' for p in env['PATH'].split(';') if (Path(p)/'cl.exe').exists())
    run([INSTALL/'verilator/verilator.exe','--cc','--timing','--trace','--compiler','msvc',
         '--top-module','tb_counter','--Mdir',obj,'-Wno-WIDTH','-Wno-TIMESCALEMOD',
         SRC/'counter.v',SRC/'tb_counter.v'], 'minimal_verilator.log', env=env)
    maincpp = obj/'main.cpp'
    maincpp.write_text('#include "Vtb_counter.h"\n#include "verilated.h"\n#include "verilated_vcd_c.h"\n'
        'int main(int argc,char**argv){VerilatedContext c;c.commandArgs(argc,argv);'
        'c.traceEverOn(true);Vtb_counter top{&c};VerilatedVcdC trace;'
        'top.trace(&trace,99);trace.open("counter.vcd");while(!c.gotFinish()){'
        'top.eval();trace.dump(c.time());if(!top.eventsPending())break;c.time(top.nextTimeSlot());}'
        'top.final();trace.close();return 0;}\n')
    cpp = list(obj.glob('V*.cpp')) + [inc/n for n in
          ['verilated.cpp','verilated_threads.cpp','verilated_timing.cpp','verilated_vcd_c.cpp']] + [maincpp]
    args = ['/nologo','/std:c++20','/EHsc','/MD','/O2','/DVL_TIME_CONTEXT',
            '/I'+str(inc),'/I'+str(inc/'vltstd'),'/I'+str(OUT),'/I'+str(obj),
            '/Fe:'+str(obj/'counter.exe')] + list(map(str,cpp))
    (obj/'build.rsp').write_text('\n'.join('"'+x+'"' for x in args))
    run([cl,'@build.rsp'],'minimal_msvc.log',obj,env)
    text = run([obj/'counter.exe'],'minimal_simulation.log',SRC,env)
    if 'PASS counter 40 cycles at 10ns' not in text or 'FAIL' in text:
        raise RuntimeError('Counter self-check did not pass')
    wave = SRC/'counter.vcd'
    if wave.stat().st_size < 100 or '#420000' not in wave.read_text():
        raise RuntimeError('Waveform missing, empty or incomplete')
    vsim = shutil.which('vsim') or shutil.which('modelsim')
    data = {'rtl_simulation':'PASS', 'simulator':'installed Verilator 5.006 + existing MSVC',
            'modelsim_on_path':vsim,'modelsim_simulation_verified':False,
            'gui_project_open_verified':False,
            'sources':{f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in SRC.glob('*.v')},
            'vcd':str(SRC/'counter.vcd')}
    (OUT/'minimal_simulation.json').write_text(json.dumps(data,indent=2))
    print(text)

if __name__ == '__main__':
    main()
