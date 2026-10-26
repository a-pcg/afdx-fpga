`timescale 1ns/1ps
// Joint development top v0.2: shared-pool transmit + dual-network receive on one clock.
// Development mapping: internal index 0..7 <-> wire VLID 1..8 <-> DA 03:00:00:00:00:01+index.
// One synchronous active-high reset for MAC, pool, queues, SN, IC/RM and in-flight state.
// Line ports are byte streams paced by byte_ce with no ready; the PHY stays outside this top.
// RX samples the TX monotonic cycle counter, so BAG and SFD timestamps share one time base.
module afdx_system_top #(
 parameter VL_WIDTH=7,SLOT_WIDTH=6,MAX_PER_VL=4,
 // P1 time base: one source of truth for the core clock and the byte rhythm. The
 // RX skew window (1 ms) and RM resync interval (10 ms) are derived in core cycles
 // from CYCLES_PER_MS so their physical meaning does not change with the clock; they
 // can still be overridden explicitly. External time parameters stay in core cycles.
 parameter [31:0] CORE_CLK_HZ=32'd100000000,
 parameter [31:0] BYTE_CYCLES=32'd8,
 parameter [63:0] CYCLES_PER_MS={32'd0,CORE_CLK_HZ}/64'd1000,
 parameter [63:0] SKEW_MAX=CYCLES_PER_MS,
 parameter RM_EXPECTED_SN=0,parameter [31:0] RM_GAP_TOLERANCE=2,
 parameter [63:0] RM_RESYNC_TIMEOUT=64'd10*CYCLES_PER_MS,parameter RM_SN0_RESTART=1,
 parameter [31:0] INITIAL_TIME_LOW=32'd0,INITIAL_TIME_HIGH=32'd0
)(
 input wire clk,reset,
 // physical line toward the PHY (transmit)
 output wire [7:0] tx_line_data,output wire tx_line_valid,output wire tx_byte_ce,
 // physical line from the PHY (receive networks A and B)
 input wire [7:0] rx_line_data_a,input wire rx_line_valid_a,input wire rx_line_error_a,
 input wire [7:0] rx_line_data_b,input wire rx_line_valid_b,input wire rx_line_error_b,
 // application transmit
 input wire app_msg_valid,output wire app_msg_ready,input wire [VL_WIDTH-1:0] app_vl,input wire [11:0] app_length,
 input wire [7:0] app_data,input wire app_valid,input wire app_last,output wire app_ready,
 output wire app_rsp_valid,input wire app_rsp_ready,output wire [3:0] app_rsp_status,
 input wire drop_valid,output wire drop_ready,input wire [VL_WIDTH-1:0] drop_vl,
 output wire drop_rsp_valid,input wire drop_rsp_ready,output wire [3:0] drop_rsp_status,
 // configuration and run control
 input wire cfg_valid,input wire [VL_WIDTH-1:0] cfg_vl,input wire [2:0] cfg_bag_exp,
 output wire cfg_ready,output wire cfg_reject,
 input wire run,input wire app_enable,
 // application receive
 output wire [7:0] rx_data,output wire rx_valid,output wire rx_last,input wire rx_ready,
 output wire [2:0] rx_vl,output wire rx_network,output wire [7:0] rx_sn,
 output wire [15:0] rx_len,output wire [63:0] rx_time,
 output wire rx_decision,output wire rx_decision_pass,output wire [2:0] rx_reason,
 // status and simulation observability
 output wire initialized,output wire [63:0] monotonic_time,
 output wire [31:0] delivered_frames,output wire [31:0] rejected_frames,
 output wire [63:0] bad_fcs,output wire [63:0] full_drops,
 output wire sfd_pulse,output wire [63:0] sfd_time,output wire [VL_WIDTH-1:0] sfd_vl,
 output wire tx_fault,output wire busy
);
 wire byte_tick,tx_initialized,rx_initialized,rx_busy,tx_busy;
 wire [63:0] tx_time;

 afdx_shared_tx #(.VL_WIDTH(VL_WIDTH),.SLOT_WIDTH(SLOT_WIDTH),.MAX_PER_VL(MAX_PER_VL),
  .CORE_CLK_HZ(CORE_CLK_HZ),.BYTE_CYCLES(BYTE_CYCLES),.CYCLES_PER_MS(CYCLES_PER_MS),
  .INITIAL_TIME_LOW(INITIAL_TIME_LOW),.INITIAL_TIME_HIGH(INITIAL_TIME_HIGH)) tx (
  .clk(clk),.reset(reset),.run(run),.app_enable(app_enable),
  .cfg_valid(cfg_valid),.cfg_vl(cfg_vl),.cfg_bag_exp(cfg_bag_exp),.cfg_ready(cfg_ready),.cfg_reject(cfg_reject),
  .app_msg_valid(app_msg_valid),.app_msg_ready(app_msg_ready),.app_vl(app_vl),.app_length(app_length),
  .app_data(app_data),.app_valid(app_valid),.app_last(app_last),.app_ready(app_ready),
  .app_rsp_valid(app_rsp_valid),.app_rsp_ready(app_rsp_ready),.app_rsp_status(app_rsp_status),
  .drop_valid(drop_valid),.drop_ready(drop_ready),.drop_vl(drop_vl),
  .drop_rsp_valid(drop_rsp_valid),.drop_rsp_ready(drop_rsp_ready),.drop_rsp_status(drop_rsp_status),
  .line_data(tx_line_data),.line_valid(tx_line_valid),.byte_tick(byte_tick),
  .monotonic_time(tx_time),.sfd_pulse(sfd_pulse),.sfd_time(sfd_time),.sfd_vl(sfd_vl),
  .pending(),.pool_free(),.initialized(tx_initialized),.busy(tx_busy),.fault(tx_fault));

 afdx_rx_dual #(.SKEW_MAX(SKEW_MAX),.RM_EXPECTED_SN(RM_EXPECTED_SN),.RM_GAP_TOLERANCE(RM_GAP_TOLERANCE),.RM_RESYNC_TIMEOUT(RM_RESYNC_TIMEOUT),.RM_SN0_RESTART(RM_SN0_RESTART)) rx (
  .clk(clk),.reset(reset),.time_now(tx_time),.byte_ce({byte_tick,byte_tick}),
  .line_valid({rx_line_valid_b,rx_line_valid_a}),.line_error({rx_line_error_b,rx_line_error_a}),
  .line_data({rx_line_data_b,rx_line_data_a}),
  .m_data(rx_data),.m_valid(rx_valid),.m_last(rx_last),.m_ready(rx_ready),
  .m_vl(rx_vl),.m_network(rx_network),.m_sn(rx_sn),.m_len(rx_len),.m_time(rx_time),
  .initialized(rx_initialized),.bad_fcs(bad_fcs),.full_drops(full_drops),
  .decision(rx_decision),.decision_pass(rx_decision_pass),.decision_reason(rx_reason),
  .delivered_frames(delivered_frames),.rejected_frames(rejected_frames),.busy(rx_busy));

 assign tx_byte_ce=byte_tick;
 assign monotonic_time=tx_time;
 assign initialized=tx_initialized&&rx_initialized;
 assign busy=tx_busy||rx_busy;
endmodule
