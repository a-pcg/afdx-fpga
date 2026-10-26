"""T8-C pool-only native pack self-check: RAM geometry plus LUT/FF deltas.

Replays the eLinx Classic frontend and native EQ6HL130 pack for the current
afdx_packet_pool.v, then audits the map.rpt RAM Summary and the exported VQM so
the 64 x 2048 x 8 payload bank geometry, capacity and the single-write RAM
inference cannot silently change. Writes reports/packet_pool_ram_check_t8/
resource_check.json. Timing is not verified here.
"""
from pathlib import Path
import hashlib
import importlib.util
import json
import re
import sys
ROOT=Path(__file__).resolve().parents[2]
NAME='t8_pool_ram_check'
REPORT=ROOT/'reports/packet_pool_ram_check_t8'
BASELINE=ROOT/'reports/packet_pool_ram_check/resource_check.json'
BASELINE_T8=ROOT/'reports/packet_pool_ram_check_t8/resource_check.json'
EXPECTED_BITS=64*2048*8


def ram_summary(map_report):
    text=map_report.read_text(errors='replace')
    start=text.find('Analysis & Synthesis RAM Summary')
    if start<0:raise RuntimeError('RAM Summary section missing')
    rows=[]
    for line in text[start:].splitlines()[:4000]:
        cells=[c.strip() for c in line.split(';')]
        # ; name ; type ; mode ; depthA ; widthA ; depthB ; widthB ; size ; mif ;
        if len(cells)>=10 and cells[2] in ('AUTO','M9K','M10K'):
            rows.append({'name':cells[1],'type':cells[2],'mode':cells[3],'port_a_depth':cells[4],
                         'port_a_width':cells[5],'port_b_depth':cells[6],'port_b_width':cells[7],
                         'size':cells[8],'mif':cells[9]})
    return rows


def vqm_audit(vqm):
    text=vqm.read_text(errors='replace')
    blocks={}
    for instance,key,value in re.findall(r'defparam\s+(\S+)\s+\.(port_a_\w+)\s*=\s*(\d+)\s*;',text):
        blocks.setdefault(instance,{})[key]=int(value)
    blocks={k:v for k,v in blocks.items() if 'port_a_data_width' in v}
    bits=sum((b['port_a_last_address']-b['port_a_first_address']+1)*b['port_a_data_width'] for b in blocks.values())
    return {'vqm_payload_primitives':len(blocks),'vqm_payload_bits':bits,
            'largest_primitive_depth':max((b['port_a_last_address']-b['port_a_first_address']+1 for b in blocks.values()),default=0),
            'largest_address_width':max((b['port_a_address_width'] for b in blocks.values()),default=0),
            'vqm_sha256':hashlib.sha256(vqm.read_bytes()).hexdigest()}


