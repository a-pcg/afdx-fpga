"""Build and run the afdx_system_mgmt_top integration demo.

Preferred flow is Verilator 5.006 (--cc --timing with a generated main, MSVC
compile, same style as scripts/rtl_test_build.py); when that toolchain is not
usable the script falls back to ModelSim vlog/vsim, which is the flow used by
the other management testbenches in this project.

Steps
  1. regenerate the SNMP request/expected vectors
     (scripts/mgmt/gen_system_mgmt_vectors.py -> tb/system_mgmt_snmp_vectors.vh)
  2. build + run tb/tb_afdx_system_mgmt.v
  3. write reports/mgmt_integration/{inputs,result}.json, the tool logs and a
     copy of the successful simulation log

Usage
  python scripts/mgmt/run_system_mgmt_integration.py
  python scripts/mgmt/run_system_mgmt_integration.py --tool verilator
  python scripts/mgmt/run_system_mgmt_integration.py --tool modelsim
"""
from pathlib import Path
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "mgmt"))

from rtl_test_build import INC, VC, VER  # noqa: E402
import gen_system_mgmt_vectors as vecgen  # noqa: E402

REPORT = ROOT / "reports/mgmt_integration"
TOP = "tb_afdx_system_mgmt"
TB = ROOT / "tb/tb_afdx_system_mgmt.v"
VECTOR_INCLUDE = ROOT / "tb/system_mgmt_snmp_vectors.vh"

