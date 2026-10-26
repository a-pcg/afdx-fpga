"""E3 route + timing evidence for the narrowed real-pin RX top.

Continues an already packed E3 snapshot. Each requested clock period gets its
own isolated run directory, copies the accepted synth checkpoint, loads the
same vendor SDC parser hook used by the accepted counter probe, runs the
vendor run_route flow and keeps every raw log/report. Slack/WNS/TNS are read
from the produced reports, never inferred from a process exit code.
"""
from pathlib import Path
import json, hashlib, re, shutil, subprocess, datetime, time, argparse, sqlite3

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "reports/elinx_integration"
SHELL = Path(r"C:/Program Files (x86)/eHiWay/eLinx3.0/bin/shell")
NAME = "native_rx_e3"
TOP = "afdx_rx_e3_top"


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def read_text(p):
    return Path(p).read_text(errors="replace") if Path(p).is_file() else None


def tan_tables(path):
    """The vendor timing report is a SQLite database (print_timing_report)."""
    if not Path(path).is_file():
        return None
    con = sqlite3.connect(str(path))
    con.row_factory = sqlite3.Row
    wanted = {
        "timing_summary": "elinx_timing_summary",
        "intra_clock_summary": "elinx_intra_clock_summary",
        "inter_clock_summary": "elinx_inter_clock_summary",
        "unconstrained_clock_summary": "elinx_unconstrain_clock_summary",
        "user_ignored_clock_summary": "elinx_user_ignored_clock_summary",
        "timer_setting": "elinx_timer_setting",
        "not_delay_io": "elinx_not_delay_Io",
        "intra_longest_sync": "elinx_intra_clock_longest_sync_path_report",
        "unconstrained_longest": "elinx_unconstrain_longest_path_report",
    }
    out = {}
    try:
        for key, table in wanted.items():
            exists = con.execute("select count(*) from sqlite_master where type=%s and name=?" % "'table'",
                                 (table,)).fetchone()[0]
            out[key] = [dict(r) for r in con.execute("select * from " + table)] if exists else None
    finally:
        con.close()
    return out


def parse_slack(text):
    out = {"raw": text}
    for name, fmax, sync, async_ in re.findall(
            r"clock name:(\S+) Fmax:([\d.]+) sync_slack:([-\d.]+) async_slack:(\S+)", text):
        out.setdefault("clocks", []).append({"name": name, "fmax_mhz": float(fmax),
                                             "sync_slack_ns": float(sync), "async_slack": async_})
    m = re.search(r"all_path_min_slack:([-\d.]+)\s+all_route_min_slack:([-\d.]+)", text)
    if m:
        out["all_path_min_slack_ns"] = float(m.group(1))
        out["all_route_min_slack_ns"] = float(m.group(2))
    return out


def parse_route_status(text):
    stats = {}
    for label, value in re.findall(r"^# of ([^.:]+?)[. ]*:\s*(\d+):?\s*$", text, re.M):
        stats[label.strip()] = int(value)
    return {"raw": text, "stats": stats}


