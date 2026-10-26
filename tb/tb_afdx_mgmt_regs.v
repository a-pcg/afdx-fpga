`timescale 1ns/1ps
// tb_afdx_mgmt_regs -- self-checking Verilog-2001 testbench for afdx_mgmt_regs.
// Covers: reset values, version/ID, scratch, BAG shadow+commit (rejected while
// run=1), illegal-address read/write error semantics, counter snapshot atomicity,
// RTC seconds/nanoseconds with snapshot, offset/frequency store-only behaviour.
// PASS text: "TEST_PASS tb_afdx_mgmt_regs checks=N"; on failure "$stop" is used
// and the surrounding ModelSim do-script maps that to exit code 1.
module tb_afdx_mgmt_regs;
 localparam [31:0] EXP_VERSION = 32'h0001_0000;
 localparam [31:0] EXP_ID      = 32'h4146_4458;
 localparam [15:0] A_VERSION=16'h0000, A_ID=16'h0004, A_CTRL=16'h0008, A_STATUS=16'h000C,
                   A_BAG_SHADOW=16'h0010, A_BAG_ACTIVE=16'h0014, A_BAG_COMMIT=16'h0018,
                   A_SNAP_DEL=16'h0020, A_SNAP_REJ=16'h0024, A_SNAP_DROP=16'h0028,
                   A_SNAP_FCS_LO=16'h002C, A_SNAP_FCS_HI=16'h0030, A_SNAP_TX=16'h0034,
                   A_SNAP_SEC_LO=16'h0038, A_SNAP_SEC_HI=16'h003C, A_SNAP_NSEC=16'h0040,
                   A_RTC_OFF_LO=16'h0044, A_RTC_OFF_HI=16'h0048, A_RTC_FREQ=16'h004C,
                   A_SCRATCH=16'h0050, A_SNAP_COUNT=16'h0054, A_ERROR_COUNT=16'h0058,
                   A_RTC_LIVE_NSEC=16'h0060, A_RTC_LIVE_SEC_LO=16'h0064, A_RTC_LIVE_SEC_HI=16'h0068;

 reg clk, reset;
 reg [15:0] addr;
 reg [31:0] wdata;
 reg we, re;
 wire [31:0] rdata;
 wire ready, error;
 wire run;
 wire [2:0] bag_active;
 wire bag_committed;
 reg [31:0] stat_delivered, stat_rejected, stat_drop, stat_tx_frames;
 reg [63:0] stat_bad_fcs;
 reg [63:0] time_now;
 integer checks, errors;
 reg [31:0] rd;
 reg        rd_err;

 afdx_mgmt_regs dut (
  .clk(clk), .reset(reset),
  .addr(addr), .wdata(wdata), .we(we), .re(re),
  .rdata(rdata), .ready(ready), .error(error),
  .run(run), .bag_active(bag_active), .bag_committed(bag_committed),
  .stat_delivered_frames(stat_delivered), .stat_rejected_frames(stat_rejected),
  .stat_drop_frames(stat_drop), .stat_bad_fcs(stat_bad_fcs),
  .stat_tx_frames(stat_tx_frames), .time_now(time_now)
 );

 initial begin clk = 1'b0; forever #5 clk = ~clk; end

 task fail_msg;
  input [511:0] m;
  begin errors = errors + 1; $display("TEST_FAIL %0s at time %0t", m, $time); end
 endtask

 task check;
  input cond;
  input [511:0] m;
  begin checks = checks + 1; if (!cond) fail_msg(m); end
 endtask

 task bus_write;
  input [15:0] a;
  input [31:0] d;
  begin
   @(negedge clk); addr = a; wdata = d; we = 1'b1; re = 1'b0;
   @(posedge clk); #1; check(ready === 1'b1, "write: ready not asserted");
   rd_err = error;
   @(negedge clk); we = 1'b0;
   @(posedge clk); #1;
  end
 endtask

 task bus_read;
  input [15:0] a;
  begin
   @(negedge clk); addr = a; we = 1'b0; re = 1'b1;
   @(posedge clk); #1; check(ready === 1'b1, "read: ready not asserted");
   rd = rdata; rd_err = error;
   @(negedge clk); re = 1'b0;
   @(posedge clk); #1;
  end
 endtask

 task advance_ns;
  input [63:0] d;
  begin
   @(negedge clk); time_now = time_now + d;
   @(posedge clk); #1;
   @(posedge clk); #1;
  end
 endtask

 task do_reset;
  begin
   reset = 1'b1; we = 1'b0; re = 1'b0; addr = 16'd0; wdata = 32'd0;
   time_now = 64'd0; // keep the documented <=1e9 ns/clock RTC delta at reset release
   repeat (3) @(posedge clk);
   @(negedge clk); reset = 1'b0;
   repeat (2) @(posedge clk); #1;
  end
 endtask

 initial begin
  checks = 0; errors = 0;
  reset = 1'b1; we = 1'b0; re = 1'b0; addr = 16'd0; wdata = 32'd0;
  stat_delivered = 32'd0; stat_rejected = 32'd0; stat_drop = 32'd0;
  stat_tx_frames = 32'd0; stat_bad_fcs = 64'd0; time_now = 64'd0;
  repeat (3) @(posedge clk);
  @(negedge clk); reset = 1'b0;
  repeat (2) @(posedge clk); #1;

  // 1) reset values
  $display("PHASE reset-values");
  check(run === 1'b0, "reset: run not 0");
  check(bag_active === 3'd0, "reset: bag_active not 0");
  check(bag_committed === 1'b0, "reset: bag_committed not 0");
  check(ready === 1'b0, "reset: ready not 0");
  check(error === 1'b0, "reset: error not 0");
  check(rdata === 32'd0, "reset: rdata not 0");
  bus_read(A_STATUS);
  check((rd & ~32'h0000_0020) === 32'd0, "reset: STATUS bits other than rtc_valid not 0");
  check(rd[5] === 1'b1, "reset: STATUS rtc_valid not set");

  // 2) version / ID / scratch
  $display("PHASE version-id-scratch");
  bus_read(A_VERSION); check(rd === EXP_VERSION, "VERSION mismatch");
  bus_read(A_ID); check(rd === EXP_ID, "ID mismatch");
  bus_write(A_SCRATCH, 32'h1234_5678);
  bus_read(A_SCRATCH); check(rd === 32'h1234_5678, "SCRATCH readback mismatch");
  check(rd_err === 1'b0, "legal access raised error");

  // 3) BAG shadow + commit (legal at run=0)
  $display("PHASE bag-shadow-commit");
  bus_write(A_BAG_SHADOW, 32'd5);
  bus_read(A_BAG_SHADOW); check(rd === 32'd5, "BAG_SHADOW readback");
  bus_read(A_BAG_ACTIVE); check(rd === 32'd0, "BAG_ACTIVE changed before commit");
  bus_write(A_BAG_COMMIT, 32'd1);
  bus_read(A_BAG_ACTIVE); check(rd === 32'd5, "BAG_ACTIVE not committed");
  check(bag_active === 3'd5, "BAG_ACTIVE output not committed");
  check(bag_committed === 1'b1, "bag_committed flag not set");
  bus_read(A_STATUS); check(rd[1] === 1'b1, "STATUS bag_committed not set");

  // 4) commit rejected while run=1
  $display("PHASE commit-reject-while-run");
  bus_write(A_BAG_SHADOW, 32'd2);
  bus_write(A_CTRL, 32'd1); // run=1
  check(run === 1'b1, "run not set");
  bus_read(A_STATUS); check(rd[0] === 1'b1, "STATUS run not set");
  bus_write(A_BAG_COMMIT, 32'd1);
  check(rd_err === 1'b1, "commit while run=1 did not report error");
  bus_read(A_BAG_ACTIVE); check(rd === 32'd5, "BAG_ACTIVE changed on rejected commit");
  bus_read(A_STATUS);
  check(rd[3] === 1'b1, "STATUS error_sticky not set after rejected commit");
  check(rd[4] === 1'b1, "STATUS commit_rejected not set");

  // 5) clear error, then commit at run=0
  $display("PHASE clear-error-and-commit");
  bus_write(A_CTRL, 32'd5); // run=1, clear_error=1
  check(run === 1'b1, "run lost on clear_error");
  bus_read(A_STATUS);
  check(rd[3] === 1'b0, "error_sticky not cleared");
  check(rd[4] === 1'b0, "commit_rejected not cleared");
  bus_write(A_CTRL, 32'd0); // run=0
  check(run === 1'b0, "run not cleared");
  bus_write(A_BAG_COMMIT, 32'd1);
  bus_read(A_BAG_ACTIVE); check(rd === 32'd2, "BAG_ACTIVE not updated after legal commit");

  // 6) illegal address: read and write
  $display("PHASE illegal-address");
  bus_read(16'h1234);
  check(rd === 32'd0, "illegal read did not return 0");
  check(rd_err === 1'b1, "illegal read did not set error");
  bus_write(16'h1234, 32'hDEAD_BEEF);
  check(rd_err === 1'b1, "illegal write did not set error");
  bus_read(A_ERROR_COUNT); check(rd === 32'd2, "ERROR_COUNT mismatch after 2 illegal accesses");
  bus_read(A_STATUS);      check(rd[3] === 1'b1, "STATUS error_sticky not set");
  bus_read(A_SCRATCH);     check(rd === 32'h1234_5678, "illegal write corrupted SCRATCH");
  bus_write(A_CTRL, 32'd4); // clear_error, run=0
  bus_read(A_ERROR_COUNT); check(rd === 32'd0, "ERROR_COUNT not cleared");
  bus_read(A_STATUS);      check(rd[3] === 1'b0, "error_sticky not cleared");

  // 7) counter snapshot atomicity
  $display("PHASE counter-snapshot");
  stat_delivered = 32'h1122_3344; stat_rejected = 32'h5566_7788;
  stat_drop = 32'h99AA_BBCC; stat_tx_frames = 32'hDEAD_BEEF;
  stat_bad_fcs = 64'h0123_4567_89AB_CDEF;
  advance_ns(64'd0);
  bus_write(A_CTRL, 32'd2); // snapshot_cmd
  bus_read(A_SNAP_COUNT); check(rd === 32'd1, "SNAP_COUNT after first snapshot");
  // change inputs after snapshot; snapshot must not change
  stat_delivered = 32'hFFFF_FFFF; stat_bad_fcs = 64'hFFFF_FFFF_FFFF_FFFF;
  bus_read(A_SNAP_DEL);    check(rd === 32'h1122_3344, "SNAP_DELIVERED changed after snapshot");
  bus_read(A_SNAP_REJ);    check(rd === 32'h5566_7788, "SNAP_REJECTED mismatch");
  bus_read(A_SNAP_DROP);   check(rd === 32'h99AA_BBCC, "SNAP_DROP mismatch");
  bus_read(A_SNAP_FCS_LO); check(rd === 32'h89AB_CDEF, "SNAP_BAD_FCS_LO mismatch");
  stat_bad_fcs = 64'h0000_0000_0000_0001;
  bus_read(A_SNAP_FCS_HI); check(rd === 32'h0123_4567, "SNAP_BAD_FCS_HI torn/changed");
  bus_read(A_SNAP_TX);     check(rd === 32'hDEAD_BEEF, "SNAP_TX mismatch");
  bus_read(A_STATUS);      check(rd[2] === 1'b1, "STATUS snapshot_valid not set");

  // 8) RTC seconds/nanoseconds and snapshot latch
  $display("PHASE rtc-seconds-nanoseconds");
  advance_ns(64'd1_000_000_000); // +1 s
  advance_ns(64'd1_000_000_000); // +1 s
  advance_ns(64'd345_678_901);   // +0.345678901 s -> sec=2, nsec=345678901
  bus_write(A_CTRL, 32'd2);
  bus_read(A_SNAP_SEC_LO); check(rd === 32'd2, "RTC SEC_LO expected 2");
  bus_read(A_SNAP_SEC_HI); check(rd === 32'd0, "RTC SEC_HI expected 0");
  bus_read(A_SNAP_NSEC);   check(rd === 32'd345_678_901, "RTC NSEC expected 345678901");
  // advance and take a snapshot crossing a second boundary
  advance_ns(64'd654_321_098); // nsec -> 999999999
  bus_write(A_CTRL, 32'd2);
  bus_read(A_SNAP_NSEC);   check(rd === 32'd999_999_999, "RTC NSEC expected 999999999");
  advance_ns(64'd1);           // carry -> sec=3, nsec=0
  bus_write(A_CTRL, 32'd2);
  bus_read(A_SNAP_SEC_LO); check(rd === 32'd3, "RTC second carry failed");
  bus_read(A_SNAP_NSEC);   check(rd === 32'd0, "RTC NSEC after carry not 0");

  // 9) offset/frequency registers are store-only (must not move RTC)
  $display("PHASE rtc-offset-freq-store-only");
  bus_write(A_RTC_OFF_LO, 32'hAABB_CCDD);
  bus_write(A_RTC_OFF_HI, 32'h0011_2233);
  bus_write(A_RTC_FREQ, 32'hFFFF_FE0C); // -500 ppm
  bus_read(A_RTC_OFF_LO); check(rd === 32'hAABB_CCDD, "RTC_OFFSET_LO readback");
  bus_read(A_RTC_OFF_HI); check(rd === 32'h0011_2233, "RTC_OFFSET_HI readback");
  bus_read(A_RTC_FREQ);   check(rd === 32'hFFFF_FE0C, "RTC_FREQ readback");
  advance_ns(64'd100); // +100 ns only
  bus_write(A_CTRL, 32'd2);
  bus_read(A_SNAP_SEC_LO); check(rd === 32'd3, "RTC changed by offset/freq write (sec)");
  bus_read(A_SNAP_NSEC);   check(rd === 32'd100, "RTC changed by offset/freq write (nsec)");
  // live registers are consistent with the same elapsed time
  bus_read(A_RTC_LIVE_SEC_LO); check(rd === 32'd3, "live RTC sec expected 3");
  bus_read(A_RTC_LIVE_NSEC);   check(rd === 32'd100, "live RTC nsec expected 100");

  // 10) reset restores documented values
  $display("PHASE second-reset");
  do_reset();
  check(run === 1'b0, "reset2: run not 0");
  check(bag_active === 3'd0, "reset2: bag_active not 0");
  check(bag_committed === 1'b0, "reset2: bag_committed not 0");
  bus_read(A_STATUS); check((rd & ~32'h0000_0020) === 32'd0, "reset2: STATUS bits other than rtc_valid not 0");
  bus_read(A_SNAP_SEC_LO); check(rd === 32'd0, "reset2: snapshot sec not 0");
  bus_read(A_SNAP_NSEC);   check(rd === 32'd0, "reset2: snapshot nsec not 0");
  bus_read(A_VERSION);     check(rd === EXP_VERSION, "reset2: VERSION not constant");

  if (errors == 0) begin
   $display("TEST_PASS tb_afdx_mgmt_regs checks=%0d", checks);
   $finish;
  end else begin
   $display("TEST_FAIL tb_afdx_mgmt_regs errors=%0d checks=%0d", errors, checks);
   $stop;
  end
 end

 // safety timeout
 initial begin
  #2000000;
  $display("TEST_FAIL tb_afdx_mgmt_regs timeout");
  $stop;
 end
endmodule
