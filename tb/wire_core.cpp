#include "Vafdx_wire_core.h"
#include "verilated.h"
#include <vector>
#include <deque>
#include <fstream>
#include <iostream>
#include <stdexcept>
using Bytes=std::vector<unsigned char>;
struct Vector{Bytes payload,wire;};
static Vafdx_wire_core d;static unsigned long long cycles=0,checks=0;static unsigned tx_count=0,rx_count=0,mac_drop_count=0,udp_drop_count=0;
static std::deque<Bytes> tx_expected;static std::deque<Vector> rx_expected;static Bytes line,rx_payload;static std::vector<Bytes> transmitted;
static bool loopback=false,held=false;static unsigned held_data=0;static bool held_last=false;
static unsigned last_ifg=12;static bool transmitted_any=false;
static void ck(bool x,const char*s){++checks;if(!x)throw std::runtime_error(std::string(s)+" cycle="+std::to_string(cycles));}
static Bytes hex(const std::string&s){Bytes b;for(size_t i=0;i<s.size();i+=2)b.push_back((unsigned char)std::stoul(s.substr(i,2),nullptr,16));return b;}
static void tick(){
 d.clk=0;d.eval();if(loopback){d.line_rx_valid=d.line_tx_valid;d.line_rx_data=d.line_tx_data;d.line_rx_error=0;d.eval();}
 if(!d.reset){
 if(d.byte_tick){
 if(d.line_tx_valid){
 if(line.empty()){ck(!tx_expected.empty(),"unexpected wire TX");if(transmitted_any)ck(last_ifg>=12,"core IFG violation");last_ifg=0;}
 line.push_back(d.line_tx_data);
 }else{if(!line.empty()){ck(line==tx_expected.front(),"core wire CRC/preamble/packet mismatch");tx_expected.pop_front();transmitted.push_back(line);line.clear();++tx_count;transmitted_any=true;}++last_ifg;}
 }
 if(held)ck(d.app_rx_valid&&d.app_rx_data==held_data&&bool(d.app_rx_last)==held_last,"application payload unstable");
 if(d.app_rx_valid&&d.app_rx_ready){
 ck(!rx_expected.empty(),"unrequested/corrupt application packet delivered");auto&e=rx_expected.front();
 ck(d.app_rx_len==e.payload.size(),"core application length");ck(rx_payload.size()<e.payload.size(),"core application overrun");
 ck(d.app_rx_data==e.payload[rx_payload.size()],"core application byte");ck(bool(d.app_rx_last)==(rx_payload.size()+1==e.payload.size()),"core application last");
 // SN is the byte immediately before the four-byte FCS.
 ck(d.app_rx_sn==e.wire[e.wire.size()-5],"core SN");rx_payload.push_back(d.app_rx_data);
 if(d.app_rx_last){rx_expected.pop_front();rx_payload.clear();++rx_count;}
 }
 held=d.app_rx_valid&&!d.app_rx_ready;held_data=d.app_rx_data;held_last=d.app_rx_last;
 }
 d.clk=1;d.eval();++cycles;
 if(!d.reset){ck(!d.tx_reject,"core rejected a legal application message");if(d.mac_rx_drop)++mac_drop_count;if(d.rx_drop)++udp_drop_count;}
}
static void reset(){ck(tx_expected.empty()&&rx_expected.empty(),"pending expected packets at reset");d.reset=1;d.app_tx_msg_valid=0;d.app_tx_valid=0;d.app_rx_ready=0;d.line_rx_valid=0;d.line_rx_error=0;loopback=false;held=false;for(int i=0;i<4;++i)tick();d.reset=0;line.clear();rx_payload.clear();transmitted_any=false;last_ifg=12;tick();}
static void drain(){d.line_rx_valid=0;d.line_rx_error=0;d.app_rx_ready=1;
 for(unsigned i=0;i<100000 && (!tx_expected.empty()||!rx_expected.empty());++i)tick();ck(tx_expected.empty()&&rx_expected.empty(),"wire core drain timeout");for(int i=0;i<32;++i)tick();}
static void wire_byte(bool valid,unsigned data=0,bool error=false){
 d.clk=0;d.eval();while(!d.byte_tick){tick();d.clk=0;d.eval();}d.line_rx_valid=valid;d.line_rx_data=data;d.line_rx_error=error;tick();}
