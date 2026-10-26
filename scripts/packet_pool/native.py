"""Native pool check with a mandatory RAM geometry/capacity audit."""
from pathlib import Path
import argparse
import hashlib
import importlib.util
import json
import re
ROOT=Path(__file__).resolve().parents[2]
NAME='native_packet_pool_banked'
REPORT=ROOT/'reports/packet_pool_64'

def audit(result):
    vqm=ROOT/'elinx/m0_probe'/NAME/(NAME+'.runs/synth_1')/(NAME+'.vqm')
    text=vqm.read_text(errors='replace')
    blocks={}
    for instance,key,value in re.findall(r'defparam\s+(\S+)\s+\.(port_a_\w+)\s*=\s*(\d+)\s*;',text):
        blocks.setdefault(instance,{})[key]=int(value)
    blocks={k:v for k,v in blocks.items() if 'port_a_data_width' in v}
    bits=sum((b['port_a_last_address']-b['port_a_first_address']+1)*b['port_a_data_width'] for b in blocks.values())
    maximum=max((b['port_a_last_address']-b['port_a_first_address']+1 for b in blocks.values()),default=0)
    address_width=max((b['port_a_address_width'] for b in blocks.values()),default=0)
    resources=result['native_resources']
    memories=next((v for k,v in resources.items() if k.lower()=='total memorys'),None)
    expected=64*2048*8
    checks={'vqm_payload_bits':bits,'expected_payload_bits':expected,'vqm_ram_primitives':len(blocks),
            'largest_primitive_depth':maximum,'largest_address_width':address_width,'native_memory_blocks':memories,
            'minimum_4096_bit_blocks':expected//4096,'vqm_sha256':hashlib.sha256(vqm.read_bytes()).hexdigest(),
            'scope':'geometry and capacity plausibility only; no post-map equivalence or timing proof'}
    passed=bits==expected and maximum<=2048 and address_width<=11 and memories is not None and memories>=expected//4096
    checks['status']='PASS' if passed else 'FAIL'
    (REPORT/'memory_capacity_audit.json').write_text(json.dumps(checks,indent=2)+'\n')
    if not passed:raise RuntimeError('Pool native RAM capacity/geometry audit failed: '+str(checks))
    return checks

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--audit-only',action='store_true');args=parser.parse_args()
    path=REPORT/'native_synthesis.json'
    if args.audit_only:result=json.loads(path.read_text())
    else:
        spec=importlib.util.spec_from_file_location('native_check',ROOT/'scripts/m0_native/run.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        result=module.check_case(NAME,'afdx_packet_pool',[ROOT/'rtl/buffer/afdx_packet_pool.v'])
        path.write_text(json.dumps(result,indent=2)+'\n')
    expected_hash=hashlib.sha256((ROOT/'rtl/buffer/afdx_packet_pool.v').read_bytes()).hexdigest()
    if result['source_snapshot']['afdx_packet_pool.v']['sha256']!=expected_hash:
        raise RuntimeError('Native report does not match current pool RTL')
    print(json.dumps(audit(result)),flush=True)

if __name__=='__main__':main()
