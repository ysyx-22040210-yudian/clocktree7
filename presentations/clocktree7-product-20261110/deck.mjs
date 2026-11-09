const ROOT='D:/VMshare/clocktree7';
const IMG=ROOT+'/docs/images/';
const FONT='Microsoft YaHei';
const C={navy:'#101E33',ink:'#15283E',muted:'#52677E',teal:'#12877E',cyan:'#73C7F3',purple:'#8D6AC9',paper:'#F6F8FB',line:'#DDE5ED'};
const URL='https://github.com/ysyx-22040210-yudian/clocktree7/blob/507990b13d0f5bb5d73abefb35255ec832b82040/';

function text(s,name,value,x,y,w,h,size=24,color=C.ink,bold=false){
 const sh=s.shapes.add({geometry:'textbox',name,position:{left:x,top:y,width:w,height:h},fill:'none',line:{fill:'none',width:0}});
 sh.text=value;sh.text.style={typeface:FONT,fontSize:size,color,bold,wrap:'square',autoFit:'none',insets:0,verticalAlignment:'top'};
 return sh;
}
function picture(s,file,x,y,w,h,crop){return s.images.add({path:file.includes(':')?file:IMG+file,alt:'ClockTree7 实际运行界面：'+file,fit:'contain',position:{left:x,top:y,width:w,height:h},...(crop?{crop}:{})});}
function table(s,values,x,y,w,h,widths,size=23){
 const t=s.tables.add({rows:values.length,columns:values[0].length,left:x,top:y,width:w,height:h,values,columnWidths:widths});
 t.borders.assign({fill:C.line,width:1,style:'solid'});
 for(let r=0;r<values.length;r++)for(let c=0;c<values[0].length;c++){
  const cell=t.getCell(r,c);cell.fill=r===0?C.navy:(r%2?'#FFFFFF':C.paper);
  cell.text.style={typeface:FONT,fontSize:size,color:r===0?'#FFFFFF':C.ink,bold:r===0,verticalAlignment:'middle',insets:{top:10,bottom:10,left:15,right:15},autoFit:'none'};
 }
 return t;
}
function base(p,i,d){
 const s=i===0?p.slides.getItem(0):p.slides.add({width:1280,height:720});
 s.background.fill=d.dark?C.navy:'#FFFFFF';
 if(i>0){text(s,'title',d.title,56,42,1168,64,42,d.dark?'#FFFFFF':C.ink,true);
  if(d.sub)text(s,'subtitle',d.sub,58,119,1158,52,22,d.dark?'#B8C9DB':C.muted);
  text(s,'footer','ClockTree7  ·  '+String(i+1).padStart(2,'0'),56,681,500,22,14,d.dark?'#9FB1C6':C.muted);
 }
 s.speakerNotes.text='讲解建议：\n'+d.notes+'\n\n来源：\n'+d.sources.map(path=>URL+path).join('\n')+'\n\n口径：资料截至 2026-11-10，源码提交 507990b。截图为样例或已保存的实际运行结果；没有使用商业客户设计或承诺商业全芯片性能。';
 return s;
}
function stack(s,items,x,y,w,gap=124,size=24,dark=false){items.forEach((b,j)=>{text(s,'heading-'+j,b[0],x,y+j*gap,w,36,27,dark?C.cyan:C.teal,true);text(s,'body-'+j,b[1],x,y+42+j*gap,w,gap-46,size,dark?'#DFE8F3':C.ink);});}
function note(s,v,y=632,dark=false){text(s,'note',v,58,y,1160,38,17,dark?'#B8C9DB':C.muted);}

