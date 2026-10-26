"""Read-only r8 audit. Snapshot inputs; decode existing SQLite/route outputs.

No vendor process, route, optimization, business edit or historical JSON update.
"""
from pathlib import Path
import json,hashlib,datetime,shutil,sqlite3,re,collections
ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'reports/elinx_integration'
ORIGINAL=ROOT/'elinx/integration/rx_e4/20261019_183111_316801_r8_fix'
NAME='native_rx_e4'
INSTALL=Path(r'C:/Program Files (x86)/eHiWay/eLinx3.0')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def entry(p):return {'sha256':sha(p),'bytes':p.stat().st_size}
def rows(c,t):return [dict(r) for r in c.execute('select * from '+t)]
def coverage(path,critical_net):
 text=path.read_text(errors='replace')
 parts=re.split(r'(?m)^\s*net (\d+):([^\n]*)\n',text)
 categories=collections.Counter();types=collections.Counter();special=[]
 for i in range(1,len(parts),3):
  idx,name,body=parts[i:i+3]
  nodes=collections.Counter(re.findall(r'node [^:]+: (\w+)',body));types.update(nodes)
  category='source_channel_sink' if nodes['SOURCE'] and nodes['CHAN'] and nodes['SINK'] else 'empty' if not nodes else 'other'
  categories[category]+=1
  if critical_net and name.strip()==critical_net.strip():
   special.append({'net_id':int(idx),'name':name,'node_types':dict(nodes),'source_header':body.splitlines()[0]})
 return {'net_sections':(len(parts)-1)//3,'categories':dict(categories),'node_type_occurrences':dict(types),
         'critical_net':special,'scope':'Text route trace coverage only; repeated CHAN nodes counted as occurrences, not unique resources or a connectivity DRC'}
