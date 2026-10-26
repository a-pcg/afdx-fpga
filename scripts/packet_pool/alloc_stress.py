"""T8-C allocation scan stress: full pool, generation uniqueness, round-robin,
reset during allocation, response backpressure and response/write-back order."""
from pathlib import Path
import hashlib
import json
import sys
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts'))
from rtl_test_build import build,execute


def main():
    rtl=ROOT/'rtl/buffer/afdx_packet_pool.v';tb=ROOT/'tb/pool_alloc_stress.cpp'
    # (SLOT_WIDTH,TAG_WIDTH): the second build shrinks the generation field to
    # 5 bits so the reuse rounds actually wrap the generation counter.
    for sw,tw in [(6,32),(3,8)]:
        slots=1<<sw;genw=tw-sw
        report=ROOT/'reports'/('packet_pool_alloc_stress_'+str(slots))
        report.mkdir(parents=True,exist_ok=True)
        (report/'result.json').write_text('{"status":"RUNNING"}\n')
        parameters={'SLOT_WIDTH':sw,'TAG_WIDTH':tw}
        exe,env=build('afdx_packet_pool',[rtl],tb,report,parameters)
        (report/'inputs.json').write_text(json.dumps({
            'parameters':parameters,'rtl_sha256':hashlib.sha256(rtl.read_bytes()).hexdigest(),
            'testbench_sha256':hashlib.sha256(tb.read_bytes()).hexdigest(),'seed':20261020,
            'model':'T8-C three-stage generation RMW: allocation latency +2 clocks (min 3 ticks from command capture, worst slots+2); response only after generation write-back; full-pool status 2 without scan; round-robin unchanged',
            'coverage':['full pool','generation uniqueness/wrap','round-robin sparse reuse',
                        'reset during allocation pipeline','reset during held response',
                        'response backpressure','response/write-back ordering',
                        'randomized allocate/publish/release/stale tags']},indent=2)+'\n')
        execute([exe,slots,sw,genw,report/'result.json'],report/'simulation.log',env)
        result=json.loads((report/'result.json').read_text())
        assert result['status']=='PASS'
        print(json.dumps(result),flush=True)


if __name__=='__main__':main()
