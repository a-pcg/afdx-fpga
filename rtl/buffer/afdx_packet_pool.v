`timescale 1ns/1ps
// Shared 2 KiB packet slots, tagged ownership and publish/reference lifecycle.
// ALLOC -> writable owner -> PUBLISH with 1..3 consumers -> RELEASE per consumer.
// T7: read and write control are split into 8 groups of 8 banks. The global
// request is registered once at the group boundary and each group decodes only
// its own 8 banks, so the 64-way control fanout becomes 8 + 8. The payload RAM
// geometry, single write block and same-cycle dual-port behaviour are unchanged;
// write commits and read data are each one clock later than the pre-T7 version.
// One payload write and one registered read per cycle. No memory-array reset.
// Tags change at reuse. All callers must flush stale tags on a system reset;
// descriptor lifetime must also be bounded below generation-counter wrap time.
// T8-C: the allocation scan's generation read-modify-write is split into three
// stages inside the same 8-group hierarchy, so no single net has to drive the
// width-26 64:1 generation read mux, the +1 carry chain and the 64 way
// generation write enables at once:
//   stage 1 (SCAN):     every group captures its own 8:1 generation read mux
//                       selected by the low scan bits into a group read flop
//                       (source -> 8 group registers -> 8 local banks).
//   stage 2 (SCAN_GEN): the registered scan group selects gen_mux from those 8
//                       group flops, gen_inc=gen_mux+1 is registered into one
//                       per-group write-data flop and the local slot index is
//                       registered per group.
//   stage 3 (WRITE):    only the committed group and local slot decode write
//                       data back into the generation flop array.
// The allocation response is raised after that write-back, so rsp_tag never
// exposes an uncommitted generation and cmd/wr/rd ownership compares always
// read the same generation flop array. Allocation latency grows by two clocks
// (SCAN -> SCAN_GEN -> WRITE -> RESPOND); cmd_ready/rsp_valid/wr_ready/rd_ready
// handshakes, round-robin order, full-pool detection and tag generation
// semantics are unchanged. Payload banks keep exactly one write statement and
// one registered read statement per bank; no T8 register or mux is placed in a
// bank always block.
module afdx_packet_pool #(
    parameter SLOT_WIDTH=6,
    parameter OFFSET_WIDTH=11,
    parameter TAG_WIDTH=32
)(
    input wire clk,reset,
    input wire cmd_valid,
    output wire cmd_ready,
    input wire [1:0] cmd_op,
    input wire [TAG_WIDTH-1:0] cmd_tag,
    input wire [1:0] cmd_consumers,
    output wire rsp_valid,
    input wire rsp_ready,
    output reg [TAG_WIDTH-1:0] rsp_tag,
    output reg [2:0] rsp_status,
    output reg [1:0] rsp_refs,
    input wire wr_valid,
    output wire wr_ready,
    input wire [TAG_WIDTH-1:0] wr_tag,
    input wire [OFFSET_WIDTH-1:0] wr_offset,
    input wire [7:0] wr_data,
    output wire wr_error,
    input wire rd_valid,
    output wire rd_ready,
    input wire [TAG_WIDTH-1:0] rd_tag,
    input wire [OFFSET_WIDTH-1:0] rd_offset,
    output reg data_valid,
    input wire data_ready,
    output wire [7:0] data_byte,
    output reg data_error,
    output reg [TAG_WIDTH-1:0] data_tag,
    output reg [OFFSET_WIDTH-1:0] data_offset,
    output reg [SLOT_WIDTH:0] free_count,
    output wire initialized
);
    localparam SLOTS=(1 << SLOT_WIDTH);
    localparam GEN_WIDTH=TAG_WIDTH-SLOT_WIDTH;
    // T8-C adds the SCAN_GEN and WRITE commit states between scan and response.
    localparam INIT=3'd0,IDLE=3'd1,SCAN=3'd2,SCAN_GEN=3'd3,WRITE_ST=3'd4,RESPOND=3'd5;
    // T7 hierarchy: groups of up to 8 banks.
    localparam GW=(SLOT_WIDTH>3) ? 3 : SLOT_WIDTH;
    localparam GROUPS=(1 << GW);
    localparam LB=SLOT_WIDTH-GW;
    localparam LW=(LB>0) ? LB : 1;
    localparam GSZ=(1 << LB);
    reg [2:0] state;
    reg [GEN_WIDTH-1:0] generations [0:SLOTS-1];
    reg [1:0] references [0:SLOTS-1];
    reg sealed [0:SLOTS-1];
    wire [7:0] bank_byte [0:SLOTS-1];
    reg [SLOT_WIDTH-1:0] init_index,scan_index,round_robin;
    // T8-C generation read-modify-write pipeline (plain flops, never inside a
    // payload RAM always block).
    reg [GEN_WIDTH-1:0] grd [0:GROUPS-1];
    reg [GEN_WIDTH-1:0] gwr [0:GROUPS-1];
    reg [LW-1:0] glocal [0:GROUPS-1];
    reg [GROUPS-1:0] gcommit;
    reg [SLOT_WIDTH-1:0] scan_slot_q;
    reg [GW-1:0] scan_group_q;
    reg [LW-1:0] scan_local_q;
    wire [SLOT_WIDTH-1:0] cmd_slot=cmd_tag[SLOT_WIDTH-1:0];
    wire [SLOT_WIDTH-1:0] wr_slot=wr_tag[SLOT_WIDTH-1:0];
    wire [SLOT_WIDTH-1:0] rd_slot=rd_tag[SLOT_WIDTH-1:0];
    wire cmd_owned=references[cmd_slot]!=0 && generations[cmd_slot]==cmd_tag[TAG_WIDTH-1:SLOT_WIDTH];
    wire wr_owned=references[wr_slot]==1 && !sealed[wr_slot] && generations[wr_slot]==wr_tag[TAG_WIDTH-1:SLOT_WIDTH];
    wire rd_owned=references[rd_slot]!=0 && sealed[rd_slot] && generations[rd_slot]==rd_tag[TAG_WIDTH-1:SLOT_WIDTH];
    assign initialized=state!=INIT && !reset;
    assign cmd_ready=state==IDLE && !reset;
    assign rsp_valid=state==RESPOND && !reset;
    assign wr_ready=initialized;
    assign wr_error=wr_valid && wr_ready && !wr_owned;
    assign data_byte=data_error ? 8'd0 : bank_byte[data_tag[SLOT_WIDTH-1:0]];
    // T8-C group select for the scan and its registered pipeline copies.
    wire [GW-1:0] scan_group=scan_index[SLOT_WIDTH-1:LB];
    wire [LW-1:0] scan_local;
    generate
        if (LB>0) begin: scan_local_slice
            assign scan_local=scan_index[LB-1:0];
        end else begin: no_scan_local_slice
            assign scan_local={LW{1'b0}};
        end
    endgenerate
    // Stage 1: one local 8:1 generation read mux per group, registered into the
    // group read flops. The scan only drives 8 group cones instead of a 64:1
    // mux plus the full write decoder.
    wire [GEN_WIDTH-1:0] grd_next [0:GROUPS-1];
    // Stage 2: registered group select and the single +1 increment.
    wire [GEN_WIDTH-1:0] gen_mux=grd[scan_group_q];
    wire [GEN_WIDTH-1:0] gen_inc=gen_mux+{{(GEN_WIDTH-1){1'b0}},1'b1};
    // Stage 3: group-local commit decode. Only the committed group's local
    // generation is enabled; write data is the group's own registered copy.
    wire [SLOTS-1:0] gen_we;
    wire [GEN_WIDTH-1:0] gen_wdata [0:SLOTS-1];
    genvar g,b;
    generate for (g=0;g<GROUPS;g=g+1) begin: gen_group
        reg [GEN_WIDTH-1:0] gread;
        integer k;
        always @* begin
            gread=generations[g*GSZ];
            for (k=1;k<GSZ;k=k+1)
                if ({{(32-LW){1'b0}},scan_local}==k) gread=generations[g*GSZ+k];
        end
        assign grd_next[g]=gread;
        for (b=0;b<GSZ;b=b+1) begin: slot
            assign gen_we[g*GSZ+b]=gcommit[g] && (glocal[g]==b);
            assign gen_wdata[g*GSZ+b]=gwr[g];
        end
    end endgenerate

    // T7 control registers. One write request and one read request are in flight
    // per cycle; only the selected group captures the global request.
    reg [GROUPS-1:0] wg_valid;
    reg [OFFSET_WIDTH-1:0] wg_offset [0:GROUPS-1];
    reg [7:0] wg_data [0:GROUPS-1];
    reg [LW-1:0] wg_local [0:GROUPS-1];
    reg [GROUPS-1:0] rg_valid;
    reg [OFFSET_WIDTH-1:0] rg_offset [0:GROUPS-1];
    reg [LW-1:0] rg_local [0:GROUPS-1];
    reg rm_valid;
    reg [TAG_WIDTH-1:0] rm_tag;
    reg [OFFSET_WIDTH-1:0] rm_offset;
    reg rm_error;
    wire [GW-1:0] wr_group=wr_slot[SLOT_WIDTH-1:LB];
    wire [GW-1:0] rd_group=rd_slot[SLOT_WIDTH-1:LB];
    wire [LW-1:0] wr_local,rd_local;
    generate
        if (LB>0) begin: local_slice
            assign wr_local=wr_slot[LB-1:0];
            assign rd_local=rd_slot[LB-1:0];
        end else begin: no_local_slice
            assign wr_local={LW{1'b0}};
            assign rd_local={LW{1'b0}};
        end
    endgenerate
    wire wr_accept=wr_valid && wr_ready && wr_owned;
    wire rd_advance=!data_valid || data_ready;
    assign rd_ready=initialized && rd_advance;
    wire rd_accept=rd_valid && rd_ready;
    integer gi;
    always @(posedge clk) begin
        if(reset) begin
            state<=INIT;init_index<=0;scan_index<=0;round_robin<=0;
            free_count<=0;rsp_tag<=0;rsp_status<=0;rsp_refs<=0;
            wg_valid<=0;rg_valid<=0;rm_valid<=0;rm_tag<=0;rm_offset<=0;rm_error<=0;
            data_valid<=0;data_error<=0;data_tag<=0;data_offset<=0;
            scan_slot_q<=0;scan_group_q<=0;scan_local_q<=0;gcommit<=0;
            for(gi=0;gi<GROUPS;gi=gi+1) begin
                wg_offset[gi]<=0;wg_data[gi]<=0;wg_local[gi]<=0;
                rg_offset[gi]<=0;rg_local[gi]<=0;
                grd[gi]<=0;gwr[gi]<=0;glocal[gi]<=0;
            end
        end else begin
            // T8-C read-modify-write pipeline. Stage 1 captures all group read
            // muxes every cycle, stage 2 registers gen_inc and the local slot,
            // stage 3 commits the generation during WRITE_ST.
            for(gi=0;gi<GROUPS;gi=gi+1) begin
                grd[gi]<=grd_next[gi];
                gwr[gi]<=gen_inc;
                glocal[gi]<=scan_local_q;
                gcommit[gi]<=(state==SCAN_GEN) && ({{(32-GW){1'b0}},scan_group_q}==gi);
            end
            for(gi=0;gi<SLOTS;gi=gi+1)
                if(gen_we[gi]) generations[gi]<=gen_wdata[gi];
            if(state==INIT) generations[init_index]<=0;
            // write control: one-cycle group pipeline (write commits +1 cycle).
            wg_valid<=0;
            if(wr_accept) begin
                wg_valid[wr_group]<=1'b1;
                wg_offset[wr_group]<=wr_offset;
                wg_data[wr_group]<=wr_data;
                wg_local[wr_group]<=wr_local;
            end
            // read control: hold the request when the output stage stalls.
            if(rd_advance) begin
                rg_valid<=0;
                data_valid<=rm_valid;
                if(rm_valid) begin
                    data_tag<=rm_tag;
                    data_offset<=rm_offset;
                    data_error<=rm_error;
                end
                rm_valid<=1'b0;
            end
            if(rd_accept) begin
                rg_valid[rd_group]<=1'b1;
                rg_offset[rd_group]<=rd_offset;
                rg_local[rd_group]<=rd_local;
                rm_valid<=1'b1;
                rm_tag<=rd_tag;
                rm_offset<=rd_offset;
                rm_error<=!rd_owned;
            end
            case (state)
                INIT: begin
                    references[init_index]<=0; sealed[init_index]<=0;
                    if (&init_index) begin
                        free_count<={1'b1,{SLOT_WIDTH{1'b0}}};
                        state<=IDLE;
                    end else init_index<=init_index+{{(SLOT_WIDTH-1){1'b0}},1'b1};
                end
                IDLE: if (cmd_valid) begin
                    rsp_tag<=cmd_tag; rsp_refs<=0; rsp_status<=0;
                    case (cmd_op)
                        2'd0: begin
                            if (free_count==0) begin rsp_status<=2; state<=RESPOND; end
                            else begin scan_index<=round_robin; state<=SCAN; end
                        end
                        2'd1: begin
                            if (!cmd_owned) rsp_status<=1;
                            else if (sealed[cmd_slot] || cmd_consumers==0) rsp_status<=3;
                            else begin
                                sealed[cmd_slot]<=1; references[cmd_slot]<=cmd_consumers;
                                rsp_refs<=cmd_consumers;
                            end
                            state<=RESPOND;
                        end
                        2'd2: begin
                            if (!cmd_owned) rsp_status<=1;
                            else begin
                                references[cmd_slot]<=references[cmd_slot]-2'd1;
                                rsp_refs<=references[cmd_slot]-2'd1;
                                if (references[cmd_slot]==1) begin
                                    sealed[cmd_slot]<=0; free_count<=free_count+{{SLOT_WIDTH{1'b0}},1'b1};
                                end
                            end
                            state<=RESPOND;
                        end
                        default: begin rsp_status<=4; state<=RESPOND; end
                    endcase
                end
                SCAN: begin
                    if (references[scan_index]==0) begin
                        // Found a free slot: latch it and let stage 1 (already
                        // registered this cycle) feed the increment next cycle.
                        scan_slot_q<=scan_index;
                        scan_group_q<=scan_group;
                        scan_local_q<=scan_local;
                        state<=SCAN_GEN;
                    end else scan_index<=scan_index+{{(SLOT_WIDTH-1){1'b0}},1'b1};
                end
                SCAN_GEN: begin
                    // gen_mux/gen_inc read the registered group flops; gwr and
                    // glocal capture them for the write-back stage. The response
                    // tag is registered here but is only visible in RESPOND.
                    rsp_tag<={gen_inc,scan_slot_q}; rsp_refs<=1; rsp_status<=0;
                    state<=WRITE_ST;
                end
                WRITE_ST: begin
                    // generations[scan_slot_q] is committed on this edge through
                    // the group-local decode; ownership metadata and free_count
                    // commit in the same edge so the response is only raised
                    // after the new generation is readable by the next command.
                    references[scan_slot_q]<=1; sealed[scan_slot_q]<=0;
                    free_count<=free_count-{{SLOT_WIDTH{1'b0}},1'b1};
                    round_robin<=scan_slot_q+{{(SLOT_WIDTH-1){1'b0}},1'b1};
                    state<=RESPOND;
                end
                RESPOND: if (rsp_ready) state<=IDLE;
                default: begin state<=INIT; init_index<=0; end
            endcase
        end
    end

    // Explicit per-slot banks keep every inferred memory within the native
    // mapper's supported 2 KiB geometry. Do not flatten into a 128 KiB RAM: the
    // Classic VQM bridge can produce unsupported oversized RAM primitives.
    // Each bank is enabled only by its own group register (T7), so the control
    // fanout is 8 group registers + 8 local banks instead of one 64-way net.
    genvar bank;
    generate for (bank=0;bank<SLOTS;bank=bank+1) begin: payload_bank
        reg [7:0] memory [0:(1 << OFFSET_WIDTH)-1];
        reg [7:0] read_byte;
        wire bank_we=wg_valid[bank/(1<<LB)] && ({{(32-LW){1'b0}},wg_local[bank/(1<<LB)]}==(bank%(1<<LB)));
        wire bank_re=rg_valid[bank/(1<<LB)] && ({{(32-LW){1'b0}},rg_local[bank/(1<<LB)]}==(bank%(1<<LB))) && rd_advance;
        assign bank_byte[bank]=read_byte;
        always @(posedge clk) begin
            if (bank_we)
                memory[wg_offset[bank/(1<<LB)]]<=wg_data[bank/(1<<LB)];
            if (bank_re)
                read_byte<=memory[rg_offset[bank/(1<<LB)]];
        end
    end endgenerate
endmodule
