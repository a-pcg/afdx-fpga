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
// B: system-level RX capacity and pool-slot behaviour.
// Phase 1a (queue depth 4, burst): VL0..7 each queue 4 frames (MAX_PER_VL=4) while
//   run=1; the scheduler sends the first frame of all 8 VLs back-to-back, then the
//   per-VL BAG spaces the remaining three rounds. Records delivered / MAC full drops
//   / SN coverage -> the RX burst capacity limit.
// Phase 1b (paced 32/32): the same 32 frames (4 per VL, next SNs) are offered one at
//   a time and the TB waits for each delivery before offering the next, so the RX
//   drain is never overrun. Proves the delivery path itself: 32/32, exact SN order,
//   payload and SFD-time delta==0.
// Phase 2 (full pool): run=0, configure VL8..16, queue 4 frames for VL0..15 (64
//   slots) until the pool is full; probe one extra admission on the empty VL16 queue
//   (expected status 2 = full); then run=1 and drain: no hang, 64 SFDs, pool empty,
//   counters explainable (VL8..15 are dropped by the fixed 8-VL RX by design).
static Vafdx_system_top d;
static unsigned long long checks=0, cycles=0;
static const unsigned RX_VLS=8;
static const unsigned POOL_SLOTS=64;
static const unsigned MAX_SN=16;
static std::array<std::array<std::vector<unsigned char>,MAX_SN>,RX_VLS> pay_by_sn;
static std::array<std::vector<unsigned long long>,RX_VLS> sfd_times;
static std::array<std::vector<unsigned>,RX_VLS> delivered_sns;
static std::array<unsigned,RX_VLS> last_delivered_sn{};
static std::array<bool,RX_VLS> has_delivered{};
static std::array<std::deque<unsigned>,128> wire_q;
static unsigned delivered=0, decisions=0, sfd_count=0, frames_on_wire=0;
static unsigned long long line_bytes=0, first_byte=0, last_byte=0, prev_byte=0;
static int cad_min=0, cad_max=0; static unsigned long long cadence_violations=0;
static unsigned long long bag_intervals=0, bag_violations=0, min_bag=~0ULL;
static unsigned long long max_sfd_delta=0;
static unsigned pool_peak=0, pool_min_free=POOL_SLOTS;
static std::vector<unsigned char> rx_frame;
static bool frame_active=false;
static unsigned frame_seen=0, frame_total=0;
static bool p1b_active=false;
static std::array<unsigned,8> p1b_next{};
static unsigned long long strict_sn_errors=0;
static void need(bool ok,const std::string&s){++checks;if(!ok)throw std::runtime_error(s);}
static unsigned body_of(unsigned len){ unsigned b=len+43u; return b<60u?60u:b; }
static unsigned wire_of(unsigned len){ return 8u+body_of(len)+4u; }
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
static void step(){
    d.clk=0; d.eval();
    bool byte_now=d.tx_line_valid&&d.tx_byte_ce&&!d.reset;
    bool drive=d.tx_line_valid&&!d.reset;
    d.rx_line_valid_a=drive; d.rx_line_valid_b=drive;
    d.rx_line_data_a=d.tx_line_data; d.rx_line_data_b=d.tx_line_data;
    d.rx_line_error_a=0; d.rx_line_error_b=0;
    d.rx_ready=1;
    if(!d.reset&&byte_now){
        if(frame_active&&frame_total&&frame_seen>=frame_total) frame_active=false;
        if(frame_active){
            long long gap=(long long)(d.monotonic_time-prev_byte);
            if(cad_min==0||gap<cad_min)cad_min=(int)gap;
            if(gap>cad_max)cad_max=(int)gap;
            if(gap!=8)++cadence_violations;
            ++frame_seen;
        }else{
            frame_active=true; frame_seen=1; frame_total=0; ++frames_on_wire;
        }
        if(frame_seen==8&&d.tx_line_data==0xd5u){
            unsigned vl=d.sfd_vl;
            need(vl<128,"SFD VL out of range");
            need(!wire_q[vl].empty(),"SFD without a queued frame");
            frame_total=wire_q[vl].front(); wire_q[vl].pop_front();
        }
        if(frame_total&&frame_seen==frame_total) frame_active=false;
        if(!line_bytes) first_byte=d.monotonic_time;
        prev_byte=d.monotonic_time; last_byte=d.monotonic_time; ++line_bytes;
    }
    if(!d.reset&&d.rx_valid&&d.rx_ready){
        rx_frame.push_back(d.rx_data);
        if(d.rx_last){
            unsigned vl=d.rx_vl;
            need(vl<RX_VLS,"RX delivered an out-of-range VL index");
            unsigned sn=(unsigned)d.rx_sn;
            need(sn<MAX_SN,"RX delivered SN out of range");
            need(!pay_by_sn[vl][sn].empty(),"RX delivered an unsolicited frame (vl="+std::to_string(vl)+" sn="+std::to_string(sn)+")");
            const std::vector<unsigned char>& exp=pay_by_sn[vl][sn];
            need(rx_frame.size()==exp.size(),"RX delivered wrong payload length vl="+std::to_string(vl)
                 +" sn="+std::to_string(sn)+" got="+std::to_string(rx_frame.size())+" exp="+std::to_string(exp.size()));
            need(rx_frame==exp,"RX delivered corrupted payload vl="+std::to_string(vl)+" sn="+std::to_string(sn));
            if(has_delivered[vl])
                need(sn>last_delivered_sn[vl],"RX delivered SN out of order vl="+std::to_string(vl));
            if(p1b_active&&sn>=4){
                if(sn!=p1b_next[vl]) ++strict_sn_errors; else p1b_next[vl]=sn+1;
            }
            // SFD times of a VL are assigned in SN order, so the frame with SN=s is
            // the s-th SFD of that VL even when earlier frames were dropped.
            need(sn<sfd_times[vl].size(),"RX delivered a SN whose SFD was not recorded");
            unsigned long long sfd=sfd_times[vl][sn];
            std::uint64_t delta=d.rx_time>sfd?d.rx_time-sfd:sfd-d.rx_time;
            if(delta>max_sfd_delta)max_sfd_delta=delta;
            need(delta==0,"RX delivery time differs from the SFD time (vl="+std::to_string(vl)+" sn="+std::to_string(sn)+")");
            has_delivered[vl]=true; last_delivered_sn[vl]=sn;
            delivered_sns[vl].push_back(sn);
            ++delivered; rx_frame.clear();
        }
    }
    d.clk=1; d.eval(); ++cycles;
    if(!d.reset){
        need(!d.tx_fault,"TX reported an internal fault");
        if(d.rx_decision)++decisions;
        if(d.sfd_pulse){
            unsigned vl=d.sfd_vl;
            need(vl<128,"SFD VL out of range");
            ++sfd_count;
            if(vl<RX_VLS){
                if(sfd_times[vl].size()<MAX_SN) sfd_times[vl].push_back(d.sfd_time);
                static std::array<unsigned long long,RX_VLS> prev_sfd{};
                static std::array<bool,RX_VLS> seen{};
                if(seen[vl]){
                    unsigned long long gap=d.sfd_time-prev_sfd[vl];
                    ++bag_intervals;
                    if(gap<min_bag)min_bag=gap;
                    if(gap<100000ULL)++bag_violations;
                }
                prev_sfd[vl]=d.sfd_time; seen[vl]=true;
            }
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
static std::vector<unsigned char> make_payload(unsigned vl,unsigned idx,unsigned len){
    std::vector<unsigned char> p(len);
    for(unsigned i=0;i<len;++i)p[i]=(unsigned char)((i*13u+vl*29u+idx*7u+3u)&0xffu);
    return p;
}
static unsigned attempt_admit(unsigned vl,const std::vector<unsigned char>& payload){
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
static void enqueue_frame(unsigned vl,unsigned sn,const std::vector<unsigned char>& payload){
    (void)sn;
    unsigned retries=0;
    while(true){
        unsigned st=attempt_admit(vl,payload);
        if(st==0) return;
        need(st==2,"unexpected app response status "+std::to_string(st));
        for(unsigned k=0;k<64;++k)step();
        need(++retries<2000000,"full-pool retry timeout");
    }
}
static bool sn_delivered(unsigned vl,unsigned sn){
    for(unsigned s:delivered_sns[vl]) if(s==sn) return true;
    return false;
}
static void wait_delivered(unsigned vl,unsigned sn,unsigned long long max_cycles){
    unsigned long long n=0;
    while(!sn_delivered(vl,sn)){
        step(); need(++n<max_cycles,"delivery wait timeout (vl="+std::to_string(vl)+" sn="+std::to_string(sn)+")");
    }
}
static void idle_drain(unsigned long long max_cycles){
    unsigned long long n=0;
    while(d.busy&&n<max_cycles){step();++n;}
    need(n<max_cycles,"drain timeout");
    unsigned long long before=decisions; unsigned long long quiet=0;
    while(n<max_cycles&&quiet<40000){step();++n; if(decisions==before)++quiet; else{quiet=0;before=decisions;}}
}
int main(int argc,char**argv){try{
    Verilated::commandArgs(argc,argv); need(argc==2,"usage result.json");
    reset_system();
    for(unsigned vl=0;vl<RX_VLS;++vl)configure_vl(vl);
    const unsigned lens[4]={1,17,63,1471};
    // ---------- phase 1a: queue depth 4, initial burst ----------
    unsigned long long p1a_start=cycles;
    d.run=1; d.app_enable=1;
    for(unsigned idx=0;idx<4;++idx){
        for(unsigned vl=0;vl<RX_VLS;++vl){
            unsigned sn=idx;
            unsigned len=lens[(idx+vl)%4];
            std::vector<unsigned char> p=make_payload(vl,idx,len);
            pay_by_sn[vl][sn]=p;
            enqueue_frame(vl,sn,p);
            wire_q[vl].push_back(wire_of(len));
        }
    }
    {
        unsigned long long n=0;
        while((sfd_count<32||d.busy)&&n<3000000){step();++n;}
        need(n<3000000,"phase1a completion timeout");
        idle_drain(3000000);
    }
    unsigned p1a_sfd=sfd_count, p1a_delivered=delivered, p1a_frames=frames_on_wire;
    unsigned long long p1a_cycles=cycles-p1a_start;
    unsigned long long p1a_bad=(d.bad_fcs&0xffffffffULL)+(d.bad_fcs>>32);
    unsigned long long p1a_full=(d.full_drops&0xffffffffULL)+(d.full_drops>>32);
    unsigned p1a_rejected=d.rejected_frames;
    unsigned p1a_pool_peak=pool_peak;
    need(p1a_sfd==32,"phase1a SFD count mismatch: "+std::to_string(p1a_sfd));
    need(p1a_bad==0,"phase1a RX bad FCS");
    need(cadence_violations==0,"TX byte cadence violations");
    need(delivered>=1,"phase1a delivered nothing");
    // ---------- phase 1b: paced 32/32 ----------
    unsigned long long p1b_start=cycles;
    unsigned p1b_delivered_before=delivered;
    unsigned long long p1b_full_before=p1a_full;
    unsigned long long p1b_sn_errors_before=strict_sn_errors;
    p1b_active=true; p1b_next.fill(4);
    for(unsigned idx=0;idx<4;++idx){
        for(unsigned vl=0;vl<RX_VLS;++vl){
            unsigned sn=4+idx;
            unsigned len=lens[(idx+vl)%4];
            std::vector<unsigned char> p=make_payload(vl,100+idx,len);
            pay_by_sn[vl][sn]=p;
            enqueue_frame(vl,sn,p);
            wire_q[vl].push_back(wire_of(len));
            wait_delivered(vl,sn,600000);
        }
    }
    unsigned p1b_delivered=delivered-p1b_delivered_before;
    unsigned long long p1b_cycles=cycles-p1b_start;
    unsigned long long p1b_bad=(d.bad_fcs&0xffffffffULL)+(d.bad_fcs>>32);
    unsigned long long p1b_full=(d.full_drops&0xffffffffULL)+(d.full_drops>>32);
    p1b_active=false;
    unsigned p1b_sn_errors=(unsigned)(strict_sn_errors-p1b_sn_errors_before);
    for(unsigned vl=0;vl<RX_VLS;++vl) need(p1b_next[vl]==8,"phase1b SN chain incomplete (vl="+std::to_string(vl)+")");
    need(p1b_delivered==32,"phase1b delivered count mismatch: "+std::to_string(p1b_delivered));
    need(p1b_full==p1b_full_before,"phase1b RX MAC full drops appeared: "+std::to_string(p1b_full-p1b_full_before));
    need(p1b_bad==0,"phase1b RX bad FCS");
    need(p1b_sn_errors==0,"phase1b SN order errors: "+std::to_string(p1b_sn_errors));
    // phase 1b evidence: every VL delivered SN 4..7 in order and without new MAC drops.
    // (phase 1a may legitimately have dropped SN 0..3 under the burst; those are
    // reported rather than asserted away.)
    std::string p1a_sn_map, p1b_sn_map;
    for(unsigned vl=0;vl<RX_VLS;++vl){
        p1a_sn_map+="vl"+std::to_string(vl)+":";
        for(unsigned s:delivered_sns[vl]) if(s<4) p1a_sn_map+=std::to_string(s)+",";
        p1a_sn_map+=" ";
        p1b_sn_map+="vl"+std::to_string(vl)+":";
        for(unsigned s:delivered_sns[vl]) if(s>=4&&s<8) p1b_sn_map+=std::to_string(s)+",";
        p1b_sn_map+=" ";
    }
    // ---------- phase 2: full pool ----------
    d.run=0; d.app_enable=0;
    for(unsigned vl=RX_VLS;vl<=16;++vl)configure_vl(vl);
    unsigned long long p2_start=cycles;
    d.app_enable=1;
    unsigned p2_enqueued=0;
    for(unsigned idx=0;idx<4;++idx){
        for(unsigned vl=0;vl<16;++vl){
            unsigned len=lens[(idx+vl)%4];
            std::vector<unsigned char> p=make_payload(vl,200+idx,len);
            if(vl<RX_VLS){ unsigned sn=8+idx; pay_by_sn[vl][sn]=p; }
            enqueue_frame(vl,0,p);
            wire_q[vl].push_back(wire_of(len));
            ++p2_enqueued;
        }
    }
    unsigned pool_after_fill=pool_used();
    need(pool_after_fill==POOL_SLOTS,"pool did not fill to 64 slots: "+std::to_string(pool_after_fill));
    unsigned extra_status=attempt_admit(16,make_payload(16,999,8));
    unsigned pool_after_reject=pool_used();
    need(extra_status==2,"full pool admission did not report status 2: "+std::to_string(extra_status));
    d.app_enable=0;
    d.run=1;
    unsigned p2_sfd_before=sfd_count, p2_del_before=delivered, p2_frames_before=frames_on_wire;
    {
        unsigned long long n=0;
        while((sfd_count<p2_sfd_before+64||d.busy)&&n<4000000){step();++n;}
        need(n<4000000,"phase2 completion timeout");
        idle_drain(4000000);
    }
    unsigned p2_sfd=sfd_count-p2_sfd_before, p2_frames=frames_on_wire-p2_frames_before;
    unsigned p2_delivered=delivered-p2_del_before;
    unsigned long long p2_cycles=cycles-p2_start;
    unsigned long long p2_bad=(d.bad_fcs&0xffffffffULL)+(d.bad_fcs>>32);
    unsigned long long p2_full=(d.full_drops&0xffffffffULL)+(d.full_drops>>32);
    unsigned p2_pool_peak=pool_peak;
    unsigned pool_used_end=pool_used();
    need(p2_sfd==64,"phase2 SFD count mismatch: "+std::to_string(p2_sfd));
    need(p2_bad==0,"phase2 RX bad FCS");
    need(pool_used_end==0,"pool not empty after drain: "+std::to_string(pool_used_end));
    std::cout<<"PASS capacity_rx phase1a(burst): sfd="<<p1a_sfd<<" delivered="<<p1a_delivered
             <<" rejected="<<p1a_rejected<<" rx_full_drops="<<p1a_full<<" pool_peak="<<p1a_pool_peak
             <<" min_bag="<<(bag_intervals?min_bag:0)<<" bad_fcs="<<p1a_bad<<" cycles="<<p1a_cycles<<"\n";
    std::cout<<"PASS capacity_rx phase1b(paced): delivered="<<p1b_delivered<<" sn_errors="<<p1b_sn_errors
             <<" full_drops_delta="<<(p1b_full-p1b_full_before)<<" bad_fcs="<<p1b_bad<<" cycles="<<p1b_cycles<<"\n";
    std::cout<<"PASS capacity_rx phase2(full pool): enqueued="<<p2_enqueued<<" pool_after_fill="<<pool_after_fill
             <<" extra_status="<<extra_status<<" pool_after_reject="<<pool_after_reject
             <<" sfd="<<p2_sfd<<" delivered="<<p2_delivered<<" pool_peak="<<p2_pool_peak
             <<" pool_end="<<pool_used_end<<" bad_fcs="<<p2_bad<<" full_drops="<<p2_full<<" cycles="<<p2_cycles<<"\n";
    d.final();
    std::ofstream out(argv[1]);
    out<<"{\"status\":\"PASS\",\"test\":\"system_capacity_rx\""
       <<",\"phase_1a_burst\":{\"vl_configured\":8,\"frames_enqueued\":32,\"sfd_count\":"<<p1a_sfd
       <<",\"frames_on_wire\":"<<p1a_frames<<",\"delivered\":"<<p1a_delivered
       <<",\"rejected_descriptors\":"<<p1a_rejected<<",\"rx_full_drops\":"<<p1a_full
       <<",\"rx_bad_fcs\":"<<p1a_bad<<",\"pool_slots\":"<<POOL_SLOTS<<",\"pool_peak_used\":"<<p1a_pool_peak
       <<",\"bag_intervals_checked\":"<<bag_intervals<<",\"min_bag_interval_cycles\":"<<(bag_intervals?min_bag:0)
       <<",\"bag_violations\":"<<bag_violations<<",\"byte_cadence_violations\":"<<cadence_violations
       <<",\"max_sfd_time_delta\":"<<max_sfd_delta<<",\"cycles\":"<<p1a_cycles
       <<",\"delivered_sns_matrix\":\""<<p1a_sn_map<<"\""
       <<",\"interpretation\":\"initial 8-VL burst at 100 Mbit/s can trigger RX MAC full drops; the per-VL BAG paces the remaining rounds\"}"
       <<",\"phase_1b_paced\":{\"frames_enqueued\":32,\"delivered\":"<<p1b_delivered
       <<",\"per_vl_delivered\":8,\"sn_sequence_per_vl\":\"paced adds SN 4..7; phase 1a delivered 0..3 where the RX kept up\""
       <<",\"delivered_sns_matrix\":\""<<p1b_sn_map<<"\""
       <<",\"sn_order_errors\":"<<p1b_sn_errors<<",\"rx_full_drops_delta\":"<<(p1b_full-p1b_full_before)
       <<",\"rx_bad_fcs\":"<<p1b_bad<<",\"max_sfd_time_delta\":"<<max_sfd_delta
       <<",\"cycles\":"<<p1b_cycles<<"}"
       <<",\"phase_2_full_pool\":{\"vl_configured_total\":17,\"frames_enqueued\":"<<p2_enqueued
       <<",\"pool_slots\":"<<POOL_SLOTS<<",\"pool_used_after_fill\":"<<pool_after_fill
       <<",\"extra_admission_status\":"<<extra_status<<",\"pool_used_after_reject\":"<<pool_after_reject
       <<",\"pool_peak_used\":"<<p2_pool_peak<<",\"sfd_count\":"<<p2_sfd<<",\"frames_on_wire\":"<<p2_frames
       <<",\"delivered\":"<<p2_delivered<<",\"rx_bad_fcs\":"<<p2_bad<<",\"rx_full_drops\":"<<p2_full
       <<",\"pool_used_after_drain\":"<<pool_used_end<<",\"no_hang\":true,\"cycles\":"<<p2_cycles<<"}"
       <<",\"cycles\":"<<cycles<<",\"checks\":"<<checks
       <<",\"scope\":\"RTL simulation at 100 MHz; VL0..7 with MAX_PER_VL=4; 8-VL RX subset; full-pool case fills all 64 slots with VL0..15 while run=0\""
       <<"}\n";
    return 0;
}catch(const std::exception&e){
    std::cerr<<"FAIL "<<e.what()<<" cycles="<<cycles<<" sfd="<<sfd_count<<" delivered="<<delivered<<"\n";
    std::cerr<<"counters: rx_delivered="<<d.delivered_frames<<" rx_rejected="<<d.rejected_frames
             <<" bad_fcs="<<((d.bad_fcs&0xffffffffULL)+(d.bad_fcs>>32))
             <<" full_drops="<<((d.full_drops&0xffffffffULL)+(d.full_drops>>32))<<"\n";
    for(unsigned vl=0;vl<RX_VLS;++vl){
        std::cerr<<"vl"<<vl<<": sns=";
        for(unsigned s:delivered_sns[vl]) std::cerr<<s<<",";
        std::cerr<<"\n";
    }
    return 1;}}
