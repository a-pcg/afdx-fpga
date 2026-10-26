#include "Vafdx_baseline_core.h"
#include "verilated.h"
#include <vector>
#include <fstream>
#include <sstream>
#include <iostream>
#include <stdexcept>
#include <random>
using Bytes=std::vector<unsigned char>;
static Vafdx_baseline_core d;
static std::mt19937 rng(20261017);
static unsigned long long cycles=0;
static unsigned checks=0,tx_cases=0,rx_cases=0,bad_cases=0;
static void require(bool ok,const std::string& why){++checks;if(!ok)throw std::runtime_error(why);}
static void tick(){d.clk=0;d.eval();d.clk=1;d.eval();++cycles;}
static void reset(){d.reset=1;d.app_tx_msg_valid=0;d.app_tx_valid=0;d.mac_tx_ready=0;d.mac_rx_valid=0;d.app_rx_ready=0;tick();tick();d.reset=0;tick();}
static Bytes decode(const std::string& s){Bytes b;for(size_t i=0;i<s.size();i+=2)b.push_back((unsigned char)std::stoul(s.substr(i,2),nullptr,16));return b;}
static void send(const Bytes& payload,const Bytes& expected){
 d.app_tx_len=(unsigned short)payload.size();d.app_tx_msg_valid=1;
 require(d.app_tx_msg_ready,"TX not idle");tick();d.app_tx_msg_valid=0;
 for(size_t i=0;i<payload.size();++i){
  if(rng()%3==0){d.app_tx_valid=0;tick();}
  d.app_tx_valid=1;d.app_tx_data=payload[i];d.app_tx_last=(i+1==payload.size());d.eval();
  require(d.app_tx_ready,"TX load lost ready");tick();
 }
 d.app_tx_valid=0;Bytes got;bool stalled=false;unsigned char held=0;bool hs=false,he=false;
 for(unsigned t=0;t<30000;++t){
  d.mac_tx_ready=((t%8)==0 && rng()%4!=0);d.clk=0;d.eval();
  if(stalled){require(d.mac_tx_valid && d.mac_tx_data==held && d.mac_tx_sop==hs && d.mac_tx_eop==he,"TX changed during backpressure");}
  if(d.mac_tx_valid && d.mac_tx_ready){
   require(bool(d.mac_tx_sop)==got.empty(),"TX SOP mismatch");
   require(bool(d.mac_tx_eop)==(got.size()+1==expected.size()),"TX EOP mismatch");got.push_back(d.mac_tx_data);
  }
  stalled=d.mac_tx_valid&&!d.mac_tx_ready;held=d.mac_tx_data;hs=d.mac_tx_sop;he=d.mac_tx_eop;
  tick();if(d.tx_done)break;
  if(t==29999)require(false,"TX timeout");
 }
 require(got==expected,"TX frame differs from independent golden vector at case "+std::to_string(tx_cases));
 d.mac_tx_ready=0;++tx_cases;
}
static void receive(const Bytes& frame,const Bytes& payload,bool bad,int mac_error=0){
 d.app_rx_ready=0;require(d.mac_rx_ready,"RX not idle");
 for(size_t i=0;i<frame.size();++i){
  // MAC client driver: byte enable every 8 clocks (100 Mbps / 100 MHz).
  d.mac_rx_valid=0;for(int k=0;k<7;++k)tick();
  d.mac_rx_valid=1;d.mac_rx_data=frame[i];d.mac_rx_sop=(mac_error!=2 && i==0)||(mac_error==3 && i==5);d.mac_rx_eop=(i+1==frame.size());
  d.mac_rx_error=(mac_error==1 && i==frame.size()/2);d.clk=0;d.eval();
  require(d.mac_rx_ready,"RX refused a byte of accepted frame");tick();
 }
 d.mac_rx_valid=0;d.mac_rx_error=0;Bytes got;bool drop=false,done=false,stalled=false;unsigned char held=0;bool last=false;
 for(unsigned t=0;t<10000;++t){
  d.app_rx_ready=(rng()%4!=0);d.clk=0;d.eval();
  if(d.rx_drop)drop=true;
  if(stalled)require(d.app_rx_valid&&d.app_rx_data==held&&bool(d.app_rx_last)==last,"RX changed during application backpressure");
  if(d.app_rx_valid){
   require(!bad,"Corrupt/unsupported frame was delivered");
   require(d.app_rx_len==payload.size(),"RX length mismatch");
   require(d.app_rx_sn==frame.back(),"RX SN mismatch");
   if(d.app_rx_ready){got.push_back(d.app_rx_data);if(d.app_rx_last)done=true;}
  }
  stalled=d.app_rx_valid&&!d.app_rx_ready;held=d.app_rx_data;last=d.app_rx_last;
  tick();if(drop||done)break;
 }
 if(bad){require(drop && got.empty(),"RX failed to reject bad frame");++bad_cases;}
 else{require(done && !drop && got==payload,"RX timeout/data mismatch");++rx_cases;}
 d.app_rx_ready=0;tick();require(d.mac_rx_ready,"RX failed to recover after frame");
}
static void reject_len(unsigned n){d.app_tx_len=n;d.app_tx_msg_valid=1;tick();require(d.tx_reject,"Invalid length not rejected");d.app_tx_msg_valid=0;tick();require(d.app_tx_msg_ready,"TX not recovered");}
int main(int argc,char**argv){
 try{
 Verilated::commandArgs(argc,argv);unsigned seed=argc>1?unsigned(std::stoul(argv[1])):20261017;rng.seed(seed);reset();std::ifstream f("vectors/tx_frames.txt");require(f.good(),"missing TX vectors");
 std::string a,b;Bytes first_payload,first_frame;
 while(f>>a>>b){auto pl=decode(a),fr=decode(b);if(tx_cases==0){first_payload=pl;first_frame=fr;}send(pl,fr);receive(fr,pl,false);}
 std::ifstream gf("vectors/rx_good.txt");require(gf.good(),"missing positive RX vectors");while(gf>>a>>b)receive(decode(b),decode(a),false);
 std::ifstream bf("vectors/rx_bad.txt");require(bf.good(),"missing RX vectors");std::string label;int err;
 while(bf>>label>>err>>b){receive(decode(b),{},true,err);}
 reject_len(0);reject_len(1472);reject_len(65535);
 // Premature and missing last must abort and never transmit a partial packet.
 for(bool early:{false,true}){
 d.app_tx_len=early?3:1;d.app_tx_msg_valid=1;tick();d.app_tx_msg_valid=0;
 d.app_tx_valid=1;d.app_tx_data=0x55;d.app_tx_last=early;tick();require(d.tx_reject,"bad last accepted");d.app_tx_valid=0;tick();require(!d.mac_tx_valid&&d.app_tx_msg_ready,"bad last left TX stuck");}
 // Reset interrupts partial application input, partial MAC frame and stalled TX.
 d.app_tx_len=2;d.app_tx_msg_valid=1;tick();d.app_tx_msg_valid=0;d.app_tx_valid=1;d.app_tx_last=0;tick();reset();
 d.mac_rx_valid=1;d.mac_rx_sop=1;d.mac_rx_eop=0;d.mac_rx_data=3;tick();reset();
 send(first_payload,first_frame);receive(first_frame,first_payload,false);
 // Hold a completely prepared frame at MAC boundary, then reset it.
 d.app_tx_len=1;d.app_tx_msg_valid=1;tick();d.app_tx_msg_valid=0;d.app_tx_valid=1;d.app_tx_last=1;tick();d.app_tx_valid=0;
 require(d.mac_tx_valid,"TX reset test did not reach SEND");reset();require(!d.mac_tx_valid,"old TX frame leaked through reset");
 send(first_payload,first_frame);
 require(tx_cases>=260 && rx_cases>=260 && bad_cases>=7,"Vector corpus was unexpectedly empty or incomplete");
 d.final();std::ofstream out("reports/simulation.json");out<<"{\"status\":\"PASS\",\"seed\":"<<seed<<",\"checks\":"<<checks<<",\"tx_frames\":"<<tx_cases<<",\"rx_frames\":"<<rx_cases<<",\"bad_frames\":"<<bad_cases<<",\"cycles\":"<<cycles<<"}\n";
 std::cout<<"PASS TX="<<tx_cases<<" RX="<<rx_cases<<" rejected="<<bad_cases<<" checks="<<checks<<" cycles="<<cycles<<std::endl;return 0;
 }catch(const std::exception&e){std::cerr<<"FAIL: "<<e.what()<<std::endl;return 1;}
}
