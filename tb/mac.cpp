#include "Vmac_pair.h"
#include "verilated.h"
#include <vector>
#include <deque>
#include <fstream>
#include <iostream>
#include <random>
#include <stdexcept>
using Bytes=std::vector<unsigned char>;
struct RxExpected{Bytes bytes;unsigned long long timestamp=0;};
struct Vector{Bytes client,wire;};
static Vmac_pair d;static unsigned long long cycles=0,checks=0;
static unsigned tx_frames=0,rx_frames=0,rx_drops=0,tx_rejects=0,exact_ifg=0,blocked=0;
static std::deque<Bytes> expected_tx;static std::deque<RxExpected> expected_rx;
static Bytes wire_current,rx_current;
static unsigned idle_cells=12;static bool had_wire=false;static bool random_ready=false;
static std::mt19937 rng(20261018);
static bool rx_held=false;static unsigned rx_hold_data=0;static bool rx_hold_sop=false,rx_hold_eop=false;
static unsigned long long rx_hold_time=0;
static void ck(bool ok,const std::string&why){++checks;if(!ok)throw std::runtime_error(why+" cycle="+std::to_string(cycles));}
static Bytes hex(const std::string&s){Bytes b;for(size_t i=0;i<s.size();i+=2)b.push_back((unsigned char)std::stoul(s.substr(i,2),nullptr,16));return b;}
static void tick(){
 d.byte_ce=(cycles%8==0);d.time_now=cycles;
 if(random_ready)d.rx_ready=(rng()%5!=0);
 d.clk=0;d.eval();bool expected_sfd=false,expected_done=false;
 if(!d.reset){
 if(d.byte_ce){
 if(d.line_tx_valid){
  if(wire_current.empty()){
   ck(!expected_tx.empty(),"unexpected transmitted frame");if(had_wire){ck(idle_cells>=12,"IFG shorter than 12 bytes");if(idle_cells==12)++exact_ifg;}
   idle_cells=0;
  }
  expected_sfd=(wire_current.size()==7);wire_current.push_back(d.line_tx_data);
  ck(wire_current.size()<=expected_tx.front().size(),"TX frame overrun");
  expected_done=(wire_current.size()==expected_tx.front().size());
 }else{
  if(!wire_current.empty()){
   ck(wire_current==expected_tx.front(),"wire preamble/data/FCS differs from zlib golden");expected_tx.pop_front();wire_current.clear();++tx_frames;had_wire=true;
  }
  ++idle_cells;
 }
 }
 if(rx_held)ck(d.rx_valid&&d.rx_data==rx_hold_data&&bool(d.rx_sop)==rx_hold_sop&&bool(d.rx_eop)==rx_hold_eop&&d.rx_timestamp==rx_hold_time,"RX output changed under backpressure");
 if(d.rx_valid&&d.rx_ready){
  ck(!expected_rx.empty(),"bad or unrequested frame delivered");auto& e=expected_rx.front();
  ck(bool(d.rx_sop)==rx_current.empty(),"RX SOP wrong");ck(d.rx_timestamp==e.timestamp,"RX timestamp overwritten/misaligned");
  ck(rx_current.size()<e.bytes.size(),"RX overrun");ck(d.rx_data==e.bytes[rx_current.size()],"RX payload/FCS stripping mismatch");
  ck(bool(d.rx_eop)==(rx_current.size()+1==e.bytes.size()),"RX EOP wrong");rx_current.push_back(d.rx_data);
  if(d.rx_eop){expected_rx.pop_front();rx_current.clear();++rx_frames;}
 }
 rx_held=d.rx_valid&&!d.rx_ready;rx_hold_data=d.rx_data;rx_hold_sop=d.rx_sop;rx_hold_eop=d.rx_eop;rx_hold_time=d.rx_timestamp;
 }
 d.clk=1;d.eval();
 if(!d.reset){ck(bool(d.tx_sfd_pulse)==expected_sfd,"TX SFD pulse not aligned with line D5");ck(bool(d.tx_done)==expected_done,"TX done not aligned with final FCS byte");if(expected_sfd)ck(d.tx_sfd_time==cycles,"TX SFD timestamp wrong");if(d.rx_drop)++rx_drops;if(d.tx_reject)++tx_rejects;}
 ++cycles;
}
static void reset(){
 ck(expected_tx.empty()&&expected_rx.empty(),"reset test silently discarded expected frames");
 d.reset=1;d.tx_valid=0;d.line_rx_valid=0;d.line_rx_error=0;d.rx_ready=0;random_ready=false;rx_held=false;
 for(int i=0;i<4;++i)tick();d.reset=0;wire_current.clear();rx_current.clear();had_wire=false;idle_cells=12;tick();
}
static void idle(unsigned n=12){
 for(unsigned i=0;i<n;++i){while(cycles%8)tick();d.line_rx_valid=0;d.line_rx_error=0;tick();}
}
static void receive_wire(const Bytes&w,const Bytes*client=nullptr,int error=0,unsigned gap=12){
 if(client)expected_rx.push_back({*client,0});
 for(size_t i=0;i<w.size();++i){while(cycles%8)tick();d.line_rx_valid=1;d.line_rx_data=w[i];d.line_rx_error=(error&&i==10);tick();
  if(client&&i==7){ck(d.rx_sfd_pulse,"RX SFD pulse missing");ck(d.rx_sfd_time==cycles-1,"RX SFD timestamp wrong");expected_rx.back().timestamp=d.rx_sfd_time;}
 }
 idle(gap);
}
static void drain(){d.line_rx_valid=0;random_ready=true;
 for(unsigned i=0;i<100000 && (!expected_tx.empty()||!expected_rx.empty());++i)tick();
 ck(expected_tx.empty()&&expected_rx.empty(),"queue failed to drain");for(int i=0;i<20;++i)tick();}
