module reset_mux(input rst0_ni,rst1_ni,sel, output rst_no); assign rst_no=sel?rst1_ni:rst0_ni; endmodule
module reset_sync(input clk_i,rst_ni, output rst_no); reg [1:0] sync_ff=0; always @(posedge clk_i or negedge rst_ni) if(!rst_ni) sync_ff<=0; else sync_ff<={sync_ff[0],1'b1}; assign rst_no=sync_ff[1]; endmodule
module reset_top(input clk_i,rst_a_ni,rst_b_ni,rst_c_ni,sel, output [3:0] sinks, output noise);
wire s_root_a,s_root_b,s_early1,s_sync0,s_sync1,s_early2,s_mux_mid,s_sync3,s_sync4,s_early8,s_mux9,s_sync10;
reset_mux outside_reset_tree(.rst0_ni(clk_i),.rst1_ni(clk_i),.sel(sel),.rst_no(noise));
generate if(1) begin : l1
reset_mux u_root_a(.rst0_ni(rst_a_ni),.rst1_ni(rst_b_ni),.sel(sel),.rst_no(s_root_a));
reset_mux u_root_b(.rst0_ni(rst_c_ni),.rst1_ni(rst_c_ni),.sel(sel),.rst_no(s_root_b));
reset_sync u_early1(.rst_ni(s_root_b),.clk_i(clk_i),.rst_no(s_early1));
end endgenerate
generate if(1) begin : l2
reset_sync u_sync0(.rst_ni(s_root_a),.clk_i(clk_i),.rst_no(s_sync0));
reset_sync u_sync1(.rst_ni(s_sync0),.clk_i(clk_i),.rst_no(s_sync1));
reset_sync u_early2(.rst_ni(s_root_a),.clk_i(clk_i),.rst_no(s_early2));
reset_mux u_mux_mid(.rst0_ni(s_sync1),.rst1_ni(s_root_a),.sel(sel),.rst_no(s_mux_mid));
reset_sync u_sync3(.rst_ni(s_mux_mid),.clk_i(clk_i),.rst_no(s_sync3));
reset_sync u_sync4(.rst_ni(s_sync3),.clk_i(clk_i),.rst_no(s_sync4));
reset_sync u_early8(.rst_ni(s_sync4),.clk_i(clk_i),.rst_no(s_early8));
reset_mux u_mux9(.rst0_ni(s_sync4),.rst1_ni(s_root_a),.sel(sel),.rst_no(s_mux9));
reset_sync u_sync10(.rst_ni(s_mux9),.clk_i(clk_i),.rst_no(s_sync10));
end endgenerate
assign sinks={s_early1,s_early2,s_early8,s_sync10};
endmodule
