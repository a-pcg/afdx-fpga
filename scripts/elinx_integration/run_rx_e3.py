"""E3: real-pin top for the frozen dual-network RX. Classic/VQM/native pack only.

The E2 case exposed every internal signal of afdx_rx_dual as a top-level port
(390 IO) and was stopped by the pre-place IO DRC (free locations: 339). This
case wraps the same frozen RTL in a real board-level boundary and keeps all
synthesis/route evidence in one place. It never edits RX RTL or the vendor
installation.

--resume <case_root> re-verifies an interrupted run against its own logs and
products (no vendor tool is launched again); the accepted pack is only marked
verified after the Classic summary, VQM, native report, console markers and
checkpoint all agree.
"""
from pathlib import Path
import importlib.util, hashlib, json, datetime, re, subprocess, os, argparse, shutil
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "reports/elinx_integration"
CASE = ROOT / "elinx/integration/rx_e3"
WRAPPER = CASE / "src/afdx_rx_e3_top.v"
NAME = "native_rx_e3"
TOP = "afdx_rx_e3_top"
FREE_IO_LOCATIONS = 339
INSTALL = Path(r"C:/Program Files (x86)/eHiWay/eLinx3.0")
SHELL = INSTALL / "bin/shell"
CLASSIC = INSTALL / "bin/Passkey/bin"
VERILATOR = INSTALL / "verilator/verilator.exe"
VER_INC = ROOT / "third_party/verilator-5.006/include"

# Frozen RX RTL closure, expected SHA256 recomputed by E2 and rechecked here.
EXPECTED = {
 "rtl/rx/afdx_rx_dual.v": "ec71defb922c302eafb8b129dba91c679943a28d1c83623786cfa194eb2e9d6b",
 "rtl/rx/afdx_rx_merge.v": "746bfdb4942ef03aa58b8ba059de47cd7aa774b2fdf45efbc9cc20a83d056ca2",
 "rtl/rx/afdx_integrity_checker.v": "fbdc0f592951ea187337a570dc41f43b227216b6d6d7429af173d6092176c765",
 "rtl/eth_mac_rx.v": "0d575142b86fd273dcc06008f81a705d31c269575e2d270e8f9ae7197ac0f101",
 "rtl/eth_crc32_byte.v": "2faf48bbcf1b0872eadbdeebf1d6233bc373eb7022ae21d50f98c3c9d2729736",
 "rtl/afdx_udp_rx.v": "cba9cf7552c533c78813cf3632c059b24bf5a149943cabf7baab5b7432265cb1",
}
EXPECTED_MODULES = ["afdx_rx_dual", "afdx_rx_merge", "afdx_integrity_checker",
                   "eth_mac_rx", "eth_crc32_byte", "afdx_udp_rx", "afdx_rx_e3_top"]


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def strip_comments(text):
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"//[^\n]*", "", text)


def module_closure(texts):
    """Module graph from defined module names and instantiation references."""
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
    """Parse the real board boundary from the wrapper port list."""
    text = strip_comments(Path(path).read_text())
    header = text.split(");", 1)[0]
    pins = {"inputs": {}, "outputs": {}}
    for direction, hi, lo, name in re.findall(
            r"\b(input|output)\s+wire\s*(?:\[\s*(\d+)\s*:\s*(\d+)\s*\])?\s*(\w+)", header):
        w = abs(int(hi) - int(lo)) + 1 if hi else 1
        pins[direction + "s"][name] = w
    return pins


def verify_sources(report, total_pins):
    frozen = {rel: sha(ROOT / rel) for rel in EXPECTED}
    for rel, want in EXPECTED.items():
        if frozen[rel] != want:
            raise RuntimeError("Frozen RX source drift: " + rel)
    if total_pins > FREE_IO_LOCATIONS * 0.75:
        raise RuntimeError("Narrowed top still uses too many IO: %d" % total_pins)
    report["input_hashes"] = frozen
    report["wrapper_sha256"] = sha(WRAPPER)
    refs = ["docs/coordination/elinx_handoff.md", "reports/elinx_integration/rx_e2_latest.json",
            "reports/elinx_integration/rx_e2_audit.json", "reports/rx_dual/inputs.json",
            "scripts/m0_native/run.py", "scripts/elinx_integration/run_rx_e3.py"]
    report["provenance_hashes"] = {k: sha(ROOT / k) for k in refs if (ROOT / k).is_file()}
    texts = {rel: (ROOT / rel).read_text(errors="replace") for rel in EXPECTED}
    texts[str(WRAPPER.relative_to(ROOT))] = WRAPPER.read_text(errors="replace")
    graph = module_closure(texts)
    report["module_dependency_graph"] = graph
    if sorted(graph) != sorted(EXPECTED_MODULES):
        raise RuntimeError("Unexpected module closure: " + repr(sorted(graph)))


