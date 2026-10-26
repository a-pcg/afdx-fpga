"""Independent fixed-time wire stimuli. Never clamps application payload length."""
from pathlib import Path
import json,struct,zlib
LENGTHS=[1,16,17,18,1471]
def csum(data):
 if len(data)&1:data+=b'\0'
 total=sum(struct.unpack('!%dH'%(len(data)//2),data))
 while total>>16:total=(total&65535)+(total>>16)
 return (~total)&65535

def image(vl,sn,p):
 src=bytes([192,168,1,1]);dst=bytes([192,168,1,2])
 udp=struct.pack('!HHHH',1000,1001,len(p)+8,0)+p
 value=csum(src+dst+b'\x00\x11'+struct.pack('!H',len(udp))+udp) or 65535
 udp=udp[:6]+struct.pack('!H',value)+udp[8:]
 ip=struct.pack('!BBHHHBBH4s4s',69,0,len(udp)+20,0,16384,64,17,0,src,dst)
 ip=ip[:10]+struct.pack('!H',csum(ip))+ip[12:]
 body=bytes.fromhex('03000000')+(vl+1).to_bytes(2,'big')+bytes.fromhex('0200000000010800')+ip+udp
 body+=bytes(max(0,59-len(body)))+bytes([sn])
 assert len(body)==max(60,len(p)+43)
 return b'\x55'*7+b'\xd5'+body+zlib.crc32(body).to_bytes(4,'little')

def generate(directory):
 directory.mkdir(parents=True,exist_ok=True);events=[];plans=[];ident=1
 def add(ep,t,net,vl,sn,n,policy='required',id=None):
  nonlocal ident
  if id is None:id=ident;ident+=1
  p=bytes((id*29+i*17)%256 for i in range(n));w=image(vl,sn,p)
  events.append((ep,t,net,id,vl,sn,policy,p.hex(),w.hex()))
  return id,(len(w)+12)*8
 # Fixed starts and byte-level back-to-back bursts, never gated by DUT drain.
 for ep in range(3):
  t=256;seq=[0]*8
  for i in range(24):
   vl=0 if ep==0 else i%8
   n=LENGTHS[i%5];sn=seq[vl];seq[vl]=1 if sn==255 else sn+1
   id,gap=add(ep,t,0,vl,sn,n)
   skew=[0,8,32,80][i%4];add(ep,t+skew,1,vl,sn,n,id=id)
   # Extra fixed gap after long frames protects the no-loss case, while short
   # bursts overlap already queued frames and application backpressure.
   t+=gap+(4096 if n==1471 else 80)
  plans.append({'episode':ep,'end':t+12000,'initial_app_hold':2000,'seed':20261019+ep,'reset_at':0,'pressure':False})
 # Pressure-only frame IDs explicitly permit whole-frame loss; all content,
 # ordering, uniqueness and required recovery are still checked.
 ep=3;t=256
 for i in range(32):
  id,gap=add(ep,t,0,(i%4)*2,(i//4)+1,LENGTHS[i%4],'may_drop')
  add(ep,t+8,1,(i%4)*2+1,(i//4)+1,LENGTHS[i%4],'may_drop');t+=gap+8
 release=t+2000;recovery=release+20000
 add(ep,recovery,0,7,0,1)
 plans.append({'episode':ep,'end':recovery+5000,'initial_app_hold':release,'seed':4,'reset_at':0,'pressure':True})
 # Reset during a physical long frame; interrupted IDs are never deliverable.
 ep=4;add(ep,256,0,0,0,1471,'cancel');add(ep,256,1,0,0,1471,'cancel',id=ident-1)
 add(ep,16000,0,1,0,16)
 plans.append({'episode':ep,'end':22000,'initial_app_hold':0,'seed':5,'reset_at':1000,'pressure':False})
 # Reset while an already-validated short payload is waiting for the app.
 ep=5;add(ep,256,0,0,0,1,'cancel');add(ep,256,1,0,0,1,'cancel',id=ident-1)
 add(ep,4000,1,2,0,18)
 plans.append({'episode':ep,'end':10000,'initial_app_hold':1800,'seed':6,'reset_at':1600,'pressure':False})
 for ep in range(6):
  for net in range(2):
   lane=sorted([e for e in events if e[0]==ep and e[2]==net],key=lambda e:e[1])
   for a,b in zip(lane,lane[1:]):assert b[1]>=a[1]+(len(bytes.fromhex(a[8]))+12)*8,(a,b)
 text='\n'.join(' '.join(map(str,e)) for e in events)+'\n'
 (directory/'events.txt').write_text(text)
 (directory/'plans.txt').write_text('\n'.join(f"{p['episode']} {p['end']} {p['initial_app_hold']} {p['seed']} {p['reset_at']} {int(p['pressure'])}" for p in plans)+'\n')
 (directory/'policy.json').write_text(json.dumps({'application_lengths_unmodified':LENGTHS,'frame_padding_only':'Ethernet PAD before SN; never application bytes','stimuli':'fixed time independent lanes, no per-batch drain','expected':'required packets in per-VL independent queues; may_drop is explicit pressure-only whole-frame subset; cancel forbidden after reset','plans':plans},indent=2)+'\n')
 return events
