"""Observe the existing E5 retry. Read-only process handles; never starts a build.

Writes solely reports/elinx_integration/e5_takeover and its independent summary.
--watch-seconds is bounded local observation, not a scheduler or background task.
"""
from pathlib import Path
import json,hashlib,datetime,time,argparse,ctypes,sqlite3,re,shutil
from ctypes import wintypes as W
ROOT=Path(__file__).resolve().parents[2]
BASE=ROOT/'elinx/integration/sys_e5/20261020_001348_473629_e5_t5t6b_r9c'
TAG='e5_t5t6b_r9c';NAME='native_sys_e5'
ACTIVE=BASE/'route_20261020_085223/p10'
FIRST=BASE/'route_20261020_002130/p10'
OUT=ROOT/'reports/elinx_integration/e5_takeover'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def meta(p,hashit=False):
 if not p.is_file():return {'exists':False}
 st=p.stat();d={'exists':True,'bytes':st.st_size,'mtime_utc':datetime.datetime.fromtimestamp(st.st_mtime,datetime.timezone.utc).isoformat()}
 if hashit:d['sha256']=sha(p)
 return d
def load(p):
 try:return json.loads(p.read_text(encoding='utf-8'))
 except Exception as e:return {'read_error':str(e)}
def now():return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).isoformat()
def dbread(path):
 try:
  c=sqlite3.connect(path.resolve().as_uri()+'?mode=ro&immutable=1',uri=True);c.row_factory=sqlite3.Row
  result={'quick_check':[r[0] for r in c.execute('pragma quick_check')],
          'tables':[r[0] for r in c.execute("select name from sqlite_master where type='table'")]}
  for t in ['elinx_timing_summary','elinx_intra_clock_summary','elinx_unconstrain_clock_summary','elinx_not_delay_Io']:
   if t in result['tables']:result[t]=[dict(r) for r in c.execute('select * from '+t)]
  if 'elinx_intra_clock_longest_sync_path_report' in result['tables']:
   result['worst_20_paths']=[dict(r) for r in c.execute('select * from elinx_intra_clock_longest_sync_path_report order by cast(slack as real) limit 20')]
  c.close();return result
 except Exception as e:return {'read_error':str(e)}