NAMES = [
    "rtl/system/afdx_system_mgmt_top.v",
    "rtl/system/afdx_system_top.v",
    "rtl/mgmt/afdx_mgmt_top.v",
    "rtl/mgmt/afdx_mgmt_regs.v",
    "rtl/mgmt/afdx_snmp_agent.v",
    "rtl/afdx_shared_tx.v",
    "rtl/afdx_udp_tx_shared.v",
    "rtl/afdx_bag_scheduler.v",
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

SOURCES = [ROOT / n for n in NAMES] + [TB]

VERILATOR_MAIN = """#include "Vtb_afdx_system_mgmt.h"
#include "verilated.h"
#include <memory>
int main(int argc, char** argv, char**) {
    const std::unique_ptr<VerilatedContext> contextp{new VerilatedContext};
    contextp->commandArgs(argc, argv);
    const std::unique_ptr<Vtb_afdx_system_mgmt> topp{
        new Vtb_afdx_system_mgmt{contextp.get()}};
    while (!contextp->gotFinish()) {
        topp->eval();
        if (!topp->eventsPending()) break;
        contextp->time(topp->nextTimeSlot());
    }
    topp->final();
    return 0;
}
"""


def rel(path):
    return path.relative_to(ROOT).as_posix()


def digest(path):
    return {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "bytes": path.stat().st_size}


def write_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def run_tool(args, log, cwd=ROOT, env=None, timeout=3600, shell=False):
    with log.open("wb") as stream:
        return subprocess.run(
            args, cwd=str(cwd), env=env, stdout=stream,
            stderr=subprocess.STDOUT, timeout=timeout, shell=shell).returncode


def parse_verdict(text):
    fail = re.search(r"TEST_FAIL[^\n]*", text)
    if fail:
        return "FAIL", fail.group(0).strip()
    match = re.search(r"TEST_PASS[^\n]*", text)
    if match:
        return "PASS", match.group(0).strip()
    return "FAIL", "no TEST_PASS/TEST_FAIL marker in simulator output"


def parse_metrics(pass_line):
    metrics = {}
    for key in ("delivered", "rejected", "bad_fcs", "snap_tx", "checks"):
        found = re.search(key + r"=(\d+)", pass_line)
        if found:
            metrics[key] = int(found.group(1))
    return metrics


def verilator_version():
    try:
        result = subprocess.run([str(VER), "--version"], capture_output=True,
                                text=True, timeout=60)
        return result.stdout.strip() or result.stderr.strip()
    except Exception as exc:  # pragma: no cover - diagnostics only
        return "unavailable: %s" % exc


def expand_tb_for_verilator():
    """Return a TB copy with the generated vector include expanded inline.

    The Verilator 5.006 build shipped with this project's toolchain silently
    drops `include directives (verified with a minimal probe), so the Verilator
    flow compiles an expanded copy of the unchanged hand-written testbench.  The
    expansion is a pure text substitution: <include line> -> file contents.
    ModelSim keeps compiling the original TB with +incdir+tb.
    """
    include_line = '`include "system_mgmt_snmp_vectors.vh"'
    tb_text = TB.read_text(encoding="utf-8")
    if include_line not in tb_text:
        raise RuntimeError("TB does not contain the expected include line")
    vectors = VECTOR_INCLUDE.read_text(encoding="ascii")
    expanded = tb_text.replace(include_line, vectors)
    path = REPORT / "tb_afdx_system_mgmt_expanded.v"
    write_text(path, expanded)
    return path


def build_and_run_verilator():
    """Return (status, verdict, pass_line, log_path, detail)."""
    report = REPORT
    obj = report / "verilator_obj"
    obj_rel = rel(obj)
    obj.mkdir(parents=True, exist_ok=True)
    build_log = report / "verilator_build.log"
    compile_log = report / "msvc.log"
    run_log = report / "verilator_run.log"

    # Verilator needs the generated configuration header next to the build.
    template = (INC / "verilated_config.h.in").read_text(encoding="utf-8")
    config = (template.replace("@PACKAGE_NAME@", "Verilator")
                      .replace("@PACKAGE_VERSION@", "5.006")
                      .replace("@VERILATOR_VERSION_INTEGER@", "5006000"))
    write_text(report / "verilated_config.h", config)
    write_text(report / "verilator_main.cpp", VERILATOR_MAIN)

    env = {k.upper(): v for k, v in os.environ.items()}
    env["VERILATOR_ROOT"] = str(INC.parent).replace("\\", "/")
    try:
        tb_for_verilator = expand_tb_for_verilator()
    except Exception as exc:
        return "FAIL", "BUILD", "vector expansion failed: %s" % exc, build_log, str(exc)

    args = [str(VER), "--cc", "--timing", "--compiler", "msvc",
            "--default-language", "1364-2001", "--top-module", TOP,
            "-Wno-fatal", "--Mdir", obj_rel]
    args += [rel(path) for path in SOURCES if path != TB]
    args.append(rel(tb_for_verilator))
    try:
        code = run_tool(args, build_log, env=env, timeout=900)
    except Exception as exc:
        return "FAIL", "BUILD", "verilator build raised %s" % exc, build_log, str(exc)
    if code:
        return "FAIL", "BUILD", "verilator build exit code %d" % code, build_log, "see log"
    if not (obj / ("V" + TOP + ".mk")).is_file():
        return "FAIL", "BUILD", "verilator did not produce a makefile", build_log, "see log"

    # MSVC environment + compile
    try:
        setup = subprocess.run(
            'call "' + str(VC) + '" >nul && set', shell=True,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            check=True)
    except Exception as exc:
        return "FAIL", "BUILD", "vcvars64 failed: %s" % exc, compile_log, str(exc)
    for line in setup.stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            if key:
                env[key.upper()] = value
    try:
        compiler = next(Path(p) / "cl.exe" for p in env["PATH"].split(";")
                        if (Path(p) / "cl.exe").is_file())
    except StopIteration:
        return "FAIL", "BUILD", "cl.exe not found after vcvars64", compile_log, "see log"

    cpp = list(obj.glob("*.cpp")) + [
        report / "verilator_main.cpp",
        INC / "verilated.cpp",
        INC / "verilated_timing.cpp",
        INC / "verilated_threads.cpp",
    ]
    executable = obj / "test.exe"
    cl_args = [
        "/nologo", "/std:c++20", "/EHsc", "/MD", "/O2", "/DVL_TIME_CONTEXT",
        "/I" + str(INC), "/I" + str(INC / "vltstd"), "/I" + str(report),
        "/I" + str(obj), "/Fe:" + str(executable),
    ] + [str(path) for path in cpp]
    write_text(report / "verilator_build.rsp",
               "\n".join('"' + argument + '"' for argument in cl_args) + "\n")
    try:
        code = run_tool([str(compiler), "@" + str(report / "verilator_build.rsp")],
                        compile_log, cwd=obj, env=env, timeout=900)
    except Exception as exc:
        return "FAIL", "BUILD", "MSVC compile raised %s" % exc, compile_log, str(exc)
    if code or not executable.is_file():
        return "FAIL", "BUILD", "MSVC compile exit code %s" % code, compile_log, "see log"

    try:
        code = run_tool([str(executable)], run_log, env=env, timeout=1800)
    except subprocess.TimeoutExpired:
        return "FAIL", "RUN", "verilator simulation timeout", run_log, "timeout"
    text = run_log.read_text(encoding="utf-8", errors="replace")
    status, pass_line = parse_verdict(text)
    return status, "RUN", pass_line, run_log, "exit code %s" % code


def build_and_run_modelsim():
    """Return (status, verdict, pass_line, log_path, detail)."""
    report = REPORT
    work = report / "modelsim_work"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True, exist_ok=True)
    vlib = shutil.which("vlib")
    vlog = shutil.which("vlog")
    vsim = shutil.which("vsim")
    if not vlib or not vlog or not vsim:
        return "FAIL", "BUILD", "vlib/vlog/vsim not on PATH", None, "ModelSim not found"

    compile_log = work / "vlog.log"
    run_log = work / "vsim.log"
    env = {k.upper(): v for k, v in os.environ.items()}
    vlib_log = work / "vlib.log"
    code = run_tool([vlib, "work"], vlib_log, cwd=work, env=env, timeout=300)
    if code:
        return "FAIL", "BUILD", "vlib failed (exit %s)" % code, vlib_log, "see log"
    compile_code = run_tool([vlog, "-quiet", "-work", "work",
                             "+incdir+" + str(ROOT / "tb")]
                            + [str(path) for path in SOURCES], compile_log,
                            cwd=work, env=env, timeout=900)
    compile_text = compile_log.read_text(encoding="utf-8", errors="replace")
    if compile_code or "Error:" in compile_text or "** Error" in compile_text:
        return "FAIL", "BUILD", "vlog reported errors (exit %s)" % compile_code, compile_log, "see log"
    try:
        code = run_tool([vsim, "-c", "-quiet", "-do", "run -all; quit -force -code 0",
                         "work." + TOP], run_log, cwd=work, env=env, timeout=1800)
    except subprocess.TimeoutExpired:
        return "FAIL", "RUN", "modelsim simulation timeout", run_log, "timeout"
    text = run_log.read_text(encoding="utf-8", errors="replace")
    status, pass_line = parse_verdict(text)
    return status, "RUN", pass_line, run_log, "exit code %s" % code


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tool",
                        choices=["auto", "verilator", "modelsim", "both"],
                        default="auto",
                        help="auto: Verilator then ModelSim on failure; "
                             "both: run both and record both attempts")
    args = parser.parse_args()

    REPORT.mkdir(parents=True, exist_ok=True)
    write_text(REPORT / "result.json", '{"status": "RUNNING"}\n')

    # 1. regenerate vectors
    vector_log = REPORT / "vector_generation.log"
    import io
    from contextlib import redirect_stdout
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        vector_meta = vecgen.emit(vecgen.DEFAULT_IN_UCAST, verbose=True)
    write_text(vector_log, buffer.getvalue())

    # 2. build + run
    attempts = []
    if args.tool in ("auto", "verilator", "both"):
        status, stage, pass_line, log, detail = build_and_run_verilator()
        attempts.append({"tool": "verilator", "status": status, "stage": stage,
                         "verdict": pass_line, "log": rel(log) if log else None,
                         "detail": detail})
    if args.tool in ("modelsim", "both") or (
            args.tool == "auto" and attempts and attempts[-1]["status"] != "PASS"):
        status, stage, pass_line, log, detail = build_and_run_modelsim()
        attempts.append({"tool": "modelsim", "status": status, "stage": stage,
                         "verdict": pass_line, "log": rel(log) if log else None,
                         "detail": detail})

    passing = next((attempt for attempt in attempts if attempt["status"] == "PASS"), None)
    final_status = "PASS" if passing else "FAIL"
    metrics = parse_metrics(passing["verdict"]) if passing else {}

    if passing and passing["log"]:
        shutil.copyfile(ROOT / passing["log"], REPORT / "simulation.log")
    for attempt in attempts:
        if attempt["status"] == "PASS" and attempt["log"]:
            shutil.copyfile(ROOT / attempt["log"],
                            REPORT / ("simulation_" + attempt["tool"] + ".log"))

    manifest = {
        "stage": "MGMT-INT-1",
        "phase": "behaviour",
        "top": TOP,
        "testbench": rel(TB),
        "parameters": {"VL_WIDTH": 7, "SLOT_WIDTH": 6, "MAX_PER_VL": 4,
                       "CFG_VL_COUNT": 4},
        "scenario": {
            "frames_sent": 17,
            "good_frames": 16,
            "vl_count": 4,
            "payload_lengths": [1, 17, 63, 1471],
            "corrupt_probe_frames": 1,
            "bag_commit_values": [1, 0],
            "expected": {"delivered": 16, "rejected": 16, "bad_fcs": 2,
                         "tx_frames": 17, "snmp_get_direct": 2, "snmp_get_rx": 1},
        },
        "files": {rel(path): digest(path) for path in SOURCES},
        "support_files": {
            rel(VECTOR_INCLUDE): digest(VECTOR_INCLUDE),
            "scripts/mgmt/gen_system_mgmt_vectors.py":
                digest(ROOT / "scripts/mgmt/gen_system_mgmt_vectors.py"),
            "scripts/mgmt/snmp_client.py":
                digest(ROOT / "scripts/mgmt/snmp_client.py"),
            "scripts/mgmt/run_system_mgmt_integration.py":
                digest(ROOT / "scripts/mgmt/run_system_mgmt_integration.py"),
            "scripts/rtl_test_build.py": digest(ROOT / "scripts/rtl_test_build.py"),
        },
        "snmp_vectors": vector_meta,
        "tools": {
            "verilator": verilator_version(),
            "modelsim_vlog": shutil.which("vlog"),
            "modelsim_vsim": shutil.which("vsim"),
        },
        "python": {"executable": sys.executable, "version": sys.version},
    }
    write_text(REPORT / "inputs.json", json.dumps(manifest, indent=2) + "\n")

    result = {
        "status": final_status,
        "tool": passing["tool"] if passing else None,
        "attempts": attempts,
        "pass_line": passing["verdict"] if passing else None,
        "metrics": metrics,
        "simulation_log": "reports/mgmt_integration/simulation.log" if passing else None,
        "checks": metrics.get("checks"),
        "sources": {rel(path): manifest["files"][rel(path)]["sha256"]
                    for path in SOURCES},
        "vector_include_sha256": manifest["support_files"][rel(VECTOR_INCLUDE)]["sha256"],
    }
    write_text(REPORT / "result.json", json.dumps(result, indent=2) + "\n")
    print(json.dumps({"status": final_status, "tool": result["tool"],
                      "metrics": metrics,
                      "pass_line": result["pass_line"],
                      "attempts": attempts}, indent=2))
    return 0 if final_status == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
