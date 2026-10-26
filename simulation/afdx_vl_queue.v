`timescale 1ns/1ps
// Serialized linked descriptor queues. Payload ownership stays with caller.
// PUSH transfers one descriptor reference; POP transfers it to the response.
module afdx_vl_queue #(
 parameter VL_WIDTH=7, NODE_WIDTH=6, TAG_WIDTH=32, LEN_WIDTH=12,
 parameter MAX_PER_VL=4, MAX_LENGTH=2048
)(
 input wire clk,reset,
 input wire cmd_valid, output wire cmd_ready,
 input wire [1:0] cmd_op,
 input wire [VL_WIDTH-1:0] cmd_vl,
 input wire [TAG_WIDTH-1:0] cmd_tag,
 input wire [LEN_WIDTH-1:0] cmd_length,
 output wire rsp_valid,input wire rsp_ready,
 output reg [2:0] rsp_status,
 output reg [VL_WIDTH-1:0] rsp_vl,
 output reg [TAG_WIDTH-1:0] rsp_tag,
 output reg [LEN_WIDTH-1:0] rsp_length,
 output reg [NODE_WIDTH:0] rsp_count,
 output reg [(1<<VL_WIDTH)-1:0] pending,
 output reg [NODE_WIDTH:0] free_count,
 output wire initialized
);
 localparam VLS=(1<<VL_WIDTH),NODES=(1<<NODE_WIDTH);
 localparam TABLE_WIDTH=3*NODE_WIDTH+1;
 localparam INIT=0,IDLE=1,LOOKUP=2,SCAN=3,PUSH=4,READ_NODE=5,POP=6,RESPOND=7;
 reg [2:0] state;
 reg [VL_WIDTH-1:0] init_index,active_vl;
 reg [1:0] active_op;
 reg [TAG_WIDTH-1:0] active_tag;
 reg [LEN_WIDTH-1:0] active_length;
 reg [NODE_WIDTH-1:0] scan_index,round_robin;
 reg [NODES-1:0] available;
 reg [TABLE_WIDTH-1:0] table_memory [0:VLS-1];
 reg [TABLE_WIDTH-1:0] table_q;
 reg [TAG_WIDTH+LEN_WIDTH-1:0] descriptors [0:NODES-1];
 reg [TAG_WIDTH+LEN_WIDTH-1:0] descriptor_q;
 reg [NODE_WIDTH-1:0] links [0:NODES-1];
 reg [NODE_WIDTH-1:0] link_q;
 wire [NODE_WIDTH-1:0] head=table_q[NODE_WIDTH-1:0];
 wire [NODE_WIDTH-1:0] tail=table_q[2*NODE_WIDTH-1:NODE_WIDTH];
 wire [NODE_WIDTH:0] count=table_q[TABLE_WIDTH-1:2*NODE_WIDTH];
 wire [NODE_WIDTH:0] count_plus=count+1'b1,count_minus=count-1'b1;
 assign initialized=state!=INIT && !reset;
 assign cmd_ready=state==IDLE && !reset;
 assign rsp_valid=state==RESPOND && !reset;
 // Every data array has an explicit synchronous read; no RAM-output reset.
 always @(posedge clk) begin
  if (!reset && state==INIT) table_memory[init_index]<=0;
  if (cmd_valid && cmd_ready) table_q<=table_memory[cmd_vl];
  if (!reset && state==PUSH) begin
   table_memory[active_vl]<={count_plus,scan_index,(count==0 ? scan_index : head)};
   descriptors[scan_index]<={active_tag,active_length};
   if (count!=0) links[tail]<=scan_index;
  end
  if (!reset && state==READ_NODE) begin descriptor_q<=descriptors[head];link_q<=links[head];end
  if (!reset && state==POP && active_op==1)
   table_memory[active_vl]<={count_minus,(count==1 ? {NODE_WIDTH{1'b0}} : tail),(count==1 ? {NODE_WIDTH{1'b0}} : link_q)};
 end
 always @(posedge clk) begin
  if(reset)begin
   state<=INIT;init_index<=0;active_vl<=0;active_op<=0;active_tag<=0;active_length<=0;
   pending<=0;free_count<=NODES;available<={NODES{1'b1}};scan_index<=0;round_robin<=0;
   rsp_status<=0;rsp_vl<=0;rsp_tag<=0;rsp_length<=0;rsp_count<=0;
  end else case(state)
   INIT: if(&init_index)state<=IDLE;else init_index<=init_index+1'b1;
   IDLE: if(cmd_valid)begin
    active_vl<=cmd_vl;active_op<=cmd_op;active_tag<=cmd_tag;active_length<=cmd_length;
    rsp_vl<=cmd_vl;rsp_status<=0;rsp_tag<=0;rsp_length<=0;rsp_count<=0;state<=LOOKUP;
   end
   LOOKUP: begin
    rsp_count<=count;
    if(active_op==0)begin
     if(active_length==0 || active_length>MAX_LENGTH)begin rsp_status<=4;state<=RESPOND;end
     else if(count>=MAX_PER_VL || free_count==0)begin rsp_status<=2;state<=RESPOND;end
     else begin scan_index<=round_robin;state<=SCAN;end
    end else if(active_op==1 || active_op==2)begin
     if(count==0)begin rsp_status<=1;state<=RESPOND;end else state<=READ_NODE;
    end else begin rsp_status<=3;state<=RESPOND;end
   end
   SCAN: if(available[scan_index])state<=PUSH;else scan_index<=scan_index+1'b1;
   PUSH: begin
    available[scan_index]<=0;free_count<=free_count-1'b1;round_robin<=scan_index+1'b1;
    pending[active_vl]<=1;rsp_tag<=active_tag;rsp_length<=active_length;rsp_count<=count_plus;state<=RESPOND;
   end
   READ_NODE:state<=POP;
   POP:begin
    rsp_tag<=descriptor_q[TAG_WIDTH+LEN_WIDTH-1:LEN_WIDTH];rsp_length<=descriptor_q[LEN_WIDTH-1:0];
    if(active_op==1)begin
     available[head]<=1;free_count<=free_count+1'b1;pending[active_vl]<=count>1;rsp_count<=count_minus;
    end
    state<=RESPOND;
   end
   RESPOND:if(rsp_ready)state<=IDLE;
   default:begin state<=INIT;init_index<=0;pending<=0;available<={NODES{1'b1}};free_count<=NODES;end
  endcase
 end
endmodule
