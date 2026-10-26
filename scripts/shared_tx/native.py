"""T4-only native pack with recursive RTL and separate, immutable older evidence.

Default and legacy --only-shared both use native_shared_tx_t4 and
reports/shared_tx_t4_native. They never overwrite the T3 case/reports.
"""
from pathlib import Path
import argparse,datetime,hashlib,importlib.util,json,re
from evidence import capture,resolve_rtl
ROOT=Path(__file__).resolve().parents[2]
CASE='native_shared_tx_t4'
REPORT=ROOT/'reports/shared_tx_t4_native'
FROZEN=ROOT/'reports/shared_tx_pre_t4_native'

def write(name,value):
 (REPORT/name).write_text(json.dumps(value,indent=2)+'\n')
def sha(file):return hashlib.sha256(file.read_bytes()).hexdigest()
def audit_capacity(result):
 vqm=ROOT/'elinx/m0_probe'/CASE/(CASE+'.runs/synth_1')/(CASE+'.vqm')
 blocks={}
 for instance,key,value in re.findall(r'defparam\s+(\S+)\s+\.(port_a_\w+)\s*=\s*(\d+)\s*;',vqm.read_text(errors='replace')):
  blocks.setdefault(instance,{})[key]=int(value)
 payload=[b for instance,b in blocks.items() if 'payload_bank' in instance and b.get('port_a_logical_ram_depth')==2048 and b.get('port_a_logical_ram_width')==8]
 bits=sum((b['port_a_last_address']-b['port_a_first_address']+1)*b['port_a_data_width'] for b in payload)
 memories=result['native_resources']['Total Memorys']
 passed=bits==1048576 and len(payload)==512 and memories>=256 and all(b['port_a_address_width']<=11 for b in payload)
 audit={'status':'PASS' if passed else 'FAIL','payload_pool_bits':bits,'payload_pool_primitives':len(payload),'native_memory_blocks_total':memories,'vqm_sha256':sha(vqm),'scope':'capacity/geometry plausibility, not mapped equivalence or timing'}
 write('memory_capacity_audit.json',audit)
 if not passed:raise RuntimeError('T4 RAM capacity/geometry audit failed')
 return audit

