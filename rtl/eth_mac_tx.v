`timescale 1ns/1ps
// Four store-and-forward slots (T11: 2 -> 4, so a burst of short frames can be
// absorbed while a long frame is being loaded from the pool). Client: DA..last
// payload/SN, 60..1514 bytes. Four 2048-byte regions in one simple-dual-port RAM
// (one write port, one synchronous read port).
// Line: 7*55,D5,client,FCS (least-significant CRC byte first),12 idle bytes.
// byte_ce is a clock enable, NEVER a ready input; line cannot stall mid-frame.
module eth_mac_tx(
 input wire clk,reset,byte_ce,input wire [63:0] time_now,
 input wire [7:0] s_data,input wire s_valid,s_sop,s_eop,output wire s_ready,
 output reg [7:0] line_data,output wire line_valid,
 output reg sfd_pulse,output reg [63:0] sfd_time,output reg done,output reg reject,
 output wire [2:0] slots_used);
 localparam WAIT=3'd0,PREAMBLE=3'd1,DATA=3'd2,FCS=3'd3,GAP=3'd4;
 reg [2:0] state;
 reg [7:0] mem[0:8191];reg [7:0] ram_q;
 reg [3:0] occupied;
 reg [1:0] write_slot,read_slot;
 reg capture_bad;
 reg [10:0] write_index,read_index,active_len;
 reg [10:0] lengths[0:3];
 reg [10:0] read_address;
 reg [3:0] phase;
 reg [31:0] crc,fcs_word;
 wire [31:0] crc_next;
 eth_crc32_byte crc_step(.crc(crc),.data(ram_q),.next_crc(crc_next));
 assign s_ready=!occupied[write_slot]&&!reset;
 assign slots_used={2'b0,occupied[0]}+{2'b0,occupied[1]}+{2'b0,occupied[2]}+{2'b0,occupied[3]};
 assign line_valid=(state==PREAMBLE||state==DATA||state==FCS)&&!reset;
 always @* begin
 read_address=0;
 if(state==DATA)begin
 read_address=read_index;
 if(byte_ce&&read_index<active_len-11'd1)read_address=read_index+11'd1;
 end
 line_data=0;
 case(state)
 PREAMBLE:line_data=(phase==4'd7)?8'hd5:8'h55;
 DATA:line_data=ram_q;
 FCS:case(phase)
 0:line_data=fcs_word[7:0];1:line_data=fcs_word[15:8];
 2:line_data=fcs_word[23:16];default:line_data=fcs_word[31:24];endcase
 default:line_data=0;
 endcase
 end
 always @(posedge clk)begin
 if(s_valid&&s_ready&&write_index<11'd1514)mem[{write_slot,write_index}]<=s_data;
 ram_q<=mem[{read_slot,read_address}];
 end
 always @(posedge clk)begin
 if(reset)begin state<=WAIT;occupied<=0;write_slot<=0;read_slot<=0;capture_bad<=0;
 write_index<=0;read_index<=0;active_len<=0;phase<=0;crc<=32'hffffffff;fcs_word<=0;
 sfd_pulse<=0;sfd_time<=0;done<=0;reject<=0;end
 else begin
 sfd_pulse<=0;done<=0;reject<=0;
 if(s_valid&&s_ready)begin
 if(s_eop)begin
 if(capture_bad||write_index<11'd59||write_index>=11'd1514||s_sop!=(write_index==0))reject<=1;
 else begin occupied[write_slot]<=1;lengths[write_slot]<=write_index+11'd1;write_slot<=write_slot+2'd1;end
 write_index<=0;capture_bad<=0;
 end else begin
 if(s_sop!=(write_index==0)||write_index>=11'd1514)capture_bad<=1;
 if(write_index<11'd1514)write_index<=write_index+11'd1;
 end
 end
 case(state)
 WAIT:if(occupied[read_slot])begin active_len<=lengths[read_slot];read_index<=0;phase<=0;crc<=32'hffffffff;state<=PREAMBLE;end
 PREAMBLE:if(byte_ce)begin
 if(phase==4'd7)begin sfd_pulse<=1;sfd_time<=time_now;state<=DATA;end
 else phase<=phase+4'd1;
 end
 DATA:if(byte_ce)begin
 crc<=crc_next;
 if(read_index==active_len-11'd1)begin
 fcs_word<=~crc_next;phase<=0;state<=FCS;occupied[read_slot]<=0;
 end else read_index<=read_index+11'd1;
 end
 FCS:if(byte_ce)begin
 if(phase==4'd3)begin phase<=0;state<=GAP;read_slot<=read_slot+2'd1;done<=1;end
 else phase<=phase+4'd1;
 end
 GAP:if(byte_ce)begin
 if(phase==4'd11)begin
 phase<=0;
 // Starting a preamble now makes the next enabled byte follow exactly 12 idles.
 if(occupied[read_slot])begin active_len<=lengths[read_slot];read_index<=0;crc<=32'hffffffff;state<=PREAMBLE;end
 else state<=WAIT;
 end else phase<=phase+4'd1;
 end
 default:state<=WAIT;
 endcase
 end end
endmodule
