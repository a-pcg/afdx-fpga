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
#include <utility>
#include <vector>
// T7 directed backpressure test for the grouped pool control pipeline.
// Verifies five-field (data/valid/tag/offset/error) lockstep, random ready,
// reset with a non-empty pipeline, same-cycle write/read, and the explicit
// +1 cycle write-commit/read-data latency model.
static Vafdx_packet_pool d;
static std::mt19937 rng(20261020);
static unsigned long long checks=0,cycles=0;
static unsigned reads=0,writes=0,resets=0;
static const unsigned SLOTS=8;
static void need(bool ok,const char*s){++checks;if(!ok)throw std::runtime_error(s);}
static void tick(){d.clk=0;d.eval();d.clk=1;d.eval();++cycles;}
static unsigned char pattern(unsigned slot,unsigned offset){unsigned x=slot*0x9e3779b9u+offset*0x85ebca6bu;x^=x>>16;return (unsigned char)x;}
struct Exp{std::uint32_t tag;unsigned offset;bool error;unsigned char value;};
static std::deque<Exp> pending;
static std::map<std::pair<std::uint32_t,unsigned>,unsigned char> memory_model;
static unsigned char known(const std::uint32_t tag,unsigned offset){
 auto it=memory_model.find({tag,offset});if(it==memory_model.end())throw std::runtime_error("backpressure test read an unwritten address");return it->second;
}
static void check_front(){
 const Exp&e=pending.front();
 need(d.data_valid,"missing read response");
 if(d.data_tag!=e.tag||d.data_offset!=e.offset||bool(d.data_error)!=e.error||d.data_byte!=(e.error?0:e.value)){
  std::cerr<<"DBG cyc="<<cycles<<" exp tag="<<e.tag<<" off="<<e.offset<<" err="<<e.error<<" val="<<(unsigned)e.value
           <<" got tag="<<d.data_tag<<" off="<<d.data_offset<<" err="<<(unsigned)d.data_error<<" val="<<(unsigned)d.data_byte
           <<" pending="<<pending.size()<<"\n";
 }
 need(d.data_tag==e.tag,"data/tag lockstep mismatch");
 need(d.data_offset==e.offset,"data/offset lockstep mismatch");
 need(bool(d.data_error)==e.error,"data/error lockstep mismatch");
 need(d.data_byte==(e.error?0:e.value),"data payload mismatch");
}
static void reset(){
 d.reset=1;d.cmd_valid=0;d.rsp_ready=0;d.wr_valid=0;d.rd_valid=0;d.data_ready=0;tick();tick();d.reset=0;
 unsigned n=0;while(!d.initialized){need(!d.cmd_ready&&!d.wr_ready&&!d.rd_ready&&!d.data_valid,"access leaked during init");tick();need(++n<SLOTS+8,"init timeout");}
 pending.clear();memory_model.clear();++resets;
}
static std::uint32_t command(unsigned op,std::uint32_t tag,unsigned refs){
 need(d.cmd_ready,"command engine busy");d.cmd_op=op;d.cmd_tag=tag;d.cmd_consumers=refs;d.cmd_valid=1;tick();d.cmd_valid=0;
 unsigned n=0;while(!d.rsp_valid){tick();need(++n<SLOTS+8,"command timeout");}
 std::uint32_t rt=d.rsp_tag;unsigned st=d.rsp_status;d.rsp_ready=1;tick();d.rsp_ready=0;need(st==0,"command status");return rt;
}
static void write(std::uint32_t tag,unsigned offset,unsigned char value){
 d.wr_valid=1;d.wr_tag=tag;d.wr_offset=offset;d.wr_data=value;d.clk=0;d.eval();need(d.wr_ready&&!d.wr_error,"write rejected");tick();d.wr_valid=0;++writes;memory_model[{tag,offset}]=value;
}
// drive one read request, wait for the handshake, push the expected response.
static void request(std::uint32_t tag,unsigned offset,bool error){
 need(d.rd_ready,"read port not ready");d.rd_valid=1;d.rd_tag=tag;d.rd_offset=offset;
 d.clk=0;d.eval();need(d.rd_ready,"read not accepted after ready");
 d.clk=1;d.eval();++cycles;d.rd_valid=0;++reads;
 pending.push_back(Exp{tag,offset,error,(unsigned char)(error?0:known(tag,offset))});
}
static void wait_response(){
 unsigned n=0;d.data_ready=0;
 while(true){d.clk=0;d.eval();if(d.data_valid)break;d.clk=1;d.eval();++cycles;need(++n<16,"read response timeout");}
 check_front();
}
static void consume_response(){
 d.data_ready=1;d.clk=0;d.eval();check_front();d.clk=1;d.eval();++cycles;pending.pop_front();d.data_ready=0;
}
static void drain(){
 while(!pending.empty()){wait_response();consume_response();}
 d.clk=0;d.eval();need(!d.data_valid,"pipeline did not drain");tick();
}
int main(int argc,char**argv){try{
 Verilated::commandArgs(argc,argv);need(argc==2,"usage result.json");
 reset();
 std::vector<std::uint32_t> tags;
 for(unsigned i=0;i<SLOTS;++i)tags.push_back(command(0,0,0));
 const unsigned offsets[5]={0,1,100,1023,2047};
 for(unsigned i=0;i<SLOTS;++i)for(unsigned k=0;k<5;++k)write(tags[i],offsets[k],pattern(i,offsets[k]));
 for(unsigned i=0;i<SLOTS;++i)command(1,tags[i],1);
 tick();tick(); // let the +1-cycle write pipeline commit before reads
 // 1) random ready, mixed valid/stale-tag reads, five-field lockstep.
 unsigned stale_tag=tags[SLOTS-1];
 command(2,stale_tag,0); // retire one slot; its tag becomes unowned
 bool offer_active=false;unsigned offer_slot=0,offer_off=0;bool offer_error=false;
 for(unsigned i=0;i<20000;++i){
  d.data_ready=(rng()%4)!=0;
  if(!offer_active && pending.size()<2 && (rng()%3)!=0){
   offer_error=(rng()%8)==0;offer_slot=rng()%(SLOTS-1);offer_off=offsets[rng()%5];
   d.rd_valid=1;d.rd_tag=offer_error?stale_tag:tags[offer_slot];d.rd_offset=offer_off;offer_active=true;
  }
  d.clk=0;d.eval();
  bool ready=d.rd_ready;bool valid=d.data_valid;bool consume_ok=valid&&d.data_ready;
  if(valid)check_front();
  d.clk=1;d.eval();++cycles;
  if(offer_active&&ready){
   pending.push_back(Exp{offer_error?stale_tag:tags[offer_slot],offer_off,offer_error,(unsigned char)(offer_error?0:known(offer_error?stale_tag:tags[offer_slot],offer_off))});
   offer_active=false;d.rd_valid=0;++reads;
  }
  if(consume_ok)pending.pop_front();
 }
 d.rd_valid=0;d.data_ready=1;drain();
 // 2) same-cycle write (unsealed slot) + read (published slot), and the
 // explicit write-commit +1 model: writes are only legal before publish, so the
 // same address cannot be both writable and readable in this pool.
 {
  std::uint32_t tu=command(0,0,0); // slot freed by the stale-tag release
  unsigned char wv=0x5a;write(tu,50,wv);
  unsigned char rv=known(tags[0],100);
  d.wr_valid=1;d.wr_tag=tu;d.wr_offset=60;d.wr_data=0x3c;
  d.rd_valid=1;d.rd_tag=tags[0];d.rd_offset=100;d.data_ready=0;
  d.clk=0;d.eval();need(d.wr_ready&&d.rd_ready,"same-cycle write/read not ready");
  d.clk=1;d.eval();++cycles;d.wr_valid=0;d.rd_valid=0;++writes;
  memory_model[{tu,60}]=0x3c;
  pending.push_back(Exp{tags[0],100,false,rv});
  wait_response();need(d.data_byte==rv,"concurrent read saw wrong published data");consume_response();
  command(1,tu,1);
  tick();tick(); // let the +1-cycle writes commit before reading them back
  request(tu,50,false);wait_response();need(d.data_byte==wv,"write commit +1 model mismatch (offset 50)");consume_response();
  request(tu,60,false);wait_response();need(d.data_byte==0x3c,"concurrent write did not commit");consume_response();
 }
 // 3) stall stability: hold data for several cycles with data_ready=0.
 {
  std::uint32_t tg=tags[1];unsigned off=1023;
  request(tg,off,false);wait_response();
  for(unsigned k=0;k<8;++k){d.data_ready=0;d.clk=0;d.eval();check_front();d.clk=1;d.eval();++cycles;}
  consume_response();
 }
 // 4) reset while the pipeline holds valid data.
 {
  request(tags[2],offsets[1],false);request(tags[3],offsets[2],false);
  d.data_ready=0;
  for(unsigned k=0;k<3;++k)tick();
  need(d.data_valid,"pipeline not full before reset");
  reset();
  need(!d.data_valid&&!d.data_error,"reset leaked pipeline validity");
  for(unsigned k=0;k<8;++k){d.data_ready=1;tick();need(!d.data_valid,"stale read appeared after reset");}
  d.data_ready=0;pending.clear();
 }
 // 5) final reset sanity.
 reset();
 need(d.free_count==SLOTS,"reset did not restore free slots");
 d.final();std::ofstream out(argv[1]);
 out<<"{\"status\":\"PASS\",\"slots\":"<<SLOTS<<",\"checks\":"<<checks<<",\"cycles\":"<<cycles<<",\"reads\":"<<reads<<",\"writes\":"<<writes<<",\"resets\":"<<resets<<"}\n";
 std::cout<<"PASS pool backpressure checks="<<checks<<" reads="<<reads<<" writes="<<writes<<"\n";return 0;
 }catch(const std::exception&e){std::cerr<<"FAIL "<<e.what()<<" cycles="<<cycles<<"\n";return 1;}}
