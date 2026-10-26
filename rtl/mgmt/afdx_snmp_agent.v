`timescale 1ns/1ps
// afdx_snmp_agent -- standalone SNMPv1 GET/GETNEXT minimal subset.
//
// Scope (implemented): BER decode of version/community/PDU/request-id/one
// varbind OID; OID table of 10 entries; response PDU 0xA2 with error-status
// 0/noSuchName(2); counter32 / timeticks / integer / octet-string / NULL
// encoding; GETNEXT returns the next OID; unknown OID -> noSuchName.
//
// NOT implemented (documented): SNMP SET, multi-varbind, SNMPv2/v3, traps,
// IP/UDP/MAC encapsulation.  The byte stream here is the UDP payload only and
// the testbench is the peer; see docs/management/mgmt_snmp_slice.md.
//
// Request framing: short-form BER lengths only (<=127).  A malformed or
// unsupported request is dropped without a response (no genErr), which keeps
// the slice small; the reference client only emits supported requests.
//
// Register side: simple read bus into rtl/mgmt/afdx_mgmt_regs.v snapshot/RTC
// read-only registers.  The snapshot command itself is issued by the
// testbench/adapter (this module is read-only on the register bus):
//   0x0020 inUcastPkts (snapshot delivered_frames)
//   0x0024 inErrors   (snapshot rejected_frames)
//   0x0038 rtc seconds low 32 bits (TimeTicks, upper 32 bits not exposed in v1)
//   0x0040 rtc nanoseconds (Counter32)
// mgmt_regs has no octet counters, so inOctets/outOctets are direct 32-bit
// ports (same 32-bit wrap semantics as the mgmt_regs counters).
//
// OID table (lexicographic order used by GETNEXT):
//   0 sysDescr       1.3.6.1.2.1.1.1.0        OctetString "AFDX dev slice"
//   1 sysName        1.3.6.1.2.1.1.5.0        OctetString "afdx-es-01"
//   2 ifNumber       1.3.6.1.2.1.2.1.0        INTEGER 2
//   3 ifDescr        1.3.6.1.2.1.2.2.1.2.1    OctetString "lane0"
//   4 inOctets       1.3.6.1.2.1.2.2.1.10.1   Counter32 cnt_in_octets
//   5 inUcastPkts    1.3.6.1.2.1.2.2.1.11.1   Counter32 snapshot 0x0020
//   6 inErrors       1.3.6.1.2.1.2.2.1.14.1   Counter32 snapshot 0x0024
//   7 outOctets      1.3.6.1.2.1.2.2.1.16.1   Counter32 cnt_out_octets
//   8 rtcSeconds     1.3.6.1.4.1.55555.1.1.0  TimeTicks (enterprise test OID)
//   9 rtcNanoseconds 1.3.6.1.4.1.55555.1.2.0  Counter32 (enterprise test OID)
module afdx_snmp_agent (
 input  wire        clk,
 input  wire        reset,
 // inbound UDP payload byte stream
 input  wire        s_valid,
 input  wire [7:0]  s_data,
 input  wire        s_sop,
 input  wire        s_eop,
 output wire        s_ready,
 // outbound response UDP payload byte stream
 output wire        m_valid,
 output wire [7:0]  m_data,
 output wire        m_sop,
 output wire        m_eop,
 input  wire        m_ready,
 // read-only register bus to mgmt_regs
 output reg  [15:0] reg_addr,
 output reg         reg_re,
 input  wire [31:0] reg_rdata,
 input  wire        reg_ready,
 // direct 32-bit octet counters (mgmt_regs has no octet counters)
 input  wire [31:0] cnt_in_octets,
 input  wire [31:0] cnt_out_octets
);
 localparam REQ_MAX   = 256;
 localparam RESP_MAX  = 192;
 localparam [5:0] IDLE=6'd0, RX=6'd1,
   P_MSG_TAG=6'd10, P_MSG_LEN=6'd11,
   P_VER_TAG=6'd12, P_VER_LEN=6'd13, P_VER_VAL=6'd14,
   P_COMM_TAG=6'd15, P_COMM_LEN=6'd16, P_COMM_CPY=6'd17,
   P_PDU_TAG=6'd18, P_PDU_LEN=6'd19,
   P_RID_TAG=6'd20, P_RID_LEN=6'd21, P_RID_CPY=6'd22,
   P_ES_TAG=6'd23, P_ES_LEN=6'd24, P_ES_VAL=6'd25,
   P_EI_TAG=6'd26, P_EI_LEN=6'd27, P_EI_VAL=6'd28,
   P_VBL_TAG=6'd29, P_VBL_LEN=6'd30,
   P_VB_TAG=6'd31, P_VB_LEN=6'd32,
   P_OID_TAG=6'd33, P_OID_LEN=6'd34, P_OID_CPY=6'd35,
   P_VAL_TAG=6'd36, P_VAL_LEN=6'd37, P_VAL_CHK=6'd38,
   R_INIT=6'd40, R_BYTE=6'd41, R_END=6'd42, R_DONE=6'd43,
   C_INIT=6'd44, C_OID=6'd45,
   V_INIT=6'd50, V_FILL=6'd51, V_READ=6'd52, V_BUILD=6'd53,
   BUILD=6'd60, TX=6'd61, DROP=6'd62;

 reg [5:0] state;
 reg [7:0] req_mem [0:511];
 reg [7:0] resp_mem[0:511];
 reg [8:0] rx_count, rx_len, p, base, p_base;
 reg [8:0] msg_end, pdu_end, vbl_end, vb_end;
 reg [8:0] loop_i;
 reg [4:0] comm_len;
 reg [7:0] comm_mem[0:511];
 reg [2:0] rid_len;
 reg [7:0] rid_mem[0:511];
 reg [4:0] req_oid_len;
 reg [7:0] req_oid[0:511];
 reg pdu_getnext;

 // OID table ROM (initialised for simulation; a synthesis flow would use a ROM)
 reg [7:0] tab_oid[0:511];
 reg [7:0] tab_len[0:511];
 reg [7:0] tab_off[0:511];
 initial begin
  tab_len[0]=8'd8;  tab_off[0]=8'd0;
  tab_len[1]=8'd8;  tab_off[1]=8'd16;
  tab_len[2]=8'd8;  tab_off[2]=8'd32;
  tab_len[3]=8'd10; tab_off[3]=8'd48;
  tab_len[4]=8'd10; tab_off[4]=8'd64;
  tab_len[5]=8'd10; tab_off[5]=8'd80;
  tab_len[6]=8'd10; tab_off[6]=8'd96;
  tab_len[7]=8'd10; tab_off[7]=8'd112;
  tab_len[8]=8'd11; tab_off[8]=8'd128;
  tab_len[9]=8'd11; tab_off[9]=8'd144;
  // 0: 1.3.6.1.2.1.1.1.0
  tab_oid[0]=8'h2B; tab_oid[1]=8'd6; tab_oid[2]=8'd1; tab_oid[3]=8'd2;
  tab_oid[4]=8'd1; tab_oid[5]=8'd1; tab_oid[6]=8'd1; tab_oid[7]=8'd0;
  // 1: 1.3.6.1.2.1.1.5.0
  tab_oid[16]=8'h2B; tab_oid[17]=8'd6; tab_oid[18]=8'd1; tab_oid[19]=8'd2;
  tab_oid[20]=8'd1; tab_oid[21]=8'd1; tab_oid[22]=8'd5; tab_oid[23]=8'd0;
  // 2: 1.3.6.1.2.1.2.1.0
  tab_oid[32]=8'h2B; tab_oid[33]=8'd6; tab_oid[34]=8'd1; tab_oid[35]=8'd2;
  tab_oid[36]=8'd1; tab_oid[37]=8'd2; tab_oid[38]=8'd1; tab_oid[39]=8'd0;
  // 3: 1.3.6.1.2.1.2.2.1.2.1
  tab_oid[48]=8'h2B; tab_oid[49]=8'd6; tab_oid[50]=8'd1; tab_oid[51]=8'd2;
  tab_oid[52]=8'd1; tab_oid[53]=8'd2; tab_oid[54]=8'd2; tab_oid[55]=8'd1;
  tab_oid[56]=8'd2; tab_oid[57]=8'd1;
  // 4: 1.3.6.1.2.1.2.2.1.10.1
  tab_oid[64]=8'h2B; tab_oid[65]=8'd6; tab_oid[66]=8'd1; tab_oid[67]=8'd2;
  tab_oid[68]=8'd1; tab_oid[69]=8'd2; tab_oid[70]=8'd2; tab_oid[71]=8'd1;
  tab_oid[72]=8'd10; tab_oid[73]=8'd1;
  // 5: 1.3.6.1.2.1.2.2.1.11.1
  tab_oid[80]=8'h2B; tab_oid[81]=8'd6; tab_oid[82]=8'd1; tab_oid[83]=8'd2;
  tab_oid[84]=8'd1; tab_oid[85]=8'd2; tab_oid[86]=8'd2; tab_oid[87]=8'd1;
  tab_oid[88]=8'd11; tab_oid[89]=8'd1;
  // 6: 1.3.6.1.2.1.2.2.1.14.1
  tab_oid[96]=8'h2B; tab_oid[97]=8'd6; tab_oid[98]=8'd1; tab_oid[99]=8'd2;
  tab_oid[100]=8'd1; tab_oid[101]=8'd2; tab_oid[102]=8'd2; tab_oid[103]=8'd1;
  tab_oid[104]=8'd14; tab_oid[105]=8'd1;
  // 7: 1.3.6.1.2.1.2.2.1.16.1
  tab_oid[112]=8'h2B; tab_oid[113]=8'd6; tab_oid[114]=8'd1; tab_oid[115]=8'd2;
  tab_oid[116]=8'd1; tab_oid[117]=8'd2; tab_oid[118]=8'd2; tab_oid[119]=8'd1;
  tab_oid[120]=8'd16; tab_oid[121]=8'd1;
  // 8: 1.3.6.1.4.1.55555.1.1.0  (55555 = 0x83 0xB2 0x03)
  tab_oid[128]=8'h2B; tab_oid[129]=8'd6; tab_oid[130]=8'd1; tab_oid[131]=8'd4;
  tab_oid[132]=8'd1; tab_oid[133]=8'h83; tab_oid[134]=8'hB2; tab_oid[135]=8'h03;
  tab_oid[136]=8'd1; tab_oid[137]=8'd1; tab_oid[138]=8'd0;
  // 9: 1.3.6.1.4.1.55555.1.2.0
  tab_oid[144]=8'h2B; tab_oid[145]=8'd6; tab_oid[146]=8'd1; tab_oid[147]=8'd4;
  tab_oid[148]=8'd1; tab_oid[149]=8'h83; tab_oid[150]=8'hB2; tab_oid[151]=8'h03;
  tab_oid[152]=8'd1; tab_oid[153]=8'd2; tab_oid[154]=8'd0;
 end

 // string constants
 function [7:0] descr_byte;
  input [4:0] i;
  case (i)
   5'd0: descr_byte=8'h41;  5'd1: descr_byte=8'h46;  5'd2: descr_byte=8'h44;
   5'd3: descr_byte=8'h58;  5'd4: descr_byte=8'h20;  5'd5: descr_byte=8'h64;
   5'd6: descr_byte=8'h65;  5'd7: descr_byte=8'h76;  5'd8: descr_byte=8'h20;
   5'd9: descr_byte=8'h73;  5'd10: descr_byte=8'h6c; 5'd11: descr_byte=8'h69;
   5'd12: descr_byte=8'h63; 5'd13: descr_byte=8'h65; default: descr_byte=8'h00;
  endcase
 endfunction
 function [7:0] name_byte;
  input [4:0] i;
  case (i)
   5'd0: name_byte=8'h61; 5'd1: name_byte=8'h66; 5'd2: name_byte=8'h64;
   5'd3: name_byte=8'h78; 5'd4: name_byte=8'h2d; 5'd5: name_byte=8'h65;
   5'd6: name_byte=8'h73; 5'd7: name_byte=8'h2d; 5'd8: name_byte=8'h30;
   5'd9: name_byte=8'h31; default: name_byte=8'h00;
  endcase
 endfunction
 function [7:0] ifdescr_byte;
  input [4:0] i;
  case (i)
   5'd0: ifdescr_byte=8'h6c; 5'd1: ifdescr_byte=8'h61; 5'd2: ifdescr_byte=8'h6e;
   5'd3: ifdescr_byte=8'h65; 5'd4: ifdescr_byte=8'h30; default: ifdescr_byte=8'h00;
  endcase
 endfunction

 // resolve / value registers
 reg [8:0] sel_idx;
 reg [8:0] res_i;
 reg [8:0] res_j;
 reg res_lt, res_gt;
 reg [4:0] resp_oid_len;
 reg [7:0] resp_oid[0:511];
 reg [2:0] kind; // 0 octet, 1 int, 2 counter, 3 timeticks, 4 null
 reg [4:0] const_len;
 reg [7:0] val_mem[0:511];
 reg [5:0] val_len;
 reg [1:0] need_reads, read_idx;
 reg [15:0] rd_addr0;
 reg [31:0] rd0;
 reg rd_pending;
 reg [31:0] error_status, error_index, snap_value;
 reg [8:0] build_k, tx_k, resp_len;

 // response length wires
 wire [8:0] len_ver      = 9'd3;
 wire [8:0] len_comm     = 9'd2 + {4'd0, comm_len};
 wire [8:0] len_rid      = 9'd2 + {6'd0, rid_len};
 wire [8:0] len_oid      = 9'd2 + {4'd0, resp_oid_len};
 wire [8:0] len_vb_body  = len_oid + {3'd0, val_len};
 wire [8:0] len_vb       = 9'd2 + len_vb_body;
 wire [8:0] len_vbl      = 9'd2 + len_vb;
 wire [8:0] len_vbl_body = len_vbl - 9'd2;
 wire [8:0] len_vb_body_w = len_vb - 9'd2;
 wire [8:0] len_pdu_body = len_rid + 9'd6 + len_vbl;
 wire [8:0] len_pdu      = 9'd2 + len_pdu_body;
 wire [8:0] len_msg_body = len_ver + len_comm + len_pdu;
 wire [8:0] total_len    = 9'd2 + len_msg_body;
 wire [8:0] base_comm    = 9'd5;
 wire [8:0] base_pdu     = base_comm + len_comm;
 wire [8:0] base_rid     = base_pdu + 9'd2;
 wire [8:0] base_es      = base_rid + len_rid;
 wire [8:0] base_ei      = base_es + 9'd3;
 wire [8:0] base_vbl     = base_ei + 9'd3;
 wire [8:0] base_vb      = base_vbl + 9'd2;
 wire [8:0] base_oid     = base_vb + 9'd2;
 wire [8:0] base_val     = base_oid + len_oid;

 assign s_ready = (state == RX);
 assign m_valid = (state == TX);
 assign m_data  = resp_mem[tx_k];
 assign m_sop   = (state == TX) && (tx_k == 9'd0);
 assign m_eop   = (state == TX) && (tx_k == (resp_len - 9'd1));

 always @(posedge clk) begin
  if (reset) begin
   state <= IDLE;
   rx_count <= 9'd0;
   rx_len <= 9'd0;
   p <= 9'd0;
   base <= 9'd0;
   p_base <= 9'd0;
   loop_i <= 8'd0;
   comm_len <= 5'd0;
   rid_len <= 3'd0;
   req_oid_len <= 5'd0;
   pdu_getnext <= 1'b0;
   msg_end <= 9'd0; pdu_end <= 9'd0; vbl_end <= 9'd0; vb_end <= 9'd0;
   sel_idx <= 4'd0;
   resp_oid_len <= 5'd0;
   val_len <= 6'd0;
   kind <= 3'd0;
   const_len <= 5'd0;
   need_reads <= 2'd0;
   read_idx <= 2'd0;
   rd_pending <= 1'b0;
   rd0 <= 32'd0;
   rd_addr0 <= 16'd0;
   reg_addr <= 16'd0;
   reg_re <= 1'b0;
   error_status <= 32'd0;
   error_index <= 32'd0;
   resp_len <= 9'd0;
   build_k <= 9'd0;
   tx_k <= 9'd0;
  end else begin
   reg_re <= 1'b0;
   case (state)
    IDLE: begin
     rx_count <= 9'd0;
     loop_i <= 8'd0;
     if (s_valid && s_sop) begin
      req_mem[0] <= s_data;
      rx_count <= 9'd1;
      state <= s_eop ? P_MSG_TAG : RX;
     end
    end
    RX: begin
     if (s_valid) begin
      req_mem[rx_count] <= s_data;
      rx_count <= rx_count + 9'd1;
      if (s_eop) begin
       rx_len <= rx_count + 9'd1;
       p <= 9'd0;
       state <= P_MSG_TAG;
      end else if (rx_count == (REQ_MAX-1)) begin
       state <= DROP;
      end
     end
    end

    // ---- BER parse (short-form lengths only) ----
    P_MSG_TAG: begin
     if (req_mem[p] != 8'h30) state <= DROP;
     else begin p <= p + 9'd1; state <= P_MSG_LEN; end
    end
    P_MSG_LEN: begin
     msg_end <= p + 9'd1 + {1'b0, req_mem[p]};
     p <= p + 9'd1;
     state <= P_VER_TAG;
    end
    P_VER_TAG: begin
     if (req_mem[p] != 8'h02) state <= DROP;
     else begin p <= p + 9'd1; state <= P_VER_LEN; end
    end
    P_VER_LEN: begin
     if (req_mem[p] != 8'd1) state <= DROP;
     else begin p <= p + 9'd1; state <= P_VER_VAL; end
    end
    P_VER_VAL: begin
     if (req_mem[p] != 8'd0) state <= DROP;
     else begin p <= p + 9'd1; state <= P_COMM_TAG; end
    end
    P_COMM_TAG: begin
     if (req_mem[p] != 8'h04) state <= DROP;
     else begin p <= p + 9'd1; state <= P_COMM_LEN; end
    end
    P_COMM_LEN: begin
     if (req_mem[p] == 8'd0 || req_mem[p] > 8'd16) state <= DROP;
     else begin
      comm_len <= req_mem[p][4:0];
      base <= p + 9'd1;
      p <= p + 9'd1;
      loop_i <= 8'd0;
      state <= P_COMM_CPY;
     end
    end
    P_COMM_CPY: begin
     if (loop_i == {3'd0, comm_len}) begin
      p <= base + {3'd0, comm_len};
      loop_i <= 8'd0;
      state <= P_PDU_TAG;
     end else begin
      comm_mem[loop_i] <= req_mem[base + loop_i];
      loop_i <= loop_i + 8'd1;
     end
    end
    P_PDU_TAG: begin
     if (req_mem[p] == 8'hA0) begin pdu_getnext <= 1'b0; p <= p + 9'd1; state <= P_PDU_LEN; end
     else if (req_mem[p] == 8'hA1) begin pdu_getnext <= 1'b1; p <= p + 9'd1; state <= P_PDU_LEN; end
     else state <= DROP;
    end
    P_PDU_LEN: begin
     pdu_end <= p + 9'd1 + {1'b0, req_mem[p]};
     p <= p + 9'd1;
     state <= P_RID_TAG;
    end
    P_RID_TAG: begin
     if (req_mem[p] != 8'h02) state <= DROP;
     else begin p <= p + 9'd1; state <= P_RID_LEN; end
    end
    P_RID_LEN: begin
     if (req_mem[p] == 8'd0 || req_mem[p] > 8'd4) state <= DROP;
     else begin
      rid_len <= req_mem[p][2:0];
      base <= p + 9'd1;
      p <= p + 9'd1;
      loop_i <= 8'd0;
      state <= P_RID_CPY;
     end
    end
    P_RID_CPY: begin
     if (loop_i == {5'd0, rid_len}) begin
      p <= base + {6'd0, rid_len};
      loop_i <= 8'd0;
      state <= P_ES_TAG;
     end else begin
      rid_mem[loop_i] <= req_mem[base + loop_i];
      loop_i <= loop_i + 8'd1;
     end
    end
    P_ES_TAG: begin
     if (req_mem[p] != 8'h02) state <= DROP;
     else begin p <= p + 9'd1; state <= P_ES_LEN; end
    end
    P_ES_LEN: begin
     if (req_mem[p] != 8'd1) state <= DROP;
     else begin p <= p + 9'd1; state <= P_ES_VAL; end
    end
    P_ES_VAL: begin
     if (req_mem[p] != 8'd0) state <= DROP;
     else begin p <= p + 9'd1; state <= P_EI_TAG; end
    end
    P_EI_TAG: begin
     if (req_mem[p] != 8'h02) state <= DROP;
     else begin p <= p + 9'd1; state <= P_EI_LEN; end
    end
    P_EI_LEN: begin
     if (req_mem[p] != 8'd1) state <= DROP;
     else begin p <= p + 9'd1; state <= P_EI_VAL; end
    end
    P_EI_VAL: begin
     if (req_mem[p] != 8'd0) state <= DROP;
     else begin p <= p + 9'd1; state <= P_VBL_TAG; end
    end
    P_VBL_TAG: begin
     if (req_mem[p] != 8'h30) state <= DROP;
     else begin p <= p + 9'd1; state <= P_VBL_LEN; end
    end
    P_VBL_LEN: begin
     vbl_end <= p + 9'd1 + {1'b0, req_mem[p]};
     p <= p + 9'd1;
     state <= P_VB_TAG;
    end
    P_VB_TAG: begin
     if (req_mem[p] != 8'h30) state <= DROP;
     else begin p <= p + 9'd1; state <= P_VB_LEN; end
    end
    P_VB_LEN: begin
     vb_end <= p + 9'd1 + {1'b0, req_mem[p]};
     p <= p + 9'd1;
     state <= P_OID_TAG;
    end
    P_OID_TAG: begin
     if (req_mem[p] != 8'h06) state <= DROP;
     else begin p <= p + 9'd1; state <= P_OID_LEN; end
    end
    P_OID_LEN: begin
     if (req_mem[p] == 8'd0 || req_mem[p] > 8'd16) state <= DROP;
     else begin
      req_oid_len <= req_mem[p][4:0];
      base <= p + 9'd1;
      p <= p + 9'd1;
      loop_i <= 8'd0;
      state <= P_OID_CPY;
     end
    end
    P_OID_CPY: begin
     if (loop_i == {3'd0, req_oid_len}) begin
      p <= base + {3'd0, req_oid_len};
      loop_i <= 8'd0;
      state <= P_VAL_TAG;
     end else begin
      req_oid[loop_i] <= req_mem[base + loop_i];
      loop_i <= loop_i + 8'd1;
     end
    end
    P_VAL_TAG: begin
     if (req_mem[p] != 8'h05) state <= DROP;
     else begin p <= p + 9'd1; state <= P_VAL_LEN; end
    end
    P_VAL_LEN: begin
     if (req_mem[p] != 8'd0) state <= DROP;
     else begin p <= p + 9'd1; state <= P_VAL_CHK; end
    end
    P_VAL_CHK: begin
     if ((p == vb_end) && (vb_end == vbl_end) && (vbl_end == pdu_end) &&
         (pdu_end == msg_end) && (msg_end == rx_len))
      state <= R_INIT;
     else
      state <= DROP;
    end

    // ---- OID resolution ----
    R_INIT: begin
     res_i <= 4'd0;
     res_j <= 9'd0;
     res_lt <= 1'b0;
     res_gt <= 1'b0;
     sel_idx <= 9'd15;
     state <= R_BYTE;
    end
    R_BYTE: begin
     if (res_i >= 9'd10) begin
      state <= R_DONE;
     end else if (res_j < req_oid_len && res_j < tab_len[res_i]) begin
      if (req_oid[res_j] < tab_oid[tab_off[res_i] + res_j]) begin
       res_gt <= 1'b1; state <= R_END;
      end else if (req_oid[res_j] > tab_oid[tab_off[res_i] + res_j]) begin
       res_lt <= 1'b1; state <= R_END;
      end else begin
       res_j <= res_j + 9'd1;
      end
     end else begin
      if (req_oid_len == tab_len[res_i]) state <= R_END;
      else if (req_oid_len < tab_len[res_i]) begin res_gt <= 1'b1; state <= R_END; end
      else begin res_lt <= 1'b1; state <= R_END; end
     end
    end
    R_END: begin
     if (res_lt) begin
      res_i <= res_i + 9'd1;
      res_j <= 9'd0;
      res_lt <= 1'b0;
      res_gt <= 1'b0;
      state <= R_BYTE;
     end else if (res_gt) begin
      if (pdu_getnext) begin
       sel_idx <= res_i;
       state <= R_DONE;
      end else begin
       res_i <= res_i + 9'd1;
       res_j <= 9'd0;
       res_lt <= 1'b0;
       res_gt <= 1'b0;
       state <= R_BYTE;
      end
     end else begin
      if (pdu_getnext) begin
       if (res_i == 9'd9) sel_idx <= 9'd15;
       else sel_idx <= res_i + 4'd1;
      end else begin
       sel_idx <= res_i;
      end
      state <= R_DONE;
     end
    end
    R_DONE: begin
     state <= C_INIT;
    end
    C_INIT: begin
     resp_oid_len <= (sel_idx == 9'd15) ? req_oid_len : tab_len[sel_idx][4:0];
     loop_i <= 8'd0;
     state <= C_OID;
    end
    C_OID: begin
     if (loop_i == {3'd0, resp_oid_len}) begin
      loop_i <= 8'd0;
      state <= V_INIT;
     end else begin
      resp_oid[loop_i] <= (sel_idx == 9'd15) ? req_oid[loop_i] :
                          tab_oid[tab_off[sel_idx] + loop_i];
      loop_i <= loop_i + 8'd1;
     end
    end

    // ---- value fetch ----
    V_INIT: begin
     loop_i <= 8'd0;
     need_reads <= 2'd0;
     read_idx <= 2'd0;
     rd_pending <= 1'b0;
     error_status <= 32'd0;
     error_index <= 32'd0;
     case (sel_idx)
      9'd0: begin kind <= 3'd0; const_len <= 5'd14; state <= V_FILL; end
      9'd1: begin kind <= 3'd0; const_len <= 5'd10; state <= V_FILL; end
      9'd2: begin
       kind <= 3'd1;
       val_mem[0] <= 8'h02; val_mem[1] <= 8'h01; val_mem[2] <= 8'd2;
       val_len <= 6'd3;
       state <= V_BUILD;
      end
      9'd3: begin kind <= 3'd0; const_len <= 5'd5; state <= V_FILL; end
      9'd4: begin
       kind <= 3'd2; rd0 <= cnt_in_octets; state <= V_BUILD;
      end
      9'd5: begin
       kind <= 3'd2; need_reads <= 2'd1; rd_addr0 <= 16'h0020; state <= V_READ;
      end
      9'd6: begin
       kind <= 3'd2; need_reads <= 2'd1; rd_addr0 <= 16'h0024; state <= V_READ;
      end
      9'd7: begin
       kind <= 3'd2; rd0 <= cnt_out_octets; state <= V_BUILD;
      end
      9'd8: begin
       kind <= 3'd3; need_reads <= 2'd1; rd_addr0 <= 16'h0038; state <= V_READ;
      end
      9'd9: begin
       kind <= 3'd2; need_reads <= 2'd1; rd_addr0 <= 16'h0040; state <= V_READ;
      end
      default: begin
       kind <= 3'd4;
       error_status <= 32'd2;
       error_index <= 32'd1;
       val_mem[0] <= 8'h05; val_mem[1] <= 8'h00;
       val_len <= 6'd2;
       state <= V_BUILD;
      end
     endcase
    end
    V_FILL: begin
     if (loop_i < {3'd0, const_len}) begin
      if (sel_idx == 4'd0) val_mem[2 + loop_i] <= descr_byte(loop_i[4:0]);
      else if (sel_idx == 4'd1) val_mem[2 + loop_i] <= name_byte(loop_i[4:0]);
      else val_mem[2 + loop_i] <= ifdescr_byte(loop_i[4:0]);
      loop_i <= loop_i + 8'd1;
     end else begin
      val_mem[0] <= 8'h04;
      val_mem[1] <= {2'd0, const_len[4:0]};
      val_len <= 6'd2 + {1'b0, const_len};
      loop_i <= 8'd0;
      state <= V_BUILD;
     end
    end
    V_READ: begin
     if (!rd_pending) begin
      reg_addr <= rd_addr0;
      reg_re <= 1'b1;
      rd_pending <= 1'b1;
     end else if (reg_ready) begin
      rd0 <= reg_rdata;
      rd_pending <= 1'b0;
      reg_re <= 1'b0;
      state <= V_BUILD;
     end else begin
      reg_re <= 1'b1; // hold request until the register bus acknowledges
     end
    end
    V_BUILD: begin
     case (kind)
      3'd0: begin
       val_len <= 6'd2 + {1'b0, const_len};
      end
      3'd1: begin
       val_len <= 6'd3;
      end
      3'd2: begin
       val_mem[0] <= 8'h41;
       val_mem[1] <= 8'h05;
       val_mem[2] <= 8'h00;
       val_mem[3] <= rd0[31:24];
       val_mem[4] <= rd0[23:16];
       val_mem[5] <= rd0[15:8];
       val_mem[6] <= rd0[7:0];
       val_len <= 6'd7;
      end
      3'd3: begin
       val_mem[0] <= 8'h43;
       val_mem[1] <= 8'h05;
       val_mem[2] <= 8'h00;
       val_mem[3] <= rd0[31:24];
       val_mem[4] <= rd0[23:16];
       val_mem[5] <= rd0[15:8];
       val_mem[6] <= rd0[7:0];
       val_len <= 6'd7;
      end
      default: begin
       val_len <= 6'd2;
      end
     endcase
     build_k <= 9'd0;
     state <= BUILD;
    end

    // ---- response build ----
    BUILD: begin
     if (build_k == 9'd0) resp_mem[build_k] <= 8'h30;
     else if (build_k == 9'd1) resp_mem[build_k] <= len_msg_body[7:0];
     else if (build_k == 9'd2) resp_mem[build_k] <= 8'h02;
     else if (build_k == 9'd3) resp_mem[build_k] <= 8'h01;
     else if (build_k == 9'd4) resp_mem[build_k] <= 8'h00;
     else if (build_k == 9'd5) resp_mem[build_k] <= 8'h04;
     else if (build_k == 9'd6) resp_mem[build_k] <= {3'd0, comm_len};
     else if (build_k >= base_comm + 9'd2 && build_k < base_pdu)
      resp_mem[build_k] <= comm_mem[build_k - (base_comm + 9'd2)];
     else if (build_k == base_pdu) resp_mem[build_k] <= 8'hA2;
     else if (build_k == base_pdu + 9'd1) resp_mem[build_k] <= len_pdu_body[7:0];
     else if (build_k == base_rid) resp_mem[build_k] <= 8'h02;
     else if (build_k == base_rid + 9'd1) resp_mem[build_k] <= {5'd0, rid_len};
     else if (build_k >= base_rid + 9'd2 && build_k < base_es)
      resp_mem[build_k] <= rid_mem[build_k - (base_rid + 9'd2)];
     else if (build_k == base_es) resp_mem[build_k] <= 8'h02;
     else if (build_k == base_es + 9'd1) resp_mem[build_k] <= 8'h01;
     else if (build_k == base_es + 9'd2) resp_mem[build_k] <= error_status[7:0];
     else if (build_k == base_ei) resp_mem[build_k] <= 8'h02;
     else if (build_k == base_ei + 9'd1) resp_mem[build_k] <= 8'h01;
     else if (build_k == base_ei + 9'd2) resp_mem[build_k] <= error_index[7:0];
     else if (build_k == base_vbl) resp_mem[build_k] <= 8'h30;
     else if (build_k == base_vbl + 9'd1) resp_mem[build_k] <= len_vbl_body[7:0];
     else if (build_k == base_vb) resp_mem[build_k] <= 8'h30;
     else if (build_k == base_vb + 9'd1) resp_mem[build_k] <= len_vb_body_w[7:0];
     else if (build_k == base_oid) resp_mem[build_k] <= 8'h06;
     else if (build_k == base_oid + 9'd1) resp_mem[build_k] <= {3'd0, resp_oid_len};
     else if (build_k >= base_oid + 9'd2 && build_k < base_val)
      resp_mem[build_k] <= resp_oid[build_k - (base_oid + 9'd2)];
     else
      resp_mem[build_k] <= val_mem[build_k - base_val];
     if (build_k + 9'd1 == total_len) begin
      resp_len <= total_len;
      build_k <= 9'd0;
      tx_k <= 9'd0;
      state <= TX;
     end else begin
      build_k <= build_k + 9'd1;
     end
    end
    TX: begin
     if (m_ready) begin
      if (tx_k + 9'd1 == resp_len) begin
       tx_k <= 9'd0;
       state <= IDLE;
      end else begin
       tx_k <= tx_k + 9'd1;
      end
     end
    end
    DROP: begin
     rx_count <= 9'd0;
     state <= IDLE;
    end
    default: state <= IDLE;
   endcase
  end
 end
endmodule
