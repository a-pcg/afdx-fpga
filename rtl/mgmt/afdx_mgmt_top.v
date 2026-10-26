`timescale 1ns/1ps
// afdx_mgmt_top -- standalone management integration slice.
//
// Combines afdx_mgmt_regs (management registers, counters snapshot, RTC) and
// afdx_snmp_agent (SNMPv1 GET/GETNEXT engine) behind one external management
// bus and one UDP-payload byte-stream interface.
//
// NOT integrated into afdx_system_top / E5; no IP/UDP/MAC encapsulation, no
// SNMP SET/multi-varbind/v2/v3/trap, no 615A, no RTC servo.
//
// Bus arbitration: external management bus has priority; a one-cycle external
// write to CTRL issues the mgmt_regs snapshot command when snap is pulsed; the
// SNMP agent is read-only and only gets the bus when it is otherwise idle.
// The internal snapshot write preserves CTRL.run.
//
// Snapshot semantics: pulse snap for one clock while the external bus is idle;
// the slice writes CTRL[1] (snapshot) once, then the SNMP agent can read the
// snapshot/RTC registers.  A snap pulse during an external transaction is
// retried after it completes (one pending request, no queue).
module afdx_mgmt_top (
 input  wire        clk,
 input  wire        reset,
 // external management bus
 input  wire [15:0] addr,
 input  wire [31:0] wdata,
 input  wire        we,
 input  wire        re,
 output wire [31:0] rdata,
 output wire        ready,
 output wire        error,
 // UDP payload byte stream (no IP/UDP headers)
 input  wire        s_valid,
 input  wire [7:0]  s_data,
 input  wire        s_sop,
 input  wire        s_eop,
 output wire        s_ready,
 output wire        m_valid,
 output wire [7:0]  m_data,
 output wire        m_sop,
 output wire        m_eop,
 input  wire        m_ready,
 // data-path counters / time base / snapshot trigger
 input  wire [63:0] time_now,
 input  wire [31:0] stat_delivered_frames,
 input  wire [31:0] stat_rejected_frames,
 input  wire [31:0] stat_drop_frames,
 input  wire [63:0] stat_bad_fcs,
 input  wire [31:0] stat_tx_frames,
 input  wire [31:0] cnt_in_octets,
 input  wire [31:0] cnt_out_octets,
 input  wire        snap
);
 // regs <-> bus
 wire [15:0] regs_addr;
 wire [31:0] regs_wdata;
 wire        regs_we, regs_re;
 wire [31:0] regs_rdata;
 wire        regs_ready, regs_error;
 wire        run;
 wire [2:0]  bag_active_w;
 wire        bag_committed_w;

 // agent read bus
 wire [15:0] agent_addr;
 wire        agent_re;
 wire [31:0] agent_rdata;
 wire        agent_ready;

 // snapshot command FSM: IDLE -> WRITE(1 accepted cycle) -> DONE(2) -> IDLE
 reg [1:0] snap_state;
 localparam [1:0] S_IDLE=2'd0, S_WRITE=2'd1, S_DONE=2'd2;
 reg snap_pending;

 wire ext_active = we | re;
 wire snap_write = (snap_state == S_WRITE) && !ext_active;
 wire serve_ext  = ext_active;
 wire serve_snap = snap_write;
 wire serve_agent= !ext_active && !serve_snap && (snap_state == S_IDLE) && agent_re;

 assign regs_addr  = serve_ext ? addr : (serve_snap ? 16'h0008 : agent_addr);
 assign regs_wdata = serve_ext ? wdata : ({31'd0, run} | 32'd2);
 assign regs_we    = serve_ext ? we : serve_snap;
 assign regs_re    = serve_ext ? re : (serve_agent ? 1'b1 : 1'b0);

 assign rdata  = regs_rdata;
 assign ready  = regs_ready && serve_ext;
 assign error  = regs_error && serve_ext;
 assign agent_ready = regs_ready && serve_agent;

 always @(posedge clk) begin
  if (reset) begin
   snap_state <= S_IDLE;
   snap_pending <= 1'b0;
  end else begin
   case (snap_state)
    S_IDLE: begin
     if (snap) snap_pending <= 1'b1;
     else if (snap_pending) begin
      if (!ext_active) begin
       snap_state <= S_WRITE;
       snap_pending <= 1'b0;
      end
     end
    end
    S_WRITE: begin
     if (!ext_active) snap_state <= S_DONE;
    end
    S_DONE: begin
     snap_state <= S_IDLE;
    end
    default: snap_state <= S_IDLE;
   endcase
  end
 end

 afdx_mgmt_regs u_regs (
  .clk(clk), .reset(reset),
  .addr(regs_addr), .wdata(regs_wdata), .we(regs_we), .re(regs_re),
  .rdata(regs_rdata), .ready(regs_ready), .error(regs_error),
  .run(run), .bag_active(bag_active_w), .bag_committed(bag_committed_w),
  .stat_delivered_frames(stat_delivered_frames),
  .stat_rejected_frames(stat_rejected_frames),
  .stat_drop_frames(stat_drop_frames),
  .stat_bad_fcs(stat_bad_fcs),
  .stat_tx_frames(stat_tx_frames),
  .time_now(time_now)
 );

 afdx_snmp_agent u_snmp (
  .clk(clk), .reset(reset),
  .s_valid(s_valid), .s_data(s_data), .s_sop(s_sop), .s_eop(s_eop), .s_ready(s_ready),
  .m_valid(m_valid), .m_data(m_data), .m_sop(m_sop), .m_eop(m_eop), .m_ready(m_ready),
  .reg_addr(agent_addr), .reg_re(agent_re), .reg_rdata(regs_rdata), .reg_ready(agent_ready),
  .cnt_in_octets(cnt_in_octets), .cnt_out_octets(cnt_out_octets)
 );
endmodule