static void receive(const Bytes&w,const Vector*e=nullptr,bool err=false,unsigned gap=12){if(e)rx_expected.push_back(*e);for(size_t i=0;i<w.size();++i)wire_byte(true,w[i],err&&i==15);for(unsigned i=0;i<gap;++i)wire_byte(false);}
static void send(const Vector&v,bool expect_rx=false){
 tx_expected.push_back(v.wire);if(expect_rx)rx_expected.push_back(v);
 d.app_tx_len=(unsigned)v.payload.size();d.app_tx_msg_valid=1;bool ok=false;
 for(unsigned t=0;t<30000&&!ok;++t){d.clk=0;d.eval();ok=d.app_tx_msg_ready;tick();}ck(ok,"application descriptor timeout");d.app_tx_msg_valid=0;
 for(size_t i=0;i<v.payload.size();++i){d.app_tx_valid=1;d.app_tx_data=v.payload[i];d.app_tx_last=(i+1==v.payload.size());ok=false;
 for(unsigned t=0;t<30000&&!ok;++t){d.clk=0;d.eval();ok=d.app_tx_ready;tick();}ck(ok,"application byte timeout");if(i%7==0){d.app_tx_valid=0;tick();}}
 d.app_tx_valid=0;
}
int main(int argc,char**argv){try{
 Verilated::commandArgs(argc,argv);reset();std::ifstream in("vectors/wire_good.txt");ck(in.good(),"missing wire vectors");std::vector<Vector> v;std::string a,b;while(in>>a>>b)v.push_back({hex(a),hex(b)});ck(v.size()>=260,"wire corpus incomplete");
 d.app_rx_ready=1;
 // End-to-end actual TX octets looped to RX, including normal sequence wrap.
 loopback=true;for(auto& f:v){send(f,true);drain();}loopback=false;ck(mac_drop_count==0&&udp_drop_count==0,"healthy loopback dropped packets");
 // Corrupt FCS is rejected by MAC; corrupted UDP with recomputed FCS is rejected by UDP.
 Bytes bad=v[11].wire;bad[60]^=1;unsigned before=mac_drop_count;receive(bad);ck(mac_drop_count==before+1,"wire FCS corruption reached UDP/application");
 std::ifstream bf("vectors/wire_bad_udp.txt");bf>>b;before=udp_drop_count;unsigned mac_good=d.mac_good_frames;
 receive(hex(b));
 // MAC commits at EOF; the complete frame still has to cross the client bus.
 // A bad UDP frame produces no application descriptor, so drain() alone cannot
 // infer when this hidden pipeline has finished.
 for(unsigned t=0;t<5000&&udp_drop_count==before;++t)tick();
 drain();ck(d.mac_good_frames==mac_good+1&&udp_drop_count==before+1,"MAC/UDP checksum layer isolation failed");
 // Fill the downstream pipeline: application frame + MAC head + MAC tail.
 d.app_rx_ready=0;receive(v[0].wire,&v[0]);receive(v[1].wire,&v[1]);receive(v[2].wire,&v[2]);unsigned full=d.mac_full_drops;
 receive(v[3].wire);ck(d.mac_full_drops==full+1,"wire overload not reported");drain();receive(v[4].wire,&v[4]);drain();
 // Hold one maximum payload at application output; timestamping/network need not stop.
 d.app_rx_ready=0;receive(v[11].wire,&v[11]);for(int i=0;i<200;++i)tick();drain();
 reset();d.app_rx_ready=1;loopback=true;send(v[0],true);drain();loopback=false;
 std::ofstream out("reports/wire_core.json");out<<"{\"status\":\"PASS\",\"loopback_frames\":"<<v.size()+1<<",\"tx_frames\":"<<tx_count<<",\"rx_frames\":"<<rx_count<<",\"mac_drops\":"<<mac_drop_count<<",\"udp_drops\":"<<udp_drop_count<<",\"checks\":"<<checks<<",\"cycles\":"<<cycles<<"}\n";
 std::cout<<"PASS wire core TX="<<tx_count<<" RX="<<rx_count<<" checks="<<checks<<std::endl;return 0;
 }catch(const std::exception&e){std::cerr<<"FAIL "<<e.what()<<std::endl;return 1;}}
