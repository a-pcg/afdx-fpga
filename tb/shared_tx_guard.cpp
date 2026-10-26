#include "Vafdx_shared_tx.h"
#include "verilated.h"
#include <array>
#include <cstdint>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>
using Bytes=std::vector<unsigned char>;
static Vafdx_shared_tx d;
static std::array<std::array<Bytes,10>,4> golden;
static std::array<bool,4> configured{},seen{};
static std::array<unsigned,4> bag{},next_sn{};
static std::array<std::uint64_t,4> last{},minimum_gap{};
static unsigned frames=0,sfds=0,cfg_commits=0,withdrawals=0,held_transactions=0,before_boundaries=0,exact_boundaries=0,init_cases=0;
static unsigned long long checks=0,cycles=0;
static Bytes wire,expected_wire;static bool expect_frame=false,low_wrap=false;
static std::uint64_t previous_byte=0,previous_end=0,physical_sfd_time=0;
static void need(bool ok,const char*s){++checks;if(!ok)throw std::runtime_error(s);}
static std::uint64_t period(unsigned exponent){return 100000ULL<<exponent;}
static Bytes decode(const std::string&s){Bytes b;for(size_t i=0;i<s.size();i+=2)b.push_back((unsigned char)std::stoul(s.substr(i,2),nullptr,16));return b;}
static void step(){
 d.clk=0;d.eval();bool physical_sfd=false;
 bool commit=d.cfg_valid&&d.cfg_ready&&!d.reset;
 unsigned cv=d.cfg_vl,ce=d.cfg_bag_exp;
 if(d.reset){need(!d.cfg_ready&&!d.cfg_reject&&!d.initialized,"configuration accepted during reset");}
 else{
  need(!d.fault,"internal TX fault during guard test");
  if(commit){need(d.initialized&&!d.run&&!d.app_enable&&!d.busy,"config committed while not quiescent");if(seen[cv])need(d.monotonic_time-last[cv]>=period(bag[cv]>ce?bag[cv]:ce),"premature hardware config handshake");}
  if(d.line_valid&&d.byte_tick){
   if(wire.empty()){if(previous_end)need(d.monotonic_time-previous_end>=104,"guard test IFG violation");}
   else need(d.monotonic_time-previous_byte==8,"guard test byte cadence mismatch");
   if(wire.size()==7){need(d.line_data==0xd5,"guard test missing D5");physical_sfd=true;physical_sfd_time=d.monotonic_time;}
   wire.push_back(d.line_data);previous_byte=d.monotonic_time;
  }
  if(!d.line_valid&&!wire.empty()){need(expect_frame&&wire==expected_wire,"guard/SN frame differs from golden bytes");wire.clear();expect_frame=false;previous_end=previous_byte;++frames;}
 }
 d.clk=1;d.eval();++cycles;
 if(!d.reset){
  if(commit){if(seen[cv]){if(minimum_gap[cv]<period(ce))minimum_gap[cv]=period(ce);if(minimum_gap[cv]<period(bag[cv]))minimum_gap[cv]=period(bag[cv]);}bag[cv]=ce;configured[cv]=true;++cfg_commits;}
  if(d.sfd_pulse){
   unsigned vl=d.sfd_vl;need(vl<4&&physical_sfd&&d.sfd_time==physical_sfd_time,"guard test real SFD timestamp mismatch");
   need(configured[vl],"unconfigured VL reached wire");if(seen[vl])need(d.sfd_time-last[vl]>=minimum_gap[vl],"SFD BAG violated across accepted reconfiguration");
   last[vl]=d.sfd_time;seen[vl]=true;minimum_gap[vl]=period(bag[vl]);next_sn[vl]=next_sn[vl]==255?1:next_sn[vl]+1;++sfds;
  }else need(!physical_sfd,"missing registered SFD event");
  if((d.monotonic_time>>32)>7)low_wrap=true;
 }
}
static bool guard_expected(unsigned vl,unsigned exponent){
 return !seen[vl] || d.monotonic_time-last[vl]>=period(bag[vl]>exponent?bag[vl]:exponent);
}
static bool ready(unsigned vl,unsigned exponent,bool valid){
 d.cfg_vl=vl;d.cfg_bag_exp=exponent;d.cfg_valid=valid;d.clk=0;d.eval();
 need(d.initialized&&!d.run&&!d.app_enable&&!d.busy,"ready reference used outside quiescence");
 bool expected=guard_expected(vl,exponent);
 // The guard is pipelined: hardware may only assert ready when the true
 // condition holds, and may take a couple of cycles to do so.
 need(!(d.cfg_ready&&!expected),"premature hardware config ready");
 need(bool(d.cfg_reject)==(valid&&!bool(d.cfg_ready)),"cfg_reject is not a cycle-level not-accepted indication");
 return bool(d.cfg_ready);
}
static void immediate(unsigned vl,unsigned exponent){unsigned n=0;while(!ready(vl,exponent,true)){step();need(++n<8,"unseen/safe VL configuration blocked");}unsigned old=cfg_commits;step();d.cfg_valid=0;d.eval();need(cfg_commits==old+1,"immediate config did not commit once");}
static void withdrawn(unsigned vl,unsigned exponent,unsigned width=1){
 unsigned old=cfg_commits;
 for(unsigned i=0;i<width;++i){need(!ready(vl,exponent,true),"early config unexpectedly ready");step();}
 d.cfg_valid=0;d.eval();step();need(cfg_commits==old,"withdrawn valid mutated configuration");++withdrawals;
}
static void held(unsigned vl,unsigned exponent){
 unsigned old=cfg_commits;unsigned waited=0;bool counted=false;
 std::uint64_t interval=period(bag[vl]>exponent?bag[vl]:exponent);
 while(true){bool accepted=ready(vl,exponent,true);auto age=d.monotonic_time-last[vl];
  if(age==interval-1){need(!accepted,"one-cycle-early config accepted");++before_boundaries;}
  if(accepted){need(age>=interval,"config accepted before deadline");
   need(age<=interval+4,"pipelined guard latency larger than four cycles");
   if(!counted){++exact_boundaries;counted=true;}}
  step();if(accepted)break;need(++waited<13000000,"held configuration timeout");
 }
 d.cfg_valid=0;d.eval();need(waited>0&&cfg_commits==old+1,"held valid did not wait/commit exactly once");++held_transactions;
}
static void reject_unconfigured(unsigned vl){
 d.app_enable=1;d.app_msg_valid=1;d.app_vl=vl;d.app_length=17;d.app_valid=1;d.app_data=0x77;d.app_last=1;
 unsigned n=0;while(true){d.clk=0;d.eval();bool accept=d.app_msg_ready;need(!d.app_ready,"unconfigured input data became ready");step();if(accept)break;need(++n<100,"unconfigured admission timeout");}d.app_msg_valid=0;
 while(!d.app_rsp_valid){step();need(++n<100,"unconfigured response timeout");}
 for(unsigned i=0;i<3;++i){need(d.app_rsp_status==7&&!d.app_ready&&d.pool_free==16&&d.pending==0,"canceled startup config had side effects");step();}
 d.app_rsp_ready=1;step();d.app_rsp_ready=0;d.app_valid=0;d.app_enable=0;d.eval();
}
static void reset_with_valid(unsigned vl,unsigned exponent,bool withdraw_in_init){
 need(!expect_frame,"guard reset must not silently clear a golden frame");
 d.reset=1;d.run=0;d.app_enable=0;d.app_msg_valid=0;d.app_valid=0;d.app_rsp_ready=0;d.drop_valid=0;d.drop_rsp_ready=0;
 d.cfg_valid=1;d.cfg_vl=vl;d.cfg_bag_exp=exponent;wire.clear();previous_end=0;
 step();step();configured.fill(false);seen.fill(false);bag.fill(0);next_sn.fill(0);last.fill(0);minimum_gap.fill(0);
 unsigned old=cfg_commits;d.reset=0;unsigned n=0;
 while(!d.initialized){d.clk=0;d.eval();need(!d.cfg_ready,"configuration ready during table initialization");if(withdraw_in_init&&n==3)d.cfg_valid=0;step();need(++n<40,"guard initialization timeout");}
 if(withdraw_in_init){need(cfg_commits==old,"startup withdrawal unexpectedly committed");d.cfg_valid=0;}
 else{unsigned k=0;while(!ready(vl,exponent,true)){step();need(++k<8,"reset did not clear SFD guard history");}step();d.cfg_valid=0;d.eval();need(cfg_commits==old+1,"held startup valid not accepted exactly once");}
 ++init_cases;
}
static void send(unsigned vl){
 need(configured[vl]&&!expect_frame&&next_sn[vl]<10,"invalid golden message setup");expected_wire=golden[vl][next_sn[vl]];expect_frame=true;
 d.app_enable=1;d.app_msg_valid=1;d.app_vl=vl;d.app_length=17;unsigned n=0;
 while(true){d.clk=0;d.eval();bool accept=d.app_msg_ready;step();if(accept)break;need(++n<200,"guard payload admission timeout");}d.app_msg_valid=0;
 unsigned pos=0;while(!d.app_rsp_valid){d.app_valid=pos<17;d.app_data=pos;d.app_last=pos==16;d.clk=0;d.eval();bool accept=d.app_valid&&d.app_ready;step();if(accept)++pos;need(++n<2000,"guard enqueue timeout");}
 d.app_valid=0;need(d.app_rsp_status==0&&pos==17,"guard message enqueue rejected");for(unsigned i=0;i<4;++i)step();d.app_rsp_ready=1;step();d.app_rsp_ready=0;d.app_enable=0;d.run=1;
 n=0;while(expect_frame||d.busy){step();need(++n<14000000,"guard physical send timeout");}d.run=0;d.clk=0;d.eval();need(d.pool_free==16,"guard test leaked slot");
}
int main(int argc,char**argv){try{
 Verilated::commandArgs(argc,argv);need(argc==3,"usage vectors result");std::ifstream in(argv[1]);unsigned vl,sn;std::string text;while(in>>vl>>sn>>text){need(vl<4&&sn<10,"bad guard golden vector index");golden[vl][sn]=decode(text);}
 reset_with_valid(1,2,false);immediate(0,3);send(0);
 withdrawn(0,3);withdrawn(0,1);withdrawn(0,4,8);
 // Change target/proposal only after valid withdrawal; an unseen VL is independent.
 immediate(1,4);send(1);need(!ready(0,1,false),"other VL changed original SFD history");
 held(0,1);send(0);held(0,3);send(0);withdrawn(0,0);held(0,0);send(0);held(0,0);send(0);
 // Stop/start does not clear history; a pending long proposal is then canceled by reset.
 d.run=1;for(unsigned i=0;i<17;++i)step();d.run=0;d.eval();withdrawn(0,0);need(!ready(0,7,true),"early long proposal not blocked");
 reset_with_valid(0,7,false);send(0);
 reset_with_valid(2,3,true);reject_unconfigured(2);immediate(2,0);send(2);
 need(frames==8&&sfds==8,"guard frame/SFD accounting mismatch");need(held_transactions==4&&before_boundaries==4&&exact_boundaries==4,"guard boundary coverage missing");need(low_wrap,"low32 wrap not exercised at nonzero high timestamp");
 d.final();std::ofstream out(argv[2]);out<<"{\"status\":\"PASS\",\"frames\":"<<frames<<",\"sfd_events\":"<<sfds<<",\"cfg_commits\":"<<cfg_commits<<",\"withdrawals\":"<<withdrawals<<",\"held_transactions\":"<<held_transactions<<",\"before_boundaries\":"<<before_boundaries<<",\"exact_boundaries\":"<<exact_boundaries<<",\"reset_init_cases\":"<<init_cases<<",\"checks\":"<<checks<<",\"cycles\":"<<cycles<<",\"low32_wrap\":true,\"initial_high32\":7,\"s06_long_idle_closed\":false}\n";
 std::cout<<"PASS T3 hardware guard checks="<<checks<<"\n";return 0;
}catch(const std::exception&e){std::cerr<<"FAIL "<<e.what()<<" cycles="<<cycles<<" frames="<<frames<<"\n";return 1;}}
