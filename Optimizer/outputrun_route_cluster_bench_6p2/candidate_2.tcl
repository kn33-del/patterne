open_checkpoint {/home/vik/ie421/Optimizer/Original DCPs/route_cluster_bench_6p2_impl.dcp}
if {[llength [get_pblocks cp_local_2]] > 0} { delete_pblocks [get_pblocks cp_local_2] }
create_pblock cp_local_2
resize_pblock cp_local_2 -add {SLICE_X0Y16:SLICE_X99Y120 RAMB18_X0Y9:RAMB18_X4Y49 RAMB36_X0Y4:RAMB36_X4Y24}
set_property IS_SOFT 0 [get_pblocks cp_local_2]
add_cells_to_pblock cp_local_2 [get_cells -quiet [list {u_bench/GEN_LANES[9].u_lane_tile/local3_reg[1]} {u_bench/GEN_LANES[9].u_lane_tile/stage3_reg[122]} {clk_IBUF_inst} {clk_IBUF_BUFG_inst} {u_bench/GEN_LANES[9].u_lane_tile/stage3[122]_i_1__1}]]
place_design -unplace
place_design -directive Default
phys_opt_design -placement_opt -critical_pin_opt
route_design -directive Default
write_checkpoint -force {/home/vik/ie421/Optimizer/outputrun_route_cluster_bench_6p2/candidate_2.dcp}
