"""E4 route + timing evidence for the R3 timing-converged dual RX.

Consumes a packed E4 experiment (reports/rx_r3_runs/<tag>_pack.json), copies the
synth checkpoint into an isolated run directory per clock period, runs the same
vendor route flow as E3, and reads WNS/TNS/Fmax from the produced vendor reports.
Additionally decodes the vendor timing database (native_rx_e4.tan.pb, a SQLite
database written by the vendor print_timing_report) into a worst-path table so
every experiment records which cells limit it.
"""
from pathlib import Path
import json, hashlib, re, shutil, subprocess, datetime, time, argparse, sqlite3

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "reports/elinx_integration"
RUNS = ROOT / "reports/rx_r3_runs"
SHELL = Path(r"C:/Program Files (x86)/eHiWay/eLinx3.0/bin/shell")
NAME = "native_rx_e4"
TOP = "afdx_rx_e4_top"


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def read_text(p):
    return Path(p).read_text(errors="replace") if Path(p).is_file() else None


def tan_summary(path):
    if not Path(path).is_file():
        return None
    con = sqlite3.connect(str(path))
    con.row_factory = sqlite3.Row
    out = {}
    try:
        out["timing_summary"] = [dict(r) for r in con.execute("select * from elinx_timing_summary")]
        out["intra_clock_summary"] = [dict(r) for r in con.execute("select * from elinx_intra_clock_summary")]
        out["unconstrained"] = [dict(r) for r in con.execute("select * from elinx_unconstrain_clock_summary")]
    except sqlite3.Error as error:
        out["error"] = str(error)
    finally:
        con.close()
    return out