export const slides=[
{title:'ClockTree7',layout:'cover',dark:true,notes:'开场约 45 秒。ClockTree7 面向 SoC 设计、CRG/DFT 与验证团队，将已 elaboration 的 RTL 连接恢复成可浏览、可核对的时钟与复位结构。介绍将先覆盖实际能力与边界，再说明 NPI 分析原理，最后用真实 Tk 截图逐步演示。它是静态结构分析工具，不能替代物理 CTS 或时序签核。',sources:['README.md','docs/GUI_USER_MANUAL.zh-CN.md']},
{title:'ClockTree7 的工作范围',sub:'输入器件规则与 elab，恢复配置范围内的全部目标分支',layout:'image',image:'target-mode-workbench.png',items:[['工程输入','RTL filelist 或 KDB\n层次、module、关注端口'],['自动分析','枚举实际例化\n解析端口连接与结构关系'],['工程输出','架构图、连接证据、问题表\n寄存器映射与分支 SV']],notes:'本工具不是由用户手工连线的制图器。用户给出 module 定义名、例化直接父层、关注输入/输出，以及器件类型；实际实例与连线都由 elab 解析。所谓全部分支，是所有配置目标结构的已解析分支，不是未经配置的全部标准单元负载。',sources:['README.md','docs/TARGET_MODE_VALIDATION.zh-CN.md']},
{title:'适用场景与交付物',sub:'围绕结构审阅、问题定位和验证准备组织结果',layout:'table',values:[['使用场景','工具支持','可交付结果'],['CRG / SoC 架构审阅','重建多根、扇出、汇合与跳级结构','完整 JSON、当前视图 SVG'],['RTL / DFT 连接核对','按实例、端口和实际信号定位','连接摘要、原始 NPI 证据'],['控制寄存器梳理','辅助口追到有时序依据的 Q / D','逐 bit CSV、映射与证据 JSON'],['验证代码准备','按真实末端输入套用两种模板','单一 SV 文件、全部分支反馈'],['大设计任务运维','阶段日志、内存保护、断点恢复','可诊断、可继续的任务记录']],widths:[280,430,458],notes:'商业价值用实际工作产物说明，不引用未经测量的节省人力百分比。可用于架构评审前准备、RTL 连接故障定位、控制位文档核对以及验证模板批量生成。SV 生成属于时钟树功能，目前不用于复位树。',sources:['README.md','docs/GUI_USER_MANUAL.zh-CN.md']},
{title:'原生 Tk 工作台',sub:'配置、全网架构与证据在同一个桌面界面中完成',layout:'wideimage',image:'workbench-compact.png',bottom:'左侧配置与任务记录  /  中央完整架构  /  右侧属性和连线证据',notes:'指出左侧 RTL 层次与 module 规则，中间统计和画布，右侧属性、连接证据、问题入口。支持时钟/复位两个工作区，自动保存各自配置及结果。界面采用 Python Tkinter/ttk，适配离线 Linux EDA 桌面，Windows 可编辑配置与浏览已有结果。',sources:['docs/GUI_USER_MANUAL.zh-CN.md','docs/WORKBENCH_VALIDATION.zh-CN.md']},
{title:'时钟与复位器件模型',sub:'统一的端口语义，专用的图形表达',layout:'devices',notes:'时钟树支持 MUX、GATE、OCC、DIV、SUBSYSTEM；复位树支持 MUX、RSY_SYNC、SUBSYSTEM。MUX 可多输入、一个输出；GATE/OCC/DIV/RSY_SYNC 关注一个输入和一个输出。SUBSYSTEM 只有输入，没有虚构输出。器件类型由用户选择，不通过 module 名猜测；DIV 图标中的除 N 不表示工具推断了实际分频比。',sources:['clocktree/devices.py','clocktree/profiles.py','docs/GUI_USER_MANUAL.zh-CN.md']},
{title:'规则层次与真实连接',sub:'默认两级，可增删；L1～LN 用于规则标注与检查',layout:'wideimage',image:'horizontal-clock.png',crop:{left:0,top:.34,right:0,bottom:.065},bottom:'同层允许串接和扇出；允许跳级；分支可在任意层结束',notes:'截图是同层横向展开的已提取结构。层次编号是逻辑规则层，不是固定的 RTL 深度，也不要求分支走满所有层。默认紧凑布局按真实连接安排器件；保留按层排列模式。逆序层与环路作为诊断保留，不把异常边隐藏。',sources:['docs/GUI_USER_MANUAL.zh-CN.md','docs/WORKBENCH_VALIDATION.zh-CN.md']},
{title:'三个范围字段的分工',sub:'控制查找与追踪边界，减少无关遍历',layout:'table',values:[['字段','作用','填写示例'],['设计顶层','RTL 编译 / elab 导入入口\n不能只改路径就裁剪已有 KDB','soc_top'],['各规则层 RTL 路径','查该路径下直接例化的 module\n不递归搜索内部子层次','soc_top.u_crg'],['端口追踪根层次','追踪该根及内部子层次\n越界连接单独记录为范围边界','soc_top.u_domain'],['每层复位信号','供 SV 模板绑定 $var_rst\n采用分支最初器件所属层','rst_n 或完整层次路径']],widths:[260,565,343],foot:'范围限制减少额外遍历，不保证降低 Verdi 导入完整 KDB 的基础内存。',notes:'用例：查 soc_top.u_crg.u_mux 时，规则父层填 soc_top.u_crg；若查 generate 内的实例，填准确的 generate 父层。端口追踪根应包含目标器件之间的全部连接路径。设计顶层与追踪范围不能混为一谈。$var_rst 是用户配置元数据，不是自动验证过的复位连接。',sources:['docs/GUI_USER_MANUAL.zh-CN.md','docs/PORTABILITY.zh-CN.md','docs/SV_RESET_VALIDATION.zh-CN.md']},
{title:'子系统端口的确认流程',sub:'前处理只发现 input；主提取严格使用已确认名单',layout:'steps',items:[['01  定位子系统','在直接父层内匹配 module 的实际例化。'],['02  读取 input','独立 Verdi 进程读取端口名、方向和实际范围。'],['03  正则与勾选','按 re.search 筛选；实时显示选中端口名数量。'],['04  确认后提取','确认整口，主提取按实例实际位宽逐 bit 追踪。']],foot:'修改 module、层次或正则后需重新确认；未确认的端口不会自动加入主追踪。',notes:'强调正则发生在器件编辑过程中，不在启动主提取时自动扩大关注范围。默认区分大小写。非法表达式、零匹配或空选择不能完成确认。同名端口跨实例只按一个端口名计数；整条总线选择也计一个，实际追踪时按 elab 位宽展开。取消或失败的前处理结果不会当作已确认配置。',sources:['docs/GUI_USER_MANUAL.zh-CN.md','docs/VALIDATION.zh-CN.md']},
{title:'全分支浏览与上下文保留',sub:'概览、局部定位和独立分支窗口共同支持大图审阅',layout:'image',image:'branch-canvas.png',items:[['分支紧凑布局','相关器件尽量靠近\n正交布线避开器件'],['独立分支窗口','双击真实器件打开\n保留主画布与原视口'],['持续的定位上下文','上游 / 下游、后退 / 前进\n100 条搜索历史分设计保存']],notes:'整个分支定义为所选器件的全部上游边与全部下游边的并集；不包括其上游节点分出的其他旁支。MUX 保留全部已解析输入。子系统以器件引用在不同输入分支末端出现，真实实例只计算一次。当前版本已修复多输入器件跳转丢失端口上下文的问题。',sources:['docs/BRANCH_CANVAS_VALIDATION.zh-CN.md','docs/NAVIGATION_FIX_VALIDATION.zh-CN.md']},
{title:'目标结构提取的核心原理',sub:'索引关注输出，从每个关注输入反查真实驱动',layout:'steps',items:[['01  枚举目标例化','按直接父层和 module 分组查找，保存实例与端口。'],['02  读取实际绑定','通过 NPI Language Model 读取 npiHighConn。'],['03  输入反查驱动','沿别名与层次连接查找，匹配已索引的目标输出。'],['04  记录证明与边界','保存真实边、输入 driver 证据及未解析位置。']],foot:'默认模式不展开每个输出的全部普通 load，面向高扇出大设计。',notes:'用户无需提供哪个器件连到哪个器件。输出索引和输入反查使成本更贴近关注结构数量。NPI 对象比较、共享路径缓存和逐输入处理保证映射依据来自 elab，而不是字符串网名相等。公共路径首次验证后复用；缓存淘汰后按需重查。结果中的末端表示没有已配置下游结构。',sources:['docs/TARGET_MODE_VALIDATION.zh-CN.md','docs/PORTABILITY.zh-CN.md','npi/target_trace.tcl']},
{title:'两种提取模式的覆盖区别',sub:'根据关注结构覆盖与普通负载核验的需求选择',layout:'table',values:[['维度','目标结构提取（默认）','全负载核验'],['主要路径','全部关注输入反查 driver\n匹配目标输出索引','关注输入 driver\n关注输出 load 双向追踪'],['适用任务','完整恢复已配置器件网络\n减少高扇出输出枚举','需要核查目标输出的普通负载\n或核对双向证据'],['连接依据','输入反查证明\n普通负载未核验','按已执行 driver / load 保存证据'],['大设计策略','共享路径缓存、逐输入断点\n不调用输出全 load / Netlist Model','逐项迭代、入队去重\n及时释放临时句柄']],widths:[226,471,471],foot:'两种模式都保留未知逻辑与范围边界；不会把缺失路径补成已确认连线。',notes:'不要把默认目标模式的绿色状态解释为所有普通负载均已检查。全负载模式也只在配置范围和对应原生接口支持能力内分析。超大商业芯片的基础导入内存以及单次厂商 API 的行为仍需现场验收。',sources:['README.md','docs/TARGET_MODE_VALIDATION.zh-CN.md','docs/GUI_USER_MANUAL.zh-CN.md']},
{title:'可读摘要与原始连接证据',sub:'每条边都能回到实际实例、端口、信号与 NPI 路径',layout:'image',image:'connection-evidence.png',items:[['默认先看摘要','来源.输出（信号）\n→ 目标.输入（信号）'],['区分验证方向','输入反查、双向核验\n未执行方向明确说明'],['细节按需展开','跨层 / 别名转换\n完整 NPI 类型与逐步路径']],notes:'先解释用户每天需要看的摘要，再展示如何回查原始证据。实际例化连接名来自 npiHighConn，不用追踪后的规范网络名替换原信号。总线、拼接、常量和未连接状态会保留；旧结果未采集的字段会明确提示。截图是已保存 GUI 证据页面，具体证据模式以当前结果为准。',sources:['docs/GUI_USER_MANUAL.zh-CN.md','docs/WORKBENCH_VALIDATION.zh-CN.md']},
{title:'面向大设计的执行架构',sub:'让 EDA 查询、磁盘数据与 GUI 展示各自承担明确职责',layout:'table',values:[['执行部分','处理方式','目的'],['独立 Verdi 工作进程','限定范围查询，句柄及时释放','隔离原生 API 与 GUI'],['Python 分析与事件处理','逐条消费、队列回收、共享路径','控制中间状态与重复工作'],['SQLite / 流式结果','证据落盘、索引检索、逐段导出','避免把全量证据积在内存'],['后台布局进程','缓存布局和正交线段；可取消','保持 Tk 主线程响应'],['Tk 可见场景','复用图元、空间索引、缩放合并','只增删可见对象与细节']],widths:[270,510,388],foot:'资源优化不删除真实边；概览与分页改变显示范围，完整数据仍在结果索引中。',notes:'解释架构为什么更适合超大结果。缓存不是无限增长：布局缓存有数量和对象总量边界，过期后台结果丢弃。导航小图布局变化时才重建，平移只更新视口。工程成本也要说明：索引占用磁盘，后台初始布局和缓存本身有开销。',sources:['docs/MEMORY.zh-CN.md','docs/WORKBENCH_VALIDATION.zh-CN.md']},
{title:'内存阈值与可恢复的轮换',sub:'Linux 监测本次 Verdi 进程树与提取控制进程的合计 RSS',layout:'image',image:'module-checkpoints.png',items:[['阈值可配置','默认 60 GiB\n填 0 关闭监测'],['接近阈值','先提交完整断点\n再轮换 Verdi'],['触及阈值','终止、撤回未提交数据\n重启并重试当前单元']],foot:'每条 module 规则完成即提交断点，并在下一条规则前补一次 RSS 检查。',notes:'区别接近阈值的安全轮换与硬阈值时的回退重试。主任务总运行时间不设上限。若在同一单元反复无进展，停止并保留证据，不能通过无限重启掩盖无法容纳的导入。基础 KDB 导入本身超过阈值时，需要扩大资源或调整实际数据库范围，工具不能承诺自动裁剪厂商内部内存。',sources:['docs/MEMORY.zh-CN.md','docs/GUI_USER_MANUAL.zh-CN.md']},
{title:'超时、闪退与阶段诊断',sub:'保留发生位置与已提交工作，支持可核对的继续运行',layout:'image',image:'crash-recovery-notice.png',items:[['可选端口超时','默认 0 不限时\n超时仅跳过当前例化剩余端口'],['阶段日志先写入','准备、导入、查找、追踪\n拓扑、索引、结果、显示'],['重开后恢复','提示已知退出原因与阶段\n校验输入和版本后从断点继续']],foot:'日志不足时只报告已知线索；超时或跳过会把结果标为连接不完整。',notes:'这里不是给总任务加超时。端口超时从 elab 导入后开始，外部控制进程能结束卡在原生调用里的 Verdi。断点恢复撤回未提交尾部，已提交工作不重复。若旧后端仍在运行，会观察原任务而不是再起一个。配置和结果启动恢复与异常任务续跑是两个相关但不同的能力。',sources:['docs/PORT_TRACE_TIMEOUT_VALIDATION.zh-CN.md','docs/RESILIENCE_VALIDATION.zh-CN.md','docs/GUI_USER_MANUAL.zh-CN.md']},
{title:'辅助端口：实际信号与追踪方向',sub:'角色名称固定，真实 RTL 端口名由用户填写',layout:'table',values:[['器件','用户配置的辅助角色','追踪与展示'],['MUX','$var_pllsel ← pll_sel 角色口','driver → 来源时序输出 Q'],['DIV','$var_div ← div 角色口','driver → 来源时序输出 Q'],['DIV','$var_swdone ← sw_done 角色口','load → 目的寄存器写入位 D'],['GATE','$var_gt0～2 ← gt 角色口','driver → 来源时序输出 Q']],widths:[185,448,535],foot:'辅助口的例化信号名显示在器件上；主时钟/复位画布不为这些辅助口增加连线。',notes:'pll_sel/div/sw_done/gt 是角色，不要求真实模块恰好使用这些接口名。用户可以填 choose_src、ratio_cfg、done_o、enable_ctl 等实际端口。架构提取先采集绑定并检查方向；辅助口到寄存器是另一个独立任务，不改写已经证明的时钟树边。',sources:['docs/AUXILIARY_PORTS_VALIDATION.zh-CN.md','docs/REGISTER_TRACE_VALIDATION.zh-CN.md']},
{title:'有时序依据的寄存器终点',sub:'依据 elab 语句和触发关系，继续穿透组合 reg / logic',layout:'codepair',leftTitle:'DFT 关闭假设下继续追踪',leftCode:'assign ctl = dft_mode\n  ? test_value : D[5:2];\n\nalways @(posedge clk)\n  E[10] <= sw_done;',rightTitle:'结果保留信号路径与时序语义',rightBody:'控制口 → top.D[5:2] 的 Q\n\nsw_done → top.E[10] 的写入位\n\n保存触发沿、写入表达式与假设\n不虚构 RTL 中不存在的 D 引脚',foot:'默认 dft_mode=0；可修改信号名或留空取消假设，其他未知条件保留待核对。',notes:'reg/logic 声明本身不是时序证据。边沿触发 always、具有可证明保持条件的简单 latch，以及 NPI 明确的时序 UDP 可提供依据。组合 always @* 中的 reg 继续追到真实存储。DFT 置零用于静态分析，不修改 RTL/KDB/仿真信号。行为级 E[10] D 方向表示它的写入端，并不存在新造的 E_D 层次。',sources:['docs/REGISTER_TRACE_VALIDATION.zh-CN.md','docs/GUI_USER_MANUAL.zh-CN.md']},
{title:'逐 bit 映射与位序保留',sub:'范围、拼接与重复位都按 elab 关系保存',layout:'table',values:[['例化端口','实际寄存器位','关系'],['choose_src[4]','top.D[2]','直接位映射'],['choose_src[5]','top.D[3]','直接位映射'],['choose_src[6]','top.D[4]','直接位映射'],['choose_src[7]','top.D[5]','直接位映射']],widths:[410,400,358],foot:'连续映射可表示为 D[5:2]；非连续位保留 SV 拼接，反相与组合依赖单独标记。',notes:'对应真实验证的非零 formal 范围 [7:4] 到 D[5:2]。不能只凭两个总线宽度相等就推断下标相同。升序、反序、重复、参数化位宽和静态 part-select 均要保持位序。多目的 load 全部保留。动态下标、复杂语句或黑盒缺少依据时显示未解析，不猜 bit。',sources:['docs/REGISTER_TRACE_VALIDATION.zh-CN.md','docs/GUI_USER_MANUAL.zh-CN.md']},
{title:'每个真实末端输入生成一段 SV',sub:'起始有 MUX 与起始无 MUX，分别套用用户模板',layout:'steps',items:[['01  确认架构与寄存器结果','基于已经提取的实际结构和逐位寄存器映射。'],['02  分析全部末端','生成单位是“真实末端实例 + 关注输入口 / bit”。'],['03  处理歧义分支','多个寄存器候选先跳过，展示全部候选供选择。'],['04  写入一个 SV 文件','就绪分支逐段写出，并保存全量 mapping JSON。']],foot:'同一网名连接两个真实末端，仍生成两段；显示引用和 MUX 多输入不增加段数。',notes:'SV 功能当前用于时钟树。用户负责模板语义；工具填充有证据的信号映射。一个分支最多一个 MUX、一个 DIV、三个有唯一顺序的门控，超限或顺序不确定时反馈问题。处理跳过项后完整重新生成文件，不追加重复片段。当前架构连接不完整时保守阻止生成。',sources:['docs/SV_TEMPLATES_VALIDATION.zh-CN.md','docs/GUI_USER_MANUAL.zh-CN.md']},
{title:'模板变量与绑定规则',sub:'按真实连接顺序绑定，保持信号全路径和 bit 信息',layout:'table',values:[['变量','来源 / 顺序','缺位或歧义'],['$var_pllsel / $var_div','对应辅助口的来源寄存器','已存在但未解析：跳过'],['$var_swdone','DIV 状态口的目的写入位','多个目的：选择后再生成'],['$var_gt0 / gt1 / gt2','沿分支从前到后的三个门控','不足三个：后续填 1\'b1'],['$var_pll0～3','MUX 配置输入顺序；无 MUX 取起始输入','不存在的槽位填 1\'b1'],['$var_clkip','真实末端当前输入的实际信号','总线逐 bit；未知不补值'],['$var_rst','最初器件所属层的复位配置','模板引用但未填写：跳过']],widths:[345,480,343],size:22,foot:'缺位默认值仅用于约定的 PLL / GATE 槽位，不替代未知或失败的追踪结果。',notes:'准确变量全称是 $var_gt0、$var_gt1、$var_gt2，以及 $var_pll0 到 $var_pll3。旧 $var_gt 兼容为 gt0。rst 不取末端层，也不固定取 L1；若起始器件属于 L2，取 L2 的配置。复位短名根据起始器件实际父层补全，保留静态 bit；不自动取反或验证有效电平。',sources:['docs/SV_TEMPLATES_VALIDATION.zh-CN.md','docs/SV_RESET_VALIDATION.zh-CN.md']},
{title:'真实映射下的模板替换',sub:'示例分支：MUX → GATE → DIV → SUBSYSTEM 输入',layout:'codepair',leftTitle:'用户模板片段',leftCode:'initial begin\n  $display("clock=%b", $var_clkip);\n  $display("sel=%h", $var_pllsel);\n  $display("gt=%b", $var_gt0);\n  $display("rst=%b", $var_rst);\nend',rightTitle:'对应替换结果',rightCode:'initial begin\n  $display("clock=%b", sv_top.cd);\n  $display("sel=%h", sv_top.D[5:2]);\n  $display("gt=%b", sv_top.C[7]);\n  $display("rst=%b", sv_top.rst_n);\nend',foot:'信号映射来自实际 KDB 验证；此页为缩短后的模板示例，模板逻辑由使用方编写。',notes:'原始 KDB 验证中 MUX 分支绑定 D[5:2]、D[9:6]、C[7]、E[10]，末端为 sv_top.cd。rst_n 是该次初始配置的 L1 值；后续 GUI 测试交换了 L1/L2 复位配置，生成值也随之变化。实际完整验收片段在测试平台上下文中经 VCS 编译。本页缩短片段用于讲清替换，不声称该精简版本单独经过编译。',sources:['docs/SV_TEMPLATES_VALIDATION.zh-CN.md','docs/SV_RESET_VALIDATION.zh-CN.md']},
{title:'使用演示',sub:'从设计配置到一份可核对的分支 SV',layout:'demo',dark:true,notes:'演示建议约 15 分钟。本 PPT 使用实际原生 Tk 截图逐步说明，不嵌入录屏。第一组 wb_top 样例展示子系统多输入和分支定位；寄存器/SV 使用 sv_top 专用样例，不能把它们伪装成同一个设计的连续运行。现场如无 EDA 可以打开历史结果演示浏览；真实提取和寄存器追踪需要目标设备的 Verdi 与许可。',sources:['docs/GUI_USER_MANUAL.zh-CN.md','docs/WORKBENCH_VALIDATION.zh-CN.md','docs/SV_RESET_VALIDATION.zh-CN.md']},
{title:'演示 01｜加载设计与规则',sub:'在已有 EDA 环境中启动；安装路径由 verdi 命令自动定位',layout:'image',image:'layer-reset-input.png',items:[['选择设计入口','导入已生成 KDB\n或填写 RTL 文件列表'],['配置逻辑层','填写直接父层、module\n选择类型、输入与唯一输出'],['填写复位和范围','每层可配置复位信号\n追踪根覆盖全部连线路径']],notes:'实际操作：1 在可运行 verdi 的终端启动 bash launch.sh。2 选择时钟工作区，导入配置或新建默认两级。3 各层填写父层路径，增加 module 规则和类型；MUX 可多个关注输入，SUBSYSTEM 不填输出。4 填写本层复位信号。5 检查端口追踪根，按资源设置内存和端口超时。预期结果：规则可保存，界面显示自动检测到的环境；不要求每次输入 Verdi 安装路径。截图来自复位变量验收的 sv_top 配置。',sources:['docs/GUI_USER_MANUAL.zh-CN.md','docs/SV_RESET_VALIDATION.zh-CN.md']},
{title:'演示 02｜筛选并确认子系统输入',sub:'实时显示选中端口名数量，确认后才进入主提取',layout:'image',image:'subsystem-input-preview.png',items:[['读取实际 input','列出适用例化与范围\n支持参数化位宽'],['正则筛选并勾选','例如 clk\n全选、清空或手动子集'],['核对数量后确认','同名跨实例只计一次\n一条总线按一个端口名计数']],notes:'实际操作：器件编辑中选择 SUBSYSTEM，填写 module 和父层，点击读取输入端口。输入正则 clk，点击筛选，勾选 clk_bus 与 aux_clk。截图共有 4 条实例端口记录，但已选 2 个端口名。确认后保存器件，再启动主提取。预期：两个实例各自按 [3:2]、[4:2] 等真实范围逐位处理。可现场取消 aux_clk 观察计数从 2 降至 1。',sources:['docs/GUI_USER_MANUAL.zh-CN.md','docs/VALIDATION.zh-CN.md']},
{title:'演示 03｜运行提取与检查完成状态',sub:'阶段、当前对象和最近断点始终可查',layout:'wideimage',image:'runtime-stages-complete.png',bottom:'准备 → 导入 → 查找 → 追踪 → 拓扑 → 索引 → 结果 → 显示',notes:'实际操作：点击开始提取全部时钟树。观察阶段条、当前 module/实例/端口与断点数，必要时展开任务日志。完成后核对实例、边、根、末端与独立网络数量；再确认状态标记与未解析数。预期不是仅进程退出为 0，而是完整结果和证据可打开。此页截图为阶段显示验收案例，与上一页 wb_top 数据规模不同；现场按当前样例实际计数核对。',sources:['docs/GUI_USER_MANUAL.zh-CN.md','docs/VALIDATION.zh-CN.md']},
{title:'演示 04｜沿确切输入跳转分支',sub:'双击器件打开独立画布；主画布和导航上下文保留',layout:'image',image:'navigation-exact-input.png',items:[['定位来源器件','搜索 wb_top.g1\n选择下游连接'],['选择具体输入','wb_top.s1.aux_clk\n保留 g1 对应引用'],['验证定位结果','蓝圈标出 aux_clk\n后退 / 前进保持同一输入']],notes:'实际操作：在 wb_top 图中定位 g1，点击下游，候选中选 s1 的 aux_clk。图中同一个 s1 存在 g0 与 g1 两个引用，本次应选 g1 引用而不是首先显示的 g0 引用。蓝圈和状态栏提供可见确认。再后退、前进，或展开总线，仍保持该输入。双击具体器件可打开独立分支；主视口不变。此页采用最新导航修复的实际截图。',sources:['docs/NAVIGATION_FIX_VALIDATION.zh-CN.md','docs/BRANCH_CANVAS_VALIDATION.zh-CN.md']},
{title:'演示 05｜定位并导出问题',sub:'全量问题表提供分类、搜索、分页与关联对象',layout:'image',image:'workbench-issues.png',items:[['按类别筛选','超时、跳过、缺口\n未解析逻辑、多驱动、环路'],['定位相关对象','器件与具体端口\n规则层或导入阶段日志'],['导出全部问题','CSV 包含全量记录\n问题数与涉及实例数分开统计']],notes:'实际操作：点击问题表/CSV，搜索一个实例或端口，选中记录查看原因和处理提示，点击定位。一条问题关联多个对象时展开关联列表。正常范围边界独立标记，不混入未解析错误。预期：记录覆盖全部页，而不止前 500 条；导出记录总量与全量查询一致。此页使用带负例的专用验收数据，不把问题截图说成当前良好样例失败。',sources:['docs/GUI_USER_MANUAL.zh-CN.md','docs/WORKBENCH_VALIDATION.zh-CN.md']},
{title:'演示 06｜辅助口追到寄存器',sub:'独立任务保存路径、映射关系、触发依据与 DFT 假设',layout:'image',image:'register-endpoints.png',items:[['检查任务参数','追踪根、DFT 置零名称\n内存与每端口超时'],['启动寄存器追踪','来源口向 Q 追踪\nsw_done 向写入位追踪'],['核对 E[10] 证据','查看触发沿和写入表达式\n导出全量 CSV / JSON']],notes:'切换到专用寄存器样例，不与 wb_top 混淆。实际操作：打开包含 aux_ports 的结构结果，点击辅助口→寄存器；确认默认 dft_mode=0 及追踪根；开始新任务。完成后搜索 E[10]，选行看 D 写入位证据，再定位原器件。目的列表分页不会丢弃其他目的。未固定条件、组合依赖或动态下标需保留问题，不能只看寄存器路径就认为可直接赋值。',sources:['docs/REGISTER_TRACE_VALIDATION.zh-CN.md','docs/GUI_USER_MANUAL.zh-CN.md']},
{title:'演示 07｜模板预览与单文件生成',sub:'多候选先反馈；确认后重新生成相关分支',layout:'wideimage',image:'sv-reset-preview.png',bottom:'两套模板 → 分析全部分支 → 核对变量 / 候选 → 全部分支写入一个 SV 文件',notes:'使用 sv_top 的实际时钟结果与寄存器结果目录。输入起始有 MUX/无 MUX 两套模板，点击分析全部分支。在预览核对 clkip、gt 顺序及起始层 rst。真实验收中第一次一个分支就绪，一个因 sw_done 到 E[11]、E[12] 而跳过；选择 E[11] 后重新导出得到两段。原有完整片段连同测试平台经 VCS 编译。反馈 mapping JSON 保留所有候选和跳过原因，再次导出完整替换而不是重复追加。',sources:['docs/SV_TEMPLATES_VALIDATION.zh-CN.md','docs/SV_RESET_VALIDATION.zh-CN.md']},
{title:'原生画布交互的实测结果',sub:'8,195 实例 / 8,194 条边，同一 VM、同一窗口与操作脚本',layout:'performance',notes:'数字来自 2026-11-07 画布验收。拖动 P95 从 2738.06 ms 到 25.05 ms，缩放 P95 从 2617.37 ms 到 26.40 ms；停止缩放后 147.70 ms 恢复细节。记录包含 Tk update_idletasks，不是显示器到眼睛延迟。两版本默认布局不同、可见节点位置也不同，因此不能把此比例当作所有商业图形的通用加速。GUI RSS 从 103.13 增至 128.54 MiB，初始布局从 0.790 到 2.961 秒；缓存/后台启动存在成本。',sources:['docs/WORKBENCH_VALIDATION.zh-CN.md','docs/WORKBENCH_VALIDATION.data.json']},
{title:'验收覆盖与证据口径',sub:'实际 EDA、保存结果回归与合成压力分别报告',layout:'table',values:[['检查层面','代表性覆盖','验收结论 / 边界'],['当前自动回归','260 passed / 1 skipped','导航修复后的完整测试'],['公开设计实际提取','PULP：62 实例 / 48 边\nOpenC910：6,984 / 6,983','各自配置关注网络\n不是辅助控制位全芯片覆盖'],['公开设计子系统','OpenTitan clkmgr：15 / 10','仅 clkmgr；整芯片旧工具编译受限'],['寄存器追踪 KDB','10,000 负载专用设计','10,032 条映射，完整 bit 集合核对'],['GUI / 索引合成压力','100,000 节点 / 99,999 边','概览、搜索、高亮与资源清理']],widths:[265,465,438],size:22,foot:'历史 EDA 记录与最新浏览回归不是同一轮测试；现场商业 KDB 仍需独立验收。',notes:'当前自动测试计数来自 507990b 导航修复。公开设计数据来自已记录的对应版本实际提取，不能说本轮重新提取了全部。最近分支回归还使用 8195 实例已有真实 NPI 结果；100k 是合成索引而不是100k详细器件同时显示。OpenTitan full top 编译失败已记录。用这些边界增加可复核性，不以没有证据的全芯片性能承诺作为销售话术。',sources:['docs/NAVIGATION_FIX_VALIDATION.zh-CN.md','docs/VALIDATION.zh-CN.md','docs/REGISTER_TRACE_VALIDATION.zh-CN.md']},
{title:'离线部署与环境自检',sub:'采用目标设备自己的 Verdi、许可与工程入口',layout:'table',values:[['用途','运行条件','建议检查'],['真实提取 / 寄存器追踪','Linux + Python 3.6+\nVerdi / NPI + 有效许可','doctor --self-test'],['GUI 编辑与结果浏览','Windows 或 Linux\nTkinter / ttk + 中文字体','doctor --viewer-only --gui'],['Linux 完整工作流','上述条件 + 有效图形会话','doctor --self-test --gui'],['便携交付','源码 / 离线包、启动器、手册\nSHA-256 清单与样例 golden','校验清单，跑时钟与复位样例']],widths:[290,480,398],foot:'实际兼容验收：Python 3.6.8、Tk 8.5.13、Verdi O-2018.09-SP2；其他版本按目标设备自检。',notes:'产品运行使用 Python 标准库和系统 Tk，不需要联网安装 Python GUI 包。Verdi 路径通过 PATH 的命令自动发现，沿用站点许可设置。公共只读安装时在用户状态目录保存记录，可自定义 runs。离线包不含 EDA、许可证、商业设计或第三方 KDB。安装了其他 EDA 工具不能自动替代当前 Verdi/NPI 后端。',sources:['docs/PORTABILITY.zh-CN.md','README.md']},
{title:'能力边界与结果判读',sub:'把已证明的连接、静态假设和待处理内容区分清楚',layout:'table',values:[['已提供的能力','需要单独完成的工作'],['配置范围内目标网络的静态结构恢复','物理 CTS、STA、毛刺与 CDC / RDC 签核'],['寄存器时序依据与静态 bit 映射','动态索引、复杂黑盒或未支持语句的人工处理'],['保存并标明 DFT 置零假设','工程模式约束的确认与功能仿真'],['依照用户模板替换真实信号','模板语义、复位极性及验证方案评审'],['内存保护、超时、恢复与阶段日志','商业 KDB 的导入资源与版本兼容验收']],widths:[584,584],foot:'“任务完成”与“全部连接已解析”分别判断；缺失、超时和歧义会明确显示。',notes:'这一页用于专业技术交流，避免把结构恢复包装成 sign-off。工具不推断 MUX 当前选择、不模拟门控/OCC 行为、不计算实际 DIV 比值。用户配置的 rst 不自动取反。RSS限制无法保证任意KDB在给定内存内导入。对未知边保持显式边界，可以让后续验证有依据。',sources:['README.md','docs/REGISTER_TRACE_VALIDATION.zh-CN.md','docs/SV_RESET_VALIDATION.zh-CN.md']},
{title:'项目试点的验收方案',sub:'用一个可人工核对的范围建立端到端基线',layout:'closing',dark:true,items:[['选定 CRG / 子系统范围','提供 elab、直接父层、module 与关注端口规则。'],['核对结构与控制位','比对实例、边、末端全集及关键寄存器 bit。'],['验证可恢复的工程运行','检查超时、资源阈值、日志、断点与设备环境。'],['审阅生成结果','确认两套模板、复位绑定、跳过项与完整单文件。']],notes:'结束页提供建议试点方法，不编造客户、定价、商业许可证或已签约状态。先从可人工核对的 CRG 子网开始，建立 golden 后再扩大范围。交付可包括配置、结构 JSON/SVG、问题 CSV、寄存器证据与 SV mapping。源码、操作手册与验收报告在工具仓库。可按听众需要回到架构原理、实图导航或模板演示页。',sources:['README.md','docs/GUI_USER_MANUAL.zh-CN.md','docs/VALIDATION.zh-CN.md']}
];

