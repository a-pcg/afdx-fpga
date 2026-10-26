"""E2 frozen eight-VL receiver: separate synthesis/pack/route evidence."""
from pathlib import Path
import importlib.util, hashlib, json, datetime, re, sys, shutil
ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'reports/elinx_integration'
EXPECTED={
 'rtl/rx/afdx_rx_dual.v':'ec71defb922c302eafb8b129dba91c679943a28d1c83623786cfa194eb2e9d6b',
 'rtl/rx/afdx_rx_merge.v':'746bfdb4942ef03aa58b8ba059de47cd7aa774b2fdf45efbc9cc20a83d056ca2',
 'rtl/rx/afdx_integrity_checker.v':'fbdc0f592951ea187337a570dc41f43b227216b6d6d7429af173d6092176c765'}
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
 stamp=datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f')
 base=ROOT/'elinx/integration/rx_e2'/stamp
 base.mkdir(parents=True)
 report={'case_root':str(base.relative_to(ROOT)), 'stage':'INPUT_VERIFICATION',
         'top':'afdx_rx_dual','parameters':{'networks':2,'rx_vls_per_network':8,'SKEW_MAX':100000,'time_width':64},
         'native_target':'eHiChip6/EQ6HL130/CSG484_H','pack_verified':False,
         'internal_timing_verified':False,'system_timing_verified':False,
         'io_timing_budget_provided':False,'physical_pins_verified':False}
 def save():
  (base/'e2_results.json').write_text(json.dumps(report,indent=2))
  (OUT/'rx_e2_latest.json').write_text(json.dumps(report,indent=2))
 try:
  prior=json.loads((ROOT/'reports/rx_dual/inputs.json').read_text())['sha256']
  files=[ROOT/k for k in prior if k.replace('\\','/').startswith('rtl/')]
  frozen={str(p.relative_to(ROOT)).replace('\\','/'):sha(p) for p in files}
  for k,v in EXPECTED.items():
   if frozen[k]!=v:raise RuntimeError('Handoff source changed: '+k)
  for k,v in prior.items():
   if k.replace('\\','/') in frozen and frozen[k.replace('\\','/')]!=v:
    raise RuntimeError('RX behavioral manifest mismatch: '+k)
  # Preserve behavioral provenance alongside, without running/writing its tests.
  refs=['docs/coordination/rx_handoff.md','reports/rx_dual/inputs.json','reports/rx_stage1/summary.json',
        'docs/rx/profile.md','scripts/elinx_integration/run_rx_e2.py','scripts/m0_native/run.py']
  report['input_hashes']=frozen
  report['provenance_hashes']={k:sha(ROOT/k) for k in refs}
  (base/'freeze.json').write_text(json.dumps(report,indent=2))
  spec=importlib.util.spec_from_file_location('native_readonly',ROOT/'scripts/m0_native/run.py')
  n=importlib.util.module_from_spec(spec);spec.loader.exec_module(n);n.BASE=base
  name='native_rx_dual_e2';top='afdx_rx_dual'
  project,run,snapshot=n.prepare(name,top,files)
  report['source_snapshot']=snapshot
  report['stage']='CLASSIC_SYNTHESIS';save()
  steps=[]
  steps.append(n.execute([n.CLASSIC/'quartus_map.exe','--read_settings_files=on','--write_settings_files=off',name],project,project/'classic.log',timeout=720))
  summary=(run/(name+'.map.summary')).read_text(errors='replace')
  if not re.search(r'Status\s*:\s*Successful',summary):raise RuntimeError('Classic not successful')
  report['classic_summary']=summary; report['stage']='VQM_EXPORT';save()
  vqm=run/(name+'.vqm')
  steps.append(n.execute([n.CLASSIC/'quartus_cdb.exe',name,'--vqm='+vqm.as_posix()],project,project/'vqm_export.log',timeout=120))
  if not vqm.is_file() or not vqm.stat().st_size:raise RuntimeError('No VQM')
  report['vqm_sha256']=sha(vqm);report['stage']='NATIVE_PACK';save()
  steps.append(n.execute([n.SHELL/'bin/Implementation.exe','-f',project/'run_native_pack.tcl','-silence'],n.SHELL/'bin',project/'console.log',timeout=360))
  native=(run/(name+'_utilization_synth.rpt')).read_text(errors='replace')
  log=(project/'console.log').read_text(errors='replace')
  cp=run/(name+'_eHiChip6.ecp')
  if 'Synthesis Status : Successful' not in native or 'pack_gene is 1' not in log or not cp.is_file():
   raise RuntimeError('Pack success evidence missing')
  report['pack_verified']=True;report['native_utilization']=native
  report['pack_checkpoint_sha256']=sha(cp); report['steps']=steps
  report['stage']='PACK_PASS';save()
  print('PACK_PASS: '+str(base),flush=True)
 except Exception as e:
  report['failure']=str(e);save();raise
 finally:
  report['artifacts']={str(p.relative_to(base)):{'sha256':sha(p),'bytes':p.stat().st_size}
    for p in base.rglob('*') if p.is_file() and p.name not in ['e2_results.json','freeze.json']}
  save()
if __name__=='__main__':main()
