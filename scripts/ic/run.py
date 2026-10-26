"""IC truth tables and two-network state isolation for 8 and 4096 VLs."""
from pathlib import Path
import hashlib
import json
import random
import sys
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts'))
from rtl_test_build import build,execute


def corpus(n):
    history={};rows=[]
    def add(net,vl,sn,good=1,enabled=1,hardcoded=None):
        prev=history.get((net,vl))
        if not good:passed,reason=0,1
        else:
            allowed=prev is None or sn==0 or not enabled
            if prev is not None:
                following=[1,2] if prev==255 else [255,1] if prev==254 else [prev+1,prev+2]
                allowed=allowed or sn in following
            passed,reason=(1,0) if allowed else (0,2)
            history[net,vl]=sn
        if hardcoded is not None:assert passed==hardcoded
        rows.append((net,vl,sn,good,enabled,len(rows)&65535,passed,reason))
    for sn,expect in [(17,1),(18,1),(20,1),(20,0),(21,1),(0,1),(1,1),(9,0),(10,1),(254,0),(255,1),(1,1),(3,1)]:add(0,0,sn,hardcoded=expect)
    add(1,0,99,hardcoded=1);add(1,0,100,hardcoded=1)
    add(0,0,77,good=0,hardcoded=0);add(0,0,4,hardcoded=1)
    add(0,0,200,enabled=0,hardcoded=1);add(0,0,202,hardcoded=1)
    for net in range(2):
        for vl in range(n):add(net,vl,(vl*13+net*79)%256)
    # Every table entry must preserve its own previous value, including high VLs.
    for net in range(2):
        for vl in range(n):
            prev=history[net,vl];add(net,vl,1 if prev==255 else prev+1)
    rng=random.Random(20261018+n)
    for _ in range(2048):
        net=rng.randrange(2);vl=rng.randrange(n);prev=history[net,vl]
        sn=(1 if prev==255 else prev+1) if rng.randrange(3) else rng.randrange(256)
        add(net,vl,sn,good=int(rng.randrange(9)!=0),enabled=int(rng.randrange(10)!=0))
    return '\n'.join(' '.join(map(str,row)) for row in rows)+'\n'


def main():
    for width in [3,12]:
        n=1<<width;report=ROOT/'reports'/('ic_'+str(n));vectors=ROOT/'vectors/ic';vectors.mkdir(parents=True,exist_ok=True)
        text=corpus(n);cases=vectors/(str(n)+'.txt');cases.write_text(text)
        rtl=ROOT/'rtl/rx/afdx_integrity_checker.v';tb=ROOT/'tb/integrity_checker.cpp'
        exe,env=build('afdx_integrity_checker',[rtl],tb,report,{'VL_WIDTH':width})
        (report/'inputs.json').write_text(json.dumps({'rtl_sha256':hashlib.sha256(rtl.read_bytes()).hexdigest(),'testbench_sha256':hashlib.sha256(tb.read_bytes()).hexdigest(),'golden_sha256':hashlib.sha256(text.encode()).hexdigest(),'vl_width':width},indent=2)+'\n')
        execute([exe,cases,n,report/'result.json'],report/'simulation.log',env)
        result=json.loads((report/'result.json').read_text());assert result['status']=='PASS';print(json.dumps(result),flush=True)


if __name__=='__main__':main()
