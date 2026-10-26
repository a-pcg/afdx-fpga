`timescale 1ns/1ps
// IEEE 802.3 reflected CRC32 accumulator, one accepted octet per cycle.
// MAC caller owns framing/handshake. enable means a byte was accepted.
// start initializes a new frame; start+enable includes data as its first byte.
// fcs is complemented state. Transmit fcs[7:0] first, then [15:8], etc.
// RX residue_valid is meaningful only after the final FCS byte was accepted.
module afdx_eth_crc32 (
    input wire clk,
    input wire reset,
    input wire start,
    input wire enable,
    input wire [7:0] data,
    output wire [31:0] fcs,
    output wire residue_valid
);
    reg [31:0] crc_state;
    reg [31:0] crc_next;
    integer bit_index;

    always @* begin
        crc_next = (start ? 32'hffffffff : crc_state) ^ {24'd0, data};
        for (bit_index = 0; bit_index < 8; bit_index = bit_index + 1) begin
            if (crc_next[0])
                crc_next = (crc_next >> 1) ^ 32'hedb88320;
            else
                crc_next = crc_next >> 1;
        end
    end

    always @(posedge clk) begin
        if (reset)
            crc_state <= 32'hffffffff;
        else if (enable)
            crc_state <= crc_next;
        else if (start)
            crc_state <= 32'hffffffff;
    end

    assign fcs = ~crc_state;
    assign residue_valid = (crc_state == 32'hdebb20e3);
endmodule
