module flex_mux(input a,b,sel, output o); assign o=sel?b:a; endmodule
module flex_gate(input i,en, output o); assign o=i&en; endmodule
module flex_occ(input i,en, output o); reg pass=0; always @(negedge i) pass<=en; assign o=i&pass; endmodule
module flex_div(input i, output reg o=0); always @(posedge i) o<=~o; endmodule
module flex_top(input clk_a,clk_b,clk_c,en,sel, output [3:0] sinks);
wire s_mux0,s_mux1,s_gate0,s_gate1,s_early,s_occ0,s_div0,s_gate2,s_occ1,s_div1,s_gate3,s_div2,s_occ2;
generate if(1) begin : l1
flex_mux u_mux0(.a(clk_a),.b(clk_b),.sel(sel),.o(s_mux0));
flex_mux u_mux1(.a(clk_c),.b(clk_c),.sel(sel),.o(s_mux1));
end endgenerate
generate if(1) begin : l2
flex_gate u_gate0(.i(s_mux0),.en(en),.o(s_gate0));
flex_gate u_gate1(.i(s_gate0),.en(en),.o(s_gate1));
flex_gate u_early(.i(s_mux0),.en(en),.o(s_early));
flex_occ u_occ0(.i(s_gate1),.en(en),.o(s_occ0));
flex_div u_div0(.i(s_occ0),.o(s_div0));
flex_gate u_gate2(.i(s_div0),.en(en),.o(s_gate2));
flex_occ u_occ1(.i(s_gate2),.en(en),.o(s_occ1));
flex_div u_div1(.i(s_occ1),.o(s_div1));
flex_gate u_gate3(.i(s_div1),.en(en),.o(s_gate3));
flex_div u_div2(.i(s_div1),.o(s_div2));
flex_occ u_occ2(.i(s_div2),.en(en),.o(s_occ2));
end endgenerate
assign sinks={s_early,s_gate3,s_mux1,s_occ2};
endmodule
