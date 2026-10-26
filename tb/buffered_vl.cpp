#include "Vafdx_buffered_vl.h"
#include "verilated.h"
#include <cstdint>
#include <deque>
#include <fstream>
#include <iostream>
#include <random>
#include <stdexcept>
#include <type_traits>
#include <vector>
using Bytes=std::vector<unsigned char>;
struct Packet{std::uint32_t tag;Bytes bytes;};
static Vafdx_buffered_vl d;
static std::vector<std::deque<Packet>> queues;
static std::mt19937 rng(20261020);
static unsigned vls,slots,used=0,transactions=0,enqueued=0,delivered=0,discarded=0,rejected=0,bytes_in=0,bytes_out=0,reset_canceled=0;
static unsigned long long checks=0,cycles=0;
static void need(bool ok,const char*s){++checks;if(!ok)throw std::runtime_error(s);}
static void tick(){d.clk=0;d.eval();d.clk=1;d.eval();++cycles;}
template<class T>static bool bit(const T& value,unsigned i){if constexpr(std::is_integral<T>::value)return (value>>i)&1;else return (value[i/32]>>(i%32))&1;}
static void balanced(){need(d.pool_free+used==slots&&d.queue_free+used==slots,"integrated pool/queue conservation");for(unsigned i=0;i<vls;++i)need(bit(d.pending,i)==!queues[i].empty(),"integrated pending mismatch");}
static void reset(){d.reset=1;d.cmd_valid=0;d.rsp_ready=0;d.s_valid=0;d.m_ready=0;tick();tick();d.reset=0;
 unsigned n=0;while(!d.cmd_ready){need(!d.s_ready&&!d.m_valid&&!d.rsp_valid,"reset/init leaked transaction");tick();need(++n<vls+slots+12,"integrated initialization timeout");}
 reset_canceled+=used;for(auto&q:queues)q.clear();used=0;balanced();}
static void start(unsigned op,unsigned vl,unsigned length=0){need(d.cmd_ready,"integrated engine not idle");d.cmd_valid=1;d.cmd_op=op;d.cmd_vl=vl;d.cmd_length=length;tick();d.cmd_valid=0;d.cmd_vl=(vl+1)%vls;d.cmd_length=0;}
static void acknowledge(unsigned status,unsigned vl,unsigned length,std::uint32_t tag){
 need(d.rsp_status==status&&d.rsp_vl==vl&&d.rsp_length==length&&d.rsp_tag==tag,"integrated transaction response mismatch");
 for(unsigned n=1+rng()%7;n;--n){tick();need(d.rsp_valid&&!d.cmd_ready&&!d.s_ready&&!d.m_valid&&d.rsp_status==status&&d.rsp_vl==vl&&d.rsp_length==length&&d.rsp_tag==tag,"integrated response changed under backpressure");}
 balanced();d.rsp_ready=1;tick();d.rsp_ready=0;++transactions;need(d.cmd_ready,"transaction did not retire");}
