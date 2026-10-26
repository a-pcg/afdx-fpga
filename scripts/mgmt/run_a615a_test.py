from pathlib import Path
import sys
ROOT=Path(r'C:/Users/zhang/Desktop/test/afdx')
sys.path.insert(0,str(ROOT/'scripts'))
from rtl_test_build import build, execute
OUT=ROOT/'reports/a615a'
OUT.mkdir(parents=True,exist_ok=True)
sources=[ROOT/'rtl/mgmt/afdx_a615a_agent.v']
tb=ROOT/'tb/a615a_agent_test.cpp'
exe,env=build('afdx_a615a_agent',sources,tb,OUT)
log=OUT/'simulation.log'
execute([exe],log,env)
text=log.read_text(errors='replace')
tail=text.strip().splitlines()[-3:]
print(chr(10).join(tail))
ok='TEST_PASS' in text
print('A615A='+('PASS' if ok else 'FAIL'))