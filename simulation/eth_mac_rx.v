`timescale 1ns/1ps
// Line ingress has no ready: CRC-checked frames enter a two-slot FIFO or drop.
// FCS is removed before client delivery. CRC state includes received FCS.
module eth_mac_rx(
 input wire clk,reset,byte_ce,input wire [63:0] time_now,
 input wire [7:0] line_data,input wire line_valid,line_error,
 output wire [7:0] m_data,output wire m_valid,m_sop,m_eop,input wire m_ready,
 output wire [63:0] m_sfd_time,output reg sfd_pulse,output reg [63:0] sfd_time,
 output reg drop,output reg [1:0] drop_reason,output reg [31:0] good_frames,bad_fcs,full_drops,format_drops,
 output wire [1:0] slots_used);
 localparam IDLE=2'd0,PREAMBLE=2'd1,DATA=2'd2,DRAIN=2'd3;
 reg [1:0] state;
 reg [7:0] mem[0:4095];reg [7:0] ram_q;
 reg [1:0] occupied;reg write_slot,read_slot,room,bad,playing;
 reg [3:0] pre_count,idle_count;
 reg [10:0] raw_count,out_index,active_len;
 reg [10:0] lengths[0:1];reg [63:0] timestamps[0:1];
 reg [10:0] read_address;
 reg [31:0] crc;wire [31:0] crc_next;
 eth_crc32_byte crc_step(.crc(crc),.data(line_data),.next_crc(crc_next));
 assign m_valid=playing&&!reset;assign m_data=ram_q;
 assign m_sop=(out_index==0);assign m_eop=(out_index==active_len-11'd1);
 assign m_sfd_time=timestamps[read_slot];
 assign slots_used={1'b0,occupied[0]}+{1'b0,occupied[1]};
 always @* begin
 read_address=0;
 if(playing)begin read_address=out_index;if(m_ready&&out_index<active_len-11'd1)read_address=out_index+11'd1;end
 end
 always @(posedge clk)begin
 if(!reset&&byte_ce&&state==DATA&&line_valid&&room&&raw_count<11'd1518)
 mem[{write_slot,raw_count}]<=line_data;
 ram_q<=mem[{read_slot,read_address}];
 end
 always @(posedge clk)begin
 if(reset)begin state<=IDLE;occupied<=0;write_slot<=0;read_slot<=0;room<=0;bad<=0;playing<=0;
 pre_count<=0;idle_count<=4'd12;raw_count<=0;out_index<=0;active_len<=0;crc<=32'hffffffff;
 sfd_pulse<=0;sfd_time<=0;drop<=0;drop_reason<=0;good_frames<=0;bad_fcs<=0;full_drops<=0;format_drops<=0;end
 else begin
 sfd_pulse<=0;drop<=0;
 if(!playing)begin
 if(occupied[read_slot])begin playing<=1;out_index<=0;active_len<=lengths[read_slot];end
 end else if(m_ready)begin
 if(m_eop)begin occupied[read_slot]<=0;read_slot<=!read_slot;playing<=0;end
 else out_index<=out_index+11'd1;
 end
 if(byte_ce)begin
 case(state)
 IDLE:if(line_valid)begin
 idle_count<=0;bad<=line_error;
 if(line_data==8'h55&&idle_count>=4'd12)begin pre_count<=1;state<=PREAMBLE;end
 else state<=DRAIN;
 end else if(idle_count<4'd12)idle_count<=idle_count+4'd1;
 PREAMBLE:if(!line_valid)begin state<=IDLE;idle_count<=1;drop<=1;drop_reason<=2;format_drops<=format_drops+32'd1;end
 else begin
 if(line_error)bad<=1;
 if(pre_count==4'd7)begin
 if(line_data==8'hd5)begin
 sfd_pulse<=1;sfd_time<=time_now;
 if(!occupied[write_slot])timestamps[write_slot]<=time_now;
 room<=!occupied[write_slot];raw_count<=0;crc<=32'hffffffff;state<=DATA;
 end else state<=DRAIN;
 end else if(line_data==8'h55)pre_count<=pre_count+4'd1;
 else state<=DRAIN;
 end
 DATA:if(line_valid)begin
 crc<=crc_next;
 if(line_error||raw_count>=11'd1518)bad<=1;
 if(raw_count<11'd1519)raw_count<=raw_count+11'd1;
 end else begin
 state<=IDLE;idle_count<=1;
 if(bad||raw_count<11'd64||raw_count>11'd1518)begin
 drop<=1;drop_reason<=2;format_drops<=format_drops+32'd1;
 end else if(crc!=32'hdebb20e3)begin drop<=1;drop_reason<=1;bad_fcs<=bad_fcs+32'd1;end
 else if(!room)begin drop<=1;drop_reason<=3;full_drops<=full_drops+32'd1;end
 else begin
 lengths[write_slot]<=raw_count-11'd4;occupied[write_slot]<=1;write_slot<=!write_slot;good_frames<=good_frames+32'd1;
 end
 end
 DRAIN:if(!line_valid)begin state<=IDLE;idle_count<=1;drop<=1;drop_reason<=2;format_drops<=format_drops+32'd1;end
 default:state<=IDLE;
 endcase
 end
 end end
endmodule