def parse_placed(text):
    out = {"raw": text}
    m = re.search(r"Total Pins\s*:\s*(\d+)\s*/\s*(\d+)", text)
    if m:
        out["pins"] = int(m.group(1))
        out["pin_locations"] = int(m.group(2))
    m = re.search(r"Min Slack\s*:\s*(-?\d+)", text)
    if m:
        out["min_slack_ps"] = int(m.group(1))
    m = re.search(r"EndPoints\s*:\s*(\d+)", text)
    if m:
        out["endpoints"] = int(m.group(1))
    for key, pattern in [("lut", r"Total LUTs\s*:\s*(\d+)"), ("ff", r"Total FFs\s*:\s*(\d+)"),
                         ("clb", r"Total CLBs\s*:\s*(\d+)"), ("memory", r"Total Memorys\s*:\s*(\d+)")]:
        m = re.search(pattern, text)
        if m:
            out[key] = int(m.group(1))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--period", type=float, help="single period; default runs 10 and 0.1 ns")
    ap.add_argument("--timeout", type=int, default=7200, help="seconds per implementation round")
    args = ap.parse_args()
    report = json.loads((OUT / "rx_e3_latest.json").read_text())
    base = ROOT / report["case_root"]
    project = base / NAME
    synth = project / (NAME + ".runs/synth_1")
    if not report.get("pack_verified"):
        raise RuntimeError("Cannot route an unverified pack")
    for rel, digest in report.get("artifacts", {}).items():
        p = base / rel
        if p.is_file() and sha(p) != digest["sha256"]:
            raise RuntimeError("Existing artifact changed: " + rel)
    for fname, digest in report["source_snapshot"].items():
        if sha(project / "src" / fname) != digest["sha256"]:
            raise RuntimeError("Snapshot changed: " + fname)
    if sha(ROOT / report["wrapper"]) != report["wrapper_sha256"]:
        raise RuntimeError("Wrapper source changed since pack")

    dest = base / ("route_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
    dest.mkdir()
    results = {"stage": "RUNNING", "original_case": report["case_root"],
               "target": report["native_target"], "top": TOP, "case_name": NAME,
               "input_hashes": report["input_hashes"],
               "source_checkpoint_sha256": sha(synth / (NAME + "_eHiChip6.ecp")),
               "io_budget_provided": False, "system_timing_verified": False,
               "records": [],
               "scripts": {str(p.relative_to(ROOT)): sha(p) for p in
                           [Path(__file__), ROOT / "scripts/elinx_integration/run_rx_e3.py"]}}

    def save():
        (dest / "route_results.json").write_text(json.dumps(results, indent=2))
        (OUT / "rx_e3_route_latest.json").write_text(json.dumps(results, indent=2))

    def period_label(period):
        period = float(period)
        return "p" + (str(int(period)) if period.is_integer() else str(period).replace(".", "p"))
    periods = [(args.period, period_label(args.period))] if args.period is not None \
        else [(10.0, "p10"), (0.1, "p0p1")]
    for period, label in periods:
        p = dest / label
        run = p / (NAME + ".runs")
        imp = run / "imple_1"
        shutil.copytree(synth, run / "synth_1")
        imp.mkdir()
        shutil.copyfile(project / "log4cpp.property", p / "log4cpp.property")
        sdc = p / "clock.sdc"
        sdc.write_text("create_clock -name clk -period " + repr(period) + " [get_ports {clk}]\n")
        (imp / (NAME + ".run.psf")).write_text(
            "source {" + (SHELL / "Infrastructure/psk_extern/sdc2.0.1/sdcparser_call.tcl").as_posix() + "}\n" +
            "read_sdc {" + sdc.as_posix() + "}\n")
        script = p / "run.tcl"
        script.write_text(
            "source {" + (SHELL / "bin/run_route.tcl").as_posix() + "}\n" +
            "run_route {" + p.as_posix() + "} " + NAME + " " + TOP +
            " eHiChip6 EQ6HL130 CSG484_H synth_1 imple_1\nexit\n")
        rec = {"period_ns": period, "directory": str(p.relative_to(ROOT)), "status": "RUNNING"}
        results["records"].append(rec)
        save()
        argv = [str(SHELL / "bin/Implementation.exe"), "-f", str(script), "-silence"]
        started = time.monotonic()
        with (p / "console.log").open("wb") as stream:
            proc = subprocess.Popen(argv, cwd=SHELL / "bin", stdout=stream, stderr=subprocess.STDOUT)
            rec["pid"] = proc.pid
            rec["argv"] = argv
            rec["cwd"] = str(SHELL / "bin")
            save()
            try:
                proc.wait(timeout=args.timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
                rec["timeout"] = True
        rec["seconds"] = round(time.monotonic() - started, 3)
        rec["returncode"] = proc.returncode
        log = read_text(p / "console.log") or ""
        rec["constraint_parse_observed"] = ("Command: create_clock" in log
                                           and "No errors found." in log
                                           and "-period = " in log)
        rec["constraint_period_line"] = [l.strip() for l in log.splitlines() if "Argument -period" in l]
        rec["completion_marker"] = "All was well!" in log
        edi = read_text(imp / (NAME + ".edi")) or ""
        rec["native_diagnostics"] = [l for l in edi.splitlines()
                                     if re.search(r"\[ERROR\]|\[CRIT\]|unconstrain|no.clock|exceeds", l, re.I)]
        rec["drc_or_flow_error"] = [l for l in log.splitlines()
                                    if re.search(r"drc fail|Error:|constraint file error|Exiting", l, re.I)]
        rec["wns_lines"] = [l for l in log.splitlines() if "WNS:" in l]
        rec["route_status"] = parse_route_status(read_text(imp / (NAME + "_route_status.rpt")) or "")
        slack_text = read_text(imp / (NAME + ".slack.rpt"))
        rec["slack_report"] = parse_slack(slack_text) if slack_text else None
        rec["utilization_placed"] = parse_placed(read_text(imp / (NAME + "_utilization_placed.rpt")) or "")
        rec["timing_tables"] = tan_tables(imp / (NAME + ".tan.pb"))
        clk_attr = read_text(imp / (NAME + "_clk_attribute.txt"))
        rec["clock_attribute"] = clk_attr
        cp = imp / (NAME + "_eHiChip6_EQ6HL130.ecp")
        rec["checkpoint_sha256"] = sha(cp) if cp.is_file() else None
        rec["files"] = {str(f.relative_to(p)): {"sha256": sha(f), "bytes": f.stat().st_size}
                        for f in p.rglob("*") if f.is_file()}
        slack = rec["slack_report"] or {}
        rec["route_completed"] = bool(rec["completion_marker"] and rec["checkpoint_sha256"]
                                     and slack.get("clocks"))
        rec["status"] = ("ROUTE_COMPLETE" if rec["route_completed"]
                         else ("TIMEOUT" if rec.get("timeout") else "FAIL_OR_INCOMPLETE"))
        save()
        print(json.dumps({k: v for k, v in rec.items()
                          if k not in ["files", "argv", "timing_tables", "route_status",
                                       "utilization_placed", "slack_report", "clock_attribute"]}), flush=True)
        if rec["status"] != "ROUTE_COMPLETE":
            results["stage"] = "ROUTE_FAILED_OR_INCOMPLETE"
            save()
            return 1
    results["stage"] = "ROUTE_RUNS_COMPLETE_AUDIT_PENDING"
    save()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
