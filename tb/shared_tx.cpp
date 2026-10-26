#include "Vafdx_shared_tx.h"
#include "verilated.h"
#include <array>
#include <cstdint>
#include <deque>
#include <fstream>
#include <iostream>
#include <random>
#include <stdexcept>
#include <string>
#include <vector>
using Bytes=std::vector<unsigned char>;
struct Frame{unsigned vl;Bytes payload,wire;};
static Vafdx_shared_tx d;
static std::vector<std::deque<Frame>> expected;
static std::vector<std::uint64_t> last_sfd;
static std::vector<bool> seen_sfd;
static std::vector<int> last_sn;
static std::vector<bool> wire_vls;
static std::mt19937 rng(20261021);
static unsigned vls,slots,frames=0,sfds=0,enqueued=0,rejected=0,dropped=0,reset_canceled=0,wrap_sn_events=0,unconfigured_rejected=0;
static unsigned long long checks=0,cycles=0;
static Bytes current;static std::uint64_t byte_time=0,end_time=0,current_sfd=0;
static unsigned active_vl=0;static bool pause_test=false,pause_applied=false,low_wrap=false;
static std::uint64_t pause_end=0;
static void need(bool ok,const char*s){++checks;if(!ok)throw std::runtime_error(s);}
static Bytes decode(const std::string&s){Bytes b;for(size_t i=0;i<s.size();i+=2)b.push_back((unsigned char)std::stoul(s.substr(i,2),nullptr,16));return b;}
static void step(){
 if(pause_applied&&cycles>=pause_end&&pause_test)d.run=1;
 d.clk=0;d.eval();bool physical_sfd=false;
 if(!d.reset){
  if(d.line_valid&&d.byte_tick){
   if(current.empty()){if(end_time)need(d.monotonic_time-end_time>=104,"shared TX IFG violation");}
   else need(d.monotonic_time-byte_time==8,"shared TX byte cadence violation");
   if(current.size()==7){need(d.line_data==0xd5,"missing SFD byte");physical_sfd=true;current_sfd=d.monotonic_time;}
   current.push_back(d.line_data);byte_time=d.monotonic_time;
   if(pause_test&&!pause_applied){pause_applied=true;pause_end=cycles+1500;d.run=0;d.eval();}
  }
  if(!d.line_valid&&!current.empty()){
   need(current.size()>=72,"shared TX truncated frame");unsigned vl=current[13]-1;
   need(vl<vls&&vl==active_vl&&!expected[vl].empty(),"shared TX wrong VL or unsolicited frame");
   need(current==expected[vl].front().wire,"shared TX differs from independent UDP/IP/FCS golden bytes");
   unsigned sn=current[current.size()-5];if(sn==1&&last_sn[vl]==255)++wrap_sn_events;last_sn[vl]=sn;
   expected[vl].pop_front();wire_vls[vl]=true;current.clear();end_time=byte_time;++frames;
  }
 }
 d.clk=1;d.eval();++cycles;
 if(!d.reset){
  need(!d.fault,"shared TX internal ownership/encoder/MAC fault");
  if(d.sfd_pulse){
   need(physical_sfd&&d.sfd_time==current_sfd,"shared TX SFD timestamp mismatch");active_vl=d.sfd_vl;need(active_vl<vls,"SFD VL out of range");
   unsigned exp=active_vl==0?0:active_vl==1?1:active_vl==vls/2?2:active_vl==vls-1?3:0;
   if(seen_sfd[active_vl])need(d.sfd_time-last_sfd[active_vl]>=(100000ULL<<exp),"shared TX actual SFD BAG violation");
   last_sfd[active_vl]=d.sfd_time;seen_sfd[active_vl]=true;++sfds;
   if(pause_test&&cycles<pause_end)need(sfds==1,"new frame started during run pause");
  }else need(!physical_sfd,"physical SFD without timestamp pulse");
  if(d.monotonic_time>0xffffffffULL)low_wrap=true;
 }
}
static void reset(){
 for(auto&q:expected){reset_canceled+=q.size();q.clear();}current.clear();end_time=0;seen_sfd.assign(vls,false);last_sn.assign(vls,-1);pause_test=false;pause_applied=false;
 d.reset=1;d.run=0;d.app_enable=0;d.cfg_valid=0;d.app_msg_valid=0;d.app_valid=0;d.app_rsp_ready=0;d.drop_valid=0;d.drop_rsp_ready=0;step();step();d.reset=0;
 unsigned n=0;while(!d.initialized){step();need(++n<vls+slots+20,"shared TX initialization timeout");}
 need(d.monotonic_time>=0xffff0000ULL,"initial time parameter lost");
}
static void configure(){
 d.app_enable=0;d.run=0;d.clk=0;d.eval();
 for(unsigned i=0;i<vls;++i){d.cfg_valid=1;d.cfg_vl=i;d.cfg_bag_exp=i==1?1:i==vls/2?2:i==vls-1?3:0;unsigned n=0;while(true){d.clk=0;d.eval();if(d.cfg_ready)break;step();need(++n<16,"idle shared TX configuration not ready");}step();}d.cfg_valid=0;
}
static unsigned enqueue(const Frame&f,unsigned mode=0){
 d.app_enable=1;d.app_msg_valid=1;d.app_vl=f.vl;d.app_length=f.payload.size();unsigned n=0;
 while(true){d.clk=0;d.eval();bool accept=d.app_msg_ready;step();if(accept)break;need(++n<2000000,"app descriptor admission timeout");}
 d.app_msg_valid=0;d.app_vl=(f.vl+1)%vls;d.app_length=0;d.eval();unsigned pos=0;bool offering=false;
 unsigned wire_length=mode==1?f.payload.size()/2:f.payload.size();n=0;
 while(!d.app_rsp_valid){
  if(!offering&&pos<wire_length&&d.app_ready&&rng()%5!=0)offering=true;
  d.app_valid=offering;d.app_data=pos<f.payload.size()?f.payload[pos]:0;d.app_last=mode==2?0:pos+1==wire_length;
  d.clk=0;d.eval();bool byte=d.app_valid&&d.app_ready;step();if(byte){++pos;offering=false;}
  need(++n<f.payload.size()*10+slots*3+200,"app enqueue completion timeout");
 }
 d.app_valid=0;unsigned status=d.app_rsp_status;
 if(status==0){need(pos==f.payload.size(),"successful message consumed wrong byte count");expected[f.vl].push_back(f);++enqueued;}else{need(status==2||status==4||status==5||status==7,"unexpected app enqueue error");if(status==7){need(pos==0&&!d.app_ready,"unconfigured rejection consumed input");++unconfigured_rejected;}++rejected;}
 for(unsigned k=rng()%7;k;--k){step();need(d.app_rsp_valid&&d.app_rsp_status==status&&!d.app_msg_ready,"app response changed under backpressure");}
 d.app_rsp_ready=1;step();d.app_rsp_ready=0;return status;
}
static unsigned drop(unsigned vl){
 d.drop_valid=1;d.drop_vl=vl;unsigned n=0;
 while(true){d.clk=0;d.eval();bool accept=d.drop_ready;step();if(accept)break;need(++n<2000000,"drop admission timeout");}d.drop_valid=0;
 while(!d.drop_rsp_valid){step();need(++n<2000000,"drop completion timeout");}
 unsigned status=d.drop_rsp_status;need(status==0||status==1,"drop ownership error");
 if(status==0){need(!expected[vl].empty(),"dropped an unknown descriptor");expected[vl].pop_front();++dropped;}
 for(unsigned k=0;k<5;++k){step();need(d.drop_rsp_valid&&d.drop_rsp_status==status,"drop response changed under backpressure");}
 d.drop_rsp_ready=1;step();d.drop_rsp_ready=0;return status;
}
static void drain(unsigned target){unsigned n=0;while(frames<target||d.busy){step();need(++n<40000000,"shared TX drain timeout");}need(d.pool_free==slots,"shared TX leaked payload slots");for(const auto&q:expected)need(q.empty(),"shared TX lost a queued frame");}
int main(int argc,char**argv){try{
 Verilated::commandArgs(argc,argv);need(argc==5,"usage vls slots vectors result");vls=std::stoul(argv[1]);slots=std::stoul(argv[2]);expected.resize(vls);last_sfd.resize(vls);seen_sfd.resize(vls);wire_vls.assign(vls,false);
 std::ifstream input(argv[3]);std::vector<Frame>a,b,c,repeat,r;char phase;unsigned vl;std::string payload,wire;
 while(input>>phase>>vl>>payload>>wire){Frame f{vl,decode(payload),decode(wire)};(phase=='A'?a:phase=='B'?b:phase=='C'?c:phase=='D'?repeat:r).push_back(f);}
 need(a.size()==16&&r.size()==1&&repeat.size()==1,"incomplete shared TX golden corpus");reset();
 // Every configuration bit resets clear. Rejection must leave the pool empty.
 for(unsigned index=0;index<vls;++index){Frame unknown{index,Bytes(17,0x61),{}};need(enqueue(unknown)==7,"unconfigured VL accepted");need(d.pool_free==slots&&!d.busy,"unconfigured rejection allocated or queued");}
 // Illegal length retains status 4 even when its VL is not configured.
 Frame zero{0,Bytes(),{}};need(enqueue(zero)==4,"length status precedence changed");
 d.app_enable=0;d.run=0;d.cfg_valid=1;d.cfg_vl=0;d.cfg_bag_exp=0;{unsigned n=0;while(true){d.clk=0;d.eval();if(d.cfg_ready)break;step();need(++n<16,"single-VL configuration not ready");}}step();step();d.cfg_valid=0;
 // run toggles preserve configuration; active configuration cannot set a new VL.
 d.run=1;for(unsigned i=0;i<32;++i)step();d.run=0;
 d.app_enable=1;d.cfg_valid=1;d.cfg_vl=vls-1;d.cfg_bag_exp=3;d.clk=0;d.eval();need(!d.cfg_ready&&d.cfg_reject,"active config was accepted");step();d.cfg_valid=0;
 Frame unknown{vls-1,Bytes(17,0x71),{}};need(enqueue(unknown)==7,"rejected config changed configured state");
 need(enqueue(a[0])==0,"configured VL rejected after run toggle");need(drop(0)==0,"configuration preface drop failed");configure();
 need(drop(0)==1,"empty drop should report EMPTY");
 Frame invalid{0,Bytes(0),{}};need(enqueue(invalid)==4,"zero length was not rejected");invalid.payload.resize(1472);need(enqueue(invalid)==4,"oversized AFDX payload was not rejected");invalid.payload.resize(15);need(enqueue(invalid,1)==5,"early last did not roll back");need(enqueue(invalid,2)==5,"missing last did not roll back");
 for(const auto&f:a)need(enqueue(f)==0,"prefill failed");
 invalid.vl=1;need(enqueue(invalid)==2,"per-VL/full-pool rejection failed");need(drop(0)==0,"queued drop failed");
 d.cfg_valid=1;d.cfg_vl=0;d.cfg_bag_exp=7;d.clk=0;d.eval();need(!d.cfg_ready&&d.cfg_reject,"active configuration not rejected");step();d.cfg_valid=0;
 d.app_enable=0;pause_test=true;d.run=1;drain(15);need(pause_applied,"run pause not exercised");pause_test=false;
 for(const auto&f:b){while(enqueue(f)==2){unsigned previous=frames;while(frames==previous)step();}}
 d.app_enable=0;drain(15+b.size());need(low_wrap,"32-bit time wrap not exercised");
 for(const auto&f:c){while(enqueue(f)==2){unsigned previous=frames;while(frames==previous)step();}}
 d.app_enable=0;drain(15+b.size()+c.size());
 // Submit immediately after draining; hardware must withhold an unsafe
 // handshake. Keep the real-SFD checker continuous across reconfiguration.
 d.run=0;d.app_enable=0;d.cfg_valid=1;d.cfg_vl=0;d.cfg_bag_exp=0;
 unsigned config_wait=0;
 while(true){d.clk=0;d.eval();bool accepted=d.cfg_ready;step();if(accepted)break;need(++config_wait<200000,"guarded reconfiguration timeout");}
 step();d.cfg_valid=0;
 need(enqueue(repeat[0])==0,"reconfigured VL enqueue failed");d.app_enable=0;d.run=1;drain(16+b.size()+c.size());
 // Partial application input is canceled by common reset before publication.
 d.run=0;d.app_enable=1;d.app_msg_valid=1;d.app_vl=0;d.app_length=20;
 while(true){d.clk=0;d.eval();bool accept=d.app_msg_ready;step();if(accept)break;}d.app_msg_valid=0;while(!d.app_ready)step();d.app_valid=1;d.app_data=0x33;d.app_last=0;step();step();reset();
 need(enqueue(r[0])==7,"reset did not revoke VL configuration");need(d.pool_free==slots&&!d.busy,"reset rejection leaked state");configure();
 // Reset during a real physical frame also clears another queued descriptor.
 Frame aborted{0,Bytes(1471,0x51),{}};need(enqueue(aborted)==0,"reset prefill failed");need(enqueue(r[0])==0,"reset backlog failed");d.app_enable=0;d.run=1;while(current.size()<20)step();reset();
 need(enqueue(r[0])==7,"wire reset retained configuration");configure();
 need(enqueue(r[0])==0,"post-reset enqueue failed");d.app_enable=0;d.run=1;unsigned target=frames+1;drain(target);
 need(enqueued==frames+dropped+reset_canceled,"TX enqueue/delivery/drop/reset accounting mismatch");
 if(vls==4)need(wrap_sn_events>=1,"normal SN 255-to-1 transition not observed");
 for(bool covered:wire_vls)need(covered,"a configured VL never reached the real wire");
 d.final();std::ofstream out(argv[4]);out<<"{\"status\":\"PASS\",\"vl_count\":"<<vls<<",\"slots\":"<<slots<<",\"wire_vl_coverage\":"<<vls<<",\"frames\":"<<frames<<",\"sfd_events\":"<<sfds<<",\"enqueued\":"<<enqueued<<",\"dropped\":"<<dropped<<",\"reset_canceled\":"<<reset_canceled<<",\"rejected\":"<<rejected<<",\"unconfigured_rejected\":"<<unconfigured_rejected<<",\"checks\":"<<checks<<",\"cycles\":"<<cycles<<",\"sn_wrap_events\":"<<wrap_sn_events<<",\"low32_wrap\":true,\"sequence_wrap_exercised\":"<<(vls==4?"true":"false")<<"}\n";
 std::cout<<"PASS shared-pool physical TX frames="<<frames<<" checks="<<checks<<"\n";return 0;
}catch(const std::exception&e){std::cerr<<"FAIL "<<e.what()<<" cycles="<<cycles<<" frames="<<frames<<"\n";return 1;}}
