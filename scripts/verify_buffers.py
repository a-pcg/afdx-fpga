"""Replay buffer unit/integration suites; native packing is opt-in and sequential."""
from pathlib import Path
import argparse,hashlib,json,subprocess,sys,time
ROOT=Path(__file__).resolve().parents[1]
def main():
 parser=argparse.ArgumentParser();parser.add_argument('--native',action='store_true');args=parser.parse_args()
 steps=['packet_pool/run.py','vl_queue/run.py','buffered_vl/run.py']
 if args.native:steps+=['packet_pool/native.py','buffered_vl/native.py']
 result={'status':'RUNNING','steps':[]};output=ROOT/'reports/buffer_verification.json'
 output.write_text(json.dumps(result,indent=2)+'\n')
 try:
  for step in steps:
   start=time.monotonic();subprocess.run([sys.executable,str(ROOT/'scripts'/step)],cwd=ROOT,check=True)
   result['steps'].append({'script':step,'seconds':round(time.monotonic()-start,2)})
   output.write_text(json.dumps(result,indent=2)+'\n')
  result['sources']={str(p.relative_to(ROOT)).replace('\\','/'):hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT/'rtl/buffer').glob('*.v')}
  result['status']='PASS'
 except Exception as error:
  result['status']='FAIL';result['error']=str(error);raise
 finally:output.write_text(json.dumps(result,indent=2)+'\n')
if __name__=='__main__':main()
