#include "Vafdx_rx_dual.h"
#include "verilated.h"
#include <algorithm>
#include <fstream>
#include <iostream>
#include <random>
#include <stdexcept>
#include <string>
#include <vector>
static Vafdx_rx_dual d;static uint64_t cycles=0,checks=0;static unsigned delivered=0,batches=0,pass_decisions=0,reject_decisions=0,cancelled_pass=0,pending_pass=0;
static std::mt19937 rng(20261018);static std::vector<unsigned> got;static unsigned want_vl,want_sn;
static uint64_t stamps[2]={0,0};
static const uint64_t time_base=0xfffffffffffff000ULL;
static bool check_output=true,blocked=false;static uint64_t held=0;static uint64_t held_time=0;
static void need(bool b,const char*s){++checks;if(!b)throw std::runtime_error(s);}
static std::vector<unsigned> bytes(std::string s){std::vector<unsigned> b;if(s=="-")return b;for(size_t i=0;i<s.size();i+=2)b.push_back(std::stoul(s.substr(i,2),nullptr,16));return b;}
static uint64_t sig(){return d.m_data|(uint64_t(d.m_last)<<8)|(uint64_t(d.m_vl)<<9)|(uint64_t(d.m_sn)<<12)|(uint64_t(d.m_len)<<20)|(uint64_t(d.m_network)<<36);}
static void tick(){d.clk=0;d.time_now=cycles+time_base;d.eval();
 if(!d.reset){
 need(!d.m_valid||d.initialized,"payload published before global initialization/rearm");
 if(blocked)need(d.m_valid&&sig()==held&&d.m_time==held_time,"blocked application fields changed");
 blocked=d.m_valid&&!d.m_ready;held=sig();held_time=d.m_time;
 if(d.m_valid&&d.m_ready){if(check_output){need(d.m_vl==want_vl&&d.m_sn==want_sn,"wrong VL or SN delivered");need(d.m_time==stamps[d.m_network],"SFD timestamp detached from payload");if(batches==0)need(d.m_network==0,"first simultaneous winner not A");}got.push_back(d.m_data);if(d.m_last){need(got.size()==d.m_len,"payload length metadata");++delivered;need(pending_pass==1,"delivery without unique accepted decision");--pending_pass;}}
 }else {blocked=false;cancelled_pass+=pending_pass;pending_pass=0;}
 d.clk=1;d.eval();if(!d.reset&&d.decision){if(d.decision_pass){++pass_decisions;++pending_pass;need(pending_pass==1,"multiple active payload decisions");}else ++reject_decisions;}++cycles;}
