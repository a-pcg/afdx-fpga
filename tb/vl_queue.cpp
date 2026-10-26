#include "Vafdx_vl_queue.h"
#include "verilated.h"
#include <cstdint>
#include <deque>
#include <fstream>
#include <iostream>
#include <random>
#include <stdexcept>
#include <type_traits>
#include <vector>
struct Descriptor{std::uint32_t tag;unsigned length;};
static Vafdx_vl_queue d;
static std::vector<std::deque<Descriptor>> model;
static std::mt19937 rng(20261019);
static unsigned vls,nodes,used=0,commands=0,next_tag=1;
static unsigned long long checks=0,cycles=0;
static void need(bool ok,const char* s){++checks;if(!ok)throw std::runtime_error(s);}
static void tick(){d.clk=0;d.eval();d.clk=1;d.eval();++cycles;}
template<class T>static bool bit(const T& value,unsigned i){if constexpr(std::is_integral<T>::value)return (value>>i)&1;else return (value[i/32]>>(i%32))&1;}
static void balanced(){need(d.free_count+used==nodes,"descriptor node conservation");for(unsigned i=0;i<vls;++i)need(bit(d.pending,i)==!model[i].empty(),"pending bitmap mismatch");}
static void reset(){d.reset=1;d.cmd_valid=0;d.rsp_ready=0;tick();tick();d.reset=0;unsigned n=0;
 while(!d.initialized){need(!d.cmd_ready&&!d.rsp_valid,"queue accessed before initialization");tick();need(++n<=vls+3,"initialization timeout");}
 for(auto&q:model)q.clear();used=0;balanced();}
static unsigned command(unsigned op,unsigned vl,unsigned length=1){
 need(d.cmd_ready,"command engine not idle");d.cmd_valid=1;d.cmd_op=op;d.cmd_vl=vl;d.cmd_tag=next_tag++;d.cmd_length=length;
 auto tag=d.cmd_tag;unsigned expected_status=0;Descriptor expected{};
 auto &q=model[vl];unsigned count=q.size();
 if(op==0){if(length==0||length>2048)expected_status=4;else if(count>=4||used==nodes)expected_status=2;else{expected={tag,length};q.push_back(expected);++used;++count;}}
 else if(op==1||op==2){if(q.empty())expected_status=1;else{expected=q.front();if(op==1){q.pop_front();--used;--count;}}}
 else expected_status=3;
 tick();d.cmd_valid=0;d.cmd_vl=(vl+1)%vls;d.cmd_tag=0xdeadbeef;d.cmd_length=0;
 unsigned timeout=0;while(!d.rsp_valid){tick();need(++timeout<nodes+12,"queue transaction timeout");}
 need(d.rsp_status==expected_status&&d.rsp_vl==vl&&d.rsp_count==count,"queue status/VL/count mismatch");
 need(d.rsp_tag==expected.tag&&d.rsp_length==expected.length,"queue FIFO descriptor mismatch");
 for(unsigned n=rng()%5;n;--n){tick();need(d.rsp_valid&&!d.cmd_ready&&d.rsp_status==expected_status&&d.rsp_vl==vl&&d.rsp_count==count&&d.rsp_tag==expected.tag&&d.rsp_length==expected.length,"response changed under backpressure");}
 d.rsp_ready=1;tick();d.rsp_ready=0;++commands;balanced();return expected_status;
}
int main(int argc,char**argv){try{
 Verilated::commandArgs(argc,argv);need(argc==4,"usage vls nodes result");vls=std::stoul(argv[1]);nodes=std::stoul(argv[2]);model.resize(vls);reset();
 for(unsigned vl=0;vl<vls;++vl){command(1,vl);command(2,vl);}
 command(0,0,0);command(0,0,2049);command(0,0,4095);command(3,0);
 for(unsigned i=0;i<4;++i)command(0,0,i+1);need(command(0,0)==2,"per-VL quota not enforced");
 command(2,0);for(unsigned i=0;i<4;++i)command(1,0);command(1,0);
 for(unsigned i=0;i<nodes;++i)need(command(0,i%vls,2048)==0,"unable to fill shared nodes");
 need(command(0,vls-1)==2,"global node exhaustion not enforced");
 for(unsigned vl=0;vl<vls;++vl)while(!model[vl].empty())command(1,vl);
 for(unsigned i=0;i<6000;++i){unsigned op=rng()%4,vl=rng()%vls;command(op,vl,1+rng()%2048);if(i==1999||i==3999)reset();}
 for(unsigned vl=0;vl<vls;++vl)while(!model[vl].empty())command(1,vl);
 // Reset with an unconsumed PUSH response must clear every queue and node.
 d.cmd_valid=1;d.cmd_op=0;d.cmd_vl=0;d.cmd_tag=123;d.cmd_length=1;tick();d.cmd_valid=0;
 while(!d.rsp_valid)tick();reset();for(unsigned vl=0;vl<vls;++vl)command(1,vl);
 d.final();std::ofstream out(argv[3]);out<<"{\"status\":\"PASS\",\"vl_count\":"<<vls<<",\"nodes\":"<<nodes<<",\"commands\":"<<commands<<",\"checks\":"<<checks<<",\"cycles\":"<<cycles<<"}\n";
 std::cout<<"PASS VL queues "<<vls<<" checks="<<checks<<"\n";return 0;
}catch(const std::exception&e){std::cerr<<"FAIL "<<e.what()<<"\n";return 1;}}
