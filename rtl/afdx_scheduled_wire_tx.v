`timescale 1ns/1ps
// Development integration: existing per-VL encoders and byte MAC, with BAG
// deadlines retired at actual SFD timestamps rather than MAC-client admission.
// One outstanding physical frame keeps the scheduler grant/VL association exact.
module afdx_scheduled_wire_tx #(
    parameter VL_COUNT=4,
    parameter INDEX_W=2,
    parameter [31:0] INITIAL_TIME_LOW=32'd0,
    parameter [31:0] INITIAL_TIME_HIGH=32'd0
)(
    input wire clk,reset,run,
    input wire cfg_valid,
    input wire [INDEX_W-1:0] cfg_index,
    input wire [2:0] cfg_bag_exp,
    output wire cfg_ready,cfg_reject,
    input wire [INDEX_W-1:0] app_vl,
    input wire [15:0] app_len,
    input wire app_msg_valid,
    output wire app_msg_ready,
    input wire [7:0] app_data,
    input wire app_valid,app_last,
    output wire app_ready,
    output wire [7:0] line_data,
    output wire line_valid,byte_tick,
    output wire [63:0] monotonic_time,
    output wire sfd_pulse,
    output wire [63:0] sfd_time,
    output wire [INDEX_W-1:0] sfd_vl,
    output wire busy,
    output wire reject
);
    reg [63:0] ticks;
    reg [2:0] divider;
    reg loading,frame_in_flight,client_complete;
    reg [INDEX_W-1:0] load_index,active_index;
    wire [VL_COUNT-1:0] msg_ready,data_ready,tx_valid,tx_sop,tx_eop,tx_reject,tx_done;
    wire [7:0] tx_data [0:VL_COUNT-1];
    wire [VL_COUNT-1:0] msg_valid,data_valid,tx_ready;
    wire grant_valid;
    wire [INDEX_W-1:0] grant_index;
    wire [INDEX_W-1:0] selected=frame_in_flight ? active_index : grant_index;
    wire client_valid=(frame_in_flight || (grant_valid && run)) && !client_complete && tx_valid[selected] && !reset;
    wire client_sop=tx_sop[selected];
    wire client_eop=tx_eop[selected];
    wire client_ready,wire_done,mac_reject;
    wire [1:0] unused_slots;
    wire [31:0] scheduler_time=sfd_pulse ? sfd_time[31:0] : ticks[31:0];
    wire retiring=sfd_pulse && frame_in_flight;
    wire scheduler_enabled=run || frame_in_flight;
    assign busy=frame_in_flight;
    assign cfg_ready=!run && !frame_in_flight && !loading && (&msg_ready) && !reset;
    assign cfg_reject=cfg_valid && !cfg_ready && !reset;
    assign app_msg_ready=run && !loading && msg_ready[app_vl] && !reset;
    assign app_ready=loading && data_ready[load_index] && !reset;
    assign monotonic_time=ticks;
    assign byte_tick=(divider==0) && !reset;
    assign sfd_vl=active_index;
    assign reject=(|tx_reject) || mac_reject;

    afdx_bag_scheduler #(.VL_COUNT(VL_COUNT),.INDEX_W(INDEX_W)) scheduler (
        .clk(clk),.reset(reset),.enable(scheduler_enabled),
        .cfg_valid(cfg_valid && cfg_ready),.cfg_index(cfg_index),.cfg_bag_exp(cfg_bag_exp),
        .time_now(scheduler_time),.pending(tx_valid),
        .mac_idle(!frame_in_flight || retiring),.grant_ready(retiring),
        .grant_valid(grant_valid),.grant_index(grant_index));
    genvar i;
    generate for (i=0;i<VL_COUNT;i=i+1) begin: vl
        localparam [47:0] VL_MAC=48'h030000000001+i;
        assign msg_valid[i]=app_msg_valid && app_msg_ready && app_vl==i;
        assign data_valid[i]=app_valid && loading && load_index==i;
        assign tx_ready[i]=client_ready && client_valid && selected==i;
        afdx_udp_tx #(.DEST_MAC(VL_MAC)) encoder (
            .clk(clk),.reset(reset),.msg_len(app_len),.msg_valid(msg_valid[i]),.msg_ready(msg_ready[i]),
            .s_data(app_data),.s_valid(data_valid[i]),.s_last(app_last),.s_ready(data_ready[i]),
            .m_data(tx_data[i]),.m_valid(tx_valid[i]),.m_ready(tx_ready[i]),.m_sop(tx_sop[i]),
            .m_eop(tx_eop[i]),.reject(tx_reject[i]),.done(tx_done[i]));
    end endgenerate
    eth_mac_tx mac (
        .clk(clk),.reset(reset),.byte_ce(byte_tick),.time_now(ticks),
        .s_data(tx_data[selected]),.s_valid(client_valid),.s_sop(client_sop),.s_eop(client_eop),.s_ready(client_ready),
        .line_data(line_data),.line_valid(line_valid),.sfd_pulse(sfd_pulse),.sfd_time(sfd_time),
        .done(wire_done),.reject(mac_reject),.slots_used(unused_slots));

    always @(posedge clk) begin
        if (reset) begin
            ticks<={INITIAL_TIME_HIGH,INITIAL_TIME_LOW};divider<=0;loading<=0;frame_in_flight<=0;client_complete<=0;
            load_index<=0;active_index<=0;
        end else begin
            ticks<=ticks+64'd1;divider<=divider+3'd1;
            if (app_msg_valid && app_msg_ready && app_len>=1 && app_len<=1471) begin
                loading<=1;load_index<=app_vl;
            end
            if (loading && ((app_valid && app_ready && app_last) || tx_reject[load_index])) loading<=0;
            if (client_valid && client_ready) begin
                if (!frame_in_flight) begin frame_in_flight<=1;active_index<=grant_index; end
                if (client_eop) client_complete<=1;
            end
            if (wire_done) begin frame_in_flight<=0;client_complete<=0;end
        end
    end
endmodule