static void send_client(const Bytes&c,const Bytes*wire,bool malformed_sop=false){
 if(wire)expected_tx.push_back(*wire);
 for(size_t i=0;i<c.size();++i){
 d.tx_valid=1;d.tx_data=c[i];d.tx_sop=(i==0)&&!malformed_sop;d.tx_eop=(i+1==c.size());
 bool accepted=false;
 for(unsigned t=0;t<30000&&!accepted;++t){d.clk=0;d.eval();accepted=d.tx_ready;if(!accepted)++blocked;tick();}
 ck(accepted,"TX client permanently backpressured");
 if(i%11==0){d.tx_valid=0;tick();}
 }
 d.tx_valid=0;
}
int main(int argc,char**argv){try{
 Verilated::commandArgs(argc,argv);reset();std::ifstream in("vectors/mac_good.txt");ck(in.good(),"missing MAC vectors");std::vector<Vector> good;std::string a,b;
 while(in>>a>>b)good.push_back({hex(a),hex(b)});ck(good.size()>=40,"incomplete corpus");
 for(auto&v:good)send_client(v.client,&v.wire);drain();ck(exact_ifg==good.size()-1&&blocked>0,"TX queue/continuous IFG coverage missing");
 // RX normal and repeated minimum IFG, random application backpressure.
 random_ready=true;
 for(auto&v:good)receive_wire(v.wire,&v.client);drain();
 // Back-to-back minimum frames exercise the maximum frame rate; maximum
 // frames exercise RAM-bank wrap and simultaneous ingress/client drain.
 unsigned drops_before_stress=rx_drops;
 for(unsigned i=0;i<1024;++i)receive_wire(good[0].wire,&good[0].client);
 for(unsigned i=0;i<64;++i)receive_wire(good[12].wire,&good[12].client);
 drain();ck(rx_drops==drops_before_stress,"healthy line-rate stress lost frames");
 std::ifstream bad("vectors/mac_bad.txt");ck(bad.good(),"missing MAC bad vectors");std::string name;unsigned reason,error;
 unsigned malformed=0;
 while(bad>>name>>reason>>error>>b){unsigned before=rx_drops;receive_wire(hex(b),nullptr,error);ck(rx_drops==before+1,"bad wire frame was not rejected: "+name);ck(d.rx_drop_reason==reason,"wrong rejection reason: "+name);++malformed;}
 // No ready on wire. Fill both RX slots, discard a third frame without damaging queued metadata.
 drain();random_ready=false;d.rx_ready=0;unsigned before_full=d.rx_full_drops;
 receive_wire(good[0].wire,&good[0].client);receive_wire(good[1].wire,&good[1].client);receive_wire(good[2].wire);
 ck(d.rx_full_drops==before_full+1,"full queue did not discard entire frame");ck(d.rx_slots==2,"full RX queue accounting wrong");drain();
 // Insufficient IFG must fail and then recover at a proper gap.
 random_ready=true;receive_wire(good[0].wire,&good[0].client,0,11);unsigned before=rx_drops;
 receive_wire(good[1].wire);ck(rx_drops==before+1&&d.rx_drop_reason==2,"short IFG accepted");drain();
 receive_wire(good[3].wire,&good[3].client);drain();
 // Invalid client lengths and malformed SOP must never reach the line.
 before=tx_rejects;send_client(Bytes(59,0x11),nullptr);send_client(Bytes(1515,0x22),nullptr);send_client(good[0].client,nullptr,true);
 for(int i=0;i<40;++i)tick();ck(tx_rejects==before+3,"invalid TX frame not rejected");
 // Both sides must operate again after reset.
 reset();send_client(good[0].client,&good[0].wire);receive_wire(good[0].wire,&good[0].client);drain();
 // Reset a partial inbound preamble and a partially captured client frame.
 while(cycles%8)tick();d.line_rx_valid=1;d.line_rx_data=0x55;tick();d.line_rx_valid=0;
 d.tx_valid=1;d.tx_sop=1;d.tx_eop=0;d.tx_data=0x03;tick();d.tx_valid=0;reset();
 send_client(good[1].client,&good[1].wire);receive_wire(good[1].wire,&good[1].client);drain();
 std::ofstream out("reports/mac.json");out<<"{\"status\":\"PASS\",\"seed\":20261018,\"tx_frames\":"<<tx_frames<<",\"rx_frames\":"<<rx_frames<<",\"minimum_frame_stress\":1024,\"maximum_frame_stress\":64,\"malformed_cases\":"<<malformed<<",\"rx_drops\":"<<rx_drops<<",\"tx_rejects\":"<<tx_rejects<<",\"exact_12_byte_ifg\":"<<exact_ifg<<",\"backpressured_cycles\":"<<blocked<<",\"checks\":"<<checks<<",\"cycles\":"<<cycles<<"}\n";
 std::cout<<"PASS MAC TX="<<tx_frames<<" RX="<<rx_frames<<" checks="<<checks<<std::endl;return 0;
 }catch(const std::exception&e){std::cerr<<"FAIL "<<e.what()<<std::endl;return 1;}}
