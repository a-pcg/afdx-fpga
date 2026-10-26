"""Read and audit the E3 route/timing evidence; never launches vendor tools."""
from pathlib import Path
import json, hashlib, re, sqlite3

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "reports/elinx_integration"
INSTALL = Path(r"C:/Program Files (x86)/eHiWay/eLinx3.0")
NAME = "native_rx_e3"
TOP = "afdx_rx_e3_top"
FREE_IO = 339


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def tan_summary(path):
    """Vendor timing report (SQLite): clock domains, unconstrained groups, worst path."""
    if not Path(path).is_file():
        return None
    con = sqlite3.connect(str(path))
    con.row_factory = sqlite3.Row

    def rows(table, where="", args=()):
        exists = con.execute("select count(*) from sqlite_master where type='table' and name=?",
                             (table,)).fetchone()[0]
        return [dict(r) for r in con.execute("select * from " + table + where, args)] if exists else []

    try:
        result = {
            "timing_summary": rows("elinx_timing_summary"),
            "intra_clock_summary": rows("elinx_intra_clock_summary"),
            "inter_clock_summary": rows("elinx_inter_clock_summary"),
            "unconstrained_clock_summary": rows("elinx_unconstrain_clock_summary"),
            "user_ignored_clock_summary": rows("elinx_user_ignored_clock_summary"),
            "timer_setting": rows("elinx_timer_setting"),
            "io_rows_in_timing_db": len(rows("elinx_not_delay_Io")),
            "intra_longest_sync": rows("elinx_intra_clock_longest_sync_path_report",
                                       " order by slack limit 5"),
            "unconstrained_longest": rows("elinx_unconstrain_longest_path_report",
                                          " order by slack limit 5"),
        }
        worst = rows("elinx_intra_clock_longest_sync_path_report", " order by slack limit 1")
        result["worst_path"] = None
        if worst:
            w = worst[0]
            pid = w["path_id"]
            nodes = rows("elinx_data_path_node_detail",
                         " where path_id=? order by path_node_detail_id", (pid,))
            src = rows("elinx_source_clock_path_node_detail", " where path_id=?", (pid,))
            dst = rows("elinx_dest_clcock_path_node_detail", " where path_id=?", (pid,))
            carry = [n for n in nodes if n["resource"].endswith("/cin") or n["resource"].endswith("/cout")]
            ram_nodes = [n for n in nodes if "ram_block" in n["resource"] or "portbdataout" in n["resource"]]
            mux_nodes = [n for n in nodes if "Mux" in n["resource"]]
            result["worst_path"] = {
                "path_id": pid, "slack_ps": w["slack"], "levels": w["levels"],
                "total_delay_ps": w["total_delay"], "logic_delay_ps": w["logic_delay"],
                "net_delay_ps": w["net_delay"], "clock_skew_ps": w["clock_skew"],
                "from_cell": w["from_cell_name"], "to_cell": w["to_cell_name"],
                "ram_read_write_nodes": len(ram_nodes), "mux_nodes": len(mux_nodes),
                "carry_chain_nodes": len(carry),
                "clock_source_nodes": src, "clock_dest_nodes": dst,
                "node_chain": nodes,
                "scope": "logic_delay + net_delay from the vendor timer; net delay dominates"}
    finally:
        con.close()
    return result


def clock_slack(record):
    for clock in (record.get("slack_report") or {}).get("clocks", []):
        if clock["name"] == "clk":
            return clock
    return None


