`timescale 1ns/1ps
// M1: one configured VL, store-and-forward. MAC client bytes exclude FCS.
module afdx_udp_tx #(
 parameter [47:0] DEST_MAC=48'h030000000001, SRC_MAC=48'h020000000001,
 parameter [31:0] SRC_IP=32'hc0a80101, DEST_IP=32'hc0a80102,
 parameter [15:0] SRC_PORT=16'd1000, DEST_PORT=16'd1001
)(input wire clk, reset,
 input wire [15:0] msg_len, input wire msg_valid, output wire msg_ready,
 input wire [7:0] s_data, input wire s_valid, s_last, output wire s_ready,
 output reg [7:0] m_data, output wire m_valid, input wire m_ready,
 output wire m_sop, m_eop, output reg reject, output reg done);
 localparam IDLE=2'd0, LOAD=2'd1, SEND=2'd2;
 reg [1:0] state;
 reg [7:0] mem[0:1470];
 reg [7:0] payload_q;
 reg [10:0] payload_addr;
 reg [10:0] wr_idx, out_idx, plen, frame_len;
 reg [15:0] udp_len, ip_len, udp_check, ip_check;
 reg [31:0] payload_sum;
 reg [7:0] seq_num;
 reg [31:0] new_sum;
 // A registered read port allows a single block RAM instead of a large mux tree.
 always @* begin
 payload_addr=0;
 if(state==SEND && out_idx>=11'd42 && out_idx<11'd42+plen)begin
 payload_addr=out_idx-11'd42;
 if(m_ready && out_idx<11'd41+plen)payload_addr=out_idx-11'd41;
 end
 end
 always @(posedge clk)begin
 if(state==LOAD && s_valid && s_ready && s_last==(wr_idx==plen-11'd1))mem[wr_idx]<=s_data;
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
 new_sum=payload_sum+(wr_idx[0]?{24'd0,s_data}:{16'd0,s_data,8'd0});
 m_data=8'd0;
 case(out_idx)
 0:m_data=DEST_MAC[47:40];1:m_data=DEST_MAC[39:32];2:m_data=DEST_MAC[31:24];
 3:m_data=DEST_MAC[23:16];4:m_data=DEST_MAC[15:8];5:m_data=DEST_MAC[7:0];
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
 udp_len<=0;ip_len<=0;payload_sum<=0;udp_check<=0;ip_check<=0;seq_num<=0;reject<=0;done<=0;end
 else begin
 reject<=0;done<=0;
 case(state)
 IDLE:if(msg_valid) begin
 if(msg_len<16'd1 || msg_len>16'd1471) reject<=1;
 else begin plen<=msg_len[10:0];udp_len<=msg_len+16'd8;ip_len<=msg_len+16'd28;
 frame_len<=(msg_len<16'd17)?11'd60:msg_len[10:0]+11'd43;
 ip_check<=checksum(32'h4500+{16'd0,msg_len}+32'd28+32'h4000+32'h4011+
 {16'd0,SRC_IP[31:16]}+{16'd0,SRC_IP[15:0]}+{16'd0,DEST_IP[31:16]}+{16'd0,DEST_IP[15:0]});
 wr_idx<=0;payload_sum<=0;state<=LOAD;end
 end
 LOAD:if(s_valid) begin
 if(s_last!=(wr_idx==plen-11'd1)) begin reject<=1;state<=IDLE;end
 else begin
 payload_sum<=new_sum;
 if(wr_idx==plen-11'd1) begin
 udp_check<=(checksum(new_sum+pseudo_sum(udp_len))==0)?16'hffff:checksum(new_sum+pseudo_sum(udp_len));
 out_idx<=0;state<=SEND;end
 else wr_idx<=wr_idx+11'd1;
 end
 end
 SEND:if(m_ready) begin
 if(m_eop) begin state<=IDLE;done<=1;seq_num<=(seq_num==8'd255)?8'd1:seq_num+8'd1;end
 else out_idx<=out_idx+11'd1;
 end
 default:state<=IDLE;
 endcase
 end
 end
endmodule
