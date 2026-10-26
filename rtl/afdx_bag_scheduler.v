`timescale 1ns/1ps
// M2 isolated round-robin BAG regulator. Grant handshake MUST denote actual SFD.
// This conservative policy guarantees spacing, not absolute periodic jitter.
module afdx_bag_scheduler #(parameter VL_COUNT=4, INDEX_W=2)(
 input wire clk,reset,enable,
 input wire cfg_valid,input wire [INDEX_W-1:0] cfg_index,input wire [2:0] cfg_bag_exp,
 input wire [31:0] time_now,input wire [VL_COUNT-1:0] pending,
 input wire mac_idle,grant_ready,output wire grant_valid,output reg [INDEX_W-1:0] grant_index);
 localparam [INDEX_W-1:0] LAST_INDEX=VL_COUNT[INDEX_W-1:0]-1'b1;
 reg [VL_COUNT-1:0] configured,has_deadline;
 reg [2:0] bag_exp[0:VL_COUNT-1];
 reg [31:0] next_time[0:VL_COUNT-1];
 reg [INDEX_W-1:0] scan_index;
 reg held;
 wire [31:0] elapsed=time_now-next_time[scan_index];
 assign grant_valid=held&&enable&&!reset;
 always @(posedge clk)begin
 if(reset)begin configured<=0;has_deadline<=0;scan_index<=0;held<=0;grant_index<=0;end
 else if(!enable)begin
 held<=0;
 if(cfg_valid && {1'b0,cfg_index}<{1'b0,LAST_INDEX}+1'b1)begin
 configured[cfg_index]<=1;has_deadline[cfg_index]<=0;bag_exp[cfg_index]<=cfg_bag_exp;
 end
 end else begin
 if(held)begin
 if(grant_ready&&mac_idle)begin
 next_time[grant_index]<=time_now+(32'd100000<<bag_exp[grant_index]);has_deadline[grant_index]<=1;
 scan_index<=(grant_index==LAST_INDEX)?{INDEX_W{1'b0}}:grant_index+1'b1;held<=0;
 end
 end else if(mac_idle)begin
 if(configured[scan_index]&&pending[scan_index]&&(!has_deadline[scan_index]||!elapsed[31]))begin
 held<=1;grant_index<=scan_index;
 end else scan_index<=(scan_index==LAST_INDEX)?{INDEX_W{1'b0}}:scan_index+1'b1;
 end
 end
 end
endmodule
