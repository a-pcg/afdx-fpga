#include "Vafdx_system_top.h"
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
// P2 experiment TB: the joint TX+RX loopback of tb/system_loopback.cpp with the
// line byte period exposed as a run-time parameter, plus byte-rate accounting.
// cadence=8 reproduces the frozen T8 line rate; cadence=4 is the
// 50 MHz/4 = 100 Mbit/s experiment (elinx/m0_probe/byte_rate4/afdx_shared_tx.v).
// All functional checks of the original TB are kept (16 frames 1/17/63/1471,
// per-VL SN, BAG spacing, SFD timestamp delta==0, fault injection, delivery
// accounting); the only changed expectations are the byte period and the
// 13-byte inter-frame gap, both scaled by the cadence parameter.
using Bytes=std::vector<unsigned char>;
struct Exp{unsigned vl;Bytes payload;unsigned sn;unsigned network;std::uint64_t sfd;unsigned fault;};
static Vafdx_system_top d;
static const unsigned VLS=4;
static std::mt19937 rng(20261022);
static unsigned long long checks=0,cycles=0;
static unsigned delivered=0,sent=0,dropped=0,expected_descriptors=0,decisions=0,dec_a=0,dec_b=0,deliv_a=0,deliv_b=0;
static unsigned long long max_time_delta=0;
static std::array<std::deque<Exp>,VLS> per_vl;
static std::array<std::deque<Exp>,VLS> deliver_q;
static std::array<unsigned,VLS> next_sn{};
static std::array<bool,VLS> sn_started{};
static std::array<std::uint64_t,VLS> last_sfd{};
static std::array<bool,VLS> has_sfd{};
static std::array<unsigned,VLS> sent_per_vl{};
static const unsigned bag_exp[VLS]={0,1,0,2};
static Bytes current;
static std::uint64_t byte_time=0,end_time=0,current_sfd=0;
static unsigned frame_ordinal=0,frame_mode=0;
static Bytes delivery;
static unsigned delivery_vl=0,delivery_sn=0,delivery_network=0,delivery_len=0;
static unsigned long long delivery_time=0;
static bool delivery_active=false;
// byte-rate accounting
static unsigned CAD=8;
static unsigned long long line_bytes=0, first_byte_time=0, last_byte_time=0;
static unsigned long long frame_start_time=0;
static unsigned frame_bytes=0;
static unsigned long long total_frame_bytes=0, expected_wire_bytes=0;
static long long intra_min=-1, intra_max=-1;
static unsigned long long min_gap=~0ULL, max_gap=0, gap_sum=0;
static unsigned gap_count=0, frames_on_wire=0, sfd_count=0;
static unsigned long long frame_dur_min=~0ULL, frame_dur_max=0, frame_dur_sum=0;
static unsigned long long full_drop_total();
static unsigned long long bad_fcs_total();
static void need(bool ok,const std::string&s){++checks;if(!ok)throw std::runtime_error(s);}
static void step(){
 bool physical_sfd=false;
 d.clk=0;d.eval();
 bool byte_now=d.tx_line_valid&&d.tx_byte_ce&&!d.reset;
 if(byte_now&&current.empty()){
  ++frame_ordinal;
  frame_mode=frame_ordinal==3?1:frame_ordinal==7?2:frame_ordinal==11?3:0;
  frame_start_time=d.monotonic_time;frame_bytes=0;
  if(end_time){
   need(d.monotonic_time-end_time>=13ULL*CAD,"system TX IFG violation");
   unsigned long long gap=d.monotonic_time-end_time;
   if(gap<min_gap)min_gap=gap;
   if(gap>max_gap)max_gap=gap;
   gap_sum+=gap;++gap_count;
  }
 }
 // Loop the transmit line back into both receive networks (same model as the
 // frozen loopback TB: whole-frame fault injection only asserts MAC line_error).
 bool drive=d.tx_line_valid&&!d.reset;
 d.rx_line_valid_a=drive;d.rx_line_valid_b=drive;
 d.rx_line_data_a=d.tx_line_data;d.rx_line_data_b=d.tx_line_data;
 d.rx_line_error_a=drive&&(frame_mode&1);d.rx_line_error_b=drive&&(frame_mode&2);
 d.rx_ready=1;
 if(!d.reset&&byte_now){
  if(!current.empty()){
   need(d.monotonic_time-byte_time==CAD,"system TX byte cadence violation");
   long long delta=(long long)(d.monotonic_time-byte_time);
   if(intra_min<0||delta<intra_min)intra_min=delta;
   if(delta>intra_max)intra_max=delta;
  }
  physical_sfd=false;
  if(current.size()==7){need(d.tx_line_data==0xd5,"system missing SFD byte");physical_sfd=true;current_sfd=d.monotonic_time;}
  current.push_back(d.tx_line_data);byte_time=d.monotonic_time;
  if(!line_bytes)first_byte_time=d.monotonic_time;
  last_byte_time=d.monotonic_time;++line_bytes;++frame_bytes;
 }
 if(!d.tx_line_valid&&!current.empty()){
  need(current.size()>=72,"system truncated frame");
  unsigned long long dur=byte_time-frame_start_time+(unsigned long long)CAD;
  ++frames_on_wire;total_frame_bytes+=frame_bytes;
  if(dur<frame_dur_min)frame_dur_min=dur;
  if(dur>frame_dur_max)frame_dur_max=dur;
  frame_dur_sum+=dur;
  std::cout<<"LINE n="<<frames_on_wire<<" bytes="<<frame_bytes<<" start="<<frame_start_time
           <<" end="<<byte_time<<" dur="<<dur<<" gap="<<(frame_start_time>=end_time?frame_start_time-end_time:0)
           <<" cadence="<<CAD<<std::endl;
  current.clear();end_time=byte_time;
 }
 if(!d.reset&&d.rx_valid&&d.rx_ready){
  if(!delivery_active){delivery_active=true;delivery.clear();delivery_vl=d.rx_vl;delivery_sn=d.rx_sn;delivery_network=d.rx_network;delivery_len=d.rx_len;delivery_time=d.rx_time;}
  else need(d.rx_vl==delivery_vl&&d.rx_sn==delivery_sn&&d.rx_network==delivery_network&&d.rx_len==delivery_len,"system RX descriptor changed mid-frame");
  delivery.push_back(d.rx_data);
  if(d.rx_last){
   need(delivery_vl<VLS,"system delivered an out-of-range VL index");
   need(!deliver_q[delivery_vl].empty(),"system delivered an unsolicited frame");
   const Exp&e=deliver_q[delivery_vl].front();
   need(delivery_sn==e.sn,"system delivered wrong SN: vl="+std::to_string(delivery_vl)+" got="+std::to_string(delivery_sn)+" exp="+std::to_string(e.sn)+" len="+std::to_string(delivery_len)+" net="+std::to_string(delivery_network)+" full_drops="+std::to_string(full_drop_total())+" bad_fcs="+std::to_string(bad_fcs_total()));
   if(e.fault==0)need(delivery_network<=1,"system delivered an invalid network flag");
   else need(delivery_network==(e.fault==1?1u:0u),"system delivered the frame from the faulted network");
   need(delivery_len==e.payload.size(),"system delivered wrong payload length");
   need(delivery==e.payload,"system delivered corrupted payload");
   std::uint64_t delta=delivery_time>e.sfd?delivery_time-e.sfd:e.sfd-delivery_time;
   if(delta>max_time_delta)max_time_delta=delta;
   deliver_q[delivery_vl].pop_front();++delivered;if(delivery_network)++deliv_b;else ++deliv_a;delivery_active=false;
   std::cout<<"DEL n="<<delivered<<" vl="<<delivery_vl<<" sn="<<delivery_sn<<" net="<<delivery_network<<std::endl;
  }
 }
 d.clk=1;d.eval();++cycles;
 if(!d.reset){
  need(!d.tx_fault,"system TX reported an internal fault");
  if(d.rx_decision){++decisions;if(d.rx_network)++dec_b;else ++dec_a;std::cout<<"DEC n="<<decisions<<" net="<<(int)d.rx_network<<" pass="<<(int)d.rx_decision_pass<<" reason="<<(int)d.rx_reason<<" vl="<<(int)d.rx_vl<<" sn="<<(int)d.rx_sn<<std::endl;}
  if(d.sfd_pulse){
   need(physical_sfd&&d.sfd_time==current_sfd,"system SFD timestamp mismatch");
   unsigned vl=d.sfd_vl;need(vl<VLS,"system SFD VL outside the configured subset");
   need(!per_vl[vl].empty(),"system sent an unsolicited VL frame");
   Exp e=per_vl[vl].front();per_vl[vl].pop_front();
   e.sn=sn_started[vl]?next_sn[vl]:0u;
   next_sn[vl]=(e.sn==255)?1:e.sn+1;sn_started[vl]=true;
   e.fault=frame_mode;e.network=(frame_mode==1)?1u:0u;e.sfd=d.sfd_time;
   expected_descriptors+=frame_mode==3?0:(frame_mode==0?2:1);
   if(has_sfd[vl])need(d.sfd_time-last_sfd[vl]>=(100000ULL<<bag_exp[vl]),"system BAG spacing violation");
   last_sfd[vl]=d.sfd_time;has_sfd[vl]=true;++sent;++sent_per_vl[vl];++sfd_count;
   unsigned body=e.payload.size()+43u;if(body<60u)body=60u;
   expected_wire_bytes+=8ULL+body+4ULL;
   std::cout<<"SFD n="<<sent<<" vl="<<vl<<" mode="<<frame_mode<<" time="<<d.sfd_time<<std::endl;
   if(frame_mode==3){++dropped;}else{deliver_q[vl].push_back(e);}
  }else need(!physical_sfd,"physical SFD without timestamp pulse");
 }
}
static void reset_system(){
 d.reset=1;d.run=0;d.app_enable=0;d.cfg_valid=0;d.app_msg_valid=0;d.app_valid=0;
 d.app_rsp_ready=0;d.drop_valid=0;d.drop_rsp_ready=0;d.rx_ready=1;
 step();step();d.reset=0;
 unsigned n=0;while(!d.initialized){step();need(++n<4000,"system initialization timeout");}
}
static void configure(){
 d.app_enable=0;d.run=0;d.cfg_valid=0;d.clk=0;d.eval();
 for(unsigned i=0;i<VLS;++i){
  d.cfg_valid=1;d.cfg_vl=i;d.cfg_bag_exp=bag_exp[i];
  unsigned cw=0;
  while(true){d.clk=0;d.eval();if(d.cfg_ready)break;step();need(++cw<16,"system configuration not ready on an idle port");}
  step();
 }
 d.cfg_valid=0;d.clk=0;d.eval();
}
static void enqueue(unsigned vl,const Bytes&payload){
 d.app_enable=1;d.app_msg_valid=1;d.app_vl=vl;d.app_length=payload.size();
 unsigned n=0;
 while(true){d.clk=0;d.eval();bool accept=d.app_msg_ready;step();if(accept)break;need(++n<3000000,"system app admission timeout");}
 d.app_msg_valid=0;d.app_vl=(vl+1)%VLS;d.app_length=0;d.eval();
 unsigned pos=0;bool offering=false;n=0;
 while(!d.app_rsp_valid){
  if(!offering&&pos<payload.size()&&d.app_ready&&rng()%5!=0)offering=true;
  d.app_valid=offering;d.app_data=pos<payload.size()?payload[pos]:0;d.app_last=pos+1==payload.size();
  d.clk=0;d.eval();bool byte=d.app_valid&&d.app_ready;
  step();if(byte){++pos;offering=false;}
  need(++n<payload.size()*12+500000,"system app payload timeout");
 }
 d.app_valid=0;
 unsigned status=d.app_rsp_status;
 need(status==0,"system enqueue rejected with status "+std::to_string(status));
 need(pos==payload.size(),"system successful message consumed the wrong byte count");
 d.app_rsp_ready=1;step();d.app_rsp_ready=0;
}
static void drain(){
 unsigned long long n=0;
 while((delivered<sent-dropped||d.busy)&&n<60000000){step();++n;}
 while(n<60000000){unsigned before=decisions;for(unsigned i=0;i<2000&&n<60000000;++i){step();++n;}if(decisions==before)break;}
 need(n<60000000,"system drain timeout");
}
static unsigned long long full_drop_total(){return (d.full_drops&0xffffffffULL)+(d.full_drops>>32);}
static unsigned long long bad_fcs_total(){return (d.bad_fcs&0xffffffffULL)+(d.bad_fcs>>32);}
int main(int argc,char**argv){try{
 Verilated::commandArgs(argc,argv);need(argc==3,"usage result.json cadence");
 CAD=(unsigned)std::stoul(argv[2]);
 need(CAD==4||CAD==8,"cadence must be 4 or 8");
 reset_system();configure();
 d.run=1;d.app_enable=1;
 const unsigned lens[4]={1,17,63,1471};
 for(unsigned v=0;v<VLS;++v)for(unsigned k=0;k<4;++k){
  unsigned len=lens[(k+v)%4];Bytes p;
  for(unsigned i=0;i<len;++i)p.push_back((i*17+v*31+k*7)%256);
  per_vl[v].push_back(Exp{v,p,0,0,0,0});
  enqueue(v,p);
 }
 d.app_enable=0;
 drain();
 unsigned total=0;for(unsigned v=0;v<VLS;++v){need(sent_per_vl[v]==4,"a configured VL never reached the wire four times");total+=sent_per_vl[v];}
 need(total==sent,"system sent accounting mismatch");
 need(delivered==sent-dropped,"system accept=deliver+drop accounting mismatch");
 for(unsigned q=0;q<VLS;++q)need(deliver_q[q].empty(),"system left expected frames undelivered on a VL");
 need(max_time_delta==0,"system RX SFD timestamp differs from the transmitted SFD: delta="+std::to_string(max_time_delta)+" cycles");
 need(d.delivered_frames==delivered,"system RX delivered counter mismatch: hw="+std::to_string(d.delivered_frames)+" model="+std::to_string(delivered));
 need(d.delivered_frames+d.rejected_frames==decisions,"system RX decision/delivery accounting mismatch");
 need(decisions<=expected_descriptors,"system RX produced more descriptors than the wire carried");
 need(decisions+full_drop_total()>=expected_descriptors,"system RX lost descriptors without a MAC drop");
 need(delivery_active==false,"system RX left a partial delivery open");
 need(dropped==1,"whole-frame double fault injection was not exercised");
 // P2 byte-rate accounting and extra invariants.
 need(line_bytes==expected_wire_bytes,"line byte count mismatch: wire="+std::to_string(line_bytes)+" expected="+std::to_string(expected_wire_bytes));
 need(frames_on_wire==sent,"line frame count mismatch");
 need(intra_min==(long long)CAD&&intra_max==(long long)CAD,"intra-frame byte cadence is not exactly the cadence parameter");
 need(min_gap==~0ULL||min_gap>=13ULL*CAD,"inter-frame gap below 13 byte periods");
 need(total_frame_bytes==line_bytes,"frame byte accounting mismatch");
 need(full_drop_total()==0,"RX produced MAC full drops");
 need(bad_fcs_total()==0,"RX reported bad FCS on a clean loopback");
 unsigned long long window=last_byte_time>=first_byte_time?last_byte_time-first_byte_time+CAD:1;
 double bytes_per_cycle=(double)line_bytes/(double)window;
 double wire_bpc=1.0/(double)CAD;
 double util=(double)line_bytes*(double)CAD/(double)window;
 double frame_dur_avg=(double)frame_dur_sum/(double)frames_on_wire;
 double gap_avg=gap_count?(double)gap_sum/(double)gap_count:0.0;
 std::cout<<"PASS byte-rate loopback cadence="<<CAD<<" frames="<<sent<<" delivered="<<delivered
          <<" dropped="<<dropped<<" line_bytes="<<line_bytes<<" window_cycles="<<window
          <<" checks="<<checks<<" cycles="<<cycles<<" max_sfd_delta="<<max_time_delta<<"\n";
 std::cout<<"wire: bytes/cycle="<<bytes_per_cycle<<" (wire while transmitting="<<wire_bpc<<")"
          <<" line_utilization="<<util<<" avg_MB/s@50MHz="<<bytes_per_cycle*50.0
          <<" avg_MB/s@100MHz="<<bytes_per_cycle*100.0<<"\n";
 std::cout<<"byte gaps: intra min/max="<<intra_min<<"/"<<intra_max
          <<" inter min/avg/max="<<(min_gap==~0ULL?0ULL:min_gap)<<"/"<<gap_avg<<"/"<<max_gap
          <<" frame_dur avg="<<frame_dur_avg<<" min="<<frame_dur_min<<" max="<<frame_dur_max<<"\n";
 d.final();
 std::ofstream out(argv[1]);
 out<<"{\"status\":\"PASS\",\"cadence\":"<<CAD
    <<",\"frames_sent\":"<<sent<<",\"delivered\":"<<delivered<<",\"dropped_by_fault_injection\":"<<dropped
    <<",\"checks\":"<<checks<<",\"cycles\":"<<cycles<<",\"max_sfd_time_delta\":"<<max_time_delta
    <<",\"line_bytes\":"<<line_bytes<<",\"expected_wire_bytes\":"<<expected_wire_bytes
    <<",\"frames_on_wire\":"<<frames_on_wire<<",\"total_frame_bytes\":"<<total_frame_bytes
    <<",\"window_cycles\":"<<window
    <<",\"bytes_per_cycle\":"<<bytes_per_cycle
    <<",\"wire_bytes_per_cycle\":"<<wire_bpc
    <<",\"line_utilization\":"<<util
    <<",\"avg_MBps_at_50MHz\":"<<bytes_per_cycle*50.0
    <<",\"avg_MBps_at_100MHz\":"<<bytes_per_cycle*100.0
    <<",\"wire_MBps_at_50MHz\":"<<wire_bpc*50.0
    <<",\"wire_MBps_at_100MHz\":"<<wire_bpc*100.0
    <<",\"wire_bitrate_Mbps_at_50MHz\":"<<wire_bpc*50.0*8.0
    <<",\"wire_bitrate_Mbps_at_100MHz\":"<<wire_bpc*100.0*8.0
    <<",\"min_intra_byte_gap\":"<<intra_min<<",\"max_intra_byte_gap\":"<<intra_max
    <<",\"min_inter_frame_gap\":"<<(min_gap==~0ULL?0ULL:min_gap)
    <<",\"avg_inter_frame_gap\":"<<gap_avg<<",\"max_inter_frame_gap\":"<<max_gap
    <<",\"frame_duration_min\":"<<frame_dur_min<<",\"frame_duration_avg\":"<<frame_dur_avg
    <<",\"frame_duration_max\":"<<frame_dur_max
    <<",\"rx_decisions\":"<<decisions<<",\"rx_full_drops\":"<<full_drop_total()
    <<",\"rx_bad_fcs\":"<<bad_fcs_total()<<",\"tx_fault\":0"
    <<",\"redundancy_a_fault_used\":true,\"redundancy_b_fault_used\":true}\n";
 return 0;
}catch(const std::exception&e){std::cerr<<"FAIL "<<e.what()<<" cycles="<<cycles<<" sent="<<sent<<" delivered="<<delivered<<"\n";return 1;}}
