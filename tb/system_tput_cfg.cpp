#include "Vafdx_system_top.h"
#include "Vafdx_system_top___024root.h"
#include "verilated.h"
#include "run_config.h"
#include <array>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <deque>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>
// P2: config-driven sustained TX throughput / BAG feasibility test for the T11
// 50 MHz (4 core cycles per byte) candidate and the 100 MHz/8 compatibility config.
// All time bases come from the generated run_config.h (single run config).
//
// Per phase (long / short / mixed payload lengths):
//   - 128 VLs configured with BAG exponent 0 (1 ms), run=1
//   - the application keeps enqueueing round-robin (blocking, retry on pool full)
//     so the TX is never starved by the testbench itself
//   - a steady-state window is measured after a 64-frame warm-up and must cover at
//     least 100 ms AND at least 1000 frames (whichever is longer)
//   - recorded: line bytes / wire-active cycles / MAC idle+GAP cycles / inter-frame
//     gaps / per-VL SFD intervals (BAG) / pool peak / queue depth / enqueue cost /
//     tx_fault; both line-byte and UDP-payload throughput.
static Vafdx_system_top d;
static unsigned long long checks=0, cycles=0;
static const unsigned NVL=128;
static const unsigned POOL_SLOTS=64;
static const unsigned BYTE_CYCLES=RCFG_BYTE_CYCLES;
static const unsigned long long CPM=RCFG_CYCLES_PER_MS;
static const double CORE_HZ=(double)RCFG_CORE_CLK_HZ;
static const unsigned long long IFG_CYCLES=12ull*BYTE_CYCLES;
static const unsigned WIN_MS=100;
static const unsigned long long WIN_FRAMES=1000;
static const unsigned WARM_FRAMES=64;
static void need(bool ok,const std::string&s){++checks;if(!ok)throw std::runtime_error(s);}
struct Phase {
    std::string name;
    unsigned mode=0;
    unsigned long long phase_cycles=0, line_bytes=0, frames=0, payload_bytes=0;
    unsigned long long win_cycles=0, win_line_bytes=0, win_frames=0, win_payload_bytes=0;
    unsigned long long mac_idle=0, mac_gap=0, mac_active=0, starve=0;
    unsigned long long gap_min=~0ULL, gap_max=0, gap_sum=0, gap_count=0, gap_gt=0;
    unsigned long long bag_intervals=0, bag_violations=0, bag_min=~0ULL;
    unsigned long long enq=0, full_retries=0, enq_sum=0, enq_max=0;
    unsigned min_pending=~0u, max_pending=0, pool_peak=0;
    unsigned long long sfd_total=0;
    double mbps_line=0, mbps_payload=0, payload_share=0;
};
static Phase* pr=nullptr;
static std::array<unsigned long long,NVL> last_sfd{};
static std::array<bool,NVL> has_sfd{};
static std::array<std::deque<unsigned>,NVL> vlen;      // payload length FIFO per VL
static std::array<std::deque<unsigned>,NVL> vwire;     // expected wire length FIFO per VL
static unsigned long long last_byte_time=0, prev_byte_time=0;
static bool last_line_valid=false, collecting=false, warm_done=false;
static unsigned long long win_start_cycle=0, win_end_cycle=0, win_start_sfd=0, win_start_bytes=0, win_start_payload=0;
static unsigned frame_byte_idx=0, sfd_byte_time=0;
static bool sfd_byte_seen=false;
static unsigned bag_exp=0;
static unsigned pending_vls(){
    auto* r=d.rootp; unsigned n=0;
    for(unsigned w=0;w<4;++w){ std::uint32_t v=r->afdx_system_top__DOT__tx__DOT__pending[w]; while(v){n+=v&1u;v>>=1u;}}
    return n;
}
static unsigned pool_used(){
    unsigned n=0; auto* r=d.rootp;
    for(unsigned i=0;i<POOL_SLOTS;++i) if(r->afdx_system_top__DOT__tx__DOT__buffers__DOT__pool__DOT__references[i]!=0) ++n;
    return n;
}
static void byte_account(){
    if(!d.tx_line_valid)return;
    unsigned long long now=d.monotonic_time;
    if(!last_line_valid){
        frame_byte_idx=1;
        if(prev_byte_time){
            unsigned long long gap=now-prev_byte_time;
            need(gap>=IFG_CYCLES,"inter-frame gap shorter than the 12-byte IFG");
            if(collecting){
                if(gap<pr->gap_min)pr->gap_min=gap;
                if(gap>pr->gap_max)pr->gap_max=gap;
                pr->gap_sum+=gap; ++pr->gap_count;
                if(gap>IFG_CYCLES+4ull*BYTE_CYCLES)++pr->gap_gt;
            }
        }
    }else{
        need(now-prev_byte_time==BYTE_CYCLES,"intra-frame byte cadence != BYTE_CYCLES");
        ++frame_byte_idx;
    }
    if(frame_byte_idx==8){
        need(d.tx_line_data==0xd5u,"frame byte 8 is not the SFD (0xD5)");
        sfd_byte_time=(unsigned)now; sfd_byte_seen=true;
    }
    ++pr->line_bytes;
    if(collecting)++pr->win_line_bytes;
    prev_byte_time=now; last_line_valid=true;
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
    if(!d.reset&&!d.tx_line_valid) last_line_valid=false;
    d.clk=1; d.eval(); ++cycles;
    if(d.reset)return;
    need(!d.tx_fault,"TX fault");
    unsigned ms=d.rootp->afdx_system_top__DOT__tx__DOT__mac__DOT__state;
    if(collecting){
        if(ms==0)++pr->mac_idle; else if(ms==4)++pr->mac_gap; else ++pr->mac_active;
        unsigned pv=pending_vls();
        if(pv<pr->min_pending)pr->min_pending=pv;
        if(pv>pr->max_pending)pr->max_pending=pv;
        if(pv==0&&ms==0)++pr->starve;
        unsigned used=pool_used(); if(used>pr->pool_peak)pr->pool_peak=used;
    }
    if(d.sfd_pulse){
        need(sfd_byte_seen&&(unsigned long long)sfd_byte_time==d.sfd_time,"SFD timestamp != physical SFD byte time");
        sfd_byte_seen=false;
        unsigned vl=d.sfd_vl;
        ++pr->sfd_total;
        need(!vlen[vl].empty(),"SFD without a queued frame model");
        unsigned plen=vlen[vl].front(); vlen[vl].pop_front();
        unsigned wlen=vwire[vl].front(); vwire[vl].pop_front();
        pr->frames++;
        pr->payload_bytes+=plen;
        if(collecting){
            ++pr->win_frames;
            pr->win_payload_bytes+=plen;
        }
        if(has_sfd[vl]){
            unsigned long long expected=(CPM<<bag_exp);
            unsigned long long gap=d.sfd_time-last_sfd[vl];
            ++pr->bag_intervals;
            if(gap<pr->bag_min)pr->bag_min=gap;
            if(gap<expected)++pr->bag_violations;
        }
        last_sfd[vl]=d.sfd_time; has_sfd[vl]=true;
        if(!warm_done&&pr->sfd_total>=WARM_FRAMES){
            warm_done=true; collecting=true;
            win_start_cycle=cycles; win_start_sfd=pr->sfd_total;
            win_start_bytes=pr->line_bytes; win_start_payload=pr->payload_bytes;
            pr->win_line_bytes=0; pr->win_frames=0; pr->win_payload_bytes=0;
        } else if(collecting){
            if(pr->win_frames>=WIN_FRAMES&&(cycles-win_start_cycle)>=(unsigned long long)WIN_MS*CPM){
                collecting=false; win_end_cycle=cycles;
            }
        }
    }
}
static void reset_system(){
    d.reset=1; d.run=0; d.app_enable=0; d.cfg_valid=0; d.app_msg_valid=0; d.app_valid=0;
    d.app_rsp_ready=0; d.drop_valid=0; d.drop_rsp_ready=0; d.rx_ready=1;
    step(); step(); d.reset=0;
    unsigned n=0; while(!d.initialized){step();need(++n<8000,"initialization timeout");}
}
static void configure_vl(unsigned vl,unsigned exp){
    d.cfg_valid=1; d.cfg_vl=vl; d.cfg_bag_exp=exp;
    unsigned cw=0;
    while(true){ d.clk=0; d.eval(); if(d.cfg_ready)break; step(); need(++cw<64,"configuration not ready"); }
    step();
}
static std::vector<unsigned char> make_payload(unsigned vl,unsigned idx,unsigned len){
    std::vector<unsigned char> p(len);
    for(unsigned i=0;i<len;++i)p[i]=(unsigned char)((i*13u+vl*29u+idx*7u+3u)&0xffu);
    return p;
}
static unsigned phase_len(unsigned mode,unsigned vl,unsigned idx){
    static const unsigned shorts[5]={1,16,17,18,63};
    static const unsigned mixed[6]={1,16,17,18,63,1471};
    if(mode==0)return 1471;
    if(mode==1)return shorts[(vl+idx)%5];
    return mixed[(vl+idx)%6];
}
static unsigned wire_of(unsigned plen){ unsigned b=plen+43u; if(b<60u)b=60u; return 8u+b+4u; }
static unsigned attempt_admit(unsigned vl,const std::vector<unsigned char>& payload,unsigned long long& spent){
    d.app_msg_valid=1; d.app_vl=vl; d.app_length=payload.size();
    unsigned n=0;
    while(true){ d.clk=0; d.eval(); bool accept=d.app_msg_ready; step(); ++spent; if(accept)break; need(++n<8000000,"app admission timeout"); }
    d.app_msg_valid=0; d.app_vl=0; d.app_length=0; d.eval();
    unsigned pos=0; n=0;
    while(!d.app_rsp_valid){
        d.app_valid=(pos<payload.size());
        d.app_data=pos<payload.size()?payload[pos]:0;
        d.app_last=(pos+1==payload.size());
        d.clk=0; d.eval();
        bool byte=d.app_valid&&d.app_ready;
        step(); ++spent;
        if(byte)++pos;
        need(++n<payload.size()*12+4000000,"app payload timeout");
    }
    d.app_valid=0; d.app_data=0; d.app_last=0;
    unsigned status=d.app_rsp_status;
    d.app_rsp_ready=1; step(); d.app_rsp_ready=0; ++spent;
    if(status==0) need(pos==payload.size(),"successful enqueue consumed wrong byte count");
    else need(pos==0,"rejected enqueue consumed payload");
    return status;
}
static void enqueue_frame(unsigned vl,const std::vector<unsigned char>& payload){
    unsigned retries=0;
    while(true){
        unsigned long long spent=0;
        unsigned st=attempt_admit(vl,payload,spent);
        if(st==0){
            ++pr->enq;
            pr->enq_sum+=spent;
            if(spent>pr->enq_max)pr->enq_max=spent;
            vlen[vl].push_back(payload.size());
            vwire[vl].push_back(wire_of(payload.size()));
            return;
        }
        need(st==2,"unexpected app response status "+std::to_string(st));
        ++pr->full_retries;
        for(unsigned k=0;k<64;++k)step();
        need(++retries<4000000,"full-pool retry timeout");
    }
}
static void drain(unsigned long long max_cycles){
    unsigned long long n=0;
    while(d.busy&&n<max_cycles){step();++n;}
    need(n<max_cycles,"drain timeout");
    unsigned long long before=(unsigned long long)d.delivered_frames+(unsigned long long)d.rejected_frames;
    unsigned long long quiet=0;
    while(n<max_cycles&&quiet<20000){step();++n;
        unsigned long long now=(unsigned long long)d.delivered_frames+(unsigned long long)d.rejected_frames;
        if(now==before)++quiet; else{quiet=0;before=now;}}
}
static void run_phase(unsigned mode,const char* name,unsigned exp,std::ofstream& out){
    Phase r; r.name=name; r.mode=mode; pr=&r;
    last_sfd.fill(0); has_sfd.fill(false);
    for(auto&q:vlen)q.clear(); for(auto&q:vwire)q.clear();
    last_byte_time=0; prev_byte_time=0; last_line_valid=false;
    collecting=false; warm_done=false; bag_exp=exp;
    reset_system();
    for(unsigned vl=0;vl<NVL;++vl)configure_vl(vl,exp);
    d.run=1; d.app_enable=1;
    unsigned long long t0=cycles;
    unsigned pass=0; bool closed=false;
    while(!closed&&pass<40000){
        for(unsigned vl=0;vl<NVL;++vl){
            unsigned len=phase_len(mode,vl,pass);
            std::vector<unsigned char> p=make_payload(vl,pass,len);
            enqueue_frame(vl,p);
            if(warm_done&&!collecting){closed=true;break;}
        }
        ++pass;
    }
    need(closed,"steady-state window did not close");
    d.app_enable=0;
    drain(400000000ULL);
    r.phase_cycles=cycles-t0;
    // window timing must exclude the post-window drain
    need(win_end_cycle>win_start_cycle,"window end not recorded");
    r.win_cycles=win_end_cycle-win_start_cycle;
    r.win_payload_bytes=r.win_payload_bytes;
    r.win_frames=r.win_frames;
    need(r.win_frames>=WIN_FRAMES,"window frames below 1000");
    need(r.win_cycles>=(unsigned long long)WIN_MS*CPM,"window shorter than 100 ms");
    r.mbps_line=r.win_line_bytes*8.0*CORE_HZ/(double)r.win_cycles/1e6;
    r.mbps_payload=r.win_payload_bytes*8.0*CORE_HZ/(double)r.win_cycles/1e6;
    r.payload_share=r.win_line_bytes? (double)r.win_payload_bytes/(double)r.win_line_bytes : 0.0;
    double win_ms=1000.0*(double)r.win_cycles/CORE_HZ;
    std::cout<<"PHASE "<<r.name<<" win_ms="<<win_ms<<" frames="<<r.win_frames
             <<" line_bytes="<<r.win_line_bytes<<" line_mbps="<<r.mbps_line
             <<" payload_mbps="<<r.mbps_payload<<" mac_idle="<<r.mac_idle
             <<" mac_gap="<<r.mac_gap<<" bag_iv="<<r.bag_intervals<<" bag_viol="<<r.bag_violations
             <<" pool_peak="<<r.pool_peak<<"\n";
    out<<"{\"phase\":\""<<r.name<<"\",\"mode\":"<<r.mode<<",\"window_ms\":"<<win_ms
       <<",\"window_cycles\":"<<r.win_cycles<<",\"frames\":"<<r.win_frames
       <<",\"line_bytes\":"<<r.win_line_bytes<<",\"payload_bytes\":"<<r.win_payload_bytes
       <<",\"payload_share\":"<<r.payload_share
       <<",\"line_mbps\":"<<r.mbps_line<<",\"payload_mbps\":"<<r.mbps_payload
       <<",\"wire_active_cycles\":"<<(r.win_line_bytes*BYTE_CYCLES)
       <<",\"line_idle_fraction\":"<<(r.win_cycles? 1.0-(double)(r.win_line_bytes*BYTE_CYCLES)/(double)r.win_cycles : 0.0)
       <<",\"mac_idle\":"<<r.mac_idle<<",\"mac_gap\":"<<r.mac_gap<<",\"mac_active\":"<<r.mac_active
       <<",\"starve_cycles\":"<<r.starve
       <<",\"gap_count\":"<<r.gap_count<<",\"gap_min\":"<<(r.gap_count?r.gap_min:0)
       <<",\"gap_max\":"<<r.gap_max<<",\"gap_mean\":"<<(r.gap_count? (double)r.gap_sum/(double)r.gap_count : 0.0)
       <<",\"gap_over_ifg\":"<<r.gap_gt
       <<",\"bag_intervals\":"<<r.bag_intervals<<",\"bag_violations\":"<<r.bag_violations
       <<",\"bag_min_cycles\":"<<(r.bag_intervals?r.bag_min:0)
       <<",\"bag_expected_cycles\":"<<(CPM<<bag_exp)
       <<",\"enqueued\":"<<r.enq<<",\"full_retries\":"<<r.full_retries
       <<",\"enq_cycles_mean\":"<<(r.enq? (double)r.enq_sum/(double)r.enq : 0.0)
       <<",\"enq_cycles_max\":"<<r.enq_max
       <<",\"pending_vls_min\":"<<(r.min_pending==~0u?0:r.min_pending)<<",\"pending_vls_max\":"<<r.max_pending
       <<",\"pool_peak\":"<<r.pool_peak<<",\"sfd_total\":"<<r.sfd_total
       <<",\"phase_cycles\":"<<r.phase_cycles<<"}";
}
int main(int argc,char**argv){
    try{
        Verilated::commandArgs(argc,argv);
        need(argc>=2,"usage: tput result.json [bag_exp]");
        unsigned exp=0;
        if(argc>=3)exp=(unsigned)std::stoul(argv[2]);
        std::ofstream out(argv[1]);
        out<<"{\"status\":\"RUNNING\",\"config\":\""<<RCFG_NAME<<"\""
           <<",\"core_clk_hz\":"<<RCFG_CORE_CLK_HZ<<",\"byte_cycles\":"<<RCFG_BYTE_CYCLES
           <<",\"cycles_per_ms\":"<<RCFG_CYCLES_PER_MS
           <<",\"nominal_bit_rate\":"<<RCFG_NOMINAL_BIT_RATE
           <<",\"ifg_cycles\":"<<RCFG_IFG_CYCLES<<",\"bag_exp\":"<<exp<<",\"phases\":[";
        run_phase(0,"long_1471B",exp,out); out<<",";
        run_phase(1,"short_1_16_17_18_63B",exp,out); out<<",";
        run_phase(2,"mixed_1_16_17_18_63_1471B",exp,out);
        out<<"]}\n";
        out.close();
        std::cout<<"PASS tput config="<<RCFG_NAME<<" checks="<<checks<<" cycles="<<cycles<<"\n";
        return 0;
    }catch(const std::exception&e){
        std::cerr<<"FAIL "<<e.what()<<" cycles="<<cycles<<"\n";
        return 1;
    }
}
