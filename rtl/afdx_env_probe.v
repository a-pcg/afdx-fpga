`timescale 1ns/1ps
// Non-empty M0 synthesis probe: clock, small FIFO, observable counter.
module afdx_env_probe(input wire clk,reset,input wire [7:0] data_in,
 input wire push,pop,output wire [7:0] data_out,output wire full,empty,output reg [31:0] cycles);
 reg [7:0] mem[0:15];reg [3:0] wp,rp;reg [4:0] count;
 wire take_pop=pop&&!empty;
 wire take_push=push&&(!full||take_pop);
 assign full=(count==5'd16);assign empty=(count==0);assign data_out=mem[rp];
 always @(posedge clk) begin
 if(reset) begin wp<=0;rp<=0;count<=0;cycles<=0;end
 else begin cycles<=cycles+32'd1;
 if(take_push) begin mem[wp]<=data_in;wp<=wp+4'd1;end
 if(take_pop) rp<=rp+4'd1;
 case({take_push,take_pop}) 2'b10:count<=count+5'd1;2'b01:count<=count-5'd1;default:count<=count;endcase
 end end
endmodule
