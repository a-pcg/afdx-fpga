`timescale 1ns/1ps
// Serialized atomic merge of two checked-frame descriptor sources.
// Development profile only; see docs/rx/profile.md.
//
// R3 timing revision (2026-10-19). Vendor E3 STA on the narrowed RX top showed
// that the single worst path family was the RM duplicate test: one clock ran
// from the per-VL last_time read through the 64-bit modular difference, the
// absolute-value mux and the SKEW_MAX window compare into the seen/last_sn/
// last_time write enables (16.55 ns, WNS -6.60 ns at a 10 ns constraint).
// The test is now registered into three stages:
//   RD     : latch seen[vl], last_sn[vl], last_time[vl] into local registers
//   DELTA  : register the 64-bit modular difference stamp-last_time
//   WAIT_IC: register |delta| <= SKEW_MAX together with the seen/SN test
// Only descriptor latency grows (+2 core clocks). The valid/ready contract, the
// serialized acceptance order, the atomic state update in DECIDE and the one
// held decision under backpressure are unchanged; the isolated merge suite and
// the full dual-line suite still decide on handshakes, not on exact cycle counts.
//
// RM expected-SN option (2026-10-20, docs/rx/rm_expected_sn_design.md):
// RM_EXPECTED_SN=0 keeps the legacy acceptance-order profile bit-for-bit.
// RM_EXPECTED_SN=1 enables a dev-profile expected-SN arbitration per VL across
// both networks (L01 late-copy fix):
//   first frame (seen=0)            -> deliver
//   sn == last delivered SN         -> duplicate, reason 3 (no SFD window)
//   sn == 0 and RM_SN0_RESTART=1    -> restart, deliver (expected becomes 1)
//   expected = successor(last), diff = (sn - expected) & 0xFF
//     diff == 0                     -> deliver
//     1 <= diff <= RM_GAP_TOLERANCE -> gap-tolerated deliver (updates last=sn)
//     diff >= 128                   -> late copy, reason 4, dropped
//     tolerance < diff < 128        -> gap, reason 5, dropped unless the
//                                      registered |stamp-last_time| exceeds
//                                      RM_RESYNC_TIMEOUT, then accepted (resync)
// successor(v) = (v==255) ? 1 : v+1 matches the frozen integrity checker.
// late_frames/gap_frames are diagnostics (stay 0 when the option is off).
module afdx_rx_merge #(
 parameter VL_WIDTH=3, TAG_WIDTH=32, TIME_WIDTH=64, parameter [TIME_WIDTH-1:0] SKEW_MAX=100,
 parameter RM_EXPECTED_SN=0,
 parameter [31:0] RM_GAP_TOLERANCE=2,
 parameter [TIME_WIDTH-1:0] RM_RESYNC_TIMEOUT=1000000,
 parameter RM_SN0_RESTART=1
)(
 input wire clk,reset,
 input wire [1:0] s_valid, output wire [1:0] s_ready,
 input wire [2*VL_WIDTH-1:0] s_vl,
 input wire [31:0] s_vlid,s_len,
 input wire [15:0] s_sn,
 input wire [2*TAG_WIDTH-1:0] s_tag,
 input wire [2*TIME_WIDTH-1:0] s_time,
 input wire [1:0] s_good,s_ic_enable,
 output wire m_valid,input wire m_ready,
 output wire [VL_WIDTH-1:0] m_vl,
 output wire [15:0] m_vlid,m_len,
 output wire [7:0] m_sn,
 output wire [TAG_WIDTH-1:0] m_tag,
 output wire [TIME_WIDTH-1:0] m_time,
 output wire m_network,output reg m_pass,
 output reg [2:0] m_reason,
 output reg [31:0] late_frames,gap_frames,
 output wire initialized
);
 localparam N=(1<<VL_WIDTH);
 localparam INIT=0,IDLE=1,RD=2,DELTA=3,WAIT_IC=4,DECIDE=5,OUT=6;
 localparam [1:0] CLS_DELIVER=2'd0,CLS_DUP=2'd1,CLS_LATE=2'd2,CLS_GAP=2'd3;
 reg [2:0] state;
 reg [VL_WIDTH-1:0] init_idx;
 reg rr,net;
 reg [VL_WIDTH-1:0] vl;
 reg [15:0] vlid,len;
 reg [7:0] sn;
 reg [TAG_WIDTH-1:0] tag;
 reg [TIME_WIDTH-1:0] stamp;
 reg seen[0:N-1]; reg [7:0] last_sn[0:N-1]; reg [TIME_WIDTH-1:0] last_time[0:N-1];
 // RM pipeline registers (one per stage).
 reg rm_seen; reg [7:0] rm_last_sn; reg [TIME_WIDTH-1:0] rm_last_time;
 reg [TIME_WIDTH-1:0] delta_q;
 reg duplicate_q;
 // Expected-SN stage registers (constant-folded away when RM_EXPECTED_SN=0).
 reg [1:0] class_q;
 reg resync_q;
 wire pick=s_valid[rr]?rr:!rr;
 wire ic_ready,ic_valid,ic_pass,ic_init;
 wire [1:0] ic_reason;
 wire take=state==IDLE && ic_ready && (|s_valid) && !reset;
 wire [TIME_WIDTH-1:0] abs_delta=delta_q[TIME_WIDTH-1]?(~delta_q+1'b1):delta_q;
 wire duplicate_now=rm_seen && (sn==rm_last_sn) && (abs_delta<=SKEW_MAX);
 wire [7:0] expected_now=(rm_last_sn==8'd255)?8'd1:rm_last_sn+8'd1;
 wire [7:0] diff_now=sn-expected_now;
 wire [7:0] gap_tol8=RM_GAP_TOLERANCE[7:0];
 wire dup_now=rm_seen && (sn==rm_last_sn);
 // SN0 restart is only honoured after a silence longer than the resync timeout;
 // otherwise an SN0 late/duplicate copy would be accepted as a restart (L01 hole).
 wire restart_now=(RM_SN0_RESTART!=0) && rm_seen && (sn==8'd0) && (sn!=rm_last_sn) && (abs_delta>RM_RESYNC_TIMEOUT);
 wire late_now=rm_seen && (sn!=rm_last_sn) && !restart_now && (diff_now>=8'd128);
 wire gap_now=rm_seen && (sn!=rm_last_sn) && !restart_now && (diff_now>gap_tol8) && (diff_now<8'd128);
 wire [1:0] class_now=!rm_seen ? CLS_DELIVER : dup_now ? CLS_DUP : late_now ? CLS_LATE : gap_now ? CLS_GAP : CLS_DELIVER;
 assign s_ready[0]=state==IDLE && ic_ready && !reset && !pick;
 assign s_ready[1]=state==IDLE && ic_ready && !reset && pick;
 assign initialized=state!=INIT && ic_init && !reset;
 assign m_valid=state==OUT && !reset;
 assign m_network=net; assign m_vl=vl; assign m_vlid=vlid;assign m_len=len;
 assign m_sn=sn;assign m_tag=tag;assign m_time=stamp;
 afdx_integrity_checker #(.VL_WIDTH(VL_WIDTH),.TAG_WIDTH(TAG_WIDTH)) ic(
 .clk(clk),.reset(reset),.s_valid(take),.s_ready(ic_ready),
 .s_vl(s_vl[pick*VL_WIDTH+:VL_WIDTH]),.s_network(pick),.s_sn(s_sn[pick*8+:8]),
 .s_frame_good(s_good[pick]),.s_ic_enable(s_ic_enable[pick]),.s_tag(s_tag[pick*TAG_WIDTH+:TAG_WIDTH]),
 .m_valid(ic_valid),.m_ready(state==DECIDE),.m_vl(),.m_network(),.m_sn(),.m_tag(),
 .m_pass(ic_pass),.m_reason(ic_reason),.initialized(ic_init));
 always @(posedge clk) begin
 if(reset)begin state<=INIT;init_idx<=0;rr<=0;net<=0;vl<=0;vlid<=0;len<=0;sn<=0;tag<=0;stamp<=0;m_pass<=0;m_reason<=0;
 rm_seen<=0;rm_last_sn<=0;rm_last_time<=0;delta_q<=0;duplicate_q<=0;class_q<=CLS_DELIVER;resync_q<=0;
 late_frames<=0;gap_frames<=0;end
 else case(state)
 INIT:begin seen[init_idx]<=0;last_sn[init_idx]<=0;last_time[init_idx]<=0;
 if(&init_idx)state<=IDLE;else init_idx<=init_idx+1'b1;end
 IDLE:if(take)begin net<=pick;vl<=s_vl[pick*VL_WIDTH+:VL_WIDTH];
 vlid<=s_vlid[pick*16+:16];len<=s_len[pick*16+:16];sn<=s_sn[pick*8+:8];
 tag<=s_tag[pick*TAG_WIDTH+:TAG_WIDTH];stamp<=s_time[pick*TIME_WIDTH+:TIME_WIDTH];rr<=!pick;state<=RD;end
 RD:begin rm_seen<=seen[vl];rm_last_sn<=last_sn[vl];rm_last_time<=last_time[vl];state<=DELTA;end
 DELTA:begin delta_q<=stamp-rm_last_time;state<=WAIT_IC;end
 WAIT_IC:begin
 if(RM_EXPECTED_SN!=0)begin
  duplicate_q<=dup_now;
  class_q<=class_now;
  resync_q<=(abs_delta>RM_RESYNC_TIMEOUT);
 end else begin
  duplicate_q<=duplicate_now;
 end
 if(ic_valid)state<=DECIDE;
 end
 DECIDE:begin
 if(!ic_pass)begin m_pass<=0;m_reason<={1'b0,ic_reason};end
 else if(RM_EXPECTED_SN==0)begin
  if(duplicate_q)begin m_pass<=0;m_reason<=3'd3;end
  else begin m_pass<=1;m_reason<=0;seen[vl]<=1;last_sn[vl]<=sn;last_time[vl]<=stamp;end
 end else begin
  if(class_q==CLS_DUP)begin m_pass<=0;m_reason<=3'd3;end
  else if(class_q==CLS_LATE)begin m_pass<=0;m_reason<=3'd4;late_frames<=late_frames+32'd1;end
  else if(class_q==CLS_GAP&&!resync_q)begin m_pass<=0;m_reason<=3'd5;gap_frames<=gap_frames+32'd1;end
  else begin
   m_pass<=1;m_reason<=0;seen[vl]<=1;last_sn[vl]<=sn;last_time[vl]<=stamp;
   if(class_q==CLS_GAP)gap_frames<=gap_frames+32'd1;
  end
 end
 state<=OUT;end
 OUT:if(m_ready)state<=IDLE;
 default:begin state<=INIT;init_idx<=0;end
 endcase
 end
endmodule