static Bytes payload(unsigned size){Bytes b(size);for(auto&x:b)x=(unsigned char)rng();return b;}
// mode 0 normal, 1 early last, 2 missing last at declared length.
static void enqueue(unsigned vl,unsigned length,unsigned mode=0){
 Bytes b=payload(length);unsigned expected=0;
 if(length==0||length>2048)expected=4;
 else if(used==slots)expected=2;
 else if(mode)expected=5;
 else if(queues[vl].size()>=4)expected=2;
 start(0,vl,length);unsigned pos=0,n=0;bool offering=false;unsigned wire_length=mode==1?length/2:length;
 while(!d.rsp_valid){
  if(!offering&&pos<wire_length&&d.s_ready&&rng()%5!=0)offering=true;
  d.s_valid=offering;d.s_data=pos<b.size()?b[pos]:0;
  d.s_last=mode==2?0:pos+1==(mode==1?length/2:length);
  d.clk=0;d.eval();bool accepted=d.s_valid&&d.s_ready;
  need(!d.m_valid,"enqueue unexpectedly delivered bytes");tick();if(accepted){++pos;++bytes_in;offering=false;}
  need(++n<length*8+slots+100,"enqueue timeout");
 }
 d.s_valid=0;d.eval();need(d.rsp_status==expected,"enqueue/rollback status mismatch");
 unsigned out_length=0;std::uint32_t tag=0;
 if(!expected){need(pos==length,"successful enqueue did not consume full payload");tag=d.rsp_tag;out_length=length;
  for(const auto&q:queues)for(const auto&p:q)need((p.tag&(slots-1))!=(tag&(slots-1)),"one pool slot owned by two queued packets");
  queues[vl].push_back({tag,b});++used;++enqueued;
 }else{++rejected;if(length==0||length>2048||used==slots)need(pos==0,"early rejection consumed payload");else need(pos==wire_length,"late rejection did not consume the expected input frame");}
 acknowledge(expected,vl,out_length,tag);
}
static void consume(unsigned vl,bool discard=false){
 bool empty=queues[vl].empty();Packet expected=empty?Packet{}:queues[vl].front();start(discard?2:1,vl);
 unsigned pos=0,n=0;bool stalled=false;unsigned held=0;bool held_sop=false,held_last=false;
 while(!d.rsp_valid){
  d.m_ready=rng()%4!=0;d.clk=0;d.eval();
  if(stalled)need(d.m_valid&&d.m_data==held&&d.m_sop==held_sop&&d.m_last==held_last,"stream changed under backpressure");
  need(!d.s_ready,"dequeue unexpectedly accepted ingress");
  stalled=d.m_valid&&!d.m_ready;held=d.m_data;held_sop=d.m_sop;held_last=d.m_last;
  if(d.m_valid){need(!empty&&!discard&&pos<expected.bytes.size(),"unsolicited output byte");need(d.m_vl==vl&&d.m_length==expected.bytes.size(),"stream descriptor metadata mismatch");need(d.m_data==expected.bytes[pos]&&bool(d.m_sop)==(pos==0)&&bool(d.m_last)==(pos+1==expected.bytes.size()),"dequeued payload/order/boundary mismatch");if(d.m_ready){++pos;++bytes_out;}}
  tick();need(++n<expected.bytes.size()*10+slots+100,"dequeue timeout");
 }
 d.m_ready=0;
 if(!empty){need(pos==(discard?0:expected.bytes.size()),"dequeue length mismatch");queues[vl].pop_front();--used;if(discard)++discarded;else ++delivered;}
 acknowledge(empty?1:0,vl,empty?0:expected.bytes.size(),empty?0:expected.tag);
}
int main(int argc,char**argv){try{
 Verilated::commandArgs(argc,argv);need(argc==4,"usage vls slots result");vls=std::stoul(argv[1]);slots=std::stoul(argv[2]);queues.resize(vls);reset();
 for(unsigned vl=0;vl<vls;++vl)consume(vl);
 enqueue(0,0);enqueue(0,2049);enqueue(0,15,1);enqueue(0,15,2);
 for(unsigned i=0;i<4;++i)enqueue(0,i==0?2048:1+i);enqueue(0,13);consume(0,true);
 for(unsigned i=0;i<3;++i)consume(0);
 for(unsigned i=0;i<slots;++i)enqueue(i%vls,1+i%64);enqueue(vls-1,8);
 for(unsigned vl=0;vl<vls;++vl)while(!queues[vl].empty())consume(vl,(vl%3)==0);
 // Every VL, including the highest index, shares the same payload pool.
 for(unsigned vl=0;vl<vls;++vl){enqueue(vl,1+(vl*17)%257);consume(vl);}
 for(unsigned i=0;i<1200;++i){unsigned vl=rng()%vls;if(rng()%3==0)consume(vl,rng()%4==0);else enqueue(vl,1+rng()%300);}
 for(unsigned vl=0;vl<vls;++vl)while(!queues[vl].empty())consume(vl);
 // Common reset rolls back a partially written allocation.
 start(0,0,20);while(!d.s_ready)tick();d.s_valid=1;d.s_data=0x37;d.s_last=0;tick();tick();reset();consume(0);
 // Common reset cancels a held output response and queued ownership together.
 enqueue(vls-1,20);start(1,vls-1);while(!d.m_valid)tick();for(unsigned i=0;i<8;++i)tick();reset();consume(vls-1);
 start(3,0);while(!d.rsp_valid)tick();acknowledge(3,0,0,0);
 need(d.pool_free==slots&&d.queue_free==slots,"final integration leak");
 need(enqueued==delivered+discarded+reset_canceled,"packet delivery/discard/reset accounting mismatch");
 d.final();std::ofstream out(argv[3]);out<<"{\"status\":\"PASS\",\"vl_count\":"<<vls<<",\"slots\":"<<slots<<",\"transactions\":"<<transactions<<",\"enqueued\":"<<enqueued<<",\"delivered\":"<<delivered<<",\"discarded\":"<<discarded<<",\"reset_canceled\":"<<reset_canceled<<",\"rejected\":"<<rejected<<",\"bytes_in\":"<<bytes_in<<",\"bytes_out\":"<<bytes_out<<",\"checks\":"<<checks<<",\"cycles\":"<<cycles<<"}\n";
 std::cout<<"PASS buffered VL integration "<<vls<<" checks="<<checks<<"\n";return 0;
}catch(const std::exception&e){std::cerr<<"FAIL "<<e.what()<<"\n";return 1;}}
