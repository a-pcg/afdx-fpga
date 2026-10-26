from pathlib import Path
import hashlib,json,os,random,sys,subprocess
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts'))
from rtl_test_build import build,execute
from reference import Reference,anchors,anchors_on

def main():
    report=ROOT/'reports/rx_merge';report.mkdir(parents=True,exist_ok=True)
    (report/'result.json').write_text('{"status":"RUNNING"}\n')
    vec=ROOT/'vectors/rx_merge';vec.mkdir(parents=True,exist_ok=True)
    rows=[]
    on=os.environ.get('RM_EXPECTED_SN','0') not in ('','0')
    r=Reference(expected_sn=on)
    def add(args,expected=None):
        result=r.decide(*args)
        if expected is not None:assert result==expected
        net,vl,sn,good,en,stamp=args
        rows.append((net,vl,sn,good,en,(0xabc00000+len(rows)*131071)%2**32,stamp,*result))
    for *args,p,q in (anchors_on() if on else anchors()):add(args,(p,q))
    rng=random.Random(20261018)
    for i in range(4000):
        net=rng.randrange(2);vl=rng.randrange(8);sn=rng.randrange(256)
        prior=r.observed.get((net,vl))
        if prior is not None and rng.randrange(2):sn=1 if prior==255 else prior+1
        add((net,vl,sn,int(rng.randrange(11)!=0),int(rng.randrange(8)!=0),(i*23)&0xffffffff))
    # 255->1 and all VLs on only A: operation continues with B absent.
    for vl in range(8):
        for sn in [0,254,255,1,2,0,1]:add((0,vl,sn,1,1,100000+vl*1000+sn))
    text='\n'.join(' '.join(map(str,row)) for row in rows)+'\n'
    cases=vec/'cases.txt';cases.write_text(text)
    (vec/'anchors.json').write_text(json.dumps(anchors(),indent=2)+'\n')
    files=[ROOT/'rtl/rx/afdx_rx_merge.v',ROOT/'rtl/rx/afdx_integrity_checker.v'];tb=ROOT/'tb/rx_merge.cpp'
    exe,env=build('afdx_rx_merge',files,tb,report,{'TIME_WIDTH':32,'RM_EXPECTED_SN':int(os.environ.get('RM_EXPECTED_SN','0'))})
    execute([exe,cases,report/'result.json',0],report/'simulation.log',env)
    with (report/'negative_control.log').open('wb') as f:
        bad=subprocess.run([str(exe),str(cases),str(report/'negative_unexpected.json'),'1'],env=env,stdout=f,stderr=subprocess.STDOUT)
    assert bad.returncode!=0,'corrupted expected acceptance did not fail'
    manifest={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files+[tb,Path(__file__),Path(__file__).with_name('reference.py'),cases,vec/'anchors.json']}
    (report/'inputs.json').write_text(json.dumps({'sha256':manifest,'seed':20261018,'negative_control_exit':bad.returncode,'profile':'development-v1','rm_expected_sn':int(on),'timing_verified':False},indent=2)+'\n')
    print((report/'result.json').read_text())
if __name__=='__main__':
    try:main()
    except Exception:
        (ROOT/'reports/rx_merge/result.json').write_text('{"status":"FAIL"}\n')
        raise
