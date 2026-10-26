"""Run verified native eLinx synthesis CLI; no GUI automation or guessed Tcl."""
from pathlib import Path
import subprocess,json,time,sys
ROOT=Path(__file__).resolve().parents[1]
EXE=Path(r'C:\Program Files (x86)\eHiWay\eLinx3.0\bin\shell\bin\eLinx_synthesis.exe')
steps={
 'wire_frontend':'read_verilog rtl/afdx_wire_core.v rtl/afdx_baseline_core.v rtl/afdx_udp_tx.v rtl/afdx_udp_rx.v rtl/eth_crc32_byte.v rtl/eth_mac_tx.v rtl/eth_mac_rx.v; hierarchy -check -top afdx_wire_core; proc; opt; check -assert; stat',
 'frontend':'read_verilog rtl/afdx_baseline_core.v rtl/afdx_udp_tx.v rtl/afdx_udp_rx.v; hierarchy -check -top afdx_baseline_core; proc; opt; check -assert; stat',
 'scheduled_tx_frontend':'read_verilog rtl/afdx_scheduled_tx.v rtl/afdx_bag_scheduler.v rtl/afdx_udp_tx.v; hierarchy -check -top afdx_scheduled_tx; proc; opt; check -assert; stat'
}
if '--mapping' in sys.argv:steps['probe_mapping']='read_verilog rtl/afdx_env_probe.v; synth_stratix -top afdx_env_probe -family eHiChip6 -vqm reports/probe.vqm; check -assert; stat'
results={}
for name,script in steps.items():
 (ROOT/'scripts'/('elinx_'+name+'.ys')).write_text(script+'\n')
 start=time.time();r=subprocess.run([str(EXE),'-Q','-s','scripts/elinx_'+name+'.ys'],cwd=ROOT,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=180)
 (ROOT/'reports'/('elinx_'+name+'.log')).write_text(r.stdout+r.stderr,encoding='utf-8')
 results[name]={'exit_code':r.returncode,'seconds':round(time.time()-start,2)}
 print(name,results[name]);print((r.stdout+r.stderr)[-1800:])
(ROOT/'reports/elinx_checks.json').write_text(json.dumps(results,indent=2)+'\n')
if any(x['exit_code'] for x in results.values()):raise SystemExit(1)
