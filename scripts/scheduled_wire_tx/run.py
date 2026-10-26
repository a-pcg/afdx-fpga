"""Verify BAG at physical SFD, including large-to-small frame transitions."""
from pathlib import Path
import hashlib
import json
import sys
import zlib
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts'))
from rtl_test_build import build,execute


def main():
    vectors=ROOT/'vectors/scheduled_wire_tx';vectors.mkdir(parents=True,exist_ok=True)
    lines=[]
    for line in (ROOT/'vectors/scheduled_frames.txt').read_text().splitlines():
        vl,payload,body=line.split();body=bytes.fromhex(body)
        physical=b'\x55'*7+b'\xd5'+body+zlib.crc32(body).to_bytes(4,'little')
        lines.append(vl+' '+payload+' '+physical.hex())
    text='\n'.join(lines)+'\n';(vectors/'cases.txt').write_text(text)
    names=['rtl/afdx_scheduled_wire_tx.v','rtl/afdx_bag_scheduler.v','rtl/afdx_udp_tx.v','rtl/eth_mac_tx.v','rtl/eth_crc32_byte.v']
    files=[ROOT/x for x in names];tb=ROOT/'tb/scheduled_wire_tx.cpp';report=ROOT/'reports/scheduled_wire_tx'
    # This bundled Verilator clamps oversized decimal -G arguments to INT_MAX.
    # Signed -65536 preserves the desired 32-bit pattern 0xffff0000.
    exe,env=build('afdx_scheduled_wire_tx',files,tb,report,{'INITIAL_TIME_LOW':-65536})
    manifest={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in names}
    manifest['test_parameters']={'INITIAL_TIME_LOW':-65536,'effective_low32_hex':'ffff0000','INITIAL_TIME_HIGH':0}
    manifest['tb/scheduled_wire_tx.cpp']=hashlib.sha256(tb.read_bytes()).hexdigest();manifest['golden_vectors']=hashlib.sha256(text.encode()).hexdigest()
    (report/'inputs.json').write_text(json.dumps(manifest,indent=2)+'\n')
    execute([exe],report/'simulation.log',env)
    result=json.loads((report/'result.json').read_text());assert result['status']=='PASS';print(json.dumps(result))


if __name__=='__main__':main()