def main():
 parser=argparse.ArgumentParser();parser.add_argument('--only-shared',action='store_true',help='Compatibility flag; T4 runner always builds only shared TX.');parser.parse_args()
 REPORT.mkdir(parents=True,exist_ok=True)
 frozen=json.loads((FROZEN/'manifest.json').read_text())
 for name,item in frozen['files'].items():
  if sha(FROZEN/'snapshot'/name)!=item['sha256']:raise RuntimeError('Frozen T4 evidence changed: '+name)
 protected=json.loads((FROZEN/'preserve_t3_native.json').read_text())
 for name,digest in protected.items():
  if sha(ROOT/name)!=digest:raise RuntimeError('Protected T3 native evidence changed: '+name)
 sources,graph=resolve_rtl([ROOT/'rtl/afdx_shared_tx.v'])
 names=[p.relative_to(ROOT).as_posix() for p in sources]
 if 'rtl/afdx_bag_scheduler64.v' not in names or 'rtl/afdx_bag_scheduler.v' in names:raise RuntimeError('T4 scheduler dependency selection is incorrect')
 if len({p.name for p in sources})!=len(sources):raise RuntimeError('Native flat snapshot would collide on basenames')
 # Link to the actual accepted behavior inputs before any new tool captures.
 behavior=json.loads((FROZEN/'snapshot/reports/shared_tx_128/inputs.json').read_text())
 for name in names:
  if behavior['sources'].get(name)!=sha(ROOT/name):raise RuntimeError('RTL differs from accepted T4 behavior: '+name)
 parameters={'VL_WIDTH':7,'SLOT_WIDTH':6,'MAX_PER_VL':4,'INITIAL_TIME_LOW':0,'INITIAL_TIME_HIGH':0}
 write('status.json',{'stage':'T4','status':'RUNNING','case':CASE,'started_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'timing_verified':False})
 try:
  # These are explicitly after-the-fact helper records, not reconstructed runtime manifests.
  supplement=capture(REPORT,names,parameters,phase='tooling_supplement')
  supplement['executed_in_accepted_behavior_runs']=False
  supplement['frozen_runtime_manifest']=str((FROZEN/'manifest.json').relative_to(ROOT))
  supplement['note']='Native/evidence helper changes after accepted T4 behavior. Original runtime manifests/scripts remain frozen and are not rewritten.'
  write('manifest_tooling_supplement.json',supplement)
  pre=capture(REPORT,names,parameters,phase='native_inputs');pre['invocation']=['python','scripts/shared_tx/native.py','--only-shared'];pre['case']=CASE;write('manifest_native_inputs.json',pre)
  spec=importlib.util.spec_from_file_location('native_check',ROOT/'scripts/m0_native/run.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
  result=module.check_case(CASE,'afdx_shared_tx',sources)
  write('native_synthesis.json',result)
  for file in sources:
   expected=pre['files'][file.relative_to(ROOT).as_posix()]['sha256']
   if result['source_snapshot'][file.name]['sha256']!=expected or sha(file)!=expected:raise RuntimeError('Native/current RTL snapshot mismatch: '+str(file))
  if sha(ROOT/result['checkpoint'])!=result['checkpoint_sha256']:raise RuntimeError('Native ECP hash mismatch')
  audit=audit_capacity(result)
  post=capture(REPORT,names,parameters,phase='native');post['invocation']=pre['invocation'];post['case']=CASE;post['native_generated_configs']={}
  base=ROOT/'elinx/m0_probe'/CASE
  for file in [base/(CASE+'.qsf'),base/(CASE+'.epr'),base/'run_native_pack.tcl',base/'sources.json',base/(CASE+'.runs/synth_1')/(CASE+'.run.psf'),base/'src/core.edc']:
   post['native_generated_configs'][file.relative_to(ROOT).as_posix()]={'sha256':sha(file),'bytes':file.stat().st_size}
  post['native_working_directory']=result['steps'][-1]['cwd']
  write('manifest_native.json',post)
  for name,digest in protected.items():
   if sha(ROOT/name)!=digest:raise RuntimeError('T3 evidence overwritten: '+name)
  write('acceptance.json',{'stage':'T4','status':'NATIVE_PACK_AND_CAPACITY_PASS','case':CASE,'behavior_rerun':False,'behavior_source':'reports/shared_tx_pre_t4_native/snapshot','behavior_status':'Previously accepted T4; RTL hashes matched native snapshot','runtime_vs_supplement':'Frozen runtime manifests are original. New helper/native manifests are subsequent execution records, not retrospective behavior execution claims.','rtl_dependencies':names,'rtl_dependency_graph':graph,'native_resources':result['native_resources'],'checkpoint':result['checkpoint'],'checkpoint_sha256':result['checkpoint_sha256'],'capacity_audit':audit,'manifests':{name:sha(REPORT/name) for name in ['manifest_native_inputs.json','manifest_native.json','manifest_tooling_supplement.json']},'protected_t3_native_unchanged':True,'timing_verified':False,'limits':['No behavioral rerun in this task','No mapped equivalence, placement/routing, IO budget, GUI or effective 100 MHz timing proof','Root legacy afdx_shared_tx.epr still lists the public 32-bit scheduler and is context only; the generated independent T4 native EPR is the execution input']})
  write('status.json',{'stage':'T4','status':'NATIVE_PACK_AND_CAPACITY_PASS','case':CASE,'timing_verified':False})
  print(json.dumps(result['native_resources']),flush=True)
 except Exception as error:
  write('status.json',{'stage':'T4','status':'FAIL','case':CASE,'error':str(error),'timing_verified':False});raise
if __name__=='__main__':main()
