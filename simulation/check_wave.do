onerror {quit -force -code 1}
set d [examine -radix unsigned /tb_afdx_system/delivered_frames]
set r [examine -radix unsigned /tb_afdx_system/rejected_frames]
set f [open wave_counts.txt w]
puts $f "FINAL_DELIVERED=$d"
puts $f "FINAL_REJECTED=$r"
close $f
if {$d != 2 || $r != 2} {quit -force -code 1}
quit -force -code 0
