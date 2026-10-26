// a615a_agent_test.cpp -- self-checking byte-stream client model for
// afdx_a615a_agent (ARINC 615A minimal data-loading subset).
//
// The harness drives/observes the UDP-payload byte streams directly and models
// the loader/target peer: Find / Download / Upload, TFTP-style DATA/ACK blocks,
// ACK-loss retransmission, retry-limit abort, duplicate DATA, error codes and
// the status/counter read port.
//
// PASS text: TEST_PASS tb_a615a_agent checks=N rx_pkts=.. tx_pkts=.. file_len=700
#include "Vafdx_a615a_agent.h"
#include "verilated.h"

#include <cstdint>
#include <cstdio>
#include <deque>
#include <stdexcept>
#include <string>
#include <vector>

using Bytes = std::vector<std::uint8_t>;

namespace {

Vafdx_a615a_agent* dut = nullptr;
std::deque<std::uint8_t> txq;
bool next_sop = true;
bool rx_done = false;
bool rx_active = false;
Bytes rxbuf;
unsigned long long cycles = 0;
unsigned long long sent_pkts = 0;
unsigned long long recv_pkts = 0;
unsigned checks = 0;

struct Fail : std::runtime_error {
  explicit Fail(const std::string& m) : std::runtime_error(m) {}
};

void expect(bool ok, const std::string& msg) {
  ++checks;
  if (!ok) throw Fail(msg);
}

void tick_once() {
  dut->clk = 0;
  dut->eval();
  // outbound: the byte offered during this cycle (consumed at the posedge)
  if (dut->m_valid) {
    if (dut->m_sop) {
      rx_active = true;
      rxbuf.clear();
    }
    if (rx_active) rxbuf.push_back(static_cast<std::uint8_t>(dut->m_data));
    if (dut->m_eop) rx_done = true;
  }
  // inbound: hold the queue head while the agent is not ready
  const bool send = !txq.empty() && (dut->s_ready != 0);
  dut->s_valid = send ? 1 : 0;
  dut->s_data = send ? txq.front() : 0;
  dut->s_sop = (send && next_sop) ? 1 : 0;
  dut->s_eop = (send && txq.size() == 1) ? 1 : 0;
  dut->eval();
  dut->clk = 1;
  dut->eval();
  if (send) {
    txq.pop_front();
    if (next_sop) next_sop = false;
  }
  dut->clk = 0;
  dut->eval();
  ++cycles;
}

void send_packet(const Bytes& pkt) {
  if (!txq.empty()) throw Fail("send_packet with a pending packet");
  for (std::uint8_t b : pkt) txq.push_back(b);
  next_sop = true;
  unsigned long long guard = 0;
  while (!txq.empty()) {
    tick_once();
    if (++guard > 2000000ULL) throw Fail("inbound byte-stream stuck");
  }
  ++sent_pkts;
}

Bytes recv_packet(unsigned long long timeout = 20000) {
  rx_done = false;
  rx_active = false;
  rxbuf.clear();
  unsigned long long waited = 0;
  while (!rx_done) {
    tick_once();
    if (++waited > timeout) throw Fail("outbound packet timeout");
  }
  ++recv_pkts;
  return rxbuf;
}

std::uint32_t status(std::uint8_t addr) {
  dut->st_addr = addr & 0x3F;
  dut->eval();
  return static_cast<std::uint32_t>(dut->st_rdata);
}

void do_reset() {
  dut->reset = 1;
  for (int i = 0; i < 6; ++i) tick_once();
  dut->reset = 0;
  for (int i = 0; i < 3; ++i) tick_once();
}

Bytes find_req() { return Bytes{0x01}; }

Bytes download_req(std::uint8_t user, const std::string& name) {
  Bytes b{0x03, user, static_cast<std::uint8_t>(name.size())};
  b.insert(b.end(), name.begin(), name.end());
  return b;
}

Bytes upload_req(std::uint8_t user, const std::string& name) {
  Bytes b{0x04, user, static_cast<std::uint8_t>(name.size())};
  b.insert(b.end(), name.begin(), name.end());
  return b;
}

Bytes ack_msg(std::uint16_t seq) {
  return Bytes{0x05, static_cast<std::uint8_t>(seq >> 8),
               static_cast<std::uint8_t>(seq & 0xFF)};
}

Bytes data_msg(std::uint16_t seq, const Bytes& data) {
  const std::uint16_t n = static_cast<std::uint16_t>(data.size());
  Bytes b{0x07, static_cast<std::uint8_t>(seq >> 8),
          static_cast<std::uint8_t>(seq & 0xFF),
          static_cast<std::uint8_t>(n >> 8), static_cast<std::uint8_t>(n & 0xFF)};
  b.insert(b.end(), data.begin(), data.end());
  return b;
}

void expect_ack(const Bytes& pkt, std::uint16_t seq, const char* what) {
  expect(pkt.size() == 3, std::string(what) + ": ACK length");
  expect(pkt[0] == 0x05, std::string(what) + ": ACK opcode");
  const std::uint16_t got = (static_cast<std::uint16_t>(pkt[1]) << 8) | pkt[2];
  expect(got == seq, std::string(what) + ": ACK sequence");
}

void expect_error(const Bytes& pkt, std::uint8_t err, const char* what) {
  expect(pkt.size() == 4, std::string(what) + ": ERROR length");
  expect(pkt[0] == 0x06, std::string(what) + ": ERROR opcode");
  expect(pkt[1] == err, std::string(what) + ": ERROR code");
}

void expect_data(const Bytes& pkt, std::uint16_t seq, std::size_t len,
                 const char* what) {
  expect(pkt.size() == 5 + len, std::string(what) + ": DATA length");
  expect(pkt[0] == 0x07, std::string(what) + ": DATA opcode");
  const std::uint16_t got_seq = (static_cast<std::uint16_t>(pkt[1]) << 8) | pkt[2];
  const std::uint16_t got_len = (static_cast<std::uint16_t>(pkt[3]) << 8) | pkt[4];
  expect(got_seq == seq, std::string(what) + ": DATA sequence");
  expect(got_len == len, std::string(what) + ": DATA length field");
}

void expect_payload(const Bytes& pkt, const Bytes& data, const char* what) {
  expect(pkt.size() == 5 + data.size(), std::string(what) + ": payload size");
  for (std::size_t i = 0; i < data.size(); ++i)
    expect(pkt[5 + i] == data[i], std::string(what) + ": payload byte " +
                                     std::to_string(i));
}

Bytes pattern(std::size_t n, unsigned seed) {
  Bytes b(n);
  for (std::size_t i = 0; i < n; ++i)
    b[i] = static_cast<std::uint8_t>((i * 7u + seed * 13u + 3u) & 0xFFu);
  return b;
}

const char* NAME = "afdx_cfg.bin";  // 12 bytes

}  // namespace