class Process:
 def __init__(self,pid):
  self.pid=pid;self.k=ctypes.WinDLL('kernel32',use_last_error=True)
  self.k.OpenProcess.argtypes=[W.DWORD,W.BOOL,W.DWORD];self.k.OpenProcess.restype=W.HANDLE
  self.k.GetExitCodeProcess.argtypes=[W.HANDLE,ctypes.POINTER(W.DWORD)]
  self.k.WaitForSingleObject.argtypes=[W.HANDLE,W.DWORD];self.k.WaitForSingleObject.restype=W.DWORD
  self.k.GetProcessTimes.argtypes=[W.HANDLE]+[ctypes.POINTER(W.FILETIME)]*4
  self.k.CloseHandle.argtypes=[W.HANDLE]
  self.handle=self.k.OpenProcess(0x1000|0x100000,False,pid)
 def state(self):
  if not self.handle:return {'pid':self.pid,'handle_acquired':False,'exitcode':'UNKNOWN_FROM_HANDLE'}
  code=W.DWORD();ok=self.k.GetExitCodeProcess(self.handle,ctypes.byref(code))
  running=self.k.WaitForSingleObject(self.handle,0)==258
  ts=[W.FILETIME() for _ in range(4)];self.k.GetProcessTimes(self.handle,*[ctypes.byref(t) for t in ts])
  ticks=lambda t:t.dwHighDateTime*2**32+t.dwLowDateTime
  start=datetime.datetime(1601,1,1,tzinfo=datetime.timezone.utc)+datetime.timedelta(microseconds=ticks(ts[0])/10)
  return {'pid':self.pid,'handle_acquired':True,'running':running,'creation_utc':start.isoformat(),
          'cpu_seconds':(ticks(ts[2])+ticks(ts[3]))/1e7,'exitcode_uint32':None if running or not ok else code.value,
          'exitcode_hex':None if running or not ok else hex(code.value)}
 def close(self):
  if self.handle:self.k.CloseHandle(self.handle)
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--watch-seconds',type=int,default=0);args=ap.parse_args()
 OUT.mkdir(parents=True,exist_ok=True)
 stamp=datetime.datetime.now().strftime('%Y%m%d_%H%M%S');audit=OUT/stamp;audit.mkdir()
 pack=load(ROOT/'reports/sys_e5_runs'/f'{TAG}_pack.json');original=load(ACTIVE.parent/'route_results.json')
 if original.get('pid')!=44632:raise RuntimeError('Active case PID changed; reselect explicitly')
 child=Process(44632);parent=Process(25320)
 frozen={}
 src=BASE/NAME/'src';synth=BASE/NAME/(NAME+'.runs/synth_1');sin=ACTIVE/(NAME+'.runs/synth_1')
 for n,info in pack['source_snapshot'].items():
  p=src/n
  if sha(p)!=info['sha256']:raise RuntimeError('Frozen source mismatch '+n)
  frozen[str(p.relative_to(ROOT))]=meta(p,True)
 for n in [NAME+'.vqm',NAME+'_eHiChip6.ecp']:
  if sha(synth/n)!=sha(sin/n):raise RuntimeError('Route input differs from frozen pack '+n)
  frozen[str((sin/n).relative_to(ROOT))]=meta(sin/n,True)
 cp_hash=sha(sin/(NAME+'_eHiChip6.ecp'))
 if cp_hash!=original['source_checkpoint_sha256']:raise RuntimeError('Source checkpoint provenance mismatch')
 for p in [ACTIVE/'run.tcl',ACTIVE/'clock.sdc',ACTIVE/(NAME+'.runs/imple_1')/(NAME+'.run.psf'),ROOT/'scripts/elinx_integration/route_sys_e5.py']:
  frozen[str(p.relative_to(ROOT))]=meta(p,True)
  shutil.copyfile(p,audit/p.name)
 firstjson=FIRST.parent/'route_results.json';first=load(firstjson)
 firstlog=(FIRST/'console.log').read_text(errors='replace')
 firstaudit={'directory':str(FIRST.relative_to(ROOT)),'historical_report':meta(firstjson,True),
    'reported_returncode':first.get('returncode'),'returncode_hex':hex(first['returncode']&0xffffffff) if first.get('returncode') is not None else None,
    'status':first.get('status'),'completion_marker':first.get('completion_marker'),'seconds':first.get('seconds'),
    'tail':firstlog.splitlines()[-18:],'timing_result_accepted':False,
    'files':{p.name:meta(p,True) for p in (FIRST/(NAME+'.runs/imple_1')).glob('*') if p.is_file()}}
 shutil.copyfile(firstjson,audit/'first_attempt_original_report.json')
 toolscript=Path(r'C:/Program Files (x86)/eHiWay/eLinx3.0/bin/shell/bin/run_route.tcl')
 lines=toolscript.read_text(encoding='utf-8',errors='replace').splitlines()
 result={'audit_started_at_shanghai':now(),'stage':'LIVE_TAKEOVER_NONTERMINAL','active_directory':str(ACTIVE.relative_to(ROOT)),
   'toolchain_case_owned_by_external_runner':True,'new_synthesis_or_route_started':False,'process_killed':False,
   'rtl_changed':False,'frozen_input_hashes':frozen,'first_failed_attempt':firstaudit,'observations':[],
   'route_status_script':{'sha256':sha(toolscript),'relevant_lines':{str(i):l for i,l in enumerate(lines,1) if any(k in l for k in ['route_node_statistic','write_route_status_rpt','route_delete_interface'])},
    'scope':'Disabled statistics are a supported explanation for stale zero counters; exact internal writer behavior is not independently proven.'},
   'internal_10ns_pass':False,'system_timing_verified':False,'io_budget_provided':False}
 def save():
  (audit/'takeover_audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
  (OUT.parent/'sys_e5_takeover_latest.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
 deadline=time.monotonic()+max(args.watch_seconds,0)
 try:
  while True:
   imp=ACTIVE/(NAME+'.runs/imple_1');log=(ACTIVE/'console.log').read_text(errors='replace');cs=child.state();ps=parent.state()
   ob={'at_shanghai':now(),'child':cs,'parent':ps,'original_route_report':load(ACTIVE.parent/'route_results.json'),
       'console':meta(ACTIVE/'console.log'),'tail':log.splitlines()[-14:],
       'output_files':{n:meta(imp/n) for n in [NAME+'.route',NAME+'.route.dly.pb',NAME+'.tan.pb',NAME+'.slack.rpt',NAME+'.tbg.pb',NAME+'_eHiChip6_EQ6HL130.ecp',NAME+'_route_status.rpt']}}
   result['observations'].append(ob);save()
   print(json.dumps({'at':ob['at_shanghai'],'running':cs.get('running'),'cpu':cs.get('cpu_seconds'),'console_bytes':ob['console'].get('bytes'),'slack_exists':ob['output_files'][NAME+'.slack.rpt']['exists']}),flush=True)
   if cs.get('running') is False or (not cs.get('handle_acquired') and ob['original_route_report'].get('returncode') is not None):
    rc=cs.get('exitcode_uint32');rc=ob['original_route_report'].get('returncode') if rc is None else rc
    result['observed_exitcode_uint32']=rc;result['observed_exitcode_hex']=hex(rc&0xffffffff) if rc is not None else None
    complete='All was well!' in log and (imp/(NAME+'.slack.rpt')).is_file() and (imp/(NAME+'_eHiChip6_EQ6HL130.ecp')).is_file()
    result['stage']='TERMINATED_COMPLETE_REQUIRES_STA_AUDIT' if rc==0 and complete else 'TERMINATED_CRASH_OR_INCOMPLETE'
    result['terminal_artifacts']={str(p.relative_to(ROOT)):meta(p,True) for p in imp.glob('*') if p.is_file()}
    if complete:result['timing_database']=dbread(imp/(NAME+'.tan.pb'))
    result['third_attempt_started']=False
    save();break
   if time.monotonic()>=deadline:break
   time.sleep(min(30,max(0,deadline-time.monotonic())))
 finally:
  child.close();parent.close()
  result['audit_snapshot_finished_at_shanghai']=now()
  result['frozen_inputs_still_match']=all(sha(ROOT/k)==v['sha256'] for k,v in frozen.items())
  save()
  manifest={str(p.relative_to(ROOT)):meta(p,True) for p in audit.glob('*') if p.is_file()}
  (audit/'manifest.json').write_text(json.dumps(manifest,indent=2))
if __name__=='__main__':main()
