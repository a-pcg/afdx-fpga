"""Allocator ownership/conservation and actual RTL payload RAM checks."""
from pathlib import Path
import hashlib
import json
import sys
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts'))
from rtl_test_build import build,execute


def main():
    rtl=ROOT/'rtl/buffer/afdx_packet_pool.v';tb=ROOT/'tb/packet_pool.cpp'
    for width in [3,6]:
        n=1<<width;report=ROOT/'reports'/('packet_pool_'+str(n))
        exe,env=build('afdx_packet_pool',[rtl],tb,report,{'SLOT_WIDTH':width})
        (report/'inputs.json').write_text(json.dumps({'rtl_sha256':hashlib.sha256(rtl.read_bytes()).hexdigest(),'testbench_sha256':hashlib.sha256(tb.read_bytes()).hexdigest(),'slot_width':width,'offset_width':11,'tag_width':32,'seed':20261018},indent=2)+'\n')
        execute([exe,n,report/'result.json'],report/'simulation.log',env)
        result=json.loads((report/'result.json').read_text());assert result['status']=='PASS';print(json.dumps(result),flush=True)


if __name__=='__main__':main()
