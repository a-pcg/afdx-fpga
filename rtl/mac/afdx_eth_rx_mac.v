`timescale 1ns/1ps
// Byte-level receive MAC, two independent store-and-forward buffers.
// Samples line_data/line_valid/error only on line_byte_ce. No line backpressure.
// Requires seven 55 preamble bytes then D5; checks 64..1518-byte DA..FCS frame.
// Delivers DA..SN excluding FCS only after CRC/length checks. No UDP parsing.
// If both buffers are held by downstream, discard the entire new frame.
module afdx_eth_rx_mac (
    input wire clk,
    input wire reset,
    input wire [7:0] line_data,
    input wire line_valid,
    input wire line_byte_ce,
    input wire line_error,
    output wire [7:0] m_data,
    output wire m_valid,
    input wire m_ready,
    output wire m_sop,
    output wire m_eop,
    output wire [10:0] m_len,
    output reg accepted,
    output reg drop,
    output reg [2:0] drop_reason
);
    localparam WAIT_FRAME=2'd0, PREAMBLE=2'd1, BODY=2'd2, DRAIN=2'd3;
    reg [1:0] state;
    reg [3:0] preamble_count;
    reg [10:0] byte_count;
    reg frame_bad;
    reg write_slot;
    reg [1:0] occupied;
    reg [7:0] memory0 [0:1517];
    reg [7:0] memory1 [0:1517];
    reg queue_slot [0:1];
    reg [10:0] queue_len [0:1];
    reg queue_head, queue_tail;
    reg [1:0] queue_count;
    reg [10:0] output_index;
    wire [31:0] unused_fcs;
    wire crc_good;
    wire crc_start;
    wire crc_enable;
    wire end_of_frame;
    wire push_frame;
    wire pop_frame;

    assign crc_start = state==PREAMBLE && preamble_count==7 &&
                       line_byte_ce && line_valid && line_data==8'hd5;
    assign crc_enable = state==BODY && line_byte_ce && line_valid;
    assign end_of_frame = state==BODY && line_byte_ce && !line_valid;
    assign push_frame = end_of_frame && !frame_bad && crc_good &&
                        byte_count>=64 && byte_count<=1518;
    assign m_valid = queue_count!=0 && !reset;
    assign m_data = queue_slot[queue_head] ? memory1[output_index] : memory0[output_index];
    assign m_len = queue_len[queue_head];
    assign m_sop = output_index==0;
    assign m_eop = output_index+11'd1==m_len;
    assign pop_frame = m_valid && m_ready && m_eop;

    afdx_eth_crc32 crc (.clk(clk), .reset(reset), .start(crc_start),
        .enable(crc_enable), .data(line_data), .fcs(unused_fcs), .residue_valid(crc_good));

    always @(posedge clk) begin
        if (reset) begin
            state<=WAIT_FRAME; preamble_count<=0; byte_count<=0;
            frame_bad<=0; write_slot<=0; occupied<=0;
            queue_head<=0; queue_tail<=0; queue_count<=0;
            output_index<=0; accepted<=0; drop<=0; drop_reason<=0;
        end else begin
            accepted<=0; drop<=0;
            if (m_valid && m_ready) begin
                if (m_eop) begin
                    occupied[queue_slot[queue_head]]<=0;
                    queue_head<=!queue_head;
                    output_index<=0;
                end else output_index<=output_index+11'd1;
            end
            if (push_frame) begin
                queue_slot[queue_tail]<=write_slot;
                queue_len[queue_tail]<=byte_count-11'd4;
                queue_tail<=!queue_tail;
                accepted<=1;
            end
            case ({push_frame,pop_frame})
                2'b10: queue_count<=queue_count+2'd1;
                2'b01: queue_count<=queue_count-2'd1;
                default: queue_count<=queue_count;
            endcase

            if (line_byte_ce) begin
                case (state)
                    WAIT_FRAME: if (line_valid) begin
                        if (line_data==8'h55 && !line_error) begin
                            preamble_count<=1;
                            state<=PREAMBLE;
                        end else begin
                            drop<=1; drop_reason<=3; state<=DRAIN;
                        end
                    end
                    PREAMBLE: begin
                        if (!line_valid || line_error ||
                            (preamble_count<7 && line_data!=8'h55) ||
                            (preamble_count==7 && line_data!=8'hd5)) begin
                            drop<=1; drop_reason<=3;
                            state<=line_valid ? DRAIN : WAIT_FRAME;
                        end else if (preamble_count==7) begin
                            if (occupied==2'b11) begin
                                drop<=1; drop_reason<=4; state<=DRAIN;
                            end else begin
                                write_slot<=occupied[0] ? 1'b1 : 1'b0;
                                if (occupied[0]) occupied[1]<=1;
                                else occupied[0]<=1;
                                byte_count<=0; frame_bad<=0; state<=BODY;
                            end
                        end else preamble_count<=preamble_count+4'd1;
                    end
                    BODY: begin
                        if (line_valid) begin
                            if (byte_count<1518) begin
                                if (write_slot) memory1[byte_count]<=line_data;
                                else memory0[byte_count]<=line_data;
                                byte_count<=byte_count+11'd1;
                            end else frame_bad<=1;
                            if (line_error) frame_bad<=1;
                        end else begin
                            if (!push_frame) begin
                                occupied[write_slot]<=0;
                                drop<=1;
                                if (frame_bad) drop_reason<=5;
                                else if (byte_count<64 || byte_count>1518) drop_reason<=1;
                                else drop_reason<=2;
                            end
                            state<=WAIT_FRAME;
                        end
                    end
                    DRAIN: if (!line_valid) state<=WAIT_FRAME;
                    default: state<=WAIT_FRAME;
                endcase
            end
        end
    end
endmodule
