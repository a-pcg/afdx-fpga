"""Resume the native pack stage of an interrupted E4 experiment.

The E4 pack script runs Classic synthesis, VQM export and the vendor native pack
in order. If the process is interrupted after VQM export, the Classic/VQM
products are still valid and only the vendor pack has to be replayed. This
script preserves the interrupted console log, checks the recorded product
hashes, replays only the native pack, and then re-runs the same acceptance
checks as the fresh path.

Usage:
  python scripts/elinx_integration/resume_rx_e4_pack.py --tag r3_reg_cross
"""
from pathlib import Path
import importlib.util, json, shutil, argparse

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("e4", Path(__file__).with_name("run_rx_e4.py"))
e4 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(e4)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True)
    args = ap.parse_args()
    pack_path = e4.RUNS / (args.tag + "_pack.json")
    report = json.loads(pack_path.read_text())
    base = ROOT / report["case_root"]
    project = base / e4.NAME
    run = project / (e4.NAME + ".runs/synth_1")
    if not (project / "classic.log").is_file() or not (run / (e4.NAME + ".vqm")).is_file():
        raise RuntimeError("Classic/VQM products missing; rerun the full pack instead")
    vqm_sha = e4.sha(run / (e4.NAME + ".vqm"))
    if report.get("vqm_sha256") and report["vqm_sha256"] != vqm_sha:
        raise RuntimeError("VQM changed since Classic export")
    report["vqm_sha256"] = vqm_sha
    # Preserve the interrupted vendor log; never delete or overwrite history.
    console = project / "console.log"
    if console.is_file() and console.stat().st_size:
        evidence = base / "pack_interrupted_evidence"
        evidence.mkdir(exist_ok=True)
        shutil.copy2(console, evidence / ("console_attempt1.log"))
        report.setdefault("pack_attempts", []).append({
            "attempt": 1, "interrupted": True,
            "console_sha256": e4.sha(console),
            "console_bytes": console.stat().st_size,
            "preserved": str((evidence / "console_attempt1.log").relative_to(ROOT)),
            "reason": "parent python process was interrupted during NATIVE_PACK; "
                      "Classic synthesis and VQM export were already complete"})
    report["stage"] = "NATIVE_PACK_RETRY"
    report.pop("failure", None)
    pack_path.write_text(json.dumps(report, indent=2))
    (e4.OUT / "rx_e4_latest.json").write_text(json.dumps(report, indent=2))

    n = importlib.util.spec_from_file_location("native_readonly", ROOT / "scripts/m0_native/run.py")
    native = importlib.util.module_from_spec(n)
    n.loader.exec_module(native)
    step = native.execute([e4.SHELL / "bin/Implementation.exe", "-f",
                           project / "run_native_pack.tcl", "-silence"],
                          e4.SHELL / "bin", project / "console.log", timeout=3600)
    report.setdefault("steps", []).append(step)
    pins = e4.wrapper_pins(e4.WRAPPER)
    total_pins = sum(pins["inputs"].values()) + sum(pins["outputs"].values())
    e4.verify_pack(report, project, run, total_pins)
    report["pack_attempts"].append({"attempt": 2, "interrupted": False, "repacked": True})
    report["artifacts"] = {str(p.relative_to(base)): {"sha256": e4.sha(p), "bytes": p.stat().st_size}
                           for p in base.rglob("*") if p.is_file()
                           and p.name not in ["e4_results.json"]}
    (base / "e4_results.json").write_text(json.dumps(report, indent=2))
    (e4.OUT / "rx_e4_latest.json").write_text(json.dumps(report, indent=2))
    pack_path.write_text(json.dumps(report, indent=2))
    print("PACK_REPACKED_AND_VERIFIED: " + str(base), flush=True)


if __name__ == "__main__":
    main()
