`timescale 1ns/1ps
// Metadata IC: one independent previous-SN state per network and configured VL.
// Profile: XAPP1130 p9; first valid frame, SN=0, next SN or next+1 pass.
// Every basic-valid observed frame updates PSN, including sequence rejects.
// Bad MAC/structure frames do NOT update PSN. VL lookup precedes this engine.
// VL count is 2**VL_WIDTH. Sequential startup clearing keeps state in RAM.
// Decisions carry a buffer tag; this module does not move payload bytes.
module afdx_integrity_checker #(
    parameter VL_WIDTH=3,
    parameter TAG_WIDTH=16
)(
    input wire clk,
    input wire reset,
    input wire s_valid,
    output wire s_ready,
    input wire [VL_WIDTH-1:0] s_vl,
    input wire s_network,
    input wire [7:0] s_sn,
    input wire s_frame_good,
    input wire s_ic_enable,
    input wire [TAG_WIDTH-1:0] s_tag,
    output wire m_valid,
    input wire m_ready,
    output wire [VL_WIDTH-1:0] m_vl,
    output wire m_network,
    output wire [7:0] m_sn,
    output wire [TAG_WIDTH-1:0] m_tag,
    output reg m_pass,
    output reg [1:0] m_reason,
    output wire initialized
);
    localparam INIT=3'd0, IDLE=3'd1, LOOKUP=3'd2, DECIDE=3'd3, OUTPUT=3'd4;
    localparam ENTRY_COUNT=(2 << VL_WIDTH);
    reg [2:0] state;
    reg [8:0] entries [0:ENTRY_COUNT-1];
    reg [VL_WIDTH:0] init_index;
    reg [VL_WIDTH:0] lookup_address;
    reg [8:0] lookup_value;
    reg [VL_WIDTH-1:0] pending_vl;
    reg pending_network;
    reg [7:0] pending_sn;
    reg pending_good, pending_enable;
    reg [TAG_WIDTH-1:0] pending_tag;

    function [7:0] successor;
        input [7:0] value;
        begin successor=(value==8'd255) ? 8'd1 : value+8'd1; end
    endfunction
    wire sequence_ok = !lookup_value[8] || pending_sn==0 ||
                       pending_sn==successor(lookup_value[7:0]) ||
                       pending_sn==successor(successor(lookup_value[7:0]));
    assign s_ready = state==IDLE && !reset;
    assign m_valid = state==OUTPUT && !reset;
    assign initialized = state!=INIT && !reset;
    assign m_vl=pending_vl;
    assign m_network=pending_network;
    assign m_sn=pending_sn;
    assign m_tag=pending_tag;

    always @(posedge clk) lookup_value<=entries[lookup_address];

    always @(posedge clk) begin
        if (reset) begin
            state<=INIT; init_index<=0; lookup_address<=0;
            pending_vl<=0; pending_network<=0; pending_sn<=0;
            pending_good<=0; pending_enable<=0; pending_tag<=0;
            m_pass<=0; m_reason<=0;
        end else begin
            case (state)
                INIT: begin
                    entries[init_index]<=9'd0;
                    if (&init_index) state<=IDLE;
                    else init_index<=init_index+{{VL_WIDTH{1'b0}},1'b1};
                end
                IDLE: if (s_valid) begin
                    pending_vl<=s_vl; pending_network<=s_network;
                    pending_sn<=s_sn; pending_good<=s_frame_good;
                    pending_enable<=s_ic_enable; pending_tag<=s_tag;
                    lookup_address<={s_network,s_vl};
                    state<=LOOKUP;
                end
                LOOKUP: state<=DECIDE;
                DECIDE: begin
                    if (!pending_good) begin
                        m_pass<=0; m_reason<=1;
                    end else begin
                        entries[lookup_address]<={1'b1,pending_sn};
                        if (!pending_enable || sequence_ok) begin m_pass<=1; m_reason<=0; end
                        else begin m_pass<=0; m_reason<=2; end
                    end
                    state<=OUTPUT;
                end
                OUTPUT: if (m_ready) state<=IDLE;
                default: begin state<=INIT; init_index<=0; end
            endcase
        end
    end
endmodule
