`timescale 1ns/1ps
module mac_pair(input wire clk,reset,byte_ce,input wire [63:0] time_now,
 input wire [7:0] tx_data,input wire tx_valid,tx_sop,tx_eop,output wire tx_ready,
 output wire [7:0] line_tx_data,output wire line_tx_valid,output wire tx_sfd_pulse,output wire [63:0] tx_sfd_time,
 output wire tx_done,tx_reject,output wire [1:0] tx_slots,
 input wire [7:0] line_rx_data,input wire line_rx_valid,line_rx_error,
 output wire [7:0] rx_data,output wire rx_valid,rx_sop,rx_eop,input wire rx_ready,output wire [63:0] rx_timestamp,
 output wire rx_sfd_pulse,output wire [63:0] rx_sfd_time,output wire rx_drop,output wire [1:0] rx_drop_reason,
 output wire [31:0] rx_good_frames,rx_bad_fcs,rx_full_drops,rx_format_drops,output wire [1:0] rx_slots);
 eth_mac_tx tx(.clk(clk),.reset(reset),.byte_ce(byte_ce),.time_now(time_now),
 .s_data(tx_data),.s_valid(tx_valid),.s_sop(tx_sop),.s_eop(tx_eop),.s_ready(tx_ready),
 .line_data(line_tx_data),.line_valid(line_tx_valid),.sfd_pulse(tx_sfd_pulse),.sfd_time(tx_sfd_time),.done(tx_done),.reject(tx_reject),.slots_used(tx_slots));
 eth_mac_rx rx(.clk(clk),.reset(reset),.byte_ce(byte_ce),.time_now(time_now),
 .line_data(line_rx_data),.line_valid(line_rx_valid),.line_error(line_rx_error),
 .m_data(rx_data),.m_valid(rx_valid),.m_sop(rx_sop),.m_eop(rx_eop),.m_ready(rx_ready),.m_sfd_time(rx_timestamp),
 .sfd_pulse(rx_sfd_pulse),.sfd_time(rx_sfd_time),.drop(rx_drop),.drop_reason(rx_drop_reason),
 .good_frames(rx_good_frames),.bad_fcs(rx_bad_fcs),.full_drops(rx_full_drops),.format_drops(rx_format_drops),.slots_used(rx_slots));
endmodule
