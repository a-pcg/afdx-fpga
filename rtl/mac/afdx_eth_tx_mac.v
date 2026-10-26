`timescale 1ns/1ps
// Store-and-forward byte-level TX MAC for the competition simulation boundary.
// Client supplies DA through final AFDX SN (60..1514 octets), excluding FCS.
// Caller already pads before SN; this MAC rejects short client frames.
// Physical byte observer samples line_data iff line_byte_ce && line_valid.
// One byte opportunity every BYTE_CYCLES core clocks. At 100 MHz and 8 cycles
// this represents 100 Mbps. Output has no ready and cannot be paused in-frame.
// This is a byte-level MAC boundary, not a PHY/MII electrical implementation.
module afdx_eth_tx_mac #(
    parameter BYTE_CYCLES = 8
)(
    input wire clk,
    input wire reset,
    input wire [7:0] s_data,
    input wire s_valid,
    output wire s_ready,
    input wire s_sop,
    input wire s_eop,
    output reg drop,
    output reg done,
    output wire [7:0] line_data,
    output wire line_valid,
    output wire line_byte_ce,
    output wire line_sop,
    output wire line_eop
);
    localparam CAPTURE = 3'd0, PREAMBLE = 3'd1, BODY = 3'd2,
               FCS = 3'd3, GAP = 3'd4;
    reg [2:0] state;
    reg [7:0] memory [0:1513];
    reg [10:0] wr_count;
    reg [10:0] frame_len;
    reg [10:0] read_index;
    reg bad_frame;
    reg [3:0] byte_index;
    reg [31:0] phase;
    reg [7:0] read_byte;
    wire [31:0] fcs;
    wire crc_start;
    wire crc_enable;

    assign s_ready = (state == CAPTURE) && !reset;
    assign line_byte_ce = (phase == BYTE_CYCLES - 1) && !reset;
    assign line_valid = ((state == PREAMBLE) || (state == BODY) || (state == FCS)) && !reset;
    assign line_sop = (state == PREAMBLE) && (byte_index == 0);
    assign line_eop = (state == FCS) && (byte_index == 3);
    assign crc_start = (state == PREAMBLE) && (byte_index == 7) && line_byte_ce;
    assign crc_enable = (state == BODY) && line_byte_ce;

    afdx_eth_crc32 crc (
        .clk(clk), .reset(reset), .start(crc_start), .enable(crc_enable),
        .data(read_byte), .fcs(fcs), .residue_valid()
    );

    // Explicit synchronous RAM read. Each physical-byte slot has eight core
    // clocks by default, so the next octet is prefetched before byte_ce.
    // BYTE_CYCLES must be >=2; a one-clock slot needs a separate RAM pipeline.
    always @(posedge clk) read_byte<=memory[read_index];

    reg [7:0] selected_byte;
    always @* begin
        selected_byte = 8'd0;
        case (state)
            PREAMBLE: selected_byte = (byte_index == 7) ? 8'hd5 : 8'h55;
            BODY: selected_byte = read_byte;
            FCS: case (byte_index)
                0: selected_byte = fcs[7:0];
                1: selected_byte = fcs[15:8];
                2: selected_byte = fcs[23:16];
                default: selected_byte = fcs[31:24];
            endcase
            default: selected_byte = 8'd0;
        endcase
    end
    assign line_data = selected_byte;

    always @(posedge clk) begin
        if (reset) begin
            state <= CAPTURE;
            wr_count <= 0;
            frame_len <= 0;
            read_index <= 0;
            bad_frame <= 0;
            byte_index <= 0;
            phase <= 0;
            drop <= 0;
            done <= 0;
        end else begin
            drop <= 0;
            done <= 0;
            if (phase == BYTE_CYCLES - 1)
                phase <= 0;
            else
                phase <= phase + 32'd1;

            case (state)
                CAPTURE: if (s_valid) begin
                    if (wr_count < 1514)
                        memory[wr_count] <= s_data;
                    if ((wr_count == 0 && !s_sop) || (wr_count != 0 && s_sop) || wr_count >= 1514)
                        bad_frame <= 1;
                    if (s_eop) begin
                        wr_count <= 0;
                        bad_frame <= 0;
                        if (bad_frame || (wr_count == 0 && !s_sop) ||
                            (wr_count != 0 && s_sop) || wr_count < 59 || wr_count >= 1514) begin
                            drop <= 1;
                        end else begin
                            frame_len <= wr_count + 11'd1;
                            read_index <= 0;
                            byte_index <= 0;
                            // Enter active states exactly at a byte boundary.
                            phase <= 0;
                            state <= PREAMBLE;
                        end
                    end else if (wr_count < 1514) begin
                        wr_count <= wr_count + 11'd1;
                    end
                end
                PREAMBLE: if (line_byte_ce) begin
                    if (byte_index == 7) begin
                        byte_index <= 0;
                        state <= BODY;
                    end else begin
                        byte_index <= byte_index + 4'd1;
                    end
                end
                BODY: if (line_byte_ce) begin
                    if (read_index + 11'd1 == frame_len) begin
                        byte_index <= 0;
                        state <= FCS;
                    end else begin
                        read_index <= read_index + 11'd1;
                    end
                end
                FCS: if (line_byte_ce) begin
                    if (byte_index == 3) begin
                        done <= 1;
                        byte_index <= 0;
                        state <= GAP;
                    end else begin
                        byte_index <= byte_index + 4'd1;
                    end
                end
                GAP: if (line_byte_ce) begin
                    if (byte_index == 11) begin
                        byte_index <= 0;
                        state <= CAPTURE;
                    end else begin
                        byte_index <= byte_index + 4'd1;
                    end
                end
                default: begin
                    state <= CAPTURE;
                    wr_count <= 0;
                    bad_frame <= 0;
                    byte_index <= 0;
                end
            endcase
        end
    end
endmodule
