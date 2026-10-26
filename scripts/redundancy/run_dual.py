from pathlib import Path
import argparse,hashlib,json,os,sys,struct,zlib,subprocess
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts'))
from rtl_test_build import build,execute

def checksum(b):
    if len(b)%2:b+=b'\0'
    s=sum(int.from_bytes(b[i:i+2],'big') for i in range(0,len(b),2))
    while s>65535:s=(s&65535)+(s>>16)
    return (~s)&65535

def wire(vl,sn,payload,kind=0):
    src=bytes([192,168,1,1]);dst=bytes([192,168,1,2]);u=struct.pack('!HHHH',1000,1001,len(payload)+8,0)
    c=checksum(src+dst+bytes([0,17])+struct.pack('!H',len(u)+len(payload))+u+payload) or 65535
    u=u[:6]+struct.pack('!H',c)+payload
    ip=struct.pack('!BBHHHBBH4s4s',69,0,len(u)+20,0,16384,64,17,0,src,dst)
    ip=ip[:10]+struct.pack('!H',checksum(ip))+ip[12:]
    body=bytes.fromhex('03000000')+(vl+1).to_bytes(2,'big')+bytes.fromhex('0200000000010800')+ip+u+bytes(max(0,17-len(payload)))+bytes([sn])
    if kind==2:body=body[:24]+bytes([body[24]^1])+body[25:] # bad IP, correct FCS
    if kind==3:body=body[:42]+bytes([body[42]^1])+body[43:] # bad UDP, correct FCS
    fcs=zlib.crc32(body)
    if kind==1:fcs^=1
    return (b'\x55'*7+b'\xd5'+body+fcs.to_bytes(4,'little')).hex()

def main(report=None):
    report=Path(report).resolve() if report is not None else ROOT/'reports/rx_dual'
    vec=report/'vectors';vec.mkdir(parents=True,exist_ok=True)
    report.mkdir(parents=True,exist_ok=True)
    (report/'result.json').write_text('{"status":"RUNNING"}\n')
    rows=[]
    on=os.environ.get('RM_EXPECTED_SN','0') not in ('','0')
    model=None
    if on:
        from reference import Reference,anchors_on
        anchors_on()  # literal ON anchors must hold before the model drives expectations
        model=Reference(expected_sn=True)
    # batches are fully drained; paired wires start on the same clock.
    def add(vl,sn,n,mode=0,kind=0,expected=None):
        p=bytes((vl*31+sn+x*17)%256 for x in range(n))
        a=wire(vl,sn,p,kind) if mode!=2 else '-'
        b=wire(vl,sn,p) if mode in (0,2) else '-'
        if not rows and mode==0:b=wire(vl,sn,bytes(x^255 for x in p))
        if on:
            # Expected delivery count for the expected-SN profile: feed only the
            # copies that reach the RM (a bad FCS/IP/UDP A copy is dropped earlier).
            exp=0;stamp=(len(rows)+1)*1000
            if vl<8 and mode in (0,1) and kind==0:exp+=model.decide(0,vl,sn,1,1,stamp)[0]
            if vl<8 and mode in (0,2):exp+=model.decide(1,vl,sn,1,1,stamp+1)[0]
            expected=1 if exp else 0
        elif expected is None:
            expected=1
        rows.append(f'{vl} {sn} {p.hex()} {a} {b} {expected}')
    for vl in range(8):
        for sn,n in [(0,1),(1,17),(2,1471),(3,64)]:add(vl,sn,n)
    # corrupt A cannot take delivery from B; complete FCS and protocol errors.
    for vl in range(8):
        add(vl,4,33,kind=1);add(vl,5,18,kind=2);add(vl,6,19,kind=3)
        add(vl,0,22,mode=1);add(vl,8,23,mode=2) # single A or B
        add(vl,40,24,mode=1,expected=0);add(vl,41,25,mode=1) # rejected observation advances IC
        add(vl,0,26);add(vl,254,27,mode=1,expected=0);add(vl,255,28,mode=1);add(vl,1,29,mode=1)
    add(9,0,17,mode=1,expected=0) # unknown wire VL must not reach IC/RM
    add(7,2,29,mode=1)
    cases=vec/'cases.txt';cases.write_text('\n'.join(rows)+'\n')
    names=['rtl/rx/afdx_rx_dual.v','rtl/rx/afdx_rx_merge.v','rtl/rx/afdx_integrity_checker.v','rtl/eth_mac_rx.v','rtl/eth_crc32_byte.v','rtl/afdx_udp_rx.v']
    files=[ROOT/p for p in names];tb=ROOT/'tb/rx_dual.cpp'
    exe,env=build('afdx_rx_dual',files,tb,report,{'RM_EXPECTED_SN':int(os.environ.get('RM_EXPECTED_SN','0'))})
    execute([exe,cases,report/'result.json'],report/'simulation.log',env)
    bad=rows.copy();parts=bad[0].split();parts[2]='ff' if parts[2]!='ff' else '00';bad[0]=' '.join(parts)
    negative=vec/'corrupt_expected.txt';negative.write_text('\n'.join(bad)+'\n')
    with (report/'negative_control.log').open('wb') as f:
        r=subprocess.run([str(exe),str(negative),str(report/'negative_unexpected.json')],env=env,stdout=f,stderr=subprocess.STDOUT)
    assert r.returncode!=0 and 'payload differs' in (report/'negative_control.log').read_text(), 'negative control did not detect corrupted payload expectation'
    (report/'inputs.json').write_text(json.dumps({'sha256':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files+[tb,Path(__file__),cases]},'profile':'development-v1','rm_expected_sn':int(on),'timing_verified':False,'negative_control_exit':r.returncode},indent=2)+'\n')
    print((report/'result.json').read_text())
if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--report',type=Path,default=ROOT/'reports/rx_dual');args=parser.parse_args()
    try:main(args.report)
    except Exception:
        args.report.mkdir(parents=True,exist_ok=True)
        (args.report/'result.json').write_text('{"status":"FAIL"}\n')
        raise
