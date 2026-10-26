`timescale 1ns/1ps
// Shared output staging encoder: per-message destination and externally managed SN.
// Derived from afdx_udp_tx; one payload RAM, independent of total VL count.
module afdx_udp_tx_shared #(
 parameter [47:0] SRC_MAC=48'h020000000001,
 parameter [31:0] SRC_IP=32'hc0a80101, DEST_IP=32'hc0a80102,
 parameter [15:0] SRC_PORT=16'd1000, DEST_PORT=16'd1001
)(input wire clk, reset,
 input wire [15:0] msg_len, input wire [47:0] msg_dest_mac,
 input wire [7:0] msg_sn,input wire msg_valid, output wire msg_ready,
 input wire [7:0] s_data, input wire s_valid, s_last, output wire s_ready,
 output reg [7:0] m_data, output wire m_valid, input wire m_ready,
 output wire m_sop, m_eop, output reg reject, output reg done);
 localparam IDLE=2'd0, LOAD=2'd1, SEND=2'd2, CHK=2'd3;
 reg [1:0] state;
 reg [7:0] mem[0:1470];
 reg [7:0] payload_q;
 // T6: one-byte input pipeline register + registered checksum final sum, so the
 // pool-RAM read mux is no longer combinationally followed by the UDP checksum
 // fold in the same cycle. s_ready/msg_ready semantics and the output byte
 // stream are unchanged; LOAD takes one extra cycle per message and SEND starts
 // after one checksum cycle.
 reg [7:0] in_data_q;
 reg in_valid_q,in_last_q;
 reg [31:0] final_sum_q,pseudo_q;
 reg [10:0] payload_addr;
 reg [10:0] wr_idx, out_idx, plen, frame_len;
 reg [15:0] udp_len, ip_len, udp_check, ip_check;
 reg [31:0] payload_sum;
 reg [7:0] seq_num;
 reg [47:0] destination_mac;
 reg [31:0] new_sum;
 // A registered read port allows a single block RAM instead of a large mux tree.
 always @* begin
 payload_addr=0;
 if(state==SEND && out_idx>=11'd42 && out_idx<11'd42+plen)begin
 payload_addr=out_idx-11'd42;
 if(m_ready && out_idx<11'd41+plen)payload_addr=out_idx-11'd41;
 end
 end
 // T6b: exactly one write port and one synchronous read port live in this always
 // block. The T6 revision left a second write in the state-machine block, which
 // made eLinx map the 1471x8 mem[] as registers instead of block RAM. Write
 // enable/address/data below come only from registered signals.
 wire mem_we=(state==LOAD)&&in_valid_q&&(in_last_q==(wr_idx==plen-11'd1));
 wire [10:0] mem_waddr=wr_idx;
 wire [7:0] mem_wdata=in_data_q;
 always @(posedge clk)begin
 if(mem_we) mem[mem_waddr]<=mem_wdata;
 payload_q<=mem[payload_addr];
 end
 function [15:0] checksum;
 input [31:0] sum;
 reg [31:0] folded;
 begin folded={16'd0,sum[15:0]}+{16'd0,sum[31:16]};
 folded={16'd0,folded[15:0]}+{16'd0,folded[31:16]};
 checksum=~folded[15:0]; end
 endfunction
 function [31:0] pseudo_sum;
 input [15:0] length;
 begin pseudo_sum={16'd0,SRC_IP[31:16]}+{16'd0,SRC_IP[15:0]}+
 {16'd0,DEST_IP[31:16]}+{16'd0,DEST_IP[15:0]}+32'd17+
 {16'd0,length}+{16'd0,SRC_PORT}+{16'd0,DEST_PORT}+{16'd0,length};end
 endfunction
 assign msg_ready=(state==IDLE)&&!reset;
 assign s_ready=(state==LOAD)&&!reset;
 assign m_valid=(state==SEND)&&!reset;
 assign m_sop=(out_idx==0);
 assign m_eop=(out_idx==frame_len-11'd1);
 always @* begin
 new_sum=payload_sum+(wr_idx[0]?{24'd0,in_data_q}:{16'd0,in_data_q,8'd0});
 m_data=8'd0;
 case(out_idx)
 0:m_data=destination_mac[47:40];1:m_data=destination_mac[39:32];2:m_data=destination_mac[31:24];
 3:m_data=destination_mac[23:16];4:m_data=destination_mac[15:8];5:m_data=destination_mac[7:0];
 6:m_data=SRC_MAC[47:40];7:m_data=SRC_MAC[39:32];8:m_data=SRC_MAC[31:24];
 9:m_data=SRC_MAC[23:16];10:m_data=SRC_MAC[15:8];11:m_data=SRC_MAC[7:0];
 12:m_data=8'h08;13:m_data=8'h00;14:m_data=8'h45;15:m_data=8'h00;
 16:m_data=ip_len[15:8];17:m_data=ip_len[7:0];18:m_data=0;19:m_data=0;
 20:m_data=8'h40;21:m_data=0;22:m_data=8'h40;23:m_data=8'h11;
 24:m_data=ip_check[15:8];25:m_data=ip_check[7:0];
 26:m_data=SRC_IP[31:24];27:m_data=SRC_IP[23:16];28:m_data=SRC_IP[15:8];29:m_data=SRC_IP[7:0];
 30:m_data=DEST_IP[31:24];31:m_data=DEST_IP[23:16];32:m_data=DEST_IP[15:8];33:m_data=DEST_IP[7:0];
 34:m_data=SRC_PORT[15:8];35:m_data=SRC_PORT[7:0];36:m_data=DEST_PORT[15:8];37:m_data=DEST_PORT[7:0];
 38:m_data=udp_len[15:8];39:m_data=udp_len[7:0];40:m_data=udp_check[15:8];41:m_data=udp_check[7:0];
 default:begin
 if(out_idx==frame_len-11'd1) m_data=seq_num;
 else if(out_idx>=11'd42 && out_idx<11'd42+plen) m_data=payload_q;
 end
 endcase
 end
 always @(posedge clk) begin
 if(reset) begin state<=IDLE;wr_idx<=0;out_idx<=0;plen<=0;frame_len<=0;
 in_valid_q<=0;in_data_q<=0;in_last_q<=0;final_sum_q<=0;pseudo_q<=0;
 udp_len<=0;ip_len<=0;payload_sum<=0;udp_check<=0;ip_check<=0;seq_num<=0;destination_mac<=0;reject<=0;done<=0;end
 else begin
 reject<=0;done<=0;
 case(state)
 IDLE:begin in_valid_q<=0;if(msg_valid) begin
 if(msg_len<16'd1 || msg_len>16'd1471) reject<=1;
 else begin seq_num<=msg_sn;destination_mac<=msg_dest_mac;plen<=msg_len[10:0];udp_len<=msg_len+16'd8;ip_len<=msg_len+16'd28;
 frame_len<=(msg_len<16'd17)?11'd60:msg_len[10:0]+11'd43;
 ip_check<=checksum(32'h4500+{16'd0,msg_len}+32'd28+32'h4000+32'h4011+
 {16'd0,SRC_IP[31:16]}+{16'd0,SRC_IP[15:0]}+{16'd0,DEST_IP[31:16]}+{16'd0,DEST_IP[15:0]});
 pseudo_q<=pseudo_sum(msg_len+16'd8);
 wr_idx<=0;payload_sum<=0;in_valid_q<=0;final_sum_q<=0;state<=LOAD;end
 end
 end
 LOAD:begin
 // capture the presented byte; process the previously captured byte below.
 if(s_valid)begin in_valid_q<=1;in_data_q<=s_data;in_last_q<=s_last;end
 else in_valid_q<=0;
 if(in_valid_q)begin
 if(in_last_q!=(wr_idx==plen-11'd1)) begin reject<=1;state<=IDLE;in_valid_q<=0;end
 else begin
 payload_sum<=new_sum;
 if(in_last_q)begin
 final_sum_q<=new_sum+pseudo_q;
 state<=CHK;end
 else wr_idx<=wr_idx+11'd1;
 end
 end
 end
 CHK:begin
 udp_check<=(checksum(final_sum_q)==16'd0)?16'hffff:checksum(final_sum_q);
 in_valid_q<=0;out_idx<=0;state<=SEND;
 end
 SEND:begin
 in_valid_q<=0;
 if(m_ready) begin
 if(m_eop) begin state<=IDLE;done<=1;end
 else out_idx<=out_idx+11'd1;
 end
 end
 default:state<=IDLE;
 endcase
 end
 end
endmodule
