onerror {resume}
view wave
add wave -divider {Clock and initialization}
add wave /tb_afdx_system/clk /tb_afdx_system/reset /tb_afdx_system/initialized
add wave -divider {TX application input}
add wave /tb_afdx_system/app_msg_valid /tb_afdx_system/app_msg_ready
add wave -radix unsigned /tb_afdx_system/app_vl /tb_afdx_system/app_length
add wave /tb_afdx_system/app_valid /tb_afdx_system/app_ready /tb_afdx_system/app_last
add wave -radix hexadecimal /tb_afdx_system/app_data
add wave -divider {Ethernet TX and SFD}
add wave /tb_afdx_system/tx_byte_ce /tb_afdx_system/tx_line_valid /tb_afdx_system/sfd_pulse
add wave -radix hexadecimal /tb_afdx_system/tx_line_data
add wave -divider {Network A and B receive}
add wave /tb_afdx_system/rx_line_valid_a /tb_afdx_system/rx_line_valid_b
add wave -radix hexadecimal /tb_afdx_system/rx_line_data_a /tb_afdx_system/rx_line_data_b
add wave -divider {Integrity and redundancy decision}
add wave /tb_afdx_system/rx_decision /tb_afdx_system/rx_decision_pass
add wave -radix unsigned /tb_afdx_system/rx_reason
add wave -divider {RX application delivery}
add wave /tb_afdx_system/rx_valid /tb_afdx_system/rx_ready /tb_afdx_system/rx_last
add wave -radix hexadecimal /tb_afdx_system/rx_data
add wave -radix unsigned /tb_afdx_system/rx_vl /tb_afdx_system/rx_sn /tb_afdx_system/rx_len
add wave -divider {Frame accounting and errors}
add wave -radix unsigned /tb_afdx_system/delivered_frames /tb_afdx_system/rejected_frames /tb_afdx_system/bad_fcs /tb_afdx_system/full_drops
configure wave -namecolwidth 245
configure wave -valuecolwidth 85
configure wave -timelineunits us
wave zoom full
puts {Waveform loaded. Two TX frames are received on both networks. Check delivery and rejection counters at the end.}
