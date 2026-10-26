"""Read and audit E2 artifacts; never launches tools or edits HDL."""
from pathlib import Path
import json,hashlib,re,collections,xml.etree.ElementTree as ET
ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'reports/elinx_integration'
INSTALL=Path(r'C:/Program Files (x86)/eHiWay/eLinx3.0')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
 report=json.loads((OUT/'rx_e2_latest.json').read_text());base=ROOT/report['case_root']
 name='native_rx_dual_e2';project=base/name;run=project/(name+'.runs/synth_1')
 # Recheck frozen/current HDL against both the handoff and behavior manifest.
 src={}
 for rel,h in report['input_hashes'].items():
  a=ROOT/rel;b=project/'src'/a.name
  src[rel]={'expected':h,'snapshot_sha256':sha(b),'current_sha256':sha(a)}
  if sha(b)!=h:raise RuntimeError('Frozen source mismatch')
 text=(run/(name+'.vqm')).read_text()
 blocks={}
 for inst,key,value in re.findall(r'defparam\s+(\S+)\s+\.(port_a_\w+)\s*=\s*(\d+)\s*;',text):
  blocks.setdefault(inst,{})[key]=int(value)
 blocks={k:b for k,b in blocks.items() if 'port_a_data_width' in b}
 groups=collections.defaultdict(list)
 for k,b in blocks.items():
  if k.startswith('\\eth_mac_rx:'):
   g='mac_frame' if b.get('port_a_logical_ram_depth')==4096 else 'mac_timestamp'
  elif k.startswith('\\afdx_udp_rx:'):g='udp_payload'
  elif 'afdx_integrity_checker:' in k:g='ic_state'
  else:g='rm_state'
  groups[g].append((k,b))
 expected={'mac_frame':2*4096*8,'mac_timestamp':2*2*64,'udp_payload':16*1471*8,'ic_state':16*9,'rm_state':8*(64+8)}
 audit={}
 for g,bs in groups.items():
  bits=sum((b['port_a_last_address']-b['port_a_first_address']+1)*b['port_a_data_width'] for _,b in bs)
  audit[g]={'bits':bits,'expected_bits':expected[g],'primitives':len(bs),
            'logical_shapes':[list(s)+[n] for s,n in collections.Counter((b.get('port_a_logical_ram_depth'),b.get('port_a_logical_ram_width'),b.get('port_a_address_width'),b.get('port_a_data_width')) for _,b in bs).items()]}
  if bits!=expected[g]:raise RuntimeError('Capacity mismatch '+g)
 cluster=ET.parse(run/(name+'_cluster.xml')).getroot()
 ramclusters=[c for c in cluster.findall('CLUSTER') if c.get('type')=='wb_m4kblk_hc']
 native_memory=int(re.search(r'Total Memorys\s*:\s*(\d+)',report['native_utilization']).group(1))
 capacity={'status':'GEOMETRY_AND_CAPACITY_PLAUSIBILITY_PASS','groups':audit,
           'total_vqm_bits':sum(v['bits'] for v in audit.values()),'vqm_ram_primitives':len(blocks),
           'native_memory_blocks':native_memory,'native_cluster_count':len(ramclusters),
           'vqm_sha256':sha(run/(name+'.vqm')),'cluster_sha256':sha(run/(name+'_cluster.xml')),
           'scope':'Matches source geometry and native capacity plausibility only; not mapped equivalence or timing'}
 if len(ramclusters)!=native_memory or native_memory*4096<capacity['total_vqm_bits']:
  raise RuntimeError('Native storage implausible')
 routes=[]
 for f in sorted(base.glob('route_*/route_results.json')):
  d=json.loads(f.read_text())
  for rec in d['records']:
   p=ROOT/rec['directory'];imp=p/(name+'.runs/imple_1');edi=imp/(name+'.edi')
   rec['native_diagnostics']=[s for s in edi.read_text(errors='replace').splitlines() if re.search(r'\[ERROR\]|\[CRIT\]|unconstrain|no.clock',s,re.I)] if edi.is_file() else []
   rec.pop('files',None);routes.append(rec)
 packlog=(run/(name+'.edi')).read_text(errors='replace')
 clocks={'vqm_register_clock_nets':sorted(set(re.findall(r'\.clk\(([^)]+)\)',text))),
         'vqm_ram_clock_nets':sorted(set(re.findall(r'\.clk\d\(([^)]+)\)',text))),
         'pack_diagnostics':[s for s in packlog.splitlines() if re.search(r'unconstrain|no.clock|\[CRIT\]|\[ERROR\]',s,re.I)],
         'input_delay_budget_ns':None,'output_delay_budget_ns':None,
         'unconstrained_endpoints_audit':'NOT_COMPLETED: preplace IO DRC prevents native timing analysis',
         'no_clock_endpoint_audit':'NOT_COMPLETED: VQM observed sequential clock nets are clk, but no complete native endpoint report'}
 # Record full dependency closure by defined module references in the six compiled units.
 texts={p.name:re.sub(r'//[^\n]*|/\*.*?\*/','',p.read_text(),flags=re.S) for p in (project/'src').glob('*.v')}
 mods={re.search(r'\bmodule\s+(\w+)',t).group(1):n for n,t in texts.items()}
 graph={m:[x for x in mods if x!=m and re.search(r'\b'+re.escape(x)+r'\s+(?:#\s*\(|\w+\s*\()',texts[n])] for m,n in mods.items()}
 result={'stage':'E2_PACK_PASS_ROUTE_DRC_FAIL','case_root':report['case_root'],'target':report['native_target'],
         'source_checks':src,'module_dependency_graph':graph,'pack_verified':True,'capacity':capacity,
         'classic_summary':report['classic_summary'],'native_utilization':report['native_utilization'],
         'route_attempts':routes,'route_coverage':'NOT_REACHED: 390 IO > 339 free package locations',
         'clocks_and_io':clocks,'internal_10ns_timing_verified':False,'negative_timing_control_verified':False,
         'system_timing_verified':False,'board_pins_verified':False,
         'next_decision':'Coordinator must choose an authorized internal-analysis/IO wrapper or actual system IO reduction; no HDL or DRC bypass applied'}
 (OUT/'rx_e2_audit.json').write_text(json.dumps(result,indent=2))
 (base/'audit.json').write_text(json.dumps(result,indent=2))
 # Full artifact manifest, plus installed input/tool hashes; separate from old accepted pack manifest.
 tools=[INSTALL/'bin/Passkey/bin/quartus_map.exe',INSTALL/'bin/Passkey/bin/quartus_cdb.exe',INSTALL/'bin/shell/bin/Implementation.exe',INSTALL/'bin/shell/bin/run_pack.tcl',INSTALL/'bin/shell/bin/run_route.tcl']
 tools+=list((INSTALL/'bin/shell/Infrastructure/psk_extern/sdc2.0.1').glob('*.tcl'))
 tools += [p for p in (INSTALL/'bin/shell/Infrastructure/psk_intern/eHiChip6/EQ6HL130').rglob('*') if p.is_file() and p.suffix in ['.dat','.xml','.tcl']]
 tools += [INSTALL/'bin/shell/Infrastructure/psk_intern/eHiChip6/series_library'/n for n in ['place_route_guide.xml','arc_guide.tcl']]
 manifest={'case':report['case_root'],'local':{str(p.relative_to(ROOT)):{'sha256':sha(p),'bytes':p.stat().st_size} for p in base.rglob('*') if p.is_file() and p.name!='e2_manifest.json'},
           'installed_inputs':{str(p):{'sha256':sha(p),'bytes':p.stat().st_size} for p in tools if p.is_file()},
           'scripts':{str(p.relative_to(ROOT)):sha(p) for p in (ROOT/'scripts/elinx_integration').glob('*rx_e2.py')}}
 (base/'e2_manifest.json').write_text(json.dumps(manifest,indent=2))
 (OUT/'rx_e2_manifest.json').write_text(json.dumps(manifest,indent=2))
 print(json.dumps({'capacity_bits':capacity['total_vqm_bits'],'memory_blocks':native_memory,'route_attempts':len(routes),'status':result['stage'],'local_manifest_files':len(manifest['local'])}))
if __name__=='__main__':main()
