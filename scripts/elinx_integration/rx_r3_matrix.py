"""Summarise one E4 route experiment (worst paths, families, Fmax) for the R3 matrix."""
import json, sys, collections

def sig(cell):
    parts = cell.split("|")
    return "|".join(parts[1:4]) if len(parts) > 3 else cell

def main(path):
    j = json.load(open(path))
    ts = (j.get("timing_summary") or {}).get("timing_summary") or [{}]
    ts = ts[0]
    clk = (j.get("slack_report") or {}).get("clocks") or [{}]
    clk = clk[0]
    print("tag=%s period=%s status=%s" % (j.get("experiment"), j.get("period_ns"), j.get("status")))
    print("Fmax=%.4f MHz WNS=%.3f ns TNS=%.3f ns failing_endpoints=%s/%s" % (
        clk.get("fmax_mhz", 0), clk.get("sync_slack_ns", 0), ts.get("setup_tns", 0) / 1000.0,
        ts.get("setup_number_failing_endpoints"), ts.get("setup_number_endpoints")))
    u = j.get("utilization_placed") or {}
    print("placed: LUT=%s FF=%s CLB=%s MEM=%s endpoints=%s" % (u.get("lut"), u.get("ff"), u.get("clb"), u.get("memory"), u.get("endpoints")))
    ps = (j.get("paths") or {}).get("worst_paths") or []
    fams = collections.Counter()
    for p in ps:
        fams[(sig(p["from_cell_name"]), sig(p["to_cell_name"]))] += 1
    print("path families (top %d):" % len(ps))
    for (a, b), n in fams.most_common(12):
        print("  n=%-3d %s -> %s" % (n, a, b))
    print("worst 8:")
    for p in ps[:8]:
        print("  slack=%-8s lvl=%-3s tot=%-7s logic=%-7s net=%-7s %s -> %s" % (
            p["slack"], p["levels"], p["total_delay"], p["logic_delay"], p["net_delay"],
            p["from_cell_name"], p["to_cell_name"]))
    slacks = sorted(float(p["slack"]) for p in ps)
    print("path slack range: %.3f .. %.3f (n=%d)" % (slacks[0], slacks[-1], len(slacks)))

if __name__ == "__main__":
    main(sys.argv[1])
