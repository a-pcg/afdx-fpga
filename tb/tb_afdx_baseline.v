`timescale 1ns/1ps
module tb_afdx_baseline;
reg clk,reset;
reg [15:0] app_tx_len;
reg app_tx_msg_valid;
wire app_tx_msg_ready;
reg [7:0] app_tx_data;
reg app_tx_valid,app_tx_last;
wire app_tx_ready;
wire [7:0] mac_tx_data;
wire mac_tx_valid,mac_tx_sop,mac_tx_eop;
reg mac_tx_ready;
wire tx_reject,tx_done;
reg [7:0] mac_rx_data;
reg mac_rx_valid,mac_rx_sop,mac_rx_eop,mac_rx_error;
wire mac_rx_ready;
wire [7:0] app_rx_data;
wire app_rx_valid,app_rx_last;
reg app_rx_ready;
wire [15:0] app_rx_len;
wire [7:0] app_rx_sn;
wire rx_drop;
wire [3:0] rx_drop_reason;
wire rx_accepted;
afdx_baseline_core dut(.clk(clk),.reset(reset),.app_tx_len(app_tx_len),.app_tx_msg_valid(app_tx_msg_valid),.app_tx_msg_ready(app_tx_msg_ready),.app_tx_data(app_tx_data),.app_tx_valid(app_tx_valid),.app_tx_last(app_tx_last),.app_tx_ready(app_tx_ready),.mac_tx_data(mac_tx_data),.mac_tx_valid(mac_tx_valid),.mac_tx_sop(mac_tx_sop),.mac_tx_eop(mac_tx_eop),.mac_tx_ready(mac_tx_ready),.tx_reject(tx_reject),.tx_done(tx_done),.mac_rx_data(mac_rx_data),.mac_rx_valid(mac_rx_valid),.mac_rx_sop(mac_rx_sop),.mac_rx_eop(mac_rx_eop),.mac_rx_error(mac_rx_error),.mac_rx_ready(mac_rx_ready),.app_rx_data(app_rx_data),.app_rx_valid(app_rx_valid),.app_rx_last(app_rx_last),.app_rx_ready(app_rx_ready),.app_rx_len(app_rx_len),.app_rx_sn(app_rx_sn),.rx_drop(rx_drop),.rx_drop_reason(rx_drop_reason),.rx_accepted(rx_accepted));

initial clk=0;always #5 clk=~clk;
reg [7:0] payload[0:1470],golden[0:1513],captured[0:1513];
integer failures=0,idx,len,flen,timeout_count;reg [31:0] cycle=0;
always @(posedge clk)cycle<=cycle+1;
// Explicit failure plus stop: scripts/modelsim.do exits nonzero on break.
task check;input condition;input [511:0] description;
begin if(!condition)begin failures=failures+1;$display("FAIL %s",description);$stop;end end endtask
task one_packet;input integer n;integer i;
begin
 case(n)
 1:begin $readmemh("vectors/smoke_payload_1.hex",payload,0,0);$readmemh("vectors/smoke_frame_1.hex",golden,0,59);end
 17:begin $readmemh("vectors/smoke_payload_17.hex",payload,0,16);$readmemh("vectors/smoke_frame_17.hex",golden,0,59);end
 1471:begin $readmemh("vectors/smoke_payload_1471.hex",payload,0,1470);$readmemh("vectors/smoke_frame_1471.hex",golden,0,1513);end
 endcase
 flen=(n<17)?60:n+43;
 @(negedge clk);app_tx_len=n;app_tx_msg_valid=1;
 @(negedge clk);app_tx_msg_valid=0;
 for(i=0;i<n;i=i+1)begin
 app_tx_valid=1;app_tx_data=payload[i];app_tx_last=(i==n-1);
 @(posedge clk);check(app_tx_ready,"TX load ready");@(negedge clk);
 end
 app_tx_valid=0;idx=0;timeout_count=0;
 while(idx<flen && timeout_count<30000)begin
 mac_tx_ready=(cycle%8==0);@(posedge clk);
 if(mac_tx_valid && mac_tx_ready)begin
 check(mac_tx_data===golden[idx],"TX golden bytes");check(mac_tx_sop===(idx==0),"TX SOP");check(mac_tx_eop===(idx==flen-1),"TX EOP");
 captured[idx]=mac_tx_data;idx=idx+1;end
 @(negedge clk);timeout_count=timeout_count+1;
 end
 check(idx==flen,"TX bounded completion");mac_tx_ready=0;
 for(i=0;i<flen;i=i+1)begin
 mac_rx_data=captured[i];mac_rx_valid=1;mac_rx_sop=(i==0);mac_rx_eop=(i==flen-1);
 @(posedge clk);check(mac_rx_ready,"MAC accepted frame byte");@(negedge clk);mac_rx_valid=0;repeat(7)@(negedge clk);
 end
 idx=0;timeout_count=0;
 while(idx<n && timeout_count<10000)begin
 app_rx_ready=(cycle%4!=0);@(posedge clk);check(!rx_drop,"No unexpected RX rejection");
 if(app_rx_valid && app_rx_ready)begin check(app_rx_data===payload[idx],"RX payload");check(app_rx_last===(idx==n-1),"RX last");check(app_rx_len==n,"RX length");idx=idx+1;end
 @(negedge clk);timeout_count=timeout_count+1;
 end
 check(idx==n,"RX bounded completion");app_rx_ready=0;repeat(3)@(negedge clk);
end endtask
initial begin
 reset=1;app_tx_len=0;app_tx_msg_valid=0;app_tx_data=0;app_tx_valid=0;app_tx_last=0;mac_tx_ready=0;
 mac_rx_data=0;mac_rx_valid=0;mac_rx_sop=0;mac_rx_eop=0;mac_rx_error=0;app_rx_ready=0;
 repeat(4)@(negedge clk);reset=0;repeat(2)@(negedge clk);
 one_packet(1);one_packet(17);one_packet(1471);
 $display("PASS tb_afdx_baseline failures=%0d",failures);$finish;
end
initial begin #2000000;$display("FAIL simulation timeout");$stop;end
endmodule
