"""Replay eLinx Classic frontend -> VQM -> native EQ6HL130 packing.

Uses only the installed toolchain. Never edits the installation or active RTL.
The scripts/vendor output is retained under elinx/m0_probe. This is a synthesis
and native-packing check; it does not establish behavioral correctness or timing.
"""
from pathlib import Path
import argparse
import datetime
import hashlib
import json
import re
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / 'elinx' / 'm0_probe'
INSTALL = Path(r'C:\Program Files (x86)\eHiWay\eLinx3.0')
SHELL = INSTALL / 'bin' / 'shell'
CLASSIC = INSTALL / 'bin' / 'Passkey' / 'bin'


def execute(argv, cwd, log, timeout=360):
    start = time.monotonic()
    # Direct file output preserves diagnostics during long frontend runs and on
    # timeout; no pipe-buffering dependency on descendant compiler processes.
    with log.open('wb') as stream:
        result = subprocess.Popen([str(x) for x in argv], cwd=cwd,
                                  stdout=stream, stderr=subprocess.STDOUT)
        try:
            result.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            result.kill()
            result.wait()
            raise RuntimeError('Tool timeout after ' + str(timeout) +
                               ' seconds; partial log: ' + str(log))
    output = log.read_text(encoding='utf-8', errors='replace')
    record = {'argv': [str(x) for x in argv], 'cwd': str(cwd),
              'returncode': result.returncode,
              'seconds': round(time.monotonic() - start, 3),
              'log': str(log.relative_to(ROOT))}
    if result.returncode or re.search(r'(?im)^\s*(ERROR:|Error\s*\(|\[ERROR\])', output):
        raise RuntimeError(json.dumps(record) + '\n' + output[-2500:])
    return record


def prepare(name, top, source_files):
    project = BASE / name
    inputs = project / 'src'
    inputs.mkdir(parents=True, exist_ok=True)
    run = project / (name + '.runs') / 'synth_1'
    run.mkdir(parents=True, exist_ok=True)
    snapshot = {}
    for source in source_files:
        data = source.read_bytes()
        (inputs / source.name).write_bytes(data)
        snapshot[source.name] = {'sha256': hashlib.sha256(data).hexdigest(),
                                 'original': str(source)}
    (project / 'sources.json').write_text(json.dumps(snapshot, indent=2), encoding='utf-8')

    qsf = [
        'set_global_assignment -name DEVICE EP1S80F1508C6',
        'set_global_assignment -name FAMILY Stratix',
        'set_global_assignment -name INI_VARS "qatm_force_vqm=on;vqmo_gen_svgx_vqm=on"',
        'set_global_assignment -name TOP_LEVEL_ENTITY ' + top,
        'set_global_assignment -name PROJECT_OUTPUT_DIRECTORY "' + run.as_posix() + '"',
        'set_global_assignment -name MAX_RAM_BLOCKS_M512 0',
        'set_global_assignment -name MAX_RAM_BLOCKS_MRAM 0',
        'set_global_assignment -name AUTO_MERGE_PLLS OFF',
        'set_global_assignment -name AUTO_RAM_RECOGNITION ON',
        'set_global_assignment -name AUTO_ROM_RECOGNITION ON',
        'set_global_assignment -name VERILOG_INPUT_VERSION VERILOG_2001',
    ]
    qsf += ['set_global_assignment -name VERILOG_FILE "' +
            (inputs / f.name).as_posix() + '"' for f in source_files]
    (project / (name + '.qsf')).write_text('\n'.join(qsf) + '\n', encoding='utf-8')
    (project / (name + '.qpf')).write_text(
        'QUARTUS_VERSION = "13.0"\nPROJECT_REVISION = "' + name + '"\n', encoding='utf-8')

    # Native device selection is independent of the Classic frontend's
    # intermediate Stratix family. Do not report frontend resource counts as
    # EQ6HL130 implementation results.
    xml = ET.Element('Project', {'Path': '/' + name + '.epr'})
    config = ET.SubElement(xml, 'Configuration')
    ET.SubElement(config, 'Option', {'Name': 'Project Type', 'Val': 'RTL'})
    sets = ET.SubElement(xml, 'FileSets')
    design = ET.SubElement(sets, 'FileSet', {'Name': 'sources_1', 'Type': 'DesignSrcs'})
    for source in source_files:
        ET.SubElement(design, 'File', {'Path': '/src/' + source.name})
    cons = ET.SubElement(sets, 'FileSet', {'Name': 'constrs_1', 'Type': 'Constrs'})
    ET.SubElement(cons, 'File', {'Path': '/src/core.edc'})
    ET.SubElement(ET.SubElement(cons, 'Config'), 'Option',
                  {'Name': 'TargetConstrsFile', 'Val': '/src/core.edc'})
    ET.SubElement(sets, 'FileSet', {'Name': 'sim_1', 'Type': 'SimulationSrcs'})
    runs = ET.SubElement(xml, 'Runs')
    synth = ET.SubElement(runs, 'Run', {'Id': 'synth_1', 'Type': 'Synthesis',
                                       'SrcSet': 'sources_1', 'ConstrsSet': 'constrs_1'})
    for key, value in [('Compilation Flow', 'Classic Flow'), ('Device', 'EQ6HL130'),
                       ('Package', 'CSG484_H'), ('Series', 'eHiChip6'),
                       ('LanguageVersion', 'VERILOG_2001'), ('TopModule', top)]:
        ET.SubElement(synth, 'Option', {'Id': key}).text = value
    ET.ElementTree(xml).write(project / (name + '.epr'), encoding='utf-8', xml_declaration=True)
    constraint = inputs / 'core.edc'
    constraint.write_text('create_clock -name clk -period 10.000 [get_ports {clk}]\n', encoding='utf-8')
    (run / (name + '.run.psf')).write_text(
        'source {' + constraint.as_posix() + '}\n' +
        'set_global_assignment -name TARGET_CONSTRAINT_FILE {' + constraint.as_posix() + '}\n',
        encoding='utf-8')
    shutil.copyfile(SHELL / 'Infrastructure/psk_extern/log4cpp/log4cpp.property',
                    project / 'log4cpp.property')
    # Explicit exit is essential. Without it this executable completed packing
    # then returned 0xC0000374 in the first experiment.
    script = ('source {' + (SHELL / 'bin/run_pack.tcl').as_posix() + '}\n' +
              'run_pack {' + project.as_posix() + '} ' + name + ' ' + top +
              ' eHiChip6 EQ6HL130 CSG484_H synth_1\nexit\n')
    (project / 'run_native_pack.tcl').write_text(script, encoding='utf-8')
    return project, run, snapshot