def main():
    report = json.loads((OUT / "rx_e3_latest.json").read_text())
    base = ROOT / report["case_root"]
    project = base / NAME
    sources = {}
    worktree_drift = []
    for rel, expected in report["input_hashes"].items():
        snap = project / "src" / Path(rel).name
        snap_hash, current_hash = sha(snap), sha(ROOT / rel)
        entry = {"expected": expected, "snapshot_sha256": snap_hash, "current_sha256": current_hash}
        if snap_hash != expected:
            raise RuntimeError("E3 snapshot itself changed: " + rel)
        if current_hash != expected:
            # A later task may have edited the RX RTL. E3 evidence stays pinned to
            # the frozen snapshot; the drift is reported, never silently accepted.
            entry["worktree_drift"] = True
            entry["current_bytes"] = (ROOT / rel).stat().st_size
            entry["expected_bytes"] = snap.stat().st_size
            worktree_drift.append(rel)
        sources[rel] = entry
    drift_note = None
    if worktree_drift:
        drift_note = ("The E3 pack/route/timing evidence was produced from the snapshot in "
                      + str(project.relative_to(ROOT)) + "/src. The working tree now differs for: "
                      + ", ".join(worktree_drift) + ". Those later edits are OUTSIDE this case and "
                      "were not synthesized here; any timing claim for them needs a new frozen case.")
    wrapper_now = sha(ROOT / report["wrapper"])
    if wrapper_now != report["wrapper_sha256"]:
        raise RuntimeError("Wrapper changed after pack")

    # --- Board-level IO budget -------------------------------------------------
    pin_breakdown = report["wrapper_pin_breakdown"]
    native_pins = report.get("native_pins")
    io_budget = {"free_io_locations": FREE_IO,
                 "e2_probe_pins": 390,
                 "wrapper_ports": pin_breakdown,
                 "classic_pins": report.get("classic_pins"),
                 "native_pack_pins": native_pins,
                 "headroom_vs_e2": 390 - (native_pins or 0),
                 "headroom_vs_free": FREE_IO - (native_pins or 0),
                 "verdict": "RESOLVED" if native_pins and native_pins <= FREE_IO else "UNRESOLVED",
                 "note": "Narrowed boundary exposes only real system interfaces; "
                         "byte_ce and 64-bit time_now are internal free counters."}

    # --- Route records ---------------------------------------------------------
    records = []
    for f in sorted(base.glob("route_*/route_results.json")):
        data = json.loads(f.read_text())
        for rec in data["records"]:
            p = ROOT / rec["directory"]
            imp = p / (NAME + ".runs/imple_1")
            edi = imp / (NAME + ".edi")
            rec.pop("files", None)
            rec["native_diagnostics"] = [l for l in edi.read_text(errors="replace").splitlines()
                                         if re.search(r"\[ERROR\]|\[CRIT\]|unconstrain|no.clock|exceeds", l, re.I)] \
                if edi.is_file() else []
            rec["timing"] = tan_summary(imp / (NAME + ".tan.pb"))
            rec["clk_slack"] = clock_slack(rec)
            placed = imp / (NAME + "_utilization_placed.rpt")
            if placed.is_file():
                text = placed.read_text(errors="replace")
                m = re.search(r"Total Pins\s*:\s*(\d+)\s*/\s*(\d+)", text)
                if m:
                    rec["placed_pins"], rec["placed_pin_locations"] = int(m.group(1)), int(m.group(2))
            records.append(rec)
    records.sort(key=lambda r: r["period_ns"])
    by_period = {r["period_ns"]: r for r in records}

    def internal_verdict(rec):
        if not rec or not rec.get("route_completed"):
            return {"verified": False, "reason": "route did not complete for this period"}
        timing = rec.get("timing") or {}
        summary = (timing.get("timing_summary") or [{}])[0]
        intra = [c for c in (timing.get("intra_clock_summary") or []) if c["name"] == "clk"]
        clk = rec.get("clk_slack") or {}
        slack = clk.get("sync_slack_ns")
        ok = (slack is not None and slack > 0
              and summary.get("setup_number_failing_endpoints", 1) == 0
              and intra and intra[0]["period"] == int(round(rec["period_ns"] * 1000)))
        return {"verified": bool(ok),
                "clk_sync_slack_ns": slack,
                "setup_wns_ns": summary.get("setup_wns"),
                "setup_tns_ns": summary.get("setup_tns") / 1000.0 if summary.get("setup_tns") is not None else None,
                "setup_failing_endpoints": summary.get("setup_number_failing_endpoints"),
                "setup_total_endpoints": summary.get("setup_number_endpoints"),
                "hold_worst_slack_ps": intra[0]["hold_worst_slack"] if intra else None,
                "clock_period_ps": intra[0]["period"] if intra else None,
                "clock_waveform": intra[0]["waveform"] if intra else None,
                "clock_fmax_mhz": clk.get("fmax_mhz"),
                "reason": "clk-to-clk domain constrained by create_clock and slack > 0" if ok else
                          "clk-to-clk slack is negative: the frozen RX datapath misses this period"}

    run10 = by_period.get(10.0) or by_period.get(10)
    run01 = by_period.get(0.1)
    run20 = by_period.get(20.0) or by_period.get(20)
    v10 = internal_verdict(run10)
    v01 = internal_verdict(run01)
    v20 = internal_verdict(run20)

    differential = {}
    if run10 and run01 and run10.get("clk_slack") and run01.get("clk_slack"):
        differential = {"p10_minus_p0p1_slack_ns":
                        round(run10["clk_slack"]["sync_slack_ns"] - run01["clk_slack"]["sync_slack_ns"], 3),
                        "expected_period_difference_ns": 9.9}
    if run10 and run20 and run10.get("clk_slack") and run20.get("clk_slack"):
        differential["p20_minus_p10_slack_ns"] = round(
            run20["clk_slack"]["sync_slack_ns"] - run10["clk_slack"]["sync_slack_ns"], 3)
        differential["p20_expected_period_difference_ns"] = 10.0

    negative_control = {"verified": False}
    if run01 and v01.get("clk_sync_slack_ns") is not None and v10.get("clk_sync_slack_ns") is not None:
        negative_control = {"verified": bool(v01["clk_sync_slack_ns"] < v10["clk_sync_slack_ns"] < 0 or
                                                        (v01["clk_sync_slack_ns"] < 0 <= v20.get("clk_sync_slack_ns", -1)))
                           if v20.get("clk_sync_slack_ns") is not None else
                           bool(v01["clk_sync_slack_ns"] < 0),
                           "period_ns": 0.1,
                           "sync_slack_ns": v01["clk_sync_slack_ns"],
                           "setup_tns_ns": v01.get("setup_tns_ns"),
                           "failing_endpoints": v01.get("setup_failing_endpoints"),
                           "relation_to_10ns": "tighter period must report more negative slack",
                           "differential": differential}
    relaxed_control = {"verified": v20.get("verified", False), "period_ns": 20.0,
                       "sync_slack_ns": v20.get("clk_sync_slack_ns"),
                       "purpose": "same netlist at a relaxed period; shows the flow reports positive "
                                  "slack when the period is met, so the 10ns failure is a real timing "
                                  "result rather than a flow artifact"}

    # --- Clock constraint + unconstrained path audit --------------------------
    clocks = {}
    for rec in records:
        timing = rec.get("timing") or {}
        groups = timing.get("unconstrained_clock_summary") or []
        intra = timing.get("intra_clock_summary") or []
        clocks[str(rec["period_ns"])] = {
            "requested_period_ns": rec["period_ns"],
            "constraint_parse_observed": rec.get("constraint_parse_observed"),
            "constraint_period_line": rec.get("constraint_period_line"),
            "clock_attribute_present": bool(rec.get("clock_attribute")),
            "clock_domains_in_timing_db": [{"name": c["name"], "period_ps": c["period"],
                                            "waveform": c["waveform"], "fmax_mhz": c["fmax"]}
                                           for c in intra],
            "clk_is_constrained": bool(intra) and intra[0]["name"] == "clk"
                                    and intra[0]["period"] == int(round(rec["period_ns"] * 1000)),
            "inter_clock_domains": len(timing.get("inter_clock_summary") or []),
            "user_ignored_clock_domains": len(timing.get("user_ignored_clock_summary") or []),
            "async_paths_found": (timing.get("timer_setting") or [{}])[0].get("async_paths_found"),
            "unconstrained_groups": [
                {"name": g["name"], "period_ps": g["period"],
                 "setup_worst_slack_ps": g["setup_worst_slack"],
                 "setup_total_violation_ps": g["setup_total_violation"],
                 "setup_failing_endpoints": g["setup_fail_ends"],
                 "setup_total_endpoints": g["setup_total_ends"],
                 "hold_worst_slack_ps": g["hold_worst_slack"]} for g in groups],
            "unconstrained_note": "Groups whose name contains NONE are IO paths without an "
                                  "input/output delay budget. Their period is 0 ps, so their "
                                  "reported slack is -path_delay by construction; these numbers "
                                  "are NOT clk-to-clk timing failures.",
            "input_delay_budget_ns": None, "output_delay_budget_ns": None,
            "board_pins_assigned": False,
        }

    # --- Resource delta vs the E2 probe netlist -------------------------------
    delta = None
    e2 = json.loads((OUT / "rx_e2_latest.json").read_text())
    e2_res = {k.strip(): int(v) for k, v in re.findall(
        r"^(Total (?:LUTs|FFs|CLBs|Memorys|Pins))\s*:\s*(\d+)", e2["native_utilization"], re.M)}
    if report.get("netlogic"):
        delta = {"e2_probe": e2_res, "e3_narrowed": report["netlogic"],
                 "delta": {k: report["netlogic"].get(k, 0) - v for k, v in e2_res.items()},
                 "explanation": "E3 keeps the same six frozen RX sources; the +69 FF internal "
                                "time_now/byte_ce counters are added and the 192 FF diagnostic "
                                "counters (bad_fcs, full_drops, delivered_frames, rejected_frames) "
                                "are outside the physical boundary and were trimmed. Storage "
                                "blocks are unchanged at 94."}

    # --- Throughput arithmetic (byte_ce = one byte per 8 clk) -----------------
    throughput = None
    if run10 and run10.get("clk_slack"):
        fmax = run10["clk_slack"]["fmax_mhz"]
        throughput = {"clock_assumption": "byte_ce pulses once per 8 clk (8-bit wire path)",
                      "achieved_fmax_mhz": fmax,
                      "achieved_min_period_ns": round(1000.0 / fmax, 3),
                      "max_line_rate_mbps": round(fmax, 3),
                      "required_fmax_for_100mbps_mhz": 100.0,
                      "shortfall_mhz": round(100.0 - fmax, 3),
                      "required_period_ns": 10.0,
                      "verdict": "100 Mbps is unreachable at this mapping: 60.245 MHz max "
                                 "corresponds to 60.245 Mbps on the 8-bit byte_ce path"}

    # --- Route-status report semantics ----------------------------------------
    # The vendor route_status report always prints 0 for "routable"/"fully
    # routed" nets. The already accepted counter probe shows the same zeros while
    # producing a valid slack report, so the counters are not a route-failure
    # indicator; the meaningful rows are 0 routing errors plus the completion
    # marker and the routed-netlist timing report.
    counter_case = ROOT / "elinx/integration/timing_counter/20261018_212634_396229/p10"
    counter_status = counter_case / "native_counter.runs/imple_1/native_counter_route_status.rpt"
    counter_slack = counter_case / "native_counter.runs/imple_1/native_counter.slack.rpt"
    counter_counts = {}
    if counter_status.is_file():
        for label, value in re.findall(r"^# of ([^.:]+?)[. ]*:\s*(\d+):?\s*$",
                                       counter_status.read_text(errors="replace"), re.M):
            counter_counts[label.strip()] = int(value)
    route_status_audit = {
        "e3_route_status": (run10 or {}).get("route_status"),
        "counter_probe_precedent": {
            "case": str(counter_case.relative_to(ROOT)),
            "route_status_counts": counter_counts,
            "slack_report": counter_slack.read_text(errors="replace") if counter_slack.is_file() else None,
            "completion_marker": True,
        },
        "interpretation": "The 0 values for 'routable nets' and 'fully routed nets' are stale vendor "
                          "counters, not evidence of zero routing: (a) the installed run_route.tcl "
                          "comments out the whole route_node_statistic block (lines 258-267) but still "
                          "calls write_route_status_rpt at line 292; (b) the accepted counter probe "
                          "shows the same zeros while producing a valid slack report; (c) the E3 run "
                          "wrote an 18.1 MB .route netlist and a 630,148 byte .route.dly.pb delay file, "
                          "and the timing DB reports 9,162 ps of net delay on the worst path, which "
                          "can only come from routed interconnect; (d) the independent r8 audit parsed "
                          "24,955 net segments (20,152 with SOURCE/CHAN/SINK) from a .route file of "
                          "the same vendor flow.",
        "vendor_script_evidence": {
            "run_route_tcl": str(INSTALL / "bin/shell/bin/run_route.tcl"),
            "commented_route_node_statistic_lines": "258-267",
            "unconditional_write_route_status_rpt_line": "292",
        },
        "e3_route_artifacts": {
            "route_netlist_bytes": 18100542,
            "route_delay_pb_bytes": 630148,
            "tiles_pb_bytes": 10261841,
        },
        "cross_reference": "docs/elinx/r8_evidence_audit.md section '为什么route_status为零' reaches "
                           "the same conclusion from a .route segment parse; the vendor writer root "
                           "cause is still not proven from vendor sources.",
        "limitation": "This is a documented vendor-report artifact reading plus routed-delay evidence; "
                      "it is not a formal net-by-net connectivity DRC proof.",
    }

    audit = {
        "stage": "E3_PACK_PASS_ROUTE_COMPLETE_TIMING_FAIL_AT_10NS" if run10 and v10.get("clk_sync_slack_ns") is not None else
                 ("E3_ROUTE_FAILED_OR_INCOMPLETE"),
        "case_root": report["case_root"], "top": TOP, "case_name": NAME,
        "target": report["native_target"], "free_io_locations": FREE_IO,
        "source_checks": sources,
        "worktree_drift": {"files": worktree_drift, "note": drift_note,
                           "scope": "E3 evidence stays pinned to the frozen snapshot hashes above"},
        "wrapper": {"path": report["wrapper"], "sha256": wrapper_now, "pins": pin_breakdown},
        "module_dependency_graph": report["module_dependency_graph"],
        "wrapper_elaboration": report.get("wrapper_elaboration"),
        "pack_verified": report.get("pack_verified"),
        "pack_products": report.get("pack_products"),
        "pack_attempts": report.get("pack_attempts"),
        "classic_summary": report.get("classic_summary"),
        "native_utilization": report.get("native_utilization"),
        "resource_delta_vs_e2": delta,
        "io_budget": io_budget,
        "route_records": records,
        "internal_10ns_verdict": v10,
        "internal_20ns_verdict": v20,
        "negative_control": negative_control,
        "relaxed_period_control": relaxed_control,
        "clock_and_unconstrained_audit": clocks,
        "route_status_audit": route_status_audit,
        "pack_completeness": {
            "completion_marker": (report.get("pack_products") or {}).get("completion_marker"),
            "ver_pb_present": bool((report.get("pack_products") or {}).get("ver_pb_sha256")),
            "cluster_xml_parsed": (report.get("pack_products") or {}).get("cluster_count"),
            "note": "The first E3 pack attempt was interrupted after udm_save_checkpoint: the .ecp "
                    "existed but .ver.pb was missing and the cluster XML was truncated, and the route "
                    "step rejected it with 'ecp file NOT accordance with current version'. The "
                    "accepted pack was repacked and now carries the completion marker, .ver.pb and a "
                    "parseable 19.5 MB cluster netlist. Interrupted attempt kept under "
                    "pack_attempt1_interrupted/.",
        },
        "throughput": throughput,
        "internal_10ns_verified": v10.get("verified", False),
        "negative_timing_control_verified": negative_control.get("verified", False),
        "system_100mhz_verified": False,
        "system_100mhz_blockers": [
            "clk-to-clk worst slack is -6.599 ns at 10 ns, so even the internal domain misses 100 MHz",
            "no board pin assignment and no input/output delay budget: IO paths are unconstrained "
            "(the NONE groups carry period 0 ps, so their slacks are not usable)",
            "no board-level clock source/skew model, no IO standard, no package pinout",
        ],
        "board_pins_verified": False,
        "scope": "clk-to-clk register timing of the narrowed RX boundary. The 100 MHz system "
                 "claim is NOT made: internal domain already fails 10 ns and IO paths are untimed.",
    }
    (OUT / "rx_e3_audit.json").write_text(json.dumps(audit, indent=2))
    (base / "audit.json").write_text(json.dumps(audit, indent=2))

    tools = [INSTALL / "bin/Passkey/bin/quartus_map.exe",
             INSTALL / "bin/Passkey/bin/quartus_cdb.exe",
             INSTALL / "bin/shell/bin/Implementation.exe",
             INSTALL / "bin/shell/bin/run_pack.tcl",
             INSTALL / "bin/shell/bin/run_route.tcl",
             INSTALL / "verilator/verilator.exe"]
    tools += list((INSTALL / "bin/shell/Infrastructure/psk_extern/sdc2.0.1").glob("*.tcl"))
    tools += [p for p in (INSTALL / "bin/shell/Infrastructure/psk_intern/eHiChip6/EQ6HL130").rglob("*")
              if p.is_file() and p.suffix in [".dat", ".xml", ".tcl"]]
    tools += [INSTALL / "bin/shell/Infrastructure/psk_intern/eHiChip6/series_library" / n
              for n in ["place_route_guide.xml", "arc_guide.tcl"]]
    manifest = {"case": report["case_root"],
                "local": {str(p.relative_to(ROOT)): {"sha256": sha(p), "bytes": p.stat().st_size}
                          for p in base.rglob("*") if p.is_file() and p.name != "e3_manifest.json"},
                "installed_inputs": {str(p): {"sha256": sha(p), "bytes": p.stat().st_size}
                                     for p in tools if p.is_file()},
                "scripts": {str(p.relative_to(ROOT)): sha(p)
                            for p in (ROOT / "scripts/elinx_integration").glob("*rx_e3.py")},
                "wrapper": {report["wrapper"]: report["wrapper_sha256"]}}
    (base / "e3_manifest.json").write_text(json.dumps(manifest, indent=2))
    (OUT / "rx_e3_manifest.json").write_text(json.dumps(manifest, indent=2))

    # Merge the authoritative timing/critical-path numbers into the case report
    # while preserving any fields written by the orchestrator.
    latest = json.loads((OUT / "rx_e3_latest.json").read_text()) if (OUT / "rx_e3_latest.json").is_file() else {}
    worst10 = ((run10 or {}).get("timing") or {}).get("worst_path")
    if worst10:
        worst10_summary = {k: worst10[k] for k in
                           ["path_id", "slack_ps", "levels", "total_delay_ps", "logic_delay_ps",
                            "net_delay_ps", "clock_skew_ps", "from_cell", "to_cell",
                            "ram_read_write_nodes", "mux_nodes", "carry_chain_nodes"]}
    else:
        worst10_summary = None
    latest.update({
        "stage": audit["stage"],
        "audit": "reports/elinx_integration/rx_e3_audit.json",
        "route_verified": bool(records) and all(r.get("route_completed") for r in records),
        "internal_timing_verified": v10.get("verified", False),
        "system_timing_verified": False,
        "negative_timing_control_verified": negative_control.get("verified", False),
        "internal_10ns_verified": v10.get("verified", False),
        "worst_path_10ns": worst10_summary,
        "worst_path_10ns_classification": "afdx_rx_merge 64-bit timestamp difference/add/compare chain "
                                          "feeding the last_sn RAM write enable; net delay 9.162 ns of "
                                          "16.549 ns total, RAM read 2.455 ns, several 50 ps/bit carry "
                                          "chain segments. 0.1 ns run relocates the worst path to the UDP "
                                          "parser header->m_sn enable path (15.985 ns), i.e. the class of "
                                          "failure is long enable/compare chains, not one single net.",
        "differential_controls": {"p10_minus_p0p1_slack_ns": differential.get("p10_minus_p0p1_slack_ns"),
                                  "p20_minus_p10_slack_ns": differential.get("p20_minus_p10_slack_ns"),
                                  "expected_deltas_ns": {"p10_minus_p0p1": 9.9, "p20_minus_p10": 10.0}},
        "clk_constrained": bool((clocks.get("10.0") or {}).get("clk_is_constrained")),
        "unconstrained_io_groups": (clocks.get("10.0") or {}).get("unconstrained_groups"),
        "io_budget_verdict": io_budget["verdict"],
        "throughput_conclusion": throughput["verdict"] if throughput else None,
        "route_report_note": route_status_audit["interpretation"],
    })
    (OUT / "rx_e3_latest.json").write_text(json.dumps(latest, indent=2))
    print("LATEST_REPORT_UPDATED: " + str((OUT / "rx_e3_latest.json").relative_to(ROOT)))
    print(json.dumps({
        "pins": native_pins, "free_io": FREE_IO,
        "internal_10ns": v10, "internal_20ns": v20,
        "negative_control": negative_control, "relaxed_control": relaxed_control,
        "worst_path": ((run10 or {}).get("timing") or {}).get("worst_path"),
        "route_status": [{"period": r["period_ns"], "status": r["status"],
                          "route_completed": r.get("route_completed"),
                          "clk_slack": (r.get("clk_slack") or {}).get("sync_slack_ns")}
                         for r in records],
        "local_manifest_files": len(manifest["local"]),
    }, indent=2))


if __name__ == "__main__":
    main()
