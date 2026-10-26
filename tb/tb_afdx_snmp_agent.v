`timescale 1ns/1ps
// tb_afdx_snmp_agent -- self-checking peers for the standalone SNMP slice.
//
// It instantiates the real mgmt register slice (afdx_mgmt_regs) and the SNMP
// agent, pre-loads the counters/RTC snapshot, streams the Python-generated
// request byte streams (reports/mgmt_snmp/*.hex), captures each response and
// compares it byte-for-byte with the Python reference expectation.
//
// PASS text: TEST_PASS tb_afdx_snmp_agent cases=N checks=M
// A mismatch or timeout prints TEST_FAIL and $stop; the ModelSim do script maps
// $stop to exit code 1.
module tb_afdx_snmp_agent;
 localparam MAX_BYTES = 2048;
 reg clk, reset;
 integer checks, errors;

 // request source stream
 reg  s_valid, s_sop, s_eop;
 reg  [7:0] s_data;
 wire s_ready;
 // response sink stream
 wire m_valid; wire [7:0] m_data; wire m_sop, m_eop; reg m_ready;

 // mgmt_regs side
 reg  [15:0] mgmt_addr; reg [31:0] mgmt_wdata; reg mgmt_we, mgmt_re;
 wire [31:0] mgmt_rdata; wire mgmt_ready, mgmt_error;
 wire run; wire [2:0] bag_active; wire bag_committed;
 reg [31:0] stat_delivered, stat_rejected, stat_drop, stat_tx, stat_fcs_lo, stat_fcs_hi;
 reg [63:0] time_now;
 // agent read bus
 wire [15:0] agent_addr; wire agent_re; wire [31:0] agent_rdata;

 // mux: testbench writes (snapshot command), agent reads
 wire tb_drive = mgmt_we;
 wire [15:0] bus_addr = tb_drive ? mgmt_addr : agent_addr;
 wire        bus_we   = mgmt_we;
 wire        bus_re   = tb_drive ? 1'b0 : agent_re;

 afdx_mgmt_regs u_regs (
  .clk(clk), .reset(reset),
  .addr(bus_addr), .wdata(mgmt_wdata), .we(bus_we), .re(bus_re),
  .rdata(mgmt_rdata), .ready(mgmt_ready), .error(mgmt_error),
  .run(run), .bag_active(bag_active), .bag_committed(bag_committed),
  .stat_delivered_frames(stat_delivered), .stat_rejected_frames(stat_rejected),
  .stat_drop_frames(stat_drop), .stat_bad_fcs({stat_fcs_hi, stat_fcs_lo}),
  .stat_tx_frames(stat_tx), .time_now(time_now)
 );
 assign agent_rdata = mgmt_rdata;

 afdx_snmp_agent u_agent (
  .clk(clk), .reset(reset),
  .s_valid(s_valid), .s_data(s_data), .s_sop(s_sop), .s_eop(s_eop), .s_ready(s_ready),
  .m_valid(m_valid), .m_data(m_data), .m_sop(m_sop), .m_eop(m_eop), .m_ready(m_ready),
  .reg_addr(agent_addr), .reg_re(agent_re), .reg_rdata(agent_rdata), .reg_ready(mgmt_ready && !tb_drive),
  .cnt_in_octets(32'h1122_3344), .cnt_out_octets(32'h5566_7788)
 );

 initial begin clk = 1'b0; forever #5 clk = ~clk; end

 // vectors from Python client
 reg [7:0] req_pool [0:MAX_BYTES-1];
 reg [7:0] exp_pool [0:MAX_BYTES-1];
 reg [7:0] req_len  [0:31];
 reg [7:0] exp_len  [0:31];
 integer req_off [0:31];
 integer exp_off [0:31];
 reg [7:0] got [0:MAX_BYTES-1];
 integer num_cases;

 task fail_msg; input [511:0] m; begin
  errors = errors + 1;
  $display("TEST_FAIL %0s at time %0t", m, $time);
 end endtask

 task check; input cond; input [511:0] m; begin
  checks = checks + 1;
  if (!cond) fail_msg(m);
 end endtask

 task mgmt_write;
  input [15:0] a; input [31:0] d;
  begin
   @(negedge clk); mgmt_addr = a; mgmt_wdata = d; mgmt_we = 1'b1;
   @(posedge clk); #1;
   check(mgmt_ready === 1'b1, "mgmt write not ready");
   @(negedge clk); mgmt_we = 1'b0;
   @(posedge clk); #1;
  end
 endtask

 task advance_time;
  input [63:0] v;
  begin
   @(negedge clk); time_now = v;
   @(posedge clk); #1;
  end
 endtask

 integer case_i, byte_i, wait_i, errors_before;
 integer base;
 reg [7:0] resp_count;
 reg resp_started, resp_done;

 initial begin
  checks = 0; errors = 0;
  reset = 1'b1; s_valid = 1'b0; s_sop = 1'b0; s_eop = 1'b0; s_data = 8'd0;
  m_ready = 1'b1;
  mgmt_we = 1'b0; mgmt_re = 1'b0; mgmt_addr = 16'd0; mgmt_wdata = 32'd0;
  stat_delivered = 32'd5; stat_rejected = 32'd3; stat_drop = 32'd0;
  stat_tx = 32'd0; stat_fcs_lo = 32'd0; stat_fcs_hi = 32'd0;
  time_now = 64'd0;
  base = 0;

  $readmemh("req_bytes.hex", req_pool);
  $readmemh("exp_bytes.hex", exp_pool);
  $readmemh("req_len.hex", req_len);
  $readmemh("exp_len.hex", exp_len);
  // number of cases is fixed at 12 for this slice
  num_cases = 12;
  base = 0;
  for (case_i = 0; case_i < 32; case_i = case_i + 1) begin req_off[case_i] = 0; exp_off[case_i] = 0; end
  for (case_i = 0; case_i < num_cases; case_i = case_i + 1) begin
   req_off[case_i] = base;
   base = base + req_len[case_i];
  end
  base = 0;
  for (case_i = 0; case_i < num_cases; case_i = case_i + 1) begin
   exp_off[case_i] = base;
   base = base + exp_len[case_i];
  end

  repeat (3) @(posedge clk);
  @(negedge clk); reset = 1'b0;
  repeat (2) @(posedge clk); #1;

  // Make RTC = 5 s + 3 ns and snapshot it into mgmt_regs, then load counters.
  advance_time(64'd1_000_000_000);
  advance_time(64'd2_000_000_000);
  advance_time(64'd3_000_000_000);
  advance_time(64'd4_000_000_000);
  advance_time(64'd5_000_000_000);
  advance_time(64'd5_000_000_003);
  @(posedge clk); #1;
  mgmt_write(16'h0008, 32'd2); // CTRL: snapshot command (run=0)
  @(posedge clk); #1;

  for (case_i = 0; case_i < num_cases; case_i = case_i + 1) begin
   errors_before = errors;
   resp_count = 8'd0; resp_started = 1'b0; resp_done = 1'b0;
   // stream request bytes, one per clock; first byte is accepted in IDLE
   for (byte_i = 0; byte_i < req_len[case_i]; byte_i = byte_i + 1) begin
    @(negedge clk);
    s_valid = 1'b1;
    s_data  = req_pool[req_off[case_i] + byte_i];
    s_sop   = (byte_i == 0);
    s_eop   = (byte_i == (req_len[case_i] - 1));
    @(posedge clk); #1;
   end
   @(negedge clk); s_valid = 1'b0; s_sop = 1'b0; s_eop = 1'b0;
   // capture response
   wait_i = 0;
   while (!resp_done && wait_i < 50000) begin
    @(posedge clk); #1; wait_i = wait_i + 1;
    if (m_valid) begin
     if (!resp_started) begin
      check(m_sop === 1'b1, "response missing m_sop on first byte");
      resp_started = 1'b1;
     end
     if (resp_count < MAX_BYTES) got[resp_count] = m_data;
     resp_count = resp_count + 1;
     if (m_eop) resp_done = 1'b1;
    end
   end
   check(resp_done === 1'b1, "response timeout");
   check(resp_count === exp_len[case_i], "response length mismatch");
   if (resp_count != exp_len[case_i]) begin
    $display("TEST_FAIL case %0d response length got %0d expected %0d",
             case_i, resp_count, exp_len[case_i]);
    if (case_i == 10) begin
     for (byte_i = 0; byte_i < 60; byte_i = byte_i + 1)
      if (byte_i < resp_count) $write("%02x ", got[byte_i]);
     $display("");
     for (byte_i = 0; byte_i < 60; byte_i = byte_i + 1)
      if (byte_i < exp_len[case_i]) $write("%02x ", exp_pool[exp_off[case_i] + byte_i]);
     $display("");
    end
   end
   if (resp_count == exp_len[case_i]) begin
    for (byte_i = 0; byte_i < exp_len[case_i]; byte_i = byte_i + 1) begin
     checks = checks + 1;
     if (got[byte_i] !== exp_pool[exp_off[case_i] + byte_i]) begin
      $display("TEST_FAIL case %0d byte %0d: got %02x expected %02x (time %0t)",
               case_i, byte_i, got[byte_i], exp_pool[exp_off[case_i] + byte_i], $time);
      errors = errors + 1;
     end
    end
   end
   // case-level summary: recompute after all byte comparisons
   if (errors == errors_before)
    $display("CASE %0d PASS  label=%0s req=%0d resp=%0d bytes",
             case_i, (case_i == 0 ? "GET sysDescr" :
                      case_i == 1 ? "GET sysName" :
                      case_i == 2 ? "GET ifNumber" :
                      case_i == 3 ? "GET ifDescr" :
                      case_i == 4 ? "GET inOctets" :
                      case_i == 5 ? "GET inUcastPkts" :
                      case_i == 6 ? "GET inErrors" :
                      case_i == 7 ? "GET outOctets" :
                      case_i == 8 ? "GET rtcSeconds" :
                      case_i == 9 ? "GET rtcNanoseconds" :
                      case_i == 10 ? "GETNEXT rtcSec->rtcNsec" : "GET unknown/noSuchName"),
             req_len[case_i], resp_count);
   repeat (3) @(posedge clk);
  end

  if (errors == 0) begin
   $display("TEST_PASS tb_afdx_snmp_agent cases=%0d checks=%0d", num_cases, checks);
   $finish;
  end else begin
   $display("TEST_FAIL tb_afdx_snmp_agent errors=%0d checks=%0d", errors, checks);
   $stop;
  end
 end

 initial begin
  #5000000;
  $display("TEST_FAIL tb_afdx_snmp_agent timeout");
  $stop;
 end
endmodule
