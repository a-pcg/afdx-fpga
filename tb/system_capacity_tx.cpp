#include "Vafdx_system_top.h"
#include "Vafdx_system_top___024root.h"
#include "verilated.h"
#include <array>
#include <cstdint>
#include <deque>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>
// A: system-level TX capacity/throughput with all 128 TX VLs.
// Configure VL0..127 (cfg_valid loop, BAG exp 0), run=1, enqueue exactly one frame
// per VL (mostly 1471 B, mixed 1/17/63 B) = 128 frames through the 64-slot pool.
// Admission is blocking: the TB keeps attempting app_msg_valid until the wrapper
// accepts; a status-2 (pool full) response is retried after the TX frees slots.
// The TX line is looped back into both RX networks (8-VL RX subset), so frames for
// VL>=8 are dropped by the RX parsers by design.
// Measured: line bytes vs simulation window (100 MHz, 8-cycle byte_ce => 100 Mbit/s
// nominal), SFD count, per-frame byte cadence, per-VL BAG intervals, TX fault, RX
// counters and pool peak.
static Vafdx_system_top d;
static unsigned long long checks=0, cycles=0;
static const unsigned NVL=128;
static const unsigned RX_VLS=8;
static const unsigned POOL_SLOTS=64;
static std::array<unsigned,NVL> frame_len{};
static std::array<unsigned long long,NVL> last_sfd{};
static std::array<bool,NVL> has_sfd{};
static std::array<unsigned,NVL> sfd_per_vl{};
static std::array<std::deque<std::vector<unsigned char>>,RX_VLS> rx_expect;
static std::deque<unsigned long long> rx_sfd_time[RX_VLS];
static std::array<unsigned,RX_VLS> rx_next_sn{};
static std::array<unsigned,RX_VLS> rx_delivered{};
static unsigned delivered=0, decisions=0;
static unsigned long long line_bytes=0, first_byte=0, last_byte=0, prev_byte=0;
static int cad_min=0, cad_max=0; static unsigned long long cadence_violations=0;
static unsigned sfd_count=0, frames_on_wire=0;
static unsigned long long bag_intervals=0, bag_violations=0, min_bag=~0ULL;
static unsigned long long max_sfd_delta=0;
static unsigned pool_peak=0, pool_min_free=POOL_SLOTS;
static unsigned long long run_start_cycle=0;
static unsigned long long enqueue_attempts=0, full_retries=0;
static std::vector<unsigned char> rx_frame;
static bool frame_active=false;
static unsigned frame_seen=0, frame_total=0;
// Per-frame gap measurement: idle between the previous frame's last byte and this
// frame's first byte, bucketed by this frame's wire length. On the serialized T8
// design this gap is the next frame's pool-READ -> encoder LOAD time (plus IFG),
// i.e. exactly what a prefetch design has to hide.
static unsigned long long pending_gap=0;
static std::vector<unsigned long long> gap_by_wire[4];
static unsigned gap_bucket(unsigned w){ if(w<96)return 0; if(w<128)return 1; if(w<1024)return 2; return 3; }
static void need(bool ok,const std::string&s){++checks;if(!ok)throw std::runtime_error(s);}
static unsigned wire_len(unsigned vl){
    unsigned body=frame_len[vl]+43u; if(body<60u)body=60u;
    return 8u+body+4u;
}
static unsigned pool_used(){
    unsigned n=0; auto* r=d.rootp;
    for(unsigned i=0;i<POOL_SLOTS;++i) if(r->afdx_system_top__DOT__tx__DOT__buffers__DOT__pool__DOT__references[i]!=0) ++n;
    return n;
}
static void track_pool(){
    unsigned used=pool_used();
    if(used>pool_peak)pool_peak=used;
    if(POOL_SLOTS-used<pool_min_free)pool_min_free=POOL_SLOTS-used;
}
static void byte_account(){
    if(frame_active&&frame_total&&frame_seen>=frame_total) frame_active=false;
    if(frame_active){
        long long gap=(long long)(d.monotonic_time-prev_byte);
        if(cad_min==0||gap<cad_min)cad_min=(int)gap;
        if(gap>cad_max)cad_max=(int)gap;
        if(gap!=8)++cadence_violations;
        ++frame_seen;
    }else{
        frame_active=true; frame_seen=1; frame_total=0; ++frames_on_wire;
        pending_gap=line_bytes?d.monotonic_time-prev_byte:0;
    }
    if(!line_bytes) first_byte=d.monotonic_time;
    if(frame_seen==8&&d.tx_line_data==0xd5u){
        frame_total=wire_len(d.sfd_vl);
        gap_by_wire[gap_bucket(frame_total)].push_back(pending_gap);
    }
    if(frame_total&&frame_seen==frame_total) frame_active=false;
    prev_byte=d.monotonic_time; last_byte=d.monotonic_time; ++line_bytes;
}
static void step(){
    d.clk=0; d.eval();
    bool byte_now=d.tx_line_valid&&d.tx_byte_ce&&!d.reset;
    bool drive=d.tx_line_valid&&!d.reset;
    d.rx_line_valid_a=drive; d.rx_line_valid_b=drive;
    d.rx_line_data_a=d.tx_line_data; d.rx_line_data_b=d.tx_line_data;
    d.rx_line_error_a=0; d.rx_line_error_b=0;
    d.rx_ready=1;
    if(!d.reset&&byte_now) byte_account();
    if(!d.reset&&d.rx_valid&&d.rx_ready){
        rx_frame.push_back(d.rx_data);
        if(d.rx_last){
            unsigned vl=d.rx_vl;
            need(vl<RX_VLS,"RX delivered an out-of-range VL index");
            need(!rx_expect[vl].empty(),"RX delivered an unsolicited frame");
            const std::vector<unsigned char>& exp=rx_expect[vl].front();
            need(rx_frame.size()==exp.size(),"RX delivered wrong payload length");
            need(rx_frame==exp,"RX delivered corrupted payload");
            need(d.rx_sn==rx_next_sn[vl],"RX delivered wrong SN");
            need(!rx_sfd_time[vl].empty(),"RX delivered without a recorded SFD");
            std::uint64_t delta=d.rx_time>rx_sfd_time[vl].front()?d.rx_time-rx_sfd_time[vl].front():rx_sfd_time[vl].front()-d.rx_time;
            if(delta>max_sfd_delta)max_sfd_delta=delta;
            need(delta==0,"RX delivery time differs from the SFD time");
            ++rx_next_sn[vl];
            rx_expect[vl].pop_front(); rx_sfd_time[vl].pop_front();
            ++delivered; ++rx_delivered[vl]; rx_frame.clear();
        }
    }
    d.clk=1; d.eval(); ++cycles;
    if(!d.reset){
        need(!d.tx_fault,"TX reported an internal fault");
        if(d.rx_decision)++decisions;
        if(d.sfd_pulse){
            unsigned vl=d.sfd_vl;
            need(vl<NVL,"SFD VL out of range");
            ++sfd_count; ++sfd_per_vl[vl];
            if(has_sfd[vl]){
                unsigned long long gap=d.sfd_time-last_sfd[vl];
                ++bag_intervals;
                if(gap<min_bag)min_bag=gap;
                if(gap<100000ULL)++bag_violations;
            }
            last_sfd[vl]=d.sfd_time; has_sfd[vl]=true;
            if(vl<RX_VLS) rx_sfd_time[vl].push_back(d.sfd_time);
        }
    }
    track_pool();
}
static void reset_system(){
    d.reset=1; d.run=0; d.app_enable=0; d.cfg_valid=0; d.app_msg_valid=0; d.app_valid=0;
    d.app_rsp_ready=0; d.drop_valid=0; d.drop_rsp_ready=0; d.rx_ready=1;
    step(); step(); d.reset=0;
    unsigned n=0; while(!d.initialized){step();need(++n<4000,"system initialization timeout");}
}
static void configure_vl(unsigned vl){
    d.cfg_valid=1; d.cfg_vl=vl; d.cfg_bag_exp=0;
    unsigned cw=0;
    while(true){ d.clk=0; d.eval(); if(d.cfg_ready)break; step(); need(++cw<32,"configuration not ready"); }
    step();
}
static std::vector<unsigned char> make_payload(unsigned vl,unsigned len){
    std::vector<unsigned char> p(len);
    for(unsigned i=0;i<len;++i)p[i]=(unsigned char)((i*17u+vl*31u+7u)&0xffu);
    return p;
}
static unsigned attempt_admit(unsigned vl,const std::vector<unsigned char>& payload){
    ++enqueue_attempts;
    d.app_msg_valid=1; d.app_vl=vl; d.app_length=payload.size();
    unsigned n=0;
    while(true){ d.clk=0; d.eval(); bool accept=d.app_msg_ready; step(); if(accept)break; need(++n<4000000,"app admission timeout"); }
    d.app_msg_valid=0; d.app_vl=0; d.app_length=0; d.eval();
    unsigned pos=0; n=0;
    while(!d.app_rsp_valid){
        d.app_valid=(pos<payload.size());
        d.app_data=pos<payload.size()?payload[pos]:0;
        d.app_last=(pos+1==payload.size());
        d.clk=0; d.eval();
        bool byte=d.app_valid&&d.app_ready;
        step();
        if(byte)++pos;
        need(++n<payload.size()*12+2000000,"app payload timeout");
    }
    d.app_valid=0; d.app_data=0; d.app_last=0;
    unsigned status=d.app_rsp_status;
    d.app_rsp_ready=1; step(); d.app_rsp_ready=0;
    if(status==0) need(pos==payload.size(),"successful enqueue consumed the wrong byte count");
    else need(pos==0,"rejected enqueue consumed payload bytes");
    return status;
}
static void enqueue_blocking(unsigned vl,const std::vector<unsigned char>& payload){
    unsigned retries=0;
    while(true){
        unsigned st=attempt_admit(vl,payload);
        if(st==0)return;
        if(st==2){ ++full_retries; for(unsigned k=0;k<256;++k)step(); need(++retries<2000000,"full-pool retry timeout"); continue; }
        throw std::runtime_error("unexpected app response status "+std::to_string(st));
    }
}
static void drain(){
    unsigned long long n=0;
    while((sfd_count<NVL||d.busy)&&n<12000000){step();++n;}
    need(n<12000000,"drain timeout");
    unsigned long long before=decisions; unsigned long long quiet=0;
    while(n<12000000&&quiet<40000){step();++n; if(decisions==before)++quiet; else{quiet=0;before=decisions;}}
}
int main(int argc,char**argv){try{
    Verilated::commandArgs(argc,argv); need(argc==2,"usage result.json");
    reset_system();
    for(unsigned vl=0;vl<NVL;++vl)configure_vl(vl);
    for(unsigned vl=0;vl<NVL;++vl){
        unsigned len=1471;
        if(vl%13==0)len=1; else if(vl%11==0)len=17; else if(vl%7==0)len=63;
        frame_len[vl]=len;
        if(vl<RX_VLS) rx_expect[vl].push_back(make_payload(vl,len));
    }
    d.run=1; d.app_enable=1;
    run_start_cycle=cycles;
    for(unsigned vl=0;vl<NVL;++vl) enqueue_blocking(vl,make_payload(vl,frame_len[vl]));
    d.app_enable=0;
    drain();
    need(sfd_count==NVL,"SFD count mismatch: "+std::to_string(sfd_count));
    unsigned long long expected_bytes=0;
    for(unsigned vl=0;vl<NVL;++vl){
        need(sfd_per_vl[vl]==1,"VL did not produce exactly one SFD");
        expected_bytes+=wire_len(vl);
    }
    need(frames_on_wire==NVL,"line frame count mismatch: "+std::to_string(frames_on_wire));
    need(line_bytes==expected_bytes,"line byte count mismatch: "+std::to_string(line_bytes)+" vs "+std::to_string(expected_bytes));
    need(cadence_violations==0,"TX byte cadence violations");
    need(bag_violations==0,"BAG interval violations");
    need(d.tx_fault==false,"TX fault");
    need(delivered==RX_VLS,"RX delivered unexpected frame count (only VL0..7 are parsed): "+std::to_string(delivered));
    unsigned long long window=last_byte>=first_byte?last_byte-first_byte+8:1;
    double achieved_mbps=(double)line_bytes*8.0*100.0/(double)window;
    double achieved_MBps=(double)line_bytes*100.0/(double)window;
    double sim_ms=(double)(last_byte-run_start_cycle)*10.0/1e6;
    double sim_mbps=(double)line_bytes*8.0/(sim_ms/1000.0)/1e6;
    // afdx_rx_dual exposes 64-bit MAC counters that are two 32-bit per-network halves.
    unsigned long long bad_fcs=(d.bad_fcs&0xffffffffULL)+(d.bad_fcs>>32);
    unsigned long long full_drops=(d.full_drops&0xffffffffULL)+(d.full_drops>>32);
    std::cout<<"PASS capacity_tx sfd="<<sfd_count<<" line_bytes="<<line_bytes<<" window="<<window
             <<" achieved_mbps="<<achieved_mbps<<" delivered="<<delivered<<" pool_peak="<<pool_peak
             <<" frames="<<frames_on_wire<<" cadence_violations="<<cadence_violations<<"\n";
    for(unsigned b=0;b<4;++b){
        if(gap_by_wire[b].empty())continue;
        unsigned long long sum=0,mn=~0ULL,mx=0;for(auto g:gap_by_wire[b]){sum+=g;if(g<mn)mn=g;if(g>mx)mx=g;}
        std::cout<<"gap bucket "<<b<<": frames="<<gap_by_wire[b].size()<<" min="<<mn<<" mean="<<(sum/gap_by_wire[b].size())<<" max="<<mx<<"\n";
    }
    std::cout<<"info: full_retries="<<full_retries<<" attempts="<<enqueue_attempts
             <<" rx_delivered="<<d.delivered_frames<<" rx_rejected="<<d.rejected_frames
             <<" bad_fcs="<<bad_fcs<<" full_drops="<<full_drops<<"\n";
    d.final();
    std::ofstream out(argv[1]);
    out<<"{\"status\":\"PASS\",\"test\":\"system_capacity_tx\",\"vl_configured\":"<<NVL
       <<",\"frames_enqueued\":"<<NVL<<",\"sfd_count\":"<<sfd_count<<",\"frames_on_wire\":"<<frames_on_wire
       <<",\"line_bytes\":"<<line_bytes<<",\"expected_line_bytes\":"<<expected_bytes
       <<",\"first_byte_cycle\":"<<first_byte<<",\"last_byte_cycle\":"<<last_byte
       <<",\"measure_window_cycles\":"<<window
       <<",\"achieved_mbps\":"<<achieved_mbps<<",\"achieved_MBps\":"<<achieved_MBps
       <<",\"byte_ce_cycles\":8,\"clock_mhz\":100"
       <<",\"min_byte_gap\":"<<cad_min<<",\"max_byte_gap\":"<<cad_max<<",\"cadence_violations\":"<<cadence_violations
       <<",\"bag_intervals_checked\":"<<bag_intervals<<",\"bag_violations\":"<<bag_violations
       <<",\"min_bag_interval_cycles\":"<<(bag_intervals?min_bag:0)
       <<",\"tx_fault\":"<<(d.tx_fault?1:0)
       <<",\"rx_delivered_frames\":"<<d.delivered_frames<<",\"rx_rejected_frames\":"<<d.rejected_frames
       <<",\"rx_bad_fcs\":"<<bad_fcs<<",\"rx_full_drops\":"<<full_drops
       <<",\"rx_parsed_vls\":8,\"max_sfd_time_delta\":"<<max_sfd_delta
       <<",\"pool_slots\":"<<POOL_SLOTS<<",\"pool_peak_used\":"<<pool_peak<<",\"pool_min_free\":"<<pool_min_free
       <<",\"enqueue_attempts\":"<<enqueue_attempts<<",\"full_retries\":"<<full_retries
       <<",\"cycles\":"<<cycles<<",\"checks\":"<<checks
       <<",\"sim_window_ms\":"<<sim_ms<<",\"sim_window_mbps\":"<<sim_mbps
       <<",\"scope\":\"RTL simulation at 100 MHz with the 8-cycle byte_ce; wire rate 100 Mbit/s nominal; RX is the fixed 8-VL development subset so VL>=8 frames are dropped by design\""
       <<"}\n";
    return 0;
}catch(const std::exception&e){std::cerr<<"FAIL "<<e.what()<<" cycles="<<cycles<<" sfd="<<sfd_count<<"\n";return 1;}}
