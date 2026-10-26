`timescale 1ns/1ps
// Bounded dual-wire, eight-VL development receiver. Public MAC/UDP unchanged.
// Sixteen independent UDP payload banks; conservative lane-wide backpressure.
//
// R3 timing revisions (2026-10-19). Long combinational crossings found by
// vendor E3/E4 STA are now registered:
//  1) MAC store-and-forward readout -> eight VL parsers. The MAC frame memory
//     read and byte broadcast used to feed the parser capture/checksum cone
//     directly (12.1 ns path: RAM address register -> parser checksum adder).
//     Each parser now owns a one-byte input register (afdx_udp_rx v5), so the
//     broadcast ends in flops inside the parser and the capture/checksum adders
//     only see local registers.
//  2) Live descriptor arbitration -> merge unit. The 8-way priority encode per
//     lane and the 8:1 field mux now end in a registered per-lane descriptor
//     instead of crossing to the merge input registers in the same clock.
//  3) Drain completion. The parser pulses m_done on the last transferred byte;
//     the wrapper registers that vector, so ending a drain never muxes far
//     combinational plen/out_idx compares into the active/draining flops.
// Arbitration order (lane A first, then lowest VL), drain handshake, IC/RM
// state updates and decision contents are unchanged; descriptor and drain-end
// latency grow by one clock each.
module afdx_rx_dual #(
 parameter [63:0] SKEW_MAX=64'd100000,
 parameter RM_EXPECTED_SN=0,
 parameter [31:0] RM_GAP_TOLERANCE=2,
 parameter [63:0] RM_RESYNC_TIMEOUT=64'd1000000,
 parameter RM_SN0_RESTART=1
)(
 input wire clk,reset,input wire [63:0] time_now,
 input wire [1:0] byte_ce,line_valid,line_error,input wire [15:0] line_data,
 output wire [7:0] m_data,output wire m_valid,m_last,input wire m_ready,
 output reg [2:0] m_vl,output reg m_network,output reg [7:0] m_sn,
 output reg [15:0] m_len,output reg [63:0] m_time,
 output wire initialized,output wire [63:0] bad_fcs,full_drops,
 output reg decision,output reg decision_pass,output reg [2:0] decision_reason,
 output reg [31:0] delivered_frames,rejected_frames,output wire busy
);
 wire [15:0] udp_valid,udp_last,udp_ready,parser_ready,udp_done;
 wire [7:0] udp_data[0:15];wire [15:0] udp_len[0:15];wire [7:0] udp_sn[0:15];
 wire [7:0] mac_data[0:1];wire [1:0] mac_valid,mac_sop,mac_eop,mac_ready;
 wire [63:0] mac_time[0:1];reg [63:0] frame_time[0:1];
 wire [1:0] down_ready;
 reg [27:0] generation[0:15];
 reg active,draining,keep;reg [3:0] selected;
 reg [2:0] choice_l[0:1];reg [1:0] found_l;
 reg [1:0] d_found;reg [2:0] d_choice[0:1];
 reg [15:0] d_len[0:1];reg [7:0] d_sn[0:1];reg [31:0] d_tag[0:1];reg [63:0] d_time[0:1];
 wire [1:0] desc_ready;wire result_valid,result_pass,result_net;
 wire [2:0] result_vl,result_reason;wire [15:0] result_len;
 wire [7:0] result_sn;wire [63:0] result_time;
 wire tables_initialized;
 reg [1:0] armed;
 reg [3:0] idle_count[0:1];
 reg [15:0] done_q;
 // Service-ready compatibility output: at least one network is usable.
 // A peer stuck valid during startup must not hold the healthy MAC in reset.
 // This does not imply both networks have observed their own idle boundary.
 assign initialized=tables_initialized&&(|armed)&&!reset;
 integer k,j;
 assign busy=active;
 assign down_ready[0]=&parser_ready[7:0];
 assign down_ready[1]=&parser_ready[15:8];
 // R3 timing: each network has a two-deep elastic byte FIFO between the MAC
 // readout and its eight VL parsers (instantiated below). The MAC therefore
 // only sees a local FIFO-full flag; the eight-parser ready AND only drives the
 // FIFO read side, and both long crossings end in registers.
 always @* begin
 found_l=0;choice_l[0]=0;choice_l[1]=0;
 for(j=7;j>=0;j=j-1)begin
 if(udp_valid[j])begin found_l[0]=1;choice_l[0]=j[2:0];end
 if(udp_valid[j+8])begin found_l[1]=1;choice_l[1]=j[2:0];end
 end
 end
 // Registered delivery-complete vector: the parser pulses m_done on the last
 // transferred byte, so ending the drain never muxes far combinational state.
 always @(posedge clk)begin
 if(reset)done_q<=0;else done_q<=udp_done;
 end
 always @(posedge clk)begin
 if(reset)begin
 d_found<=0;d_choice[0]<=0;d_choice[1]<=0;d_len[0]<=0;d_len[1]<=0;
 d_sn[0]<=0;d_sn[1]<=0;d_tag[0]<=0;d_tag[1]<=0;d_time[0]<=0;d_time[1]<=0;
 end else if(active)begin
 d_found<=0;
 end else begin
 d_found<=found_l;
 d_choice[0]<=choice_l[0];d_choice[1]<=choice_l[1];
 d_len[0]<=udp_len[{1'b0,choice_l[0]}];d_len[1]<=udp_len[{1'b1,choice_l[1]}];
 d_sn[0]<=udp_sn[{1'b0,choice_l[0]}];d_sn[1]<=udp_sn[{1'b1,choice_l[1]}];
 d_tag[0]<={generation[{1'b0,choice_l[0]}],1'b0,choice_l[0]};
 d_tag[1]<={generation[{1'b1,choice_l[1]}],1'b1,choice_l[1]};
 d_time[0]<=frame_time[0];d_time[1]<=frame_time[1];
 end
 end
 genvar net,vl;
 generate for(net=0;net<2;net=net+1)begin: networks
 eth_mac_rx mac(.clk(clk),.reset(reset||!armed[net]),.byte_ce(byte_ce[net]),.time_now(time_now),
 .line_data(line_data[net*8+:8]),.line_valid(line_valid[net]),.line_error(line_error[net]),
 .m_data(mac_data[net]),.m_valid(mac_valid[net]),.m_sop(mac_sop[net]),.m_eop(mac_eop[net]),.m_ready(mac_ready[net]),
 .m_sfd_time(mac_time[net]),.sfd_pulse(),.sfd_time(),.drop(),.drop_reason(),.good_frames(),
 .bad_fcs(bad_fcs[net*32+:32]),.full_drops(full_drops[net*32+:32]),.format_drops(),.slots_used());
 always @(posedge clk)begin
 if(reset)begin armed[net]<=0;idle_count[net]<=0;end
 else if(!armed[net]&&tables_initialized&&byte_ce[net])begin
 if(line_valid[net])idle_count[net]<=0;
 else if(idle_count[net]==4'd11)armed[net]<=1;
 else idle_count[net]<=idle_count[net]+4'd1;
 end
 end
 // Elastic byte FIFO {eop,sop,data} between MAC readout and the eight parsers.
 // Push when not full; pop only when the whole network can capture, and the
 // parsed byte is only offered under the same network-wide handshake, so a
 // parser that is busy never re-loads a held byte.
 reg [9:0] fifo_a,fifo_b;reg [1:0] fifo_cnt;
 // Timestamp of the frame whose SOP byte is currently in the FIFO (at most one
 // SOP can be resident because frames are >=60 bytes apart).
 reg [63:0] sop_ts;
 wire fifo_empty=(fifo_cnt==2'd0);
 wire fifo_full=(fifo_cnt==2'd2);
 wire fifo_pop=!fifo_empty&&down_ready[net];
 wire fifo_push=mac_valid[net]&&!fifo_full;
 assign mac_ready[net]=!fifo_full;
 always @(posedge clk)begin
 if(reset)begin fifo_a<=0;fifo_b<=0;fifo_cnt<=0;sop_ts<=0;end
 else begin
 if(fifo_pop)fifo_a<=fifo_b;
 if(fifo_push)begin
 if(fifo_pop)begin
 if(fifo_cnt==2'd1)fifo_a<={mac_eop[net],mac_sop[net],mac_data[net]};
 else fifo_b<={mac_eop[net],mac_sop[net],mac_data[net]};
 end else if(fifo_cnt==2'd0)begin
 fifo_a<={mac_eop[net],mac_sop[net],mac_data[net]};fifo_cnt<=2'd1;
 end else begin
 fifo_b<={mac_eop[net],mac_sop[net],mac_data[net]};fifo_cnt<=2'd2;
 end
 end
 if(fifo_push&&mac_sop[net])sop_ts<=mac_time[net];
 if(fifo_pop&&!fifo_push)fifo_cnt<=fifo_cnt-2'd1;
 else if(!fifo_pop&&fifo_push)fifo_cnt<=fifo_cnt+2'd1;
 end
 end
 // R9c timestamp pairing: the frame SFD time travels with its SOP byte through
 // the FIFO and frame_time is latched when the *parser consumes* that SOP entry.
 // Capturing at MAC push time (r9b) let the MAC readout of the next frame
 // overwrite frame_time before the current frame's descriptor latched d_time,
 // which is exactly the joint-loopback SFD delta. In r8 the push was implicitly
 // gated by the all-parser ready (mac_ready), so the race could not happen;
 // the elastic FIFO removes that gate, so the pairing must move into the FIFO.
 always @(posedge clk)begin
 if(reset)frame_time[net]<=0;
 else if(fifo_pop&&fifo_a[8])frame_time[net]<=sop_ts;
 end
 for(vl=0;vl<8;vl=vl+1)begin: virtual_links
 localparam [47:0] DA=48'h030000000001+vl;
 afdx_udp_rx #(.DEST_MAC(DA)) udp(.clk(clk),.reset(reset),.s_data(fifo_a[7:0]),
 // R3-OPEN-1 fix, FIFO form: a byte is offered to the parsers only when the
 // whole network can take it (down_ready), so a busy parser never re-loads the
 // held byte; the byte itself stays buffered in fifo_a until that handshake.
 .s_valid(!fifo_empty&&down_ready[net]),.s_sop(fifo_a[8]),.s_eop(fifo_a[9]),.s_error(1'b0),.s_ready(parser_ready[net*8+vl]),
 .m_data(udp_data[net*8+vl]),.m_valid(udp_valid[net*8+vl]),.m_last(udp_last[net*8+vl]),.m_ready(udp_ready[net*8+vl]),
 .m_len(udp_len[net*8+vl]),.m_sn(udp_sn[net*8+vl]),.drop(),.drop_reason(),.accepted(),.m_done(udp_done[net*8+vl]));
 assign udp_ready[net*8+vl]=active&&draining&&selected==(net*8+vl)&&(!keep||m_ready);
 end end endgenerate
 afdx_rx_merge #(.SKEW_MAX(SKEW_MAX),.RM_EXPECTED_SN(RM_EXPECTED_SN),.RM_GAP_TOLERANCE(RM_GAP_TOLERANCE),.RM_RESYNC_TIMEOUT(RM_RESYNC_TIMEOUT),.RM_SN0_RESTART(RM_SN0_RESTART)) merge(.clk(clk),.reset(reset),.s_valid(d_found),.s_ready(desc_ready),
 .s_vl({d_choice[1],d_choice[0]}),.s_vlid({(16'd1+{13'd0,d_choice[1]}),(16'd1+{13'd0,d_choice[0]})}),
 .s_len({d_len[1],d_len[0]}),
 .s_sn({d_sn[1],d_sn[0]}),
 .s_tag({d_tag[1],d_tag[0]}),
 .s_time({d_time[1],d_time[0]}),.s_good(2'b11),.s_ic_enable(2'b11),
 .m_valid(result_valid),.m_ready(active&&!draining),.m_vl(result_vl),.m_vlid(),.m_len(result_len),.m_sn(result_sn),.m_tag(),
 .m_time(result_time),.m_network(result_net),.m_pass(result_pass),.m_reason(result_reason),.late_frames(),.gap_frames(),.initialized(tables_initialized));
 assign m_data=udp_data[selected];assign m_valid=active&&draining&&keep&&udp_valid[selected]&&!reset;
 assign m_last=udp_last[selected];
 always @(posedge clk)begin
 if(reset)begin active<=0;draining<=0;keep<=0;selected<=0;decision<=0;decision_pass<=0;decision_reason<=0;
 for(k=0;k<16;k=k+1)generation[k]<=0;
 m_vl<=0;m_network<=0;m_sn<=0;m_len<=0;m_time<=0;delivered_frames<=0;rejected_frames<=0;end
 else begin
 decision<=0;
 if(!active&&|(d_found&desc_ready))begin
 active<=1;draining<=0;
 if(d_found[0]&&desc_ready[0])selected<={1'b0,d_choice[0]};else selected<={1'b1,d_choice[1]};
 end
 if(result_valid&&active&&!draining)begin
 draining<=1;keep<=result_pass;decision<=1;decision_pass<=result_pass;decision_reason<=result_reason;
 m_vl<=result_vl;m_network<=result_net;m_sn<=result_sn;m_len<=result_len;m_time<=result_time;
 if(!result_pass)rejected_frames<=rejected_frames+32'd1;
 end
 if(active&&draining&&(|done_q))begin
 generation[selected]<=generation[selected]+28'd1;
 active<=0;draining<=0;if(keep)delivered_frames<=delivered_frames+32'd1;
 end
 end end
endmodule
