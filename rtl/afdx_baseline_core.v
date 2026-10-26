`timescale 1ns/1ps
// Explicit M1 extended interface. NOT the contest AFDX_top adapter.
module afdx_baseline_core(
 input wire clk,reset,
 input wire [15:0] app_tx_len,input wire app_tx_msg_valid,output wire app_tx_msg_ready,
 input wire [7:0] app_tx_data,input wire app_tx_valid,app_tx_last,output wire app_tx_ready,
 output wire [7:0] mac_tx_data,output wire mac_tx_valid,mac_tx_sop,mac_tx_eop,input wire mac_tx_ready,
 output wire tx_reject,tx_done,
 input wire [7:0] mac_rx_data,input wire mac_rx_valid,mac_rx_sop,mac_rx_eop,mac_rx_error,output wire mac_rx_ready,
 output wire [7:0] app_rx_data,output wire app_rx_valid,app_rx_last,input wire app_rx_ready,
 output wire [15:0] app_rx_len,output wire [7:0] app_rx_sn,
 output wire rx_drop,output wire [3:0] rx_drop_reason,output wire rx_accepted);
 afdx_udp_tx tx(.clk(clk),.reset(reset),.msg_len(app_tx_len),.msg_valid(app_tx_msg_valid),.msg_ready(app_tx_msg_ready),
 .s_data(app_tx_data),.s_valid(app_tx_valid),.s_last(app_tx_last),.s_ready(app_tx_ready),
 .m_data(mac_tx_data),.m_valid(mac_tx_valid),.m_ready(mac_tx_ready),.m_sop(mac_tx_sop),.m_eop(mac_tx_eop),
 .reject(tx_reject),.done(tx_done));
 afdx_udp_rx rx(.clk(clk),.reset(reset),.s_data(mac_rx_data),.s_valid(mac_rx_valid),.s_sop(mac_rx_sop),
 .s_eop(mac_rx_eop),.s_error(mac_rx_error),.s_ready(mac_rx_ready),
 .m_data(app_rx_data),.m_valid(app_rx_valid),.m_last(app_rx_last),.m_ready(app_rx_ready),
 .m_len(app_rx_len),.m_sn(app_rx_sn),.drop(rx_drop),.drop_reason(rx_drop_reason),.accepted(rx_accepted),.m_done());
endmodule
