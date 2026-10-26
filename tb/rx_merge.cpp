#include "Vafdx_rx_merge.h"
#include "verilated.h"
#include <fstream>
#include <iostream>
#include <random>
#include <stdexcept>
#include <string>
static Vafdx_rx_merge d;
static uint64_t cycles=0, checks=0;static unsigned count=0;
static void need(bool b,const char*s){++checks;if(!b)throw std::runtime_error(s);}
static void tick(){d.clk=0;d.eval();d.clk=1;d.eval();++cycles;}
static void reset(){d.reset=1;d.s_valid=0;d.m_ready=0;tick();tick();d.reset=0;for(int i=0;i<40&&!d.initialized;i++)tick();need(d.initialized,"init timeout");}
static uint64_t signature(){return uint64_t(d.m_vl)|(uint64_t(d.m_network)<<3)|(uint64_t(d.m_sn)<<4)|(uint64_t(d.m_tag)<<12)|(uint64_t(d.m_pass)<<28)|(uint64_t(d.m_reason)<<29);}
int main(int argc,char**argv){try{Verilated::commandArgs(argc,argv);need(argc==4,"usage vectors result corrupt");
 std::mt19937 rng(20261018);for(int replay=0;replay<2;replay++){
 reset();std::ifstream f(argv[1]);unsigned net,vl,sn,good,en,tag,stamp,pass,reason;
 while(f>>net>>vl>>sn>>good>>en>>tag>>stamp>>pass>>reason){
 d.s_valid=1u<<net;d.s_vl=vl<<(3*net);d.s_vlid=(vl+1)<<(16*net);d.s_len=123u<<(16*net);d.s_sn=sn<<(8*net);d.s_tag=uint64_t(tag)<<(32*net);d.s_time=uint64_t(stamp)<<(32*net);d.s_good=good<<net;d.s_ic_enable=en<<net;
 unsigned timeout=0;d.eval();while(!(d.s_ready&(1u<<net))){tick();need(++timeout<40,"input timeout");}tick();d.s_valid=0;d.s_sn=0;d.s_time=0;
 while(!d.m_valid){tick();need(++timeout<60,"output timeout");}
 need(d.m_vl==vl&&d.m_network==net&&d.m_sn==sn&&d.m_tag==tag&&d.m_time==stamp&&d.m_vlid==vl+1&&d.m_len==123,"metadata changed");
 if(std::stoi(argv[3])&&count==0)pass=!pass;
 need(d.m_pass==pass&&d.m_reason==reason,"independent golden mismatch");
 auto sig=signature();for(unsigned h=rng()%9;h;--h){need(d.s_ready==0,"accepted under blocked output");need(signature()==sig&&d.m_time==stamp&&d.m_vlid==vl+1&&d.m_len==123,"blocked output changed");tick();}
 d.m_ready=1;tick();d.m_ready=0;++count;
 }
 }
 // Both source descriptors held concurrently: first A accepted, then B rejected atomically.
 reset();d.s_valid=3;d.s_vl=0;d.s_vlid=0x10001;d.s_len=0x10001;d.s_sn=0x2a2a;d.s_tag=0x0000000200000001ULL;d.s_time=0x0000010000000100ULL;d.s_good=3;d.s_ic_enable=3;
 unsigned got=0,grants=0;
 for(int i=0;i<100&&got<2;i++){d.eval();unsigned ready=d.s_ready&d.s_valid;bool out=d.m_valid;unsigned net=d.m_network,pass=d.m_pass,reason=d.m_reason;d.m_ready=1;tick();
 if(ready){need(ready==(grants?2:1),"simultaneous grant priority");d.s_valid&=~ready;++grants;}
 if(out){need(net==got&&pass==(got==0)&&reason==(got?3:0),"atomic simultaneous duplicate");++got;}
 }need(got==2&&grants==2,"lost simultaneous descriptor");
 // Cancel an output, then prove fresh first-SN behavior.
 d.s_valid=1;d.s_good=3;d.s_sn=77;d.m_ready=0;while(!d.s_ready)tick();tick();d.s_valid=0;while(!d.m_valid)tick();reset();need(!d.m_valid,"reset kept decision");
 d.s_valid=1;d.s_sn=77;d.s_good=1;d.s_ic_enable=1;tick();d.s_valid=0;while(!d.m_valid)tick();need(d.m_pass,"reset did not clear states");
 for(unsigned phase=0;phase<6;phase++){
 reset();d.s_valid=1;d.s_sn=99;d.s_good=1;d.s_ic_enable=1;d.m_ready=0;tick();d.s_valid=0;
 for(unsigned i=0;i<phase;i++)tick();reset();need(!d.m_valid,"in-flight reset leaked result");
 d.s_valid=1;d.s_sn=200;tick();d.s_valid=0;for(int i=0;i<20&&!d.m_valid;i++)tick();need(d.m_valid&&d.m_pass,"in-flight reset state not cleared");
 }
 std::ofstream o(argv[2]);o<<"{\"status\":\"PASS\",\"decisions\":"<<count+2<<",\"checks\":"<<checks<<",\"cycles\":"<<cycles<<"}\n";std::cout<<"PASS RX merge "<<count+2<<" decisions\n";return 0;
 }catch(const std::exception&e){std::cerr<<"FAIL "<<e.what()<<"\n";return 1;}}
