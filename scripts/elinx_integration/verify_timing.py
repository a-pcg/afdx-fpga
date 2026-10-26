"""Fresh isolated route runs and a negative control; tiny probes only."""
from pathlib import Path
import shutil, subprocess, json, hashlib, re, datetime, argparse
ROOT = Path(__file__).resolve().parents[2]
SHELL = Path(r'C:/Program Files (x86)/eHiWay/eLinx3.0/bin/shell')
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--probe', choices=['ram','counter'], default='counter')
    a=ap.parse_args()
    name,top=('native_ram','m0_ram_probe') if a.probe=='ram' else ('native_counter','counter')
    old=ROOT/('elinx/m0_probe' if a.probe=='ram' else 'elinx/integration')/name
    base=ROOT/'elinx/integration'/('timing_'+a.probe)/datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    base.mkdir(parents=True)
    shutil.copytree(old/'src',base/'src')
    original=json.loads((old/'sources.json').read_text())
    sources={p.name:sha(p) for p in (base/'src').glob('*.v')}
    assert all(h==original[n]['sha256'] for n,h in sources.items())
    synth=old/(name+'.runs')/'synth_1'
    inputs={p.name:sha(p) for p in synth.iterdir() if p.is_file() and p.suffix in ['.vqm','.ecp','.pb']}
    records=[]
    for period,label in [(10,'p10'),(20,'p20'),(.1,'p0p1')]:
        p=base/label; run=p/(name+'.runs'); imp=run/'imple_1'
        shutil.copytree(synth,run/'synth_1');imp.mkdir()
        shutil.copyfile(old/'log4cpp.property',p/'log4cpp.property')
        sdc=p/'clock.sdc';sdc.write_text(f'create_clock -name clk -period {period} [get_ports {{clk}}]\n')
        (imp/(name+'.run.psf')).write_text('source {'+(SHELL/'Infrastructure/psk_extern/sdc2.0.1/sdcparser_call.tcl').as_posix()+'}\nread_sdc {'+sdc.as_posix()+'}\n')
        tcl=p/'run.tcl'
        tcl.write_text('source {'+(SHELL/'bin/run_route.tcl').as_posix()+'}\n'+f'run_route {{{p.as_posix()}}} {name} {top} eHiChip6 EQ6HL130 CSG484_H synth_1 imple_1\nexit\n')
        argv=[str(SHELL/'bin/Implementation.exe'),'-f',str(tcl),'-silence']
        with (p/'console.log').open('wb') as f:
            r=subprocess.run(argv,cwd=SHELL/'bin',stdout=f,stderr=subprocess.STDOUT,timeout=120)
        log=(p/'console.log').read_text(errors='replace')
        slack=(imp/(name+'.slack.rpt')).read_text()
        m=re.search(r'Fmax:([\d.]+) sync_slack:([-\d.]+)',slack)
        cp=imp/(name+'_eHiChip6_EQ6HL130.ecp')
        if r.returncode or not m or 'All was well!' not in log or not cp.is_file():
            raise RuntimeError('Incomplete run: '+str(p))
        if 'Command: create_clock' not in log or 'No errors found.' not in log:
            raise RuntimeError('Missing constraint callback evidence')
        fmax,value=map(float,m.groups()); inferred=value+1000/fmax
        if abs(inferred-period)>.003: raise RuntimeError('Effective period mismatch')
        rec={'period_ns':period,'slack_ns':value,'fmax_mhz':fmax,'inferred_period_ns':inferred,
             'directory':str(p.relative_to(ROOT)),'returncode':r.returncode,'argv':argv,
             'checkpoint_sha256':sha(cp),'constraint_sha256':sha(sdc),
             'route_status':(imp/(name+'_route_status.rpt')).read_text(),
             'wns_lines':[s for s in log.splitlines() if 'WNS:' in s]}
        records.append(rec);print(json.dumps(rec),flush=True)
    if abs(records[1]['slack_ns']-records[0]['slack_ns']-10)>.02:
        raise RuntimeError('Differential control failed')
    if not records[2]['slack_ns']<0<records[0]['slack_ns']:
        raise RuntimeError('Negative control failed')
    result={'status':'CONSTRAINT_LOADING_DIFFERENTIAL_PASS','probe':a.probe,
            'target':'eHiChip6/EQ6HL130/CSG484_H','top':top,'source_hashes':sources,
            'input_snapshot_hashes':inputs,'cwd':str(SHELL/'bin'),'records':records,
            'vendor_parser_sha256':sha(SHELL/'Infrastructure/psk_extern/sdc2.0.1/sdcparser_call.tcl'),
            'vendor_route_sha256':sha(SHELL/'bin/run_route.tcl'),
            'afdx_system_timing_verified':False,'io_timing_verified':False,
            'physical_pins_verified':False,'scope':'Tiny probe constraint loading; no IO budget or board validation'}
    (base/'results.json').write_text(json.dumps(result,indent=2))
    (ROOT/('reports/elinx_integration/timing_'+a.probe+'_results.json')).write_text(json.dumps(result,indent=2))
if __name__=='__main__':main()