int main(int argc, char** argv) {
  try {
    Verilated::commandArgs(argc, argv);
    dut = new Vafdx_a615a_agent;
    dut->reset = 1;
    dut->s_valid = 0;
    dut->s_data = 0;
    dut->s_sop = 0;
    dut->s_eop = 0;
    dut->m_ready = 1;
    dut->ack_timeout = 100;
    dut->max_retries = 3;
    dut->write_protect = 0;
    dut->st_addr = 0;
    do_reset();

    // ---- identity / reset state -------------------------------------
    expect(status(0x0F) == 0x00010000u, "version register");
    expect(status(0x00) == 0u, "idle status after reset");
    expect(status(0x01) == 0u, "file_len after reset");
    expect(dut->file_present == 0, "file_present after reset");

    // ---- FIND on an empty store -------------------------------------
    send_packet(find_req());
    Bytes r = recv_packet();
    expect(r.size() == 4 && r[0] == 0x02 && r[1] == 0x00 && r[2] == 0x00 &&
               r[3] == 0x00,
           "FIND empty response");

    // ---- UPLOAD with no file -> 1 File not found --------------------
    send_packet(upload_req(1, NAME));
    expect_error(recv_packet(), 1, "UPLOAD no file");

    // ---- DOWNLOAD with a bad user id -> 8 No such user --------------
    send_packet(download_req(5, NAME));
    expect_error(recv_packet(), 8, "DOWNLOAD bad user");

    // ---- DOWNLOAD with write protection -> 2 Access violation -------
    dut->write_protect = 1;
    send_packet(download_req(1, NAME));
    expect_error(recv_packet(), 2, "DOWNLOAD write protected");
    dut->write_protect = 0;

    // ---- DOWNLOAD 700 bytes (2 blocks), duplicate block 1 -----------
    const Bytes d1 = pattern(512, 1);
    const Bytes d2 = pattern(188, 2);
    send_packet(download_req(1, NAME));
    expect_ack(recv_packet(), 0, "DOWNLOAD accept");
    send_packet(data_msg(1, d1));
    expect_ack(recv_packet(), 1, "DOWNLOAD block 1");
    // duplicate DATA for block 1 with different bytes: only re-ACK, no rewrite
    send_packet(data_msg(1, pattern(512, 99)));
    expect_ack(recv_packet(), 1, "DOWNLOAD duplicate block 1");
    send_packet(data_msg(2, d2));
    expect_ack(recv_packet(), 2, "DOWNLOAD block 2 (final)");
    for (int i = 0; i < 10; ++i) tick_once();
    expect(dut->file_present == 1, "file present after download");
    expect(dut->file_len == 700, "file length after download");
    expect(status(0x07) == 2u, "blocks_written after download");
    expect(status(0x0A) == 1u, "duplicate DATA counter");

    // ---- second DOWNLOAD -> 6 File exists ---------------------------
    send_packet(download_req(1, NAME));
    expect_error(recv_packet(), 6, "DOWNLOAD file exists");

    // ---- UPLOAD with a wrong name -> 1 File not found ---------------
    send_packet(upload_req(1, "other.bin"));
    expect_error(recv_packet(), 1, "UPLOAD wrong name");

    // ---- UPLOAD with one dropped ACK: retransmission ----------------
    send_packet(upload_req(1, NAME));
    expect_ack(recv_packet(), 0, "UPLOAD accept");
    Bytes b1 = recv_packet();
    expect_data(b1, 1, 512, "UPLOAD block 1");
    expect_payload(b1, d1, "UPLOAD block 1");
    // deliberately drop the ACK for block 1; the agent must retransmit it
    Bytes b1r = recv_packet(50000);
    expect_data(b1r, 1, 512, "UPLOAD retransmitted block 1");
    expect_payload(b1r, d1, "UPLOAD retransmitted block 1");
    send_packet(ack_msg(1));
    Bytes b2 = recv_packet();
    expect_data(b2, 2, 188, "UPLOAD block 2");
    expect_payload(b2, d2, "UPLOAD block 2");
    send_packet(ack_msg(2));
    for (int i = 0; i < 20; ++i) tick_once();
    expect((status(0x00) & 0x100u) == 0u, "idle after upload");
    expect(status(0x09) == 1u, "retransmit counter after ACK loss");
    expect(status(0x0B) == 1u, "timeout counter after ACK loss");

    // ---- UPLOAD with every ACK dropped: retry-limit abort -----------
    send_packet(upload_req(1, NAME));
    expect_ack(recv_packet(), 0, "UPLOAD abort accept");
    Bytes b1b = recv_packet();
    expect_data(b1b, 1, 512, "UPLOAD abort block 1");
    for (int k = 0; k < 3; ++k) {
      Bytes rr = recv_packet(50000);
      expect_data(rr, 1, 512, "UPLOAD abort retransmission");
      expect_payload(rr, d1, "UPLOAD abort retransmission");
    }
    Bytes ab = recv_packet(50000);
    expect(ab.size() == 4 && ab[0] == 0x06, "abort ERROR opcode/length");
    expect(ab[1] == 0x0A, "abort ERROR code 0x0A");
    expect(((static_cast<std::uint16_t>(ab[2]) << 8) | ab[3]) == 1u,
           "abort ERROR sequence");
    for (int i = 0; i < 10; ++i) tick_once();
    expect((status(0x00) & 0x100u) == 0u, "idle after abort");
    expect(status(0x0C) == 1u, "retry abort counter");
    expect(status(0x09) == 4u, "retransmit counter total");
    expect(status(0x0B) == 5u, "timeout counter total");

    // ---- unknown opcode -> 4 Illegal operation ----------------------
    send_packet(Bytes{0x7F});
    expect_error(recv_packet(), 4, "unknown opcode");

    // ---- FIND with a file present -----------------------------------
    send_packet(find_req());
    r = recv_packet();
    expect(r.size() == 4 + 12, "FIND present length");
    expect(r[0] == 0x02 && r[1] == 0x00, "FIND present opcode/status");
    expect(r[2] == 0x01, "FIND present flag");
    expect(r[3] == 12, "FIND name length");
    for (std::size_t i = 0; i < 12; ++i)
      expect(r[4 + i] == static_cast<std::uint8_t>(NAME[i]), "FIND name byte");
    expect(status(0x04) == 2u, "find request counter");

    // ---- malformed DOWNLOAD header -> 4 Illegal operation -----------
    send_packet(Bytes{0x03, 0x01, 0x05, 'a', 'b'});
    expect_error(recv_packet(), 4, "malformed download");

    // ---- final counters ---------------------------------------------
    expect(status(0x02) == static_cast<std::uint32_t>(sent_pkts),
           "rx_packets counter");
    expect(status(0x03) == static_cast<std::uint32_t>(recv_pkts),
           "tx_packets counter");
    expect(status(0x05) == 5u, "download request counter");
    expect(status(0x06) == 4u, "upload request counter");
    expect(status(0x08) == 3u, "blocks_sent counter");
    expect(status(0x0D) == 8u, "error response counter");
    expect(status(0x0E) == 4u, "last error code");
    expect(status(0x01) == 700u, "final file_len");
    expect((status(0x00) & 0x100u) == 0u, "final idle");

    std::printf(
        "TEST_PASS tb_a615a_agent checks=%u rx_pkts=%llu tx_pkts=%llu "
        "file_len=%u cycles=%llu\n",
        checks, sent_pkts, recv_pkts, static_cast<unsigned>(dut->file_len),
        cycles);
    dut->final();
    delete dut;
    return 0;
  } catch (const std::exception& e) {
    std::printf("TEST_FAIL tb_a615a_agent: %s (cycles=%llu)\n", e.what(),
                cycles);
    return 1;
  }
}
