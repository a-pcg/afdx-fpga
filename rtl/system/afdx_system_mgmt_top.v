`timescale 1ns/1ps
// afdx_system_mgmt_top -- standalone management/data-plane integration wrapper.
//
// Purpose
//   Connect the frozen management slice (rtl/mgmt/afdx_mgmt_top.v) to the frozen
//   joint data-path top (rtl/system/afdx_system_top.v) behind a single top level,
//   for the independent "management plane on the full system" demonstration.
//   Neither frozen module is modified and nothing here is wired into E5.
//
// Connection semantics (all documented in docs/management/integration_demo.md)
//   R1 run         : system run = ext_run OR mgmt run.  Either source starts the
//                    data path; mgmt run never masks an external run.  app_enable
//                    stays an external-only input (management does not gate
//                    application admission).
//   R2 BAG commit  : a management write to BAG_SHADOW (0x0010) then BAG_COMMIT
//                    (0x0018, bit0) accepted by the register slice (i.e. while
//                    mgmt run = 0) arms a programming sequence that drives
//                    cfg_valid/cfg_vl/cfg_bag_exp for VL 0..CFG_VL_COUNT-1, one
//                    VL at a time, holding cfg_valid until the data path returns
//                    cfg_ready.  mgmt run / committed BAG are reconstructed from
//                    the management write stream because afdx_mgmt_top exports
//                    only the bus (this is exact: the slice never writes CTRL or
//                    BAG_SHADOW internally; its only internal write is CTRL[1]
//                    snapshot, which preserves run).
//                    Conflict rule: management wins.  While the management
//                    programming sequence is active (or on the commit cycle
//                    itself) the external cfg_* request is not forwarded to the
//                    data path and cfg_reject is asserted for those cycles; the
//                    external master must hold cfg_valid (S05 contract).  External
//                    cfg requests are forwarded unchanged when management is idle.
//   R3 counters    : system delivered_frames -> stat_delivered_frames (exact),
//                    rejected_frames -> stat_rejected_frames (exact).
//                    afdx_system_top exports bad_fcs and full_drops as TWO
//                    packed 32-bit per-network counters ({network B, network A}),
//                    so the wrapper forms scalar totals first:
//                      bad_fcs_total    = bad_fcs[31:0] + bad_fcs[63:32]
//                                         -> stat_bad_fcs (64b, exact)
//                      full_drops_total = full_drops[31:0] + full_drops[63:32]
//                                         -> stat_drop_frames (32b), SATURATING
//                                            at 0xFFFFFFFF if any upper bit is set
//                    tx frames: afdx_system_top exports no TX frame counter, so
//                    the wrapper counts sfd_pulse edges -> stat_tx_frames.
//   R4 RTC         : system monotonic_time -> mgmt time_now (direct 64-bit), so
//                    the management RTC seconds/nanoseconds are derived from the
//                    data-path time base (1 ns per clk in this build).
//   R5 SNMP        : the SNMP slice consumes a UDP-payload byte stream.  The
//                    stream source is selected at run time: snmp_src_sel=0 uses
//                    the external snmp_s_* test port, snmp_src_sel=1 routes the
//                    RX application payload stream (rx_data/rx_valid/rx_last) into
//                    the SNMP agent and backpressures RX through s_ready (except
//                    the first byte of a payload, which the agent latches while
//                    its s_ready is still low; see the adapter comment below).
//                    One RX-sourced request at a time, no queue.  Responses
//                    always leave on snmp_m_*.
//   R6 line octets : cnt_in_octets counts network-A line bytes observed on
//                    rx_line_* at byte_ce; cnt_out_octets counts tx_line_* bytes
//                    at byte_ce.  These are wire octets (preamble/SFD/FCS
//                    included), not UDP payload octets; they feed SNMP inOctets /
//                    outOctets.  Both are 32-bit wrapping counters.
//   R7 snapshot    : the CTLR[1] auto-snapshot input of the mgmt slice is
//                    exported as mgmt_snap (optional; tie 0 when the management
//                    bus CTLR[1] write is used instead).
//
// Out of scope: no E5 integration, no 615A/TFTP, no RTC servo, no IP/UDP/MAC
// encapsulation in the SNMP path, no port/VL mapping change on the data path.
module afdx_system_mgmt_top #(
 parameter VL_WIDTH=7,
 parameter SLOT_WIDTH=6,
 parameter MAX_PER_VL=4,
 parameter [63:0] SKEW_MAX=64'd100000,
 parameter [31:0] INITIAL_TIME_LOW=32'd0,
 parameter [31:0] INITIAL_TIME_HIGH=32'd0,
 parameter CFG_VL_COUNT=4
)(
 input  wire        clk,
 input  wire        reset,

 // ---- external management bus (same protocol as afdx_mgmt_regs) ----
 input  wire [15:0] mgmt_addr,
 input  wire [31:0] mgmt_wdata,
 input  wire        mgmt_we,
 input  wire        mgmt_re,
 output wire [31:0] mgmt_rdata,
 output wire        mgmt_ready,
 output wire        mgmt_error,
 input  wire        mgmt_snap,

 // ---- SNMP UDP payload byte stream (test direct / RX application stream) ----
 input  wire        snmp_s_valid,
 input  wire [7:0]  snmp_s_data,
 input  wire        snmp_s_sop,
 input  wire        snmp_s_eop,
 output wire        snmp_s_ready,
 output wire        snmp_m_valid,
 output wire [7:0]  snmp_m_data,
 output wire        snmp_m_sop,
 output wire        snmp_m_eop,
 input  wire        snmp_m_ready,
 input  wire        snmp_src_sel,   // 0 = external port, 1 = RX application stream

 // ---- physical line toward the PHY (transmit) ----
 output wire [7:0]  tx_line_data,
 output wire        tx_line_valid,
 output wire        tx_byte_ce,
 // ---- physical line from the PHY (receive networks A and B) ----
 input  wire [7:0]  rx_line_data_a,
 input  wire        rx_line_valid_a,
 input  wire        rx_line_error_a,
 input  wire [7:0]  rx_line_data_b,
 input  wire        rx_line_valid_b,
 input  wire        rx_line_error_b,

 // ---- application transmit ----
 input  wire        app_msg_valid,
 output wire        app_msg_ready,
 input  wire [VL_WIDTH-1:0] app_vl,
 input  wire [11:0] app_length,
 input  wire [7:0]  app_data,
 input  wire        app_valid,
 input  wire        app_last,
 output wire        app_ready,
 output wire        app_rsp_valid,
 input  wire        app_rsp_ready,
 output wire [3:0]  app_rsp_status,
 input  wire        drop_valid,
 output wire        drop_ready,
 input  wire [VL_WIDTH-1:0] drop_vl,
 output wire        drop_rsp_valid,
 input  wire        drop_rsp_ready,
 output wire [3:0]  drop_rsp_status,

 // ---- external configuration / run (management has priority) ----
 input  wire        ext_cfg_valid,
 input  wire [VL_WIDTH-1:0] ext_cfg_vl,
 input  wire [2:0]  ext_cfg_bag_exp,
 output wire        cfg_ready,
 output wire        cfg_reject,
 input  wire        ext_run,
 input  wire        app_enable,

 // ---- application receive ----
 output wire [7:0]  rx_data,
 output wire        rx_valid,
 output wire        rx_last,
 input  wire        rx_ready,
 output wire [2:0]  rx_vl,
 output wire        rx_network,
 output wire [7:0]  rx_sn,
 output wire [15:0] rx_len,
 output wire [63:0] rx_time,
 output wire        rx_decision,
 output wire        rx_decision_pass,
 output wire [2:0]  rx_reason,

 // ---- status ----
 output wire        initialized,
 output wire [63:0] monotonic_time,
 output wire [31:0] delivered_frames,
 output wire [31:0] rejected_frames,
 output wire [63:0] bad_fcs,
 output wire [63:0] full_drops,
 output wire [31:0] tx_frames,
 output wire        sfd_pulse,
 output wire [63:0] sfd_time,
 output wire [VL_WIDTH-1:0] sfd_vl,
 output wire        tx_fault,
 output wire        busy,
 // management view of the per-network packed counters (scalar totals)
 output wire [63:0] bad_fcs_total,
 output wire [31:0] drop_frames,

 // ---- management observability (integration debug only) ----
 output wire        mgmt_run,          // reconstructed mgmt CTRL[0]
 output wire        sys_run,           // ext_run | mgmt_run fed to the data path
 output wire        cfg_busy,          // management BAG programming in progress
 output wire        cfg_done,          // one-cycle pulse: VL0..N-1 programmed
 output wire [2:0]  cfg_bag_exp_active // reconstructed committed BAG exponent
);
 localparam [15:0] A_CTRL      = 16'h0008,
                   A_BAG_SHADOW= 16'h0010,
                   A_BAG_COMMIT= 16'h0018;

 // ------------------------------------------------------------------
 // Management write-stream shadow: CTRL.run and the committed BAG shadow.
 // Exact because afdx_mgmt_top itself only writes CTRL[1] (snapshot) --
 // always with run preserved -- and never writes BAG_SHADOW.
 // ------------------------------------------------------------------
 reg        mgmt_run_r;
 reg [2:0]  bag_shadow_r;
 reg [2:0]  bag_active_r;
 wire       mgmt_bus_write = mgmt_we;

 // Data-path cfg handshake (driven by the afdx_system_top instance below).
 wire cfg_ready_sys;
 wire cfg_reject_sys;

 always @(posedge clk) begin
  if (reset) begin
   mgmt_run_r   <= 1'b0;
   bag_shadow_r <= 3'd0;
   bag_active_r <= 3'd0;
  end else if (mgmt_bus_write) begin
   case (mgmt_addr)
    A_CTRL:       mgmt_run_r <= mgmt_wdata[0];
    A_BAG_SHADOW: bag_shadow_r <= mgmt_wdata[2:0];
    A_BAG_COMMIT:
     if (mgmt_wdata[0] && !mgmt_run_r)
      bag_active_r <= bag_shadow_r;  // accepted commit only
    default: ;
   endcase
  end
 end

 assign mgmt_run           = mgmt_run_r;
 assign cfg_bag_exp_active = bag_active_r;

 // A commit write is accepted by the register slice only while its own run is 0.
 wire mgmt_commit_req = mgmt_bus_write && (mgmt_addr == A_BAG_COMMIT) &&
                        mgmt_wdata[0] && !mgmt_run_r;

 // ------------------------------------------------------------------
 // BAG commit -> per-VL data-path configuration sequencer.
 // cfg_valid/cfg_vl/cfg_bag_exp are held stable per VL until cfg_ready.
 // ------------------------------------------------------------------
 localparam [1:0] CS_IDLE  = 2'd0,
                  CS_WRITE = 2'd1,
                  CS_DONE  = 2'd2;
 localparam [VL_WIDTH-1:0] CFG_LAST_VL = CFG_VL_COUNT-1;

 reg [1:0]          cs_state;
 reg [VL_WIDTH-1:0] cs_vl;
 reg [2:0]          cs_bag;
 reg                cfg_valid_r;
 reg                cfg_done_r;

 always @(posedge clk) begin
  if (reset) begin
   cs_state    <= CS_IDLE;
   cs_vl       <= {VL_WIDTH{1'b0}};
   cs_bag      <= 3'd0;
   cfg_valid_r <= 1'b0;
   cfg_done_r  <= 1'b0;
  end else begin
   cfg_done_r <= 1'b0;
   case (cs_state)
    CS_IDLE: begin
     cfg_valid_r <= 1'b0;
     if (mgmt_commit_req) begin
      cs_bag      <= bag_shadow_r;
      cs_vl       <= {VL_WIDTH{1'b0}};
      cfg_valid_r <= 1'b1;
      cs_state    <= CS_WRITE;
     end
    end
    CS_WRITE: begin
     cfg_valid_r <= 1'b1;
     // Wait on the DATA PATH ready (cfg_ready_sys), not on the wrapper output
     // cfg_ready, which is masked while management owns the configuration port.
     if (cfg_ready_sys) begin
      if (cs_vl == CFG_LAST_VL) begin
       cfg_valid_r <= 1'b0;
       cfg_done_r  <= 1'b1;
       cs_state    <= CS_DONE;
      end else begin
       cs_vl <= cs_vl + 1'b1;
      end
     end
    end
    CS_DONE: begin
     cfg_valid_r <= 1'b0;
     cfg_done_r  <= 1'b0;
     cs_state    <= CS_IDLE;
    end
    default: cs_state <= CS_IDLE;
   endcase
  end
 end

 assign cfg_busy = (cs_state != CS_IDLE);
 assign cfg_done = cfg_done_r;

 // Management priority over the external cfg port.
 wire mgmt_cfg_active = (cs_state != CS_IDLE) || mgmt_commit_req;
 wire cfg_valid_sys   = mgmt_cfg_active ? cfg_valid_r
                                        : (ext_cfg_valid && !mgmt_commit_req);
 wire [VL_WIDTH-1:0] cfg_vl_sys = mgmt_cfg_active ? cs_vl : ext_cfg_vl;
 wire [2:0]          cfg_bag_sys = mgmt_cfg_active ? cs_bag : ext_cfg_bag_exp;

 assign sys_run = ext_run | mgmt_run_r;

 // ------------------------------------------------------------------
 // SNMP payload-stream source mux (test direct or RX application stream).
 // ------------------------------------------------------------------
 wire        snmp_s_ready_w;
 wire [7:0]  snmp_s_data_w;
 wire        snmp_s_valid_w;
 wire        snmp_s_sop_w;
 wire        snmp_s_eop_w;
 wire        rx_ready_sys;

 // First-byte handshake note: the SNMP agent captures its first byte in IDLE
 // even though s_ready is 0 there (s_ready only marks its RX state).  For the
 // RX-sourced path the adapter therefore accepts the first byte of a payload
 // without waiting for s_ready and uses s_ready for every following byte, so a
 // byte is never presented twice.  One request at a time; the RX stream is
 // backpressured while the agent consumes it.
 reg snmp_rx_sent_first;
 wire rx_handshake = snmp_s_ready_w || !snmp_rx_sent_first;

 always @(posedge clk) begin
  if (reset) snmp_rx_sent_first <= 1'b0;
  else if (snmp_src_sel && rx_valid && rx_handshake)
   snmp_rx_sent_first <= ~rx_last;   // last byte re-arms the next payload
 end

 assign snmp_s_data_w  = snmp_src_sel ? rx_data  : snmp_s_data;
 assign snmp_s_valid_w = snmp_src_sel ? rx_valid : snmp_s_valid;
 assign snmp_s_sop_w   = snmp_src_sel ? (rx_valid && !snmp_rx_sent_first) : snmp_s_sop;
 assign snmp_s_eop_w   = snmp_src_sel ? rx_last : snmp_s_eop;
 assign rx_ready_sys   = snmp_src_sel ? rx_handshake : rx_ready;

 // ------------------------------------------------------------------
 // Wrapper-level frame / octet counters feeding the management slice.
 // ------------------------------------------------------------------
 reg [31:0] tx_frame_count;
 reg [31:0] in_octets;
 reg [31:0] out_octets;

 always @(posedge clk) begin
  if (reset) begin
   tx_frame_count <= 32'd0;
   in_octets      <= 32'd0;
   out_octets     <= 32'd0;
  end else begin
   if (sfd_pulse)                        tx_frame_count <= tx_frame_count + 32'd1;
   if (tx_line_valid && tx_byte_ce)      out_octets     <= out_octets + 32'd1;
   if (rx_line_valid_a && tx_byte_ce)    in_octets      <= in_octets + 32'd1;
  end
 end

 // afdx_system_top exposes bad_fcs and full_drops as TWO packed 32-bit
 // per-network counters ({network B, network A}), not as scalar totals.  The
 // management slice is fed scalar totals:
 //   bad_fcs_total   = bad_fcs[31:0]   + bad_fcs[63:32]   (64-bit, exact)
 //   full_drops_total= full_drops[31:0]+ full_drops[63:32]
 //   drop_frames     = saturate32(full_drops_total)
 wire [63:0] bad_fcs_sum    = {32'd0, bad_fcs[31:0]}    + {32'd0, bad_fcs[63:32]};
 wire [63:0] full_drops_sum = {32'd0, full_drops[31:0]} + {32'd0, full_drops[63:32]};
 assign bad_fcs_total = bad_fcs_sum;

 // 64-bit -> 32-bit saturating conversion for the drop counter view.
 function [31:0] sat32;
  input [63:0] value;
  begin
   sat32 = (value[63:32] != 32'd0) ? 32'hFFFF_FFFF : value[31:0];
  end
 endfunction

 wire [31:0] drop_frames32 = sat32(full_drops_sum);

 // ------------------------------------------------------------------
 // Frozen data path.
 // ------------------------------------------------------------------
 afdx_system_top #(
  .VL_WIDTH(VL_WIDTH),
  .SLOT_WIDTH(SLOT_WIDTH),
  .MAX_PER_VL(MAX_PER_VL),
  .SKEW_MAX(SKEW_MAX),
  .INITIAL_TIME_LOW(INITIAL_TIME_LOW),
  .INITIAL_TIME_HIGH(INITIAL_TIME_HIGH)
 ) u_system (
  .clk(clk), .reset(reset),
  .tx_line_data(tx_line_data), .tx_line_valid(tx_line_valid), .tx_byte_ce(tx_byte_ce),
  .rx_line_data_a(rx_line_data_a), .rx_line_valid_a(rx_line_valid_a), .rx_line_error_a(rx_line_error_a),
  .rx_line_data_b(rx_line_data_b), .rx_line_valid_b(rx_line_valid_b), .rx_line_error_b(rx_line_error_b),
  .app_msg_valid(app_msg_valid), .app_msg_ready(app_msg_ready),
  .app_vl(app_vl), .app_length(app_length),
  .app_data(app_data), .app_valid(app_valid), .app_last(app_last), .app_ready(app_ready),
  .app_rsp_valid(app_rsp_valid), .app_rsp_ready(app_rsp_ready), .app_rsp_status(app_rsp_status),
  .drop_valid(drop_valid), .drop_ready(drop_ready), .drop_vl(drop_vl),
  .drop_rsp_valid(drop_rsp_valid), .drop_rsp_ready(drop_rsp_ready), .drop_rsp_status(drop_rsp_status),
  .cfg_valid(cfg_valid_sys), .cfg_vl(cfg_vl_sys), .cfg_bag_exp(cfg_bag_sys),
  .cfg_ready(cfg_ready_sys), .cfg_reject(cfg_reject_sys),
  .run(sys_run), .app_enable(app_enable),
  .rx_data(rx_data), .rx_valid(rx_valid), .rx_last(rx_last), .rx_ready(rx_ready_sys),
  .rx_vl(rx_vl), .rx_network(rx_network), .rx_sn(rx_sn), .rx_len(rx_len), .rx_time(rx_time),
  .rx_decision(rx_decision), .rx_decision_pass(rx_decision_pass), .rx_reason(rx_reason),
  .initialized(initialized), .monotonic_time(monotonic_time),
  .delivered_frames(delivered_frames), .rejected_frames(rejected_frames),
  .bad_fcs(bad_fcs), .full_drops(full_drops),
  .sfd_pulse(sfd_pulse), .sfd_time(sfd_time), .sfd_vl(sfd_vl),
  .tx_fault(tx_fault), .busy(busy)
 );

 // cfg_ready / cfg_reject presented to the external master.  The data path's
 // cfg_ready is a level ("a cfg_vl would be accepted now"), so gate it with the
 // management-programming window and surface rejection during that window.
 assign cfg_ready  = cfg_ready_sys && !mgmt_cfg_active;
 assign cfg_reject = ext_cfg_valid && (mgmt_cfg_active || cfg_reject_sys);

 // ------------------------------------------------------------------
 // Frozen management slice.
 // ------------------------------------------------------------------
 afdx_mgmt_top u_mgmt (
  .clk(clk), .reset(reset),
  .addr(mgmt_addr), .wdata(mgmt_wdata), .we(mgmt_we), .re(mgmt_re),
  .rdata(mgmt_rdata), .ready(mgmt_ready), .error(mgmt_error),
  .s_valid(snmp_s_valid_w), .s_data(snmp_s_data_w),
  .s_sop(snmp_s_sop_w), .s_eop(snmp_s_eop_w), .s_ready(snmp_s_ready_w),
  .m_valid(snmp_m_valid), .m_data(snmp_m_data), .m_sop(snmp_m_sop), .m_eop(snmp_m_eop),
  .m_ready(snmp_m_ready),
  .time_now(monotonic_time),
  .stat_delivered_frames(delivered_frames),
  .stat_rejected_frames(rejected_frames),
  .stat_drop_frames(drop_frames32),
  .stat_bad_fcs(bad_fcs_sum),
  .stat_tx_frames(tx_frame_count),
  .cnt_in_octets(in_octets),
  .cnt_out_octets(out_octets),
  .snap(mgmt_snap)
 );

 assign tx_frames    = tx_frame_count;
 assign drop_frames  = drop_frames32;
endmodule
