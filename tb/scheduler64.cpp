#include "Vafdx_bag_scheduler64.h"
#include "verilated.h"
#include <cstdint>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>
// Independent unit test for the TX-private 64-bit BAG scheduler (T4 / S06).
// time_now is driven directly ("injected time"); the only clock cycles executed are the
// scheduler decision cycles. No wall-clock or 2^31-cycle wait is performed anywhere.
static Vafdx_bag_scheduler64 d;
static unsigned vls=4;
static unsigned long long cycles=0,checks=0,grants=0;
static unsigned before_deadline=0,exact_deadline=0,after_deadline=0;
static bool idle_2p31=false,wrap_2p32=false,wrap_2p64=false,isolation=false;
static bool pause_resume=false,long_idle=false,config_reconfig=false,config_ignored=false;
static bool config_bag_edges=false,held_grant=false,window_edge=false,reset_cleared=false;
static bool round_robin=false,invalid_cfg=false,space_ok_gate=false,anchor=false,multi_outstanding=false;
static void need(bool ok,const char*what){++checks;if(!ok)throw std::runtime_error(what);}
static void settle(){d.clk=0;d.eval();}
static void posedge(){d.clk=1;d.eval();++cycles;}
static void cycle(){settle();posedge();}
static void reset_dut(){
 d.reset=1;d.enable=0;d.cfg_valid=0;d.cfg_index=0;d.cfg_bag_exp=0;d.time_now=0;d.pending=0;
 d.space_ok=1;d.grant_consumed=0;d.grant_cancel=0;d.retire_valid=0;d.retire_index=0;
 settle();posedge();posedge();d.reset=0;settle();
}
static void config(unsigned idx,unsigned exp){
 d.enable=0;d.cfg_valid=1;d.cfg_index=idx;d.cfg_bag_exp=exp;settle();posedge();d.cfg_valid=0;settle();
}
static void pend(unsigned idx,bool on){
 std::uint32_t p=d.pending;
 if(on)p|=(std::uint32_t)(1u<<idx);else p&=(std::uint32_t)~(1u<<idx);
 d.pending=p;
}
static void pend_all(bool on){
 d.pending=on?(std::uint32_t)((1u<<vls)-1u):0u;
}
// Observe a presented candidate; it stays valid until consumed. T11 helpers: pop()
// takes the presented grant (grant_consumed) and retire() reports the real SFD of the
// oldest in-flight frame, which is the only place a BAG deadline is anchored.
static unsigned wait_grant(unsigned limit,const char*what){
 for(unsigned i=0;i<limit;++i){settle();if(d.grant_valid)return i;posedge();}
 settle();if(d.grant_valid)return limit;
 throw std::runtime_error(std::string("missing grant: ")+what);
}
static void expect_no_grant(unsigned n,const char*what){
 for(unsigned i=0;i<n;++i){settle();need(!d.grant_valid,what);posedge();}
}
static void pop(unsigned vl,const char*what){
 settle();need(d.grant_valid,what);need(d.grant_index==vl,what);
 d.grant_consumed=1;settle();posedge();d.grant_consumed=0;settle();
}
static void retire(unsigned vl,std::uint64_t at,const char*what){
 (void)what;d.time_now=at;d.retire_valid=1;d.retire_index=vl;settle();posedge();d.retire_valid=0;settle();
}
// Pop + retire keeps the old (T4) consume meaning: deadline anchored at at.
static void consume(unsigned vl,std::uint64_t at,const char*what){
 pop(vl,what);retire(vl,at,what);++grants;
}
// Reset, configure one VL, grant it at t0 and consume the grant at t0 (deadline uses t0).
static std::uint64_t grant_setup(unsigned vl,unsigned exp,std::uint64_t t0,const char*what){
 reset_dut();config(vl,exp);d.enable=1;d.time_now=t0;d.space_ok=1;pend(vl,true);
 wait_grant(vls+4,what);need(d.grant_index==vl,what);
 consume(vl,t0,what);
 return t0+(std::uint64_t)(100000ULL<<exp);
}
static void scenario_boundaries(){
 std::uint64_t deadline=grant_setup(0,0,0,"boundary setup -1");need(deadline==100000ULL,"BAG 0 deadline");
 pend(0,true);d.time_now=deadline-1;expect_no_grant(2*vls,"grant before deadline (deadline-1)");++before_deadline;
 deadline=grant_setup(0,0,0,"boundary setup exact");
 pend(0,true);d.time_now=deadline;wait_grant(vls+4,"grant at exact deadline");need(d.grant_index==0,"deadline exact VL");consume(0,deadline,"deadline exact consume");++exact_deadline;
 deadline=grant_setup(0,0,0,"boundary setup +1");
 pend(0,true);d.time_now=deadline+1;wait_grant(vls+4,"grant after deadline");need(d.grant_index==0,"deadline+1 VL");consume(0,deadline+1,"deadline+1 consume");++after_deadline;
}
static void scenario_long_idle_2p31(){
 // Documented S06 counterexample: deadline 100000, now 2147583649 -> 32-bit diff 0x80000001.
 std::uint64_t deadline=grant_setup(0,0,0,"2^31 idle setup");
 pend(0,true);d.time_now=2147583649ULL;wait_grant(vls+4,"grant after >2^31 idle");need(d.grant_index==0,"2^31 idle VL");consume(0,2147583649ULL,"2^31 idle consume");
 // elapsed exactly 2^31 (public module would also reject: elapsed[31]=1).
 deadline=grant_setup(0,0,0,"2^31 exact setup");
 pend(0,true);d.time_now=deadline+(1ULL<<31);wait_grant(vls+4,"grant at elapsed 2^31");need(d.grant_index==0,"2^31 exact VL");consume(0,deadline+(1ULL<<31),"2^31 exact consume");
 idle_2p31=true;
}
static void scenario_wrap_2p32(){
 const std::uint64_t t0=0xFFFF0000ULL;const std::uint64_t deadline=grant_setup(0,0,t0,"2^32 wrap setup");
 need(deadline==t0+100000ULL,"2^32 deadline");
 pend(0,true);d.time_now=0xFFFFFFFFULL;expect_no_grant(2*vls,"grant before 2^32 wrap deadline");
 pend(0,true);d.time_now=0x100000000ULL;expect_no_grant(2*vls,"grant just after 2^32 wrap before deadline");
 pend(0,true);d.time_now=deadline;wait_grant(vls+4,"grant at deadline after 2^32 wrap");need(d.grant_index==0,"2^32 wrap VL");consume(0,deadline,"2^32 wrap consume");
 wrap_2p32=true;
}
static void scenario_wrap_2p64(){
 const std::uint64_t t0=0xFFFFFFFFFFFF0000ULL;const std::uint64_t deadline=grant_setup(0,0,t0,"2^64 wrap setup");
 need(deadline==34464ULL,"2^64 deadline wraps to 0x86A0");
 pend(0,true);d.time_now=0xFFFFFFFFFFFFFFFFULL;expect_no_grant(2*vls,"grant before 2^64 wrap deadline");
 pend(0,true);d.time_now=0ULL;expect_no_grant(2*vls,"grant just after 2^64 wrap before deadline");
 pend(0,true);d.time_now=deadline;wait_grant(vls+4,"grant at deadline after 2^64 wrap");need(d.grant_index==0,"2^64 wrap VL");consume(0,deadline,"2^64 wrap consume");
 wrap_2p64=true;
}
static void scenario_vl_isolation(){
 reset_dut();config(0,0);config(1,3);config(2,7);
 d.enable=1;d.space_ok=1;
 pend(0,true);d.time_now=0;wait_grant(vls+4,"isolation VL0 first");consume(0,0,"isolation VL0 first");
 pend(0,true);pend(1,true);d.time_now=50000;wait_grant(vls+4,"isolation VL1 no-deadline");need(d.grant_index==1,"isolated VL1 index");consume(1,50000,"isolation VL1 first");
 pend(1,false);pend(2,true);d.time_now=100;wait_grant(vls+4,"isolation VL2 no-deadline");need(d.grant_index==2,"isolated VL2 index");consume(2,100,"isolation VL2 first");
 pend(2,false);pend(0,true);d.time_now=99999;expect_no_grant(2*vls,"VL0 early after VL1/VL2 activity");
 d.time_now=100000;wait_grant(vls+4,"VL0 own deadline after other VLs");need(d.grant_index==0,"isolated VL0 index");consume(0,100000,"isolation VL0 second");
 pend(0,false);pend(1,true);d.time_now=849999;expect_no_grant(2*vls,"VL1 early after VL0 activity");
 d.time_now=850000;wait_grant(vls+4,"VL1 own deadline after other VLs");need(d.grant_index==1,"isolated VL1 index");consume(1,850000,"isolation VL1 second");
 pend(1,false);pend(2,true);d.time_now=12800099ULL;expect_no_grant(2*vls,"VL2 early after VL1 activity");
 d.time_now=12800100ULL;wait_grant(vls+4,"VL2 own deadline after other VLs");need(d.grant_index==2,"isolated VL2 index");consume(2,12800100ULL,"isolation VL2 second");
 isolation=true;
}
static void scenario_pause_resume(){
 std::uint64_t deadline=grant_setup(0,0,1000,"pause setup");need(deadline==101000ULL,"pause deadline");
 pend(0,true);d.time_now=101000ULL;wait_grant(vls+4,"pause held grant");
 for(unsigned k=0;k<5;++k){settle();need(d.grant_valid&&d.grant_index==0,"held grant changed under backpressure");posedge();}
 d.enable=0;settle();need(!d.grant_valid,"disable did not invalidate held grant");posedge();
 d.time_now=100000ULL;pend(0,true);settle();posedge();
 d.enable=1;settle();
 expect_no_grant(2*vls,"paused deadline was cleared early");
 d.time_now=101000ULL;wait_grant(vls+4,"deadline preserved across pause");need(d.grant_index==0,"pause resume VL");consume(0,101000ULL,"pause resume consume");
 pause_resume=true;
}
static void scenario_long_idle_enqueue(){
 grant_setup(0,0,0,"long idle setup");
 pend(0,false);d.time_now=(1ULL<<40)+12345ULL;
 for(unsigned k=0;k<4*vls;++k){settle();need(!d.grant_valid,"grant without pending");posedge();}
 pend(0,true);wait_grant(vls+4,"new enqueue after long idle is immediately eligible");need(d.grant_index==0,"long idle VL");consume(0,d.time_now,"long idle consume");
 long_idle=true;
}
static void scenario_config_boundaries(){
 for(unsigned exp=0;exp<8;++exp){
  const std::uint64_t expected=(std::uint64_t)(100000ULL<<exp);
  const std::uint64_t deadline=grant_setup(0,exp,0,"config boundary setup");
  need(deadline==expected,"BAG expansion mismatch");
  pend(0,true);d.time_now=deadline-1;expect_no_grant(2*vls,"config boundary early");
  pend(0,true);d.time_now=deadline;wait_grant(vls+4,"config boundary exact");need(d.grant_index==0,"config boundary VL");consume(0,deadline,"config boundary consume");
 }
 config_bag_edges=true;
 // Reconfiguration while disabled clears the stored deadline (public-scheduler semantics).
 grant_setup(0,7,0,"reconfig setup");
 pend(0,false);d.enable=0;settle();posedge();config(0,0);
 d.enable=1;pend(0,true);d.time_now=1;wait_grant(vls+4,"reconfigured VL is immediately eligible");need(d.grant_index==0,"reconfig VL");consume(0,1,"reconfig consume");
 config_reconfig=true;
}
static void scenario_config_enabled_ignored(){
 grant_setup(0,0,0,"cfg-ignored setup");
 d.cfg_valid=1;d.cfg_index=1;d.cfg_bag_exp=3;settle();posedge();d.cfg_valid=0;settle();
 d.pending=(std::uint32_t)(1u<<1);d.time_now=1000000ULL;
 expect_no_grant(2*vls,"configuration while enabled was accepted");
 d.enable=0;settle();posedge();config(1,0);
 d.enable=1;pend(1,true);d.time_now=1000000ULL;wait_grant(vls+4,"idle configuration not accepted");need(d.grant_index==1,"cfg-ignored VL");consume(1,1000000ULL,"cfg-ignored consume");
 config_ignored=true;
}
static void scenario_invalid_index(){
 if((vls&(vls-1))==0)return; // power-of-two VL counts have no unreachable index
 reset_dut();
 d.enable=0;d.cfg_valid=1;d.cfg_index=vls;d.cfg_bag_exp=0;settle();posedge();d.cfg_valid=0;settle();
 d.enable=1;pend_all(true);d.time_now=1000000ULL;
 expect_no_grant(3*vls,"out-of-range configuration created a VL");
 d.enable=0;settle();posedge();config(0,0);
 d.enable=1;pend_all(false);pend(0,true);d.time_now=1000000ULL;wait_grant(vls+4,"valid VL unusable after invalid config");need(d.grant_index==0,"invalid-index VL0");consume(0,1000000ULL,"invalid-index consume");
 invalid_cfg=true;
}
static void scenario_round_robin(){
 reset_dut();for(unsigned i=0;i<vls;++i)config(i,0);
 d.enable=1;d.time_now=0;pend_all(true);
 for(unsigned k=0;k<vls;++k){
  wait_grant(vls+4,"round robin grant");need(d.grant_index==k,"round robin order");
  consume(k,0,"round robin consume");
 }
 d.time_now=0;expect_no_grant(2*vls,"round robin restarted before deadlines");
 // After the BAG deadline every VL is due again; the scan must serve all of them
 // exactly once (the absolute wrap phase depends on the scanning history).
 d.time_now=100000ULL;unsigned seen_mask=0;
 for(unsigned k=0;k<vls;++k){
  wait_grant(vls+8,"round robin after BAG");unsigned idx=d.grant_index;
  need(idx<vls&&!(seen_mask&(1u<<idx)),"round robin repeated/skipped a VL");seen_mask|=1u<<idx;
  consume(idx,100000ULL,"round robin wrap consume");
 }
 need(seen_mask==((1u<<vls)-1u),"round robin did not cover every configured VL");
 round_robin=true;
}
static void scenario_held_and_anchor(){
 reset_dut();config(0,0);d.enable=1;pend(0,true);d.time_now=1000;
 wait_grant(vls+4,"held grant setup");
 for(unsigned k=0;k<5;++k){settle();need(d.grant_valid&&d.grant_index==0,"held grant not stable");posedge();}
 d.space_ok=0;settle();need(d.grant_valid,"grant lost while space_ok=0");posedge();settle();need(d.grant_valid,"grant consumed while space_ok=0");
 d.space_ok=1;settle();pop(0,"anchor pop");
 pend(0,true);d.time_now=1000000ULL;expect_no_grant(2*vls,"VL re-granted before its in-flight frame retires");
 retire(0,1005,"anchor retire");
 pend(0,true);d.time_now=101004ULL;expect_no_grant(2*vls,"deadline anchored before actual retire time");
 d.time_now=101005ULL;wait_grant(vls+4,"deadline anchored at actual retire time");need(d.grant_index==0,"anchor VL");consume(0,101005ULL,"anchor consume");
 held_grant=true;space_ok_gate=true;anchor=true;
}
static void scenario_multi_outstanding(){
 reset_dut();for(unsigned i=0;i<vls;++i)config(i,0);
 d.enable=1;d.space_ok=1;d.time_now=0;pend_all(true);
 for(unsigned k=0;k<vls;++k){
  wait_grant(vls+8,"multi outstanding grant");need(d.grant_index==k,"multi outstanding order");pop(k,"multi outstanding pop");
 }
 expect_no_grant(2*vls,"grant while all VLs are in flight");
 retire(0,0,"multi outstanding retire");
 pend(0,true);expect_no_grant(2*vls,"retired VL granted before its own BAG");
 d.time_now=100000ULL;wait_grant(vls+8,"retired VL granted at its BAG");need(d.grant_index==0,"multi outstanding retire VL");
 multi_outstanding=true;
 reset_dut();for(unsigned i=0;i<vls;++i)config(i,0);
 d.enable=1;d.space_ok=1;d.time_now=0;pend_all(true);
 unsigned popped=0;
 for(unsigned k=0;k<vls&&popped<4;++k){wait_grant(vls+8,"space gate grant");unsigned idx=d.grant_index;pop(idx,"space gate pop");++popped;}
 pend_all(true);d.space_ok=1;expect_no_grant(2*vls,"grant presented with four frames in flight");
 space_ok_gate=true;
}
static void scenario_window_edge(){
 std::uint64_t deadline=grant_setup(0,0,0,"window edge due setup");
 pend(0,true);d.time_now=deadline+0x7FFFFFFFFFFFFFFFULL; // elapsed = 2^63-1
 wait_grant(vls+4,"elapsed 2^63-1 should be due");need(d.grant_index==0,"window due VL");consume(0,d.time_now,"window due consume");
 deadline=grant_setup(0,0,0,"window edge future setup");
 pend(0,true);d.time_now=deadline+0x8000000000000000ULL; // elapsed = 2^63
 expect_no_grant(2*vls,"elapsed 2^63 should be outside window");
 d.time_now=deadline;wait_grant(vls+4,"deadline reachable again");need(d.grant_index==0,"window future VL");consume(0,deadline,"window future consume");
 window_edge=true;
}
static void scenario_reset_clears(){
 reset_dut();config(0,0);d.enable=1;pend(0,true);d.time_now=5000;
 wait_grant(vls+4,"reset setup");consume(0,5000,"reset setup consume");
 d.pending=1;d.time_now=105000;wait_grant(vls+4,"reset held grant");
 reset_dut();
 d.enable=1;pend(0,true);d.time_now=105000;expect_no_grant(3*vls,"reset did not clear configuration/deadline");
 reset_cleared=true;
}
int main(int argc,char**argv){
 try{
  Verilated::commandArgs(argc,argv);
  need(argc==3,"usage: scheduler64 <vl_count> <result.json>");
  vls=std::stoul(argv[1]);need(vls>=2,"vl_count too small");need(vls<=8,"unit test covers small VL counts");
  scenario_boundaries();
  scenario_long_idle_2p31();
  scenario_wrap_2p32();
  scenario_wrap_2p64();
  scenario_vl_isolation();
  scenario_pause_resume();
  scenario_long_idle_enqueue();
  scenario_config_boundaries();
  scenario_config_enabled_ignored();
  scenario_invalid_index();
  scenario_round_robin();
  scenario_held_and_anchor();
  scenario_multi_outstanding();
  scenario_window_edge();
  scenario_reset_clears();
  d.final();
  std::ofstream out(argv[2]);
  out<<"{\"status\":\"PASS\",\"vl_count\":"<<vls<<",\"checks\":"<<checks<<",\"cycles\":"<<cycles<<",\"grants\":"<<grants
   <<",\"time_source\":\"injected time_now values; no wall-clock or 2^31-cycle wait\",\"injected_time\":true"
   <<",\"coverage\":{\"boundary_deadline_minus_1\":"<<before_deadline<<",\"boundary_exact\":"<<exact_deadline<<",\"boundary_plus_1\":"<<after_deadline
   <<",\"idle_over_2p31\":true,\"wrap_2p32\":true,\"wrap_2p64\":true,\"vl_isolation\":true,\"pause_resume\":true"
   <<",\"long_idle_then_enqueue\":true,\"config_bag_exp_0_to_7\":true,\"config_reconfig\":true,\"config_ignored_while_enabled\":true"
   <<",\"invalid_cfg_index\":"<<(invalid_cfg?"true":"false")<<",\"round_robin_fairness\":true,\"held_grant\":true"
   <<",\"space_ok_gate\":\""<<(space_ok_gate?"true":"false")<<"\",\"multi_outstanding\":\""<<(multi_outstanding?"true":"false")
  <<"\",\"deadline_anchor_at_retire\":true,\"window_2p63_edge\":true,\"reset_clears\":true}}\n";
  std::cout<<"PASS scheduler64 VL="<<vls<<" checks="<<checks<<" cycles="<<cycles<<" grants="<<grants<<"\n";
  return 0;
 }catch(const std::exception&e){
  std::cerr<<"FAIL "<<e.what()<<" cycles="<<cycles<<" grants="<<grants<<"\n";
  return 1;
 }
}
