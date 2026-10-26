`timescale 1ns/1ps
// Test-only passive lane and MAC state observation; production RTL unchanged.
module rx_concurrent_probe(
 input wire clk,reset,input wire [63:0] time_now,
 input wire [1:0] byte_ce,line_valid,line_error,input wire [15:0] line_data,
 output wire [7:0] m_data,output wire m_valid,m_last,input wire m_ready,
 output wire [2:0] m_vl,output wire m_network,output wire [7:0] m_sn,
 output wire [15:0] m_len,output wire [63:0] m_time,
 output wire initialized,output wire [63:0] bad_fcs,full_drops,
 output wire decision,decision_pass,output wire [2:0] decision_reason,
 output wire busy,output wire [1:0] armed_mask,output wire tables_initialized,
 output wire [3:0] mac_slots,output wire [1:0] mac_drop,
 output wire [3:0] mac_drop_reason
);
 afdx_rx_dual dut(.clk(clk),.reset(reset),.time_now(time_now),.byte_ce(byte_ce),
 .line_data(line_data),.line_valid(line_valid),.line_error(line_error),
 .m_data(m_data),.m_valid(m_valid),.m_last(m_last),.m_ready(m_ready),
 .m_vl(m_vl),.m_network(m_network),.m_sn(m_sn),.m_len(m_len),.m_time(m_time),
 .initialized(initialized),.bad_fcs(bad_fcs),.full_drops(full_drops),
 .decision(decision),.decision_pass(decision_pass),.decision_reason(decision_reason),
 .delivered_frames(),.rejected_frames(),.busy(busy));
 assign armed_mask=dut.armed;assign tables_initialized=dut.tables_initialized;
 assign mac_slots={dut.networks[1].mac.slots_used,dut.networks[0].mac.slots_used};
 assign mac_drop={dut.networks[1].mac.drop,dut.networks[0].mac.drop};
 assign mac_drop_reason={dut.networks[1].mac.drop_reason,dut.networks[0].mac.drop_reason};
endmodule
