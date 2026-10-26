#include "Vrx_r2_probe.h"
#include "verilated.h"
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>
static Vrx_r2_probe d;static uint64_t cycles=0,checks=0;static unsigned episode=0,ic_count=0,rm_count=0;
static std::ofstream trace;static std::vector<unsigned> payload,delivered_sn;static std::vector<std::vector<unsigned>> packets;
static void need(bool b,const char*s){++checks;if(!b)throw std::runtime_error(s);}
static std::vector<unsigned> unhex(const std::string&s){std::vector<unsigned>b;for(size_t i=0;i<s.size();i+=2)b.push_back(std::stoul(s.substr(i,2),nullptr,16));return b;}
static void event(const char*layer,unsigned net,unsigned sn,unsigned pass,unsigned reason){trace<<"{\"episode\":"<<episode<<",\"cycle\":"<<cycles<<",\"layer\":\""<<layer<<"\",\"network\":"<<net<<",\"sn\":"<<sn<<",\"pass\":"<<pass<<",\"reason\":"<<reason<<"}\n";}
static void tick(){d.clk=0;d.time_now=cycles;d.eval();
 if(!d.reset){
 if(d.ic_fire){event("IC",d.ic_network,d.ic_sn,d.ic_pass,d.ic_reason);need(d.ic_pass&&d.ic_reason==0,"unexpected IC result");++ic_count;}
 if(d.m_valid&&d.m_ready){payload.push_back(d.m_data);if(d.m_last){need(payload.size()==d.m_len,"payload length");packets.push_back(payload);payload.clear();delivered_sn.push_back(d.m_sn);event("APP",d.m_network,d.m_sn,1,0);}}
 }
 d.clk=1;d.eval();if(!d.reset&&d.decision){event("RM",d.m_network,d.m_sn,d.decision_pass,d.decision_reason);need(d.decision_pass&&d.decision_reason==0,"unexpected RM result");++rm_count;}++cycles;}
static void idle(unsigned n,unsigned stuck=0){for(unsigned i=0;i<n;i++){d.byte_ce=3;d.line_valid=stuck;d.line_data=0x5555;for(int c=0;c<8;c++){tick();d.byte_ce=0;}}}
static void reset(bool arm){d.reset=1;d.byte_ce=0;d.line_valid=0;d.m_ready=1;tick();tick();d.reset=0;packets.clear();delivered_sn.clear();payload.clear();ic_count=0;rm_count=0;if(arm){idle(20);need(d.initialized,"arm timeout");}}
static uint64_t send(const std::vector<unsigned>&wire,unsigned net,unsigned stuck=0){uint64_t stamp=0;
 for(size_t i=0;i<wire.size();i++){d.byte_ce=3;d.line_valid=(1u<<net)|stuck;d.line_data=(wire[i]<<(8*net))|(net?0:0x5500);if(i==7)stamp=cycles;for(int c=0;c<8;c++){tick();d.byte_ce=0;}}
 idle(14,stuck);for(int i=0;i<300;i++)tick();return stamp;}
int main(int argc,char**argv){try{Verilated::commandArgs(argc,argv);need(argc==4,"usage vectors report trace");trace.open(argv[3]);need(trace.good(),"trace unavailable");std::ifstream f(argv[1]);need(f.good(),"vectors missing");std::string p0,p1,w0,w1;f>>p0>>w0>>p1>>w1;need(f.good(),"bad vectors");auto a=unhex(w0),b=unhex(w1);
 episode=1;reset(true);auto t0=send(a,0);auto t1=send(b,0);auto t2=send(a,1);
 need(t1>t0&&t2-t0<=100000,"late copy outside SkewMax");need(ic_count==3&&rm_count==3,"layer trace counts");
 need(delivered_sn==std::vector<unsigned>({42,43,42}),"late-copy SN observation changed");need(packets.size()==3&&packets[0]==unhex(p0)&&packets[1]==unhex(p1)&&packets[2]==packets[0],"late-copy payload observation changed");
 uint64_t late_span=t2-t0;unsigned duplicate_deliveries=1;
 // Episode 2: B valid is stuck high during startup; A has reached its idle boundary.
 episode=2;reset(false);idle(40,2);need(d.tables_initialized&&d.armed==1&&!d.initialized,"startup joint-arm limitation changed");
 send(a,0,2);idle(256,2);need(d.armed==1&&!d.initialized&&!d.m_valid&&!d.busy,"stuck B did not inhibit globally");need(packets.empty()&&ic_count==0&&rm_count==0&&d.mac_good_frames==0,"startup inhibited frame leaked");
 trace<<"{\"episode\":2,\"cycle\":"<<cycles<<",\"layer\":\"STARTUP\",\"tables_initialized\":true,\"armed_mask\":1,\"global_initialized\":false,\"a_frames_delivered\":0}\n";
 idle(11);need(!d.initialized,"armed before 12 idle byte enables");idle(1);need(d.initialized&&d.armed==3,"12-idle recovery failed");
 send(a,0);need(packets.size()==1&&packets[0]==unhex(p0)&&ic_count==1&&rm_count==1,"post-idle startup recovery failed");
 trace<<"{\"episode\":2,\"cycle\":"<<cycles<<",\"layer\":\"RECOVERY\",\"armed_mask\":3,\"a_frames_delivered\":1}\n";
 std::ofstream o(argv[2]);o<<"{\"status\":\"CHARACTERIZED\",\"compliance_status\":\"NOT_ESTABLISHED\",\"late_copy\":{\"sn_sequence\":[42,43,42],\"ic_pass\":[true,true,true],\"rm_pass\":[true,true,true],\"application_deliveries\":3,\"duplicate_deliveries\":"<<duplicate_deliveries<<",\"at_most_once_satisfied\":false,\"sfd_skew_cycles\":"<<late_span<<",\"skew_max\":100000},\"startup_stuck_valid\":{\"tables_initialized\":true,\"armed_mask_before_recovery\":1,\"global_initialized\":false,\"healthy_A_delivery_available\":false,\"A_good_frames_while_inhibited\":0,\"recovered_after_B_idle_bytes\":12},\"checks\":"<<checks<<",\"cycles\":"<<cycles<<"}\n";
 std::cout<<"CHARACTERIZED: late-copy DUPLICATE and stuck-valid GLOBAL INHIBIT observed; no compliance PASS\n";return 0;
 }catch(const std::exception&e){std::cerr<<"FAIL characterization "<<e.what()<<"\n";return 1;}}
