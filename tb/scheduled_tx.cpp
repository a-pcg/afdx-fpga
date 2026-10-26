#include "Vafdx_scheduled_tx.h"
#include "verilated.h"
#include <iostream>
#include <fstream>
#include <vector>
#include <deque>
#include <array>
#include <stdexcept>
using Bytes=std::vector<unsigned char>;
struct Frame{unsigned vl;Bytes payload,frame;};
static Vafdx_scheduled_tx d;static unsigned long long checks=0,cycles=0,frames=0;
static void ck(bool x,const char*s){++checks;if(!x)throw std::runtime_error(s);}
static void tick(){d.clk=0;d.eval();d.clk=1;d.eval();++cycles;}
static Bytes decode(std::string s){Bytes b;for(size_t i=0;i<s.size();i+=2)b.push_back((unsigned char)std::stoul(s.substr(i,2),nullptr,16));return b;}
int main(int argc,char**argv){try{
 Verilated::commandArgs(argc,argv);d.reset=1;d.run=0;d.cfg_valid=0;d.app_msg_valid=0;d.app_valid=0;d.mac_ready=0;tick();tick();d.reset=0;
 for(unsigned i=0;i<4;++i){d.cfg_valid=1;d.cfg_index=i;d.cfg_bag_exp=i;tick();}d.cfg_valid=0;d.run=1;
 std::ifstream in("vectors/scheduled_frames.txt");ck(in.good(),"missing integrated vectors");std::vector<Frame> input;std::array<std::deque<Bytes>,4> expected;
 unsigned vl;std::string a,b;while(in>>vl>>a>>b){Frame f{vl,decode(a),decode(b)};input.push_back(f);expected[vl].push_back(f.frame);}
 ck(input.size()==16,"integrated corpus size");size_t message=0,pos=0;bool loading=false;Bytes current;unsigned gap=0;unsigned long long start=0;std::array<unsigned long long,4> last{};std::array<bool,4> seen{};
 bool stalled=false;unsigned held=0;bool held_sop=false,held_eop=false;
 for(unsigned t=0;t<4000000 && frames<input.size();++t){
 d.app_msg_valid=0;d.app_valid=0;
 if(message<input.size()){
 auto&f=input[message];d.app_vl=f.vl;d.app_len=(unsigned)f.payload.size();
 if(!loading)d.app_msg_valid=1;
 else{d.app_valid=(t%5!=0);d.app_data=f.payload[pos];d.app_last=(pos+1==f.payload.size());}
 }
 d.mac_ready=(gap==0 && (t%8)==0);d.clk=0;d.eval();
 bool desc=d.app_msg_valid&&d.app_msg_ready,byte=d.app_valid&&d.app_ready;
 if(stalled)ck(d.mac_valid&&d.mac_data==held&&bool(d.mac_sop)==held_sop&&bool(d.mac_eop)==held_eop,"integrated output unstable");
 if(d.mac_valid&&d.mac_ready){
 ck(bool(d.mac_sop)==current.empty(),"integrated SOP");if(current.empty())start=d.monotonic_time;
 current.push_back(d.mac_data);
 if(d.mac_eop){unsigned id=current[5]-1;ck(id<4,"unknown output VL");ck(!expected[id].empty(),"unexpected output packet");ck(current==expected[id].front(),"interleaved/corrupt scheduled frame");expected[id].pop_front();
 if(seen[id])ck(start-last[id]>=(100000ULL<<id),"integrated BAG violation");seen[id]=true;last[id]=start;current.clear();++frames;gap=24*8;}
 }
 stalled=d.mac_valid&&!d.mac_ready;held=d.mac_data;held_sop=d.mac_sop;held_eop=d.mac_eop;
 tick();ck(!d.reject,"integrated message rejected");
 if(desc){loading=true;pos=0;}if(byte){if(++pos==input[message].payload.size()){++message;loading=false;}}
 if(gap)--gap;
 }
 ck(frames==input.size()&&message==input.size(),"integrated completion timeout");
 for(auto&q:expected)ck(q.empty(),"integrated packet lost");
 std::ofstream out("reports/scheduled_tx.json");out<<"{\"status\":\"PASS\",\"vl_count\":4,\"frames\":"<<frames<<",\"checks\":"<<checks<<",\"cycles\":"<<cycles<<"}\n";
 std::cout<<"PASS integrated TX frames="<<frames<<" checks="<<checks<<" cycles="<<cycles<<std::endl;return 0;
 }catch(const std::exception&e){std::cerr<<"FAIL "<<e.what()<<std::endl;return 1;}}
