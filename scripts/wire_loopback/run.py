"""End-to-end execution of actual UDP/IP, TX MAC, CRC and RX MAC RTL."""
from pathlib import Path
import hashlib
import json
import sys
import zlib
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts'))
from rtl_test_build import build,execute


def main():
    vectors=ROOT/'vectors/wire_loopback';vectors.mkdir(parents=True,exist_ok=True)
    records=[]
    for line in (ROOT/'vectors/tx_frames.txt').read_text().splitlines():
        payload,body=line.split();body=bytes.fromhex(body)
        wire=b'\x55'*7+b'\xd5'+body+zlib.crc32(body).to_bytes(4,'little')
        records.append(payload+' '+wire.hex())
    text='\n'.join(records)+'\n';(vectors/'cases.txt').write_text(text)
    names=['rtl/mac/afdx_wire_loopback.v','rtl/mac/afdx_eth_tx_mac.v','rtl/mac/afdx_eth_rx_mac.v',
           'rtl/mac/afdx_eth_crc32.v','rtl/afdx_baseline_core.v','rtl/afdx_udp_tx.v','rtl/afdx_udp_rx.v']
    files=[ROOT/x for x in names];tb=ROOT/'tb/wire_loopback.cpp';report=ROOT/'reports/wire_loopback'
    exe,env=build('afdx_wire_loopback',files,tb,report)
    manifest={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in names}
    manifest['tb/wire_loopback.cpp']=hashlib.sha256(tb.read_bytes()).hexdigest()
    manifest['golden_vectors']=hashlib.sha256(text.encode()).hexdigest()
    (report/'inputs.json').write_text(json.dumps(manifest,indent=2)+'\n')
    execute([exe],report/'simulation.log',env)
    result=json.loads((report/'result.json').read_text());assert result['status']=='PASS'
    print(json.dumps(result))


if __name__=='__main__':main()