export async function build({presentation,input}){
 const made=[];
 for(let i=input.start;i<input.end;i++){
  const d=slides[i],s=base(presentation,i,d);
  if(d.layout==='cover'){
   text(s,'brand','ClockTree7',62,105,640,110,84,'#FFFFFF',true);
   text(s,'cover-title','RTL 时钟与复位\n架构分析工具',66,245,600,160,48,'#FFFFFF',true);
   text(s,'cover-sub','功能、技术原理与使用演示',68,456,600,58,28,'#B8D6E6');
   text(s,'cover-date','产品技术介绍  ·  2026.11',68,632,600,30,19,'#99AEC5');
   picture(s,'workbench-compact-focus.png',706,118,508,490,{left:.35,top:.45,right:.36,bottom:.08});
  }else if(d.layout==='image'){
   stack(s,d.items,58,204,370,132,23);
   picture(s,d.image,468,190,756,420,d.crop);
   if(d.foot)note(s,d.foot);
  }else if(d.layout==='wideimage'){
   picture(s,d.image,58,181,1166,446,d.crop);note(s,d.bottom,639);
  }else if(d.layout==='table'){
   table(s,d.values,58,187,1168,d.foot?424:450,d.widths,d.size||23);if(d.foot)note(s,d.foot,636);
  }else if(d.layout==='steps'){
   stack(s,d.items,85,193,1100,105,24);if(d.foot)note(s,d.foot,638);
  }else if(d.layout==='devices'){
   text(s,'clock','时钟树',58,193,220,48,29,C.teal,true);
   picture(s,'device-library.png',58,250,680,245);
   text(s,'reset','复位树',790,193,420,48,29,C.teal,true);
   picture(s,'reset-device-library.png',790,250,434,245);
   text(s,'subsystem','SUBSYSTEM',58,533,280,50,30,C.ink,true);
   text(s,'subsystem-copy','两类分析共用的输入终点；各输入独立归属网络，器件引用不重复计数。',344,533,864,84,26,C.ink);
  }else if(d.layout==='codepair'){
   text(s,'left-heading',d.leftTitle,58,200,565,45,27,C.teal,true);
   text(s,'right-heading',d.rightTitle,677,200,545,45,27,C.teal,true);
   const left=text(s,'left-code',d.leftCode,58,266,570,321,23,C.ink);left.text.style={typeface:'Consolas',fontSize:23};
   if(d.rightCode){const right=text(s,'right-code',d.rightCode,677,266,547,321,22,C.ink);right.text.style={typeface:'Consolas',fontSize:22};}
   else text(s,'right-body',d.rightBody,677,266,547,321,26,C.ink);
   note(s,d.foot,633);
  }else if(d.layout==='demo'){
   text(s,'demo-number','07',64,211,340,245,182,C.cyan,true);
   text(s,'demo-caption','个操作环节',69,467,350,60,32,'#FFFFFF');
   text(s,'demo-list','加载设计与规则\n确认子系统输入\n运行全分支提取\n精确导航与证据核对\n寄存器映射与 SV 导出',520,218,670,356,32,'#FFFFFF');
   note(s,'使用真实 Tk 截图逐步演示；wb_top 与 sv_top 为不同功能验收样例。',636,true);
  }else if(d.layout==='performance'){
   const chart=s.charts.add('bar',{position:{left:58,top:203,width:745,height:371},categories:['拖动更新 P95','缩放更新 P95'],
    series:[{name:'旧版',values:[2738.06,2617.37],fill:'#A7B5C7'},{name:'新版',values:[25.05,26.40],fill:C.teal}],
    hasLegend:true,barOptions:{direction:'bar',grouping:'clustered',gapWidth:120},
    xAxis:{textStyle:{fontSize:18,typeface:FONT}},yAxis:{textStyle:{fontSize:18,typeface:FONT},numberFormatCode:'0" ms"'},
    dataLabels:{showValue:true,position:'outEnd',textStyle:{fontSize:18,typeface:FONT}},legend:{position:'bottom',textStyle:{fontSize:18,typeface:FONT}},chartFill:'#FFFFFF'});
   text(s,'metric-1','25.05 ms',851,216,350,70,49,C.teal,true);text(s,'metric-1-label','拖动 P95',855,293,330,43,24,C.muted);
   text(s,'metric-2','26.40 ms',851,374,350,70,49,C.teal,true);text(s,'metric-2-label','缩放 P95',855,451,330,43,24,C.muted);
   text(s,'metric-3','147.70 ms 恢复细节',855,537,355,56,24,C.ink,true);
   note(s,'测量含 Tk 刷新；两版默认布局不同。GUI RSS 103.13 → 128.54 MiB，初始布局成本增加。',636);
  }else if(d.layout==='closing'){
   stack(s,d.items,65,198,1140,102,24,true);
   text(s,'link','github.com/ysyx-22040210-yudian/clocktree7',65,640,1130,28,22,C.cyan);
  }
  made.push({number:i+1,title:d.title,notes:!!s.speakerNotes.text});
 }
 return {created:made,total:presentation.slides.items.length};
}
