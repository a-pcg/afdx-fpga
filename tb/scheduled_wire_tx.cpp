#include "Vafdx_scheduled_wire_tx.h"
#include "verilated.h"
#include <array>
#include <cstdint>
#include <deque>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>
using Bytes=std::vector<unsigned char>;
struct Frame{unsigned vl;Bytes payload,wire;};
static Vafdx_scheduled_wire_tx d;
static unsigned long long checks=0,cycles=0;
static void need(bool ok,const std::string& text){++checks;if(!ok)throw std::runtime_error(text);}
static void tick(){d.clk=0;d.eval();d.clk=1;d.eval();++cycles;}
static Bytes decode(const std::string& text){Bytes b;for(size_t i=0;i<text.size();i+=2)b.push_back((unsigned char)std::stoul(text.substr(i,2),nullptr,16));return b;}
int main(int argc,char**argv){try{
 Verilated::commandArgs(argc,argv);d.reset=1;d.run=0;d.cfg_valid=0;d.app_msg_valid=0;d.app_valid=0;tick();tick();d.reset=0;d.eval();
 need(d.monotonic_time==0xffff0000ULL,"initial time parameter mismatch");
 for(unsigned i=0;i<4;++i){need(d.cfg_ready,"configuration not ready in idle state");d.cfg_valid=1;d.cfg_index=i;d.cfg_bag_exp=i;tick();}d.cfg_valid=0;
 std::ifstream f("vectors/scheduled_wire_tx/cases.txt");need(f.good(),"missing SFD/BAG vectors");
 std::vector<Frame> input;std::array<std::deque<Bytes>,4> expected;
 unsigned vl;std::string payload,wire;
 while(f>>vl>>payload>>wire){input.push_back({vl,decode(payload),decode(wire)});expected[vl].push_back(decode(wire));}
 need(input.size()==16,"SFD/BAG corpus incomplete");
 std::array<std::uint64_t,4> last{};std::array<bool,4> seen{};
 size_t message=0,pos=0;bool loading=false,paused=false,wrapped=false;unsigned pause_end=0;
 unsigned frames=0,sfds=0,frame_vl=0;Bytes current;std::uint64_t current_sfd=0,last_byte=0,last_frame_end=0;
 unsigned config_rejections=0;
 for(unsigned t=0;t<4000000&&frames<input.size();++t){
  if(!paused&&d.busy&&sfds==0){paused=true;pause_end=t+1500;}
  d.run=!(paused&&t<pause_end);
  d.cfg_valid=(t==3500);d.cfg_index=0;d.cfg_bag_exp=7;
  d.app_msg_valid=0;d.app_valid=0;
  if(message<input.size()){
   const auto& m=input[message];d.app_vl=m.vl;d.app_len=m.payload.size();
   if(!loading)d.app_msg_valid=1;
   else{d.app_valid=t%5!=0;d.app_data=m.payload[pos];d.app_last=pos+1==m.payload.size();}
  }
  d.clk=0;d.eval();
  bool desc=d.app_msg_valid&&d.app_msg_ready,byte=d.app_valid&&d.app_ready;
  if(!d.run)need(!d.app_msg_ready,"new descriptor admitted while run=0");
  if(d.cfg_valid){need(!d.cfg_ready&&d.cfg_reject,"active config was not rejected");++config_rejections;}
  bool physical_sfd=false;
  if(d.line_valid&&d.byte_tick){
   if(!current.empty())need(d.monotonic_time-last_byte==8,"physical byte cadence changed");
   else if(frames)need(d.monotonic_time-last_frame_end>=104,"physical IFG too short");
   if(current.size()==7){need(d.line_data==0xd5,"SFD byte missing");physical_sfd=true;current_sfd=d.monotonic_time;}
   current.push_back(d.line_data);last_byte=d.monotonic_time;
  }
  if(!d.line_valid&&!current.empty()){
   need(current.size()>=72,"physical frame truncated");unsigned id=current[13]-1;
   need(id<4&&id==frame_vl,"SFD VL tag differs from destination VL");
   need(!expected[id].empty()&&current==expected[id].front(),"scheduled physical frame/FCS differs from golden bytes");
   expected[id].pop_front();current.clear();++frames;last_frame_end=last_byte;
  }
  tick();need(!d.reject,"legal message/MAC frame was rejected");
  if(d.sfd_pulse){
   need(physical_sfd&&d.sfd_time==current_sfd,"SFD pulse timestamp not aligned with D5 event");
   frame_vl=d.sfd_vl;need(frame_vl<4,"SFD tag out of range");
   if(seen[frame_vl])need(d.sfd_time-last[frame_vl]>=(100000ULL<<frame_vl),"actual SFD-to-SFD BAG violation");
   seen[frame_vl]=true;last[frame_vl]=d.sfd_time;++sfds;
   if(paused&&t<pause_end)need(sfds==1,"new physical frame started while run=0");
  }else need(!physical_sfd,"D5 event did not generate SFD pulse");
  if(d.monotonic_time>0xffffffffULL)wrapped=true;
  if(desc){loading=true;pos=0;}
  if(byte&&++pos==input[message].payload.size()){++message;loading=false;}
 }
 need(frames==16&&sfds==16&&message==16,"scheduled wire completion timeout");
 need(paused,"run pause case not exercised");
 need(wrapped,"low32 wrap case not exercised");
 need(config_rejections==1,"active configuration rejection case not exercised");
 for(const auto& q:expected)need(q.empty(),"a queued VL frame was lost");
 d.run=0;d.cfg_valid=0;d.app_msg_valid=0;d.app_valid=0;tick();
 need(d.cfg_ready&&!d.busy,"scheduler/MAC did not drain cleanly");
 d.final();std::ofstream out("reports/scheduled_wire_tx/result.json");
 out<<"{\"status\":\"PASS\",\"vl_count\":4,\"frames\":"<<frames<<",\"sfd_events\":"<<sfds<<",\"checks\":"<<checks<<",\"cycles\":"<<cycles<<",\"low32_wrap\":true,\"run_pause\":true}\n";
 std::cout<<"PASS physical SFD/BAG frames="<<frames<<" checks="<<checks<<"\n";return 0;
}catch(const std::exception&e){std::cerr<<"FAIL "<<e.what()<<"\n";return 1;}}
