"""Replay native synthesis/packing for the physical SFD/BAG integration."""
from pathlib import Path
import importlib.util
import json
ROOT=Path(__file__).resolve().parents[2]
def main():
    spec=importlib.util.spec_from_file_location('native_check',ROOT/'scripts/m0_native/run.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    names=['afdx_scheduled_wire_tx.v','afdx_bag_scheduler.v','afdx_udp_tx.v','eth_mac_tx.v','eth_crc32_byte.v']
    result=module.check_case('native_scheduled_wire','afdx_scheduled_wire_tx',[ROOT/'rtl'/name for name in names])
    (ROOT/'reports/scheduled_wire_tx/native_synthesis.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result['native_resources']),flush=True)
if __name__=='__main__':main()
