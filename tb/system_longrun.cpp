#include "Vafdx_system_top.h"
#include "verilated.h"
#include <cstdint>
#include <deque>
#include <fstream>
#include <iostream>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>
// Long-run stability test on the joint AFDX top (current RTL; the joint top's
// default DUAL_LINE behaviour is unchanged). 4 VL, mixed payload lengths
// 1/17/63/1471 B, legal BAG spacing (bag_exp=0 = 1 ms at 100 MHz), configurable
// frame count. Asserts: no full drops, no bad FCS, per-VL SN continuity, every
// rx_time == the transmitted sfd_time, no TX fault, no hang, exact payloads.
static Vafdx_system_top d;
static const unsigned VLS = 4;
static unsigned g_frames_per_vl = 500;
static const unsigned BAG_CYCLES = 100000;   // bag_exp=0 at 100 MHz = 1 ms
static const unsigned LENS[4] = {1, 17, 63, 1471};
static unsigned long long cycles = 0, checks = 0;
static void need(bool ok, const std::string &s) { ++checks; if (!ok) throw std::runtime_error(s); }
struct Sfd { unsigned sn; uint64_t time; unsigned len; };
static std::deque<Sfd> sfd_q[VLS];
static unsigned sfd_count[VLS] = {0, 0, 0, 0};
static unsigned delivered = 0, delivered_vl[VLS] = {0, 0, 0, 0};
static unsigned sn_violations = 0, len_violations = 0, payload_violations = 0;
 static unsigned seq_to_sn(unsigned c) { return c == 0 ? 0u : ((c - 1u) % 255u) + 1u; }
