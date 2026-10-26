#include "Vrx_lane_probe.h"
#include "verilated.h"
#include <fstream>
#include <iostream>
#include <map>
#include <stdexcept>
#include <string>
#include <vector>
struct Frame{unsigned n,sn;std::vector<unsigned> p,w;};
static Vrx_lane_probe d;static uint64_t cycles=0,checks=0;static unsigned app=0,desc=0,pass=0,cancelled=0,pending=0,mask=3;static bool ready=true;
static unsigned stuck=0;static std::map<unsigned,Frame> frames;static std::vector<unsigned> got_sn,bytes;static std::ofstream trace;
static void need(bool b,const char*s){++checks;if(!b)throw std::runtime_error(s);}
static std::vector<unsigned> unhex(std::string s){std::vector<unsigned>b;for(size_t i=0;i<s.size();i+=2)b.push_back(std::stoul(s.substr(i,2),nullptr,16));return b;}
static void tick(){d.clk=0;d.time_now=0x3579000000000000ULL+cycles;d.byte_ce=((cycles%8==0)?1:0)|((cycles%8==3)?2:0);d.m_ready=ready;d.eval();
 if(!d.reset){
 if(d.desc_fire){need(d.tables_initialized,"descriptor before table init");need((d.desc_fire&~d.armed_mask)==0,"descriptor from unarmed network");++desc;}
 if(d.m_valid&&d.m_ready){need(d.initialized,"APP before service_ready");need((d.armed_mask>>d.m_network)&1,"APP from unarmed network");bytes.push_back(d.m_data);if(d.m_last){need(bytes.size()==d.m_len,"APP length");auto f=frames.find(d.m_sn);need(f!=frames.end()&&bytes==f->second.p,"APP payload/SN mismatch");bytes.clear();got_sn.push_back(d.m_sn);++app;need(pending==1,"APP without unique pass decision");--pending;trace<<"APP "<<cycles<<" "<<unsigned(d.m_network)<<" "<<unsigned(d.m_sn)<<" "<<unsigned(d.m_len)<<"\n";}}
 }else{cancelled+=pending;pending=0;bytes.clear();}
 d.clk=1;d.eval();if(!d.reset&&d.decision&&d.decision_pass){++pass;++pending;need(pending==1,"more than one active pass");}++cycles;}
