#include "Vafdx_packet_pool.h"
#include "verilated.h"
#include <cstdint>
#include <deque>
#include <fstream>
#include <iostream>
#include <map>
#include <random>
#include <stdexcept>
#include <string>
#include <vector>
struct Packet{unsigned refs;bool sealed;std::map<unsigned,unsigned char> bytes;};
struct Reply{std::uint32_t tag;unsigned status,refs;};
static Vafdx_packet_pool d;
static std::map<std::uint32_t,Packet> live;
static std::vector<std::uint32_t> retired;
static std::map<unsigned,std::uint32_t> last_tag;
static unsigned slots=0,commands=0,reads=0,writes=0;
static unsigned long long checks=0,cycles=0;
static std::mt19937 rng(20261018);
static void need(bool ok,const std::string& s){++checks;if(!ok)throw std::runtime_error(s);}
static void tick(){d.clk=0;d.eval();d.clk=1;d.eval();++cycles;}
// T7: packet_pool carries read/write control through one register stage, so the
// read data and write commit are each one clock later than the request
// handshake. T9-1 (T9-B) adds one more read stage for the ownership compare, so
// the read data is now three clocks after the accept edge. Model that
// explicitly: RLAT extra clock(s) for the response, and keep 1 read/cycle
// throughput by tracking in-flight requests instead of assuming same-cycle
// data_valid. Only this latency model changes; no assertion is relaxed.
static const unsigned RLAT=1;
struct Pending{std::uint32_t tag;unsigned offset;unsigned char value;};
static std::deque<Pending> pending;
static void issue_read(std::uint32_t tag,unsigned offset,unsigned char value){
 need(d.rd_ready,"read port not ready");
 d.rd_valid=1;d.rd_tag=tag;d.rd_offset=offset;tick();d.rd_valid=0;
 pending.push_back(Pending{tag,offset,value});
 if(pending.size()>RLAT){
  const Pending&p=pending.front();
  need(d.data_valid&&!d.data_error&&d.data_tag==p.tag&&d.data_offset==p.offset&&d.data_byte==p.value,"pipelined read response mismatch");
  pending.pop_front();++reads;
 }
}
static void flush_reads(){
 while(!pending.empty()){tick();const Pending&p=pending.front();
  need(d.data_valid&&!d.data_error&&d.data_tag==p.tag&&d.data_offset==p.offset&&d.data_byte==p.value,"pipelined read flush mismatch");
  pending.pop_front();++reads;}
}
static void balanced(){need(d.free_count+live.size()==slots,"pool conservation failed");}
static void reset(){d.reset=1;d.cmd_valid=0;d.rsp_ready=0;d.wr_valid=0;d.rd_valid=0;d.data_ready=0;tick();tick();d.reset=0;
 unsigned t=0;while(!d.initialized){need(!d.cmd_ready&&!d.wr_ready&&!d.rd_ready&&!d.data_valid,"pool leaked access during init");tick();need(++t<=slots+5,"pool init timeout");}
 live.clear();retired.clear();last_tag.clear();balanced();}
static Reply command(unsigned op,std::uint32_t tag=0,unsigned refs=0){
 need(d.cmd_ready,"command engine not idle");d.cmd_op=op;d.cmd_tag=tag;d.cmd_consumers=refs;d.cmd_valid=1;tick();d.cmd_valid=0;
 unsigned t=0;while(!d.rsp_valid){tick();need(++t<slots+8,"allocation/command timeout");}
 Reply r{d.rsp_tag,d.rsp_status,d.rsp_refs};
 for(unsigned n=rng()%4;n;--n){need(d.rsp_valid&&!d.cmd_ready&&d.rsp_tag==r.tag&&d.rsp_status==r.status&&d.rsp_refs==r.refs,"command response changed under backpressure");tick();}
 d.rsp_ready=1;tick();d.rsp_ready=0;++commands;return r;
}
static std::uint32_t allocate(){auto r=command(0);
 if(live.size()==slots){need(r.status==2,"full pool did not return NO_SPACE");balanced();return 0;}
 need(r.status==0&&r.refs==1,"allocation failed with space available");unsigned id=r.tag&(slots-1);
 for(const auto& entry:live)need((entry.first&(slots-1))!=id,"same slot allocated twice");
 if(last_tag.count(id))need(last_tag[id]!=r.tag,"slot reused without changing generation");
 last_tag[id]=r.tag;live.emplace(r.tag,Packet{1,false,{}});balanced();return r.tag;
}
static void write(std::uint32_t tag,unsigned offset,unsigned char value,bool error=false){
 d.wr_valid=1;d.wr_tag=tag;d.wr_offset=offset;d.wr_data=value;d.clk=0;d.eval();need(d.wr_ready&&bool(d.wr_error)==error,"write ownership check mismatch");tick();d.wr_valid=0;++writes;
 if(!error)live.at(tag).bytes[offset]=value;
}
static void publish(std::uint32_t tag,unsigned refs){auto r=command(1,tag,refs);need(r.status==0&&r.refs==refs,"publish failed");live.at(tag).refs=refs;live.at(tag).sealed=true;balanced();}
static void release(std::uint32_t tag){auto it=live.find(tag);bool owned=it!=live.end();auto r=command(2,tag);
 need(r.status==(owned?0u:1u),"release accepted stale tag or rejected owner");
 if(owned){need(r.refs==it->second.refs-1,"reference count mismatch");if(--it->second.refs==0){retired.push_back(tag);live.erase(it);}}
 balanced();}
