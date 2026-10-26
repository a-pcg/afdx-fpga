"""Independent physical byte frames and actual Verilog receive-MAC regression."""
from pathlib import Path
import hashlib
import importlib.util
import json
import os
import random
import subprocess
import zlib

ROOT=Path(__file__).resolve().parents[2]
REPORT=ROOT/'reports/mac_rx'
OBJ=REPORT/'obj'


def frame(body):
    return b'\x55'*7+b'\xd5'+body+zlib.crc32(body).to_bytes(4,'little')


def main():
    REPORT.mkdir(parents=True,exist_ok=True);OBJ.mkdir(exist_ok=True)
    vectors=ROOT/'vectors/mac_rx';vectors.mkdir(parents=True,exist_ok=True)
    rng=random.Random(20261018)
    cases=[]
    for label,n in [('minimum',60),('maximum',1514)]+[(f'random{i}',rng.randrange(60,1515)) for i in range(200)]:
        body=bytes(rng.randrange(256) for _ in range(n));cases.append((label,1,-1,frame(body),body))
    for i in range(3):
        body=bytes([0xa0+i])*60;cases.append((f'hold{i}',1,-1,frame(body),body))
    body=bytes(range(60));valid=frame(body)
    bad=bytearray(valid);bad[-1]^=1;cases.append(('bad_fcs',0,-1,bytes(bad),b''))
    bad=bytearray(valid);bad[25]^=0x80;cases.append(('bad_body_crc',0,-1,bytes(bad),b''))
    cases.append(('short',0,-1,frame(body[:59]),b''))
    cases.append(('giant',0,-1,frame(bytes(1515)),b''))
    bad=bytearray(valid);bad[3]=0x66;cases.append(('bad_preamble',0,-1,bytes(bad),b''))
    bad=bytearray(valid);bad[7]=0x55;cases.append(('bad_sfd',0,-1,bytes(bad),b''))
    cases.append(('short_preamble',0,-1,valid[4:],b''))
    cases.append(('line_error',0,35,valid,b''))
    cases.append(('truncated',0,-1,valid[:30],b''))
    text='\n'.join(f'{name} {good} {err} {wire.hex()} {body.hex() or "-"}' for name,good,err,wire,body in cases)+'\n'
    (vectors/'cases.txt').write_text(text,encoding='ascii')
    files=['rtl/mac/afdx_eth_rx_mac.v','rtl/mac/afdx_eth_crc32.v','tb/mac_rx.cpp']
    meta={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in files}
    meta['golden_vectors']=hashlib.sha256(text.encode('ascii')).hexdigest()
    (REPORT/'inputs.json').write_text(json.dumps(meta,indent=2)+'\n')
    spec=importlib.util.spec_from_file_location('crc_build_helper',ROOT/'scripts/mac_crc/run.py')
    h=importlib.util.module_from_spec(spec);spec.loader.exec_module(h);h.REPORT=REPORT
    inc=h.INC
    header=(inc/'verilated_config.h.in').read_text().replace('@PACKAGE_NAME@','Verilator').replace('@PACKAGE_VERSION@','5.006').replace('@VERILATOR_VERSION_INTEGER@','5006000')
    (REPORT/'verilated_config.h').write_text(header)
    env={k.upper():v for k,v in os.environ.items()};env['VERILATOR_ROOT']=str(inc.parent).replace('\\','/')
    h.run([h.VER,'--cc','--compiler','msvc','--default-language','1364-2001','--top-module','afdx_eth_rx_mac','--Mdir',OBJ.relative_to(ROOT).as_posix(),*files[:2]],'verilator.log',env)
    setup=subprocess.run('call "'+str(h.VC)+'" >nul && set',shell=True,capture_output=True,text=True,encoding='utf-8',errors='replace',check=True)
    for line in setup.stdout.splitlines():
        if '=' in line:
            k,v=line.split('=',1)
            if k:env[k.upper()]=v
    compiler=next(Path(p)/'cl.exe' for p in env['PATH'].split(';') if (Path(p)/'cl.exe').exists())
    sources=list(OBJ.glob('*.cpp'))+[inc/'verilated.cpp',inc/'verilated_threads.cpp',ROOT/files[2]]
    args=['/nologo','/std:c++17','/EHsc','/MD','/O2','/DVL_TIME_CONTEXT','/I'+str(inc),'/I'+str(inc/'vltstd'),'/I'+str(REPORT),'/I'+str(OBJ),'/Fe:'+str(OBJ/'mac_rx.exe')]+[str(x) for x in sources]
    (OBJ/'build.rsp').write_text('\n'.join('"'+x+'"' for x in args),encoding='utf-8')
    h.run([compiler,'@build.rsp'],'msvc.log',env,OBJ);h.run([OBJ/'mac_rx.exe'],'simulation.log',env)
    result=json.loads((REPORT/'result.json').read_text());assert result['status']=='PASS'
    print(json.dumps(result))


if __name__=='__main__':main()