static void idle(unsigned n){for(unsigned i=0;i<n;i++){d.line_valid=stuck;d.line_data=0x5555;tick();}}
static void samples(unsigned net,unsigned n){for(unsigned count=0;count<n;){if(cycles%8==(net?3:0))++count;idle(1);}}
static void reset(){d.reset=1;d.line_valid=0;tick();tick();need(d.armed_mask==0&&!d.initialized,"joint reset did not clear armed/service_ready");d.reset=0;got_sn.clear();ready=true;stuck=0;}
static void wire_part(const Frame&f,unsigned net,size_t from,size_t to){while(cycles%8!=(net?3:0))idle(1);for(size_t i=from;i<to;i++)for(unsigned c=0;c<8;c++){d.line_valid=(1u<<net)|stuck;d.line_data=uint16_t(f.w[i]<<(8*net));tick();}}
static void send(const Frame&f,unsigned net,bool settle=true){wire_part(f,net,0,f.w.size());if(settle)idle(5000);}
int main(int argc,char**argv){try{Verilated::commandArgs(argc,argv);need(argc==6,"usage frames result trace expectIndependent enableMask");trace.open(argv[3]);bool independent=std::stoul(argv[4]);mask=std::stoul(argv[5]);std::ifstream in(argv[1]);Frame f;std::string p,w;while(in>>f.n>>f.sn>>p>>w){f.p=unhex(p);f.w=unhex(w);frames[f.sn]=f;}need(frames.size()>=7,"missing vectors");
 if(mask==0){reset();idle(400);need(d.tables_initialized&&!d.initialized&&d.armed_mask==0,"enable00 armed");send(frames[0],0);send(frames[1],1);need(app==0&&desc==0&&!d.busy,"enable00 allocated/forwarded frame");}
 else if(mask!=3){reset();unsigned net=mask==1?0:1;stuck=1u<<(!net);idle(400);need(d.armed_mask==mask&&d.initialized,"single enabled lane not ready");send(frames[0],net);need(got_sn==std::vector<unsigned>{0},"single enabled lane did not deliver");need((d.armed_mask&~mask)==0,"disabled lane armed");}
 else{
 for(unsigned blocked=0;blocked<2;blocked++){
  reset();unsigned healthy=!blocked;stuck=1u<<blocked;idle(400);need(d.tables_initialized&&d.armed_mask==(1u<<healthy),"per-lane idle observation");
  trace<<"STARTUP "<<cycles<<" "<<blocked<<" "<<unsigned(d.armed_mask)<<" "<<unsigned(d.initialized)<<"\n";
  need(bool(d.initialized)==independent,"healthy lane service blocked by other lane");
  unsigned before=app;
  for(unsigned sn=0;sn<5;sn++)send(frames[sn],healthy);
  need(app-before==(independent?5:0),"healthy lane delivery count");
  need(d.armed_mask==(1u<<healthy),"stuck network unexpectedly armed");
  // A complete-looking frame in the still-valid unarmed lane must allocate nothing.
  before=app;unsigned desc_before=desc;wire_part(frames[42],blocked,0,frames[42].w.size());
  need(app==before&&desc==desc_before,"startup frame allocated tag/descriptor");
  stuck=0;samples(blocked,11);need(d.armed_mask==(1u<<healthy),"armed before full 12-idle boundary");
  samples(blocked,1);need(d.armed_mask==3&&d.initialized,"12-idle recovery");
  send(frames[0],blocked);need(app==before+1,"recovered network did not deliver");
  trace<<"RECOVERY "<<cycles<<" "<<blocked<<" "<<unsigned(d.armed_mask)<<"\n";
 }
 // Common reset cancels an already accepted payload and makes all tags invalid.
 reset();idle(400);ready=false;send(frames[0],0);need(d.m_valid&&pending==1,"no held payload to reset");unsigned old_cancel=cancelled;
 reset();need(cancelled==old_cancel+1,"accepted payload not counted as reset cancel");idle(400);need(d.armed_mask==3&&pending==0,"reset did not rearm both networks");
 // A partial physical frame interrupted by reset cannot create a later delivery.
 unsigned before=app;wire_part(frames[4],0,0,frames[4].w.size()/2);reset();wire_part(frames[4],0,frames[4].w.size()/2,frames[4].w.size());idle(400);
 need(app==before,"partial startup frame leaked");send(frames[0],0);need(app==before+1,"reception after partial reset failed");
 // Independent arming does not change the known last-SN-only RM weakness.
 reset();idle(400);before=app;send(frames[42],0);send(frames[43],0);send(frames[42],1);
 need(app==before+3&&got_sn==std::vector<unsigned>({42,43,42}),"known RM counterexample changed unexpectedly");
 }
 need(pass==app+cancelled+pending&&pending==0,"pass/delivery/cancel accounting");
 std::ofstream out(argv[2]);out<<"{\"status\":\""<<(independent?"BEHAVIOR_PASS":"CHARACTERIZED_R9C_GLOBAL_STARTUP_LIMIT")<<"\",\"enable_mask\":"<<mask<<",\"non_coincident_byte_ce_phases\":[0,3],\"application_deliveries\":"<<app<<",\"pass_decisions\":"<<pass<<",\"reset_cancelled_pass\":"<<cancelled<<",\"in_flight\":"<<pending<<",\"descriptor_handshakes\":"<<desc<<",\"checks\":"<<checks<<",\"cycles\":"<<cycles<<",\"rm_late_copy_issue\":\"OPEN\",\"late_copy_at_most_once_satisfied\":false,\"timing_verified\":false}\n";
 std::cout<<"Completed lane arming mode="<<independent<<" enable="<<mask<<" checks="<<checks<<"\n";return 0;
 }catch(const std::exception&e){std::cerr<<"FAIL lane arming cycles="<<cycles<<" "<<e.what()<<"\n";return 1;}}
