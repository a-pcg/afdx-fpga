"""New directed characterization only; frozen stage1 RTL and reports untouched."""
from pathlib import Path
import hashlib,json,sys
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts'))
from rtl_test_build import build,execute
from run_dual import wire
from reference import Reference

def main():
    report=ROOT/'reports/rx_r2';vec=ROOT/'vectors/rx_r2';report.mkdir(parents=True,exist_ok=True);vec.mkdir(parents=True,exist_ok=True)
    frozen=json.loads((ROOT/'reports/rx_stage1/summary.json').read_text())
    rtl=[ROOT/x for x in ['rtl/rx/afdx_rx_dual.v','rtl/rx/afdx_rx_merge.v','rtl/rx/afdx_integrity_checker.v','rtl/eth_mac_rx.v','rtl/eth_crc32_byte.v','rtl/afdx_udp_rx.v']]
    for p in rtl:assert hashlib.sha256(p.read_bytes()).hexdigest()==frozen['sha256'][str(p.relative_to(ROOT))],p
    payload=[bytes(range(17)),bytes(range(17,34))]
    cases=vec/'cases.txt';cases.write_text(' '.join([payload[0].hex(),wire(0,42,payload[0]),payload[1].hex(),wire(0,43,payload[1])])+'\n')
    # Independent model predicts the known weakness, not normative acceptance.
    model=Reference(100000);expected=[model.decide(*args) for args in [(0,0,42,1,1,1000),(0,0,43,1,1,2000),(1,0,42,1,1,3000)]]
    assert expected==[(1,0)]*3
    (vec/'expected_limitations.json').write_text(json.dumps({'late_copy':{'inputs':[[0,42],[0,43],[1,42]],'observed_development_decisions':expected,'at_most_once_satisfied':False},'startup':{'stuck_network':'B','global_initialized':False,'healthy_A_delivery_available':False},'classification':'KNOWN_LIMITATION_NOT_COMPLIANCE'},indent=2)+'\n')
    sources=[ROOT/'tb/rx_r2_probe.v']+rtl;tb=ROOT/'tb/rx_r2_characterize.cpp'
    exe,env=build('rx_r2_probe',sources,tb,report)
    execute([exe,cases,report/'result.json',report/'layer_trace.jsonl'],report/'simulation.log',env)
    result=json.loads((report/'result.json').read_text());assert result['status']=='CHARACTERIZED' and not result['late_copy']['at_most_once_satisfied']
    paths=sources+[tb,Path(__file__),ROOT/'scripts/rtl_test_build.py',ROOT/'scripts/redundancy/run_dual.py',ROOT/'scripts/redundancy/reference.py',cases,vec/'expected_limitations.json']
    (report/'inputs.json').write_text(json.dumps({'record_origin':'R2 execution completion','rtl_frozen_against':'reports/rx_stage1/summary.json','sha256':{p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in paths},'top':'rx_r2_probe','parameters':{'SKEW_MAX':100000,'TIME_WIDTH':64,'VL_COUNT':8},'test_only_hierarchical_observation':True},indent=2)+'\n')
    print(json.dumps(result))
if __name__=='__main__':main()
