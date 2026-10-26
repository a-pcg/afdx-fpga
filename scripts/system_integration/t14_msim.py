"""T14 joint ModelSim cases (NUM_MACS=1 default elaboration): compat100 and cand50."""
from pathlib import Path
import hashlib, json, os, re, subprocess
ROOT=Path(r'C:\Users\zhang\Desktop\test\afdx')
OUT=ROOT/'reports/t14_run'
MAP=[('afdx_bag_scheduler64.v','rtl/afdx_bag_scheduler64.v'),
     ('afdx_buffered_vl.v','rtl/buffer/afdx_buffered_vl.v'),
     ('afdx_integrity_checker.v','rtl/rx/afdx_integrity_checker.v'),
     ('afdx_packet_pool.v','rtl/buffer/afdx_packet_pool.v'),
     ('afdx_rx_dual.v','rtl/rx/afdx_rx_dual.v'),
     ('afdx_rx_merge.v','rtl/rx/afdx_rx_merge.v'),
     ('afdx_shared_tx.v','rtl/afdx_shared_tx.v'),
     ('afdx_system_top.v','rtl/system/afdx_system_top.v'),
     ('afdx_udp_rx.v','rtl/afdx_udp_rx.v'),
     ('afdx_udp_tx_shared.v','rtl/afdx_udp_tx_shared.v'),
     ('afdx_vl_queue.v','rtl/buffer/afdx_vl_queue.v'),
     ('eth_crc32_byte.v','rtl/eth_crc32_byte.v'),
     ('eth_mac_rx.v','rtl/eth_mac_rx.v'),
     ('eth_mac_tx.v','rtl/eth_mac_tx.v')]
MSIM=r'C:\intelFPGA\18.1\modelsim_ase\win32aloem'

def make_case(tag, params):
    case=OUT/tag/'modelsim'
    case.mkdir(parents=True,exist_ok=True)
    files=[]
    for name,rel in MAP:
        data=(ROOT/rel).read_bytes(); (case/name).write_bytes(data)
        files.append({'path':name,'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data),'source':rel})
    tb=(ROOT/'tb/tb_afdx_system.v').read_text(encoding='utf-8',errors='replace')
    if params:
        patched=re.sub(r'afdx_system_top\s+#\s*\([^)]*\)\s*(\w+)\s*\(',
                       'afdx_system_top #(.CORE_CLK_HZ(%d),.BYTE_CYCLES(%d)) \\1 ('%(params[0],params[1]), tb, count=1)
        if patched==tb:
            patched=tb.replace('afdx_system_top dut(',
                'afdx_system_top #(.CORE_CLK_HZ(%d),.BYTE_CYCLES(%d)) dut('%(params[0],params[1]),1)
        if patched==tb: raise RuntimeError('could not parameterize the DUT instantiation')
        patched=patched.replace('always #5 clk=~clk;','always #10 clk=~clk;',1)
        tb=patched
    (case/'tb_afdx_system.v').write_text(tb,encoding='utf-8')
    files.append({'path':'tb_afdx_system.v','sha256':hashlib.sha256(tb.encode()).hexdigest(),'bytes':len(tb.encode()),
                  'source':'tb/tb_afdx_system.v'+(' (DUT %d/%d, 20 ns clock)'%params if params else ' (default)')})
    compile_list=' '.join([m[0] for m in MAP]+['tb_afdx_system.v'])
    (case/'modelsim.do').write_text(
        'onerror {quit -force -code 1}\n'
        'onbreak {quit -force -code 1}\n'
        'vlib work\n'
        'vlog -work work '+compile_list+'\n'
        'vsim -c -voptargs=+acc work.tb_afdx_system\n'
        'log -r /*\n'
        'run -all\n'
        'quit -force -code 0\n', encoding='utf-8')
    manifest={'case':str(case.relative_to(ROOT)).replace('\\','/'),'stage':'RUNNING','top':'tb_afdx_system',
              'dut':'afdx_system_top','num_macs':1,
              'elaboration_parameters':({'core_clk_hz':params[0],'byte_cycles':params[1]} if params else {'core_clk_hz':100000000,'byte_cycles':8}),
              'simulator':'ModelSim ALTERA 10.5b Starter','files':files}
    (case/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
    env=dict(os.environ); env['PATH']=MSIM+';'+env.get('PATH','')
    log=OUT/tag/'modelsim.log'
    proc=subprocess.run([MSIM+r'\vsim.exe','-c','-do','modelsim.do'],cwd=case,env=env,
                        stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
    raw=proc.stdout or b''
    text=raw.decode('utf-8',errors='replace')
    log.write_bytes(text.encode('utf-8'))
    ok='PASS tb_afdx_system delivered=2' in text and 'Errors: 0, Warnings: 0' in text
    manifest.update({'stage':'SIMULATED_PASS' if ok else 'FAILED','exit_code':proc.returncode,
                     'result':('PASS tb_afdx_system delivered=2' if ok else 'check log'),
                     'vlog_errors_warnings':'0 errors, 0 warnings' if ok else 'see log',
                     'vsim_errors_warnings':'0 errors, 0 warnings' if ok else 'see log',
                     'transcript':str(log.relative_to(ROOT)).replace('\\','/'),
                     'transcript_sha256':hashlib.sha256(raw).hexdigest()})
    (case/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
    print(tag,'exit',proc.returncode,'PASS' if ok else 'FAIL')
    for line in text.splitlines():
        if any(k in line for k in ['COMPLIANCE','DELIVERED','PASS tb','Errors:']):
            print('  |',line.strip()[:150])
    return ok

def main():
    ok1=make_case('compat100',None); ok2=make_case('cand50',(50000000,4))
    if not (ok1 and ok2): raise SystemExit('ModelSim case failed')
if __name__=='__main__':main()