static void read(std::uint32_t tag,unsigned offset,bool error,unsigned char value=0){
 need(d.rd_ready,"read port not ready");d.rd_valid=1;d.rd_tag=tag;d.rd_offset=offset;d.data_ready=0;tick();d.rd_valid=0;
 for(unsigned k=0;k<RLAT;++k)tick();
 need(d.data_valid&&d.data_tag==tag&&d.data_offset==offset&&bool(d.data_error)==error,"read response metadata/access mismatch");
 need(d.data_byte==(error?0:value),"payload read mismatch or stale-data disclosure");
 for(unsigned n=rng()%4;n;--n){tick();need(d.data_valid&&d.data_tag==tag&&d.data_offset==offset&&bool(d.data_error)==error&&d.data_byte==(error?0:value),"read response changed under backpressure");}
 d.data_ready=1;tick();d.data_ready=0;++reads;
}
int main(int argc,char**argv){try{
 Verilated::commandArgs(argc,argv);need(argc>=3,"usage: slots result");slots=std::stoul(argv[1]);reset();
 std::vector<std::uint32_t> initial;
 for(unsigned i=0;i<slots;++i)initial.push_back(allocate());allocate();
 for(auto tag:initial){write(tag,0,(unsigned char)tag);write(tag,2047,0xab);read(tag,0,true);publish(tag,2);write(tag,0,0xee,true);read(tag,0,false,(unsigned char)tag);release(tag);read(tag,2047,false,0xab);release(tag);release(tag);read(tag,0,true);}
 need(d.free_count==slots,"full allocation/release cycle leaked slots");
 // Full address sweep: all slots coexist, interleaved writes and reverse reads.
 // Two complementary address-dependent patterns detect bank aliasing and stuck bits.
 for(unsigned pass=0;pass<2;++pass){
  std::vector<std::uint32_t> tags;
  for(unsigned bank=0;bank<slots;++bank)tags.push_back(allocate());
  auto pattern=[&](unsigned bank,unsigned offset){unsigned x=bank*0x9e3779b9u+offset*0x85ebca6bu; x^=x>>16;return (unsigned char)(x^(pass?255:0));};
  for(unsigned offset=0;offset<2048;++offset)
   for(unsigned bank=0;bank<slots;++bank)write(tags[bank],offset,pattern(bank,offset));
  for(auto tag:tags)publish(tag,1);
  d.data_ready=1;
  for(unsigned offset=2048;offset-->0;)
   for(unsigned bank=slots;bank-->0;)issue_read(tags[bank],offset,pattern(bank,offset));
  flush_reads();
  d.rd_valid=0;tick();d.data_ready=0;
  for(auto tag:tags)release(tag);
  need(d.free_count==slots,"full-capacity sweep leaked slots");
 }
 // Single-cycle read throughput and immutable data after publication.
 auto t=allocate();write(t,0,0x12);write(t,2047,0x34);publish(t,1);d.data_ready=1;
 for(unsigned i=0;i<1024;++i)issue_read(t,i%2?2047:0,i%2?0x34:0x12);
 flush_reads();
 d.rd_valid=0;tick();d.data_ready=0;release(t);
 for(unsigned i=0;i<3000;++i){unsigned op=rng()%6;
  if(live.empty()||op==0){allocate();continue;}
  auto it=live.begin();std::advance(it,rng()%live.size());auto tag=it->first;
  if(op==1){if(!it->second.sealed){for(int k=0;k<4;++k)write(tag,rng()%2048,(unsigned char)rng());publish(tag,1+rng()%3);}else{auto r=command(1,tag,1);need(r.status==3,"double publish succeeded");}}
  else if(op==2)release(tag);
  else if(op==3){if(it->second.sealed&&!it->second.bytes.empty()){auto b=it->second.bytes.begin();std::advance(b,rng()%it->second.bytes.size());read(tag,b->first,false,b->second);}else read(tag,0,true);}
  else if(op==4){write(tag,rng()%2048,(unsigned char)rng(),it->second.sealed);}
  else if(!retired.empty())release(retired[rng()%retired.size()]);
  balanced();
 }
 while(!live.empty())release(live.begin()->first);
 need(d.free_count==slots,"random lifecycle leaked slots");
 auto r=command(3);need(r.status==4,"unknown command accepted");
 t=allocate();write(t,5,0x42);publish(t,1);read(t,5,false,0x42);reset();read(t,5,true);
 need(d.free_count==slots,"reset did not reclaim metadata");
 d.final();std::ofstream out(argv[2]);out<<"{\"status\":\"PASS\",\"slots\":"<<slots<<",\"commands\":"<<commands<<",\"reads\":"<<reads<<",\"writes\":"<<writes<<",\"checks\":"<<checks<<",\"cycles\":"<<cycles<<"}\n";
 std::cout<<"PASS slots="<<slots<<" checks="<<checks<<"\n";return 0;
}catch(const std::exception&e){std::cerr<<"FAIL "<<e.what()<<"\n";return 1;}}