def main():
 stamp=datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
 dest=ROOT/'elinx/integration/r8_evidence_audit'/stamp;dest.mkdir(parents=True)
 packpath=ROOT/'reports/rx_r3_runs/r8_fix_pack.json';pack=json.loads(packpath.read_text())
 project=ORIGINAL/NAME;synth=project/(NAME+'.runs/synth_1')
 frozen=dest/'inputs';frozen.mkdir()
 shutil.copytree(project/'src',frozen/'src')
 # Freeze the actual native route input checkpoint and sidecars, never rebuild it.
 shutil.copytree(synth,frozen/'synth_1')
 shutil.copyfile(packpath,frozen/'original_pack_report.json')
 evidence_files=[packpath]
 source_checks={}
 for name,info in pack['source_snapshot'].items():
  p=project/'src'/name;actual=sha(p)
  if actual!=info['sha256']:raise RuntimeError('Pack source hash mismatch: '+name)
  source_checks[name]={'snapshot_sha256':actual,'pack_report_sha256':info['sha256'],
    'current_working_file':info['original'],'current_sha256':sha(Path(info['original'])) if Path(info['original']).is_file() else None}
  evidence_files.append(p)
 runs=[];initial={}
 for directory in sorted(ORIGINAL.glob('route_*')):
  p=directory/'p10';imp=p/(NAME+'.runs/imple_1');sin=p/(NAME+'.runs/synth_1')
  history=directory/'route_results.json';raw=json.loads(history.read_text())
  selected=[history,p/'console.log',p/'clock.sdc',p/'run.tcl',imp/(NAME+'.run.psf'),
   imp/(NAME+'.slack.rpt'),imp/(NAME+'_route_status.rpt'),imp/(NAME+'_utilization_placed.rpt'),
   imp/(NAME+'.tan.pb'),imp/(NAME+'.route'),imp/(NAME+'.edi'),imp/(NAME+'_eHiChip6_EQ6HL130.ecp'),
   imp/(NAME+'.route.dly.pb'),imp/(NAME+'.tbg.pb'),sin/(NAME+'.vqm'),sin/(NAME+'_eHiChip6.ecp')]
  for q in selected:initial[str(q.relative_to(ROOT))]=entry(q)
  evidence_files+=selected
  freeze_run=frozen/directory.name;freeze_run.mkdir()
  for q in selected:
   # Large trace/checkpoint outputs remain original, hashed before and after reading.
   if q.suffix in ['.json','.sdc','.psf','.tcl','.log','.rpt'] or q.name.endswith('.tan.pb'):
    shutil.copyfile(q,freeze_run/q.name)
  if sha(sin/(NAME+'_eHiChip6.ecp'))!=raw['source_checkpoint_sha256'] or sha(sin/(NAME+'.vqm'))!=pack['vqm_sha256']:
   raise RuntimeError('Route input provenance mismatch')
  con=sqlite3.connect((freeze_run/(NAME+'.tan.pb')).resolve().as_uri()+'?mode=ro&immutable=1',uri=True)
  con.row_factory=sqlite3.Row
  summaries={t:rows(con,t) for t in ['elinx_timing_summary','elinx_intra_clock_summary','elinx_inter_clock_summary',
    'elinx_user_ignored_clock_summary','elinx_unconstrain_clock_summary','elinx_timer_setting','elinx_timer_tr_cfg','elinx_not_delay_Io']}
  paths=[dict(r) for r in con.execute('select * from elinx_intra_clock_longest_sync_path_report order by cast(slack as real),path_id')]
  detailed=[]
  for path in paths[:5]:
   args=(path['path_id'],path['clock_scoped_path_reports_x'],path['clock_scoped_path_reports_y'])
   tables={}
   for t in ['elinx_data_path_node_detail','elinx_source_clock_path_node_detail','elinx_dest_clcock_path_node_detail','elinx_net_segment_property']:
    tables[t]=[dict(r) for r in con.execute('select * from '+t+' where path_id=? and clock_scoped_path_reports_x=? and clock_scoped_path_reports_y=? and path_type=0 and sync_type=0',args)]
   data=sorted(tables['elinx_data_path_node_detail'],key=lambda r:r['path_node_detail_id'])
   capture=sorted(tables['elinx_dest_clcock_path_node_detail'],key=lambda r:r['path_node_detail_id'])
   arrival=data[-1]['path_delay'];required=capture[-1]['path_delay']
   detailed.append({'path':path,'nodes_and_segments':tables,'arrival_ps':arrival,'required_ps':required,
    'slack_recomputed_ps':required-arrival,'network_delay_fraction':path['net_delay']/path['total_delay']})
   if abs(required-arrival-path['slack'])>.01:raise RuntimeError('Path slack arithmetic mismatch')
  unconstrained=[dict(r) for r in con.execute('select * from elinx_unconstrain_longest_path_report order by slack limit 20')]
  schemas={r[0]:[dict(x) for x in con.execute('pragma table_info('+r[0]+')')] for r in con.execute("select name from sqlite_master where type='table'")}
  con.close()
  critical=detailed[0]['nodes_and_segments']['elinx_net_segment_property'][0]['net_name']
  trace=coverage(imp/(NAME+'.route'),critical)
  log=(p/'console.log').read_text(errors='replace')
  completed='All was well!' in log and (imp/(NAME+'_eHiChip6_EQ6HL130.ecp')).stat().st_size>0
  ignored=len(summaries['elinx_user_ignored_clock_summary'])
  if summaries['elinx_intra_clock_summary'][0]['period']!=10000 or summaries['elinx_timing_summary'][0]['setup_wns']!=-52:
   raise RuntimeError('Original timing numbers differ from expected r8')
  runs.append({'run':directory.name,'historical_json_stage':raw['stage'],
   'audit_terminal_status':'OBSERVED_FLOW_COMPLETED_SETUP_FAIL_UNCONSTRAINED_IO' if completed else 'INCOMPLETE',
   'completion_marker_observed':completed,'reported_process_returncode':raw.get('returncode'),
   'input_checkpoint_sha256':sha(sin/(NAME+'_eHiChip6.ecp')),'input_vqm_sha256':sha(sin/(NAME+'.vqm')),
   'route_trace_sha256':sha(imp/(NAME+'.route')),'timing_db_sha256':sha(imp/(NAME+'.tan.pb')),
   'output_checkpoint_sha256':sha(imp/(NAME+'_eHiChip6_EQ6HL130.ecp')),
   'sdc':(p/'clock.sdc').read_text(),'psf':(imp/(NAME+'.run.psf')).read_text(),
   'slack_report':(imp/(NAME+'.slack.rpt')).read_text(),'route_status_report':(imp/(NAME+'_route_status.rpt')).read_text(),
   'placed_report':(imp/(NAME+'_utilization_placed.rpt')).read_text(),
   'console_timing_lines':[l for l in log.splitlines() if any(x in l for x in ['ClockFmax','WNS:','routing is completed','Write routing','All was well'])],
   'database_summaries':summaries,'longest_sync_paths_raw':paths,'worst_five_details':detailed,
   'unconstrained_path_examples':unconstrained,'database_schemas':schemas,'trace_coverage':trace,
   'user_ignored_clock_groups':ignored,'units_note':'Period/delay/slack fields above are ps, verified against 10ns SDC and -0.052ns console. Raw clock_skew field may use a different scalar convention; retained without normalization.'})
 script=INSTALL/'bin/shell/bin/run_route.tcl';slines=script.read_text(encoding='utf-8',errors='replace').splitlines()
 relevant={str(i):l for i,l in enumerate(slines,1) if any(s in l for s in ['route_node_statistic','write_route_status_rpt','route_delete_interface','route_write_results'])}
 shutil.copyfile(script,frozen/'installed_run_route.tcl')
 parser=INSTALL/'bin/shell/Infrastructure/psk_extern/sdc2.0.1/sdcparser_call.tcl'
 shutil.copyfile(parser,frozen/'installed_sdcparser_call.tcl')
 # Freeze/hash every original file we inspected; prove no historical mutation.
 for q in evidence_files:initial.setdefault(str(q.relative_to(ROOT)),entry(q))
 after={rel:entry(ROOT/rel) for rel in initial}
 if after!=initial:raise RuntimeError('Original evidence changed during audit')
 result={'stage':'FINAL_EVIDENCE_AUDIT','audited_case':str(ORIGINAL.relative_to(ROOT)),
  'audit_directory':str(dest.relative_to(ROOT)),'tool_processes_started':0,'route_rerun':False,'rtl_modified':False,
  'source_hash_checks':source_checks,'runs':runs,'historical_inputs_preserved':True,'original_artifacts':initial,
  'two_runs_same_route_trace':runs[0]['route_trace_sha256']==runs[1]['route_trace_sha256'],
  'two_runs_same_timing_db':runs[0]['timing_db_sha256']==runs[1]['timing_db_sha256'],
  'counter_report_audit':{'installed_script_lines':relevant,'fact':'route_node_statistic call is commented out; write_route_status_rpt is still called before route_delete_interface',
    'inference':'Counters may be unpopulated in this vendor flow. Exact internal writer behavior is unavailable, so this is a likely explanation, not a proven root cause.',
    'resolution':'Zero routable/fully-routed counters are unusable as coverage. Nonempty SOURCE/CHAN/SINK trace and post-route net delays prove substantial routing occurred; trace presence does not establish a complete connectivity DRC.'},
  'internal_100mhz_pass':False,'io_budget_known':False,'system_timing_pass':False,
  'installed_tool':{'Implementation.exe':entry(INSTALL/'bin/shell/bin/Implementation.exe'),'run_route.tcl':entry(script),'sdcparser_call.tcl':entry(parser)},
  'audit_script':entry(Path(__file__))}
 (dest/'audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
 (OUT/'r8_evidence_audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
 manifest={str(p.relative_to(ROOT)):entry(p) for p in dest.rglob('*') if p.is_file()}
 (dest/'audit_manifest.json').write_text(json.dumps(manifest,indent=2))
 (OUT/'r8_evidence_manifest.json').write_text(json.dumps(manifest,indent=2))
 print(json.dumps({'audit_directory':str(dest),'stage':result['stage'],'runs':len(runs),
  'route_trace_equal':result['two_runs_same_route_trace'],'timing_db_equal':result['two_runs_same_timing_db'],
  'trace_coverage':runs[0]['trace_coverage'],'manifest_files':len(manifest)}),flush=True)
if __name__=='__main__':main()
