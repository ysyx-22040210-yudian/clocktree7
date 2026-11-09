const ROOT='D:/VMshare/clocktree7';
const IMG=ROOT+'/docs/images/';
const FONT='Microsoft YaHei';
const INK='#15283E', TEAL='#12877E', MUTED='#52677E';
function box(s,name,value,x,y,w,h,size=24,color=INK,bold=false){
 let a=s.shapes.items.find(v=>v.name===name);
 if(!a)a=s.shapes.add({name,geometry:'textbox',position:{left:x,top:y,width:w,height:h},fill:'none',line:{fill:'none',width:0}});
 a.position={left:x,top:y,width:w,height:h};a.text=value;
 a.text.style={typeface:FONT,fontSize:size,color,bold,wrap:'square',autoFit:'none',insets:0,verticalAlignment:'top'};
 return a;
}
function put(s,name,value){s.shapes.items.find(v=>v.name===name).text=value;}
function crop(im,W,H,x,y,w,h,frame){im.crop={left:x/W,top:y/H,right:(W-x-w)/W,bottom:(H-y-h)/H};im.frame=frame;im.fit='contain';}
function pic(s,file,x,y,w,h){return s.images.add({path:file.includes(':')?file:IMG+file,fit:'contain',alt:'ClockTree7 实际界面局部：'+file,position:{left:x,top:y,width:w,height:h}});}
function tab(s,values,x,y,w,h,widths){
 let t=s.tables.add({rows:values.length,columns:values[0].length,left:x,top:y,width:w,height:h,values,columnWidths:widths});
 t.borders.assign({fill:'#DDE5ED',width:1,style:'solid'});
 for(let r=0;r<values.length;r++)for(let c=0;c<values[0].length;c++){let a=t.getCell(r,c);a.fill=r===0?'#101E33':(r%2?'#FFFFFF':'#F6F8FB');a.text.style={typeface:FONT,fontSize:22,color:r===0?'#FFFFFF':INK,bold:r===0,verticalAlignment:'middle',insets:{top:10,bottom:10,left:15,right:15},autoFit:'none'};}return t;
}
export async function polish({presentation}){
 const S=n=>presentation.slides.getItem(n-1);
 let s=S(5);
 crop(s.images.items[0],612,684,24,94,552,460,{left:58,top:238,width:550,height:342});
 crop(s.images.items[1],612,448,24,94,552,230,{left:660,top:259,width:565,height:242});
 s.shapes.items.find(v=>v.name==='reset').position={left:660,top:193,width:500,height:48};
 box(s,'subsystem','SUBSYSTEM',58,608,280,42,29,INK,true);
 box(s,'subsystem-copy','共同的输入终点，各输入独立归属网络，引用不重复计数。',344,609,880,48,24);
 crop(S(6).images.items[0],1840,1000,0,350,1840,584,{left:58,top:181,width:1166,height:446});
 put(S(7),'title','查找范围与复位绑定');S(7).tables.items[0].getCell(3,2).value='soc_top.u_crg';
 for(const n of [8,10,19]){
  s=S(n);for(let j=0;j<4;j++){
   let w=n===19?550:620;
   s.shapes.items.find(v=>v.name==='heading-'+j).position={left:58,top:193+j*105,width:w,height:36};
   let b=s.shapes.items.find(v=>v.name==='body-'+j);b.position={left:58,top:235+j*105,width:w,height:62};b.text.style={fontSize:23};
  }
 }
 s=S(8);tab(s,[['端口名','实例中的实际范围'],['clk_bus','[3:2] / [4:2]'],['aux_clk','1 bit']],730,232,494,230,[180,314]);
 box(s,'selection-count','2 个已选端口名',730,493,490,60,38,TEAL,true);
 box(s,'selection-count-note','同名跨实例合并计数\n总线整口确认，追踪时逐 bit 展开',730,558,490,66,21,MUTED);
 s=S(10);let im=pic(s,'port-signal-details.png',742,201,482,404);
 crop(im,1840,1000,1555,275,266,289,{left:742,top:201,width:482,height:404});
 s=S(12);
 crop(s.images.items[0],1840,1000,1555,605,272,140,{left:592,top:391,width:630,height:234});
 box(s,'sample-heading','双向核验样例的连接摘要',592,198,630,42,27,TEAL,true);
 box(s,'sample-edge','fixture_top.l2.n0_gate.o\n  → fixture_top.l2.n0_mux.a',592,250,630,80,25);
 box(s,'sample-signal','实际信号：fixture_top.l2.s2_n0_gate',592,337,630,42,22,MUTED);
 s=S(14);crop(s.images.items[0],1840,1000,18,570,277,129,{left:520,top:276,width:650,height:305});
 box(s,'memory-sample','低阈值恢复测试的实际界面',520,204,690,44,27,TEAL,true);
 put(s,'note','截图阈值为 0.375 GiB，用于触发保护验收；正常默认值为 60 GiB。');
 s=S(15);crop(s.images.items[0],1920,1080,142,310,776,404,{left:468,top:210,width:756,height:408});
 S(16).tables.items[0].getCell(4,1).value='$var_gt0～$var_gt2\n来自 gt 角色口';
 s=S(17);put(s,'left-heading','DFT 关闭下的时序逻辑示例');
 put(s,'left-code','always @(posedge clk)\n  D <= cfg_next;\n\nassign ctl = dft_mode\n  ? test_value : D[5:2];\n\nalways @(posedge clk)\n  E[10] <= sw_done;');
 s=S(19);tab(s,[['真实末端输入','使用模板'],['sink.clocks[2]','起始有 MUX'],['sink.clocks[3]','起始无 MUX']],660,242,564,238,[300,264]);
 box(s,'sv-count','2 段代码，1 个 SV 文件',660,518,564,64,36,TEAL,true);
 box(s,'sv-scope','当前支持时钟树分支',660,584,564,38,22,MUTED);
 S(20).tables.items[0].getCell(3,0).value='$var_gt0～$var_gt2';
 S(20).tables.items[0].getCell(4,0).value='$var_pll0～$var_pll3';
 s=S(22);put(s,'demo-list','01  加载设计与规则\n02  确认子系统输入\n03  提取与状态检查\n04  沿实际输入跳转\n05  定位与导出问题\n06  辅助口追到寄存器\n07  模板预览与 SV 导出');
 s.shapes.items.find(v=>v.name==='demo-list').text.style={fontSize:30};
 put(s,'note','以下为不同功能验收样例的实机截图，每页注明设计或样例身份。');
 s=S(23);crop(s.images.items[0],1840,1000,12,363,283,366,{left:866,top:224,width:350,height:407});
 im=pic(s,'default-two-layers.png',480,224,345,407);
 crop(im,1840,1000,12,252,283,339,{left:480,top:224,width:345,height:407});
 box(s,'config-caption-1','设计入口与层数',480,183,345,36,24,TEAL,true);
 box(s,'config-caption-2','sv_top 层规则与复位',866,183,350,36,24,TEAL,true);
 put(s,'body-1','填写父层和 module\n子系统省略输出口');
 box(s,'demo-case','样例：新建默认配置 / sv_top 的 L1 配置',58,645,1150,28,17,MUTED);
 s=S(24);box(s,'demo-case','样例：wb_top，四条实例记录对应两个已选端口名',58,645,1150,28,17,MUTED);
 s=S(25);crop(s.images.items[0],1840,1000,328,174,1205,179,{left:58,top:187,width:1166,height:210});
 im=pic(s,'runtime-stages-complete.png',58,451,1166,140);
 crop(im,1840,1000,328,826,1205,137,{left:58,top:451,width:1166,height:140});
 box(s,'stage-caption','阶段完成状态与已提交工作',58,408,1166,38,25,TEAL,true);
 put(s,'note','样例：scope_top。阶段完成、断点提交、结果生成和未解析数量分别核对。');
 s=S(26);crop(s.images.items[0],1320,820,14,350,807,347,{left:468,top:228,width:756,height:350});
 box(s,'demo-case','样例：wb_top。图中两处 s1 是同一真实实例在不同输入分支的引用。',58,645,1150,28,17,MUTED);
 s=S(27);put(s,'heading-2','导出筛选结果的全部页');put(s,'body-2','清空筛选后可导出全量\n分别统计问题数与涉及实例数');
 crop(s.images.items[0],1380,680,0,0,1370,195,{left:468,top:210,width:756,height:132});
 im=pic(s,'workbench-issues.png',468,410,756,118);crop(im,1380,680,0,564,1375,114,{left:468,top:410,width:756,height:118});
 box(s,'issue-count','702 个筛选问题，涉及 3 个实例',468,355,756,44,28,TEAL,true);
 box(s,'demo-case','专用异常验收样例：筛选 s0 后为 702 条；原始夹具共 704 条问题。',58,645,1150,28,17,MUTED);
 s=S(28);crop(s.images.items[0],1360,860,16,690,638,148,{left:468,top:331,width:756,height:254});
 box(s,'register-result','reg_top.E[10]',468,203,756,56,36,TEAL,true);
 box(s,'register-semantic','已确认寄存器的 D 写入位',468,268,756,46,27);
 box(s,'demo-case','样例：reg_top。下方显示实际触发条件、写入表达式与 dft_mode 置零记录。',58,645,1150,28,17,MUTED);
 s=S(29);crop(s.images.items[0],1400,900,17,246,1362,98,{left:58,top:184,width:1166,height:116});
 im=pic(s,'sv-reset-preview.png',509,323,715,288);crop(im,1400,900,18,601,558,213,{left:509,top:323,width:715,height:288});
 box(s,'sv-demo-heading','检查绑定后生成',58,337,410,45,28,TEAL,true);
 box(s,'sv-demo-body','1. 核对两个末端输入\n2. 检查门控顺序与复位来源\n3. 选择有歧义的寄存器\n4. 点击全部分支写入一个文件',58,395,410,190,24);
 put(s,'note','样例：sv_top。已生成 2 段、跳过 0 段；mapping JSON 保留候选及处理结果。');
 s=S(30);put(s,'metric-1','26.40 ms');put(s,'metric-1-label','缩放 P95');put(s,'metric-2','25.05 ms');put(s,'metric-2-label','拖动 P95');
 box(s,'metric-3','停止缩放后\n147.70 ms 恢复细节',855,527,355,85,24,INK,true);
 s=S(31);s.tables.items[0].getCell(1,1).value='260 项通过 / 1 项跳过';
 s.tables.items[0].getCell(2,1).value='PULP：62 实例 / 48 边\nC910：6,984 实例 / 6,983 边';
 s.tables.items[0].getCell(3,1).value='OpenTitan clkmgr\n15 实例 / 10 边';
 s=S(32);s.tables.items[0].getCell(1,2).value='bash clocktree.sh doctor\n  --self-test';
 s.tables.items[0].getCell(2,2).value='clocktree.cmd doctor\n  --viewer-only --gui';
 s.tables.items[0].getCell(3,2).value='bash clocktree.sh doctor\n  --self-test --gui';
 s.tables.items[0].getCell(4,1).value='源码 / 离线包、启动器、手册\nSHA-256 清单与样例基准结果';
 for(const n of [1,2,3,7,8,10,11,14,15,17,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34]){
  let q=S(n).speakerNotes;
  q.text=String(q.text).replace('口径：资料截至 2026-11-10，源码提交 507990b。截图为样例或已保存的实际运行结果；没有使用商业客户设计或承诺商业全芯片性能。','资料截至 2026-11-10，源码提交 507990b。性能数值与结论适用于对应记录的设计范围和测试环境。');
 }
 return {slides:presentation.slides.items.length,polished:true};
}
