`timescale 1ns/1ps
// Self-checking ModelSim testbench for the joint AFDX system top.
// One 100 MHz clock, synchronous reset, both wire networks looped back from TX.
// Sends two frames on VL0 (BAG 1 ms), checks payload/SN/length/VL and delivery count.
module tb_afdx_system;
 reg clk,reset;
 initial clk=0;
 always #5 clk=~clk;
 // transmit line
 wire [7:0] tx_line_data; wire tx_line_valid, tx_byte_ce;
 // receive lines (looped back from TX)
 reg [7:0] rx_line_data_a,rx_line_data_b;
 reg rx_line_valid_a,rx_line_valid_b,rx_line_error_a,rx_line_error_b;
 // application transmit
 reg app_msg_valid; wire app_msg_ready; reg [6:0] app_vl; reg [11:0] app_length;
 reg [7:0] app_data; reg app_valid,app_last; wire app_ready;
 wire app_rsp_valid; reg app_rsp_ready; wire [3:0] app_rsp_status;
 // drop
 reg drop_valid; wire drop_ready; reg [6:0] drop_vl;
 wire drop_rsp_valid; reg drop_rsp_ready; wire [3:0] drop_rsp_status;
 // configuration / run
 reg cfg_valid; reg [6:0] cfg_vl; reg [2:0] cfg_bag_exp;
 wire cfg_ready,cfg_reject; reg run,app_enable;
 // application receive
 wire [7:0] rx_data; wire rx_valid,rx_last; reg rx_ready;
 wire [2:0] rx_vl; wire rx_network; wire [7:0] rx_sn; wire [15:0] rx_len; wire [63:0] rx_time;
 wire rx_decision,rx_decision_pass; wire [2:0] rx_reason;
 // status
 wire initialized; wire [63:0] monotonic_time;
 wire [31:0] delivered_frames,rejected_frames; wire [63:0] bad_fcs,full_drops;
 wire sfd_pulse; wire [63:0] sfd_time; wire [6:0] sfd_vl; wire tx_fault; wire busy;

 integer failures=0;
 integer delivered=0;
 integer rx_idx=0;
 integer plen=0;
 integer tx_seq=0;
 reg [7:0] want_sn=0;
 reg [7:0] want_vl=0;
 reg [7:0] payload[0:2047];
 integer i;
 integer j;
 // Compliance measurements (line rate / latency / IFG).
 integer cyc=0;
 integer gap_min=0,gap_max=0,wire_bytes=0,last_ce_cyc=0;
 reg ce_seen=0;
 integer sfd_cyc=0,sfd_seen=0,lat_min=0,lat_max=0,lat_sum=0,lat_n=0;
 integer last_end=0,ifg_min=0;
 reg have_ifg=0;
 reg line_valid_d=0;
 // T14 dual-network TX outputs: this joint testbench runs the default NUM_MACS=1
 // elaboration, so line B must stay parked (checked in the monitor below).
 wire [7:0] tx_line_b_data;
 wire tx_line_b_valid;
 integer line_b_bytes=0;
 time t_send;

 afdx_system_top dut(.clk(clk),.reset(reset),
  .tx_line_data(tx_line_data),.tx_line_valid(tx_line_valid),.tx_byte_ce(tx_byte_ce),
  .tx_line_b_data(tx_line_b_data),.tx_line_b_valid(tx_line_b_valid),
  .rx_line_data_a(rx_line_data_a),.rx_line_valid_a(rx_line_valid_a),.rx_line_error_a(rx_line_error_a),
  .rx_line_data_b(rx_line_data_b),.rx_line_valid_b(rx_line_valid_b),.rx_line_error_b(rx_line_error_b),
  .app_msg_valid(app_msg_valid),.app_msg_ready(app_msg_ready),.app_vl(app_vl),.app_length(app_length),
  .app_data(app_data),.app_valid(app_valid),.app_last(app_last),.app_ready(app_ready),
  .app_rsp_valid(app_rsp_valid),.app_rsp_ready(app_rsp_ready),.app_rsp_status(app_rsp_status),
  .drop_valid(drop_valid),.drop_ready(drop_ready),.drop_vl(drop_vl),
  .drop_rsp_valid(drop_rsp_valid),.drop_rsp_ready(drop_rsp_ready),.drop_rsp_status(drop_rsp_status),
  .cfg_valid(cfg_valid),.cfg_vl(cfg_vl),.cfg_bag_exp(cfg_bag_exp),
  .cfg_ready(cfg_ready),.cfg_reject(cfg_reject),.run(run),.app_enable(app_enable),
  .rx_data(rx_data),.rx_valid(rx_valid),.rx_last(rx_last),.rx_ready(rx_ready),
  .rx_vl(rx_vl),.rx_network(rx_network),.rx_sn(rx_sn),.rx_len(rx_len),.rx_time(rx_time),
  .rx_decision(rx_decision),.rx_decision_pass(rx_decision_pass),.rx_reason(rx_reason),
  .initialized(initialized),.monotonic_time(monotonic_time),
  .delivered_frames(delivered_frames),.rejected_frames(rejected_frames),
  .bad_fcs(bad_fcs),.full_drops(full_drops),
  .sfd_pulse(sfd_pulse),.sfd_time(sfd_time),.sfd_vl(sfd_vl),.tx_fault(tx_fault),.busy(busy));

 // Both receive networks see exactly the transmitted byte stream.
 always @* begin
  rx_line_data_a = tx_line_data;  rx_line_data_b = tx_line_data;
  rx_line_valid_a = tx_line_valid; rx_line_valid_b = tx_line_valid;
  rx_line_error_a = 1'b0;         rx_line_error_b = 1'b0;
 end

 // NUM_MACS=1: the second transmit line must remain idle for the whole test.
 always @(posedge clk) begin
  if(!reset) begin
   if(tx_line_b_valid!==1'b0||tx_line_b_data!==8'd0) begin
    $display("FAIL line B driven in a single-MAC build"); $finish;
   end
  end
  if(tx_line_b_valid&&tx_line_valid) line_b_bytes=line_b_bytes+1;
 end

 always @(posedge clk) begin
  cyc<=cyc+1;
  if(tx_line_valid&&tx_byte_ce)begin
   wire_bytes<=wire_bytes+1;
   if(ce_seen)begin
    if(gap_min==0||(cyc-last_ce_cyc)<gap_min)gap_min<=cyc-last_ce_cyc;
    if((cyc-last_ce_cyc)>gap_max)gap_max<=cyc-last_ce_cyc;
   end
   ce_seen<=1;last_ce_cyc<=cyc;
  end
  if(!tx_line_valid)ce_seen<=0;
  if(sfd_pulse)begin sfd_cyc<=cyc;sfd_seen<=1;end
  if(sfd_seen&&app_valid&&app_ready&&app_last)begin
   if(lat_min==0||(cyc-sfd_cyc)<lat_min)lat_min<=cyc-sfd_cyc;
   if((cyc-sfd_cyc)>lat_max)lat_max<=cyc-sfd_cyc;
   lat_sum<=lat_sum+(cyc-sfd_cyc);lat_n<=lat_n+1;sfd_seen<=0;
  end
  line_valid_d<=tx_line_valid;
  if(!tx_line_valid&&line_valid_d)begin last_end<=cyc;have_ifg<=1;end
  if(tx_line_valid&&!line_valid_d&&have_ifg)begin
   if(ifg_min==0||(cyc-last_end)<ifg_min)ifg_min<=cyc-last_end;
   have_ifg<=0;
  end
 end

 task fail(input [1023:0] name);
 begin
  failures=failures+1;
  $display("FAIL %0s at cycle-time %0t",name,$time);
  $stop;
 end endtask

 // Application-side receive checker.
 always @(posedge clk) begin
  if(!reset && rx_valid && rx_ready) begin
   if(rx_data !== payload[rx_idx]) fail("RX payload mismatch");
   if(rx_sn !== want_sn) fail("RX SN mismatch");
   if(rx_len !== plen) fail("RX length mismatch");
   if(rx_vl !== want_vl[2:0]) fail("RX VL mismatch");
   if(rx_last && (rx_idx !== plen-1)) fail("RX last at wrong position");
   rx_idx <= rx_idx+1;
   if(rx_last) begin
    delivered <= delivered+1;
    $display("DELIVERED n=%0d sn=%0d len=%0d net=%0d time=%0t",delivered+1,rx_sn,rx_len,rx_network,$time);
   end
  end
 end

 task send_frame(input integer vl,input integer n,input [7:0] base);
 integer k;
 reg accepted;
 begin
  plen=n; want_sn=8'd0; want_vl=vl[7:0];
  for(k=0;k<n;k=k+1) payload[k]=base+k[7:0];
  app_vl=vl[6:0]; app_length=n[11:0];
  @(negedge clk); app_msg_valid=1;
  accepted=0; i=0;
  while(!accepted && i<200000) begin
   @(posedge clk);
   accepted=app_msg_ready;
   i=i+1;
  end
  if(!accepted) fail("app descriptor timeout");
  @(negedge clk); app_msg_valid=0;
  for(k=0;k<n;k=k+1) begin
   app_data=payload[k]; app_valid=1; app_last=(k==n-1);
   accepted=0; i=0;
   while(!accepted && i<200000) begin
    @(posedge clk);
    accepted=app_ready;
    i=i+1;
   end
   if(!accepted) fail("app payload timeout");
   @(negedge clk);
  end
  app_valid=0; app_last=0;
  i=0;
  while(!app_rsp_valid && i<200000) begin @(negedge clk); i=i+1; end
  if(!app_rsp_valid) fail("app response timeout");
  if(app_rsp_status!==4'd0) fail("TX enqueue status");
  @(negedge clk); app_rsp_ready=1;
  @(negedge clk); app_rsp_ready=0;
  tx_seq=tx_seq+1;
 end endtask

 task wait_delivery(input integer target);
 integer t;
 begin
  t=0;
  while(delivered<target && t<2000000) begin @(negedge clk); t=t+1; end
  if(delivered<target) fail("delivery timeout");
 end endtask

 initial begin
  reset=1; cfg_valid=0; cfg_vl=0; cfg_bag_exp=0; run=0; app_enable=0;
  app_msg_valid=0; app_vl=0; app_length=0; app_data=0; app_valid=0; app_last=0;
  app_rsp_ready=0; drop_valid=0; drop_vl=0; drop_rsp_ready=0; rx_ready=1;
  repeat(6) @(negedge clk); reset=0;
  i=0;
  while(!initialized && i<200000) begin @(negedge clk); i=i+1; end
  if(!initialized) fail("initialization timeout");
  $display("INIT ok at %0t",$time);
  if(tx_fault) fail("TX fault after init");
  for(i=0;i<2;i=i+1) begin
   cfg_valid=1; cfg_vl=i[6:0]; cfg_bag_exp=3'd0;
   @(negedge clk);
   j=0;
   while(!cfg_ready && j<200000) begin @(negedge clk); j=j+1; end
   if(!cfg_ready) fail("configuration timeout");
   @(negedge clk); cfg_valid=0;
  end
  $display("CONFIG ok at %0t",$time);
  run=1; app_enable=1;
  t_send=$time;
  send_frame(0,17,8'h10);
  $display("FRAME1 sent at %0t",$time);
  wait_delivery(1);
  $display("DELIVERY1 at %0t LATENCY1_NS=%0d",$time,$time-t_send);
  rx_idx=0;
  repeat(4) @(negedge clk);
  t_send=$time;
  send_frame(1,5,8'h40);
  $display("FRAME2 sent at %0t",$time);
  wait_delivery(2);
  $display("DELIVERY2 at %0t LATENCY2_NS=%0d",$time,$time-t_send);
  if(delivered!==2) fail("wrong delivery count");
  $display("COMPLIANCE byte_gap_min=%0d gap_max=%0d wire_bytes=%0d frames_on_wire=%0d",gap_min,gap_max,wire_bytes,2);
  if(gap_min>0) begin
   $display("COMPLIANCE LINE_MBPS_AT_100MHZ=%0d LINE_MBPS_AT_50MHZ=%0d",800/gap_min,400/gap_min);
  end
  $display("COMPLIANCE IFG_MIN_CYCLES=%0d (>=96 required for 12-byte IFG at 8 clk/byte)",ifg_min);
  if(failures==0) $display("PASS tb_afdx_system delivered=%0d",delivered);
  $finish;
 end

 initial begin
  #20000000;
  $display("FAIL tb_afdx_system timeout delivered=%0d",delivered);
  $stop;
 end
endmodule
