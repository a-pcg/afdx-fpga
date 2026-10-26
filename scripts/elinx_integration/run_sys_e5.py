"""E5: standalone real-pin timing case for the R3 timing-converged dual RX.

Same physical boundary, device target and vendor flow as the accepted E3 case,
but the source list points at the CURRENT RX RTL revision, every run is labelled
with an experiment tag and no E3 directory is ever reused or overwritten.

Usage:
  python scripts/elinx_integration/run_sys_e5.py --tag base

Products:
  elinx/integration/sys_e5/<stamp>_<tag>/        vendor project + logs + checkpoint
  elinx/integration/sys_e5/<stamp>_<tag>/e5_results.json
  reports/elinx_integration/sys_e5_latest.json   latest pack summary
  reports/sys_e5_runs/<tag>_pack.json            per-experiment pack summary
"""
from pathlib import Path
import importlib.util, hashlib, json, datetime, re, subprocess, os, argparse, sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/system_integration"))
import run_config
OUT = ROOT / "reports/elinx_integration"
RUNS = ROOT / "reports/sys_e5_runs"
CASE = ROOT / "elinx/integration/sys_e5"
WRAPPER = CASE / "src/afdx_sys_e5_top.v"
NAME = "native_sys_e5"
TOP = "afdx_sys_e5_top"
FREE_IO_LOCATIONS = 339
INSTALL = Path(r"C:/Program Files (x86)/eHiWay/eLinx3.0")
SHELL = INSTALL / "bin/shell"
CLASSIC = INSTALL / "bin/Passkey/bin"
VERILATOR = INSTALL / "verilator/verilator.exe"
VER_INC = ROOT / "third_party/verilator-5.006/include"

SOURCES = [
    "rtl/system/afdx_system_top.v",
    "rtl/afdx_shared_tx.v",
    "rtl/afdx_udp_tx_shared.v",
    "rtl/afdx_bag_scheduler64.v",
    "rtl/eth_mac_tx.v",
    "rtl/eth_crc32_byte.v",
    "rtl/buffer/afdx_buffered_vl.v",
    "rtl/buffer/afdx_vl_queue.v",
    "rtl/buffer/afdx_packet_pool.v",
    "rtl/rx/afdx_rx_dual.v",
    "rtl/rx/afdx_rx_merge.v",
    "rtl/rx/afdx_integrity_checker.v",
    "rtl/afdx_udp_rx.v",
    "rtl/eth_mac_rx.v",
]
BASELINE = {}
EXPECTED_MODULES = ["afdx_system_top", "afdx_shared_tx", "afdx_udp_tx_shared",
                    "afdx_bag_scheduler64", "eth_mac_tx", "eth_crc32_byte",
                    "afdx_buffered_vl", "afdx_vl_queue", "afdx_packet_pool",
                    "afdx_rx_dual", "afdx_rx_merge", "afdx_integrity_checker",
                    "afdx_udp_rx", "eth_mac_rx", "afdx_sys_e5_top"]


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def strip_comments(text):
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"//[^\n]*", "", text)


def module_closure(texts):
    mods = {}
    for name, text in texts.items():
        body = strip_comments(text)
        m = re.search(r"\bmodule\s+(\w+)", body)
        mods[m.group(1)] = body
    graph = {}
    for mod, body in mods.items():
        graph[mod] = sorted(other for other in mods
                            if other != mod and re.search(r"\b" + re.escape(other) + r"\s+(?:#\s*\(|\w+\s*\()", body))
    return graph


def wrapper_pins(path):
    text = strip_comments(Path(path).read_text())
    header = text.split(");", 1)[0]
    pins = {"inputs": {}, "outputs": {}}
    for direction, hi, lo, name in re.findall(
            r"\b(input|output)\s+wire\s*(?:\[\s*(\d+)\s*:\s*(\d+)\s*\])?\s*(\w+)", header):
        w = abs(int(hi) - int(lo)) + 1 if hi else 1
        pins[direction + "s"][name] = w
    return pins