static void reset(){d.reset=1;d.line_valid=0;d.byte_ce=0;d.m_ready=0;tick();tick();d.reset=0;for(int i=0;i<160&&!d.initialized;++i){d.byte_ce=(i%8==0)?3:0;tick();}d.byte_ce=0;need(d.initialized,"init failed");got.clear();}
static void send(const std::vector<unsigned>&a,const std::vector<unsigned>&b,bool stall=false){
 size_t n=std::max(a.size(),b.size())+14;
 for(size_t i=0;i<n;i++){
 if(i==7){stamps[0]=cycles+time_base;stamps[1]=cycles+time_base;}
 d.byte_ce=3;d.line_valid=(i<a.size()?1:0)|(i<b.size()?2:0);d.line_data=(i<a.size()?a[i]:0)|((i<b.size()?b[i]:0)<<8);
 for(int c=0;c<8;c++){d.m_ready=stall?0:(rng()%4!=0);tick();d.byte_ce=0;}
 }d.line_valid=0;d.byte_ce=0;
}
int main(int argc,char**argv){try{Verilated::commandArgs(argc,argv);need(argc==3,"usage cases report");reset();
 std::ifstream f(argv[1]);need(f.good(),"missing vectors");std::string p,a,b;unsigned expected;
 std::vector<unsigned> last_a,last_b,last_p,first_a,first_b,first_p;
 while(f>>want_vl>>want_sn>>p>>a>>b>>expected){
 if(batches==0){first_a=bytes(a);first_b=bytes(b);first_p=bytes(p);}
 got.clear();unsigned before=delivered;send(bytes(a),bytes(b));
 for(int i=0;i<12000;i++){d.m_ready=rng()%4!=0;tick();}
 need(delivered-before==expected,"unexpected delivery count");need(got==(expected?bytes(p):std::vector<unsigned>{}),"payload differs from independent golden bytes");need(!d.busy,"descriptor not recycled");++batches;
 last_a=bytes(a);last_b=bytes(b);last_p=bytes(p);
 }
 unsigned bad_a=unsigned(d.bad_fcs);need(bad_a==8,"bad FCS coverage count");
 // Hold a fully validated output, verify stability, then reset all RX-owned banks.
 reset();got.clear();send(last_a,last_b,true);for(int i=0;i<200;i++){d.m_ready=0;tick();}need(d.m_valid,"missing blocked payload");
 reset();check_output=false;for(int i=0;i<500;i++){d.m_ready=1;tick();}need(got.empty()&&!d.m_valid&&!d.busy,"payload survived reset");
 // Same first-SN must work again after reset; all slots and IC/RM reclaimed.
 got.clear();unsigned before=delivered;send(last_a,last_b);for(int i=0;i<12000;i++){d.m_ready=1;tick();}need(delivered==before+1&&got==last_p,"reset recovery failed");
 // Fill a lane while output stays blocked: finite MAC queue must report overflow.
 reset();send(last_a,last_b,true);for(int n=0;n<5;n++)send(last_a,last_b,true);
 need(d.full_drops!=0,"bounded receive overflow was not reported");
 for(int i=0;i<30000;i++){d.m_ready=1;tick();}need(!d.busy,"overflow recovery did not drain");
 got.clear();before=delivered;send(first_a,first_a);for(int i=0;i<12000;i++){d.m_ready=1;tick();}
 need(delivered==before+1&&got==first_p,"overflow did not recover without reset");
 // Reset during a partial physical frame, then receive the same frame cleanly.
 reset();for(size_t i=0;i<last_a.size()/2;i++){d.byte_ce=1;d.line_valid=1;d.line_data=last_a[i];tick();}
 reset();got.clear();before=delivered;send(last_a,last_b);for(int i=0;i<12000;i++){d.m_ready=1;tick();}
 need(delivered==before+1&&got==last_p,"partial-line reset recovery failed");reset();
 // A frame already in progress during table initialization must be discarded;
 // only a complete post-initialization idle boundary arms physical reception.
 d.reset=1;d.byte_ce=0;tick();tick();d.reset=0;got.clear();before=delivered;
 send(last_a,last_b,true);for(int i=0;i<500;i++){d.m_ready=1;tick();}
 need(delivered==before&&got.empty(),"startup frame was not discarded");need(d.initialized,"startup idle did not arm receiver");
 got.clear();send(last_a,last_b);for(int i=0;i<12000;i++){d.m_ready=1;tick();}
 need(delivered==before+1&&got==last_p,"startup boundary recovery failed");
 need(pass_decisions==delivered+cancelled_pass+pending_pass&&pending_pass==0,"accepted-decision accounting mismatch");
 std::ofstream o(argv[2]);o<<"{\"status\":\"PASS\",\"batches\":"<<batches<<",\"delivered_including_reset_tests\":"<<delivered<<",\"bad_fcs_tested\":"<<bad_a<<",\"pass_decisions\":"<<pass_decisions<<",\"reject_decisions\":"<<reject_decisions<<",\"reset_cancelled_pass\":"<<cancelled_pass<<",\"in_flight\":"<<pending_pass<<",\"checks\":"<<checks<<",\"cycles\":"<<cycles<<"}\n";
 std::cout<<"PASS RX dual "<<batches<<" batches "<<checks<<" checks\n";return 0;
 }catch(const std::exception&e){std::cerr<<"FAIL batch="<<batches<<" VL="<<want_vl<<" SN="<<want_sn<<" "<<e.what()<<"\n";return 1;}}
