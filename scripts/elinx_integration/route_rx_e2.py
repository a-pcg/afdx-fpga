"""Continue an already packed E2 snapshot. No business source edits."""
from pathlib import Path
import json,hashlib,re,shutil,subprocess,datetime,time,argparse
ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'reports/elinx_integration'
SHELL=Path(r'C:/Program Files (x86)/eHiWay/eLinx3.0/bin/shell')
NAME='native_rx_dual_e2'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--period',type=float)
 args=ap.parse_args()
 report=json.loads((OUT/'rx_e2_latest.json').read_text())
 base=ROOT/report['case_root']; project=base/NAME; synth=project/(NAME+'.runs/synth_1')
 for rel,d in report['artifacts'].items():
  p=base/rel
  if p.is_file() and sha(p)!=d['sha256']:raise RuntimeError('Existing artifact changed: '+rel)
 for n,d in report['source_snapshot'].items():
  if sha(project/'src'/n)!=d['sha256']:raise RuntimeError('Snapshot changed: '+n)
 if not report['pack_verified']:raise RuntimeError('Cannot route an unverified pack')
 dest=base/('route_'+datetime.datetime.now().strftime('%Y%m%d_%H%M%S'))
 dest.mkdir()
 results={'stage':'RUNNING','original_case':report['case_root'],'target':report['native_target'],
          'input_hashes':report['input_hashes'],'source_checkpoint_sha256':sha(synth/(NAME+'_eHiChip6.ecp')),
          'io_budget_provided':False,'system_timing_verified':False,'records':[],
          'scripts':{str(p.relative_to(ROOT)):sha(p) for p in [Path(__file__),ROOT/'scripts/elinx_integration/run_rx_e2.py']}}
 def save():
  (dest/'route_results.json').write_text(json.dumps(results,indent=2))
  (OUT/'rx_e2_route_latest.json').write_text(json.dumps(results,indent=2))
 periods=[(args.period,'p'+str(args.period).replace('.','p'))] if args.period is not None else [(10,'p10'),(.1,'p0p1')]
 for period,label in periods:
  p=dest/label; run=p/(NAME+'.runs'); imp=run/'imple_1'
  shutil.copytree(synth,run/'synth_1');imp.mkdir()
  shutil.copyfile(project/'log4cpp.property',p/'log4cpp.property')
  sdc=p/'clock.sdc';sdc.write_text(f'create_clock -name clk -period {period} [get_ports {{clk}}]\n')
  (imp/(NAME+'.run.psf')).write_text('source {'+(SHELL/'Infrastructure/psk_extern/sdc2.0.1/sdcparser_call.tcl').as_posix()+'}\nread_sdc {'+sdc.as_posix()+'}\n')
  script=p/'run.tcl';script.write_text('source {'+(SHELL/'bin/run_route.tcl').as_posix()+'}\nrun_route {'+p.as_posix()+'} '+NAME+' afdx_rx_dual eHiChip6 EQ6HL130 CSG484_H synth_1 imple_1\nexit\n')
  rec={'period_ns':period,'directory':str(p.relative_to(ROOT)),'status':'RUNNING'}
  results['records'].append(rec);save()
  argv=[str(SHELL/'bin/Implementation.exe'),'-f',str(script),'-silence']
  started=time.monotonic()
  with (p/'console.log').open('wb') as f:
   proc=subprocess.Popen(argv,cwd=SHELL/'bin',stdout=f,stderr=subprocess.STDOUT)
   rec['pid']=proc.pid;rec['argv']=argv;rec['cwd']=str(SHELL/'bin');save()
   try: proc.wait(timeout=600)
   except subprocess.TimeoutExpired:
    proc.kill();proc.wait();rec['timeout']=True
  rec['seconds']=time.monotonic()-started;rec['returncode']=proc.returncode
  log=(p/'console.log').read_text(errors='replace')
  rec['constraint_parse_observed']='Command: create_clock' in log and f'Argument -period = {period}' in log and 'No errors found.' in log
  rec['completion_marker']='All was well!' in log
  route=imp/(NAME+'_route_status.rpt');slack=imp/(NAME+'.slack.rpt')
  cp=imp/(NAME+'_eHiChip6_EQ6HL130.ecp')
  if route.exists():rec['route_status']=route.read_text(errors='replace')
  if slack.exists():rec['slack_report']=slack.read_text(errors='replace')
  rec['wns_lines']=[s for s in log.splitlines() if 'WNS:' in s]
  rec['diagnostics']=[s for s in log.splitlines() if re.search(r'error|unconstrain|no.clock|fail|warning',s,re.I)]
  edi=imp/(NAME+'.edi')
  if edi.is_file():rec['native_diagnostics']=[s for s in edi.read_text(errors='replace').splitlines() if re.search(r'\[ERROR\]|\[CRIT\]|unconstrain|no.clock',s,re.I)]
  rec['checkpoint_sha256']=sha(cp) if cp.is_file() else None
  rec['status']='FLOW_COMPLETED_REQUIRES_AUDIT' if not proc.returncode and rec['completion_marker'] and cp.is_file() else 'FAIL_OR_INCOMPLETE'
  rec['files']={str(f.relative_to(p)):{'sha256':sha(f),'bytes':f.stat().st_size} for f in p.rglob('*') if f.is_file()}
  save();print(json.dumps({k:v for k,v in rec.items() if k not in ['files','argv']}),flush=True)
  if rec['status']=='FAIL_OR_INCOMPLETE':
   results['stage']='ROUTE_FAILED_OR_INCOMPLETE';save();return 1
 results['stage']='ROUTE_RUNS_COMPLETE_AUDIT_PENDING';save()
 return 0
if __name__=='__main__':raise SystemExit(main())
