#include "Vrx_r2_probe.h"
#include "verilated.h"
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>
static bool g_on=false;static unsigned late_rejects=0;
static std::vector<uint64_t> stamps;static std::vector<unsigned> networks;
static Vrx_r2_probe d;static uint64_t cycles=0,checks=0;static unsigned episode=0,ic_count=0,rm_count=0;
static std::ofstream trace;static std::vector<unsigned> payload,delivered_sn;static std::vector<std::vector<unsigned>> packets;
static void need(bool b,const char*s){++checks;if(!b)throw std::runtime_error(s);}
static std::vector<unsigned> unhex(const std::string&s){std::vector<unsigned>b;for(size_t i=0;i<s.size();i+=2)b.push_back(std::stoul(s.substr(i,2),nullptr,16));return b;}
static void event(const char*layer,unsigned net,unsigned sn,unsigned pass,unsigned reason){trace<<"{\"episode\":"<<episode<<",\"cycle\":"<<cycles<<",\"layer\":\""<<layer<<"\",\"network\":"<<net<<",\"sn\":"<<sn<<",\"pass\":"<<pass<<",\"reason\":"<<reason<<"}\n";}
static void tick(){d.clk=0;d.time_now=cycles;d.eval();
 if(!d.reset){
 if(d.ic_fire){event("IC",d.ic_network,d.ic_sn,d.ic_pass,d.ic_reason);need(d.ic_pass&&d.ic_reason==0,"unexpected IC result");++ic_count;}
 if(d.m_valid&&d.m_ready){payload.push_back(d.m_data);if(d.m_last){need(payload.size()==d.m_len,"payload length");packets.push_back(payload);stamps.push_back(d.m_time);networks.push_back(d.m_network);payload.clear();delivered_sn.push_back(d.m_sn);event("APP",d.m_network,d.m_sn,1,0);}}
 }
 d.clk=1;d.eval();if(!d.reset&&d.decision){event("RM",d.m_network,d.m_sn,d.decision_pass,d.decision_reason);
  // Legacy profile: every startup decision must be an acceptance. Expected-SN
  // profile: a recovered peer's old SN is a late copy (reason 4) and must not be
  // delivered; nothing else may be rejected.
  if(g_on){if(d.decision_pass){}else if(d.decision_reason==4)++late_rejects;else need(false,"unexpected RM reject under expected-SN");}
  else need(d.decision_pass&&d.decision_reason==0,"unexpected RM result");++rm_count;}++cycles;}
static void idle(unsigned n,unsigned stuck=0){for(unsigned i=0;i<n;i++){d.byte_ce=3;d.line_valid=stuck;d.line_data=0x5555;for(int c=0;c<8;c++){tick();d.byte_ce=0;}}}
static void reset(bool arm){d.reset=1;d.byte_ce=0;d.line_valid=0;d.m_ready=1;tick();tick();d.reset=0;packets.clear();stamps.clear();networks.clear();delivered_sn.clear();payload.clear();ic_count=0;rm_count=0;if(arm){idle(20);need(d.initialized,"arm timeout");}}
static uint64_t send(const std::vector<unsigned>&wire,unsigned net,unsigned stuck=0){uint64_t stamp=0;
 for(size_t i=0;i<wire.size();i++){d.byte_ce=3;d.line_valid=(1u<<net)|stuck;d.line_data=(wire[i]<<(8*net))|(net?0:0x5500);if(i==7)stamp=cycles;for(int c=0;c<8;c++){tick();d.byte_ce=0;}}
 idle(14,stuck);for(int i=0;i<300;i++)tick();return stamp;}

