"""Directed T3 configuration guard, driven through real SFDs and port handshakes."""
from pathlib import Path
import sys,json,zlib,hashlib
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'scripts'))
from rtl_test_build import build,execute
from generate_vectors import frame
from evidence import capture

def main():
 folder=ROOT/'vectors/shared_tx_guard';folder.mkdir(parents=True,exist_ok=True);rows=[]
 for vl in range(4):
  for sn in range(10):
   body=bytearray(frame(bytes(range(17)),sn));body[:6]=(0x030000000001+vl).to_bytes(6,'big')
   line=b'\x55'*7+b'\xd5'+body+zlib.crc32(body).to_bytes(4,'little');rows.append(f'{vl} {sn} {line.hex()}')
 (folder/'cases.txt').write_text('\n'.join(rows)+'\n')
 names=['rtl/afdx_shared_tx.v','rtl/afdx_udp_tx_shared.v','rtl/afdx_bag_scheduler64.v','rtl/eth_mac_tx.v','rtl/eth_crc32_byte.v','rtl/buffer/afdx_buffered_vl.v','rtl/buffer/afdx_vl_queue.v','rtl/buffer/afdx_packet_pool.v']
 report=ROOT/'reports/shared_tx_guard';report.mkdir(exist_ok=True);(report/'result.json').write_text('{"status":"RUNNING"}\n')
 parameters={'VL_WIDTH':2,'SLOT_WIDTH':4,'INITIAL_TIME_LOW':-65536,'INITIAL_TIME_HIGH':7}
 exe,env=build('afdx_shared_tx',[ROOT/n for n in names],ROOT/'tb/shared_tx_guard.cpp',report,parameters)
 capture(report,names+['tb/shared_tx_guard.cpp'],parameters,env)
 execute([exe,folder/'cases.txt',report/'result.json'],report/'simulation.log',env)
 print((report/'result.json').read_text(),flush=True)
if __name__=='__main__':main()
