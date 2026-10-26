onerror {quit -force -code 1}
onbreak {quit -force -code 1}
vlib work
vlog -work work afdx_bag_scheduler64.v afdx_buffered_vl.v afdx_integrity_checker.v afdx_packet_pool.v afdx_rx_dual.v afdx_rx_merge.v afdx_shared_tx.v afdx_system_top.v afdx_udp_rx.v afdx_udp_tx_shared.v afdx_vl_queue.v eth_crc32_byte.v eth_mac_rx.v eth_mac_tx.v tb_afdx_system.v
vsim -wlf waveform.wlf -voptargs=+acc work.tb_afdx_system
log /tb_afdx_system/*
vcd file waveform.vcd
vcd add /tb_afdx_system/clk /tb_afdx_system/reset /tb_afdx_system/tx_line_valid /tb_afdx_system/tx_byte_ce /tb_afdx_system/tx_line_data /tb_afdx_system/sfd_pulse /tb_afdx_system/rx_valid /tb_afdx_system/rx_last /tb_afdx_system/rx_decision /tb_afdx_system/rx_decision_pass /tb_afdx_system/delivered_frames /tb_afdx_system/rejected_frames
run -all
vcd flush
quit -force -code 0
