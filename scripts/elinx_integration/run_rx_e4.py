"""E4: standalone real-pin timing case for the R3 timing-converged dual RX.

Same physical boundary, device target and vendor flow as the accepted E3 case,
but the source list points at the CURRENT RX RTL revision, every run is labelled
with an experiment tag and no E3 directory is ever reused or overwritten.

Usage:
  python scripts/elinx_integration/run_rx_e4.py --tag base

Products:
  elinx/integration/rx_e4/<stamp>_<tag>/        vendor project + logs + checkpoint
  elinx/integration/rx_e4/<stamp>_<tag>/e4_results.json
  reports/elinx_integration/rx_e4_latest.json   latest pack summary
  reports/rx_r3_runs/<tag>_pack.json            per-experiment pack summary
"""
from pathlib import Path
import importlib.util, hashlib, json, datetime, re, subprocess, os, argparse
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "reports/elinx_integration"
RUNS = ROOT / "reports/rx_r3_runs"
CASE = ROOT / "elinx/integration/rx_e4"
WRAPPER = CASE / "src/afdx_rx_e4_top.v"
NAME = "native_rx_e4"
TOP = "afdx_rx_e4_top"
FREE_IO_LOCATIONS = 339
INSTALL = Path(r"C:/Program Files (x86)/eHiWay/eLinx3.0")
SHELL = INSTALL / "bin/shell"
CLASSIC = INSTALL / "bin/Passkey/bin"
VERILATOR = INSTALL / "verilator/verilator.exe"
VER_INC = ROOT / "third_party/verilator-5.006/include"

SOURCES = [
    "rtl/rx/afdx_rx_dual.v",
    "rtl/rx/afdx_rx_merge.v",
    "rtl/rx/afdx_integrity_checker.v",
    "rtl/eth_mac_rx.v",
    "rtl/eth_crc32_byte.v",
    "rtl/afdx_udp_rx.v",
]
EXPECTED_MODULES = ["afdx_rx_dual", "afdx_rx_merge", "afdx_integrity_checker",
                    "eth_mac_rx", "eth_crc32_byte", "afdx_udp_rx", "afdx_rx_e4_top"]
# Accepted E3 (frozen stage1) hashes: recorded so every E4 experiment states
# exactly which RX sources it changed relative to the E3 baseline.
E3_BASELINE = {
 "rtl/rx/afdx_rx_dual.v": "ec71defb922c302eafb8b129dba91c679943a28d1c83623786cfa194eb2e9d6b",
 "rtl/rx/afdx_rx_merge.v": "746bfdb4942ef03aa58b8ba059de47cd7aa774b2fdf45efbc9cc20a83d056ca2",
 "rtl/rx/afdx_integrity_checker.v": "fbdc0f592951ea187337a570dc41f43b227216b6d6d7429af173d6092176c765",
 "rtl/eth_mac_rx.v": "0d575142b86fd273dcc06008f81a705d31c269575e2d270e8f9ae7197ac0f101",
 "rtl/eth_crc32_byte.v": "2faf48bbcf1b0872eadbdeebf1d6233bc373eb7022ae21d50f98c3c9d2729736",
 "rtl/afdx_udp_rx.v": "cba9cf7552c533c78813cf3632c059b24bf5a149943cabf7baab5b7432265cb1",
}


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
    hashes[str(WRAPPER.relative_to(ROOT)).replace(chr(92), '/')] = sha(WRAPPER)
    report["input_hashes"] = hashes
    report["changed_vs_e3"] = sorted(rel for rel in SOURCES if hashes[rel] != E3_BASELINE[rel])
    report["e3_baseline_hashes"] = E3_BASELINE
    report["wrapper_sha256"] = sha(WRAPPER)
    texts = {rel: (ROOT / rel).read_text(errors="replace") for rel in SOURCES}
    texts[str(WRAPPER.relative_to(ROOT)).replace(chr(92), '/')] = WRAPPER.read_text(errors="replace")
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
    global WRAPPER
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="base", help="experiment label, e.g. base/r1_merge_pipe")
    ap.add_argument("--wrapper", default=None, help="optional wrapper file (default: src/afdx_rx_e4_top.v)")
    args = ap.parse_args()
    if args.wrapper:
        WRAPPER = (ROOT / args.wrapper).resolve()
    RUNS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    base = CASE / (stamp + "_" + args.tag)
    base.mkdir(parents=True)
    pins = wrapper_pins(WRAPPER)
    total_pins = sum(pins["inputs"].values()) + sum(pins["outputs"].values())
    report = {"case_root": str(base.relative_to(ROOT)), "experiment": args.tag,
              "stage": "INPUT_VERIFICATION", "top": TOP, "case_name": NAME,
              "parameters": {"networks": 2, "rx_vls_per_network": 8, "SKEW_MAX": 100000,
                             "time_width": 64},
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
        (base / "e4_results.json").write_text(json.dumps(report, indent=2))
        (OUT / "rx_e4_latest.json").write_text(json.dumps(report, indent=2))
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
                               and p.name != "e4_results.json"}
        save()


if __name__ == "__main__":
    main()
