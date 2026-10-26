`timescale 1ns/1ps
// Development integration of payload pool and VL descriptor queues.
// One serialized application transaction: enqueue, dequeue+stream, or discard.
// No UDP/MAC scheduler connection yet. Common reset cancels all ownership.
module afdx_buffered_vl #(
 parameter VL_WIDTH=7,SLOT_WIDTH=6,MAX_PER_VL=4
)(
 input wire clk,reset,
 input wire cmd_valid,output wire cmd_ready,
 input wire [1:0] cmd_op,input wire [VL_WIDTH-1:0] cmd_vl,
 input wire [11:0] cmd_length,
 input wire [7:0] s_data,input wire s_valid,s_last,output wire s_ready,
 output wire [7:0] m_data,output wire m_valid,m_sop,m_last,input wire m_ready,
 output wire [11:0] m_length,output wire [VL_WIDTH-1:0] m_vl,
 output wire rsp_valid,input wire rsp_ready,output reg [3:0] rsp_status,
 output reg [VL_WIDTH-1:0] rsp_vl,output reg [11:0] rsp_length,
 output reg [31:0] rsp_tag,
 output wire [(1<<VL_WIDTH)-1:0] pending,
 output wire [SLOT_WIDTH:0] pool_free,queue_free,
 output wire initialized
);
 localparam INIT=0,IDLE=1,ALLOC_REQ=2,ALLOC_WAIT=3,LOAD=4,PUB_REQ=5,PUB_WAIT=6,
  PUSH_REQ=7,PUSH_WAIT=8,POP_REQ=9,POP_WAIT=10,READ=11,REL_REQ=12,REL_WAIT=13,RESPOND=14;
 reg [3:0] state;
 reg [1:0] active_op;
 reg [VL_WIDTH-1:0] active_vl;
 reg [11:0] length,offset;
 reg [31:0] tag;
 // T10: the pool READ stream is pipelined. Up to two read requests are in flight so
 // the pool read port is used every cycle (it accepts one request per cycle and
 // returns one byte per cycle after its fixed latency), instead of one request per
 // three cycles. offset is the byte being consumed; issue_off is the next request.
 // read_abort drains outstanding requests before the pool is released again.
 reg [1:0] rd_inflight;
 reg [11:0] issue_off;
 reg read_abort;
 wire pool_initialized,queue_initialized;
 wire pc_ready,pr_valid;wire [31:0] pr_tag;wire [2:0] pr_status;wire [1:0] pr_refs;
 wire wr_ready,wr_error,rd_ready,data_valid,data_error;
 wire [7:0] data_byte;wire [31:0] data_tag;wire [10:0] data_offset;
 wire qc_ready,qr_valid;wire [2:0] qr_status;wire [VL_WIDTH-1:0] qr_vl;
 wire [31:0] qr_tag;wire [11:0] qr_length;wire [SLOT_WIDTH:0] qr_count;
 wire pc_valid=(state==ALLOC_REQ || state==PUB_REQ || state==REL_REQ) && !reset;
 wire [1:0] pc_op=state==ALLOC_REQ ? 2'd0 : state==PUB_REQ ? 2'd1 : 2'd2;
 wire pr_ready=state==ALLOC_WAIT || state==PUB_WAIT || state==REL_WAIT;
 wire qc_valid=(state==PUSH_REQ || state==POP_REQ) && !reset;
 wire qr_ready=state==PUSH_WAIT || state==POP_WAIT;
 wire rd_valid=state==READ && !read_abort && (rd_inflight<2'd2) && (issue_off<length) && !reset;
 wire data_ready=state==READ && (rd_inflight!=2'd0) && (data_error || m_ready || read_abort) && !reset;
 assign initialized=pool_initialized && queue_initialized && !reset;
 assign cmd_ready=state==IDLE && initialized;
 assign s_ready=state==LOAD && wr_ready && !reset;
 assign m_data=data_byte;
 assign m_length=length;
 assign m_vl=active_vl;
 assign m_valid=state==READ && (rd_inflight!=2'd0) && data_valid && !data_error && !read_abort && !reset;
 assign m_sop=m_valid && offset==0;
 assign m_last=m_valid && offset+12'd1==length;
 assign rsp_valid=state==RESPOND && !reset;
 afdx_packet_pool #(.SLOT_WIDTH(SLOT_WIDTH)) pool (
  .clk(clk),.reset(reset),.cmd_valid(pc_valid),.cmd_ready(pc_ready),.cmd_op(pc_op),.cmd_tag(tag),.cmd_consumers(2'd1),
  .rsp_valid(pr_valid),.rsp_ready(pr_ready),.rsp_tag(pr_tag),.rsp_status(pr_status),.rsp_refs(pr_refs),
  .wr_valid(s_valid && state==LOAD && !reset),.wr_ready(wr_ready),.wr_tag(tag),.wr_offset(offset[10:0]),.wr_data(s_data),.wr_error(wr_error),
  .rd_valid(rd_valid),.rd_ready(rd_ready),.rd_tag(tag),.rd_offset(issue_off[10:0]),
  .data_valid(data_valid),.data_ready(data_ready),.data_byte(data_byte),.data_error(data_error),.data_tag(data_tag),.data_offset(data_offset),
  .free_count(pool_free),.initialized(pool_initialized));
 afdx_vl_queue #(.VL_WIDTH(VL_WIDTH),.NODE_WIDTH(SLOT_WIDTH),.MAX_PER_VL(MAX_PER_VL)) queues (
  .clk(clk),.reset(reset),.cmd_valid(qc_valid),.cmd_ready(qc_ready),.cmd_op(state==PUSH_REQ ? 2'd0 : 2'd1),
  .cmd_vl(active_vl),.cmd_tag(tag),.cmd_length(length),.rsp_valid(qr_valid),.rsp_ready(qr_ready),
  .rsp_status(qr_status),.rsp_vl(qr_vl),.rsp_tag(qr_tag),.rsp_length(qr_length),.rsp_count(qr_count),
  .pending(pending),.free_count(queue_free),.initialized(queue_initialized));
 always @(posedge clk) begin
  if(reset)begin
   state<=INIT;active_op<=0;active_vl<=0;length<=0;offset<=0;tag<=0;rd_inflight<=0;issue_off<=0;read_abort<=0;
   rsp_status<=0;rsp_vl<=0;rsp_length<=0;rsp_tag<=0;
  end else case(state)
   INIT:if(initialized)state<=IDLE;
   IDLE:if(cmd_valid && cmd_ready)begin
    active_op<=cmd_op;active_vl<=cmd_vl;length<=cmd_length;offset<=0;tag<=0;rd_inflight<=0;issue_off<=0;read_abort<=0;
    rsp_status<=0;rsp_vl<=cmd_vl;rsp_length<=0;rsp_tag<=0;
    if(cmd_op==0)begin
     if(cmd_length==0 || cmd_length>2048)begin rsp_status<=4;state<=RESPOND;end
     else state<=ALLOC_REQ;
    end else if(cmd_op==1 || cmd_op==2)state<=POP_REQ;
    else begin rsp_status<=3;state<=RESPOND;end
   end
   ALLOC_REQ:if(pc_ready)state<=ALLOC_WAIT;
   ALLOC_WAIT:if(pr_valid)begin
    if(pr_status==0)begin tag<=pr_tag;state<=LOAD;end
    else begin rsp_status<=pr_status==2 ? 4'd2 : 4'd6;state<=RESPOND;end
   end
   LOAD:if(s_valid && s_ready)begin
    if(wr_error)begin rsp_status<=6;state<=REL_REQ;end
    else if(s_last || offset+12'd1==length)begin
     if(s_last && offset+12'd1==length)state<=PUB_REQ;
     else begin rsp_status<=5;state<=REL_REQ;end
    end else offset<=offset+12'd1;
   end
   PUB_REQ:if(pc_ready)state<=PUB_WAIT;
   PUB_WAIT:if(pr_valid)begin
    if(pr_status==0)state<=PUSH_REQ;else begin rsp_status<=6;state<=REL_REQ;end
   end
   PUSH_REQ:if(qc_ready)state<=PUSH_WAIT;
   PUSH_WAIT:if(qr_valid)begin
    if(qr_status==0)begin rsp_tag<=tag;rsp_length<=length;state<=RESPOND;end
    else begin rsp_status<=qr_status==2 ? 4'd2 : 4'd6;state<=REL_REQ;end
   end
   POP_REQ:if(qc_ready)state<=POP_WAIT;
   POP_WAIT:if(qr_valid)begin
    if(qr_status!=0)begin rsp_status<=qr_status==1 ? 4'd1 : 4'd6;state<=RESPOND;end
    else begin
     tag<=qr_tag;length<=qr_length;rsp_tag<=qr_tag;rsp_length<=qr_length;offset<=0;rd_inflight<=0;issue_off<=0;read_abort<=0;
     state<=active_op==2 ? REL_REQ : READ;
    end
   end
   READ:begin
    if(rd_valid && rd_ready)issue_off<=issue_off+12'd1;
    if(data_valid && data_ready)begin
     if(read_abort)begin
      if(rd_inflight==2'd1)state<=REL_REQ;
     end else if(data_error || data_tag!=tag || data_offset!=offset[10:0])begin
      rsp_status<=6;read_abort<=1;
      if(rd_inflight==2'd1)state<=REL_REQ;
     end else if(offset+12'd1==length)state<=REL_REQ;
     else offset<=offset+12'd1;
    end
    rd_inflight<=rd_inflight+(rd_valid&&rd_ready?2'd1:2'd0)-(data_valid&&data_ready?2'd1:2'd0);
   end
   REL_REQ:if(pc_ready)state<=REL_WAIT;
   REL_WAIT:if(pr_valid)begin if(pr_status!=0)rsp_status<=6;state<=RESPOND;end
   RESPOND:if(rsp_ready)state<=IDLE;
   default:state<=INIT;
  endcase
 end
endmodule
