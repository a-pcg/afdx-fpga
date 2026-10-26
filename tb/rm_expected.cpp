#include "Vafdx_rx_merge.h"
#include "verilated.h"
#include <cstdint>
#include <deque>
#include <fstream>
#include <iostream>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>
// Merge-level unit test for the expected-SN dev profile (RM_EXPECTED_SN=1).
// Covers T1 duplicate, T2 late copy (L01), T3 gap-tolerated skip + late,
// T4 gap drop + resync timeout, T5 SN wrap 255->1 and SN0 restart/duplicate,
// T6 clean dual copy stream, plus the late_frames/gap_frames diagnostics.
// Parameters are forced by the build (-G): TIME_WIDTH=32, RM_EXPECTED_SN=1,
// RM_GAP_TOLERANCE=2, RM_RESYNC_TIMEOUT=50, RM_SN0_RESTART=1.
static Vafdx_rx_merge d;
static unsigned long long cycles = 0, checks = 0;
static void need(bool ok, const std::string &s) { ++checks; if (!ok) throw std::runtime_error(s); }
struct Desc { unsigned vl, sn; uint64_t stamp; };
static std::deque<Desc> lane[2];
struct Res { bool pass; unsigned reason, vl, sn, net; };
static std::vector<Res> results;
static unsigned taken = 0;
static void step() {
 d.m_ready = 1;
 unsigned v = 0, vl = 0, sn = 0; uint64_t t = 0;
 for (unsigned l = 0; l < 2; ++l) if (!lane[l].empty()) {
  v |= 1u << l; const Desc &x = lane[l].front();
  vl |= (x.vl & 7u) << (3 * l); sn |= (x.sn & 0xffu) << (8 * l);
  t |= (x.stamp & 0xffffffffULL) << (32 * l);
 }
 d.s_valid = v; d.s_vl = vl; d.s_sn = sn; d.s_time = t;
 d.s_good = 3; d.s_ic_enable = 3; d.s_vlid = 0x00010001u; d.s_len = 60; d.s_tag = 0;
 d.clk = 0; d.eval();
 bool r0 = d.s_ready & 1u, r1 = (d.s_ready >> 1) & 1u;
 if (d.m_valid) { results.push_back(Res{(bool)d.m_pass, (unsigned)d.m_reason, (unsigned)d.m_vl, (unsigned)d.m_sn, (unsigned)d.m_network}); }
 d.clk = 1; d.eval(); ++cycles;
 if (r0 && !lane[0].empty()) { lane[0].pop_front(); ++taken; }
 if (r1 && !lane[1].empty()) { lane[1].pop_front(); ++taken; }
}
static void reset_dut() {
 d.reset = 1; d.s_valid = 0; d.m_ready = 1; d.s_good = 3; d.s_ic_enable = 3;
 step(); step(); d.reset = 0;
 unsigned n = 0; while (!d.initialized) { step(); need(++n < 200, "merge initialization timeout"); }
}
static void run_until(unsigned want_taken, unsigned want_results) {
 unsigned w = 0;
 while ((taken < want_taken || results.size() < want_results) && w < 5000) { step(); ++w; }
 need(w < 5000, "merge decision timeout taken=" + std::to_string(taken) + " results=" + std::to_string(results.size()));
}
static void expect(const Res &r, bool pass, unsigned reason, unsigned sn, const std::string &name) {
 need(r.pass == pass, name + ": pass mismatch");
 need(r.reason == reason, name + ": reason " + std::to_string(r.reason) + " expected " + std::to_string(reason));
 need(r.sn == sn, name + ": sn mismatch " + std::to_string(r.sn));
}
static unsigned size_before;
static void one(unsigned l, unsigned vl, unsigned sn, uint64_t stamp) {
 lane[l].push_back(Desc{vl, sn, stamp}); run_until(taken, results.size() + 1);
}
int main(int argc, char **argv) {
 try {
  Verilated::commandArgs(argc, argv);
  need(argc == 2, "usage result.json");
  reset_dut();
  need(d.late_frames == 0 && d.gap_frames == 0, "counters must reset");
  std::ostringstream tests;
  auto begin = [&]() { size_before = (unsigned)results.size(); };
  auto check = [&](unsigned idx, bool pass, unsigned reason, unsigned sn, const std::string &name) {
   need(results.size() > idx, name + ": missing decision");
   expect(results[idx], pass, reason, sn, name);
  };
  // T1 duplicate: same SN from the other network after a delivery.
  begin(); one(0, 0, 10, 100); one(1, 0, 10, 200);
  check(size_before, true, 0, 10, "T1 first copy");
  check(size_before + 1, false, 3, 10, "T1 duplicate");
  // T2 late copy (L01): A20, A21 delivered, then B20 arrives late.
  begin(); one(0, 1, 20, 300); one(0, 1, 21, 400); one(1, 1, 20, 500);
  check(size_before, true, 0, 20, "T2 A20"); check(size_before + 1, true, 0, 21, "T2 A21");
  check(size_before + 2, false, 4, 20, "T2 late B20");
  // T3 gap-tolerated skip: A50 delivered, B52 arrives first (diff=1 -> tolerated),
  // then A51 arrives behind the expectation (diff=254 -> late).
  begin(); one(0, 2, 50, 600); one(1, 2, 52, 700); one(0, 2, 51, 800);
  check(size_before, true, 0, 50, "T3 A50"); check(size_before + 1, true, 0, 52, "T3 tolerated B52");
  check(size_before + 2, false, 4, 51, "T3 late A51");
  // T4 gap drop + resync: A60 delivered, B70 gap inside the 50-cycle timeout is
  // dropped, B71 after the timeout resynchronises.
  begin(); one(0, 3, 60, 1000); one(1, 3, 70, 1020); one(1, 3, 71, 1200);
  check(size_before, true, 0, 60, "T4 A60"); check(size_before + 1, false, 5, 70, "T4 gap B70");
  check(size_before + 2, true, 0, 71, "T4 resync B71");
  // T5 SN wrap 255->1 and SN0 restart/duplicate.
  begin(); one(0, 4, 254, 2000); one(0, 4, 255, 2100); one(0, 4, 1, 2200); one(0, 4, 2, 2300);
  check(size_before, true, 0, 254, "T5 254"); check(size_before + 1, true, 0, 255, "T5 255");
  check(size_before + 2, true, 0, 1, "T5 wrap->1"); check(size_before + 3, true, 0, 2, "T5 2");
  begin(); one(0, 4, 0, 2400); one(1, 4, 0, 2500); one(0, 4, 1, 2600);
  check(size_before, true, 0, 0, "T5 SN0 restart"); check(size_before + 1, false, 3, 0, "T5 SN0 duplicate");
  check(size_before + 2, true, 0, 1, "T5 after restart");
  // T6 clean dual-copy stream: no late/gap counters.
  begin(); one(0, 5, 100, 3000); one(1, 5, 100, 3100); one(0, 5, 101, 3200); one(1, 5, 101, 3300);
  check(size_before, true, 0, 100, "T6 A100"); check(size_before + 1, false, 3, 100, "T6 dup B100");
  check(size_before + 2, true, 0, 101, "T6 A101"); check(size_before + 3, false, 3, 101, "T6 dup B101");
  // Diagnostics: late = T2(1) + T3(1) = 2, gap = T4(2: one drop, one resync).
  need(d.late_frames == 2, "late_frames=" + std::to_string(d.late_frames) + " expected 2");
  need(d.gap_frames == 2, "gap_frames=" + std::to_string(d.gap_frames) + " expected 2");
  unsigned passed = 0, rejected = 0;
  for (auto &r : results) { if (r.pass) ++passed; else ++rejected; }
  std::cout << "PASS rm_expected checks=" << checks << " cycles=" << cycles
            << " decisions=" << results.size() << " passed=" << passed << " rejected=" << rejected
            << " late=" << d.late_frames << " gap=" << d.gap_frames << "\n";
  std::ofstream out(argv[1]);
  out << "{\"status\":\"PASS\",\"checks\":" << checks << ",\"cycles\":" << cycles
      << ",\"decisions\":" << results.size() << ",\"passed\":" << passed
      << ",\"rejected\":" << rejected << ",\"late_frames\":" << d.late_frames
      << ",\"gap_frames\":" << d.gap_frames << "}\n";
  return 0;
 } catch (const std::exception &e) {
  std::cerr << "FAIL rm_expected " << e.what() << " cycles=" << cycles
            << " decisions=" << results.size() << " late=" << d.late_frames
            << " gap=" << d.gap_frames << "\n";
  return 1;
 }
}
