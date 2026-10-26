"""Scoped recursive TX evidence, including replay/generator/tool dependencies."""
from pathlib import Path
import hashlib,json,os,re,subprocess,sys
ROOT=Path(__file__).resolve().parents[2]
def digest(path):
 return {'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'bytes':path.stat().st_size}
def _verilog_code(path):
 text=path.read_text(errors='replace')
 return re.sub(r'//[^\n]*|/\*.*?\*/','',text,flags=re.S)

def resolve_rtl(roots):
 """Follow instantiated project modules/includes, not a fixed dependency list.

 Current project uses literal Verilog-2001 module names. Missing/unresolved
 modules remain compiler errors; ambiguous module definitions fail closed here.
 """
 index={}
 for file in (ROOT/'rtl').rglob('*.v'):
  for module in re.findall(r'\bmodule\s+([A-Za-z_]\w*)',_verilog_code(file)):
   if module in index and index[module]!=file:raise RuntimeError('Ambiguous RTL module '+module)
   index[module]=file
 selected=set();graph={}
 def visit(file):
  file=file.resolve()
  if file in selected:return
  if not file.is_file():raise RuntimeError('Missing RTL dependency '+str(file))
  selected.add(file);code=_verilog_code(file);dependencies=set()
  for include in re.findall(r'`include\s+"([^"\n]+)"',code):
   candidate=file.parent/include
   if not candidate.is_file():candidate=ROOT/include
   if not candidate.is_file():raise RuntimeError('Missing Verilog include '+include)
   dependencies.add(candidate.resolve())
  for module,target in index.items():
   if target.resolve()==file:continue
   if re.search(r'\b'+re.escape(module)+r'\s*(?:#\s*\(|[A-Za-z_]\w*\s*\()',code):dependencies.add(target.resolve())
  graph[file.relative_to(ROOT).as_posix()]=sorted(p.relative_to(ROOT).as_posix() for p in dependencies)
  for dependency in sorted(dependencies):visit(dependency)
 for file in roots:visit(Path(file))
 return sorted(selected),graph

def capture(report,names,parameters,env=None,phase='behavior'):
 files={ROOT/name for name in names}
 rtl,graph=resolve_rtl([p for p in files if p.suffix in ['.v','.sv']]);files.update(rtl)
 files.update(ROOT/name for name in ['tb/shared_tx.cpp','scripts/rtl_test_build.py','scripts/generate_vectors.py','scripts/m0_native/run.py','afdx_shared_tx.epr','constraints/core.sdc'])
 files.update((ROOT/'scripts/shared_tx').glob('*.py'))
 files.update(p for p in (ROOT/'third_party/verilator-5.006').rglob('*') if p.is_file())
 vector=ROOT/'vectors'/report.name/'cases.txt'
 if vector.exists():files.add(vector)
 for path in [report/'verilated_config.h',report/'obj/build.rsp']:
  if path.exists():files.add(path)
 payload={'rtl_dependency_graph':graph,'resolved_rtl':[p.relative_to(ROOT).as_posix() for p in rtl],'record_kind':'posthoc_tooling_supplement' if phase=='tooling_supplement' else 'executed_native_run' if phase.startswith('native') else 'runtime_capture','context_only_not_execution_inputs':['afdx_shared_tx.epr','constraints/core.sdc'],'stage':'T4','phase':phase,'parameters':parameters,'files':{p.relative_to(ROOT).as_posix():digest(p) for p in sorted(files)},'python':{'executable':sys.executable,'version':sys.version},'scope':'Exact files at this capture; external installed tool dependencies remain installed, not vendored.'}
 install=Path(r'C:\Program Files (x86)\eHiWay\eLinx3.0')
 external=[Path(sys.executable),install/'verilator/verilator.exe',install/'bin/Passkey/bin/quartus_map.exe',install/'bin/Passkey/bin/quartus_cdb.exe',install/'bin/shell/bin/Implementation.exe',install/'bin/shell/bin/run_pack.tcl']
 if env:
  compiler=next((Path(directory)/'cl.exe' for directory in env['PATH'].split(';') if (Path(directory)/'cl.exe').is_file()),None)
  if compiler:
   external.append(compiler)
   result=subprocess.run([str(compiler),'/Bv'],env=env,cwd=report,capture_output=True,timeout=20)
   payload['msvc_version_output']=(result.stdout+result.stderr).decode('utf-8',errors='replace')
  payload['library_paths']={k:env.get(k,'') for k in ['VCTOOLSINSTALLDIR','WINDOWSSDKDIR','INCLUDE','LIB','VERILATOR_ROOT']}
 payload['external_files']={str(p):digest(p) for p in external if p.is_file()}
 version=subprocess.run([str(install/'verilator/verilator.exe'),'--version'],capture_output=True,timeout=20)
 payload['verilator_version']=(version.stdout+version.stderr).decode('utf-8',errors='replace').strip()
 (report/('manifest_'+phase+'.json')).write_text(json.dumps(payload,indent=2)+'\n')
 return payload
