from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'models'))
from ethernet_reference import wire_frame
out=[]
for line in (ROOT/'vectors/tx_frames.txt').read_text().splitlines():
 payload,frame=line.split();client=bytes.fromhex(frame);out.append(payload+' '+wire_frame(client).hex())
(ROOT/'vectors/wire_good.txt').write_text('\n'.join(out)+'\n')
# Valid Ethernet FCS around corrupt UDP/IP lets the DUT demonstrate layer isolation.
a=bytes.fromhex((ROOT/'vectors/tx_frames.txt').read_text().splitlines()[11].split()[1])
b=bytearray(a);b[42]^=1
(ROOT/'vectors/wire_bad_udp.txt').write_text(wire_frame(bytes(b)).hex()+'\n')
print('wire vectors:',len(out))
