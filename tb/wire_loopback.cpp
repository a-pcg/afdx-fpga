#include "Vafdx_wire_loopback.h"
#include "verilated.h"
#include <fstream>
#include <iostream>
#include <random>
#include <stdexcept>
#include <string>
#include <vector>
using Bytes=std::vector<unsigned char>;
static Vafdx_wire_loopback d;
static Bytes payload,wire;
static unsigned wire_pos=0,app_pos=0;
static bool corrupt=false,wire_error=false,app_done=false,rx_dropped=false,held=false;
static unsigned char held_data=0;
static bool held_last=false;
static unsigned long long cycles=0,checks=0,last_byte=0;
static unsigned good_frames=0,bad_frames=0;
static std::mt19937 rng(20261018);
static void need(bool ok,const std::string& why){++checks;if(!ok)throw std::runtime_error(why);}
static Bytes decode(const std::string& s){Bytes b;for(size_t i=0;i<s.size();i+=2)b.push_back((unsigned char)std::stoul(s.substr(i,2),nullptr,16));return b;}
static void tick(){
 d.app_rx_ready=rng()%4!=0;d.inject_mask=0;d.inject_error=0;d.clk=0;d.eval();
 const bool mutate=(corrupt||wire_error)&&d.line_byte_ce&&d.line_valid&&wire_pos==50;
 if(mutate){d.inject_mask=corrupt?1:0;d.inject_error=wire_error;d.eval();}
 if(!d.reset){
  need(!d.mac_tx_drop&&!d.udp_rx_drop,"legal source packet rejected before/after MAC");
  if(d.line_byte_ce&&d.line_valid){
   need(wire_pos<wire.size(),"extra physical octet");
   if(wire_pos)need(cycles-last_byte==8,"physical link byte spacing changed");
   unsigned char expected=wire[wire_pos]^(mutate&&corrupt?1:0);
   need(d.line_data==expected,"physical frame differs from independent golden bytes");
   need(bool(d.line_sop)==(wire_pos==0),"physical SOP mismatch");
   need(bool(d.line_eop)==(wire_pos+1==wire.size()),"physical EOP mismatch");
   last_byte=cycles;++wire_pos;
  }
  if(held)need(d.app_rx_valid&&d.app_rx_data==held_data&&bool(d.app_rx_last)==held_last,"application output changed during backpressure");
  if(d.app_rx_valid){
   need(!corrupt&&!wire_error,"corrupt physical frame leaked to application");
   need(wire_pos==wire.size(),"application data published before full FCS reception");
   need(d.app_rx_len==payload.size()&&d.app_rx_sn==wire[wire.size()-5],"application length/SN mismatch");
   need(app_pos<payload.size()&&d.app_rx_data==payload[app_pos],"end-to-end payload differs");
   need(bool(d.app_rx_last)==(app_pos+1==payload.size()),"application last mismatch");
   if(d.app_rx_ready){++app_pos;if(d.app_rx_last)app_done=true;}
  }
  held=d.app_rx_valid&&!d.app_rx_ready;held_data=d.app_rx_data;held_last=d.app_rx_last;
 }
 d.clk=1;d.eval();++cycles;if(!d.reset&&d.mac_rx_drop)rx_dropped=true;
}
static void reset(){d.reset=1;d.app_tx_msg_valid=0;d.app_tx_valid=0;tick();tick();d.reset=0;held=false;wire.clear();payload.clear();wire_pos=app_pos=0;tick();}
static void exercise(const Bytes& p,const Bytes& w,bool bad_crc=false,bool error=false){
 payload=p;wire=w;wire_pos=app_pos=0;corrupt=bad_crc;wire_error=error;app_done=rx_dropped=false;
 unsigned t=0;while(!d.app_tx_msg_ready){tick();need(++t<10000,"application TX did not recover");}
 d.app_tx_len=p.size();d.app_tx_msg_valid=1;tick();d.app_tx_msg_valid=0;
 for(size_t i=0;i<p.size();++i){
  if(rng()%3==0){d.app_tx_valid=0;tick();}
  d.app_tx_valid=1;d.app_tx_data=p[i];d.app_tx_last=(i+1==p.size());d.eval();
  need(d.app_tx_ready,"application input unexpectedly stalled");tick();
 }
 d.app_tx_valid=0;t=0;
 while(!(bad_crc||error?rx_dropped:app_done)){tick();need(++t<40000,"wire loopback timeout");}
 need(wire_pos==wire.size(),"wire frame did not finish");
 if(bad_crc||error){need(!app_done&&app_pos==0,"bad wire frame partly delivered");++bad_frames;}
 else{need(app_pos==p.size()&&!rx_dropped,"legal wire frame not delivered");++good_frames;}
 for(int i=0;i<120;++i)tick();
}
int main(int argc,char**argv){try{
 Verilated::commandArgs(argc,argv);reset();std::ifstream f("vectors/wire_loopback/cases.txt");need(f.good(),"missing loopback golden vectors");
 std::string p,w;while(f>>p>>w)exercise(decode(p),decode(w));
 need(good_frames>=250,"loopback corpus incomplete");
 // Reset before separate error runs so each source SN restarts at zero.
 f.clear();f.seekg(0);f>>p>>w;Bytes first_p=decode(p),first_w=decode(w);
 reset();exercise(first_p,first_w,true,false);
 reset();exercise(first_p,first_w,false,true);
 reset();exercise(first_p,first_w,false,false);
 d.final();std::ofstream out("reports/wire_loopback/result.json");out<<"{\"status\":\"PASS\",\"good_frames\":"<<good_frames<<",\"bad_frames\":"<<bad_frames<<",\"checks\":"<<checks<<",\"cycles\":"<<cycles<<"}\n";
 std::cout<<"PASS good="<<good_frames<<" bad="<<bad_frames<<" checks="<<checks<<"\n";return 0;
}catch(const std::exception&e){std::cerr<<"FAIL "<<e.what()<<"\n";return 1;}}
