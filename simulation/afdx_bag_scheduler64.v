`timescale 1ns/1ps
// TX-private 64-bit BAG deadline scheduler (T4 / S06 fix), extended in T11 to keep
// several grants in flight so the TX can pre-load frames into the four MAC
// store-and-forward slots.
//
// Protocol (T11):
//   - grant_valid/grant_index present one candidate VL at a time. grant_consumed pops
//     it (the wrapper took one frame of that VL); grant_cancel drops a candidate whose
//     VL is no longer pending (its queue was emptied by a drop command).
//   - retire_valid/retire_index report the real SFD of the oldest in-flight frame (the
//     wrapper keeps the pop-order FIFO). That is the only place a BAG deadline is
//     anchored, so BAG semantics are unchanged: next_time uses the SFD time of the
//     frame that was actually sent.
//   - space_ok says the wrapper can accept another in-flight frame (free MAC slot).
//     At most MAX_INFLIGHT grants are outstanding.
//   - A VL is never granted twice before its in-flight frame retires (outstanding
//     mask), so per-VL ordering and BAG spacing are preserved.
// The public rtl/afdx_bag_scheduler.v is NOT instantiated or modified here; its
// 32-bit signed elapsed[31] expiry test is still valid only for service gaps
// < 2^31 cycles and is kept for the old baseline. This module keeps time_now, every
// per-VL deadline and all deadline arithmetic 64 bit.
//
// Expiry test (unsigned 64-bit modular difference):
//   due = !has_deadline || (time_now - next_time) < 2^63
// Comparable window: the modular difference equals the true elapsed cycles as long as
// both timestamps come from the same monotonic mod-2^64 reset epoch and the true
// elapsed since the deadline is < 2^63 cycles. The largest legal BAG is
// CYCLES_PER_MS<<7 = 12,800,000 cycles at 100 MHz core (128 ms) or 6,400,000 at
// 50 MHz core (still 128 ms), so any realistic idle after the
// last real SFD is many orders of magnitude below the 2^63-cycle window (2^63 cycles
// is about 2924 years at 100 MHz). A single service gap of >= 2^63 cycles would wrap
// into "not due"; that is outside the observable window and must not be relied on.
// No 32-bit truncation of time remains on this path.
module afdx_bag_scheduler64 #(parameter VL_COUNT=4, INDEX_W=2, parameter MAX_INFLIGHT=4,
 // P1: one millisecond in core cycles (100000 at 100 MHz, 50000 at 50 MHz). BAG
 // deadlines are always CYCLES_PER_MS<<bag_exp so the physical BAG is unchanged.
 parameter [63:0] CYCLES_PER_MS=64'd100000)(
 input wire clk,reset,enable,
 input wire cfg_valid,input wire [INDEX_W-1:0] cfg_index,input wire [2:0] cfg_bag_exp,
 input wire [63:0] time_now,input wire [VL_COUNT-1:0] pending,
 input wire space_ok,
 input wire grant_consumed,grant_cancel,
 input wire retire_valid,input wire [INDEX_W-1:0] retire_index,
 output wire grant_valid,output reg [INDEX_W-1:0] grant_index);
 localparam [INDEX_W-1:0] LAST_INDEX=VL_COUNT[INDEX_W-1:0]-1'b1;
 localparam [63:0] COMPARE_WINDOW=64'h8000000000000000;
 reg [VL_COUNT-1:0] configured,has_deadline,outstanding;
 reg [2:0] bag_exp[0:VL_COUNT-1];
 reg [63:0] next_time[0:VL_COUNT-1];
 reg [INDEX_W-1:0] scan_index;
 reg [2:0] inflight;
 reg cand_valid;
 wire [63:0] elapsed=time_now-next_time[scan_index];
 wire due_window=elapsed<COMPARE_WINDOW;
 wire due=!has_deadline[scan_index]||due_window;
 wire consume_now=grant_consumed&&cand_valid;
 wire cancel_now=grant_cancel&&cand_valid;
 wire [2:0] inflight_next=inflight+(consume_now?3'd1:3'd0)-(retire_valid?3'd1:3'd0);
 wire cand_free=!cand_valid||consume_now||cancel_now;
 wire scan_now=cand_free&&(inflight_next<MAX_INFLIGHT[2:0])&&space_ok;
 assign grant_valid=cand_valid&&enable&&!reset;
 always @(posedge clk)begin
  if(reset)begin
   configured<=0;has_deadline<=0;outstanding<=0;scan_index<=0;inflight<=0;cand_valid<=0;grant_index<=0;
  end else if(!enable)begin
   cand_valid<=0;outstanding<=0;inflight<=0;
   if(cfg_valid&&{1'b0,cfg_index}<{1'b0,LAST_INDEX}+1'b1)begin
    configured[cfg_index]<=1;has_deadline[cfg_index]<=0;bag_exp[cfg_index]<=cfg_bag_exp;
   end
  end else begin
   if(retire_valid)begin
    next_time[retire_index]<=time_now+(CYCLES_PER_MS<<bag_exp[retire_index]);
    has_deadline[retire_index]<=1;
    outstanding[retire_index]<=0;
   end
   if(consume_now)begin
    outstanding[grant_index]<=1;
    cand_valid<=0;
   end else if(cancel_now)begin
    cand_valid<=0;
   end
   if(scan_now)begin
    if(configured[scan_index]&&pending[scan_index]&&!outstanding[scan_index]&&due)begin
     cand_valid<=1;grant_index<=scan_index;
     scan_index<=(scan_index==LAST_INDEX)?{INDEX_W{1'b0}}:scan_index+1'b1;
    end else scan_index<=(scan_index==LAST_INDEX)?{INDEX_W{1'b0}}:scan_index+1'b1;
   end
   inflight<=inflight_next;
  end
 end
endmodule
