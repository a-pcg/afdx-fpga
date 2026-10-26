#include "Vafdx_packet_pool.h"
#include "verilated.h"
#include <algorithm>
#include <cstdint>
#include <fstream>
#include <iostream>
#include <iterator>
#include <random>
#include <set>
#include <stdexcept>
#include <string>
#include <vector>
// T8-C allocation-scan stress test for the three-stage generation
// read-modify-write pipeline. Covers:
//   A) fill the whole pool, ascending round-robin order from slot 0, immediate
//      writable ownership for every freshly returned tag, NO_SPACE without a
//      scan, previous-generation tags rejected, publish seals writes;
//   B) generation uniqueness across reuse rounds (reuses the same slot and
//      checks the committed generation incremented by exactly one; the
//      TAG_WIDTH=8 build wraps the 5-bit generation several times);
//   C) sparse release + round-robin reuse order after wrap-around;
//   D) response backpressure: the write-back is committed before rsp_valid is
//      visible (predicted new tag is rejected while scanning and accepted while
//      the response is still held), response fields stable, cmd_ready low;
//   E) reset during the scan/commit pipeline and during a held response;
//   F) randomized allocate/publish/release/stale-tag stress with a reference
//      round-robin + generation model.
// The +2 clock allocation latency is a deliberate T8 semantic and is asserted
// explicitly (minimum 3 ticks from command capture, worst case slots+2).
static Vafdx_packet_pool d;
static std::mt19937 rng(20261020);
static unsigned slots = 0, slotw = 0, genw = 0;
static std::uint32_t tagmask = 0, slotmask = 0, genmask = 0;
static std::vector<unsigned> gen;   // committed generation per slot
static std::vector<bool> owned;
static unsigned rr = 0;             // reference round-robin pointer
static std::set<std::uint32_t> live;
static unsigned long long checks = 0, cycles = 0, commands = 0;
static unsigned max_alloc_latency = 0, max_guard_ticks = 0;
static void need(bool ok, const std::string& what) { ++checks; if (!ok) throw std::runtime_error(what); }
static std::string model_dump() {
    std::string s = "free=" + std::to_string((unsigned)d.free_count) + " live=" + std::to_string(live.size()) +
                    " rr=" + std::to_string(rr) + " owned=";
    for (unsigned i = 0; i < slots; ++i) s.push_back(owned[i] ? '1' : '0');
    s += " gen=";
    for (unsigned i = 0; i < slots; ++i) s += std::to_string(gen[i]) + " ";
    return s;
}
static void tick() { d.clk = 0; d.eval(); d.clk = 1; d.eval(); ++cycles; }
template <typename T> static void setp(T& port, std::uint32_t value) { port = (T)(value & tagmask); }
template <typename T> static void setraw(T& port, std::uint32_t value) { port = (T)value; }
template <typename T> static std::uint32_t getp(const T& port) { return (std::uint32_t)port; }
static std::uint32_t mktag(unsigned slot, unsigned genv) { return (((std::uint32_t)genv) << slotw) | slot; }
static std::uint32_t curtag(unsigned slot) { return mktag(slot, gen[slot]); }
static void set_offset(std::uint32_t value) { setraw(d.wr_offset, value & 0x7ffu); }
static void reset() {
    d.reset = 1; d.cmd_valid = 0; d.rsp_ready = 0; d.wr_valid = 0; d.rd_valid = 0; d.data_ready = 0;
    tick(); tick(); d.reset = 0;
    unsigned n = 0;
    while (!d.initialized) {
        need(!d.cmd_ready && !d.wr_ready && !d.rd_ready && !d.data_valid, "access leaked during init");
        tick(); need(++n < slots + 8, "init timeout");
    }
    gen.assign(slots, 0); owned.assign(slots, false); rr = 0; live.clear();
    need((unsigned)d.free_count == slots, "reset did not restore free slots");
    need(d.wr_ready && d.rd_ready, "ports not ready after init");
}
struct Reply { std::uint32_t tag; unsigned status, refs, latency; };
static Reply command(unsigned op, std::uint32_t tag, unsigned refs) {
    need(d.cmd_ready, "command engine busy");
    setraw(d.cmd_op, op); setp(d.cmd_tag, tag);
    setraw(d.cmd_consumers, refs); d.cmd_valid = 1;
    tick(); d.cmd_valid = 0;
    unsigned t = 0;
    while (!d.rsp_valid) {
        need(!d.cmd_ready, "cmd_ready asserted inside a command");
        tick(); need(++t < slots + 8, "command timeout");
    }
    Reply r{getp(d.rsp_tag), (unsigned)d.rsp_status, (unsigned)d.rsp_refs, t};
    for (unsigned n = rng() % 4; n; --n) {
        need(d.rsp_valid && !d.cmd_ready, "response changed under backpressure");
        need(getp(d.rsp_tag) == r.tag && (unsigned)d.rsp_status == r.status && (unsigned)d.rsp_refs == r.refs,
             "response fields drifted under backpressure");
        tick();
    }
    d.rsp_ready = 1; tick(); d.rsp_ready = 0; ++commands;
    return r;
}
static void probe_write(std::uint32_t tag, bool expect_ok) {
    d.wr_valid = 1; setp(d.wr_tag, tag); set_offset(tag * 7u); setraw(d.wr_data, (0xa5u ^ tag) & 0xffu);
    d.clk = 0; d.eval();
    need(d.wr_ready, "write port not ready");
    need(bool(d.wr_error) == !expect_ok, "write ownership check mismatch: tag=" + std::to_string(tag) +
                                        " expect_ok=" + std::to_string((unsigned)expect_ok) + " " + model_dump());
    tick(); d.wr_valid = 0;
}
static unsigned predict_slot() {
    for (unsigned i = 0; i < slots; ++i) { unsigned c = (rr + i) % slots; if (!owned[c]) return c; }
    return slots;
}
static std::uint32_t expect_tag(unsigned slot) { return mktag(slot, (gen[slot] + 1) & genmask); }
static Reply allocate() {
    bool full = live.size() == slots;
    unsigned slot = predict_slot();
    std::uint32_t exp = full ? 0 : expect_tag(slot);
    Reply r = command(0, 0, 0);
    if (full) {
        need(r.status == 2 && r.refs == 0, "full pool must return NO_SPACE");
        need(r.latency == 0, "full-pool response must not start a scan");
        need((unsigned)d.free_count == 0, "free_count changed on full reject");
        return r;
    }
    need(slot < slots, "model considered the pool full");
    need(r.status == 0 && r.refs == 1, "allocation failed with space available");
    need(r.tag == exp, "allocation tag does not match committed generation: tag=" + std::to_string(r.tag) +
                            " exp=" + std::to_string(exp) + " status=" + std::to_string(r.status) +
                            " latency=" + std::to_string(r.latency) + " " + model_dump());
    need((unsigned)(r.tag & slotmask) == slot, "round-robin slot selection mismatch");
    gen[slot] = (gen[slot] + 1) & genmask; owned[slot] = true; live.insert(r.tag);
    rr = (slot + 1) % slots;
    need((unsigned)d.free_count == slots - live.size(), "free_count mismatch after allocation");
    need(r.latency >= 3 && r.latency <= slots + 2, "allocation latency outside the T8 bound");
    max_alloc_latency = std::max(max_alloc_latency, r.latency);
    return r;
}
static void release(std::uint32_t tag) {
    unsigned slot = tag & slotmask;
    bool own = owned[slot] && curtag(slot) == tag;
    Reply r = command(2, tag, 0);
    need(r.status == (own ? 0u : 1u), "release owner/stale-tag mismatch");
    if (own) { need(r.refs == 0, "release reference count mismatch"); owned[slot] = false; live.erase(tag); }
    need((unsigned)d.free_count == slots - live.size(), "free_count mismatch after release");
}
static void publish(std::uint32_t tag, unsigned refs) {
    unsigned slot = tag & slotmask;
    bool own = owned[slot] && curtag(slot) == tag;
    Reply r = command(1, tag, refs);
    need(r.status == (own ? 0u : 1u), "publish owner mismatch");
    if (own) need(r.refs == refs, "publish reference count mismatch");
}
int main(int argc, char** argv) {
    try {
        Verilated::commandArgs(argc, argv);
        need(argc == 5, "usage: slots slot_width gen_width result.json");
        slots = (unsigned)std::stoul(argv[1]); slotw = (unsigned)std::stoul(argv[2]); genw = (unsigned)std::stoul(argv[3]);
        slotmask = slots - 1; genmask = (1u << genw) - 1u;
        tagmask = (slotw + genw >= 32) ? 0xffffffffu : ((1u << (slotw + genw)) - 1u);
        reset();
        // A) fill the pool, ascending from round_robin=0, immediate writable ownership.
        std::vector<std::uint32_t> assigned;
        for (unsigned i = 0; i < slots; ++i) {
            Reply r = allocate();
            need((unsigned)(r.tag & slotmask) == i, "first allocation round not ascending from slot 0");
            probe_write(r.tag, true);
            assigned.push_back(r.tag);
        }
        Reply full = allocate();
        need(full.status == 2, "full pool status mismatch");
        for (unsigned s = 0; s < slots; ++s) probe_write(curtag(s), true);
        for (unsigned s = 0; s < slots; ++s) probe_write(mktag(s, (gen[s] - 1u) & genmask), false);
        for (auto t : assigned) { publish(t, 1); probe_write(t, false); }
        for (auto t : assigned) need(command(1, t, 1).status == 3, "double publish accepted");
        for (auto t : assigned) release(t);
        need((unsigned)d.free_count == slots, "pool not fully free after phase A");

        // B) generation uniqueness/uniform increment across reuse rounds.
        std::vector<std::uint32_t> prev = assigned;
        unsigned rounds = (genw <= 5) ? 40 : 6;
        for (unsigned rd = 0; rd < rounds; ++rd) {
            std::vector<std::uint32_t> round_tags;
            for (unsigned i = 0; i < slots; ++i) { Reply r = allocate(); probe_write(r.tag, true); round_tags.push_back(r.tag); }
            for (unsigned s = 0; s < slots; ++s) {
                need((round_tags[s] & slotmask) == s, "reuse round slot order mismatch");
                need(round_tags[s] != prev[s], "slot reused without changing generation");
            }
            for (auto t : round_tags) publish(t, 1);
            for (unsigned s = 0; s < slots; ++s) {
                probe_write(prev[s], false);
                need(command(2, prev[s], 0).status == 1, "stale release accepted");
            }
            for (auto t : round_tags) release(t);
            prev = round_tags;
        }
        need((unsigned)d.free_count == slots, "generation rounds leaked slots");

        // C) sparse release then round-robin reuse order across wrap-around.
        {
            std::vector<std::uint32_t> all;
            for (unsigned i = 0; i < slots; ++i) all.push_back(allocate().tag);
            unsigned rel[3] = {slots / 2, slots - 1, 0};
            for (unsigned k = 0; k < 3; ++k) release(all[rel[k]]);
            for (unsigned k = 0; k < 3; ++k) {
                unsigned want = predict_slot();
                Reply r = allocate();
                need((unsigned)(r.tag & slotmask) == want, "sparse reuse did not follow round-robin");
            }
            for (unsigned s = 0; s < slots; ++s) if (owned[s]) release(curtag(s));
            need((unsigned)d.free_count == slots, "sparse round-robin leaked slots");
        }

        // D) response backpressure with the write-back ordering probe.
        {
            unsigned slot = predict_slot();
            std::uint32_t exp = expect_tag(slot);
            need(d.cmd_ready, "engine not idle before ordering probe");
            d.cmd_op = 0; setp(d.cmd_tag, 0); d.cmd_consumers = 0; d.cmd_valid = 1;
            tick(); d.cmd_valid = 0;
            unsigned guard = 0;
            while (!d.rsp_valid) {
                need(!d.cmd_ready, "cmd_ready during ordering probe");
                probe_write(exp, false);   // new tag must not be owned before the write-back
                need(++guard < slots + 8, "ordering probe timeout");
            }
            max_guard_ticks = std::max(max_guard_ticks, guard);
            need(guard >= 3, "T8 commit pipeline shorter than expected");
            probe_write(exp, true);        // committed before the visible response
            for (unsigned n = 1 + rng() % 6; n; --n) {
                need(d.rsp_valid && !d.cmd_ready, "response changed while held");
                need(getp(d.rsp_tag) == exp && (unsigned)d.rsp_status == 0 && (unsigned)d.rsp_refs == 1,
                     "held response fields drifted");
                tick();
            }
            need(getp(d.rsp_tag) == exp, "response tag changed under backpressure");
            d.rsp_ready = 1; tick(); d.rsp_ready = 0;
            gen[slot] = (gen[slot] + 1) & genmask; owned[slot] = true; live.insert(exp);
            rr = (slot + 1) % slots;
            need((unsigned)d.free_count == slots - live.size(), "free_count mismatch after held response");
            publish(exp, 1); release(exp);
            need((unsigned)d.free_count == slots, "ordering probe leaked slots");
        }

        // E) reset during the scan/commit pipeline and while a response is held.
        for (unsigned delay = 0; delay <= 3; ++delay) {
            need(d.cmd_ready, "engine not idle before reset test");
            d.cmd_op = 0; setp(d.cmd_tag, 0); d.cmd_consumers = 0; d.cmd_valid = 1;
            tick(); d.cmd_valid = 0;
            for (unsigned k = 0; k < delay; ++k) tick();
            reset();
            need(!d.rsp_valid, "stale response after reset during allocation");
            need((unsigned)d.free_count == slots, "free count not restored after mid-allocation reset");
            probe_write(mktag(0, 1), false);
            need(command(2, mktag(0, 1), 0).status == 1, "stale release accepted after mid-allocation reset");
            Reply r = allocate();
            need((unsigned)(r.tag & slotmask) == 0 && (unsigned)(r.tag >> slotw) == 1,
                 "post-reset allocation did not restart generation 1");
            release(r.tag);
        }
        {
            need(d.cmd_ready, "engine not idle before held-response reset");
            d.cmd_op = 0; setp(d.cmd_tag, 0); d.cmd_consumers = 0; d.cmd_valid = 1;
            tick(); d.cmd_valid = 0;
            unsigned t = 0;
            while (!d.rsp_valid) { tick(); need(++t < slots + 8, "held-response reset timeout"); }
            for (unsigned k = 0; k < 3; ++k) tick();
            need(d.rsp_valid, "response not held before reset");
            reset();
            need(!d.rsp_valid, "reset did not cancel the held allocation response");
            need(d.cmd_ready, "engine did not return to idle after held-response reset");
            need((unsigned)d.free_count == slots, "held-response reset leaked slots");
        }

        // F) randomized lifecycle with the reference round-robin/generation model.
        for (unsigned i = 0; i < 4000; ++i) {
            unsigned op = rng() % 10;
            if (live.empty() || op < 5) { allocate(); continue; }
            if (op < 7) {
                auto it = live.begin(); std::advance(it, (std::size_t)(rng() % live.size()));
                release(*it);
            } else if (op < 9) {
                auto it = live.begin(); std::advance(it, (std::size_t)(rng() % live.size()));
                std::uint32_t tag = *it;
                publish(tag, 1); probe_write(tag, false);
                need(command(1, tag, 1).status == 3, "double publish accepted");
                release(tag);
            } else {
                unsigned s = rng() % slots;
                std::uint32_t stale = mktag(s, (gen[s] - 1u) & genmask);
                need(command(2, stale, 0).status == 1, "stale release accepted in random phase");
                probe_write(stale, false);
            }
            need((unsigned)d.free_count == slots - live.size(), "random phase conservation mismatch");
        }
        while (!live.empty()) release(*live.begin());
        need((unsigned)d.free_count == slots, "final pool not fully free");

        // G) final reset and conservation.
        reset();
        need((unsigned)d.free_count == slots, "final reset did not restore free slots");
        d.final();
        std::ofstream out(argv[4]);
        out << "{\"status\":\"PASS\",\"slots\":" << slots << ",\"slot_width\":" << slotw
            << ",\"gen_width\":" << genw << ",\"tag_width\":" << (slotw + genw)
            << ",\"checks\":" << checks << ",\"cycles\":" << cycles << ",\"commands\":" << commands
            << ",\"max_alloc_latency\":" << max_alloc_latency << ",\"ordering_guard_ticks\":" << max_guard_ticks << "}\n";
        std::cout << "PASS pool alloc stress slots=" << slots << " gen_width=" << genw << " checks=" << checks << "\n";
        return 0;
    } catch (const std::exception& e) {
        std::cerr << "FAIL " << e.what() << " cycles=" << cycles << " commands=" << commands << "\n";
        return 1;
    }
}
