#include "Vafdx_eth_rx_mac.h"
#include "verilated.h"
#include <cstdint>
#include <deque>
#include <fstream>
#include <iostream>
#include <random>
#include <stdexcept>
#include <string>
#include <vector>
using Bytes=std::vector<unsigned char>;
static Vafdx_eth_rx_mac d;
static std::deque<Bytes> queue;
static Bytes candidate;
static bool candidate_good=false,stalled=false;
static unsigned char held=0;
static unsigned held_len=0;
static bool held_sop=false,held_eop=false;
static unsigned out_index=0,accepted=0,drops=0,delivered=0,last_reason=0;
static std::uint64_t cycles=0,checks=0;
static int ready_mode=2;
static std::mt19937 rng(20261018);
static void need(bool ok,const std::string& why){++checks;if(!ok)throw std::runtime_error(why);}
static Bytes decode(const std::string& s){Bytes b;if(s=="-")return b;for(size_t i=0;i<s.size();i+=2)b.push_back((unsigned char)std::stoul(s.substr(i,2),nullptr,16));return b;}
static void tick(){
 d.m_ready=ready_mode==1||(ready_mode==2&&rng()%4!=0);d.clk=0;d.eval();
 if(!d.reset){
  if(stalled)need(d.m_valid&&d.m_data==held&&d.m_len==held_len&&bool(d.m_sop)==held_sop&&bool(d.m_eop)==held_eop,"RX MAC changed client data during backpressure");
  if(d.m_valid){
   need(!queue.empty(),"unverified/corrupt frame reached MAC client");
   need(d.m_len==queue.front().size(),"RX MAC included FCS or changed length");
   need(bool(d.m_sop)==(out_index==0),"RX MAC SOP mismatch");
   need(bool(d.m_eop)==(out_index+1==queue.front().size()),"RX MAC EOP mismatch");
   need(d.m_data==queue.front()[out_index],"RX MAC payload corruption/buffer overwrite");
   if(d.m_ready){if(d.m_eop){queue.pop_front();out_index=0;++delivered;}else ++out_index;}
  }
  stalled=d.m_valid&&!d.m_ready;held=d.m_data;held_len=d.m_len;held_sop=d.m_sop;held_eop=d.m_eop;
 }
 d.clk=1;d.eval();++cycles;
 if(!d.reset){
  if(d.accepted){need(candidate_good,"MAC accepted malformed wire frame");queue.push_back(candidate);++accepted;}
  if(d.drop){++drops;last_reason=d.drop_reason;}
 }
}
static void reset(){d.reset=1;d.line_valid=0;d.line_byte_ce=0;d.line_error=0;tick();tick();d.reset=0;queue.clear();stalled=false;out_index=0;tick();need(!d.m_valid,"old RX frame survived reset");}
static void wire_byte(unsigned char value,bool valid,bool error=false){
 d.line_data=value;d.line_valid=valid;d.line_error=error;
 d.line_byte_ce=0;for(int i=0;i<7;++i)tick();d.line_byte_ce=1;tick();d.line_byte_ce=0;
}
static void send(const Bytes& wire,const Bytes& body,bool good,int error_index=-1){
 candidate=body;candidate_good=good;
 for(size_t i=0;i<wire.size();++i)wire_byte(wire[i],true,int(i)==error_index);
 for(int i=0;i<12;++i)wire_byte(0,false);
}
static void drain(){ready_mode=1;for(unsigned i=0;!queue.empty()||d.m_valid;++i){need(i<10000,"RX MAC client drain timeout");tick();}stalled=false;}
int main(int argc,char**argv){try{
 Verilated::commandArgs(argc,argv);reset();
 std::ifstream f("vectors/mac_rx/cases.txt");need(f.good(),"RX MAC vectors missing");
 std::string label,wire_hex,body_hex;int good,error;unsigned good_cases=0,bad_cases=0;
 Bytes burst_wire[3],burst_body[3],min_wire,min_body,max_wire,max_body;
 while(f>>label>>good>>error>>wire_hex>>body_hex){
  Bytes wire=decode(wire_hex),body=decode(body_hex);unsigned before_a=accepted,before_d=drops;
  ready_mode=2;send(wire,body,good!=0,error);drain();
  if(good){need(accepted==before_a+1&&drops==before_d,"legal frame rejected with buffers available");++good_cases;}
  else{need(accepted==before_a&&drops==before_d+1,"bad wire frame not rejected exactly once");++bad_cases;}
  if(label=="minimum"){min_wire=wire;min_body=body;}
  if(label=="maximum"){max_wire=wire;max_body=body;}
  for(int i=0;i<3;++i)if(label=="hold"+std::to_string(i)){burst_wire[i]=wire;burst_body[i]=body;}
 }
 need(good_cases>=200&&bad_cases>=8,"incomplete RX MAC corpus");
 // The input has no ready: both held buffers force one entire frame drop.
 ready_mode=0;unsigned a=accepted,b=drops;
 for(int i=0;i<3;++i)send(burst_wire[i],burst_body[i],true);
 need(accepted==a+2&&drops==b+1&&last_reason==4,"two-buffer overflow policy incorrect");
 need(queue.size()==2,"overflow damaged retained frames");drain();
 // Non-stalled homogeneous full-line traffic at minimum and maximum size.
 ready_mode=1;a=accepted;b=drops;
 for(int i=0;i<100;++i)send(min_wire,min_body,true);
 for(int i=0;i<20;++i)send(max_wire,max_body,true);
 drain();need(accepted==a+120&&drops==b,"unexpected drop at sustained min/max line rate");
 // Reset pending delivery and partial physical input, then recover.
 ready_mode=0;send(min_wire,min_body,true);need(d.m_valid,"reset case not holding output");reset();
 for(size_t i=0;i<20;++i)wire_byte(max_wire[i],true);
 reset();ready_mode=1;send(min_wire,min_body,true);drain();
 d.final();std::ofstream out("reports/mac_rx/result.json");
 out<<"{\"status\":\"PASS\",\"good_cases\":"<<good_cases<<",\"bad_cases\":"<<bad_cases
    <<",\"accepted\":"<<accepted<<",\"delivered\":"<<delivered<<",\"drops\":"<<drops
    <<",\"checks\":"<<checks<<",\"cycles\":"<<cycles<<"}\n";
 std::cout<<"PASS accepted="<<accepted<<" delivered="<<delivered<<" drops="<<drops<<" checks="<<checks<<"\n";return 0;
}catch(const std::exception&e){std::cerr<<"FAIL "<<e.what()<<"\n";return 1;}}
