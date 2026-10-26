`timescale 1ns/1ps
// tb_afdx_mgmt_top -- self-checking testbench for afdx_mgmt_top.
//
// Phase 1: external management bus reads/writes (VERSION/ID, SCRATCH, illegal
// address, snapshot registers after a snap pulse).
// Phase 2: one SNMP GET (case 5 = inUcastPkts) using the byte vectors produced
// by scripts/mgmt/snmp_client.py; the response is compared byte-for-byte.
//
// PASS text: TEST_PASS tb_afdx_mgmt_top bus_checks=N snmp_resp=M checks=K
module tb_afdx_mgmt_top;
 localparam MAX_BYTES = 2048;
 reg clk, reset;
 integer checks, errors;

 reg  [15:0] addr;
 reg  [31:0] wdata;
 reg  we, re;
 wire [31:0] rdata;
 wire ready, error;

 reg  s_valid, s_sop, s_eop;
 reg  [7:0] s_data;
 wire s_ready;
 wire m_valid; wire [7:0] m_data; wire m_sop, m_eop; reg m_ready;

 reg [63:0] time_now;
 reg [31:0] stat_delivered, stat_rejected, stat_drop, stat_tx;
 reg [63:0] stat_bad_fcs;
 reg snap;

 afdx_mgmt_top dut (
  .clk(clk), .reset(reset),
  .addr(addr), .wdata(wdata), .we(we), .re(re),
  .rdata(rdata), .ready(ready), .error(error),
  .s_valid(s_valid), .s_data(s_data), .s_sop(s_sop), .s_eop(s_eop), .s_ready(s_ready),
  .m_valid(m_valid), .m_data(m_data), .m_sop(m_sop), .m_eop(m_eop), .m_ready(m_ready),
  .time_now(time_now),
  .stat_delivered_frames(stat_delivered), .stat_rejected_frames(stat_rejected),
  .stat_drop_frames(stat_drop), .stat_bad_fcs(stat_bad_fcs),
  .stat_tx_frames(stat_tx),
  .cnt_in_octets(32'h1122_3344), .cnt_out_octets(32'h5566_7788),
  .snap(snap)
 );

 initial begin clk = 1'b0; forever #5 clk = ~clk; end

 reg [7:0] req_pool [0:MAX_BYTES-1];
 reg [7:0] exp_pool [0:MAX_BYTES-1];
 reg [7:0] req_len  [0:31];
 reg [7:0] exp_len  [0:31];
 integer   req_off  [0:31];
 integer   exp_off  [0:31];
 reg [7:0] got [0:MAX_BYTES-1];

 task fail_msg; input [511:0] m; begin
  errors = errors + 1;
  $display("TEST_FAIL %0s at time %0t", m, $time);
 end endtask

 task check; input cond; input [511:0] m; begin
  checks = checks + 1;
  if (!cond) fail_msg(m);
 end endtask

 task bus_write;
  input [15:0] a; input [31:0] d;
  begin
   @(negedge clk); addr = a; wdata = d; we = 1'b1; re = 1'b0;
   @(posedge clk); #1;
   check(ready === 1'b1, "bus write not ready");
   @(negedge clk); we = 1'b0;
   @(posedge clk); #1;
  end
 endtask

 task bus_read;
  input [15:0] a;
  input [31:0] exp;
  input [511:0] m;
  integer err_seen;
  begin
   @(negedge clk); addr = a; we = 1'b0; re = 1'b1;
   @(posedge clk); #1;
   check(ready === 1'b1, "bus read not ready");
   err_seen = error;
   check(rdata === exp, m);
   @(negedge clk); re = 1'b0;
   @(posedge clk); #1;
   if (a == 16'h1234) check(err_seen === 1'b1, "illegal read did not set error");
  end
 endtask

 task advance_time;
  input [63:0] v;
  begin
   @(negedge clk); time_now = v;
   @(posedge clk); #1;
  end
 endtask

 integer case_i, byte_i, wait_i, resp_count, resp_started, resp_done, base, exp_base;
 reg [7:0] b;

 initial begin
  checks = 0; errors = 0;
  reset = 1'b1; addr = 16'd0; wdata = 32'd0; we = 1'b0; re = 1'b0;
  s_valid = 1'b0; s_sop = 1'b0; s_eop = 1'b0; s_data = 8'd0; m_ready = 1'b1;
  time_now = 64'd0;
  stat_delivered = 32'd5; stat_rejected = 32'd3; stat_drop = 32'd0;
  stat_tx = 32'd0; stat_bad_fcs = 64'd0; snap = 1'b0;

  $readmemh("../mgmt_snmp/req_bytes.hex", req_pool);
  $readmemh("../mgmt_snmp/exp_bytes.hex", exp_pool);
  $readmemh("../mgmt_snmp/req_len.hex", req_len);
  $readmemh("../mgmt_snmp/exp_len.hex", exp_len);
  base = 0;
  for (case_i = 0; case_i < 32; case_i = case_i + 1) begin req_off[case_i] = 0; exp_off[case_i] = 0; end
  for (case_i = 0; case_i < 12; case_i = case_i + 1) begin
   req_off[case_i] = base; base = base + req_len[case_i];
  end
  exp_base = 0;
  for (case_i = 0; case_i < 12; case_i = case_i + 1) begin
   exp_off[case_i] = exp_base; exp_base = exp_base + exp_len[case_i];
  end

  repeat (3) @(posedge clk);
  @(negedge clk); reset = 1'b0;
  repeat (2) @(posedge clk); #1;

  // Prepare RTC = 5 s + 3 ns and counters, then pulse snap.
  advance_time(64'd1_000_000_000);
  advance_time(64'd2_000_000_000);
  advance_time(64'd3_000_000_000);
  advance_time(64'd4_000_000_000);
  advance_time(64'd5_000_000_000);
  advance_time(64'd5_000_000_003);
  @(negedge clk); snap = 1'b1;
  @(posedge clk); #1;
  @(negedge clk); snap = 1'b0;
  repeat (10) @(posedge clk); #1;

  // ---- Phase 1: external bus ----
  $display("PHASE bus-read-write");
  bus_read(16'h0000, 32'h0001_0000, "VERSION mismatch");
  bus_read(16'h0004, 32'h4146_4458, "ID mismatch");
  bus_write(16'h0050, 32'h1234_5678);
  bus_read(16'h0050, 32'h1234_5678, "SCRATCH readback mismatch");
  bus_write(16'h0010, 32'd5);
  bus_read(16'h0010, 32'd5, "BAG_SHADOW readback mismatch");
  bus_read(16'h0014, 32'd0, "BAG_ACTIVE changed before commit");
  bus_write(16'h0018, 32'd1);
  bus_read(16'h0014, 32'd5, "BAG_ACTIVE not committed");
  bus_read(16'h1234, 32'd0, "illegal read did not return 0");
  bus_read(16'h0020, 32'd5, "SNAP_DELIVERED mismatch after snap");
  bus_read(16'h0038, 32'd5, "SNAP_RTC_SEC_LO mismatch after snap");
  bus_read(16'h0040, 32'd3, "SNAP_RTC_NSEC mismatch after snap");

  // ---- Phase 2: one SNMP GET (case 5 = inUcastPkts) ----
  $display("PHASE snmp-get-inucast");
  case_i = 5;
  resp_count = 0; resp_started = 0; resp_done = 0;
  for (byte_i = 0; byte_i < req_len[case_i]; byte_i = byte_i + 1) begin
   @(negedge clk);
   s_valid = 1'b1; s_data = req_pool[req_off[case_i] + byte_i];
   s_sop = (byte_i == 0); s_eop = (byte_i == (req_len[case_i] - 1));
   @(posedge clk); #1;
  end
  @(negedge clk); s_valid = 1'b0; s_sop = 1'b0; s_eop = 1'b0;
  wait_i = 0;
  while (!resp_done && wait_i < 50000) begin
   @(posedge clk); #1; wait_i = wait_i + 1;
   if (m_valid) begin
    if (!resp_started) begin
     check(m_sop === 1'b1, "SNMP response missing m_sop");
     resp_started = 1;
    end
    if (resp_count < MAX_BYTES) got[resp_count] = m_data;
    resp_count = resp_count + 1;
    if (m_eop) resp_done = 1;
   end
  end
  check(resp_done === 1'b1, "SNMP response timeout");
  check(resp_count === exp_len[case_i], "SNMP response length mismatch");
  if (resp_count == exp_len[case_i]) begin
   for (byte_i = 0; byte_i < exp_len[case_i]; byte_i = byte_i + 1) begin
    checks = checks + 1;
    if (got[byte_i] !== exp_pool[exp_off[case_i] + byte_i]) begin
     $display("TEST_FAIL SNMP byte %0d got %02x expected %02x", byte_i,
              got[byte_i], exp_pool[exp_off[case_i] + byte_i]);
     errors = errors + 1;
    end
   end
  end

  if (errors == 0) begin
   $display("TEST_PASS tb_afdx_mgmt_top bus_checks=13 snmp_case=5 resp=%0d checks=%0d",
            resp_count, checks);
   $finish;
  end else begin
   $display("TEST_FAIL tb_afdx_mgmt_top errors=%0d checks=%0d", errors, checks);
   $stop;
  end
 end

 initial begin
  #5000000;
  $display("TEST_FAIL tb_afdx_mgmt_top timeout");
  $stop;
 end
endmodule
