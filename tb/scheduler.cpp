#include "Vafdx_bag_scheduler.h"
#include "verilated.h"
#include <iostream>
#include <fstream>
#include <array>
#include <random>
#include <stdexcept>
#ifndef TEST_VL_COUNT
#define TEST_VL_COUNT 4
#endif
static Vafdx_bag_scheduler d;
static unsigned long long cycles=0,checks=0,grants=0;
static void ck(bool x,const char*s){++checks;if(!x)throw std::runtime_error(s);}
static void tick(){d.clk=0;d.eval();d.clk=1;d.eval();++cycles;}
int main(int argc,char**argv){try{
 Verilated::commandArgs(argc,argv);d.reset=1;d.enable=0;d.cfg_valid=0;d.mac_idle=1;d.grant_ready=0;d.time_now=0;tick();tick();d.reset=0;
 for(unsigned i=0;i<TEST_VL_COUNT;++i){d.cfg_valid=1;d.cfg_index=i;d.cfg_bag_exp=i%4;tick();}d.cfg_valid=0;d.enable=1;
#if TEST_VL_COUNT > 32
 for(unsigned i=0;i<TEST_VL_COUNT/32;++i)d.pending[i]=0xffffffff;
#else
 d.pending=(1U<<TEST_VL_COUNT)-1;
#endif
 std::array<unsigned long long,TEST_VL_COUNT> last{},count{};std::mt19937 rng(20261017);
 bool held=false;unsigned held_index=0;unsigned busy=0;
 // Cross 32-bit timestamp wrap and run >2 real 8-ms BAG periods.
 for(unsigned t=0;t<2200000;++t){
 d.time_now=0xffff0000U+t;d.mac_idle=busy==0;d.grant_ready=(rng()%5!=0);d.clk=0;d.eval();
 if(held)ck(d.grant_valid&&d.grant_index==held_index,"grant changed under backpressure");
 if(d.grant_valid && d.grant_ready && d.mac_idle){
 unsigned i=d.grant_index;ck(i<TEST_VL_COUNT,"grant out of bounds");
 if(count[i])ck(t-last[i]>=(100000ULL<<(i%4)),"BAG violated across wrap");
 last[i]=t;++count[i];++grants;busy=TEST_VL_COUNT>32?672:12304;
 }
 held=d.grant_valid&&!(d.grant_ready&&d.mac_idle);held_index=d.grant_index;
 tick();if(busy)--busy;
 }
 for(unsigned i=0;i<TEST_VL_COUNT;++i)ck(count[i]>=2,"configured VL starved");
 // Disable invalidates any held grant. Config writes while enabled are ignored.
 d.enable=0;tick();ck(!d.grant_valid,"disable leaked held grant");d.reset=1;tick();d.reset=0;d.enable=1;tick();ck(!d.grant_valid,"reset leaked old grant");
 std::ofstream out("reports/scheduler_"+std::to_string(TEST_VL_COUNT)+".json");out<<"{\"status\":\"PASS\",\"vl_count\":"<<TEST_VL_COUNT<<",\"grants\":"<<grants<<",\"checks\":"<<checks<<",\"cycles\":"<<cycles<<"}\n";
 std::cout<<"PASS scheduler VL="<<TEST_VL_COUNT<<" grants="<<grants<<" checks="<<checks<<std::endl;return 0;
 }catch(const std::exception&e){std::cerr<<"FAIL "<<e.what()<<std::endl;return 1;}}