def main():
    global NAME,REPORT
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument('--audit-only',action='store_true',
                        help='re-audit the existing native case artifacts without re-synthesizing')
    parser.add_argument('--case',default=NAME,help='native case name (default '+NAME+')')
    parser.add_argument('--report',default=str(REPORT.relative_to(ROOT)),help='report directory (repo-relative)')
    args=parser.parse_args()
    NAME=args.case
    REPORT=ROOT/args.report
    pool=ROOT/'rtl/buffer/afdx_packet_pool.v'
    if args.audit_only:
        existing=json.loads((REPORT/'resource_check.json').read_text())
        result={'native_resources':existing['native_resources'],
                'native_target':existing['native_target'],
                'native_diagnostics':existing.get('native_diagnostics',[]),
                'steps':existing.get('steps',[]),
                'checkpoint':existing['checkpoint'],
                'checkpoint_sha256':existing['checkpoint_sha256']}
    else:
        spec=importlib.util.spec_from_file_location('native_check',ROOT/'scripts/m0_native/run.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        result=module.check_case(NAME,'afdx_packet_pool',[pool])
    run=ROOT/'elinx/m0_probe'/NAME/(NAME+'.runs/synth_1')
    rows=ram_summary(run/(NAME+'.map.rpt'))
    simple=[r for r in rows if r['mode']=='Simple Dual Port']
    payload=[r for r in simple if r['port_a_depth']=='2048' and r['port_a_width']=='8'
             and r['port_b_depth']=='2048' and r['port_b_width']=='8' and r['size']=='16384']
    vqm=vqm_audit(run/(NAME+'.vqm'))
    resources=result['native_resources']
    memories=next((v for k,v in resources.items() if k.lower()=='total memorys'),None)
    checks={
        'ram_summary_total_rows':len(rows),
        'ram_simple_dual_port_rows':len(simple),
        'ram_payload_rows_2048x8':len(payload),
        'ram_payload_bits':len(payload)*16384,
        'expected_payload_bits':EXPECTED_BITS,
        'ram_payload_depth':sorted({r['port_a_depth'] for r in payload}),
        'ram_payload_width':sorted({r['port_a_width'] for r in payload}),
        'native_memory_blocks':memories,
        'minimum_4096_bit_blocks':EXPECTED_BITS//4096,
        'source_sha256':hashlib.sha256(pool.read_bytes()).hexdigest(),
    }
    checks.update(vqm)
    # The VQM splits each 2048x8 payload bank into 8 one-bit 2048-deep
    # primitives, so the expected primitive count is slots*8 = 512. The
    # invariant that matters is the total payload bit count and the per-bank
    # depth/width/packing, not the primitive granularity.
    expected_vqm_primitives=64*8
    checks['expected_vqm_payload_primitives']=expected_vqm_primitives
    passed=(len(payload)==64 and checks['ram_payload_bits']==EXPECTED_BITS and
            checks['ram_payload_depth']==['2048'] and checks['ram_payload_width']==['8'] and
            len(simple)==len(rows) and vqm['vqm_payload_primitives']==expected_vqm_primitives and
            vqm['vqm_payload_bits']==EXPECTED_BITS and vqm['largest_primitive_depth']<=2048 and
            vqm['largest_address_width']<=11 and memories is not None and
            memories>=EXPECTED_BITS//4096)
    baseline={}
    if BASELINE.exists() and BASELINE!=(REPORT/'resource_check.json'):
        old=json.loads(BASELINE.read_text())
        old_res=old.get('native_resources',{})
        baseline={'t7_source_sha256':old.get('source_sha256',{}).get('afdx_packet_pool.v'),
                  't7_native_resources':old_res,
                  't7_ram_simple_dual_port_rows':old.get('ram_simple_dual_port_count'),
                  'resource_delta':{k:(resources.get(k,0)-old_res.get(k,0)) for k in sorted(set(resources)|set(old_res))}}
    if BASELINE_T8.exists() and BASELINE_T8!=(REPORT/'resource_check.json'):
        old=json.loads(BASELINE_T8.read_text())
        old_res=old.get('native_resources',{})
        baseline['t8']={'source_sha256':old.get('source_sha256',{}).get('afdx_packet_pool.v'),
                        'native_resources':old_res,
                        'ram_simple_dual_port_rows':old.get('ram_geometry',{}).get('ram_simple_dual_port_rows'),
                        'resource_delta':{k:(resources.get(k,0)-old_res.get(k,0)) for k in sorted(set(resources)|set(old_res))}}
    REPORT.mkdir(parents=True,exist_ok=True)
    payload_report={
        'status':'SYNTHESIS_AND_PACK_PASS' if passed else 'FAIL',
        'top':'afdx_packet_pool','native_case':NAME,
        'native_target':result['native_target'],
        'native_resources':resources,
        'native_diagnostics':result['native_diagnostics'],
        'ram_geometry':checks,
        'ram_summary_line_sample':['; '.join(['',r['name'],r['type'],r['mode'],r['port_a_depth'],r['port_a_width'],r['port_b_depth'],r['port_b_width'],r['size'],r['mif'],'']) for r in payload[:4]],
        'baseline':baseline,
        'steps':result['steps'],
        'checkpoint':result['checkpoint'],
        'checkpoint_sha256':result['checkpoint_sha256'],
        'timing_verified':False,
        'scope':'pool-only native pack RAM geometry/capacity and LUT/FF delta self-check; no timing or behavior claim',
    }
    (REPORT/'resource_check.json').write_text(json.dumps(payload_report,indent=2)+'\n')
    if not passed:raise RuntimeError('Pool native RAM geometry/capacity audit failed: '+json.dumps(checks))
    print(json.dumps({'status':payload_report['status'],'native_resources':resources,
                      'ram_simple_dual_port_rows':len(simple),'ram_payload_bits':checks['ram_payload_bits'],
                      'vqm_payload_primitives':vqm['vqm_payload_primitives'],
                      'baseline_delta':baseline.get('resource_delta')}),flush=True)


if __name__=='__main__':main()
