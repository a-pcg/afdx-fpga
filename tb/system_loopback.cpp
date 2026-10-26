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
 }
 // Loop the transmit line back into both receive networks. Whole-frame fault
 // injection only asserts the MAC error input, so each network still sees a
 // well-formed byte stream and the integrity decision stays in the DUT.
 bool drive=d.tx_line_valid&&!d.reset;
 d.rx_line_valid_a=drive;d.rx_line_valid_b=drive;
 d.rx_line_data_a=d.tx_line_data;d.rx_line_data_b=d.tx_line_data;
 d.rx_line_error_a=drive&&(frame_mode&1);d.rx_line_error_b=drive&&(frame_mode&2);
 d.rx_ready=1;
 if(!d.reset&&byte_now){
  if(current.empty()){if(end_time)need(d.monotonic_time-end_time>=104,"system TX IFG violation");}
  else need(d.monotonic_time-byte_time==8,"system TX byte cadence violation");
  physical_sfd=false;
  if(current.size()==7){need(d.tx_line_data==0xd5,"system missing SFD byte");physical_sfd=true;current_sfd=d.monotonic_time;}
  current.push_back(d.tx_line_data);byte_time=d.monotonic_time;
 }
 if(!d.tx_line_valid&&!current.empty()){need(current.size()>=72,"system truncated frame");current.clear();end_time=byte_time;}
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
   last_sfd[vl]=d.sfd_time;has_sfd[vl]=true;++sent;++sent_per_vl[vl];
   std::cout<<"SFD n="<<sent<<" vl="<<vl<<" mode="<<frame_mode<<std::endl;
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
  // The TX configuration guard is pipelined (E5 timing revision), so ready may
  // take a couple of cycles; the S05 contract allows holding valid until it is
  // accepted.
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
 // After the last delivery the receiver still has to retire the redundant copy
 // of the final frame; settle until the decision count stops changing.
 while(n<60000000){unsigned before=decisions;for(unsigned i=0;i<2000&&n<60000000;++i){step();++n;}if(decisions==before)break;}
 need(n<60000000,"system drain timeout");
}
static unsigned long long full_drop_total(){return (d.full_drops&0xffffffffULL)+(d.full_drops>>32);}
static unsigned long long bad_fcs_total(){return (d.bad_fcs&0xffffffffULL)+(d.bad_fcs>>32);}
int main(int argc,char**argv){try{
 Verilated::commandArgs(argc,argv);need(argc==2,"usage result.json");
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
 need(d.delivered_frames+d.rejected_frames==decisions,"system RX decision/delivery accounting mismatch: delivered="+std::to_string(d.delivered_frames)+" rejected="+std::to_string(d.rejected_frames));
 need(decisions<=expected_descriptors,"system RX produced more descriptors than the wire carried");
 need(decisions+full_drop_total()>=expected_descriptors,"system RX lost descriptors without a MAC drop: decA="+std::to_string(dec_a)+" decB="+std::to_string(dec_b)+" delivA="+std::to_string(deliv_a)+" delivB="+std::to_string(deliv_b)+" rejected="+std::to_string(d.rejected_frames)+" badfcs="+std::to_string(bad_fcs_total()));
 need(delivery_active==false,"system RX left a partial delivery open");
 // Redundancy observation: both networks carried every frame, the faulted
 // copy never won, and the dropped frame produced no descriptor at all.
 need(dropped==1,"whole-frame double fault injection was not exercised");
 std::cout<<"PASS system loopback frames="<<sent<<" delivered="<<delivered<<" dropped="<<dropped
          <<" checks="<<checks<<" cycles="<<cycles<<" max_sfd_delta="<<max_time_delta<<"\n";
 d.final();
 std::ofstream out(argv[1]);
 out<<"{\"status\":\"PASS\",\"vl_count\":"<<VLS<<",\"configurations\":"<<VLS
    <<",\"frames_sent\":"<<sent<<",\"delivered\":"<<delivered<<",\"dropped_by_fault_injection\":"<<dropped
    <<",\"checks\":"<<checks<<",\"cycles\":"<<cycles<<",\"max_sfd_time_delta\":"<<max_time_delta
    <<",\"redundancy_a_fault_used\":true,\"redundancy_b_fault_used\":true}\n";
 return 0;
}catch(const std::exception&e){std::cerr<<"FAIL "<<e.what()<<" cycles="<<cycles<<" sent="<<sent<<" delivered="<<delivered<<"\n";return 1;}}
