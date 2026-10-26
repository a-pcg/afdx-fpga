`timescale 1ns/1ps
// Development TX: 128 VL queues, shared pool, one UDP staging encoder and MAC.
// run starts physical frames; app_enable separately controls new submissions.
// T4: BAG deadlines use the TX-private 64-bit afdx_bag_scheduler64 (full 64-bit SFD time),
// so long idle gaps no longer hit the public 32-bit elapsed[31] expiry test (S06). The public
// module remains unchanged for the old baseline; the external port list here is unchanged.
module afdx_shared_tx #(
 parameter VL_WIDTH=7,SLOT_WIDTH=6,MAX_PER_VL=4,
 // P1 time base. The defaults reproduce the 100 MHz / 8-cycle byte rhythm exactly;
 // the 50 MHz candidate elaborates with CORE_CLK_HZ=50000000 and BYTE_CYCLES=4 so a
 // byte still takes 80 ns on the line and every millisecond window keeps its physical
 // meaning. Only 100e6/8 and 50e6/4 are legal (scripts/system_integration/run_config.py).
 parameter [31:0] CORE_CLK_HZ=32'd100000000,
 parameter [31:0] BYTE_CYCLES=32'd8,
 parameter [63:0] CYCLES_PER_MS={32'd0,CORE_CLK_HZ}/64'd1000,
 parameter [31:0] INITIAL_TIME_LOW=32'd0,INITIAL_TIME_HIGH=32'd0
)(
 input wire clk,reset,run,app_enable,
 input wire cfg_valid,input wire [VL_WIDTH-1:0] cfg_vl,input wire [2:0] cfg_bag_exp,
 output wire cfg_ready,cfg_reject,
 input wire app_msg_valid,output wire app_msg_ready,input wire [VL_WIDTH-1:0] app_vl,input wire [11:0] app_length,
 input wire [7:0] app_data,input wire app_valid,app_last,output wire app_ready,
 output wire app_rsp_valid,input wire app_rsp_ready,output wire [3:0] app_rsp_status,
 input wire drop_valid,output wire drop_ready,input wire [VL_WIDTH-1:0] drop_vl,
 output wire drop_rsp_valid,input wire drop_rsp_ready,output wire [3:0] drop_rsp_status,
 output wire [7:0] line_data,output wire line_valid,byte_tick,
 output wire [63:0] monotonic_time,output wire sfd_pulse,output wire [63:0] sfd_time,
 output wire [VL_WIDTH-1:0] sfd_vl,
 output wire [(1<<VL_WIDTH)-1:0] pending,output wire [SLOT_WIDTH:0] pool_free,
 output wire initialized,busy,output reg fault
);
 localparam VLS=(1<<VL_WIDTH);
 localparam NONE=3'd0,WRITE=3'd1,READ=3'd2,DROP=3'd3,LOCAL_ERROR=3'd4;
 reg [2:0] owner;
 reg [VLS-1:0] configured;
 reg [VLS-1:0] has_sfd;
 reg [63:0] last_sfd [0:VLS-1];
 reg [2:0] configured_bag [0:VLS-1];
 reg [3:0] local_status;
 reg [63:0] ticks;
 reg [2:0] divider;
 // T10/T11: flight is an in-flight frame counter 0..4 (four MAC store-and-forward
 // slots). The BAG scheduler keeps up to four grants outstanding, so several frames
 // can be popped and loaded ahead while earlier frames are on the wire; four slots
 // absorb a burst of short frames before a long frame has to be loaded.
 // sfdq is the pop-order FIFO of {vl,sn} for frames that are in flight but have not
 // had their real SFD yet: its head is the frame currently on the wire, which is
 // what sfd_vl/SN advance/last_sfd and the scheduler's BAG anchor use.
 reg [2:0] flight_cnt;
 reg encoder_admitted,sn_initialized;
 reg [VL_WIDTH-1:0] sfdq_vl [0:3];
 reg [7:0] sfdq_sn [0:3];
 reg [1:0] sfdq_wr,sfdq_rd;
 reg [2:0] sfdq_count;
 // prefetch_ok keeps the run-pause contract: the first frame of a burst is popped
 // (flight_cnt==0), but further pop-ahead only starts after that frame's real SFD,
 // so a run pause applied at a frame's first byte still shows no new frame start.
 // Once any frame of the burst has reached its SFD the TX loads up to four ahead.
 reg prefetch_ok;
 wire [VL_WIDTH-1:0] sfd_head_vl=sfdq_vl[sfdq_rd];
 wire [7:0] sfd_head_sn=sfdq_sn[sfdq_rd];
 reg [VL_WIDTH-1:0] active_vl,init_vl;
 reg [7:0] next_sn [0:VLS-1];
 reg [7:0] sn_q;
 reg [VL_WIDTH-1:0] guard_vl_q0,guard_vl_q1,guard_vl_q2;
 reg guard_nosfd_q0,guard_nosfd_q1,guard_ok_q2;
 reg [63:0] guard_last_sfd_q0,guard_elapsed_q1,guard_interval_q1;
 reg [2:0] guard_bag_exp_q0,guard_cfg_bag_q0;
 wire b_initialized,b_cmd_ready,b_s_ready,b_m_valid,b_m_sop,b_m_last,b_m_ready,b_rsp_valid;
 wire [7:0] b_m_data;wire [11:0] b_m_length,b_rsp_length;wire [VL_WIDTH-1:0] b_m_vl,b_rsp_vl;
 wire [31:0] b_rsp_tag;wire [3:0] b_rsp_status;wire [SLOT_WIDTH:0] queue_free;
 wire grant_valid;wire [VL_WIDTH-1:0] grant_vl;
 wire need_pop;
 wire choose_drop=!need_pop && drop_valid;
 wire app_length_ok=app_length>=1 && app_length<=1471;
 wire app_configured=configured[app_vl];
 wire b_cmd_valid=owner==NONE && initialized && !fault &&
  (need_pop || choose_drop || (app_enable && app_msg_valid && app_length_ok && app_configured));
 wire [1:0] b_cmd_op=need_pop ? 2'd1 : choose_drop ? 2'd2 : 2'd0;
 wire [VL_WIDTH-1:0] b_cmd_vl=need_pop ? grant_vl : choose_drop ? drop_vl : app_vl;
 wire pop_accept=b_cmd_valid && b_cmd_ready && need_pop;
 wire b_rsp_ready=owner==READ ? 1'b1 : owner==WRITE ? app_rsp_ready : owner==DROP ? drop_rsp_ready : 1'b0;
 wire encoder_msg_ready,encoder_s_ready,encoder_valid,encoder_sop,encoder_eop,encoder_reject,encoder_done,mac_ready,mac_reject,wire_done;
 wire [7:0] encoder_data;wire [2:0] mac_slots;
 wire encoder_msg_valid=owner==READ && b_m_valid && b_m_sop && !encoder_admitted && !fault;
 wire [47:0] destination_mac=48'h030000000000+{{(48-VL_WIDTH){1'b0}},active_vl}+48'd1;
 wire retiring=sfd_pulse && (flight_cnt!=3'd0) && (sfdq_count!=0);
 wire [63:0] scheduler_time=sfd_pulse ? sfd_time : ticks;
 // The MAC can take another frame while up to three transmit (slots_used<4), the
 // encoder is idle after a LOAD, and the scheduler offers an unused grant. Several
 // grants can be outstanding, so frames n+1..n+3 are read/loaded while n transmits.
 // A grant whose VL queue was emptied by a drop command is cancelled instead of
 // being popped (which would make the pool READ report EMPTY).
 wire mac_free=(mac_slots!=3'd4);
 wire [2:0] flight_after=flight_cnt+(pop_accept?3'd1:3'd0)-(wire_done?3'd1:3'd0);
 wire grant_stale=grant_valid && !pending[grant_vl];
 assign need_pop=run && grant_valid && !grant_stale && initialized && !fault &&
  (flight_cnt<3'd4) && mac_free && encoder_msg_ready &&
  ((flight_cnt==3'd0)||prefetch_ok);
 assign initialized=b_initialized && sn_initialized && !reset;
 assign app_msg_ready=owner==NONE && initialized && app_enable && !need_pop && !drop_valid && b_cmd_ready && !fault;
 assign app_ready=owner==WRITE && b_s_ready && !fault && !reset;
 assign app_rsp_valid=(owner==WRITE && b_rsp_valid || owner==LOCAL_ERROR) && !reset;
 assign app_rsp_status=owner==LOCAL_ERROR ? local_status : b_rsp_status;
 assign drop_ready=owner==NONE && initialized && !need_pop && b_cmd_ready && !fault;
 assign drop_rsp_valid=owner==DROP && b_rsp_valid && !reset;
 assign drop_rsp_status=b_rsp_status;
 assign b_m_ready=owner==READ && encoder_admitted && encoder_s_ready && !fault;
 // Preserve real SFD history across stop/start and scheduler reconfiguration.
 // T5: three-stage guard pipeline. Stage0 snapshots the cfg_vl-indexed history
 // (128:1 reads of last_sfd/configured_bag/has_sfd) plus cfg_bag_exp; stage1
 // does the 64-bit subtract and the max(BAG) variable shift; stage2 does the
 // 64-bit compare. The cfg_vl tag is pipelined with the data, so a result
 // captured for a different VL is discarded. cfg_ready therefore has a bounded
 // three-cycle latency; the S05 contract (valid may be held and accepted later,
 // cfg_reject is a per-cycle not-accepted indication) is unchanged.
 // stage0: register the cfg_vl-indexed history reads
 always @(posedge clk) begin
  if(reset)begin
   guard_vl_q0<=0;guard_nosfd_q0<=1;guard_last_sfd_q0<=64'd0;guard_bag_exp_q0<=3'd0;guard_cfg_bag_q0<=3'd0;
  end else begin
   guard_vl_q0<=cfg_vl;
   guard_nosfd_q0<=!has_sfd[cfg_vl];
   guard_last_sfd_q0<=last_sfd[cfg_vl];
   guard_bag_exp_q0<=cfg_bag_exp;
   guard_cfg_bag_q0<=configured_bag[cfg_vl];
  end
 end
 // stage1: 64-bit elapsed subtract and max(old BAG, new BAG) variable shift
 always @(posedge clk) begin
  if(reset)begin
   guard_elapsed_q1<=64'd0;guard_interval_q1<=64'd0;guard_nosfd_q1<=1;guard_vl_q1<=0;
  end else begin
   guard_elapsed_q1<=ticks-guard_last_sfd_q0;
   guard_interval_q1<=CYCLES_PER_MS<<(guard_bag_exp_q0>guard_cfg_bag_q0?guard_bag_exp_q0:guard_cfg_bag_q0);
   guard_nosfd_q1<=guard_nosfd_q0;
   guard_vl_q1<=guard_vl_q0;
  end
 end
 // stage2: 64-bit compare and tag alignment
 always @(posedge clk) begin
  if(reset)begin guard_ok_q2<=1;guard_vl_q2<=0;end
  else begin
   guard_ok_q2<=guard_nosfd_q1||(guard_elapsed_q1>=guard_interval_q1);
   guard_vl_q2<=guard_vl_q1;
  end
 end
 wire guard_ready=(guard_vl_q2==cfg_vl)&&guard_ok_q2&&!reset;
 assign cfg_ready=initialized && !run && !app_enable && owner==NONE && flight_cnt==3'd0 && pending==0 && encoder_msg_ready && !fault && guard_ready;
 assign cfg_reject=cfg_valid && !cfg_ready && !reset;
 assign monotonic_time=ticks;
 assign byte_tick=divider==0 && !reset;
 assign sfd_vl=(sfdq_count!=0)?sfd_head_vl:{VL_WIDTH{1'b0}};
 assign busy=!initialized || owner!=NONE || flight_cnt!=3'd0 || pending!=0 || !encoder_msg_ready;
 afdx_buffered_vl #(.VL_WIDTH(VL_WIDTH),.SLOT_WIDTH(SLOT_WIDTH),.MAX_PER_VL(MAX_PER_VL)) buffers (
  .clk(clk),.reset(reset),.cmd_valid(b_cmd_valid),.cmd_ready(b_cmd_ready),.cmd_op(b_cmd_op),.cmd_vl(b_cmd_vl),.cmd_length(app_length),
  .s_data(app_data),.s_valid(app_valid && owner==WRITE && !fault),.s_last(app_last),.s_ready(b_s_ready),
  .m_data(b_m_data),.m_valid(b_m_valid),.m_sop(b_m_sop),.m_last(b_m_last),.m_ready(b_m_ready),.m_length(b_m_length),.m_vl(b_m_vl),
  .rsp_valid(b_rsp_valid),.rsp_ready(b_rsp_ready),.rsp_status(b_rsp_status),.rsp_vl(b_rsp_vl),.rsp_length(b_rsp_length),.rsp_tag(b_rsp_tag),
  .pending(pending),.pool_free(pool_free),.queue_free(queue_free),.initialized(b_initialized));
 afdx_bag_scheduler64 #(.VL_COUNT(VLS),.INDEX_W(VL_WIDTH),.MAX_INFLIGHT(4),
  .CYCLES_PER_MS(CYCLES_PER_MS)) scheduler (
  .clk(clk),.reset(reset),.enable((run || flight_cnt!=3'd0) && initialized && !fault),
  .cfg_valid(cfg_valid && cfg_ready),.cfg_index(cfg_vl),.cfg_bag_exp(cfg_bag_exp),.time_now(scheduler_time),.pending(pending),
  .space_ok(flight_cnt<3'd4),.grant_consumed(pop_accept),.grant_cancel(grant_stale),
  .retire_valid(retiring),.retire_index(sfd_head_vl),.grant_valid(grant_valid),.grant_index(grant_vl));
 afdx_udp_tx_shared encoder (
  .clk(clk),.reset(reset),.msg_len({4'd0,b_m_length}),.msg_dest_mac(destination_mac),.msg_sn(sn_q),
  .msg_valid(encoder_msg_valid),.msg_ready(encoder_msg_ready),
  .s_data(b_m_data),.s_valid(b_m_valid && owner==READ && encoder_admitted && !fault),.s_last(b_m_last),.s_ready(encoder_s_ready),
  .m_data(encoder_data),.m_valid(encoder_valid),.m_ready(mac_ready),.m_sop(encoder_sop),.m_eop(encoder_eop),.reject(encoder_reject),.done(encoder_done));
 eth_mac_tx mac (
  .clk(clk),.reset(reset),.byte_ce(byte_tick),.time_now(ticks),
  .s_data(encoder_data),.s_valid(encoder_valid && !fault),.s_sop(encoder_sop),.s_eop(encoder_eop),.s_ready(mac_ready),
  .line_data(line_data),.line_valid(line_valid),.sfd_pulse(sfd_pulse),.sfd_time(sfd_time),.done(wire_done),.reject(mac_reject),.slots_used(mac_slots));
 // Synchronous per-VL sequence table; advance only after actual SFD.
 always @(posedge clk) begin
  if(!reset && !sn_initialized)next_sn[init_vl]<=0;
  else if(!reset && retiring)next_sn[sfd_head_vl]<=sfd_head_sn==8'd255 ? 8'd1 : sfd_head_sn+8'd1;
  if(pop_accept)sn_q<=next_sn[grant_vl];
 end
 always @(posedge clk) begin
  if(reset)begin
   owner<=NONE;configured<=0;has_sfd<=0;local_status<=0;ticks<={INITIAL_TIME_HIGH,INITIAL_TIME_LOW};divider<=0;flight_cnt<=0;encoder_admitted<=0;
   active_vl<=0;init_vl<=0;sn_initialized<=0;fault<=0;sfdq_wr<=0;sfdq_rd<=0;sfdq_count<=0;prefetch_ok<=0;
  end else begin
   if(cfg_valid && cfg_ready)begin configured[cfg_vl]<=1;configured_bag[cfg_vl]<=cfg_bag_exp;end
   if(retiring)begin last_sfd[sfd_head_vl]<=sfd_time;has_sfd[sfd_head_vl]<=1;end
   ticks<=ticks+64'd1;
   // byte_ce divider: one byte every BYTE_CYCLES core cycles. For 8 the 3-bit divider
   // wraps 7->0 (BYTE_CYCLES[2:0]==0); for 4 it wraps 3->0.
   divider<=(divider+3'd1==BYTE_CYCLES[2:0])?3'd0:divider+3'd1;
   if(!sn_initialized)begin if(&init_vl)sn_initialized<=1;else init_vl<=init_vl+1'b1;end
   if(encoder_reject || mac_reject)fault<=1;
   if(pop_accept)begin
    owner<=READ;active_vl<=grant_vl;encoder_admitted<=0;
    sfdq_vl[sfdq_wr]<=grant_vl;sfdq_sn[sfdq_wr]<=next_sn[grant_vl];sfdq_wr<=sfdq_wr+2'd1;
   end
   if(retiring)sfdq_rd<=sfdq_rd+2'd1;
   sfdq_count<=sfdq_count+(pop_accept?3'd1:3'd0)-(retiring?3'd1:3'd0);
   flight_cnt<=flight_after;
   if(retiring)prefetch_ok<=1;
   if(flight_after==3'd0)prefetch_ok<=0;
   if(!pop_accept)begin
    if(owner==NONE && drop_valid && drop_ready)owner<=DROP;
    else if(owner==NONE && app_msg_valid && app_msg_ready)begin
     if(!app_length_ok)begin owner<=LOCAL_ERROR;local_status<=4'd4;end
     else if(!app_configured)begin owner<=LOCAL_ERROR;local_status<=4'd7;end
     else owner<=WRITE;
    end
   end
   if(encoder_msg_valid && encoder_msg_ready)encoder_admitted<=1;
   if(b_rsp_valid && b_rsp_ready)begin
    if(owner==READ && b_rsp_status!=0)fault<=1;
    owner<=NONE;
   end
   if(owner==LOCAL_ERROR && app_rsp_ready)owner<=NONE;
  end
 end
endmodule

