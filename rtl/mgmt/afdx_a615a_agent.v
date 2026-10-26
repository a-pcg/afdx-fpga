`timescale 1ns/1ps
// afdx_a615a_agent -- ARINC 615A minimal data-loading subset (standalone slice).
//
// NOT a complete ARINC 615A implementation and NOT wire-compatible with it: this
// slice moves a self-defined byte protocol over a UDP payload byte stream (the
// management port number, e.g. fixed test port 1000, is out of band because the
// management plane carries no IP/UDP headers).  It implements the 615A *ideas*
// requested for the demo: Find / Upload / Download with a TFTP-style
// DATA/ACK block exchange (512-byte blocks, sequence 1..N), a single fixed file
// buffer (2 blocks, no file system), ACK-timeout retransmission with a retry
// limit, duplicate-DATA idempotence and a minimal error-code set.
//
// Message format (one UDP payload = one message, multi-byte fields big endian):
//   0x01 FIND request          : [0x01]
//   0x02 FIND response         : [0x02, status=0, present, name_len, name...]
//   0x03 DOWNLOAD request      : [0x03, user, name_len, name...]
//   0x04 UPLOAD request        : [0x04, user, name_len, name...]
//   0x05 ACK                   : [0x05, seq_hi, seq_lo]
//   0x06 ERROR                 : [0x06, err, seq_hi, seq_lo]
//   0x07 DATA                  : [0x07, seq_hi, seq_lo, len_hi, len_lo, data...]
//
// Error codes: 0 OK, 1 File not found, 2 Access violation, 3 Insufficient storage,
//              4 Illegal operation, 6 File exists, 8 No such user,
//              0x0A Transfer aborted (retry limit exceeded; documented extension).
// The only accepted user id is 1 (loader); anything else returns "No such user".
// write_protect=1 makes every DOWNLOAD request fail with "Access violation".
//
// Transfer rules
//   * DOWNLOAD: request -> ACK(0); host sends DATA 1..N; the agent ACKs each
//     block; a block shorter than 512 bytes ends the transfer (a zero-length
//     final block therefore ends a transfer whose length is a multiple of 512).
//     Duplicate DATA (seq == last accepted sequence) is re-ACKed without rewriting
//     memory; unexpected sequence numbers are re-ACKed with the last accepted
//     sequence number (idempotent, no write).  Writing beyond the 1024-byte
//     buffer returns "Insufficient storage" and leaves the transfer idle.
//   * UPLOAD: request -> ACK(0); the agent sends DATA 1..N and waits for an ACK
//     per block.  Missing an ACK for ack_timeout clocks retransmits the same
//     block (up to max_retries times); exhausting the retries sends ERROR 0x0A
//     and aborts the transfer.  Duplicate/odd ACKs do not advance the transfer.
//   * One transfer at a time; new Find/Upload/Download requests during a transfer
//     are rejected with "Illegal operation".
//
// Storage model: one file name (<= NAME_MAX bytes) and one 1024-byte data buffer
// (2 blocks).  No file system, no persistence, contents start undefined.
//
// Status read port (combinational, 6-bit address):
//   0x00 status   {.., busy, dl_active, ul_active, file_present, state[3:0]}
//   0x01 file_len[15:0]
//   0x02 rx_packets      0x03 tx_packets       0x04 find_requests
//   0x05 download_requests  0x06 upload_requests   0x07 blocks_written
//   0x08 blocks_sent     0x09 retransmits      0x0A duplicate_data
//   0x0B timeouts        0x0C retry_aborts     0x0D error_responses
//   0x0E last_error      0x0F version (0x0001_0000)
module afdx_a615a_agent #(
 parameter BLOCK_SIZE = 512,
 parameter MEM_BYTES  = 1024,
 parameter NAME_MAX   = 16,
 parameter RX_MAX     = 520,
 parameter TX_MAX     = 520
)(
 input  wire        clk,
 input  wire        reset,
 // inbound / outbound UDP payload byte streams
 input  wire        s_valid,
 input  wire [7:0]  s_data,
 input  wire        s_sop,
 input  wire        s_eop,
 output wire        s_ready,
 output wire        m_valid,
 output wire [7:0]  m_data,
 output wire        m_sop,
 output wire        m_eop,
 input  wire        m_ready,
 // configuration
 input  wire [31:0] ack_timeout,
 input  wire [3:0]  max_retries,
 input  wire        write_protect,
 // status read port
 input  wire [5:0]  st_addr,
 output reg  [31:0] st_rdata,
 output wire        busy,
 output wire        file_present,
 output wire [15:0] file_len
);
 localparam [7:0] OP_FIND_REQ  = 8'h01,
                  OP_FIND_RESP = 8'h02,
                  OP_DOWNLOAD  = 8'h03,
                  OP_UPLOAD    = 8'h04,
                  OP_ACK       = 8'h05,
                  OP_ERROR     = 8'h06,
                  OP_DATA      = 8'h07;
 localparam [4:0] E_OK=5'd0, E_NOFILE=5'd1, E_ACCESS=5'd2, E_NOSPACE=5'd3,
                  E_ILLEGAL=5'd4, E_EXISTS=5'd6, E_NOUSER=5'd8, E_ABORT=5'd10;
 localparam [7:0] USER_LOADER = 8'd1;

 // main FSM states
 localparam [3:0] S_IDLE=4'd0, S_HDR=4'd1, S_NAME=4'd2, S_ACT=4'd3,
                  S_WRITE=4'd4, S_BUILD=4'd5, S_TX=4'd6, S_SEND=4'd7,
                  S_ACKWAIT=4'd8;
 // request kinds
 localparam [2:0] RK_NONE=3'd0, RK_FIND=3'd1, RK_DOWNLOAD=3'd2, RK_UPLOAD=3'd3,
                  RK_DATA=3'd4, RK_ACK=3'd5;
 // response kinds
 localparam [1:0] RS_ACK=2'd0, RS_ERROR=2'd1, RS_FIND=2'd2;
 // after-TX actions
 localparam [1:0] AT_IDLE=2'd0, AT_ACKWAIT=2'd1, AT_UPLOAD=2'd2;

 // ------------------------------------------------------------------
 // storage / session state
 // ------------------------------------------------------------------
 reg [7:0]  file_mem [0:MEM_BYTES-1];
 reg [7:0]  file_name [0:NAME_MAX-1];
 reg [4:0]  file_name_len;
 reg        file_present_r;
 reg [15:0] file_len_r;

 reg        dl_active;
 reg [15:0] dl_expected;
 reg [15:0] dl_last_acked;
 reg        ul_active;
 reg        ackwaiting;
 reg [15:0] ul_seq;
 reg [15:0] ul_total;
 reg [3:0]  retries_used;
 reg [31:0] timer;

 // receive capture
 reg [7:0]  rx_buf [0:RX_MAX-1];
 reg        r_cap;
 reg [10:0] r_idx;
 reg        packet_ready;
 reg [10:0] rx_len_r;
 reg        rx_overflow;

 // transmit
 reg [7:0]  tx_buf [0:TX_MAX-1];
 reg [10:0] tx_len_r;
 reg [10:0] tx_idx;

 // FSM registers
 reg [3:0]  state;
 reg [2:0]  req_kind;
 reg [4:0]  dec_i;
 reg        name_ok;
 reg        pkt_release;
 reg [1:0]  resp_kind;
 reg [15:0] resp_seq;
 reg [4:0]  resp_err;
 reg [1:0]  after_tx;
 reg        send_started;
 reg [10:0] send_cnt;
 reg [15:0] send_off;
 reg [15:0] send_blen;
 reg        wr_started;
 reg [10:0] wr_cnt;
 reg [15:0] wr_off;
 reg        bld_started;
 reg [4:0]  bld_cnt;
 reg [4:0]  last_error_r;

 // counters
 reg [31:0] cnt_rx_pkts, cnt_tx_pkts, cnt_find, cnt_download, cnt_upload;
 reg [31:0] cnt_blocks_written, cnt_blocks_sent, cnt_retransmits;
 reg [31:0] cnt_dup_data, cnt_timeouts, cnt_retry_aborts, cnt_errors;

 // ------------------------------------------------------------------
 // inbound byte-stream capture (one packet buffer, backpressure while busy)
 // ------------------------------------------------------------------
 // s_ready is low while a captured packet is still being consumed, so the peer
 // cannot overwrite the buffer.  Overflow bytes are accepted and discarded.
 assign s_ready = !packet_ready && !reset;

 always @(posedge clk) begin
  if (reset) begin
   r_cap        <= 1'b0;
   r_idx        <= 11'd0;
   packet_ready <= 1'b0;
   rx_len_r     <= 11'd0;
   rx_overflow  <= 1'b0;
   cnt_rx_pkts  <= 32'd0;
  end else begin
   if (pkt_release) packet_ready <= 1'b0;
   if (!packet_ready) begin
    if (s_valid && s_ready) begin
     if (!r_cap) begin
      if (s_sop) begin
       rx_buf[0] <= s_data;
       r_idx     <= 11'd1;
       rx_overflow <= 1'b0;
       if (s_eop) begin
        packet_ready <= 1'b1;
        rx_len_r     <= 11'd1;
        cnt_rx_pkts  <= cnt_rx_pkts + 32'd1;
       end else begin
        r_cap <= 1'b1;
       end
      end
     end else begin
      if (r_idx < RX_MAX) rx_buf[r_idx[9:0]] <= s_data;
      else rx_overflow <= 1'b1;
      r_idx <= r_idx + 11'd1;
      if (s_eop) begin
       r_cap        <= 1'b0;
       packet_ready <= 1'b1;
       rx_len_r     <= (r_idx < RX_MAX) ? (r_idx + 11'd1) : RX_MAX[10:0];
       cnt_rx_pkts  <= cnt_rx_pkts + 32'd1;
      end
     end
    end
   end
  end
 end

 assign file_present = file_present_r;
 assign file_len     = file_len_r;
 assign busy         = (state != S_IDLE) || packet_ready || r_cap;

 // ------------------------------------------------------------------
 // parsed header fields (valid while packet_ready)
 // ------------------------------------------------------------------
 wire [7:0]  p_op   = rx_buf[0];
 wire [7:0]  p_user = rx_buf[1];
 wire [7:0]  p_nlen = rx_buf[2];
 wire [15:0] p_seq  = {rx_buf[1], rx_buf[2]};
 wire [15:0] p_len  = {rx_buf[3], rx_buf[4]};
 wire [10:0] p_rxlen = rx_len_r;

 // narrow addressing helpers (arrays are <= 1024 entries, 10-bit indices)
 wire [9:0] rx_naddr = {5'd0, dec_i} + 10'd3;
 wire [9:0] rx_daddr = wr_cnt[9:0] + 10'd5;
 wire [9:0] wr_addr  = wr_off[9:0] + wr_cnt[9:0];
 wire [9:0] tx_daddr = send_cnt[9:0] + 10'd4;
 wire [9:0] rd_addr  = send_off[9:0] + send_cnt[9:0] - 10'd1;
 wire [9:0] tx_naddr = {5'd0, bld_cnt} + 10'd4;

 // ------------------------------------------------------------------
 // outbound byte stream
 // ------------------------------------------------------------------
 assign m_valid = (state == S_TX) && !reset;
 assign m_data  = tx_buf[tx_idx[9:0]];
 assign m_sop   = (tx_idx == 11'd0);
 assign m_eop   = (tx_idx + 11'd1 == tx_len_r);

 // helpers
 wire [31:0] timeout_eff = (ack_timeout == 32'd0) ? 32'd1 : ack_timeout;
 wire [3:0]  retries_eff = (max_retries == 4'd0) ? 4'd0 : max_retries;

 // ------------------------------------------------------------------
 // main protocol FSM
 // ------------------------------------------------------------------
 always @(posedge clk) begin
  if (reset) begin
   state            <= S_IDLE;
   req_kind         <= RK_NONE;
   dec_i            <= 5'd0;
   name_ok          <= 1'b1;
   pkt_release      <= 1'b0;
   resp_kind        <= RS_ACK;
   resp_seq         <= 16'd0;
   resp_err         <= E_OK;
   after_tx         <= AT_IDLE;
   send_started     <= 1'b0;
   send_cnt         <= 11'd0;
   send_off         <= 16'd0;
   send_blen        <= 16'd0;
   wr_started       <= 1'b0;
   wr_cnt           <= 11'd0;
   wr_off           <= 16'd0;
   bld_started      <= 1'b0;
   bld_cnt          <= 5'd0;
   last_error_r     <= E_OK;
   file_name_len    <= 5'd0;
   file_present_r   <= 1'b0;
   file_len_r       <= 16'd0;
   dl_active        <= 1'b0;
   dl_expected      <= 16'd0;
   dl_last_acked    <= 16'd0;
   ul_active        <= 1'b0;
   ackwaiting       <= 1'b0;
   ul_seq           <= 16'd0;
   ul_total         <= 16'd0;
   retries_used     <= 4'd0;
   timer            <= 32'd0;
   tx_len_r         <= 11'd0;
   tx_idx           <= 11'd0;
   cnt_tx_pkts      <= 32'd0;
   cnt_find         <= 32'd0;
   cnt_download     <= 32'd0;
   cnt_upload       <= 32'd0;
   cnt_blocks_written <= 32'd0;
   cnt_blocks_sent  <= 32'd0;
   cnt_retransmits  <= 32'd0;
   cnt_dup_data     <= 32'd0;
   cnt_timeouts     <= 32'd0;
   cnt_retry_aborts <= 32'd0;
   cnt_errors       <= 32'd0;
  end else begin
   pkt_release <= 1'b0;

   case (state)
    // ------------------------------------------------------------
    S_IDLE: begin
     if (packet_ready) state <= S_HDR;
    end

    // ------------------------------------------------------------
    S_HDR: begin
     req_kind <= RK_NONE;
     dec_i    <= 5'd0;
     name_ok  <= 1'b1;
     if (rx_overflow) begin
      resp_kind <= RS_ERROR; resp_err <= E_ILLEGAL; resp_seq <= 16'd0;
      pkt_release <= 1'b1;
      state <= S_BUILD;
     end else if (ackwaiting && !((p_op == OP_ACK) && (p_rxlen == 11'd3))) begin
      pkt_release <= 1'b1;              // keep waiting for the ACK
      state <= S_ACKWAIT;
     end else begin
      case (p_op)
       OP_FIND_REQ: begin
        if (p_rxlen != 11'd1) begin
         resp_kind <= RS_ERROR; resp_err <= E_ILLEGAL; resp_seq <= 16'd0;
         pkt_release <= 1'b1;
         state <= S_BUILD;
        end else begin
         cnt_find <= cnt_find + 32'd1;
         req_kind <= RK_FIND;
         state    <= S_ACT;
        end
       end
       OP_DOWNLOAD, OP_UPLOAD: begin
        if (p_op == OP_DOWNLOAD) cnt_download <= cnt_download + 32'd1;
        else                     cnt_upload   <= cnt_upload   + 32'd1;
        if ((p_nlen == 8'd0) || (p_nlen > NAME_MAX) ||
            (p_rxlen != (11'd3 + {3'd0, p_nlen}))) begin
         resp_kind <= RS_ERROR; resp_err <= E_ILLEGAL; resp_seq <= 16'd0;
         pkt_release <= 1'b1;
         state <= S_BUILD;
        end else if (p_op == OP_DOWNLOAD) begin
         if (write_protect) begin
          resp_kind <= RS_ERROR; resp_err <= E_ACCESS; resp_seq <= 16'd0;
          pkt_release <= 1'b1; state <= S_BUILD;
         end else if (p_user != USER_LOADER) begin
          resp_kind <= RS_ERROR; resp_err <= E_NOUSER; resp_seq <= 16'd0;
          pkt_release <= 1'b1; state <= S_BUILD;
         end else if (dl_active || ul_active) begin
          resp_kind <= RS_ERROR; resp_err <= E_ILLEGAL; resp_seq <= 16'd0;
          pkt_release <= 1'b1; state <= S_BUILD;
         end else if (file_present_r) begin
          resp_kind <= RS_ERROR; resp_err <= E_EXISTS; resp_seq <= 16'd0;
          pkt_release <= 1'b1; state <= S_BUILD;
         end else begin
          req_kind <= RK_DOWNLOAD;      // store the name in S_NAME
          state    <= S_NAME;
         end
        end else begin
         if (p_user != USER_LOADER) begin
          resp_kind <= RS_ERROR; resp_err <= E_NOUSER; resp_seq <= 16'd0;
          pkt_release <= 1'b1; state <= S_BUILD;
         end else if (!file_present_r) begin
          resp_kind <= RS_ERROR; resp_err <= E_NOFILE; resp_seq <= 16'd0;
          pkt_release <= 1'b1; state <= S_BUILD;
         end else if (dl_active || ul_active) begin
          resp_kind <= RS_ERROR; resp_err <= E_ILLEGAL; resp_seq <= 16'd0;
          pkt_release <= 1'b1; state <= S_BUILD;
         end else begin
          req_kind <= RK_UPLOAD;        // compare the name in S_NAME
          state    <= S_NAME;
         end
        end
       end
       OP_DATA: begin
        if (!dl_active) begin
         resp_kind <= RS_ERROR; resp_err <= E_ILLEGAL; resp_seq <= 16'd0;
         pkt_release <= 1'b1; state <= S_BUILD;
        end else if ((p_len > BLOCK_SIZE) || (p_rxlen != (11'd5 + p_len[10:0]))) begin
         resp_kind <= RS_ERROR; resp_err <= E_ILLEGAL; resp_seq <= 16'd0;
         pkt_release <= 1'b1; state <= S_BUILD;
        end else begin
         req_kind <= RK_DATA;
         state    <= S_ACT;
        end
       end
       OP_ACK: begin
        req_kind <= RK_ACK;
        state    <= S_ACT;
       end
       default: begin
        resp_kind <= RS_ERROR; resp_err <= E_ILLEGAL; resp_seq <= 16'd0;
        pkt_release <= 1'b1; state <= S_BUILD;
       end
      endcase
     end
    end

    // ------------------------------------------------------------
    S_NAME: begin
     if ({3'd0,dec_i} < p_nlen) begin
      if (p_op == OP_DOWNLOAD) file_name[dec_i[3:0]] <= rx_buf[rx_naddr];
      else if (rx_buf[rx_naddr] != file_name[dec_i[3:0]]) name_ok <= 1'b0;
      dec_i <= dec_i + 5'd1;
     end else begin
      state <= S_ACT;
     end
    end

    // ------------------------------------------------------------
    S_ACT: begin
     case (req_kind)
      RK_FIND: begin
       pkt_release <= 1'b1;
       resp_kind <= RS_FIND;
       resp_seq  <= 16'd0;
       state     <= S_BUILD;
      end
      RK_DOWNLOAD: begin
       file_name_len <= p_nlen[4:0];
       dl_active     <= 1'b1;
       dl_expected   <= 16'd1;
       dl_last_acked <= 16'd0;
       pkt_release   <= 1'b1;
       resp_kind     <= RS_ACK;
       resp_seq      <= 16'd0;
       after_tx      <= AT_IDLE;
       state         <= S_BUILD;
      end
      RK_UPLOAD: begin
       if (!name_ok) begin
        resp_kind <= RS_ERROR; resp_err <= E_NOFILE; resp_seq <= 16'd0;
        pkt_release <= 1'b1; state <= S_BUILD;
       end else begin
        ul_active    <= 1'b1;
        ul_seq       <= 16'd1;
        ul_total     <= (file_len_r >> 9) + 16'd1;
        retries_used <= 4'd0;
        pkt_release  <= 1'b1;
        resp_kind    <= RS_ACK;
        resp_seq     <= 16'd0;
        after_tx     <= AT_UPLOAD;
        state        <= S_BUILD;
       end
      end
      RK_DATA: begin
       if (p_seq == dl_last_acked) begin
        cnt_dup_data <= cnt_dup_data + 32'd1;
        pkt_release  <= 1'b1;
        resp_kind    <= RS_ACK;
        resp_seq     <= dl_last_acked;
        state        <= S_BUILD;
       end else if (p_seq != dl_expected) begin
        pkt_release <= 1'b1;             // idempotent re-ACK, no write
        resp_kind   <= RS_ACK;
        resp_seq    <= dl_last_acked;
        state       <= S_BUILD;
       end else if ((((p_seq - 16'd1) << 9) + p_len) > 16'd1024) begin
        resp_kind <= RS_ERROR; resp_err <= E_NOSPACE; resp_seq <= p_seq;
        pkt_release <= 1'b1; state <= S_BUILD;
       end else begin
        wr_started <= 1'b0;
        wr_cnt     <= 11'd0;
        wr_off     <= (dl_expected - 16'd1) << 9;
        state      <= S_WRITE;
       end
      end
      RK_ACK: begin
       if (ul_active && (p_seq == ul_seq)) begin
        pkt_release <= 1'b1;
        ackwaiting <= 1'b0;
        if (ul_seq == ul_total) begin
         ul_active <= 1'b0;
         state     <= S_IDLE;
        end else begin
         ul_seq       <= ul_seq + 16'd1;
         send_started <= 1'b0;
         send_cnt     <= 11'd0;
         state        <= S_SEND;
        end
       end else begin
        pkt_release <= 1'b1;             // duplicate / stale ACK: ignore
        ackwaiting <= 1'b0;
        state       <= ul_active ? S_ACKWAIT : S_IDLE;
       end
      end
      default: begin
       resp_kind <= RS_ERROR; resp_err <= E_ILLEGAL; resp_seq <= 16'd0;
       pkt_release <= 1'b1; state <= S_BUILD;
      end
     endcase
    end

    // ------------------------------------------------------------
    S_WRITE: begin
     if (!wr_started) begin
      wr_started <= 1'b1;
      wr_cnt     <= 11'd0;
     end else if (wr_cnt < p_len[10:0]) begin
      file_mem[wr_addr] <= rx_buf[rx_daddr];
      wr_cnt <= wr_cnt + 11'd1;
     end else begin
      wr_started  <= 1'b0;
      pkt_release <= 1'b1;
      dl_last_acked <= dl_expected;
      cnt_blocks_written <= cnt_blocks_written + 32'd1;
      if (p_len < BLOCK_SIZE) begin
       dl_active      <= 1'b0;
       file_present_r <= 1'b1;
       file_len_r     <= ((dl_expected - 16'd1) << 9) + p_len;
      end
      resp_kind <= RS_ACK;
      resp_seq  <= dl_expected;
      after_tx  <= AT_IDLE;
      dl_expected <= dl_expected + 16'd1;
      state <= S_BUILD;
     end
    end

    // ------------------------------------------------------------
    S_BUILD: begin
     if (!bld_started) begin
      bld_started <= 1'b1;
      bld_cnt     <= 5'd0;
      tx_idx      <= 11'd0;
      case (resp_kind)
       RS_ACK: begin
        tx_buf[0] <= OP_ACK;
        tx_buf[1] <= resp_seq[15:8];
        tx_buf[2] <= resp_seq[7:0];
        tx_len_r  <= 11'd3;
       end
       RS_ERROR: begin
        tx_buf[0] <= OP_ERROR;
        tx_buf[1] <= {3'd0, resp_err};
        tx_buf[2] <= resp_seq[15:8];
        tx_buf[3] <= resp_seq[7:0];
        tx_len_r  <= 11'd4;
        cnt_errors <= cnt_errors + 32'd1;
        last_error_r <= resp_err;
       end
       default: begin
        tx_buf[0] <= OP_FIND_RESP;
        tx_buf[1] <= 8'd0;
        tx_buf[2] <= {7'd0, file_present_r};
        tx_buf[3] <= {3'd0, file_name_len};
        tx_len_r  <= 11'd4 + {6'd0, file_name_len};
       end
      endcase
     end else if ((resp_kind == RS_FIND) && (bld_cnt < file_name_len)) begin
      tx_buf[tx_naddr] <= file_name[bld_cnt[3:0]];
      bld_cnt <= bld_cnt + 5'd1;
     end else begin
      bld_started <= 1'b0;
      state       <= S_TX;
     end
    end

    // ------------------------------------------------------------
    S_TX: begin
     if (m_ready) begin
      if (tx_idx + 11'd1 == tx_len_r) begin
       tx_idx    <= 11'd0;
       cnt_tx_pkts <= cnt_tx_pkts + 32'd1;
       case (after_tx)
        AT_ACKWAIT: begin
         timer      <= 32'd0;
         ackwaiting <= 1'b1;
         state      <= S_ACKWAIT;
        end
        AT_UPLOAD: begin
         send_started <= 1'b0;
         send_cnt     <= 11'd0;
         state        <= S_SEND;
        end
        default: state <= S_IDLE;
       endcase
      end else begin
       tx_idx <= tx_idx + 11'd1;
      end
     end
    end

    // ------------------------------------------------------------
    // build an upload DATA block for ul_seq
    S_SEND: begin
     if (!send_started) begin
      send_started <= 1'b1;
      send_cnt     <= 11'd0;
      retries_used <= 4'd0;
      send_off     <= (ul_seq - 16'd1) << 9;
      if (((ul_seq - 16'd1) << 9) >= file_len_r)
       send_blen <= 16'd0;
      else if ((file_len_r - ((ul_seq - 16'd1) << 9)) > 16'd512)
       send_blen <= 16'd512;
      else
       send_blen <= file_len_r - ((ul_seq - 16'd1) << 9);
     end else if (send_cnt == 11'd0) begin
      tx_buf[0] <= OP_DATA;
      tx_buf[1] <= ul_seq[15:8];
      tx_buf[2] <= ul_seq[7:0];
      tx_buf[3] <= send_blen[15:8];
      tx_buf[4] <= send_blen[7:0];
      tx_len_r  <= 11'd5 + send_blen[10:0];
      send_cnt  <= 11'd1;
     end else if (send_cnt <= send_blen[10:0]) begin
      tx_buf[tx_daddr] <= file_mem[rd_addr];
      send_cnt <= send_cnt + 11'd1;
     end else begin
      send_started <= 1'b0;
      tx_idx       <= 11'd0;
      cnt_blocks_sent <= cnt_blocks_sent + 32'd1;
      after_tx     <= AT_ACKWAIT;
      state        <= S_TX;
     end
    end

    // ------------------------------------------------------------
    S_ACKWAIT: begin
     if (packet_ready) begin
      state <= S_HDR;
     end else begin
      timer <= timer + 32'd1;
      if ((timer + 32'd1) >= timeout_eff) begin
       cnt_timeouts <= cnt_timeouts + 32'd1;
       if (retries_used < retries_eff) begin
        retries_used <= retries_used + 4'd1;
        cnt_retransmits <= cnt_retransmits + 32'd1;
        tx_idx <= 11'd0;
        state  <= S_TX;                 // resend the same DATA block
       end else begin
        cnt_retry_aborts <= cnt_retry_aborts + 32'd1;
        ul_active    <= 1'b0;
        ackwaiting   <= 1'b0;
        resp_kind    <= RS_ERROR;
        resp_err     <= E_ABORT;
        resp_seq     <= ul_seq;
        after_tx     <= AT_IDLE;
        state        <= S_BUILD;
       end
      end
     end
    end

    default: state <= S_IDLE;
   endcase
  end
 end

 // ------------------------------------------------------------------
 // status read port (combinational)
 // ------------------------------------------------------------------
 always @* begin
  case (st_addr)
   6'h00: st_rdata = {23'd0, busy, dl_active, ul_active, file_present_r, 1'b0, state};
   6'h01: st_rdata = {16'd0, file_len_r};
   6'h02: st_rdata = cnt_rx_pkts;
   6'h03: st_rdata = cnt_tx_pkts;
   6'h04: st_rdata = cnt_find;
   6'h05: st_rdata = cnt_download;
   6'h06: st_rdata = cnt_upload;
   6'h07: st_rdata = cnt_blocks_written;
   6'h08: st_rdata = cnt_blocks_sent;
   6'h09: st_rdata = cnt_retransmits;
   6'h0A: st_rdata = cnt_dup_data;
   6'h0B: st_rdata = cnt_timeouts;
   6'h0C: st_rdata = cnt_retry_aborts;
   6'h0D: st_rdata = cnt_errors;
   6'h0E: st_rdata = {27'd0, last_error_r};
   6'h0F: st_rdata = 32'h0001_0000;
   default: st_rdata = 32'd0;
  endcase
 end
endmodule
