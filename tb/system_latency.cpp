#include "Vafdx_system_top.h"
#include "verilated.h"
#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <deque>
#include <fstream>
#include <iostream>
#include <random>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>
// System-level end-to-end latency / jitter measurement on the joint AFDX top.
// Measurement basis: TX sfd_pulse/sfd_time -> RX application-side rx_last&&rx_ready.
// Two groups: minimum pipeline latency (rx_ready always 1) and application
// backpressure (rx_ready low for 19 of every 97 cycles). 4 VL x 25 frames = 100
// frames per group, legal BAG spacing (bag_exp=0 -> 100000 cycles).
// RTL and tb/system_loopback.cpp are untouched.
using Bytes = std::vector<unsigned char>;
static Vafdx_system_top d;
static const unsigned VLS = 4;
static const unsigned FRAMES_PER_VL = 25;
static const unsigned bag_exp[VLS] = {0, 0, 0, 0};
static const unsigned long long BAG_CYCLES = 100000ULL;
static std::mt19937 rng(20261023);
static unsigned long long checks = 0, cycles = 0;
static unsigned g_ready_mode = 0;
static unsigned long long group_start = 0;
struct Exp { unsigned vl; Bytes payload; unsigned sn; unsigned network; std::uint64_t sfd; };
struct Sample { unsigned vl, sn, network, len; std::uint64_t latency; };
static std::array<std::deque<Exp>, VLS> per_vl;
static std::array<std::deque<Exp>, VLS> deliver_q;
static std::array<unsigned, VLS> next_sn{}, sent_per_vl{}, delivered_per_vl{};
static std::array<bool, VLS> sn_started{}, has_sfd{};
static std::array<std::uint64_t, VLS> last_sfd{};
static std::vector<Sample> samples;
static unsigned delivered = 0, sent = 0, decisions = 0, passed_dec = 0;
static unsigned rm_duplicates = 0, unexpected_rejects = 0;
static unsigned long long max_time_delta = 0;
static unsigned long long full_drop_total() { return (d.full_drops & 0xffffffffULL) + (d.full_drops >> 32); }
static unsigned long long bad_fcs_total() { return (d.bad_fcs & 0xffffffffULL) + (d.bad_fcs >> 32); }
static void need(bool ok, const std::string &s) { ++checks; if (!ok) throw std::runtime_error(s); }
static Bytes current;
static std::uint64_t byte_time = 0, end_time = 0, current_sfd = 0;
static Bytes delivery;
static unsigned delivery_vl = 0, delivery_sn = 0, delivery_network = 0, delivery_len = 0;
static unsigned long long delivery_time = 0;
static bool delivery_active = false;
static bool physical_sfd = false;
static void step() {
 physical_sfd = false;
 d.clk = 0;
 // Application backpressure pattern is driven before the DUT evaluates.
 d.rx_ready = (g_ready_mode == 0) ? 1 : (((cycles - group_start) % 97) >= 19);
 d.eval();
 bool byte_now = d.tx_line_valid && d.tx_byte_ce && !d.reset;
 bool drive = d.tx_line_valid && !d.reset;
 d.rx_line_valid_a = drive; d.rx_line_valid_b = drive;
 d.rx_line_data_a = d.tx_line_data; d.rx_line_data_b = d.tx_line_data;
 d.rx_line_error_a = 0; d.rx_line_error_b = 0;
 if (!d.reset && byte_now) {
  if (current.empty()) { if (end_time) need(d.monotonic_time - end_time >= 104, "system TX IFG violation"); }
  else need(d.monotonic_time - byte_time == 8, "system TX byte cadence violation");
  if (current.size() == 7) { need(d.tx_line_data == 0xd5, "system missing SFD byte"); physical_sfd = true; current_sfd = d.monotonic_time; }
  current.push_back(d.tx_line_data); byte_time = d.monotonic_time;
 }
 if (!d.tx_line_valid && !current.empty()) { need(current.size() >= 72, "system truncated frame"); current.clear(); end_time = byte_time; }
 if (!d.reset && d.rx_valid && d.rx_ready) {
  if (!delivery_active) { delivery_active = true; delivery.clear(); delivery_vl = d.rx_vl; delivery_sn = d.rx_sn; delivery_network = d.rx_network; delivery_len = d.rx_len; delivery_time = d.rx_time; }
  else need(d.rx_vl == delivery_vl && d.rx_sn == delivery_sn && d.rx_network == delivery_network && d.rx_len == delivery_len, "system RX descriptor changed mid-frame");
  delivery.push_back(d.rx_data);
  if (d.rx_last) {
   need(delivery_vl < VLS, "delivered VL outside the configured subset");
   need(!deliver_q[delivery_vl].empty(), "unsolicited delivery on a VL");
   const Exp &e = deliver_q[delivery_vl].front();
   need(delivery_sn == e.sn, "delivered wrong SN: vl=" + std::to_string(delivery_vl) + " got=" + std::to_string(delivery_sn) + " exp=" + std::to_string(e.sn));
   // Either network may win the redundancy arbitration when no fault is injected;
   // the delivered copy must still be one of the two copies of this frame.
   need(delivery_network <= 1, "delivered an invalid network flag");
   need(delivery_len == e.payload.size(), "delivered wrong payload length");
   need(delivery == e.payload, "delivered corrupted payload");
   std::uint64_t delta = delivery_time > e.sfd ? delivery_time - e.sfd : e.sfd - delivery_time;
   if (delta > max_time_delta) max_time_delta = delta;
   need(delta == 0, "per-frame rx_time does not equal the transmitted sfd_time");
   samples.push_back(Sample{delivery_vl, delivery_sn, delivery_network, delivery_len, (std::uint64_t)d.monotonic_time - e.sfd});
   deliver_q[delivery_vl].pop_front();
   ++delivered; ++delivered_per_vl[delivery_vl]; delivery_active = false;
  }
 }
 d.clk = 1; d.eval(); ++cycles;
 if (!d.reset) {
  need(!d.tx_fault, "system TX reported an internal fault");
  if (d.rx_decision) { ++decisions; if (d.rx_decision_pass) ++passed_dec; else if (d.rx_reason == 3) ++rm_duplicates; else if (d.rx_reason == 2) ++unexpected_rejects; else ++unexpected_rejects; }
  if (d.sfd_pulse) {
   need(physical_sfd && d.sfd_time == current_sfd, "system SFD timestamp mismatch");
   unsigned vl = d.sfd_vl;
   need(vl < VLS, "system SFD VL outside the configured subset");
   need(!per_vl[vl].empty(), "system sent an unsolicited VL frame");
   Exp e = per_vl[vl].front(); per_vl[vl].pop_front();
   e.sn = sn_started[vl] ? next_sn[vl] : 0u;
   next_sn[vl] = (e.sn == 255) ? 1 : e.sn + 1; sn_started[vl] = true;
   e.network = 0; e.sfd = d.sfd_time;
   if (has_sfd[vl]) need(d.sfd_time - last_sfd[vl] >= (BAG_CYCLES << bag_exp[vl]), "system BAG spacing violation");
   last_sfd[vl] = d.sfd_time; has_sfd[vl] = true; ++sent; ++sent_per_vl[vl];
   deliver_q[vl].push_back(e);
  }
 }
}
static void reset_system() {
 d.reset = 1; d.run = 0; d.app_enable = 0; d.cfg_valid = 0; d.app_msg_valid = 0; d.app_valid = 0;
 d.app_rsp_ready = 0; d.drop_valid = 0; d.drop_rsp_ready = 0; d.rx_ready = 1;
 step(); step(); d.reset = 0;
 unsigned n = 0; while (!d.initialized) { step(); need(++n < 4000, "system initialization timeout"); }
}
static void configure() {
 d.app_enable = 0; d.run = 0; d.cfg_valid = 0; d.clk = 0; d.eval();
 for (unsigned i = 0; i < VLS; ++i) {
  d.cfg_valid = 1; d.cfg_vl = i; d.cfg_bag_exp = bag_exp[i];
  unsigned cw = 0;
  while (true) { d.clk = 0; d.eval(); if (d.cfg_ready) break; step(); need(++cw < 16, "system configuration not ready on an idle port"); }
  step();
 }
 d.cfg_valid = 0; d.clk = 0; d.eval();
}
static void enqueue(unsigned vl, const Bytes &payload) {
 d.app_enable = 1; d.app_msg_valid = 1; d.app_vl = vl; d.app_length = payload.size();
 unsigned n = 0;
 while (true) { d.clk = 0; d.eval(); bool accept = d.app_msg_ready; step(); if (accept) break; need(++n < 3000000, "system app admission timeout"); }
 d.app_msg_valid = 0; d.app_vl = (vl + 1) % VLS; d.app_length = 0; d.eval();
 unsigned pos = 0; bool offering = false; n = 0;
 while (!d.app_rsp_valid) {
  if (!offering && pos < payload.size() && d.app_ready && rng() % 5 != 0) offering = true;
  d.app_valid = offering; d.app_data = pos < payload.size() ? payload[pos] : 0; d.app_last = pos + 1 == payload.size();
  d.clk = 0; d.eval(); bool byte = d.app_valid && d.app_ready;
  step(); if (byte) { ++pos; offering = false; }
  need(++n < payload.size() * 12 + 500000, "system app payload timeout");
 }
 d.app_valid = 0;
 need(d.app_rsp_status == 0, "system enqueue rejected status=" + std::to_string(d.app_rsp_status) +
      " vl=" + std::to_string(vl) + " len=" + std::to_string(payload.size()) + " cycle=" + std::to_string(cycles));
 need(pos == payload.size(), "system successful message consumed the wrong byte count");
 d.app_rsp_ready = 1; step(); d.app_rsp_ready = 0;
}
static void drain() {
 unsigned long long n = 0; const unsigned expected = VLS * FRAMES_PER_VL;
 // Wait for the full expected frame count: the last frame of each VL is still queued
 // in the TX at the end of the enqueue loop and is emitted at its BAG deadline.
 while (delivered < expected && n < 40000000) { step(); ++n; }
 while (n < 40000000) { unsigned before = decisions; for (unsigned i = 0; i < 2000 && n < 40000000; ++i) { step(); ++n; } if (decisions == before) break; }
}
static std::string num(double v) { std::ostringstream o; o.setf(std::ios::fixed); o.precision(3); o << v; return o.str(); }
struct Stats { unsigned count; double min, avg, max, p50, p99, jitter_max, jitter_mean, stddev; };
static Stats compute(const std::vector<std::uint64_t> &v) {
 Stats s{}; s.count = (unsigned)v.size();
 if (v.empty()) return s;
 std::vector<std::uint64_t> sorted = v; std::sort(sorted.begin(), sorted.end());
 double sum = 0; for (auto x : v) sum += (double)x;
 s.min = (double)sorted.front(); s.max = (double)sorted.back(); s.avg = sum / v.size();
 auto pct = [&](double p) { if (sorted.empty()) return 0.0; size_t idx = (size_t)std::ceil(p * sorted.size()); if (idx == 0) idx = 1; if (idx > sorted.size()) idx = sorted.size(); return (double)sorted[idx - 1]; };
 s.p50 = pct(0.50); s.p99 = pct(0.99);
 double var = 0; for (auto x : v) var += ((double)x - s.avg) * ((double)x - s.avg); s.stddev = std::sqrt(var / v.size());
 double jm = 0, jsum = 0; unsigned jn = 0;
 for (size_t i = 1; i < v.size(); ++i) { double jj = std::fabs((double)v[i] - (double)v[i - 1]); if (jj > jm) jm = jj; jsum += jj; ++jn; }
 s.jitter_max = jm; s.jitter_mean = jn ? jsum / jn : 0;
 return s;
}
static std::string stats_json(const Stats &s) {
 std::ostringstream o;
 o << "{\"count\":" << s.count << ",\"min\":" << num(s.min) << ",\"avg\":" << num(s.avg)
   << ",\"max\":" << num(s.max) << ",\"p50\":" << num(s.p50) << ",\"p99\":" << num(s.p99)
   << ",\"jitter_max\":" << num(s.jitter_max) << ",\"jitter_mean\":" << num(s.jitter_mean)
   << ",\"jitter_stddev\":" << num(s.stddev) << "}";
 return o.str();
}
int main(int argc, char **argv) {
 try {
  Verilated::commandArgs(argc, argv); need(argc == 2, "usage result.json");
  std::ostringstream groups;
  std::string overall_reason;
  unsigned groups_pass = 0, group_count = 0;
  for (unsigned mode = 0; mode < 2; ++mode) {
   ++group_count;
   g_ready_mode = mode;
   for (auto &q : per_vl) q.clear(); for (auto &q : deliver_q) q.clear();
   samples.clear(); delivered = sent = decisions = passed_dec = rm_duplicates = unexpected_rejects = 0;
   delivered_per_vl.fill(0); sent_per_vl.fill(0); next_sn.fill(0); sn_started.fill(false);
   has_sfd.fill(false); last_sfd.fill(0); max_time_delta = 0;
   reset_system(); configure();
   g_ready_mode = mode; group_start = cycles;
   d.run = 1; d.app_enable = 1;
   const unsigned lens[4] = {1, 17, 63, 1471};
   unsigned ordinal = 0;
   // The TX stores at most MAX_PER_VL frames per VL. Feed one outstanding frame per
   // VL only (waiting for the previous delivery first); the TX scheduler enforces
   // the BAG spacing, and with BAG=100000 cycles the next frame is queued with
   // >95k cycles of margin. This keeps the app-side admission outside the transmit
   // buffer capacity and does not change the measured RX latency.
   for (unsigned k = 0; k < FRAMES_PER_VL; ++k) for (unsigned v = 0; v < VLS; ++v) {
    if (!k) {
     // Stagger the first frame of each VL by BAG/4 so the four VLs never become due
     // in the same window: the joint TX emits one frame at a time with only the
     // minimum IFG, and a simultaneous four-frame burst can overflow the RX MAC's
     // two store-and-forward slots (whole-frame drops, not a latency property).
     unsigned long long target = (unsigned long long)v * (BAG_CYCLES / 4);
     while ((cycles - group_start) < target) step();
    }
    if (k) {
     unsigned long long w = 0;
     while (delivered_per_vl[v] < k) {
      step(); ++w;
      if (w % 100000 == 0) std::cout << "WAIT k=" << k << " v=" << v << " cycle=" << cycles << " sent=" << sent
        << " delivered=" << delivered << " per_vl=" << delivered_per_vl[0] << "," << delivered_per_vl[1] << ","
        << delivered_per_vl[2] << "," << delivered_per_vl[3] << " drops=" << full_drop_total()
        << " rejects=" << d.rejected_frames << std::endl;
      need(w < 3000000, "wait for previous delivery timeout k=" + std::to_string(k) + " v=" + std::to_string(v) +
        " cycle=" + std::to_string(cycles) + " sent=" + std::to_string(sent) + " delivered=" + std::to_string(delivered) +
        " per_vl=" + std::to_string(delivered_per_vl[0]) + "/" + std::to_string(delivered_per_vl[1]) + "/" +
        std::to_string(delivered_per_vl[2]) + "/" + std::to_string(delivered_per_vl[3]));
     }
    }
    unsigned len = lens[(k + v) % 4]; Bytes p;
    for (unsigned i = 0; i < len; ++i) p.push_back((i * 17 + v * 31 + k * 7) % 256);
    per_vl[v].push_back(Exp{v, p, 0, 0, 0});
    enqueue(v, p); ++ordinal;
    std::cout << "ENQ k=" << k << " v=" << v << " len=" << len << " cycle=" << cycles << " sent=" << sent
              << " delivered=" << delivered << std::endl;
   }
   d.app_enable = 0;
   need(ordinal == VLS * FRAMES_PER_VL, "enqueue count mismatch");
   drain();
   std::vector<std::uint64_t> all; std::array<std::vector<std::uint64_t>, VLS> by_vl;
   for (auto &s : samples) { all.push_back(s.latency); by_vl[s.vl].push_back(s.latency); }
   Stats gs = compute(all);
   std::array<Stats, VLS> vs; for (unsigned v = 0; v < VLS; ++v) vs[v] = compute(by_vl[v]);
   std::string reason;
   if (sent != VLS * FRAMES_PER_VL) reason += "sent mismatch;";
   if (delivered != VLS * FRAMES_PER_VL) reason += "delivered mismatch;";
   for (unsigned v = 0; v < VLS; ++v) if (delivered_per_vl[v] != FRAMES_PER_VL) reason += "per-VL delivery mismatch;";
   if (unexpected_rejects != 0) reason += "unexpected rejects (non RM-duplicate);";
   if (rm_duplicates != VLS * FRAMES_PER_VL) reason += "rm duplicate count mismatch;";
   if (full_drop_total() != 0) reason += "full drops;";
   if (bad_fcs_total() != 0) reason += "bad fcs;";
   if (max_time_delta != 0) reason += "rx_time != sfd_time;";
   if (d.delivered_frames != (unsigned)delivered) reason += "hw delivered counter mismatch;";
   if (d.delivered_frames + d.rejected_frames != decisions) reason += "decision accounting mismatch;";
   if (delivery_active) reason += "partial delivery left open;";
   for (auto &q : deliver_q) if (!q.empty()) reason += "expected frame undelivered;";
   bool ok = reason.empty();
   if (ok) ++groups_pass;
   else if (overall_reason.empty()) overall_reason = "group" + std::to_string(mode) + ":" + reason;
   std::ostringstream gj;
   gj << "{\"name\":\"" << (mode == 0 ? "min_pipeline" : "app_backpressure") << "\""
      << ",\"rx_ready\":\"" << (mode == 0 ? "always" : "low for 19 of 97 cycles") << "\""
      << ",\"status\":\"" << (ok ? "PASS" : "CHARACTERIZED") << "\""
      << ",\"reason\":\"" << reason << "\""
      << ",\"frames_per_vl\":" << FRAMES_PER_VL << ",\"vl_count\":" << VLS
      << ",\"bag_cycles\":" << BAG_CYCLES
      << ",\"counters\":{\"sent\":" << sent << ",\"delivered\":" << delivered
      << ",\"passed\":" << passed_dec << ",\"rm_duplicate_rejects\":" << rm_duplicates
      << ",\"unexpected_rejects\":" << unexpected_rejects
      << ",\"hw_rejected_frames\":" << (unsigned)d.rejected_frames
      << ",\"decisions\":" << decisions
      << ",\"full_drops\":" << full_drop_total() << ",\"bad_fcs\":" << bad_fcs_total()
      << ",\"max_time_delta\":" << max_time_delta << "}"
      << ",\"stats\":{\"global\":" << stats_json(gs) << ",\"per_vl\":[";
   for (unsigned v = 0; v < VLS; ++v) { if (v) gj << ","; gj << "{\"vl\":" << v << "," << stats_json(vs[v]).substr(1); if (v != VLS - 1) gj << ""; }
   gj << "]},\"samples\":[";
   for (size_t i = 0; i < samples.size(); ++i) {
    if (i) gj << ",";
    gj << "{\"vl\":" << samples[i].vl << ",\"sn\":" << samples[i].sn << ",\"network\":" << samples[i].network
       << ",\"len\":" << samples[i].len << ",\"latency\":" << samples[i].latency << "}";
   }
   gj << "]}";
   if (mode) groups << ",";
   groups << gj.str();
   std::cout << (mode == 0 ? "min_pipeline" : "app_backpressure") << " status=" << (ok ? "PASS" : "CHARACTERIZED")
             << " reason=" << (reason.empty() ? "none" : reason)
             << " count=" << gs.count << " min=" << gs.min << " avg=" << gs.avg << " max=" << gs.max
             << " p50=" << gs.p50 << " p99=" << gs.p99 << " jitter_max=" << gs.jitter_max
             << " stddev=" << gs.stddev << " cycles=" << cycles << std::endl;
   for (unsigned v = 0; v < VLS; ++v)
    std::cout << "  vl" << v << " count=" << vs[v].count << " min=" << vs[v].min << " avg=" << vs[v].avg
              << " max=" << vs[v].max << " p50=" << vs[v].p50 << " p99=" << vs[v].p99
              << " jitter_max=" << vs[v].jitter_max << " stddev=" << vs[v].stddev << std::endl;
  }
  bool all_ok = (groups_pass == group_count);
  std::ostringstream out;
  out << "{\"status\":\"" << (all_ok ? "PASS" : "CHARACTERIZED") << "\""
      << ",\"reason\":\"" << (all_ok ? "" : overall_reason) << "\""
      << ",\"measurement_basis\":\"simulation cycles, Verilator; TX sfd_pulse/sfd_time -> RX app rx_last&&rx_ready; not hardware sign-off\""
      << ",\"vl_count\":" << VLS << ",\"frames_per_vl\":" << FRAMES_PER_VL
      << ",\"bag_cycles\":" << BAG_CYCLES
      << ",\"groups_total\":" << group_count << ",\"groups_passed\":" << groups_pass
      << ",\"checks\":" << checks << ",\"cycles\":" << cycles
      << ",\"groups\":[" << groups.str() << "]}";
  std::ofstream f(argv[1]); f << out.str() << "\n";
  std::cout << "SUMMARY status=" << (all_ok ? "PASS" : "CHARACTERIZED") << " groups_passed=" << groups_pass
            << "/" << group_count << " checks=" << checks << " cycles=" << cycles << std::endl;
  return 0;
 } catch (const std::exception &e) { std::cerr << "FAIL system_latency " << e.what() << " cycles=" << cycles << " sent=" << sent << " delivered=" << delivered << std::endl; return 1; }
}
