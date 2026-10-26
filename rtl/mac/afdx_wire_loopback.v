`timescale 1ns/1ps
// Synthesizable development loop: application -> UDP/IP -> TX MAC -> RX MAC
// -> UDP/IP -> application. Byte-level link only; no PHY or ARINC compliance claim.
module afdx_wire_loopback (
    input wire clk,reset,
    input wire [15:0] app_tx_len,
    input wire app_tx_msg_valid,
    output wire app_tx_msg_ready,
    input wire [7:0] app_tx_data,
    input wire app_tx_valid,app_tx_last,
    output wire app_tx_ready,
    output wire [7:0] app_rx_data,
    output wire app_rx_valid,app_rx_last,
    input wire app_rx_ready,
    output wire [15:0] app_rx_len,
    output wire [7:0] app_rx_sn,
    input wire [7:0] inject_mask,
    input wire inject_error,
    output wire [7:0] line_data,
    output wire line_valid,line_byte_ce,line_sop,line_eop,
    output wire mac_rx_drop,
    output wire [2:0] mac_rx_drop_reason,
    output wire mac_tx_drop,
    output wire udp_rx_drop,
    output wire [3:0] udp_rx_drop_reason
);
    wire [7:0] tx_data,rx_data,raw_line;
    wire tx_valid,tx_ready,tx_sop,tx_eop;
    wire rx_valid,rx_ready,rx_sop,rx_eop;
    wire tx_reject,tx_done,rx_accepted,mac_tx_done,mac_rx_accepted;
    wire [10:0] mac_rx_len;
    assign line_data=raw_line ^ inject_mask;
    afdx_baseline_core core (.clk(clk),.reset(reset),
        .app_tx_len(app_tx_len),.app_tx_msg_valid(app_tx_msg_valid),.app_tx_msg_ready(app_tx_msg_ready),
        .app_tx_data(app_tx_data),.app_tx_valid(app_tx_valid),.app_tx_last(app_tx_last),.app_tx_ready(app_tx_ready),
        .mac_tx_data(tx_data),.mac_tx_valid(tx_valid),.mac_tx_sop(tx_sop),.mac_tx_eop(tx_eop),.mac_tx_ready(tx_ready),
        .tx_reject(tx_reject),.tx_done(tx_done),.mac_rx_data(rx_data),.mac_rx_valid(rx_valid),
        .mac_rx_sop(rx_sop),.mac_rx_eop(rx_eop),.mac_rx_error(1'b0),.mac_rx_ready(rx_ready),
        .app_rx_data(app_rx_data),.app_rx_valid(app_rx_valid),.app_rx_last(app_rx_last),.app_rx_ready(app_rx_ready),
        .app_rx_len(app_rx_len),.app_rx_sn(app_rx_sn),.rx_drop(udp_rx_drop),
        .rx_drop_reason(udp_rx_drop_reason),.rx_accepted(rx_accepted));
    afdx_eth_tx_mac tx_mac (.clk(clk),.reset(reset),.s_data(tx_data),.s_valid(tx_valid),.s_ready(tx_ready),
        .s_sop(tx_sop),.s_eop(tx_eop),.drop(mac_tx_drop),.done(mac_tx_done),.line_data(raw_line),
        .line_valid(line_valid),.line_byte_ce(line_byte_ce),.line_sop(line_sop),.line_eop(line_eop));
    afdx_eth_rx_mac rx_mac (.clk(clk),.reset(reset),.line_data(line_data),.line_valid(line_valid),
        .line_byte_ce(line_byte_ce),.line_error(inject_error),.m_data(rx_data),.m_valid(rx_valid),
        .m_ready(rx_ready),.m_sop(rx_sop),.m_eop(rx_eop),.m_len(mac_rx_len),
        .accepted(mac_rx_accepted),.drop(mac_rx_drop),.drop_reason(mac_rx_drop_reason));
endmodule
