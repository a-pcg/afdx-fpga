#include "Vafdx_integrity_checker.h"
#include "verilated.h"
#include <fstream>
#include <iostream>
#include <random>
#include <stdexcept>
#include <string>
static Vafdx_integrity_checker d;
static unsigned long long checks=0,cycles=0;
static unsigned decisions=0;
static std::mt19937 rng(20261018);
static void need(bool ok,const std::string& why){++checks;if(!ok)throw std::runtime_error(why);}
static void tick(){d.clk=0;d.eval();d.clk=1;d.eval();++cycles;}
static void reset(unsigned n){d.reset=1;d.s_valid=0;d.m_ready=0;tick();tick();d.reset=0;unsigned t=0;
 while(!d.initialized){need(!d.s_ready&&!d.m_valid,"IC exposed RAM before initialization");tick();need(++t<=2*n+5,"IC initialization timeout");}
 need(d.s_ready,"IC did not become idle after clearing state");}
static void verify(unsigned network,unsigned vl,unsigned sn,unsigned tag,unsigned pass,unsigned reason){
 need(d.m_vl==vl&&d.m_network==network&&d.m_sn==sn&&d.m_tag==tag,"IC descriptor metadata changed");
 need(d.m_pass==pass&&d.m_reason==reason,"IC decision differs from independent truth model");}
int main(int argc,char**argv){try{
 Verilated::commandArgs(argc,argv);need(argc>=4,"usage: cases VL_count report");unsigned n=std::stoul(argv[2]);
 for(int replay=0;replay<2;++replay){reset(n);std::ifstream f(argv[1]);need(f.good(),"IC vectors missing");
 unsigned net,vl,sn,good,enable,tag,pass,reason;
 while(f>>net>>vl>>sn>>good>>enable>>tag>>pass>>reason){
  need(d.s_ready,"IC not ready between transactions");d.s_valid=1;d.s_network=net;d.s_vl=vl;d.s_sn=sn;d.s_frame_good=good;d.s_ic_enable=enable;d.s_tag=tag;tick();d.s_valid=0;
  // Source fields can change after acceptance; output must retain the descriptor.
  d.s_network=!net;d.s_vl=0;d.s_sn=0;d.s_tag=0;unsigned t=0;
  while(!d.m_valid){tick();need(++t<8,"IC decision timeout");}
  for(unsigned hold=rng()%5;hold;--hold){verify(net,vl,sn,tag,pass,reason);need(!d.s_ready,"IC accepted while output blocked");tick();}
  verify(net,vl,sn,tag,pass,reason);d.m_ready=1;tick();d.m_ready=0;++decisions;
 }
 }
 // A reset discards an outstanding decision and clears both network states.
 d.s_valid=1;d.s_network=0;d.s_vl=0;d.s_sn=42;d.s_frame_good=1;d.s_ic_enable=1;d.s_tag=123;tick();d.s_valid=0;
 while(!d.m_valid)tick();reset(n);need(!d.m_valid,"IC decision survived reset");
 d.final();std::ofstream out(argv[3]);out<<"{\"status\":\"PASS\",\"vl_count\":"<<n<<",\"decisions\":"<<decisions<<",\"checks\":"<<checks<<",\"cycles\":"<<cycles<<"}\n";
 std::cout<<"PASS VL="<<n<<" decisions="<<decisions<<" checks="<<checks<<"\n";return 0;
}catch(const std::exception&e){std::cerr<<"FAIL "<<e.what()<<"\n";return 1;}}
