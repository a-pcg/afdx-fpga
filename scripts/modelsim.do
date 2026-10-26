# Run from project root: vsim -c -do scripts/modelsim.do
onerror {quit -force -code 1}
onbreak {quit -force -code 1}
if {![file exists work]} {vlib work}
vlog -work work rtl/afdx_udp_tx.v rtl/afdx_udp_rx.v rtl/afdx_baseline_core.v tb/tb_afdx_baseline.v
vsim -c work.tb_afdx_baseline
run -all
quit -force -code 0