def verify_pack(report, project, run, total_pins):
    """The only place that marks the native pack verified."""
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
    vqm_sha = sha(vqm)
    if report.get("vqm_sha256") and report["vqm_sha256"] != vqm_sha:
        raise RuntimeError("VQM changed since export")
    report["vqm_sha256"] = vqm_sha
    native = (run / (NAME + "_utilization_synth.rpt")).read_text(errors="replace")
    console = (project / "console.log").read_text(errors="replace")
    checkpoint = run / (NAME + "_eHiChip6.ecp")
    if "Synthesis Status : Successful" not in native or "pack_gene is 1" not in console \
            or not checkpoint.is_file() or not checkpoint.stat().st_size:
        raise RuntimeError("Pack success evidence missing")
    # A complete vendor pack must also write its completion marker, version
    # checkpoint and cluster netlist. The first E3 attempt was killed by a
    # session interruption right after udm_save_checkpoint, leaving a loadable-
    # looking but incomplete .ecp (no .ver.pb, truncated cluster XML); the route
    # step then rejected it with "ecp file NOT accordance with current version".
    # Those four products are therefore mandatory here.
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
    for suffix in [".tbg.pb", "_netlist.pb"]:
        extra = run / (NAME + suffix)
        if not extra.is_file() or not extra.stat().st_size:
            raise RuntimeError("Native product missing: " + extra.name)
    report["pack_products"] = {"completion_marker": True,
                               "ver_pb_sha256": sha(ver_pb),
                               "cluster_xml_sha256": sha(cluster_xml),
                               "cluster_count": cluster_count,
                               "native_memory_blocks": native_memory}
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
    ap.add_argument("--resume", help="case_root of an interrupted run; verify products only")
    ap.add_argument("--repack", help="case_root whose Classic/VQM products are complete but whose "
                                     "native pack was interrupted; preserve the attempt and repack")
    args = ap.parse_args()
    if args.resume or args.repack:
        base = ROOT / (args.resume or args.repack)
        report = json.loads((base / "e3_results.json").read_text())
        report.pop("failure", None)
        pins = wrapper_pins(WRAPPER)
        total_pins = sum(pins["inputs"].values()) + sum(pins["outputs"].values())
        report["wrapper_pin_breakdown"] = {"inputs": pins["inputs"], "outputs": pins["outputs"],
                                           "input_pins": sum(pins["inputs"].values()),
                                           "output_pins": sum(pins["outputs"].values()),
                                           "total_pins": total_pins}
    else:
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        base = CASE / stamp
        base.mkdir(parents=True)
        pins = wrapper_pins(WRAPPER)
        total_pins = sum(pins["inputs"].values()) + sum(pins["outputs"].values())
        report = {"case_root": str(base.relative_to(ROOT)),
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
        (base / "e3_results.json").write_text(json.dumps(report, indent=2))
        (OUT / "rx_e3_latest.json").write_text(json.dumps(report, indent=2))

    try:
        verify_sources(report, total_pins)
        if args.resume:
            if not (base / "verilator_lint.log").is_file():
                raise RuntimeError("Missing wrapper elaboration log")
            lint_log = (base / "verilator_lint.log").read_text(errors="replace")
            if "%Error" in lint_log:
                raise RuntimeError("Wrapper elaboration log contains errors")
            report["steps"] = [
                {"argv": [str(CLASSIC / "quartus_map.exe"), "--read_settings_files=on",
                          "--write_settings_files=off", NAME], "cwd": str(project),
                 "log": str((project / "classic.log").relative_to(ROOT)),
                 "returncode": None, "recovered_from_interrupted_run": True},
                {"argv": [str(CLASSIC / "quartus_cdb.exe"), NAME,
                          "--vqm=" + (run / (NAME + ".vqm")).as_posix()],
                 "cwd": str(project), "log": str((project / "vqm_export.log").relative_to(ROOT)),
                 "returncode": None, "recovered_from_interrupted_run": True},
                {"argv": [str(SHELL / "bin/Implementation.exe"), "-f",
                          str(project / "run_native_pack.tcl"), "-silence"],
                 "cwd": str(SHELL / "bin"), "log": str((project / "console.log").relative_to(ROOT)),
                 "returncode": None, "recovered_from_interrupted_run": True}]
            report["recovery_note"] = ("Parent python process was interrupted after the vendor "
                                       "pack saved its checkpoint; this run re-verified the "
                                       "existing products instead of re-synthesizing. Step "
                                       "return codes were not captured before the interruption.")
            verify_pack(report, project, run, total_pins)
            if report.get("pack_attempts") and not any(a.get("attempt") == 2
                                                       for a in report["pack_attempts"]):
                report["pack_attempts"].append({"attempt": 2, "interrupted": False,
                                                "completion_marker": True, "repacked": True,
                                                "verified_by": "verified resume of products"})
            save()
            print("PACK_RECOVERED_AND_VERIFIED: " + str(base), flush=True)
            return
        if args.repack:
            # Classic and VQM are complete, but the vendor pack was killed after
            # udm_save_checkpoint: the .ecp exists, yet .ver.pb and the full
            # cluster netlist are missing, so route refuses the checkpoint.
            # Preserve that attempt verbatim, then rerun only the pack stage.
            evidence = base / "pack_attempt1_interrupted"
            evidence.mkdir(exist_ok=True)
            preserved = []
            for src_path in [project / "console.log", project / "vqm_export.log"]:
                if src_path.is_file():
                    shutil.copy2(src_path, evidence / src_path.name)
                    preserved.append(str(src_path.relative_to(ROOT)))
            for name in [NAME + "_eHiChip6.ecp", NAME + "_cluster.xml",
                         NAME + "_utilization_synth.rpt", NAME + ".tbg.pb"]:
                src_path = run / name
                if src_path.is_file():
                    shutil.copy2(src_path, evidence / name)
                    preserved.append(str(src_path.relative_to(ROOT)))
            prior_console = (evidence / "console.log").read_text(errors="replace") \
                if (evidence / "console.log").is_file() else ""
            report.setdefault("pack_attempts", []).append({
                "attempt": 1, "interrupted": True,
                "completion_marker": "All was well!" in prior_console,
                "ver_pb_present": (run / (NAME + ".ver.pb")).is_file(),
                "checkpoint_present": (run / (NAME + "_eHiChip6.ecp")).is_file(),
                "preserved_evidence": preserved,
                "reason": "session interruption killed the pack between udm_save_checkpoint and "
                          "save_pb_ver/udm_write_cluster; the route step rejected the .ecp with "
                          "'ecp file NOT accordance with current version'"})
            report["stage"] = "REPACK"; save()
            spec = importlib.util.spec_from_file_location("native_readonly",
                                                          ROOT / "scripts/m0_native/run.py")
            n = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(n)
            n.BASE = base
            steps = report.get("steps") or []
            steps.append(n.execute([SHELL / "bin/Implementation.exe", "-f",
                                    project / "run_native_pack.tcl", "-silence"],
                                   SHELL / "bin", project / "console.log", timeout=1800))
            report["steps"] = steps
            verify_pack(report, project, run, total_pins)
            report["pack_attempts"].append({"attempt": 2, "interrupted": False,
                                            "completion_marker": True, "repacked": True})
            save()
            print("PACK_REPACKED_AND_VERIFIED: " + str(base), flush=True)
            return
        (base / "freeze.json").write_text(json.dumps(report, indent=2))
        report["stage"] = "WRAPPER_ELABORATION"; save()

        # Verilator elaboration of the wrapper (syntax/connectivity only).
        env = {k.upper(): v for k, v in os.environ.items()}
        env["VERILATOR_ROOT"] = str(VER_INC.parent).replace("\\", "/")
        lint_cmd = [str(VERILATOR), "--lint-only", "--default-language", "1364-2001",
                    "--top-module", TOP, "--Mdir", str(base / "verilator_obj")]
        lint_cmd += [(ROOT / rel).relative_to(ROOT).as_posix() for rel in EXPECTED]
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

        # Classic -> VQM -> native pack, reusing the accepted native helper.
        spec = importlib.util.spec_from_file_location("native_readonly", ROOT / "scripts/m0_native/run.py")
        n = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(n)
        n.BASE = base
        sources = [ROOT / rel for rel in EXPECTED] + [WRAPPER]
        project, run, snapshot = n.prepare(NAME, TOP, sources)
        report["source_snapshot"] = snapshot
        report["stage"] = "CLASSIC_SYNTHESIS"; save()
        steps = []
        steps.append(n.execute([CLASSIC / "quartus_map.exe", "--read_settings_files=on",
                                "--write_settings_files=off", NAME],
                               project, project / "classic.log", timeout=1800))
        report["stage"] = "VQM_EXPORT"; save()
        vqm = run / (NAME + ".vqm")
        steps.append(n.execute([CLASSIC / "quartus_cdb.exe", NAME, "--vqm=" + vqm.as_posix()],
                               project, project / "vqm_export.log", timeout=600))
        report["vqm_sha256"] = sha(vqm)
        report["stage"] = "NATIVE_PACK"; save()
        steps.append(n.execute([SHELL / "bin/Implementation.exe", "-f",
                                project / "run_native_pack.tcl", "-silence"],
                               SHELL / "bin", project / "console.log", timeout=1800))
        report["steps"] = steps
        verify_pack(report, project, run, total_pins)
        save()
        print("PACK_PASS: " + str(base), flush=True)
    except Exception as error:
        report["failure"] = str(error)
        save()
        raise
    finally:
        report["artifacts"] = {str(p.relative_to(base)): {"sha256": sha(p), "bytes": p.stat().st_size}
                               for p in base.rglob("*") if p.is_file()
                               and p.name not in ["e3_results.json", "freeze.json"]}
        save()


if __name__ == "__main__":
    main()