def verify_sources(report, total_pins):
    if total_pins > FREE_IO_LOCATIONS * 0.75:
        raise RuntimeError("Narrowed top still uses too many IO: %d" % total_pins)
    hashes = {rel: sha(ROOT / rel) for rel in SOURCES}
    hashes["elinx/integration/sys_e5/src/afdx_sys_e5_top.v"] = sha(WRAPPER)
    report["input_hashes"] = hashes
    report["changed_vs_baseline"] = []
    report["baseline_hashes"] = BASELINE
    report["wrapper_sha256"] = hashes["elinx/integration/sys_e5/src/afdx_sys_e5_top.v"]
    texts = {rel: (ROOT / rel).read_text(errors="replace") for rel in SOURCES}
    texts["elinx/integration/sys_e5/src/afdx_sys_e5_top.v"] = WRAPPER.read_text(errors="replace")
    graph = module_closure(texts)
    report["module_dependency_graph"] = graph
    if sorted(graph) != sorted(EXPECTED_MODULES):
        raise RuntimeError("Unexpected module closure: " + repr(sorted(graph)))


def verify_pack(report, project, run, total_pins):
    summary = (run / (NAME + ".map.summary")).read_text(errors="replace")
    if not re.search(r"Status\s*:\s*Successful", summary):
        raise RuntimeError("Classic not successful")
    classic_pins = int(re.search(r"Total pins\s*:\s*(\d+)", summary).group(1))
    if classic_pins != total_pins:
        raise RuntimeError("Classic pin count %d != wrapper ports %d" % (classic_pins, total_pins))
    report["classic_summary"] = summary
    report["classic_pins"] = classic_pins
    vqm = run / (NAME + ".vqm")
    if not vqm.is_file() or not vqm.stat().st_size:
        raise RuntimeError("No VQM")
    report["vqm_sha256"] = sha(vqm)
    native = (run / (NAME + "_utilization_synth.rpt")).read_text(errors="replace")
    console = (project / "console.log").read_text(errors="replace")
    checkpoint = run / (NAME + "_eHiChip6.ecp")
    if "Synthesis Status : Successful" not in native or "pack_gene is 1" not in console \
            or not checkpoint.is_file() or not checkpoint.stat().st_size:
        raise RuntimeError("Pack success evidence missing")
    if "All was well!" not in console:
        raise RuntimeError("Pack did not reach its completion marker")
    ver_pb = run / (NAME + ".ver.pb")
    if not ver_pb.is_file() or not ver_pb.stat().st_size:
        raise RuntimeError("Native version checkpoint (.ver.pb) missing")
    cluster_xml = run / (NAME + "_cluster.xml")
    if not cluster_xml.is_file() or not cluster_xml.stat().st_size:
        raise RuntimeError("Native cluster netlist missing")
    native_memory = int(re.search(r"Total Memorys\s*:\s*(\d+)", native).group(1))
    try:
        cluster_count = len(ET.parse(cluster_xml).getroot().findall("CLUSTER"))
    except ET.ParseError as error:
        raise RuntimeError("Native cluster netlist does not parse: " + str(error))
    if cluster_count < native_memory:
        raise RuntimeError("Cluster netlist incomplete: %d < %d" % (cluster_count, native_memory))
    native_pins = int(re.search(r"Total Pins\s*:\s*(\d+)", native).group(1))
    if native_pins != total_pins:
        raise RuntimeError("Native pin count %d != wrapper ports %d" % (native_pins, total_pins))
    report["native_utilization"] = native
    report["native_pins"] = native_pins
    report["netlogic"] = {k.strip(): int(v) for k, v in
                          re.findall(r"^(Total (?:LUTs|FFs|CLBs|Memorys|DSPs|PLLs|Pins))\s*:\s*(\d+)",
                                     native, re.M)}
    report["pack_checkpoint_sha256"] = sha(checkpoint)
    report["pack_console_sha256"] = sha(project / "console.log")
    report["classic_log_sha256"] = sha(project / "classic.log")
    report["pack_verified"] = True
    report["stage"] = "PACK_PASS"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="base", help="experiment label, e.g. base/r1_merge_pipe")
    args = ap.parse_args()
    RUNS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    base = CASE / (stamp + "_" + args.tag)
    base.mkdir(parents=True)
    pins = wrapper_pins(WRAPPER)
    total_pins = sum(pins["inputs"].values()) + sum(pins["outputs"].values())
    # P1: the wrapper fixes the elaboration time base; validate the legal pair and
    # record it in the manifest so a case can never mix a clock with a stale BAG
    # constant. The matching SDC period is 1/core_clk_hz (20 ns for 50 MHz).
    elab = run_config.parse_wrapper_parameters(WRAPPER)
    run_config.validate(elab["core_clk_hz"], elab["byte_cycles"])
    report = {"case_root": str(base.relative_to(ROOT)), "experiment": args.tag,
              "stage": "INPUT_VERIFICATION", "top": TOP, "case_name": NAME,
              "parameters": {"tx_vls": 128, "rx_vls_per_network": 8, "time_width": 64,
                             "core_clk_hz": elab["core_clk_hz"], "byte_cycles": elab["byte_cycles"],
                             "cycles_per_ms": elab["cycles_per_ms"]},
              "elaboration_parameters": elab,
              "native_target": "eHiChip6/EQ6HL130/CSG484_H",
              "free_io_locations": FREE_IO_LOCATIONS,
              "wrapper": str(WRAPPER.relative_to(ROOT)),
              "wrapper_pin_breakdown": {"inputs": pins["inputs"], "outputs": pins["outputs"],
                                        "input_pins": sum(pins["inputs"].values()),
                                        "output_pins": sum(pins["outputs"].values()),
                                        "total_pins": total_pins},
              "pack_verified": False, "internal_timing_verified": False,
              "system_timing_verified": False, "io_timing_budget_provided": False,
              "physical_pins_verified": False}
    project = base / NAME
    run = project / (NAME + ".runs/synth_1")

    def save():
        (base / "e5_results.json").write_text(json.dumps(report, indent=2))
        (OUT / "sys_e5_latest.json").write_text(json.dumps(report, indent=2))
        (RUNS / (args.tag + "_pack.json")).write_text(json.dumps(report, indent=2))

    try:
        verify_sources(report, total_pins)
        save()
        report["stage"] = "WRAPPER_ELABORATION"; save()

        env = {k.upper(): v for k, v in os.environ.items()}
        env["VERILATOR_ROOT"] = str(VER_INC.parent).replace("\\", "/")
        lint_cmd = [str(VERILATOR), "--lint-only", "--default-language", "1364-2001",
                    "--top-module", TOP, "--Mdir", str(base / "verilator_obj")]
        lint_cmd += [Path(rel).as_posix() for rel in SOURCES]
        lint_cmd += [WRAPPER.relative_to(ROOT).as_posix()]
        with (base / "verilator_lint.log").open("wb") as stream:
            lint = subprocess.run(lint_cmd, cwd=ROOT, env=env, stdout=stream,
                                  stderr=subprocess.STDOUT, timeout=300)
        lint_log = (base / "verilator_lint.log").read_text(errors="replace")
        report["wrapper_elaboration"] = {"argv": lint_cmd, "returncode": lint.returncode,
                                         "log": str((base / "verilator_lint.log").relative_to(ROOT)),
                                         "errors": [l for l in lint_log.splitlines() if "%Error" in l]}
        if lint.returncode or "%Error" in lint_log:
            raise RuntimeError("Wrapper does not elaborate in Verilator")

        spec = importlib.util.spec_from_file_location("native_readonly", ROOT / "scripts/m0_native/run.py")
        n = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(n)
        n.BASE = base
        sources = [ROOT / rel for rel in SOURCES] + [WRAPPER]
        project, run, snapshot = n.prepare(NAME, TOP, sources)
        report["source_snapshot"] = snapshot
        report["stage"] = "CLASSIC_SYNTHESIS"; save()
        steps = []
        steps.append(n.execute([CLASSIC / "quartus_map.exe", "--read_settings_files=on",
                                "--write_settings_files=off", NAME],
                               project, project / "classic.log", timeout=3600))
        report["stage"] = "VQM_EXPORT"; save()
        vqm = run / (NAME + ".vqm")
        steps.append(n.execute([CLASSIC / "quartus_cdb.exe", NAME, "--vqm=" + vqm.as_posix()],
                               project, project / "vqm_export.log", timeout=900))
        report["stage"] = "NATIVE_PACK"; save()
        steps.append(n.execute([SHELL / "bin/Implementation.exe", "-f",
                                project / "run_native_pack.tcl", "-silence"],
                               SHELL / "bin", project / "console.log", timeout=3600))
        report["steps"] = steps
        verify_pack(report, project, run, total_pins)
        save()
        print("PACK_PASS: " + str(base), flush=True)
    except Exception as error:
        report["failure"] = str(error)
        report["stage"] = "FAILED"
        save()
        raise
    finally:
        report["artifacts"] = {str(p.relative_to(base)): {"sha256": sha(p), "bytes": p.stat().st_size}
                               for p in base.rglob("*") if p.is_file()
                               and p.name != "e5_results.json"}
        save()


if __name__ == "__main__":
    main()

