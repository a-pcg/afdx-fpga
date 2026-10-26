"""Independent physical-byte golden vectors for shared-pool transmission."""
from pathlib import Path
import sys,json,hashlib,zlib
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'scripts'))
from rtl_test_build import build,execute
from generate_vectors import frame
from evidence import capture

def main():
 for vw,sw in [(2,4),(7,6)]:
  vls=1<<vw;selected=[0,1,vls//2,vls-1];rows=[]
  def add(phase,vl,payload,sn):
   body=bytearray(frame(payload,sn));body[:6]=(0x030000000001+vl).to_bytes(6,'big')
   physical=b'\x55'*7+b'\xd5'+body+zlib.crc32(body).to_bytes(4,'little')
   rows.append(f'{phase} {vl} {payload.hex()} {physical.hex()}')
  for attempt in range(4):
   for index,vl in enumerate(selected):
    length=[1,17,1471,63][(attempt+index)%4];payload=bytes((i*17+vl*23+attempt)%256 for i in range(length))
    add('A',vl,payload,max(0,attempt-1) if vl==0 else attempt)
  sn=3
  for i in range(260 if vw==2 else 8):
   add('B',0,bytes([i%256]),sn);sn=1 if sn==255 else sn+1
  if vw==7:
   for vl in range(vls):
    next_value=sn if vl==0 else 4 if vl in selected else 0
    add('C',vl,bytes((vl+i*7)%256 for i in range(1+(vl*37)%128)),next_value)
  add('D',0,bytes([0x71,0x82,0x93]),sn+(1 if vw==7 else 0))
  add('R',vls-1,bytes(range(17)),0)
  folder=ROOT/'vectors'/('shared_tx_'+str(vls));folder.mkdir(parents=True,exist_ok=True);text='\n'.join(rows)+'\n';(folder/'cases.txt').write_text(text)
  names=['rtl/afdx_shared_tx.v','rtl/afdx_udp_tx_shared.v','rtl/afdx_bag_scheduler64.v','rtl/eth_mac_tx.v','rtl/eth_crc32_byte.v','rtl/buffer/afdx_buffered_vl.v','rtl/buffer/afdx_vl_queue.v','rtl/buffer/afdx_packet_pool.v']
  sources=[ROOT/name for name in names];tb=ROOT/'tb/shared_tx.cpp';report=ROOT/'reports'/('shared_tx_'+str(vls))
  parameters={'VL_WIDTH':vw,'SLOT_WIDTH':sw,'INITIAL_TIME_LOW':-65536}
  report.mkdir(parents=True,exist_ok=True)
  (report/'result.json').write_text(json.dumps({'status':'RUNNING'})+'\n')
  exe,env=build('afdx_shared_tx',sources,tb,report,parameters)
  manifest={str(p.relative_to(ROOT)).replace('\\','/'):hashlib.sha256(p.read_bytes()).hexdigest() for p in sources+[tb]}
  (report/'inputs.json').write_text(json.dumps({'parameters':parameters,'sources':manifest,'vectors_sha256':hashlib.sha256((folder/'cases.txt').read_bytes()).hexdigest(),'vectors_lf_sha256':hashlib.sha256(text.encode()).hexdigest()},indent=2)+'\n')
  capture(report,names,parameters,env)
  execute([exe,vls,1<<sw,folder/'cases.txt',report/'result.json'],report/'simulation.log',env)
  print((report/'result.json').read_text(),flush=True)
if __name__=='__main__':main()
