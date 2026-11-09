const ROOT='D:/VMshare/clocktree7/docs/images/';
const FONT='Microsoft YaHei';
function txt(s,name,value,x,y,w,h,size=24,color='#15283E',bold=false){let a=s.shapes.items.find(v=>v.name===name);if(!a)a=s.shapes.add({name,geometry:'textbox',fill:'none',line:{fill:'none',width:0}});a.position={left:x,top:y,width:w,height:h};a.text=value;a.text.style={typeface:FONT,fontSize:size,color,bold,wrap:'square',autoFit:'none',insets:0,verticalAlignment:'top'};return a;}
function crop(im,W,H,x,y,w,h,f){im.crop={left:x/W,top:y/H,right:(W-x-w)/W,bottom:(H-y-h)/H};im.frame=f;im.fit='contain';}
function img(s,file){return s.images.add({path:ROOT+file,alt:'ClockTree7 实际界面局部：'+file,fit:'contain'});}
function tab(s,values,x,y,w,h,widths){const t=s.tables.add({rows:values.length,columns:values[0].length,left:x,top:y,width:w,height:h,values,columnWidths:widths});t.borders.assign({fill:'#DDE5ED',width:1,style:'solid'});for(let r=0;r<values.length;r++)for(let c=0;c<values[0].length;c++){const a=t.getCell(r,c);a.fill=r===0?'#101E33':(r%2?'#FFFFFF':'#F6F8FB');a.text.style={typeface:FONT,fontSize:22,color:r===0?'#FFFFFF':'#15283E',bold:r===0,verticalAlignment:'middle',insets:{top:10,bottom:10,left:15,right:15},autoFit:'none'};}return t;}
export async function finish({presentation,input}){
 const S=n=>presentation.slides.getItem(n-1);
 let s=S(5);
 const old=await presentation.inspect({select:'$.slides[4].elements[*]',include:['id']});presentation.delete(old.records.filter(v=>v.id.startsWith('im/')).map(v=>v.id));
 txt(s,'clock','时钟树：MUX、GATE、OCC、DIV',58,189,1150,42,29,'#12877E',true);
 for(let k=0;k<4;k++){let a=img(s,'device-library.png');crop(a,612,684,k%2?306:24,k<2?154:390,270,164,{left:58+k*302,top:246,width:260,height:158});}
 txt(s,'reset','复位树：MUX、RSY_SYNC',58,441,600,42,29,'#12877E',true);
 for(let k=0;k<2;k++){let a=img(s,'reset-device-library.png');crop(a,612,448,k?306:24,154,270,164,{left:58+k*302,top:493,width:260,height:158});}
 txt(s,'subsystem','SUBSYSTEM',705,491,510,45,29,'#15283E',true);
 txt(s,'subsystem-copy','两类分析共用的输入终点\n各输入独立归属网络\n器件引用不重复计数',705,544,510,98,24);
 s=S(24);crop(s.images.items[0],950,620,17,489,444,25,{left:468,top:467,width:756,height:47});
 tab(s,[['选择','端口名','实际位宽','适用实例'],['已选','clk_bus','2 bit [3:2]','wb_top.s0'],['已选','aux_clk','1 bit','wb_top.s0'],['已选','clk_bus','3 bit [4:2]','wb_top.s1'],['已选','aux_clk','1 bit','wb_top.s1']],468,196,756,244,[90,170,190,306]);
 const confirm=img(s,'subsystem-input-preview.png');crop(confirm,950,620,811,522,122,38,{left:1009,top:549,width:207,height:65});
 txt(s,'confirm-caption','实际对话框的计数与确认入口',468,547,510,44,23,'#52677E');
 s=S(27);crop(s.images.items[0],1380,680,360,59,750,129,{left:468,top:218,width:756,height:756*129/750});
 crop(s.images.items[1],1380,680,0,564,570,114,{left:468,top:423,width:756,height:151});
 txt(s,'issue-count','702 个筛选问题，涉及 3 个实例',468,373,756,44,28,'#12877E',true);
 s=S(29);const old29=await presentation.inspect({select:'$.slides[28].elements[*]',include:['id']});const ims=old29.records.filter(v=>v.id.startsWith('im/'));presentation.delete(ims[0].id);
 tab(s,[['真实末端输入','$var_clkip','使用模板','状态'],['sv_top.sink.clocks[3]','sv_top.ng3','起始无 MUX','已就绪'],['sv_top.sink.clocks[2]','sv_top.cd','起始有 MUX','已就绪']],58,183,1166,156,[400,296,260,210]);
 s.images.items[0].frame={left:509,top:353,width:715,height:273};
 txt(s,'sv-demo-heading','检查绑定后生成',58,365,410,45,28,'#12877E',true);
 txt(s,'sv-demo-body','1. 核对两个末端输入\n2. 检查门控顺序与复位来源\n3. 选择有歧义的寄存器\n4. 点击全部分支写入一个文件',58,421,410,185,24);
 S(32).tables.items[0].getCell(2,0).value='GUI 编辑与结果浏览\n（Windows 命令示例）';
 // Preserve the source proportions after applying native image crops.
 let fixed=0;
 for(const page of presentation.slides.items)for(const image of page.images.items){
  const key=Object.keys(input.dimensions).find(k=>(image.alt||'').endsWith('：'+k));const c=image.crop;
  if(!key||!c)continue;
  const d=input.dimensions[key],sw=d[0]*(1-Number(c.left||0)-Number(c.right||0)),sh=d[1]*(1-Number(c.top||0)-Number(c.bottom||0));
  const f=image.frame,scale=Math.min(f.width/sw,f.height/sh),width=sw*scale,height=sh*scale;
  image.frame={left:f.left+(f.width-width)/2,top:f.top+(f.height-height)/2,width,height};fixed++;
 }
 return {slides:presentation.slides.items.length,naturalImageFrames:fixed};
}
