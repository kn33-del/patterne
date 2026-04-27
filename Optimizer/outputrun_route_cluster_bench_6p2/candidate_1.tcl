open_checkpoint {/home/vik/ie421/Optimizer/Original DCPs/route_cluster_bench_6p2_impl.dcp}
if {[llength [get_pblocks cp_local_1]] > 0} { delete_pblocks [get_pblocks cp_local_1] }
create_pblock cp_local_1
resize_pblock cp_local_1 -add {SLICE_X0Y16:SLICE_X103Y116 RAMB18_X0Y9:RAMB18_X4Y47 RAMB36_X0Y4:RAMB36_X4Y23}
set_property IS_SOFT 0 [get_pblocks cp_local_1]
add_cells_to_pblock cp_local_1 [get_cells -quiet [list {u_bench/sel_c_reg[3]} {u_bench/cross_stage2_reg[8][151]} {clk_IBUF_inst} {clk_IBUF_BUFG_inst} {u_bench/cross_stage2[8][151]_i_1}]]
place_design -unplace
place_design -directive Default
phys_opt_design -placement_opt -critical_pin_opt
route_design -directive Default
write_checkpoint -force {/home/vik/ie421/Optimizer/outputrun_route_cluster_bench_6p2/candidate_1.dcp}
