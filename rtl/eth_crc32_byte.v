`timescale 1ns/1ps
// Reflected IEEE CRC32 recurrence, initial state ffffffff, final complement.
module eth_crc32_byte(input wire [31:0] crc,input wire [7:0] data,output reg [31:0] next_crc);
 integer i;reg [31:0] work;
 always @* begin
 work=crc^{24'd0,data};
 for(i=0;i<8;i=i+1)begin
 if(work[0])work=(work>>1)^32'hedb88320;
 else work=work>>1;
 end
 next_crc=work;
 end
endmodule
