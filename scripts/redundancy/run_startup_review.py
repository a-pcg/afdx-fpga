"""Replay startup defect or its fix in an independent report directory."""
from pathlib import Path
import hashlib,json,os,sys,argparse
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts'))
from rtl_test_build import build,execute
from run_dual import wire

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--baseline',action='store_true');ap.add_argument('--negative-control',action='store_true');args=ap.parse_args()
    assert not(args.baseline and args.negative_control)
    out=ROOT/'reports/rx_startup_review'/('baseline' if args.baseline else 'pre_fix_negative' if args.negative_control else 'fixed');out.mkdir(parents=True,exist_ok=True)
    p0=bytes(range(17));p1=bytes(range(17,34));vectors=out/'cases.txt'
    vectors.write_text(' '.join([p0.hex(),wire(0,42,p0),p1.hex(),wire(0,43,p1)])+'\n')
    names=['tb/rx_r2_probe.v','rtl/rx/afdx_rx_dual.v','rtl/rx/afdx_rx_merge.v','rtl/rx/afdx_integrity_checker.v','rtl/eth_mac_rx.v','rtl/eth_crc32_byte.v','rtl/afdx_udp_rx.v']
    if args.negative_control:
        # Replay the same final test against the preserved, unfixed T8 source.
        # Never restore an old business module into the working tree.
        names[1]='reports/independent_t8_t9_review/accepted_t8_snapshot/rtl/rx/afdx_rx_dual.v'
    tb=ROOT/('tb/rx_r2_characterize.cpp' if args.baseline else 'tb/rx_startup.cpp')
    paths=[ROOT/n for n in names]+[tb,Path(__file__),ROOT/'scripts/rtl_test_build.py',ROOT/'scripts/redundancy/run_dual.py',vectors]
    hashes={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    (out/'inputs.json').write_text(json.dumps({'files':hashes,'scope':'baseline limitation characterization' if args.baseline else 'independent-lane startup behavior'},indent=2)+'\n')
    (out/'result.json').write_text('{"status":"RUNNING"}\n')
    exe,env=build('rx_r2_probe',[ROOT/n for n in names],tb,out,{'RM_EXPECTED_SN':int(os.environ.get('RM_EXPECTED_SN') or 0)})
    if args.negative_control:
        try:execute([exe,vectors,out/'result.json',out/'trace.jsonl'],out/'simulation.log',env)
        except RuntimeError:
            assert 'healthy lane service blocked by stuck peer' in (out/'simulation.log').read_text()
            (out/'result.json').write_text(json.dumps({'status':'EXPECTED_TEST_FAILURE','reason':'healthy lane service blocked by stuck peer','scope':'new startup test detects the unfixed baseline'})+'\n')
        else:raise AssertionError('unfixed baseline unexpectedly passed')
    else:execute([exe,vectors,out/'result.json',out/'trace.jsonl'],out/'simulation.log',env)
    assert hashes=={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    print((out/'result.json').read_text(),flush=True)

if __name__=='__main__':main()
