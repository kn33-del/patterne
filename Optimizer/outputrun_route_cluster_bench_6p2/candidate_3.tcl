open_checkpoint {/home/vik/ie421/Optimizer/Original DCPs/route_cluster_bench_6p2_impl.dcp}
if {[llength [get_pblocks cp_local_3]] > 0} { delete_pblocks [get_pblocks cp_local_3] }
create_pblock cp_local_3
resize_pblock cp_local_3 -add {SLICE_X0Y16:SLICE_X103Y103 RAMB18_X0Y9:RAMB18_X4Y41 RAMB36_X0Y4:RAMB36_X4Y20}
set_property IS_SOFT 0 [get_pblocks cp_local_3]
add_cells_to_pblock cp_local_3 [get_cells -quiet [list {u_bench/sel_b_reg[3]} {u_bench/cross_stage0_reg[0][150]} {clk_IBUF_inst} {clk_IBUF_BUFG_inst} {u_bench/cross_stage0[0][150]_i_1}]]
place_design -unplace
place_design -directive Default
phys_opt_design -placement_opt -critical_pin_opt
route_design -directive Default
write_checkpoint -force {/home/vik/ie421/Optimizer/outputrun_route_cluster_bench_6p2/candidate_3.dcp}
