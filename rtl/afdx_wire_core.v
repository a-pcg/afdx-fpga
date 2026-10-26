`timescale 1ns/1ps
// Single-VL UDP core plus two-slot octet MACs. A byte tick every 8 clocks
// models 100 Mbps at the 100 MHz target clock; no board-specific PHY included.
module afdx_wire_core(
 input wire clk,reset,
 input wire [15:0] app_tx_len,input wire app_tx_msg_valid,output wire app_tx_msg_ready,
 input wire [7:0] app_tx_data,input wire app_tx_valid,app_tx_last,output wire app_tx_ready,
 output wire [7:0] line_tx_data,output wire line_tx_valid,
 input wire [7:0] line_rx_data,input wire line_rx_valid,line_rx_error,
 output wire byte_tick,output wire [63:0] monotonic_time,
 output wire tx_reject,tx_wire_done,output wire tx_sfd_pulse,output wire [63:0] tx_sfd_time,
 output wire [7:0] app_rx_data,output wire app_rx_valid,app_rx_last,input wire app_rx_ready,
 output wire [15:0] app_rx_len,output wire [7:0] app_rx_sn,
 output wire rx_drop,output wire [3:0] rx_drop_reason,output wire rx_accepted,
 output wire mac_rx_drop,output wire [1:0] mac_rx_drop_reason,
 output wire [31:0] mac_good_frames,mac_bad_fcs,mac_full_drops,mac_format_drops,
 output wire mac_rx_sfd_pulse,output wire [63:0] mac_rx_sfd_time);
 reg [2:0] divider;reg [63:0] ticks;
 wire [7:0] client_tx_data,client_rx_data;
 wire client_tx_valid,client_tx_sop,client_tx_eop,client_tx_ready;
 wire client_rx_valid,client_rx_sop,client_rx_eop,client_rx_ready;
 wire udp_reject,mac_reject,client_tx_done;
 wire [63:0] client_rx_time;wire [1:0] unused_tx_slots,unused_rx_slots;
 assign byte_tick=(divider==0)&&!reset;assign monotonic_time=ticks;
 assign tx_reject=udp_reject||mac_reject;
 always @(posedge clk)begin
 if(reset)begin divider<=0;ticks<=0;end
 else begin divider<=divider+3'd1;ticks<=ticks+64'd1;end
 end
 afdx_baseline_core core(.clk(clk),.reset(reset),
 .app_tx_len(app_tx_len),.app_tx_msg_valid(app_tx_msg_valid),.app_tx_msg_ready(app_tx_msg_ready),
 .app_tx_data(app_tx_data),.app_tx_valid(app_tx_valid),.app_tx_last(app_tx_last),.app_tx_ready(app_tx_ready),
 .mac_tx_data(client_tx_data),.mac_tx_valid(client_tx_valid),.mac_tx_sop(client_tx_sop),.mac_tx_eop(client_tx_eop),.mac_tx_ready(client_tx_ready),
 .tx_reject(udp_reject),.tx_done(client_tx_done),
 .mac_rx_data(client_rx_data),.mac_rx_valid(client_rx_valid),.mac_rx_sop(client_rx_sop),.mac_rx_eop(client_rx_eop),.mac_rx_error(1'b0),.mac_rx_ready(client_rx_ready),
 .app_rx_data(app_rx_data),.app_rx_valid(app_rx_valid),.app_rx_last(app_rx_last),.app_rx_ready(app_rx_ready),
 .app_rx_len(app_rx_len),.app_rx_sn(app_rx_sn),.rx_drop(rx_drop),.rx_drop_reason(rx_drop_reason),.rx_accepted(rx_accepted));
 eth_mac_tx mac_tx(.clk(clk),.reset(reset),.byte_ce(byte_tick),.time_now(ticks),
 .s_data(client_tx_data),.s_valid(client_tx_valid),.s_sop(client_tx_sop),.s_eop(client_tx_eop),.s_ready(client_tx_ready),
 .line_data(line_tx_data),.line_valid(line_tx_valid),.sfd_pulse(tx_sfd_pulse),.sfd_time(tx_sfd_time),.done(tx_wire_done),.reject(mac_reject),.slots_used(unused_tx_slots));
 eth_mac_rx mac_rx(.clk(clk),.reset(reset),.byte_ce(byte_tick),.time_now(ticks),
 .line_data(line_rx_data),.line_valid(line_rx_valid),.line_error(line_rx_error),
 .m_data(client_rx_data),.m_valid(client_rx_valid),.m_sop(client_rx_sop),.m_eop(client_rx_eop),.m_ready(client_rx_ready),.m_sfd_time(client_rx_time),
 .sfd_pulse(mac_rx_sfd_pulse),.sfd_time(mac_rx_sfd_time),.drop(mac_rx_drop),.drop_reason(mac_rx_drop_reason),
 .good_frames(mac_good_frames),.bad_fcs(mac_bad_fcs),.full_drops(mac_full_drops),.format_drops(mac_format_drops),.slots_used(unused_rx_slots));
endmodule