def check_case(name, top, files):
    project, run, snapshot = prepare(name, top, files)
    steps = []
    steps.append(execute([CLASSIC / 'quartus_map.exe', '--read_settings_files=on',
                           '--write_settings_files=off', name], project, project / 'classic.log'))
    map_summary = (run / (name + '.map.summary')).read_text(errors='replace')
    if not re.search(r'Status\s*:\s*Successful', map_summary):
        raise RuntimeError('Classic report did not report success')
    vqm = run / (name + '.vqm')
    steps.append(execute([CLASSIC / 'quartus_cdb.exe', name, '--vqm=' + vqm.as_posix()],
                          project, project / 'vqm_export.log'))
    if not vqm.is_file() or vqm.stat().st_size == 0:
        raise RuntimeError('VQM output missing')
    steps.append(execute([SHELL / 'bin/Implementation.exe', '-f',
                           project / 'run_native_pack.tcl', '-silence'], SHELL / 'bin',
                          project / 'console.log'))
    native = (run / (name + '_utilization_synth.rpt')).read_text(errors='replace')
    console = (project / 'console.log').read_text(encoding='utf-8')
    checkpoint = run / (name + '_eHiChip6.ecp')
    if 'Synthesis Status : Successful' not in native or 'pack_gene is 1' not in console:
        raise RuntimeError('Native pack success markers missing')
    if not checkpoint.is_file() or not checkpoint.stat().st_size:
        raise RuntimeError('Native checkpoint missing')
    edi = (run / (name + '.edi')).read_text(encoding='utf-8', errors='replace')
    diagnostics = [line for line in edi.splitlines() if '[CRIT]' in line or '[ERROR]' in line]
    stats = dict(re.findall(r'^(Total [^:]+)\s*:\s*(\d+)\s*$', native, re.M))
    return {'status': 'SYNTHESIS_AND_PACK_PASS', 'top': top,
            'native_target': 'eHiChip6/EQ6HL130/CSG484_H', 'source_snapshot': snapshot,
            'steps': steps, 'native_resources': {k.strip(): int(v) for k, v in stats.items()},
            'native_diagnostics': diagnostics, 'timing_verified': False,
            'behavior_verified_by_this_script': False,
            'checkpoint': str(checkpoint.relative_to(ROOT)),
            'checkpoint_sha256': hashlib.sha256(checkpoint.read_bytes()).hexdigest()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--baseline', action='store_true', help='also snapshot and synthesize current M1 core')
    parser.add_argument('--only-baseline', action='store_true', help='check the core without repeating successful probes')
    args = parser.parse_args()
    BASE.mkdir(parents=True, exist_ok=True)
    cases = [('native_fifo', 'afdx_env_probe', [BASE / 'src/afdx_env_probe.v']),
             ('native_ram', 'm0_ram_probe', [BASE / 'src/m0_ram_probe.v'])]
    if args.only_baseline:
        cases = []
    if args.baseline or args.only_baseline:
        cases.append(('native_baseline', 'afdx_baseline_core',
                       [ROOT / 'rtl' / n for n in
                        ['afdx_baseline_core.v', 'afdx_udp_tx.v', 'afdx_udp_rx.v']]))
    results = {'checked_at': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'cases': {}}
    status = 0
    for name, top, files in cases:
        try:
            result = check_case(name, top, files)
        except Exception as error:
            status = 1
            result = {'status': 'FAIL', 'error': str(error)}
        results['cases'][name] = result
        print(name + ': ' + result['status'], flush=True)
    (BASE / 'results.json').write_text(json.dumps(results, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return status


if __name__ == '__main__':
    sys.exit(main())
