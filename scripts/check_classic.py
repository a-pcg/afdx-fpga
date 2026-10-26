"""Reproduce the installed Classic intermediate mapping, not EQ6 placement/routing."""
from pathlib import Path
import subprocess,json,time
ROOT=Path(__file__).resolve().parents[1]
EXE=Path(r'C:\Program Files (x86)\eHiWay\eLinx3.0\bin\Passkey\bin\quartus_map.exe')
results={}
for project in ['probe','baseline','wire']:
 t=time.time()
 with (ROOT/'reports'/('classic_'+project+'.log')).open('w',encoding='utf-8') as log:
  r=subprocess.run([str(EXE),project,'--write_settings_files=off'],cwd=ROOT/'elinx',stdout=log,stderr=subprocess.STDOUT,timeout=300)
 results[project]={'exit_code':r.returncode,'seconds':round(time.time()-t,2),'target':'Classic intermediate Stratix mapping; not final EQ6HL130 results'}
 print(project,results[project])
(ROOT/'reports/classic_checks.json').write_text(json.dumps(results,indent=2)+'\n')
if any(x['exit_code'] for x in results.values()):raise SystemExit(1)
