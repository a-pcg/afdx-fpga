from pathlib import Path
import sys,random,json,zlib
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'models'))
from ethernet_reference import wire_frame,check_wire
r=random.Random(20261018)
lengths=[60,61,62,63,64,127,128,255,256,511,512,1513,1514]+[r.randint(60,1514) for _ in range(19)]
good=[]
for n in lengths:
 c=bytes(r.randrange(256) for _ in range(n));w=wire_frame(c);assert check_wire(w);good.append((c,w))
# Include real AFDX bytes independently generated in the previous milestone.
for line in (ROOT/'vectors/tx_frames.txt').read_text().splitlines()[:12]:
 c=bytes.fromhex(line.split()[1]);good.append((c,wire_frame(c)))
(ROOT/'vectors/mac_good.txt').write_text('\n'.join(c.hex()+' '+w.hex() for c,w in good)+'\n')
c,w=good[4];bad=[]
x=bytearray(w);x[8+30]^=1;bad.append(('data_bit',1,0,bytes(x)))
x=bytearray(w);x[-1]^=1;bad.append(('fcs_bit',1,0,bytes(x)))
x=bytearray(w);x[-4:]=x[-4:][::-1];assert x!=w;bad.append(('fcs_endian',1,0,bytes(x)))
x=bytearray(w);x[3]=0x54;bad.append(('preamble',2,0,bytes(x)))
x=bytearray(w);x[7]=0x55;bad.append(('sfd',2,0,bytes(x)))
bad.extend([('short_preamble',2,0,b'\x55'*4),('short_frame',2,0,wire_frame(c[:59])),('giant',2,0,wire_frame(bytes(1515))),('line_error',2,1,w),('missing_fcs',1,0,w[:-4])])
(ROOT/'vectors/mac_bad.txt').write_text('\n'.join(n+' '+str(reason)+' '+str(err)+' '+f.hex() for n,reason,err,f in bad)+'\n')
(ROOT/'reports/mac_vectors.json').write_text(json.dumps({'seed':20261018,'good':len(good),'bad':len(bad),'known_check_123456789':hex(zlib.crc32(b'123456789')),'post_fcs_zlib_residue':'0x2144df1c'},indent=2)+'\n')
print('MAC vectors:',len(good),'good,',len(bad),'bad')
