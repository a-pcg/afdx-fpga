`timescale 1ns/1ps
// afdx_mgmt_regs -- standalone management register slice (NOT integrated).
// Scope: version/ID, run control, BAG shadow+commit, global counter snapshot,
// RTC seconds/nanoseconds with snapshot latch, offset/frequency storage only,
// and a simple synchronous address/data read-write bus with illegal-address error.
//
// Explicitly out of scope and not implemented here:
//   * no SNMP / BER / MIB, no ARINC 615A / TFTP
//   * no RTC servo / sync protocol: rtc_offset and rtc_freq_ppm are stored but
//     NOT applied to the time base (so data-path timing cannot be disturbed)
//   * not instantiated in afdx_system_top / E5; no data-path ports changed
//
// Time base assumption (documented limitation):
//   time_now is an external 64-bit nanosecond time base. The RTC maintains
//   seconds/nanoseconds by accumulating (time_now - last_time_now) once per clk.
//   A single-cycle time jump of more than 1,000,000,000 ns is not supported
//   (only one carry is applied per clock); jumps of <= 1e9 ns are exact.
//
// Bus protocol: one transaction per request. Drive we or re for one clk with
// addr/wdata stable; ready is asserted for one clk in the next cycle. For reads,
// rdata is valid in the same cycle as ready. Illegal address: rdata=0, error=1
// (one clk), sticky status bit set and error_count incremented.
//
// Register map (16-bit address, 32-bit data):
//   0x0000 VERSION          RO  0x0001_0000
//   0x0004 ID               RO  0x4146_4458 ("AFDX")
//   0x0008 CTRL             RW  [0]=run [1]=snapshot_cmd(self-clear) [2]=clear_error
//   0x000C STATUS           RO  [0]=run [1]=bag_committed [2]=snapshot_valid
//                               [3]=error_sticky [4]=commit_rejected [5]=rtc_valid
//   0x0010 BAG_SHADOW       RW  [2:0]=bag_exp (shadow only until commit)
//   0x0014 BAG_ACTIVE       RO  [2:0]=committed bag_exp
//   0x0018 BAG_COMMIT       WO  write 1 commits shadow; rejected while run=1
//   0x0020 SNAP_DELIVERED   RO  0x0024 SNAP_REJECTED   RO  0x0028 SNAP_DROP RO
//   0x002C SNAP_BAD_FCS_LO  RO  0x0030 SNAP_BAD_FCS_HI RO  0x0034 SNAP_TX_FRAMES RO
//   0x0038 SNAP_RTC_SEC_LO  RO  0x003C SNAP_RTC_SEC_HI RO  0x0040 SNAP_RTC_NSEC RO
//   0x0044 RTC_OFFSET_LO    RW  store only (not applied)
//   0x0048 RTC_OFFSET_HI    RW  store only (not applied)
//   0x004C RTC_FREQ_PPM     RW  signed ppm, store only (not applied)
//   0x0050 SCRATCH          RW
//   0x0054 SNAP_COUNT       RO  number of accepted snapshot commands
//   0x0058 ERROR_COUNT      RO  illegal bus access count
//   0x0060 RTC_LIVE_NSEC    RO  live nanoseconds (use snapshot for atomic reads)
//   0x0064 RTC_LIVE_SEC_LO  RO
//   0x0068 RTC_LIVE_SEC_HI  RO
module afdx_mgmt_regs (
 input  wire        clk,
 input  wire        reset,
 // management bus
 input  wire [15:0] addr,
 input  wire [31:0] wdata,
 input  wire        we,
 input  wire        re,
 output reg  [31:0] rdata,
 output reg         ready,
 output reg         error,
 // control/status to the data path (consumer side decides how to use them)
 output reg         run,
 output reg  [2:0]  bag_active,
 output reg         bag_committed,
 // statistics inputs from the data path
 input  wire [31:0] stat_delivered_frames,
 input  wire [31:0] stat_rejected_frames,
 input  wire [31:0] stat_drop_frames,
 input  wire [63:0] stat_bad_fcs,
 input  wire [31:0] stat_tx_frames,
 // external nanosecond time base
 input  wire [63:0] time_now
);
 localparam [31:0] VERSION = 32'h0001_0000;
 localparam [31:0] ID      = 32'h4146_4458;
 localparam [15:0] A_VERSION=16'h0000, A_ID=16'h0004, A_CTRL=16'h0008, A_STATUS=16'h000C,
                   A_BAG_SHADOW=16'h0010, A_BAG_ACTIVE=16'h0014, A_BAG_COMMIT=16'h0018,
                   A_SNAP_DEL=16'h0020, A_SNAP_REJ=16'h0024, A_SNAP_DROP=16'h0028,
                   A_SNAP_FCS_LO=16'h002C, A_SNAP_FCS_HI=16'h0030, A_SNAP_TX=16'h0034,
                   A_SNAP_SEC_LO=16'h0038, A_SNAP_SEC_HI=16'h003C, A_SNAP_NSEC=16'h0040,
                   A_RTC_OFF_LO=16'h0044, A_RTC_OFF_HI=16'h0048, A_RTC_FREQ=16'h004C,
                   A_SCRATCH=16'h0050, A_SNAP_COUNT=16'h0054, A_ERROR_COUNT=16'h0058,
                   A_RTC_LIVE_NSEC=16'h0060, A_RTC_LIVE_SEC_LO=16'h0064, A_RTC_LIVE_SEC_HI=16'h0068;

 // RTC time base (delta accumulation, <= 1e9 ns per clk)
 reg  [63:0] last_time_now;
 reg  [63:0] rtc_sec;
 reg  [31:0] rtc_nsec;
 wire [63:0] rtc_elapsed  = time_now - last_time_now;
 wire [63:0] rtc_nsec_sum = {32'd0, rtc_nsec} + rtc_elapsed;
 wire        rtc_carry    = (rtc_nsec_sum >= 64'd1_000_000_000);

 // configuration/control registers
 reg  [2:0]  bag_shadow;
 reg  [31:0] rtc_offset_lo, rtc_offset_hi, rtc_freq_ppm;
 reg  [31:0] scratch;
 reg         snapshot_valid;
 reg         error_sticky;
 reg         commit_rejected;
 reg  [31:0] snap_count;
 reg  [31:0] error_count;

 // snapshot registers
 reg  [31:0] snap_delivered, snap_rejected, snap_drop, snap_tx_frames;
 reg  [63:0] snap_bad_fcs, snap_rtc_sec;
 reg  [31:0] snap_rtc_nsec;

 wire        rtc_sec_valid = 1'b1; // RTC has a defined value from reset onwards
 wire [31:0] status_word = {26'd0, rtc_sec_valid,
                            commit_rejected, error_sticky, snapshot_valid,
                            bag_committed, run};

 always @(posedge clk) begin
  if (reset) begin
   last_time_now <= 64'd0;
   rtc_sec       <= 64'd0;
   rtc_nsec      <= 32'd0;
  end else begin
   last_time_now <= time_now;
   if (rtc_carry) begin
    rtc_sec  <= rtc_sec + 64'd1;
    rtc_nsec <= rtc_nsec_sum[31:0] - 32'd1_000_000_000;
   end else begin
    rtc_nsec <= rtc_nsec_sum[31:0];
   end
  end
 end

 always @(posedge clk) begin
  if (reset) begin
   rdata            <= 32'd0;
   ready            <= 1'b0;
   error            <= 1'b0;
   run              <= 1'b0;
   bag_active       <= 3'd0;
   bag_committed    <= 1'b0;
   bag_shadow       <= 3'd0;
   rtc_offset_lo    <= 32'd0;
   rtc_offset_hi    <= 32'd0;
   rtc_freq_ppm     <= 32'd0;
   scratch          <= 32'd0;
   snapshot_valid   <= 1'b0;
   error_sticky     <= 1'b0;
   commit_rejected  <= 1'b0;
   snap_count       <= 32'd0;
   error_count      <= 32'd0;
   snap_delivered   <= 32'd0;
   snap_rejected    <= 32'd0;
   snap_drop        <= 32'd0;
   snap_bad_fcs     <= 64'd0;
   snap_tx_frames   <= 32'd0;
   snap_rtc_sec     <= 64'd0;
   snap_rtc_nsec    <= 32'd0;
  end else begin
   ready <= 1'b0;
   error <= 1'b0;

   if (we) begin
    ready <= 1'b1;
    case (addr)
     A_CTRL: begin
      run <= wdata[0];
      if (wdata[2]) begin
       error_sticky   <= 1'b0;
       error_count    <= 32'd0;
       commit_rejected<= 1'b0;
      end
      if (wdata[1]) begin
       // latches state at command acceptance; stats and rtc regs are pre-edge values
       snap_delivered <= stat_delivered_frames;
       snap_rejected  <= stat_rejected_frames;
       snap_drop      <= stat_drop_frames;
       snap_bad_fcs   <= stat_bad_fcs;
       snap_tx_frames <= stat_tx_frames;
       snap_rtc_sec   <= rtc_sec;
       snap_rtc_nsec  <= rtc_nsec;
       snapshot_valid <= 1'b1;
       snap_count     <= snap_count + 32'd1;
      end
     end
     A_BAG_SHADOW: bag_shadow <= wdata[2:0];
     A_BAG_COMMIT: begin
      if (wdata[0]) begin
       if (run) begin
        commit_rejected <= 1'b1;
        error_sticky    <= 1'b1;
        error           <= 1'b1;
       end else begin
        bag_active     <= bag_shadow;
        bag_committed  <= 1'b1;
        commit_rejected<= 1'b0;
       end
      end
     end
     A_RTC_OFF_LO: rtc_offset_lo <= wdata;
     A_RTC_OFF_HI: rtc_offset_hi <= wdata;
     A_RTC_FREQ:   rtc_freq_ppm  <= wdata;
     A_SCRATCH:    scratch       <= wdata;
     default: begin
      error        <= 1'b1;
      error_sticky <= 1'b1;
      error_count  <= error_count + 32'd1;
     end
    endcase
   end else if (re) begin
    ready <= 1'b1;
    case (addr)
     A_VERSION:        rdata <= VERSION;
     A_ID:             rdata <= ID;
     A_CTRL:           rdata <= {29'd0, 1'b0, snapshot_valid, run};
     A_STATUS:         rdata <= status_word;
     A_BAG_SHADOW:     rdata <= {29'd0, bag_shadow};
     A_BAG_ACTIVE:     rdata <= {29'd0, bag_active};
     A_BAG_COMMIT:     rdata <= 32'd0;
     A_SNAP_DEL:       rdata <= snap_delivered;
     A_SNAP_REJ:       rdata <= snap_rejected;
     A_SNAP_DROP:      rdata <= snap_drop;
     A_SNAP_FCS_LO:    rdata <= snap_bad_fcs[31:0];
     A_SNAP_FCS_HI:    rdata <= snap_bad_fcs[63:32];
     A_SNAP_TX:        rdata <= snap_tx_frames;
     A_SNAP_SEC_LO:    rdata <= snap_rtc_sec[31:0];
     A_SNAP_SEC_HI:    rdata <= snap_rtc_sec[63:32];
     A_SNAP_NSEC:      rdata <= snap_rtc_nsec;
     A_RTC_OFF_LO:     rdata <= rtc_offset_lo;
     A_RTC_OFF_HI:     rdata <= rtc_offset_hi;
     A_RTC_FREQ:       rdata <= rtc_freq_ppm;
     A_SCRATCH:        rdata <= scratch;
     A_SNAP_COUNT:     rdata <= snap_count;
     A_ERROR_COUNT:    rdata <= error_count;
     A_RTC_LIVE_NSEC:  rdata <= rtc_nsec;
     A_RTC_LIVE_SEC_LO:rdata <= rtc_sec[31:0];
     A_RTC_LIVE_SEC_HI:rdata <= rtc_sec[63:32];
     default: begin
      rdata        <= 32'd0;
      error        <= 1'b1;
      error_sticky <= 1'b1;
      error_count  <= error_count + 32'd1;
     end
    endcase
   end
  end
 end
endmodule
