#include "Vafdx_eth_tx_mac.h"
#include "verilated.h"
#include <cstdint>
#include <fstream>
#include <iostream>
#include <random>
#include <stdexcept>
#include <string>
#include <vector>
using Bytes = std::vector<unsigned char>;
static Vafdx_eth_tx_mac d;
static Bytes expected;
static std::size_t observed=0;
static std::uint64_t cycles=0, checks=0, prior_byte=0, prior_end=0;
static unsigned frames=0;
static bool active=false;
static std::mt19937 rng(20261017);
static void need(bool ok,const std::string& why){++checks;if(!ok)throw std::runtime_error(why);}
static Bytes decode(const std::string& text){Bytes b;for(size_t i=0;i<text.size();i+=2)b.push_back((unsigned char)std::stoul(text.substr(i,2),nullptr,16));return b;}
static void tick(){
 d.clk=0;d.eval();
 if(!d.reset){
  if(active)need(d.line_valid,"MAC paused inside physical frame");
  if(d.line_byte_ce&&d.line_valid){
   need(observed<expected.size(),"unexpected physical frame or extra octet");
   if(observed==0){
    need(d.line_sop,"physical frame missing SOP");
    if(frames)need(cycles-prior_end>=104,"IFG shorter than twelve bytes");
    active=true;
   }else{
    need(!d.line_sop,"duplicate physical SOP");
    need(cycles-prior_byte==8,"physical byte cadence differs from 100 Mbps");
   }
   need(d.line_data==expected[observed],"preamble/body/FCS byte mismatch");
   need(bool(d.line_eop)==(observed+1==expected.size()),"physical EOP misplaced");
   prior_byte=cycles;++observed;
   if(d.line_eop){active=false;prior_end=cycles;++frames;}
  }
 }
 d.clk=1;d.eval();++cycles;
}
static void reset(){d.reset=1;d.s_valid=0;tick();tick();d.reset=0;active=false;expected.clear();observed=0;tick();need(d.s_ready&&!d.line_valid,"reset did not clear MAC");}
static void send(const Bytes& body,const Bytes& line){
 expected=line;observed=0;need(d.s_ready,"MAC not ready for frame");
 for(size_t i=0;i<body.size();++i){
  if(rng()%4==0){d.s_valid=0;tick();}
  d.s_valid=1;d.s_data=body[i];d.s_sop=(i==0);d.s_eop=(i+1==body.size());
  need(d.s_ready,"MAC stopped accepting client frame");tick();
 }
 d.s_valid=0;unsigned limit=0;
 while(!d.s_ready){tick();need(++limit<20000,"MAC frame/IFG timeout");}
 need(observed==expected.size(),"MAC output frame incomplete");
 need(!d.drop,"legal client frame rejected");
 expected.clear();observed=0;
}
static void malformed(unsigned len,bool missing_sop,bool extra_sop){
 expected.clear();observed=0;need(d.s_ready,"MAC not idle before malformed frame");
 for(unsigned i=0;i<len;++i){d.s_valid=1;d.s_data=(unsigned char)i;
  d.s_sop=(!missing_sop&&i==0)||(extra_sop&&i==5);d.s_eop=(i+1==len);tick();}
 need(d.drop,"malformed client frame not rejected");
 d.s_valid=0;tick();need(d.s_ready&&!d.line_valid,"malformed frame left MAC stuck");
}
int main(int argc,char**argv){try{
 Verilated::commandArgs(argc,argv);reset();std::ifstream f("vectors/mac_tx/cases.txt");need(f.good(),"missing independent TX MAC vectors");
 std::string body,line;unsigned valid_cases=0;
 while(f>>body>>line){send(decode(body),decode(line));++valid_cases;}
 need(valid_cases>=200,"incomplete TX MAC corpus");
 malformed(1,false,false);malformed(59,false,false);malformed(1515,false,false);malformed(1600,false,false);
 malformed(60,true,false);malformed(60,false,true);
 // Reset partial client capture. No bytes may be emitted from the old frame.
 d.s_valid=1;d.s_sop=1;d.s_eop=0;d.s_data=0x12;tick();reset();
 for(unsigned i=0;i<100;++i)tick();
 f.clear();f.seekg(0);f>>body>>line;send(decode(body),decode(line));
 d.final();std::ofstream out("reports/mac_tx/result.json");
 out<<"{\"status\":\"PASS\",\"frames\":"<<frames<<",\"malformed\":6,\"checks\":"<<checks<<",\"cycles\":"<<cycles<<"}\n";
 std::cout<<"PASS frames="<<frames<<" checks="<<checks<<" cycles="<<cycles<<"\n";return 0;
}catch(const std::exception&e){std::cerr<<"FAIL "<<e.what()<<"\n";return 1;}}