static unsigned unexpected_rejects = 0;
static uint64_t max_delta = 0;
static std::vector<unsigned char> rx_payload, tx_payload;
static unsigned rx_vl = 0, rx_sn = 0, rx_len = 0;
static bool rx_active = false;
static void payload_for(unsigned vl, unsigned k, std::vector<unsigned char> &out) {
 out.clear();
 unsigned n = LENS[(k + vl) % 4];
 for (unsigned i = 0; i < n; ++i) out.push_back((unsigned char)((i * 17 + vl * 31 + k * 7) & 0xff));
}
static void step() {
 d.clk = 0;
 d.rx_ready = 1;
 d.eval();
 bool drive = d.tx_line_valid && !d.reset;
 d.rx_line_valid_a = drive; d.rx_line_valid_b = drive;
 d.rx_line_data_a = d.tx_line_data; d.rx_line_data_b = d.tx_line_data;
 d.rx_line_error_a = 0; d.rx_line_error_b = 0;
 if (!d.reset && d.rx_valid && d.rx_ready) {
  if (!rx_active) { rx_active = true; rx_payload.clear(); rx_vl = d.rx_vl; rx_sn = d.rx_sn; rx_len = d.rx_len; }
  else need(d.rx_vl == rx_vl && d.rx_sn == rx_sn && d.rx_len == rx_len, "RX descriptor changed mid-frame");
  rx_payload.push_back(d.rx_data);
  if (d.rx_last) {
   need(rx_vl < VLS, "delivered VL outside the configured subset");
   need(!sfd_q[rx_vl].empty(), "delivery without a matching transmitted frame");
   const Sfd e = sfd_q[rx_vl].front();
   if (rx_sn != seq_to_sn(e.sn)) { ++sn_violations; if (sn_violations <= 8) std::printf("SNMISMATCH vl=%u seq=%u expect=%u got=%u cycles=%llu\n", rx_vl, e.sn, seq_to_sn(e.sn), rx_sn, cycles); }
   if (rx_len != e.len) ++len_violations;
   std::vector<unsigned char> expect;
   payload_for(rx_vl, e.sn, expect);
   if (rx_payload != expect) ++payload_violations;
   uint64_t delta = d.rx_time > e.time ? d.rx_time - e.time : e.time - d.rx_time;
   if (delta > max_delta) max_delta = delta;
   need(delta == 0, "per-frame rx_time does not equal the transmitted sfd_time");
   sfd_q[rx_vl].pop_front();
   ++delivered; ++delivered_vl[rx_vl];
   rx_active = false;
   if (rx_payload.empty()) need(false, "empty delivery");
  }
 }
 d.clk = 1; d.eval(); ++cycles;
 if (!d.reset) {
  need(!d.tx_fault, "TX reported an internal fault");
  if (d.sfd_pulse) {
   unsigned vl = d.sfd_vl;
   need(vl < VLS, "SFD VL outside the configured subset");
   unsigned sn = sfd_count[vl]++;
   unsigned len = LENS[(sn + vl) % 4];
   sfd_q[vl].push_back(Sfd{sn, d.sfd_time, len});
  }
  if (d.rx_decision) { if (d.rx_decision_pass) {} else if (d.rx_reason != 3) ++unexpected_rejects; }
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
  d.cfg_valid = 1; d.cfg_vl = i; d.cfg_bag_exp = 0;
  unsigned cw = 0;
  while (true) { d.clk = 0; d.eval(); if (d.cfg_ready) break; step(); need(++cw < 16, "configuration not ready"); }
  step();
 }
 d.cfg_valid = 0; d.clk = 0; d.eval();
}
static void enqueue(unsigned vl, const std::vector<unsigned char> &p) {
 d.app_enable = 1; d.app_msg_valid = 1; d.app_vl = vl; d.app_length = p.size();
 unsigned n = 0;
 while (true) { d.clk = 0; d.eval(); bool accept = d.app_msg_ready; step(); if (accept) break; need(++n < 3000000, "app admission timeout"); }
 d.app_msg_valid = 0; d.app_vl = (vl + 1) % VLS; d.app_length = 0; d.eval();
 unsigned pos = 0; bool offering = false; n = 0;
 while (!d.app_rsp_valid) {
  if (!offering && pos < p.size() && d.app_ready) offering = true;
  d.app_valid = offering; d.app_data = pos < p.size() ? p[pos] : 0; d.app_last = pos + 1 == p.size();
  d.clk = 0; d.eval(); bool byte = d.app_valid && d.app_ready;
  step(); if (byte) { ++pos; offering = false; }
  need(++n < p.size() * 12 + 500000, "app payload timeout");
 }
 d.app_valid = 0;
 need(d.app_rsp_status == 0, "enqueue rejected status=" + std::to_string(d.app_rsp_status));
 need(pos == p.size(), "successful message consumed the wrong byte count");
 d.app_rsp_ready = 1; step(); d.app_rsp_ready = 0;
}
int main(int argc, char **argv) {
 try {
  Verilated::commandArgs(argc, argv);
  need(argc == 3, "usage frames_per_vl result.json");
  g_frames_per_vl = (unsigned)std::stoul(argv[1]);
  const unsigned total = VLS * g_frames_per_vl;
  reset_system(); configure();
  const unsigned long long start = cycles;
  d.run = 1; d.app_enable = 1;
  for (unsigned k = 0; k < g_frames_per_vl; ++k) for (unsigned v = 0; v < VLS; ++v) {
   if (k == 0) { while (cycles - start < (unsigned long long)v * (BAG_CYCLES / 4)) step(); }
   else {
    unsigned long long w = 0;
    while (delivered_vl[v] < k) { step(); need(++w < BAG_CYCLES * 2, "delivery wait timeout vl=" + std::to_string(v) + " k=" + std::to_string(k)); }
   }
   std::vector<unsigned char> p; payload_for(v, k, p);
   enqueue(v, p);
  }
  d.app_enable = 0;
  unsigned long long w = 0;
  while (delivered < total && w < (unsigned long long)BAG_CYCLES * 2) { step(); ++w; }
  need(delivered == total, "not all frames delivered delivered=" + std::to_string(delivered) + "/" + std::to_string(total));
  for (unsigned i = 0; i < 4000; ++i) step();
  for (unsigned v = 0; v < VLS; ++v) need(sfd_q[v].empty(), "expected frame missing at the end");
  need(delivered == total, "delivered count mismatch");
  need(unexpected_rejects == 0, "unexpected rejects (non RM-duplicate)");
  for (unsigned v = 0; v < VLS; ++v) need(delivered_vl[v] == g_frames_per_vl, "per-VL delivered count mismatch vl=" + std::to_string(v) + " delivered=" + std::to_string(delivered_vl[v]));
  unsigned long long fd = (d.full_drops & 0xffffffffULL) + (d.full_drops >> 32);
  unsigned long long bf = (d.bad_fcs & 0xffffffffULL) + (d.bad_fcs >> 32);
  need(fd == 0, "full drops " + std::to_string(fd));
  need(bf == 0, "bad FCS " + std::to_string(bf));
  need(max_delta == 0, "max rx_time/sfd_time delta " + std::to_string(max_delta));
  need(sn_violations == 0, "SN continuity violations " + std::to_string(sn_violations));
  need(len_violations == 0 && payload_violations == 0, "payload/length violations");
  need(d.delivered_frames == delivered, "hardware delivered counter mismatch");
  need(d.delivered_frames + d.rejected_frames == 2 * total, "descriptor accounting mismatch");
  std::cout << "PASS longrun frames=" << total << " per_vl=" << g_frames_per_vl << " cycles=" << (cycles - start)
            << " delivered=" << delivered << " rejected=" << d.rejected_frames << " full_drops=" << fd
            << " bad_fcs=" << bf << " max_delta=" << max_delta << " checks=" << checks << std::endl;
  std::ofstream out(argv[2]);
  out << "{\"status\":\"PASS\",\"vl_count\":" << VLS << ",\"frames_per_vl\":" << g_frames_per_vl
      << ",\"frames_sent\":" << total << ",\"delivered\":" << delivered
      << ",\"rejected_duplicates\":" << d.rejected_frames << ",\"full_drops\":" << fd
      << ",\"bad_fcs\":" << bf << ",\"max_sfd_time_delta\":" << max_delta
      << ",\"sn_continuity_violations\":" << sn_violations
      << ",\"len_violations\":" << len_violations << ",\"payload_violations\":" << payload_violations
      << ",\"unexpected_rejects\":" << unexpected_rejects << ",\"hang\":false"
      << ",\"cycles\":" << (cycles - start) << ",\"checks\":" << checks
      << ",\"bag_cycles\":" << BAG_CYCLES << ",\"payload_lengths\":[1,17,63,1471]}\n";
  return 0;
 } catch (const std::exception &e) {
  std::cerr << "FAIL longrun " << e.what() << " cycles=" << cycles << " delivered=" << delivered << std::endl;
  return 1;
 }
}
