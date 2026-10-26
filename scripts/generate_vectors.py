"""Independent byte-level vectors; only Python standard library, no DUT algorithm import."""
from pathlib import Path
import struct,random,zlib,json
ROOT=Path(__file__).resolve().parents[1]
def checksum(data):
 if len(data)%2:data+=b'\0'
 n=sum(struct.unpack('!%dH'%(len(data)//2),data))
 while n>>16:n=(n&65535)+(n>>16)
 return (~n)&65535
def frame(payload,sn):
 src=bytes([192,168,1,1]);dst=bytes([192,168,1,2]);u=struct.pack('!HHHH',1000,1001,8+len(payload),0)
 c=checksum(src+dst+b'\0\x11'+struct.pack('!H',len(u)+len(payload))+u+payload) or 65535
 u=u[:6]+struct.pack('!H',c)
 ip=struct.pack('!BBHHHBBH4s4s',0x45,0,28+len(payload),0,0x4000,64,17,0,src,dst)
 ip=ip[:10]+struct.pack('!H',checksum(ip))+ip[12:]
 return bytes.fromhex('0300000000010200000000010800')+ip+u+payload+bytes(max(0,17-len(payload)))+bytes([sn])
def main():
 rng=random.Random(20261017);lengths=[1,2,15,16,17,18,255,256,511,512,1470,1471]+[rng.randrange(1,1472) for _ in range(24)]+[1]*224
 cases=[]
 for i,n in enumerate(lengths):
  payload=bytes(rng.randrange(256) for _ in range(n));sn=0 if i==0 else ((i-1)%255)+1
  cases.append((payload,frame(payload,sn)))
 # Force UDP computed checksum 0 -> wire 0xffff.
 for x in range(65536):
  payload=x.to_bytes(2,'big');f=frame(payload,((len(cases)-1)%255)+1)
  if f[40:42]==b'\xff\xff':cases.append((payload,f));break
 lines=['%s %s'%(a.hex(),b.hex()) for a,b in cases]
 (ROOT/'vectors/tx_frames.txt').write_text('\n'.join(lines)+'\n')
 for n,i in [(1,0),(17,4),(1471,11)]:
  payload=cases[i][0];fr=frame(payload,{1:0,17:1,1471:2}[n])
  (ROOT/('vectors/smoke_payload_'+str(n)+'.hex')).write_text('\n'.join('%02x'%x for x in payload)+'\n')
  (ROOT/('vectors/smoke_frame_'+str(n)+'.hex')).write_text('\n'.join('%02x'%x for x in fr)+'\n')

 good=bytearray(cases[20][1]);good[20:22]=b'\0\0';good[24:26]=b'\0\0';good[24:26]=struct.pack('!H',checksum(bytes(good[14:34])))
 (ROOT/'vectors/rx_good.txt').write_text(cases[20][0].hex()+' '+good.hex()+'\n')
 scheduled=[]
 for attempt in range(4):
  for vl in range(4):
   size=[1,17,1471,63][(attempt+vl)%4];payload=bytes((x*17+vl*23+attempt)%256 for x in range(size))
   fr=bytearray(frame(payload,attempt));fr[5]=vl+1
   scheduled.append(str(vl)+' '+payload.hex()+' '+fr.hex())
 (ROOT/'vectors/scheduled_frames.txt').write_text('\n'.join(scheduled)+'\n')
 bad=[]
 for label,index,mask in [('ip_checksum',24,1),('udp_payload',42,1),('unknown_vl',5,1),('udp_zero',40,0),('ip_version',14,0x10),('ip_options',14,1),('fragment',20,0x20),('ether_type',12,0x80),('udp_length',39,1)]:
  f=bytearray(cases[20][1])
  if label=='udp_zero':f[40:42]=b'\0\0'
  else:f[index]^=mask
  bad.append((label,bytes(f),0))
 bad.extend([('mac_error',cases[0][1],1),('short',cases[0][1][:40],0),('missing_sop',cases[0][1],2),('extra_sop',cases[0][1],3),('oversize',cases[0][1]+bytes(1500),0)])
 (ROOT/'vectors/rx_bad.txt').write_text('\n'.join('%s %d %s'%(n,e,f.hex()) for n,f,e in bad)+'\n')
 meta={'seed':20261017,'valid_frames':len(cases),'bad_frames':len(bad),'extra_positive_rx_frames':1,'scheduled_frames':16, 'payload_lengths':sorted(set(map(lambda c:len(c[0]),cases))), 'client_bytes':sum(len(f) for _,f in cases),'line_bytes':sum(len(f)+24 for _,f in cases),'mac_fcs_samples':[hex(zlib.crc32(f)) for _,f in cases[:3]]}
 (ROOT/'reports/vectors.json').write_text(json.dumps(meta,indent=2)+'\n');print(json.dumps(meta))
if __name__=='__main__':main()
