`timescale 1ns/1ps
// E5: physical-pin boundary around the joint AFDX system top (TX + dual-network RX).
// Only board-level signals are exposed; debug counters and the 64-bit monotonic
// time / SFD observability ports stay inside the design.
module afdx_sys_e5_top (
 input  wire        clk,
 input  wire        reset,
 // transmit line toward the PHY
 output wire [7:0]  tx_line_data,
 output wire        tx_line_valid,
 // receive lines from the PHY (networks A and B)
 input  wire [7:0]  rx_line_data_a,
 input  wire        rx_line_valid_a,
 input  wire        rx_line_error_a,
 input  wire [7:0]  rx_line_data_b,
 input  wire        rx_line_valid_b,
 input  wire        rx_line_error_b,
 // application transmit
 input  wire        app_msg_valid,
 output wire        app_msg_ready,
 input  wire [6:0]  app_vl,
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
 input  wire [6:0]  drop_vl,
 output wire        drop_rsp_valid,
 input  wire        drop_rsp_ready,
 output wire [3:0]  drop_rsp_status,
 // configuration and run control
 input  wire        cfg_valid,
 input  wire [6:0]  cfg_vl,
 input  wire [2:0]  cfg_bag_exp,
 output wire        cfg_ready,
 output wire        cfg_reject,
 input  wire        run,
 input  wire        app_enable,
 // application receive
 output wire [7:0]  rx_data,
 output wire        rx_valid,
 output wire        rx_last,
 input  wire        rx_ready,
 output wire [2:0]  rx_vl,
 output wire        rx_network,
 output wire [7:0]  rx_sn,
 output wire [15:0] rx_len,
 output wire        rx_decision,
 output wire        rx_decision_pass,
 output wire [2:0]  rx_reason,
 // status
 output wire        initialized,
 output wire        tx_fault,
 output wire        busy
);
 // P1: fixed elaboration parameters of this candidate. 50 MHz core with 4 core
 // cycles per line byte keeps the 80 ns byte time / 100 Mbit/s nominal line rate;
 // the matching constraint is a 20 ns clock (route_sys_e5.py --period 20).
 localparam [31:0] CORE_CLK_HZ=32'd50000000;
 localparam [31:0] BYTE_CYCLES=32'd4;
 localparam [63:0] CYCLES_PER_MS={32'd0,CORE_CLK_HZ}/64'd1000;
 afdx_system_top #(.VL_WIDTH(7),.SLOT_WIDTH(6),.MAX_PER_VL(4),
  .CORE_CLK_HZ(CORE_CLK_HZ),.BYTE_CYCLES(BYTE_CYCLES),.CYCLES_PER_MS(CYCLES_PER_MS)) dut (
  .clk(clk),.reset(reset),
  .tx_line_data(tx_line_data),.tx_line_valid(tx_line_valid),.tx_byte_ce(),
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
  .rx_vl(rx_vl),.rx_network(rx_network),.rx_sn(rx_sn),.rx_len(rx_len),.rx_time(),
  .rx_decision(rx_decision),.rx_decision_pass(rx_decision_pass),.rx_reason(rx_reason),
  .initialized(initialized),.monotonic_time(),
  .delivered_frames(),.rejected_frames(),.bad_fcs(),.full_drops(),
  .sfd_pulse(),.sfd_time(),.sfd_vl(),.tx_fault(tx_fault),.busy(busy));
endmodule
