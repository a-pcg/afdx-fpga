`timescale 1ns/1ps
// M2 four-VL integration. Dedicated per-VL payload buffers are an initial
// implementation; final 128-VL core must use a shared packet pool.
module afdx_scheduled_tx # (parameter VL_COUNT=4,INDEX_W=2)(
 input wire clk,reset,run,
 input wire cfg_valid,input wire [INDEX_W-1:0] cfg_index,input wire [2:0] cfg_bag_exp,
 input wire [INDEX_W-1:0] app_vl,input wire [15:0] app_len,
 input wire app_msg_valid,output wire app_msg_ready,
 input wire [7:0] app_data,input wire app_valid,app_last,output wire app_ready,
 output wire [7:0] mac_data,output wire mac_valid,mac_sop,mac_eop,input wire mac_ready,
 output wire reject,output wire [31:0] monotonic_time);
 wire [VL_COUNT-1:0] msg_ready,data_ready,tx_valid,tx_sop,tx_eop,tx_reject,tx_done;
 wire [7:0] tx_data[0:VL_COUNT-1];
 wire [VL_COUNT-1:0] msg_valid,data_valid,tx_ready;
 reg loading,active;
 reg [INDEX_W-1:0] load_index,active_index;
 reg [31:0] ticks;
 wire grant_valid;wire [INDEX_W-1:0] grant_index;
 wire [INDEX_W-1:0] select_index=active?active_index:grant_index;
 assign monotonic_time=ticks;
 assign app_msg_ready=run&&!loading&&msg_ready[app_vl]&&!reset;
 assign app_ready=loading&&data_ready[load_index]&&!reset;
 assign mac_valid=(active||grant_valid)&&tx_valid[select_index]&&!reset;
 assign mac_data=tx_data[select_index];assign mac_sop=tx_sop[select_index];assign mac_eop=tx_eop[select_index];
 assign reject=|tx_reject;
 afdx_bag_scheduler #(.VL_COUNT(VL_COUNT),.INDEX_W(INDEX_W)) scheduler(
 .clk(clk),.reset(reset),.enable(run),.cfg_valid(cfg_valid),.cfg_index(cfg_index),.cfg_bag_exp(cfg_bag_exp),
 .time_now(ticks),.pending(tx_valid),.mac_idle(!active),.grant_ready(mac_ready&&mac_valid),
 .grant_valid(grant_valid),.grant_index(grant_index));
 genvar i;generate for(i=0;i<VL_COUNT;i=i+1)begin:vl
 localparam [47:0] VL_MAC=48'h030000000001+i;
 assign msg_valid[i]=app_msg_valid&&app_msg_ready&&(app_vl==i);
 assign data_valid[i]=app_valid&&loading&&(load_index==i);
 assign tx_ready[i]=mac_ready&&(active||grant_valid)&&(select_index==i);
 afdx_udp_tx #(.DEST_MAC(VL_MAC)) encoder(
 .clk(clk),.reset(reset),.msg_len(app_len),.msg_valid(msg_valid[i]),.msg_ready(msg_ready[i]),
 .s_data(app_data),.s_valid(data_valid[i]),.s_last(app_last),.s_ready(data_ready[i]),
 .m_data(tx_data[i]),.m_valid(tx_valid[i]),.m_ready(tx_ready[i]),.m_sop(tx_sop[i]),.m_eop(tx_eop[i]),
 .reject(tx_reject[i]),.done(tx_done[i]));
 end endgenerate
 always @(posedge clk)begin
 if(reset)begin ticks<=0;loading<=0;load_index<=0;active<=0;active_index<=0;end
 else begin
 ticks<=ticks+32'd1;
 if(app_msg_valid&&app_msg_ready&&app_len>=16'd1&&app_len<=16'd1471)begin loading<=1;load_index<=app_vl;end
 if(loading&&((app_valid&&app_ready&&app_last)||tx_reject[load_index]))loading<=0;
 if(mac_valid&&mac_ready)begin
 if(mac_eop)active<=0;
 else if(!active)begin active<=1;active_index<=grant_index;end
 end
 end end
endmodule
