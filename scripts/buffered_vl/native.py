"""Native checks of 128 VL queues and their connection to the 128 KiB pool."""
from pathlib import Path
import hashlib,importlib.util,json,re
ROOT=Path(__file__).resolve().parents[2]
def main():
 spec=importlib.util.spec_from_file_location('native_check',ROOT/'scripts/m0_native/run.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
 cases=[('native_vl_queue','afdx_vl_queue',['afdx_vl_queue.v'],'vl_queue_128'),('native_buffered_vl','afdx_buffered_vl',['afdx_buffered_vl.v','afdx_vl_queue.v','afdx_packet_pool.v'],'buffered_vl_128')]
 for name,top,names,folder in cases:
  result=module.check_case(name,top,[ROOT/'rtl/buffer'/f for f in names]);report=ROOT/'reports'/folder
  (report/'native_synthesis.json').write_text(json.dumps(result,indent=2)+'\n')
  print(name,result['native_resources'],flush=True)
  if top=='afdx_buffered_vl':
   vqm=ROOT/'elinx/m0_probe'/name/(name+'.runs/synth_1')/(name+'.vqm');text=vqm.read_text(errors='replace');blocks={}
   for instance,key,value in re.findall(r'defparam\s+(\S+)\s+\.(port_a_\w+)\s*=\s*(\d+)\s*;',text):blocks.setdefault(instance,{})[key]=int(value)
   payload=[b for b in blocks.values() if b.get('port_a_logical_ram_depth')==2048 and b.get('port_a_logical_ram_width')==8]
   bits=sum((b['port_a_last_address']-b['port_a_first_address']+1)*b['port_a_data_width'] for b in payload)
   memories=result['native_resources']['Total Memorys'];passed=bits==1048576 and len(payload)==512 and memories>=256 and all(b['port_a_address_width']<=11 for b in payload)
   audit={'status':'PASS' if passed else 'FAIL','payload_bits':bits,'payload_primitives':len(payload),'native_memory_blocks':memories,'vqm_sha256':hashlib.sha256(vqm.read_bytes()).hexdigest(),'scope':'payload capacity and geometry plausibility; no mapped equivalence or timing proof'}
   (report/'memory_capacity_audit.json').write_text(json.dumps(audit,indent=2)+'\n');assert passed,audit
if __name__=='__main__':main()
