#include "Vafdx_rx_dual.h"
#include "verilated.h"
#include <fstream>
#include <iostream>
#include <map>
#include <stdexcept>
#include <string>
#include <sstream>
#include <vector>
// Concurrent RX acceptance suite (fix A: test-only; RTL unchanged).
//
// mode 0 = COMPLIANCE: same-VL frames spaced by a legal AFDX BAG (100000 cycles =
//   12500 byte slots, the value enforced by tb/system_loopback.cpp), readiness
//   on/off and optional network skew. Every frame must be delivered exactly once
//   in SN order, with zero drops, zero IC-sequence rejects, exact payload bytes
//   and exact SFD timestamps.
// mode 1 = CAPACITY CHARACTERISATION: line-rate frame trains (12-byte IFG) that
//   exceed the legal BAG. Whole-frame drops are allowed and must account exactly
//   (per network: full_drops + delivered + IC-sequence rejects + RM duplicates ==
//   frames sent). Duplicate/unknown delivery, payload/timestamp corruption and
//   accounting mismatch stay fatal.
// mode 2 = L01 CHARACTERISATION: same-VL frames close enough that a late copy of
//   an older SN arrives after an intervening accepted SN; expected to reproduce
//   the documented L01 late-copy re-delivery (payload/timestamp still checked).
static Vafdx_rx_dual d;
static uint64_t cycles=0,checks=0;
static const uint64_t TIME_BASE=0xfffffffffffff000ULL;
static void need(bool x,const char*s){++checks;if(!x)throw std::runtime_error(s);}
static std::vector<unsigned> unhex(const std::string&s){std::vector<unsigned> v;for(size_t i=0;i<s.size();i+=2)v.push_back(std::stoul(s.substr(i,2),nullptr,16));return v;}
struct Frame{unsigned vl,sn;std::vector<unsigned> payload,wire;uint64_t stamp[2]={0,0};};
static uint64_t signature(){return d.m_data|(uint64_t(d.m_last)<<8)|(uint64_t(d.m_vl)<<9)|(uint64_t(d.m_sn)<<12)|(uint64_t(d.m_len)<<20)|(uint64_t(d.m_network)<<36);}
static std::vector<Frame> frames;
static std::map<unsigned,unsigned> pending;
static std::map<unsigned,unsigned> all_frames;
static std::vector<unsigned> packet;
static unsigned g_mode=0,next_sn[8];
static unsigned delivered=0,delivered_net[2]={0,0};
static unsigned passed=0,passed_net[2]={0,0};
static unsigned rej2=0,rej2_net[2]={0,0};
static unsigned rej3=0,rej3_net[2]={0,0};
static unsigned rej4=0,rej4_net[2]={0,0},rej5=0,rej5_net[2]={0,0};
static unsigned order_violations=0,late_copies=0,unknown_deliveries=0,late_net[2]={0,0};
static std::string first_late;static uint64_t first_late_cycle=0;
static bool held=false;static uint64_t held_sig=0,held_stamp=0;
static void tick(){
 d.clk=0;d.time_now=TIME_BASE+cycles;d.eval();
 if(!d.reset){
  if(held)need(d.m_valid&&signature()==held_sig&&d.m_time==held_stamp,"stalled output changed");
  held=d.m_valid&&!d.m_ready;held_sig=signature();held_stamp=d.m_time;
  if(d.m_valid&&d.m_ready){
   unsigned key=(unsigned(d.m_vl)<<8)|d.m_sn;
   auto it=pending.find(key);
   if(it==pending.end()){
    auto af=all_frames.find(key);
    need(af!=all_frames.end(),"unknown VL/SN delivery");
    Frame &f=frames[af->second];
    need(d.m_len==f.payload.size(),"late copy wrong payload length");
    need(d.m_time==f.stamp[d.m_network],"late copy timestamp does not belong to the frame");
    packet.push_back(d.m_data);
    if(d.m_last){need(packet==f.payload,"late copy payload differs from golden bytes");packet.clear();
     late_copies++;late_net[d.m_network&1u]++;if(first_late.empty()){first_late_cycle=cycles;std::ostringstream os;os<<"vl="<<unsigned(d.m_vl)<<" sn="<<unsigned(d.m_sn)<<" net="<<unsigned(d.m_network)<<" cycle="<<cycles;first_late=os.str();}
     need(g_mode==2,"duplicate delivery outside the L01 characterisation");}
   }else{
    Frame &f=frames[it->second];
    need(d.initialized&&d.m_len==f.payload.size(),"unready delivery or wrong payload length");
    need(d.m_time==f.stamp[d.m_network],"SOP/SFD timestamp does not belong to delivered frame");
    packet.push_back(d.m_data);
    if(d.m_last){need(packet==f.payload,"payload differs from independent golden bytes");packet.clear();
     if(d.m_sn!=next_sn[d.m_vl]){order_violations++;
      if(g_mode==0)need(false,"same-VL order violation in the compliance scenario");
      next_sn[d.m_vl]=d.m_sn;}
     next_sn[d.m_vl]++;pending.erase(it);delivered++;delivered_net[d.m_network]++;}
   }
  }
 }else held=false;
 d.clk=1;d.eval();
 if(!d.reset&&d.decision){
  unsigned net=unsigned(d.m_network)&1u;
  if(d.decision_pass){passed++;passed_net[net]++;}
  else if(d.decision_reason==2){rej2++;rej2_net[net]++;}
  else if(d.decision_reason==3){rej3++;rej3_net[net]++;}
  else if(d.decision_reason==4){rej4++;rej4_net[net]++;}   // expected-SN: late copy
  else if(d.decision_reason==5){rej5++;rej5_net[net]++;}   // expected-SN: gap drop
  else need(false,"unexpected decision reason (IC basic-invalid should be impossible)");
 }
 cycles++;
}
static void reset(){
 d.reset=1;d.byte_ce=0;d.line_valid=0;d.line_error=0;d.m_ready=1;tick();tick();d.reset=0;
 for(unsigned i=0;i<256;i++){d.byte_ce=((cycles%8==0)?1:0)|((cycles%8==3)?2:0);tick();}
 need(d.initialized,"startup not ready");d.byte_ce=0;
 delivered=0;delivered_net[0]=delivered_net[1]=0;passed=0;passed_net[0]=passed_net[1]=0;
 rej2=0;rej2_net[0]=rej2_net[1]=0;rej3=0;rej3_net[0]=rej3_net[1]=0;
 rej4=0;rej4_net[0]=rej4_net[1]=0;rej5=0;rej5_net[0]=rej5_net[1]=0;
 order_violations=0;late_copies=0;late_net[0]=late_net[1]=0;unknown_deliveries=0;first_late.clear();first_late_cycle=0;
 packet.clear();pending.clear();for(auto &s:next_sn)s=0;
 // Align every scenario start to a byte_ce phase boundary so results do not
 // depend on how many cycles earlier scenarios consumed (deterministic suite).
 d.byte_ce=0;d.line_valid=0;d.line_error=0;while(cycles%8)tick();
}
int main(int argc,char**argv){try{
 Verilated::commandArgs(argc,argv);need(argc==3,"cases report");std::ifstream in(argv[1]);need(in.good(),"vectors missing");
 unsigned scenario_count,n,b_skew,ready_mode,gap_bytes;std::string name;in>>scenario_count;
 std::ostringstream json;
 json<<"{\"timing_verified\":false,\"line_rate_signoff\":false,\"rm_late_copy_closed\":false,\"scenarios\":[";
 unsigned compliant_pass=0,compliant_runs=0,capacity_runs=0,l01_runs=0,l01_reproduced=0;std::string first_failure;
 for(unsigned scenario=0;scenario<scenario_count;scenario++){
  in>>name>>g_mode>>n>>b_skew>>ready_mode>>gap_bytes;need(in.good(),"scenario header missing");frames.clear();all_frames.clear();
  for(unsigned i=0;i<n;i++){Frame f;std::string payload,wire;in>>f.vl>>f.sn>>payload>>wire;need(in.good(),"frame missing");f.payload=unhex(payload);f.wire=unhex(wire);frames.push_back(f);}
  reset();
  for(unsigned i=0;i<n;i++){unsigned key=(frames[i].vl<<8)|frames[i].sn;
   need(pending.emplace(key,i).second,"repeated golden key");need(all_frames.emplace(key,i).second,"repeated golden key");}
  unsigned frame_index[2]={0,0},offset[2]={0,0},gap[2]={0,b_skew};bool finished[2]={false,false};
  uint64_t start=cycles;unsigned drain=0;
  while(drain<4096){
   d.byte_ce=0;
   for(unsigned net=0;net<2;net++)if(cycles%8==(net?3:0)){
    d.byte_ce|=1u<<net;
    if(gap[net]){d.line_valid&=~(1u<<net);gap[net]--;}
    else if(frame_index[net]<n){
     auto &f=frames[frame_index[net]];d.line_valid|=1u<<net;
     d.line_data=(d.line_data&(net?0x00ff:0xff00))|(f.wire[offset[net]]<<(8*net));
     if(offset[net]==7)f.stamp[net]=TIME_BASE+cycles;
     if(++offset[net]==f.wire.size()){offset[net]=0;frame_index[net]++;gap[net]=gap_bytes;}
    }else{d.line_valid&=~(1u<<net);finished[net]=true;}
   }
   d.m_ready=ready_mode==0?1:((cycles-start)%97>=19);
   tick();if(finished[0]&&finished[1])drain++;
   need(cycles-start<6000000,"scenario timeout");
  }
  unsigned fd0=(unsigned)(d.full_drops&0xffffffffu),fd1=(unsigned)(d.full_drops>>32);
  unsigned missing=n-delivered;
  need(unknown_deliveries==0,"unknown VL/SN delivery");
  // Every physical frame is either dropped whole by the MAC, delivered once (first
  // copy or a documented L01 late copy), IC-sequence rejected, or RM-duplicate rejected.
  bool acc=(fd0+delivered_net[0]+late_net[0]+rej2_net[0]+rej3_net[0]+rej4_net[0]+rej5_net[0]==n)&&
           (fd1+delivered_net[1]+late_net[1]+rej2_net[1]+rej3_net[1]+rej4_net[1]+rej5_net[1]==n);
  bool desc_acc=(passed+rej2+rej3+rej4+rej5==2*n-(fd0+fd1));
  need(acc&&desc_acc,"descriptor/drop accounting mismatch");
  std::string status;
  if(g_mode==0){
   compliant_runs++;
   bool ok=missing==0&&order_violations==0&&rej2==0&&fd0==0&&fd1==0&&late_copies==0&&pending.empty()&&packet.empty();
   status=ok?"PASS":"FAIL_COMPLIANCE";if(ok)compliant_pass++;else if(first_failure.empty())first_failure=name;
  }else if(g_mode==1){capacity_runs++;status="CHARACTERIZED_CAPACITY_LIMIT";}
  else{l01_runs++;if(late_copies>0){l01_reproduced++;status="L01_REPRODUCED";}else status="L01_NOT_REPRODUCED";}
  std::cout<<name<<" mode="<<g_mode<<" status="<<status<<" delivered="<<delivered<<"/"<<n
   <<" passed="<<passed<<" ic_sequence_rej="<<rej2<<" rm_dup_rej="<<rej3<<" late_rej="<<rej4<<" gap_rej="<<rej5
   <<" order_violations="<<order_violations<<" late_copies="<<late_copies<<" missing="<<missing
   <<" full_drops_net0="<<fd0<<" full_drops_net1="<<fd1<<" bad_fcs="<<(unsigned)d.bad_fcs
   <<" cycles="<<cycles<<"\n";
  if(!first_late.empty())std::cout<<"FIRST_LATE_COPY "<<first_late<<"\n";
  if(scenario)json<<",";
  json<<"{\"name\":\""<<name<<"\",\"mode\":"<<g_mode<<",\"status\":\""<<status<<"\""
   <<",\"frames_sent\":"<<n<<",\"delivered\":"<<delivered<<",\"passed\":"<<passed
   <<",\"ic_sequence_rejects\":"<<rej2<<",\"rm_duplicate_rejects\":"<<rej3<<",\"late_rejects\":"<<rej4<<",\"gap_rejects\":"<<rej5
   <<",\"order_violations\":"<<order_violations<<",\"late_copies\":"<<late_copies
   <<",\"missing\":"<<missing<<",\"full_drops_net0\":"<<fd0<<",\"full_drops_net1\":"<<fd1
   <<",\"bad_fcs\":"<<(unsigned)d.bad_fcs<<",\"gap_bytes\":"<<gap_bytes
   <<",\"skew_bytes\":"<<b_skew<<",\"ready_mode\":"<<ready_mode<<",\"cycles\":"<<cycles<<"}";
 }
 json<<"],\"checks\":"<<checks<<",\"total_cycles\":"<<cycles
  <<",\"compliant_runs\":"<<compliant_runs<<",\"compliant_pass\":"<<compliant_pass
  <<",\"capacity_runs\":"<<capacity_runs<<",\"l01_runs\":"<<l01_runs
  <<",\"l01_reproduced\":"<<l01_reproduced<<",\"first_failure\":\""<<first_failure<<"\"}"
  <<"\n";
 std::ofstream out(argv[2]);out<<json.str();
 std::cout<<"SUMMARY compliant_pass="<<compliant_pass<<"/"<<compliant_runs<<" capacity_runs="<<capacity_runs
  <<" l01_runs="<<l01_runs<<" l01_reproduced="<<l01_reproduced<<" checks="<<checks<<" cycles="<<cycles<<"\n";
 return 0;
}catch(const std::exception&e){std::cerr<<"FAIL concurrent "<<e.what()<<" cycle="<<cycles<<"\n";return 1;}}