// A and B enables deliberately have different phases; neither is backpressurable.
static void skew_idle(unsigned count,unsigned stuck=0){
 for(unsigned i=0;i<count;i++){d.byte_ce=((cycles%8==0)?1:0)|((cycles%8==3)?2:0);d.line_valid=stuck;d.line_data=0x5555;tick();}
}
static uint64_t skew_send(const std::vector<unsigned>&wire,unsigned net,unsigned stuck=0){
 size_t index=0;uint64_t stamp=0;const unsigned phase=net?3:0;
 while(index<wire.size()){
  d.byte_ce=((cycles%8==0)?1:0)|((cycles%8==3)?2:0);
  d.line_valid=stuck|(1u<<net);
  d.line_data=(d.line_data & (net?0x00ff:0xff00))|(wire[index]<<(8*net));
  if(cycles%8==phase){if(index==7)stamp=cycles;tick();++index;}else tick();
 }
 skew_idle(160,stuck);return stamp;
}
int main(int argc,char**argv){try{
 Verilated::commandArgs(argc,argv);need(argc==4,"vectors result trace");trace.open(argv[3]);
  const char* env=getenv("RM_EXPECTED_SN");g_on=env&&*env&&std::atoi(env)!=0;
 std::ifstream f(argv[1]);std::string p0,w0,p1,w1;f>>p0>>w0>>p1>>w1;need(f.good(),"vectors missing");
 auto a=unhex(w0),b=unhex(w1);unsigned recovered=0,canceled=0,accepted=0,completed=0;
 for(unsigned healthy=0;healthy<2;healthy++){
  episode=healthy+1;reset(false);const unsigned stuck=1u<<(1-healthy),mask=1u<<healthy;
  // No byte enables means no false idle-boundary recognition.
  for(unsigned i=0;i<40;i++){d.byte_ce=0;d.line_valid=stuck;tick();}
  need(d.tables_initialized&&!d.armed&&!d.initialized,"armed without byte samples");
  skew_idle(320,stuck);need(d.armed==mask&&d.initialized,"healthy lane service blocked by stuck peer");
  auto t=skew_send(a,healthy,stuck);skew_idle(600,stuck);
  need(packets.size()==1&&packets[0]==unhex(p0)&&networks[0]==healthy&&stamps[0]==t,"healthy lane payload/time failed");
  // Peer holds valid throughout another complete healthy frame; application stalls.
  d.m_ready=0;auto t1=skew_send(b,healthy,stuck);skew_idle(400,stuck);
  need(d.m_valid&&packets.size()==1&&d.m_time==t1,"pending delivery not held");
  auto data=d.m_data;auto len=d.m_len;auto sn=d.m_sn;auto stamp=d.m_time;
  for(unsigned i=0;i<64;i++){skew_idle(1,stuck);need(d.m_valid&&d.m_data==data&&d.m_len==len&&d.m_sn==sn&&d.m_time==stamp,"backpressure changed payload metadata");}
  d.m_ready=1;skew_idle(800,stuck);need(packets.size()==2&&packets[1]==unhex(p1)&&stamps[1]==t1,"stalled delivery failed");
  // The failed peer has had no idle samples; 11 samples cannot rearm it.
  for(unsigned n=0;n<11;n++){d.byte_ce=stuck;d.line_valid=0;tick();d.byte_ce=0;tick();}
  need(d.armed==mask&&d.initialized,"peer armed before full idle boundary");
  d.byte_ce=stuck;tick();d.byte_ce=0;tick();need(d.armed==3&&d.initialized,"peer 12-idle recovery failed");
  // Peer is now a valid independent source. Its SN42 was not previously observed.
  const unsigned late_before=late_rejects;
  auto t2=skew_send(a,1-healthy);skew_idle(800);
  if(g_on){need(packets.size()==2,"expected-SN: recovered peer late copy must be rejected");need(late_rejects==late_before+1,"expected-SN: late reject count");}
  else need(packets.size()==3&&packets[2]==unhex(p0)&&networks[2]==1-healthy&&stamps[2]==t2,"recovered peer delivery failed");need(rm_count==3,"recovery decision count");accepted+=rm_count;completed+=packets.size();++recovered;
  // Cancel an already accepted application frame under backpressure.
  reset(false);skew_idle(320,stuck);d.m_ready=0;skew_send(a,healthy,stuck);skew_idle(400,stuck);need(d.m_valid,"cancel test frame absent");
  need(rm_count==1&&packets.empty(),"pending cancellation accounting");accepted+=rm_count;
  reset(false);++canceled;need(!d.m_valid&&!d.armed&&!d.initialized,"reset leaked stale ownership");
  skew_idle(320,stuck);skew_send(a,healthy,stuck);skew_idle(800,stuck);
  need(packets.size()==1&&packets[0]==unhex(p0)&&rm_count==1,"post-reset startup recovery failed");accepted+=rm_count;completed+=packets.size();
 }
 need(accepted==completed+canceled+(g_on?late_rejects:0),"accepted/delivered/reset-canceled conservation");
 std::ofstream o(argv[2]);o<<"{\"status\":\"PASS\",\"rm_expected_sn\":"<<(g_on?1:0)<<",\"late_copy_rejects\":"<<late_rejects<<",\"healthy_lanes_tested\":2,\"peer_recoveries\":"<<recovered<<",\"accepted_decisions\":"<<accepted<<",\"delivered\":"<<completed<<",\"reset_canceled_pending_frames\":"<<canceled<<",\"inflight\":0,\"byte_enables\":\"independent phases A0/B3 mod8\",\"checks\":"<<checks<<",\"cycles\":"<<cycles<<",\"rm_late_copy_closed\":false,\"timing_verified\":false}\n";
 std::cout<<"PASS independent startup, healthy service, stalled output, recovery and reset\n";return 0;
}catch(const std::exception&e){std::cerr<<"FAIL startup "<<e.what()<<" cycle="<<cycles<<"\n";return 1;}}
