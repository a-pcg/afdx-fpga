import sys
p = r"C:\Users\zhang\Desktop\test\afdx\rtl\mgmt\afdx_a615a_agent.v"
s = open(p, encoding="ascii").read()
subs = []
def sub(old, new, count=1):
    subs.append((old, new, count))

# add ackwaiting state register
sub(""" reg        ul_active;
 reg [15:0] ul_seq;""",
""" reg        ul_active;
 reg        ackwaiting;
 reg [15:0] ul_seq;""")
sub("""   ul_active        <= 1'b0;
   ul_seq           <= 16'd0;""",
"""   ul_active        <= 1'b0;
   ackwaiting       <= 1'b0;
   ul_seq           <= 16'd0;""")
# S_HDR: ignore non-ACK packets while waiting for an upload ACK
sub("""     if (rx_overflow) begin
      resp_kind <= RS_ERROR; resp_err <= E_ILLEGAL; resp_seq <= 16'd0;
      pkt_release <= 1'b1;
      state <= S_BUILD;
     end else begin
      case (p_op)""",
"""     if (rx_overflow) begin
      resp_kind <= RS_ERROR; resp_err <= E_ILLEGAL; resp_seq <= 16'd0;
      pkt_release <= 1'b1;
      state <= S_BUILD;
     end else if (ackwaiting && !((p_op == OP_ACK) && (p_rxlen == 11'd3))) begin
      pkt_release <= 1'b1;              // keep waiting for the ACK
      state <= S_ACKWAIT;
     end else begin
      case (p_op)""")
# RK_ACK: clear ackwaiting on match; ignore stray in idle
sub("""       if (ul_active && (p_seq == ul_seq)) begin
        pkt_release <= 1'b1;
        if (ul_seq == ul_total) begin
         ul_active <= 1'b0;
         state     <= S_IDLE;""",
"""       if (ul_active && (p_seq == ul_seq)) begin
        pkt_release <= 1'b1;
        ackwaiting <= 1'b0;
        if (ul_seq == ul_total) begin
         ul_active <= 1'b0;
         state     <= S_IDLE;""")
sub("""       end else begin
        pkt_release <= 1'b1;             // duplicate / stale ACK: ignore
        state       <= ul_active ? S_ACKWAIT : S_IDLE;
       end""",
"""       end else begin
        pkt_release <= 1'b1;             // duplicate / stale ACK: ignore
        ackwaiting <= 1'b0;
        state       <= ul_active ? S_ACKWAIT : S_IDLE;
       end""")
# TX completion into ACKWAIT: set ackwaiting, do NOT reset retry count
sub("""        AT_ACKWAIT: begin
         timer        <= 32'd0;
         retries_used <= 4'd0;
         state        <= S_ACKWAIT;
        end""",
"""        AT_ACKWAIT: begin
         timer      <= 32'd0;
         ackwaiting <= 1'b1;
         state      <= S_ACKWAIT;
        end""")
# reset retries when a NEW upload block is built
sub("""     if (!send_started) begin
      send_started <= 1'b1;
      send_cnt     <= 11'd0;
      send_off     <= (ul_seq - 16'd1) << 9;""",
"""     if (!send_started) begin
      send_started <= 1'b1;
      send_cnt     <= 11'd0;
      retries_used <= 4'd0;
      send_off     <= (ul_seq - 16'd1) << 9;""")
# abort clears ackwaiting
sub("""        cnt_retry_aborts <= cnt_retry_aborts + 32'd1;
        ul_active    <= 1'b0;""",
"""        cnt_retry_aborts <= cnt_retry_aborts + 32'd1;
        ul_active    <= 1'b0;
        ackwaiting   <= 1'b0;""")

for old, new, count in subs:
    found = s.count(old)
    if found != count:
        print("PATCH_FAIL", repr(old[:80]), "found", found, "expected", count)
        sys.exit(1)
    s = s.replace(old, new)
open(p, "w", encoding="ascii", newline="\n").write(s)
print("PATCHED", len(subs), "replacements")
