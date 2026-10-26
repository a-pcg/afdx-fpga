`timescale 1ns/1ps
// TEST ONLY: passive hierarchical observation. Frozen DUT files are unchanged.
module rx_r2_probe #(
 parameter RM_EXPECTED_SN=0
)(
 input wire clk,reset,input wire [63:0] time_now,
 input wire [1:0] byte_ce,line_valid,line_error,input wire [15:0] line_data,
 output wire [7:0] m_data,output wire m_valid,m_last,input wire m_ready,
 output wire [2:0] m_vl,output wire m_network,output wire [7:0] m_sn,
 output wire [15:0] m_len,output wire [63:0] m_time,
 output wire initialized,output wire decision,decision_pass,
 output wire [2:0] decision_reason,output wire busy,
 output wire ic_fire,ic_pass,ic_network,output wire [7:0] ic_sn,
 output wire [1:0] ic_reason,armed,output wire tables_initialized,
 output wire [63:0] mac_good_frames
);
 afdx_rx_dual #(.RM_EXPECTED_SN(RM_EXPECTED_SN)) dut(.clk(clk),.reset(reset),.time_now(time_now),.byte_ce(byte_ce),
 .line_valid(line_valid),.line_error(line_error),.line_data(line_data),
 .m_data(m_data),.m_valid(m_valid),.m_last(m_last),.m_ready(m_ready),
 .m_vl(m_vl),.m_network(m_network),.m_sn(m_sn),.m_len(m_len),.m_time(m_time),
 .initialized(initialized),.bad_fcs(),.full_drops(),.decision(decision),
 .decision_pass(decision_pass),.decision_reason(decision_reason),
 .delivered_frames(),.rejected_frames(),.busy(busy));
 assign ic_fire=dut.merge.ic.m_valid && dut.merge.ic.m_ready;
 assign ic_pass=dut.merge.ic.m_pass;
 assign ic_reason=dut.merge.ic.m_reason;
 assign ic_network=dut.merge.ic.m_network;
 assign ic_sn=dut.merge.ic.m_sn;
 assign armed=dut.armed;
 assign tables_initialized=dut.tables_initialized;
 assign mac_good_frames={dut.networks[1].mac.good_frames,dut.networks[0].mac.good_frames};
endmodule
