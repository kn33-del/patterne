open_checkpoint {/home/vik/ie421/Optimizer/Original DCPs/route_cluster_bench_6p2_impl.dcp}
if {[llength [get_pblocks cp_local_5]] > 0} { delete_pblocks [get_pblocks cp_local_5] }
create_pblock cp_local_5
resize_pblock cp_local_5 -add {SLICE_X0Y4:SLICE_X113Y92 RAMB18_X0Y3:RAMB18_X5Y37 RAMB36_X0Y1:RAMB36_X5Y18}
set_property IS_SOFT 0 [get_pblocks cp_local_5]
add_cells_to_pblock cp_local_5 [get_cells -quiet [list {u_bench/sel_b_reg[3]} {u_bench/cross_stage0_reg[2][165]} {clk_IBUF_inst} {clk_IBUF_BUFG_inst} {u_bench/cross_stage0[2][165]_i_1}]]
place_design -unplace
place_design -directive Default
phys_opt_design -placement_opt -critical_pin_opt
route_design -directive Default
write_checkpoint -force {/home/vik/ie421/Optimizer/outputrun_route_cluster_bench_6p2/candidate_5.dcp}
