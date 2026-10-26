#include "Vafdx_eth_crc32.h"
#include "verilated.h"
#include <cstdint>
#include <fstream>
#include <iostream>
#include <random>
#include <stdexcept>
#include <string>
#include <vector>

static Vafdx_eth_crc32 dut;
static std::uint64_t checks = 0;
static std::mt19937 rng(20261017);
static void need(bool ok, const std::string& text) {
    ++checks;
    if (!ok) throw std::runtime_error(text);
}
static void tick() {
    dut.clk = 0; dut.eval(); dut.clk = 1; dut.eval();
}
static void idle() {
    dut.start = 0; dut.enable = 0;
    auto saved = dut.fcs;
    dut.data = static_cast<unsigned char>(rng());
    tick(); need(dut.fcs == saved, "CRC changed without an accepted byte");
}
static void byte(unsigned char value, bool start = false) {
    dut.data = value; dut.enable = 1; dut.start = start; tick();
    dut.enable = 0; dut.start = 0;
}
static std::vector<unsigned char> decode(const std::string& hex) {
    std::vector<unsigned char> value;
    if (hex == "-") return value;
    for (size_t i=0; i<hex.size(); i+=2)
        value.push_back(static_cast<unsigned char>(std::stoul(hex.substr(i,2), nullptr, 16)));
    return value;
}
int main(int argc, char** argv) {
    try {
        Verilated::commandArgs(argc, argv);
        dut.reset = 1; dut.enable = 0; dut.start = 0; tick();
        dut.reset = 0; need(dut.fcs == 0, "reset FCS must be zero");
        std::ifstream vectors("vectors/mac_crc/cases.txt");
        need(vectors.good(), "golden CRC vectors missing");
        std::string encoded, crc_hex; unsigned frames=0;
        while (vectors >> encoded >> crc_hex) {
            auto payload = decode(encoded);
            auto expected = static_cast<std::uint32_t>(std::stoul(crc_hex, nullptr, 16));
            // Alternate separate start and start coincident with first octet.
            if (frames%2 == 0 || payload.empty()) {
                dut.start=1; dut.enable=0; tick(); dut.start=0;
                need(dut.fcs==0, "start did not reset state");
            }
            for (size_t i=0; i<payload.size(); ++i) {
                if (i>0 && rng()%4 == 0) idle();
                byte(payload[i], i==0 && frames%2==1);
            }
            need(dut.fcs == expected, "golden CRC mismatch frame " + std::to_string(frames));
            // A complete frame followed by little-endian FCS has fixed residue.
            for (unsigned shift=0; shift<32; shift+=8)
                byte(static_cast<unsigned char>(expected >> shift));
            need(dut.residue_valid, "valid frame FCS residue rejected");
            // Re-run and corrupt one FCS bit; residue must fail.
            dut.start=1; dut.enable=0; tick(); dut.start=0;
            for (auto value : payload) byte(value);
            for (unsigned shift=0; shift<32; shift+=8)
                byte(static_cast<unsigned char>((expected >> shift) ^ (shift==0 ? 1u : 0u)));
            need(!dut.residue_valid, "corrupted FCS accepted");
            idle();
            ++frames;
        }
        need(frames >= 500, "incomplete golden CRC corpus");
        // reset wins over simultaneous start and enable and discards the byte.
        dut.reset=1; dut.start=1; dut.enable=1; dut.data=0x55; tick();
        need(dut.fcs==0 && !dut.residue_valid, "reset priority is wrong");
        dut.reset=0; dut.start=0; dut.enable=0; idle();
        dut.final();
        std::ofstream result("reports/mac_crc/result.json");
        result << "{\"status\":\"PASS\",\"frames\":" << frames
               << ",\"checks\":" << checks << "}\n";
        std::cout << "PASS frames=" << frames << " checks=" << checks << "\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "FAIL " << error.what() << "\n";
        return 1;
    }
}