def tan_paths(path, limit=20, detail=3):
    if not Path(path).is_file():
        return None
    con = sqlite3.connect(str(path))
    con.row_factory = sqlite3.Row
    out = {"worst_paths": [], "node_detail": {}}
    try:
        rows = con.execute("select * from elinx_intra_clock_longest_sync_path_report "
                           "order by cast(slack as real) limit ?", (limit,)).fetchall()
        for r in rows:
            d = dict(r)
            out["worst_paths"].append({k: d.get(k) for k in
                                       ("path_id", "slack", "levels", "route", "high_fanout",
                                        "from_cell_name", "to_cell_name", "total_delay",
                                        "logic_delay", "net_delay", "clock_skew")})
        for r in rows[:detail]:
            pid = r["path_id"]
            nodes = []
            for n in con.execute("select * from elinx_data_path_node_detail where path_id=? "
                                 "order by path_node_detail_id", (pid,)):
                d = dict(n)
                nodes.append({k: d.get(k) for k in
                              ("delay_type", "node_type", "incr_delay", "path_delay",
                               "location", "resource")})
            out["node_detail"][str(pid)] = nodes
    except sqlite3.Error as error:
        out["error"] = str(error)
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
    for key, pattern in [("pins", r"Total Pins\s*:\s*(\d+)\s*/\s*(\d+)"),
                         ("min_slack_ps", r"Min Slack\s*:\s*(-?\d+)"),
                         ("endpoints", r"EndPoints\s*:\s*(\d+)"),
                         ("lut", r"Total LUTs\s*:\s*(\d+)"),
                         ("ff", r"Total FFs\s*:\s*(\d+)"),
                         ("clb", r"Total CLBs\s*:\s*(\d+)"),
                         ("memory", r"Total Memorys\s*:\s*(\d+)")]:
        m = re.search(pattern, text)
        if m:
            if key == "pins":
                out["pins"] = int(m.group(1))
                out["pin_locations"] = int(m.group(2))
            else:
                out[key] = int(m.group(1))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True, help="pack experiment label")
    ap.add_argument("--period", type=float, default=10.0)
    ap.add_argument("--timeout", type=int, default=3600)
    args = ap.parse_args()
    pack_report_path = RUNS / (args.tag + "_pack.json")
    report = json.loads(pack_report_path.read_text())
    if report.get("failure") or not report.get("pack_verified"):
        raise RuntimeError("Cannot route an unverified pack: " + str(pack_report_path))
    base = ROOT / report["case_root"]
    project = base / NAME
    synth = project / (NAME + ".runs/synth_1")
    wrapper_name = Path(report["wrapper"]).name
    if sha(project / "src" / wrapper_name) != report["source_snapshot"][wrapper_name]["sha256"]:
        raise RuntimeError("Snapshot changed: " + wrapper_name)
    for rel, digest in report.get("artifacts", {}).items():
        p = base / rel
        if p.is_file() and sha(p) != digest["sha256"]:
            raise RuntimeError("Existing artifact changed: " + rel)

    label = "p" + (str(int(args.period)) if float(args.period).is_integer()
                   else str(args.period).replace(".", "p"))
    dest = base / ("route_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
    p = dest / label
    run = p / (NAME + ".runs")
    imp = run / "imple_1"
    results = {"stage": "RUNNING", "experiment": args.tag, "period_ns": args.period,
               "original_case": report["case_root"], "target": report["native_target"],
               "top": TOP, "case_name": NAME,
               "input_hashes": report["input_hashes"],
               "changed_vs_e3": report.get("changed_vs_e3"),
               "source_checkpoint_sha256": sha(synth / (NAME + "_eHiChip6.ecp")),
               "io_budget_provided": False, "system_timing_verified": False}
    out_path = RUNS / (args.tag + "_route_" + label + ".json")

    def save():
        (dest / "route_results.json").write_text(json.dumps(results, indent=2))
        (OUT / ("rx_e4_route_" + args.tag + "_" + label + ".json")).write_text(json.dumps(results, indent=2))
        out_path.write_text(json.dumps(results, indent=2))

    try:
        p.mkdir(parents=True)
        shutil.copytree(synth, run / "synth_1")
        imp.mkdir()
        shutil.copyfile(project / "log4cpp.property", p / "log4cpp.property")
        sdc = p / "clock.sdc"
        sdc.write_text("create_clock -name clk -period " + repr(args.period) + " [get_ports {clk}]\n")
        (imp / (NAME + ".run.psf")).write_text(
            "source {" + (SHELL / "Infrastructure/psk_extern/sdc2.0.1/sdcparser_call.tcl").as_posix() + "}\n" +
            "read_sdc {" + sdc.as_posix() + "}\n")
        script = p / "run.tcl"
        script.write_text(
            "source {" + (SHELL / "bin/run_route.tcl").as_posix() + "}\n" +
            "run_route {" + p.as_posix() + "} " + NAME + " " + TOP +
            " eHiChip6 EQ6HL130 CSG484_H synth_1 imple_1\nexit\n")
        argv = [str(SHELL / "bin/Implementation.exe"), "-f", str(script), "-silence"]
        started = time.monotonic()
        with (p / "console.log").open("wb") as stream:
            proc = subprocess.Popen(argv, cwd=SHELL / "bin", stdout=stream, stderr=subprocess.STDOUT)
            results["pid"] = proc.pid
            results["argv"] = argv
            save()
            try:
                proc.wait(timeout=args.timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
                results["timeout"] = True
        results["seconds"] = round(time.monotonic() - started, 3)
        results["returncode"] = proc.returncode
        log = read_text(p / "console.log") or ""
        results["constraint_parse_observed"] = ("Command: create_clock" in log
                                               and "No errors found." in log
                                               and "-period = " in log)
        results["constraint_period_line"] = [l.strip() for l in log.splitlines() if "Argument -period" in l]
        results["completion_marker"] = "All was well!" in log
        results["wns_lines"] = [l for l in log.splitlines() if "WNS:" in l]
        results["route_status"] = parse_route_status(read_text(imp / (NAME + "_route_status.rpt")) or "")
        slack_text = read_text(imp / (NAME + ".slack.rpt"))
        results["slack_report"] = parse_slack(slack_text) if slack_text else None
        results["utilization_placed"] = parse_placed(read_text(imp / (NAME + "_utilization_placed.rpt")) or "")
        results["timing_summary"] = tan_summary(imp / (NAME + ".tan.pb"))
        results["paths"] = tan_paths(imp / (NAME + ".tan.pb"))
        cp = imp / (NAME + "_eHiChip6_EQ6HL130.ecp")
        results["checkpoint_sha256"] = sha(cp) if cp.is_file() else None
        results["files"] = {str(f.relative_to(p)): {"sha256": sha(f), "bytes": f.stat().st_size}
                            for f in p.rglob("*") if f.is_file()}
        slack = results["slack_report"] or {}
        results["route_completed"] = bool(results["completion_marker"] and results["checkpoint_sha256"]
                                          and slack.get("clocks"))
        results["status"] = "ROUTE_COMPLETE" if results["route_completed"] else (
            "TIMEOUT" if results.get("timeout") else "FAIL_OR_INCOMPLETE")
        save()
        clk = (slack.get("clocks") or [{}])[0]
        summary = {"tag": args.tag, "period_ns": args.period, "status": results["status"],
                   "fmax_mhz": clk.get("fmax_mhz"), "wns_ns": clk.get("sync_slack_ns"),
                   "seconds": results["seconds"], "dir": str(p.relative_to(ROOT))}
        (RUNS / (args.tag + "_route_" + label + "_summary.json")).write_text(json.dumps(summary, indent=2))
        print(json.dumps(summary), flush=True)
        return 0
    except Exception as error:
        results["failure"] = str(error)
        results["status"] = "FAILED"
        save()
        raise


if __name__ == "__main__":
    raise SystemExit(main())
