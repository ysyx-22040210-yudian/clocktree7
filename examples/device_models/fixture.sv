module ct_buf(input wire i, output wire o); assign o=i; endmodule
module ct_gate(input wire i, input wire en, output wire o); assign o=i & en; endmodule
module ct_mux(input wire a,b,sel, output wire o); assign o=sel ? b:a; endmodule
module ct_occ(input wire i,en, output wire o); reg [1:0] pulses=2; reg pass=0; always @(posedge i) if(en) pulses<=2; else if(pulses!=0) pulses<=pulses-1'b1; always @(negedge i) pass<=en || (pulses!=0); assign o=i & pass; endmodule
module stage_1(input wire en,sel,input wire [2:0] incoming,output wire s1_n0_root_a,output wire s1_n0_root_b,output wire s1_n0_root_c,output wire s1_n0_end1);
ct_occ n0_root_a(.i(incoming[0]),.en(en),.o(s1_n0_root_a));
ct_occ n0_root_b(.i(incoming[1]),.en(en),.o(s1_n0_root_b));
ct_occ n0_root_c(.i(incoming[2]),.en(en),.o(s1_n0_root_c));
ct_occ n0_end1(.i(s1_n0_root_a),.en(en),.o(s1_n0_end1));
endmodule
module stage_2(input wire en,sel,input wire [1:0] incoming,output wire s2_n0_gate,output wire s2_n0_end2,output wire s2_n0_mux,output wire s2_n0_same_layer);
ct_gate n0_gate(.i(incoming[0]),.en(en),.o(s2_n0_gate));
ct_occ n0_end2(.i(s2_n0_gate),.en(en),.o(s2_n0_end2));
ct_mux n0_mux(.a(s2_n0_gate),.b(incoming[1]),.sel(sel),.o(s2_n0_mux));
ct_occ n0_same_layer(.i(s2_n0_mux),.en(en),.o(s2_n0_same_layer));
endmodule
module stage_3(input wire en,sel,input wire [0:0] incoming,output wire s3_n0_gate,output wire s3_n0_end3);
ct_gate n0_gate(.i(incoming[0]),.en(en),.o(s3_n0_gate));
ct_occ n0_end3(.i(s3_n0_gate),.en(en),.o(s3_n0_end3));
endmodule
module stage_4(input wire en,sel,input wire [0:0] incoming,output wire s4_n0_end4);
ct_occ n0_end4(.i(incoming[0]),.en(en),.o(s4_n0_end4));
endmodule
module stage_5(input wire en,sel,input wire [1:0] incoming,output wire s5_n0_mux,output wire s5_n0_end5);
ct_mux n0_mux(.a(incoming[0]),.b(incoming[1]),.sel(sel),.o(s5_n0_mux));
ct_gate n0_end5(.i(s5_n0_mux),.en(en),.o(s5_n0_end5));
endmodule
module stage_6(input wire en,sel,input wire [0:0] incoming,output wire s6_n0_end6);
ct_occ n0_end6(.i(incoming[0]),.en(en),.o(s6_n0_end6));
endmodule
module stage_7(input wire en,sel,input wire [0:0] incoming,output wire s7_n0_enter7,output wire s7_n0_end7);
ct_occ n0_enter7(.i(incoming[0]),.en(en),.o(s7_n0_enter7));
ct_gate n0_end7(.i(s7_n0_enter7),.en(en),.o(s7_n0_end7));
endmodule
module fixture_top(input wire clk_a,clk_b,clk_c,en,sel, output wire [6:0] sinks);
wire s1_n0_root_a;
wire s1_n0_root_b;
wire s1_n0_root_c;
wire s1_n0_end1;
wire s2_n0_gate;
wire s2_n0_end2;
wire s2_n0_mux;
wire s2_n0_same_layer;
wire s3_n0_gate;
wire s3_n0_end3;
wire s4_n0_end4;
wire s5_n0_mux;
wire s5_n0_end5;
wire s6_n0_end6;
wire s7_n0_enter7;
wire s7_n0_end7;
stage_1 l1(.en(en),.sel(sel),.incoming({clk_c,clk_b,clk_a}),.s1_n0_root_a(s1_n0_root_a),.s1_n0_root_b(s1_n0_root_b),.s1_n0_root_c(s1_n0_root_c),.s1_n0_end1(s1_n0_end1));
stage_2 l2(.en(en),.sel(sel),.incoming({s1_n0_root_b,s1_n0_root_a}),.s2_n0_gate(s2_n0_gate),.s2_n0_end2(s2_n0_end2),.s2_n0_mux(s2_n0_mux),.s2_n0_same_layer(s2_n0_same_layer));
stage_3 l3(.en(en),.sel(sel),.incoming({s2_n0_gate}),.s3_n0_gate(s3_n0_gate),.s3_n0_end3(s3_n0_end3));
stage_4 l4(.en(en),.sel(sel),.incoming({s1_n0_root_b}),.s4_n0_end4(s4_n0_end4));
stage_5 l5(.en(en),.sel(sel),.incoming({s3_n0_gate,s2_n0_same_layer}),.s5_n0_mux(s5_n0_mux),.s5_n0_end5(s5_n0_end5));
stage_6 l6(.en(en),.sel(sel),.incoming({s1_n0_root_c}),.s6_n0_end6(s6_n0_end6));
stage_7 l7(.en(en),.sel(sel),.incoming({s5_n0_mux}),.s7_n0_enter7(s7_n0_enter7),.s7_n0_end7(s7_n0_end7));
assign sinks={s1_n0_end1,s2_n0_end2,s3_n0_end3,s4_n0_end4,s5_n0_end5,s6_n0_end6,s7_n0_end7};
endmodule
