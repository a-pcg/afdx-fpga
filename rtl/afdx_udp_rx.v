`timescale 1ns/1ps
// M1: single configured VL; verify a whole MAC-client frame before delivery.
// MAC validates FCS. Header registers and one synchronous payload RAM avoid
// creating dozens of asynchronous RAM read ports for protocol header fields.
//
// R3 timing revisions (2026-10-19), all latency-only:
//  v2: the whole-frame check cone was split into registered stages
//      (CHECK / CHK_A / CHK_B / CHK_C / CHK_IP / CHK_UDP).
//  v4: capture-time UDP checksum end offset, checksum/accept registers, m_done.
//  v5: one-byte input register (p_data/p_sop/p_eop) at the parser boundary so
//      the MAC store-and-forward broadcast ends in flops inside the parser.
//  v6: (a) the input register is load/consume in the same clock, so s_ready is
//          again only (state==CAPTURE): the parser buffer capacity never feeds
//          the network-wide MAC ready path (v5 made m_ready depend on p_valid);
//      (b) the UDP checksum window is a registered accumulator flag (udp_acc),
//          so the 16-bit wr_idx/udp_end compare no longer gates the byte adder;
//      (c) the 32-bit UDP sum bootstrap is registered (sum_udp_q2) and the
//          checksum test is spread over three CHK_UDP phases;
//      (d) CHK_B is split over two phases (flags/length range vs UDP/IP length
//          relation and frame-length relation).
// Captured bytes, delivered payload, drop_reason numbering and the valid/ready
// contract are unchanged; only the per-frame decision latency grows.
module afdx_udp_rx #(
 parameter [47:0] DEST_MAC=48'h030000000001,
 parameter [31:0] SRC_IP=32'hc0a80101, DEST_IP=32'hc0a80102,
 parameter [15:0] SRC_PORT=16'd1000, DEST_PORT=16'd1001
)(input wire clk,reset,
 input wire [7:0] s_data,input wire s_valid,s_sop,s_eop,s_error,output wire s_ready,
 output wire [7:0] m_data,output wire m_valid,m_last,input wire m_ready,
 output wire [15:0] m_len,output reg [7:0] m_sn,
 output reg drop,output reg [3:0] drop_reason,output reg accepted,output reg m_done);
 localparam CAPTURE=3'd0,CHECK=3'd1,CHK_A=3'd2,CHK_B=3'd3,CHK_C=3'd4,
            CHK_IP=3'd5,CHK_UDP=3'd6,DELIVER=3'd7;
 reg [2:0] state;
 reg [7:0] header[0:41];
 reg [7:0] payload_mem[0:1470];
 reg [7:0] payload_q,sn_last;
 reg [10:0] payload_addr,wr_idx,frame_len,out_idx,plen;
 reg bad;
 reg [31:0] ip_sum,udp_sum;
 reg [31:0] sum_ip_q,sum_udp_q,sum_udp_q2,pseudo_q;
 reg hdr_bad_a,hdr_bad_b1,hdr_bad_b2,hdr_bad_c,udp_zero_q,ip_ok_q,udp_ok_q;
 reg [1:0] udp_phase;
 reg chk_b_phase,udp_acc;
 reg [15:0] udp_end_q;
 // One-byte, full-rate input register: load and consume happen in the same
 // clock, so s_ready depends only on the capture state (MAC contract restored).
 (* preserve *) reg [7:0] p_data;reg p_valid,p_sop,p_eop;
 wire cap_ready=(state==CAPTURE)&&!reset;
 wire take=cap_ready&&p_valid;
 assign s_ready=cap_ready;
 wire [15:0] ip_len={header[16],header[17]};
 wire [15:0] udp_len={header[38],header[39]};
 wire [31:0] byte_word=wr_idx[0]?{24'd0,p_data}:{16'd0,p_data,8'd0};
 wire [31:0] pseudo={16'd0,SRC_IP[31:16]}+{16'd0,SRC_IP[15:0]}+
 {16'd0,DEST_IP[31:16]}+{16'd0,DEST_IP[15:0]}+32'd17+{16'd0,udp_len};
 wire hdr_bad_b=hdr_bad_b1|hdr_bad_b2;
 function [15:0] folded;
 input [31:0] val;reg [31:0] t;
 begin t={16'd0,val[15:0]}+{16'd0,val[31:16]};t={16'd0,t[15:0]}+{16'd0,t[31:16]};folded=t[15:0];end
 endfunction
 always @* begin
 payload_addr=0;
 if(state==DELIVER)begin
 payload_addr=out_idx;
 if(m_ready && out_idx<plen-11'd1)payload_addr=out_idx+11'd1;
 end
 end
 always @(posedge clk)begin
 if(reset)begin p_valid<=0;p_data<=0;p_sop<=0;p_eop<=0;end
 else begin
 if(s_valid&&s_ready)begin p_valid<=1;p_data<=s_data;p_sop<=s_sop;p_eop<=s_eop;end
 else if(take)p_valid<=0;
 end
 end
 always @(posedge clk)begin
 if(take)begin
 if(wr_idx<11'd42)header[wr_idx[5:0]]<=p_data;
 if(wr_idx>=11'd42&&wr_idx<11'd1513)payload_mem[wr_idx-11'd42]<=p_data;
 end
 payload_q<=payload_mem[payload_addr];
 end
 assign m_valid=(state==DELIVER)&&!reset;
 assign m_data=payload_q;
 assign m_last=(out_idx==plen-11'd1);
 assign m_len={5'd0,plen};
 always @(posedge clk)begin
 if(reset)begin state<=CAPTURE;wr_idx<=0;frame_len<=0;out_idx<=0;plen<=0;
 bad<=0;ip_sum<=0;udp_sum<=0;sn_last<=0;drop<=0;drop_reason<=0;accepted<=0;m_sn<=0;m_done<=0;
 sum_ip_q<=0;sum_udp_q<=0;sum_udp_q2<=0;pseudo_q<=0;hdr_bad_a<=0;hdr_bad_b1<=0;hdr_bad_b2<=0;
 hdr_bad_c<=0;udp_zero_q<=0;ip_ok_q<=0;udp_ok_q<=0;udp_phase<=0;chk_b_phase<=0;
 udp_acc<=0;udp_end_q<=0;end
 else begin
 drop<=0;accepted<=0;m_done<=0;
 case(state)
 CAPTURE:if(take)begin
 sn_last<=p_data;
 if(wr_idx==11'd33)udp_acc<=1;
 if(wr_idx==11'd39)udp_end_q<=16'd34+{header[38],p_data};
 if(wr_idx>=11'd14&&wr_idx<11'd34)ip_sum<=ip_sum+byte_word;
 if(udp_acc)begin
 udp_sum<=udp_sum+byte_word;
 if(({5'd0,wr_idx}+16'd1)==udp_end_q)udp_acc<=0;
 end
 if(s_error||(wr_idx==0&&!p_sop)||(wr_idx!=0&&p_sop)||wr_idx>=11'd1514)bad<=1;
 if(p_eop)begin frame_len<=(wr_idx<11'd1514)?wr_idx+11'd1:11'd1515;state<=CHECK;end
 else if(wr_idx<11'd1514)wr_idx<=wr_idx+11'd1;
 end
 CHECK:begin
 wr_idx<=0;bad<=0;out_idx<=0;
 sum_ip_q<=ip_sum;sum_udp_q<=udp_sum;pseudo_q<=pseudo;
 udp_end_q<=0;udp_phase<=0;chk_b_phase<=0;udp_acc<=0;
 ip_sum<=0;udp_sum<=0;
 if(bad||frame_len<11'd60||frame_len>11'd1514)begin drop<=1;drop_reason<=1;state<=CAPTURE;end
 else state<=CHK_A;
 end
 CHK_A:begin
 hdr_bad_a<=({header[0],header[1],header[2],header[3],header[4],header[5]}!=DEST_MAC||
 {header[12],header[13]}!=16'h0800||header[14]!=8'h45||header[23]!=8'd17);
 state<=CHK_B;
 end
 CHK_B:begin
 if(!chk_b_phase)begin
 hdr_bad_b1<=({header[20],header[21]}&16'hbfff)!=16'd0||ip_len<16'd29||ip_len>16'd1499;
 chk_b_phase<=1;
 end else begin
 hdr_bad_b2<=(udp_len!=ip_len-16'd20)||
 ({5'd0,frame_len}!=((ip_len<16'd45)?16'd60:ip_len+16'd15));
 chk_b_phase<=0;state<=CHK_C;
 end
 end
 CHK_C:begin
 hdr_bad_c<=({header[26],header[27],header[28],header[29]}!=SRC_IP||
 {header[30],header[31],header[32],header[33]}!=DEST_IP||
 {header[34],header[35]}!=SRC_PORT||{header[36],header[37]}!=DEST_PORT);
 udp_zero_q<=({header[40],header[41]}==16'd0);
 state<=CHK_IP;
 end
 CHK_IP:begin
 ip_ok_q<=(folded(sum_ip_q)==16'hffff);
 state<=CHK_UDP;
 end
 CHK_UDP:if(udp_phase==2'd0)begin
 if(hdr_bad_a||hdr_bad_b||hdr_bad_c)begin drop<=1;drop_reason<=2;state<=CAPTURE;end
 else if(!ip_ok_q)begin drop<=1;drop_reason<=3;state<=CAPTURE;end
 else begin sum_udp_q2<=sum_udp_q+pseudo_q;udp_phase<=2'd1;end
 end else if(udp_phase==2'd1)begin
 udp_ok_q<=(!udp_zero_q)&&(folded(sum_udp_q2)==16'hffff);udp_phase<=2'd2;
 end else begin
 udp_phase<=2'd0;
 if(!udp_ok_q)begin drop<=1;drop_reason<=4;state<=CAPTURE;end
 else begin plen<=udp_len[10:0]-11'd8;m_sn<=sn_last;accepted<=1;state<=DELIVER;end
 end
 DELIVER:if(m_ready)begin
 if(m_last)begin m_done<=1;state<=CAPTURE;end
 else out_idx<=out_idx+11'd1;
 end
 default:begin state<=CAPTURE;wr_idx<=0;bad<=0;ip_sum<=0;udp_sum<=0;udp_acc<=0;end
 endcase
 end
 end
endmodule
