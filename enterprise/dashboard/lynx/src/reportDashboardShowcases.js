const clone = value => JSON.parse(JSON.stringify(value))

export const DASHBOARD_REFERENCE_MANIFEST = Object.freeze([
  ['TEMPLATE-01', 1440, 606], ['TEMPLATE-02', 1188, 666], ['TEMPLATE-03', 1302, 732],
  ['TEMPLATE-04', 1376, 774], ['TEMPLATE-05', 958, 538], ['TEMPLATE-06', 1146, 644],
  ['TEMPLATE-07', 906, 508], ['TEMPLATE-08', 1188, 665], ['TEMPLATE-09', 1110, 624],
  ['TEMPLATE-10', 848, 480], ['TEMPLATE-11', 1085, 609], ['TEMPLATE-12', 994, 558],
  ['TEMPLATE-13', 1148, 644], ['TEMPLATE-14', 652, 368], ['TEMPLATE-15', 996, 558],
  ['TEMPLATE-16', 1264, 710], ['TEMPLATE-17', 917, 516], ['TEMPLATE-18', 1082, 594],
  ['TEMPLATE-19', 767, 432], ['TEMPLATE-20', 929, 523]
].map(([code, width, height], index) => Object.freeze({
  code,
  width,
  height,
  aspectRatio: Number((width / height).toFixed(6)),
  preview: `jimu-screen/templates/template-${String(index + 1).padStart(2, '0')}.jpg`
})))

const R = (kind, title, x, y, w, h, role = kind, options = {}) => ({ kind, title, x, y, w, h, role, ...options })
const L = (...items) => items
const C = (...items) => items

function theme(id, background, panel, primary, secondary, accent, text = '#EAF8FF', muted = '#7EA8C4') {
  return Object.freeze({
    id, background, backgroundRaised: panel, panel: `${panel}E8`, panelStrong: `${panel}F5`,
    primary, secondary, accent, positive: '#35E4A4', danger: '#FF627D', grid: `${primary}55`,
    text, muted, surfaceLift: secondary, surfaceRadius: 0,
    palette: [primary, secondary, accent, '#35E4A4', '#A879FF', '#FF627D', '#58D7FF', '#6B88FF']
  })
}

const THEMES = Object.freeze({
  t01: theme('teal-territory', '#07121B', '#0B2431', '#39DDE8', '#2A87B8', '#F5A94C'),
  t02: theme('indigo-production', '#111333', '#202554', '#6D76FF', '#3A43A6', '#4FE6FF'),
  t03: theme('blue-quality', '#031333', '#092759', '#1F80FF', '#245CD7', '#26D5E8'),
  t04: theme('cyan-warehouse', '#03272B', '#063C41', '#15D5D5', '#1F8C93', '#65EDE4'),
  t05: theme('violet-vehicle', '#151443', '#23215D', '#675DFF', '#2B8DFF', '#FFD84D'),
  t06: theme('teal-sales', '#061D29', '#0A3340', '#1BD8D0', '#218FB0', '#FFB94C'),
  t07: theme('blue-trade-map', '#061735', '#0C2B59', '#277BFF', '#1BBDE6', '#FFB744'),
  t08: theme('midnight-region', '#041323', '#09283E', '#16CFE8', '#2276A5', '#FFD05B'),
  t09: theme('violet-finance', '#101433', '#202447', '#7569E8', '#3C8CFF', '#F0C84E'),
  t10: theme('orbital-blue', '#03142A', '#082D55', '#13BFF4', '#326EFF', '#6EF0FF'),
  t11: theme('magenta-agri', '#241035', '#34154B', '#C865FF', '#7354D7', '#F88D4C'),
  t12: theme('purple-order', '#17163E', '#2A265B', '#8C6EFF', '#5269F0', '#FFCB4A'),
  t13: theme('cyan-group', '#061A24', '#0A313B', '#19DBD0', '#1D8092', '#FFD35A'),
  t14: theme('electric-platform', '#041436', '#09245A', '#2377FF', '#10CDEA', '#F25A44'),
  t15: theme('azure-analytics', '#041A35', '#0A315A', '#22B7F2', '#2D79D8', '#5FE7D0'),
  t16: theme('black-gold', '#0C0B0A', '#201A14', '#D5A451', '#84602F', '#F4CE79', '#F4E6CF', '#A89170'),
  t17: theme('light-finance', '#EAF7FA', '#FFFFFF', '#4DC8D0', '#77A4DB', '#F2C04C', '#16344B', '#6F8798'),
  t18: theme('navy-orange', '#071A33', '#10294B', '#1CB7D3', '#32669D', '#FF7048'),
  t19: theme('equipment-blue', '#031531', '#092A59', '#277DFF', '#23BED7', '#FFB647'),
  t20: theme('factory-cyan', '#04242B', '#083B43', '#21D6D1', '#208C98', '#F4CE4E')
})

export const DASHBOARD_SHOWCASE_SPECS = Object.freeze([
  {
    id: 'showcase-01', templateId: 'territory-industry-map', code: 'TEMPLATE-01', name: '销售行业区域洞察', title: '销售行业数据可视化模板', shortName: '区域行业地图', preview: 'jimu-screen/templates/template-01.jpg', theme: THEMES.t01,
    coordinateSpace: 'native',
    header: {
      title: { x: 28, y: 5, w: 420, h: 42, fontSize: 28, textAlign: 'left', letterSpacing: 1 },
      time: { x: 520, y: 7, w: 310, h: 34 }
    },
    sectionBands: [
      { title: '销售数据总揽', subtitle: 'Sales Data Overview', x: 28, y: 74, w: 548, h: 28 },
      { title: '应收款项目', subtitle: 'Accounts receivable items', x: 28, y: 201, w: 248, h: 29 },
      { title: '月度排名', subtitle: 'Monthly ranking', x: 300, y: 201, w: 276, h: 29 },
      { title: '区域完成情况', subtitle: 'Regional completion status', x: 28, y: 410, w: 548, h: 29 },
      { title: '订单流向情况', subtitle: 'Order flow situation', x: 594, y: 74, w: 816, h: 29 }
    ],
    summary: '宽幅深色区域地图居中，左侧信息卡与排行、底部趋势、右侧双环形指标围绕地图形成态势总览。',
    layout: L('顶部细窄状态栏与右侧时间，主标题靠左嵌入横向导轨', '中央约60%宽度为省域地图与点位焦点，左右侧栏保持悬浮卡片', '左侧上下信息卡、底部多序列趋势与右侧双环形指标形成非对称包围'),
    components: C('区域地图与点位标注', '行业排行横条', '多序列趋势柱线', '双环形进度', '信息摘要列表'),
    constraints: L('地图必须是最大视觉焦点且不被业务卡片遮挡', '左右卡片使用低对比透明面板，避免整齐九宫格', '只替换地图维度、指标和数据，不改变中央留白比例'),
    regions: [
      R('metric','当月签约额',28,108,168,78,'metric',{ frame: false, metricValue: 1323.12, unit: '万元' }),
      R('metric','当月回款额',208,108,168,78,'metric',{ frame: false, metricValue: 678.13, unit: '万元' }),
      R('metric','当月尾款',388,108,168,78,'metric',{ frame: false, metricValue: 645.45, unit: '万元' }),
      R('area','应收款项目',28,232,248,164,'area',{ frame: false, hideTitle: true }),
      R('ranking','月度排名',300,232,276,164,'business-ranking',{ frame: false, hideTitle: true }),
      R('bar','区域完成情况',28,441,548,150,'bar',{ frame: false, hideTitle: true, component: 'JMultipleBar' }),
      R('map','全国区域分布',594,104,816,486,'hero-map',{ frame: false, hideTitle: true }),
      R('mapOperations','运营中心',620,438,212,142,'map-operations',{ frame: false, noInset: true }),
      R('pie','结构完成度',1290,260,120,120,'pie',{ frame: false, progress: 78, hideTitle: true }),
      R('pie','增长达成率',1290,420,120,120,'pie',{ frame: false, progress: 57, hideTitle: true }),
      R('weather','天气状态',1090,8,320,38,'weather-strip',{ frame: false, noInset: true })
    ]
  },
  {
    id: 'showcase-02', templateId: 'production-live-control', code: 'TEMPLATE-02', name: '生产实时控制总览', title: '生产实时分析看板', shortName: '生产实时控制', preview: 'jimu-screen/templates/template-02.jpg', theme: THEMES.t02,
    summary: '靛蓝生产驾驶舱以中央超大仪表为核心，左右垂直分析栏和底部六块小组件组成强对称布局。',
    layout: L('顶部居中标题，两侧状态与日期对称分布', '中央上半区为超大圆形完成率仪表，左右各两块窄面板', '底部横向排列六个等宽组件，形成稳定生产监控栅格'),
    components: C('大型进度仪表', '横向产线排行', '趋势折线', '状态环图', '指标卡组', '异常列表'),
    constraints: L('中央仪表占据首屏视觉中心且数值字号最大', '左右面板严格对称，底部组件等高对齐', '靛蓝底色上仅使用蓝紫和青色高亮'),
    regions: [R('pie','设备开动结构',28,112,410,250),R('area','实时产量趋势',28,380,410,250),R('gauge','生产完成率',470,112,980,500,'hero-gauge'),R('hbar','车间计划进度',1482,112,410,250),R('table','工单状态',1482,380,410,250),R('metric','计划产量',28,670,280,330),R('bar','产线产量',325,670,280,330),R('area','工时波动',622,670,280,330),R('line','节拍趋势',919,670,280,330),R('hbar','质量达成',1216,670,280,330),R('metric','异常工单',1513,670,379,330)]
  },
  {
    id: 'showcase-03', templateId: 'quality-corporate-panel', code: 'TEMPLATE-03', name: '企业质量经营驾驶舱', title: '公司质量管理大屏', shortName: '企业质量经营', preview: 'jimu-screen/templates/template-03.jpg', theme: THEMES.t03,
    summary: '标准蓝色三列驾驶舱，左列趋势与柱图、中央地图及指标、右列结构与排行形成均衡管理视图。',
    layout: L('顶部标题带贯穿全宽并保留右上角时间', '主体采用25%/50%/25%三列，中央地图位于上中区', '底部三块趋势组件等高收口，右侧结构分析纵向排列'),
    components: C('柱状趋势', '面积折线', '中心区域地图', '双值指标卡', '结构环图', '排行条形'),
    constraints: L('三列边界清晰且中央列宽度为两侧约两倍', '地图、趋势、结构和排行四种表达必须同时出现', '使用高亮蓝和青色，禁止紫色成为主色'),
    regions: [R('bar','质量批次趋势',28,112,430,270),R('area','问题变化趋势',28,400,430,270),R('bar','车间质量对比',28,690,430,310),R('map','区域质量分布',480,112,920,410,'hero-map'),R('metric','累计检验批次',480,540,445,170),R('metric','一次通过数量',955,540,445,170),R('line','过程稳定趋势',480,730,920,270),R('pie','质量结构占比',1430,112,462,270),R('hbar','缺陷类型排行',1430,400,462,270),R('line','整改闭环趋势',1430,690,462,310)]
  },
  {
    id: 'showcase-04', templateId: 'smart-warehouse-grid', code: 'TEMPLATE-04', name: '智慧仓储运营看板', title: '智慧仓储可视化数据看板', shortName: '智慧仓储', preview: 'jimu-screen/templates/template-04.jpg', theme: THEMES.t04,
    summary: '青绿色仓储看板采用细线框网格，中央大结构饼图与右侧三层仓储指标构成高密度运营总览。',
    layout: L('顶部标题带下方排列四张摘要卡', '左侧上下两表一图，中央以大型结构饼图为核心', '右侧三层指标与横条，底部跨栏趋势补充吞吐变化'),
    components: C('仓储摘要卡', '大型结构饼图', '库存横向条形', '吞吐柱图', '库位明细表', '告警趋势'),
    constraints: L('青绿细线框必须贯穿所有面板边界', '中央饼图面积至少为普通图表的两倍', '高密度信息仍需保留12像素以上组件间距'),
    regions: [R('table','仓库运行状态',28,110,430,285),R('bar','入出库吞吐',28,415,430,270),R('line','库存变化',28,705,430,295),R('metric','库存总量',480,110,430,130),R('metric','可用库位',930,110,430,130),R('pie','库存结构分析',480,260,880,500,'hero-pie'),R('bar','作业效率趋势',480,780,880,220),R('metric','今日入库',1382,110,510,170),R('hbar','库区占用率',1382,300,510,250),R('metric','今日出库',1382,570,510,170),R('bar','异常告警',1382,760,510,240)]
  },
  {
    id: 'showcase-05', templateId: 'vehicle-service-matrix', code: 'TEMPLATE-05', name: '车辆业务监控矩阵', title: '车辆业务数据监控平台', shortName: '车辆业务矩阵', preview: 'jimu-screen/templates/template-05.jpg', theme: THEMES.t05,
    summary: '紫蓝高密度九宫格，以中央同心圆分析和多组彩色柱图构成车辆业务全景监控。',
    layout: L('顶部标题下方布置一排五个窄指标', '主体三列，每列纵向三层；中央中层为同心圆焦点', '底部趋势与结构图保持相同高度形成密集矩阵'),
    components: C('窄型指标卡组', '多序列柱图', '同心圆进度', '横向排行', '面积趋势', '状态环图'),
    constraints: L('整体必须呈现紧凑九宫格而非大面积留白', '中央同心圆是唯一圆形主焦点', '紫色为主、蓝色为辅，黄色只用于警示和关键柱'),
    regions: [R('bar','业务受理趋势',28,112,450,250),R('table','车辆业务明细',28,382,450,300),R('area','月度服务变化',28,702,450,298),R('metric','在册车辆',500,112,270,140),R('metric','今日办理',790,112,270,140),R('metric','办结率',1080,112,270,140),R('pie','业务结构总览',500,272,850,410,'hero-rings'),R('bar','车型业务分布',500,702,850,298),R('line','服务时效',1372,112,520,250),R('hbar','网点办理排行',1372,382,520,300),R('pie','状态占比',1372,702,520,298)]
  },
  {
    id: 'showcase-06', templateId: 'enterprise-sales-target', code: 'TEMPLATE-06', name: '企业销售目标看板', title: '企业销售目标看板', shortName: '销售目标', preview: 'jimu-screen/templates/template-06.jpg', theme: THEMES.t06,
    summary: '深青销售看板以顶部四指标、左侧目标树与右侧两列三层图表组成清晰的经营节奏。',
    layout: L('顶部横排四个盾形或卡片式关键指标', '左侧约35%宽度用于目标概览、排行榜和明细', '右侧2×3分析矩阵覆盖趋势、对比、结构与排行'),
    components: C('目标指标卡', '销售趋势折线', '团队对比柱图', '客户排行', '订单明细表', '结构环图'),
    constraints: L('顶部指标必须形成连续一排且等宽', '右侧六块图表严格按两列三层对齐', '青色主调中使用橙色区分未达成状态'),
    regions: [R('metric','年度销售目标',28,104,430,145),R('table','销售组织概览',28,270,430,300),R('table','重点订单明细',28,590,430,410),R('metric','累计销售额',480,104,335,145),R('metric','目标达成率',835,104,335,145),R('metric','新增客户数',1190,104,335,145),R('metric','回款完成率',1545,104,347,145),R('line','销售额趋势',480,270,690,220),R('bar','团队目标对比',1190,270,702,220),R('area','订单变化趋势',480,510,690,220),R('hbar','客户贡献排行',1190,510,702,220),R('pie','产品结构',480,750,690,250),R('bar','区域销售对比',1190,750,702,250)]
  },
  {
    id: 'showcase-07', templateId: 'commodity-trade-map', code: 'TEMPLATE-07', name: '商品交易区域分析', title: '商品交易所数据大屏', shortName: '商品交易地图', preview: 'jimu-screen/templates/template-07.jpg', theme: THEMES.t07,
    summary: '亮蓝交易大屏以中央中国地图为视觉中心，左右指标与表格环绕，底部趋势组件横向铺开。',
    layout: L('顶部标题与天气时间形成完整导航带', '中央地图占据中上部最大区域，左右各有两层交易摘要', '底部四块等宽组件展示结构、趋势、排行与价格变化'),
    components: C('中国区域地图', '交易额指标卡', '价格趋势', '品类结构环图', '交易排行表', '成交量柱图'),
    constraints: L('地图位于几何中心并保持大于850像素宽', '左右信息密度对称，底部四图等高', '高亮蓝为主色，橙黄只强调价格和成交额'),
    regions: [R('bar','交易品类走势',28,112,410,245),R('pie','成交结构',28,377,410,245),R('table','重点商品',28,642,410,358),R('map','全国交易热度',460,112,1000,570,'hero-map'),R('line','价格指数趋势',460,702,480,298),R('bar','成交量变化',960,702,500,298),R('table','区域交易排行',1482,112,410,245),R('hbar','市场活跃度',1482,377,410,245),R('pie','买卖结构',1482,642,410,358)]
  },
  {
    id: 'showcase-08', templateId: 'regional-operation-map', code: 'TEMPLATE-08', name: '区域运营销售总览', title: '区域运营销售大屏', shortName: '区域运营', preview: 'jimu-screen/templates/template-08.jpg', theme: THEMES.t08,
    summary: '午夜蓝区域看板以省域地图居中，左右细长排行与底部环形指标构成低亮度态势分析。',
    layout: L('顶部标题条较窄，右上角保留日期时间', '中央省域地图约占主体宽度一半，左右为窄列表和指标', '底部左侧三环形状态、中央明细表、右侧趋势图平衡收口'),
    components: C('省域地图', '渠道排行条形', '三环状态指标', '区域明细表', '趋势折线', '经营摘要卡'),
    constraints: L('地图保持暗色线稿并用青色点位高亮', '左右列表采用紧凑行高且不可压缩地图', '底部环形指标仅使用三枚并保持等距'),
    regions: [R('table','区域经营概览',28,112,390,260),R('hbar','渠道贡献排行',28,392,390,310),R('pie','经营健康度',28,722,390,278),R('map','省域运营分布',440,112,1010,590,'hero-map'),R('table','区域订单明细',440,722,1010,278),R('metric','累计交易额',1472,112,420,180),R('hbar','城市销售排行',1472,312,420,260),R('line','销售趋势',1472,592,420,220),R('pie','客户结构',1472,832,420,168)]
  },
  {
    id: 'showcase-09', templateId: 'enterprise-sales-annual', code: 'TEMPLATE-09', name: '企业销售年度大屏', title: '企业销售大屏', shortName: '年度销售', preview: 'jimu-screen/templates/template-09.jpg', theme: THEMES.t09,
    summary: '深紫年度销售大屏以中央多年柱状对比为主，左右指标与仪表分布，底部订单分析和结构环收尾。',
    layout: L('顶部标题下方左右各三张核心指标卡', '中央为横跨半屏的年度销售柱状主图', '底部左侧趋势、中央年度总量环、右侧多维对比形成三段式收口'),
    components: C('年度销售柱图', '核心指标卡', '进度仪表', '销售趋势', '总量环图', '订单结构'),
    constraints: L('年度柱图必须居中且柱体具有强烈纵向层次', '左右指标数量和纵向位置对称', '主色保持深紫蓝，黄色只标注目标或峰值'),
    regions: [R('metric','累计销售额',28,112,380,170),R('metric','订单总量',28,302,380,170),R('pie','目标达成率',28,492,380,230),R('line','客户增长趋势',28,742,380,258),R('bar','年度销售对比',430,112,1060,610,'hero-bars'),R('pie','年度销售总量',760,742,400,258),R('metric','平均客单价',1512,112,380,170),R('metric','回款金额',1512,302,380,170),R('pie','回款完成率',1512,492,380,230),R('hbar','重点客户排行',1512,742,380,258)]
  },
  {
    id: 'showcase-10', templateId: 'orbital-industry-platform', code: 'TEMPLATE-10', name: '通用行业综合分析平台', title: '通用行业综合数据分析平台', shortName: '环球综合分析', preview: 'jimu-screen/templates/template-10.jpg', theme: THEMES.t10,
    summary: '蓝色环球态势大屏以中央数字地球和超大核心数值为焦点，左右两列分析面板及底部指标环绕。',
    layout: L('顶部标题下方中央显示超大核心数值', '中央数字地球占据近半画布，左右各三层数据面板', '底部中央横排三枚进度环并与左右列表对齐'),
    components: C('数字地球主视觉', '核心数字翻牌', '趋势面积图', '横向排行', '进度环组', '业务明细表'),
    constraints: L('中央地球是唯一主视觉且背后保留径向光晕', '超大数值必须位于地球上方并居中', '左右栏宽度一致，青蓝渐变不得被暖色替代'),
    regions: [R('bar','业务规模趋势',28,112,420,250),R('hbar','重点对象排行',28,382,420,250),R('table','运营明细',28,652,420,348),R('metric','综合业务规模',470,112,980,145,'hero-number'),R('hero','全球业务态势',520,277,880,470,'hero-orb'),R('pie','运行效率',520,767,260,233),R('pie','服务达成',830,767,260,233),R('pie','风险控制',1140,767,260,233),R('line','实时变化趋势',1472,112,420,250),R('area','周期对比',1472,382,420,250),R('table','异常事件',1472,652,420,348)]
  },
  {
    id: 'showcase-11', templateId: 'agricultural-service-purple', code: 'TEMPLATE-11', name: '农机服务数据分析', title: '农机服务可视化大屏', shortName: '农机服务', preview: 'jimu-screen/templates/template-11.jpg', theme: THEMES.t11,
    summary: '紫红农业分析大屏以中央统计数字和上下趋势为轴，左侧结构环图、右侧三张折线卡形成错落布局。',
    layout: L('顶部标题下方中央排列六个统计数字', '左侧上方指标列表、下方超大结构环图', '中央上下两块宽趋势，右侧三块窄折线纵向堆叠'),
    components: C('统计指标带', '大型结构环图', '宽幅趋势折线', '三联小趋势', '服务排行表', '面积分析'),
    constraints: L('紫红渐变是主视觉语言，橙色只用于结构环', '中部统计数字必须形成一条水平基线', '右侧三图等宽等高且与中央趋势错位'),
    regions: [R('table','服务资源概览',28,112,390,300),R('pie','农机服务结构',28,432,390,568,'hero-pie'),R('metric','服务总量',440,112,220,145),R('metric','覆盖区域',680,112,220,145),R('metric','在线设备',920,112,220,145),R('metric','服务完成率',1160,112,220,145),R('line','服务量趋势',440,277,940,330),R('area','区域作业变化',440,627,940,373),R('line','设备在线趋势',1402,112,490,280),R('line','作业效率趋势',1402,412,490,280),R('line','服务响应趋势',1402,712,490,288)]
  },
  {
    id: 'showcase-12', templateId: 'order-management-purple', code: 'TEMPLATE-12', name: '订单管理运营看板', title: '订单管理可视化看板', shortName: '订单管理', preview: 'jimu-screen/templates/template-12.jpg', theme: THEMES.t12,
    summary: '紫蓝订单看板采用顶部指标、中央双环与全宽多图矩阵，突出订单趋势、状态与履约。',
    layout: L('顶部四张指标卡与两枚环形进度同列', '中层由趋势折线、结构柱图和状态环图并排', '底部使用两块宽图和一块窄图形成订单节奏收口'),
    components: C('订单指标卡', '双环进度', '订单趋势折线', '状态柱图', '来源结构环', '履约面积图'),
    constraints: L('顶部指标与环形进度保持同一高度', '中层和底层图表边界严格对齐', '紫色高亮为主，黄色仅标注转折点和目标线'),
    regions: [R('metric','订单总量',28,112,275,170),R('metric','成交金额',323,112,275,170),R('pie','完成率',618,112,275,170),R('pie','交付率',913,112,275,170),R('metric','新增客户',1208,112,330,170),R('metric','异常订单',1558,112,334,170),R('bar','月度订单量',28,302,600,310),R('line','订单金额趋势',648,302,620,310),R('pie','订单来源结构',1288,302,604,310),R('area','履约趋势',28,632,920,368),R('bar','区域订单对比',968,632,620,368),R('pie','订单状态',1608,632,284,368)]
  },
  {
    id: 'showcase-13', templateId: 'group-integrated-platform', code: 'TEMPLATE-13', name: '集团综合运营一体化', title: '集团综合运营一体化平台', shortName: '集团综合运营', preview: 'jimu-screen/templates/template-13.jpg', theme: THEMES.t13,
    summary: '深青集团运营大屏以中央大型环形关系盘为核心，左右纵向图表和底部明细表构成一体化态势平台。',
    layout: L('顶部窄标题带，两侧留出状态信息', '中央大型环形进度与关系节点占据主体核心', '左右分别堆叠排行和趋势，底部中央明细表横向展开'),
    components: C('大型关系环盘', '目标完成率', '部门排行', '运营趋势', '结构环图', '综合明细表'),
    constraints: L('中央关系环直径不小于560像素', '左右图表向中央形成视觉引导但不得覆盖环盘', '青绿色为主，暖色只表达风险节点'),
    regions: [R('metric','集团综合指数',28,112,390,160),R('hbar','组织贡献排行',28,292,390,260),R('line','运营趋势',28,572,390,220),R('pie','业务结构',28,812,390,188),R('hero','集团运营关系',440,112,1040,610,'hero-orb'),R('table','集团经营明细',440,742,1040,258),R('metric','年度目标',1502,112,390,160),R('line','资金趋势',1502,292,390,260),R('pie','区域结构',1502,572,390,220),R('hbar','风险事项',1502,812,390,188)]
  },
  {
    id: 'showcase-14', templateId: 'digital-platform-stream', code: 'TEMPLATE-14', name: '数字平台实时透视', title: '数字平台数据透视', shortName: '数字平台透视', preview: 'jimu-screen/templates/template-14.jpg', theme: THEMES.t14,
    summary: '电光蓝平台大屏以中央长数字翻牌和底部折线柱图为主，左右窄栏显示用户、地域与实时事件。',
    layout: L('顶部标题下方中央放置超长数字翻牌', '左侧用户画像和列表、右侧区域排行和事件流形成双侧栏', '中央下半区先排四指标，再放置横跨大区的柱线组合图'),
    components: C('长数字翻牌', '用户画像卡', '四指标摘要', '柱线组合趋势', '地域排行', '实时事件流'),
    constraints: L('数字翻牌必须居中且具有最大的字符间距', '左右侧栏保持窄而长，不改成普通等宽三列', '橙红色仅用于主趋势线和实时告警'),
    regions: [R('table','用户画像',28,112,360,300),R('hbar','访问来源',28,432,360,250),R('table','实时访客',28,702,360,298),R('metric','平台实时访问量',410,112,1100,180,'hero-number'),R('metric','今日新增',410,312,255,150),R('metric','活跃用户',685,312,255,150),R('metric','访问次数',960,312,255,150),R('metric','转化数量',1235,312,275,150),R('bar','实时访问与转化趋势',410,482,1100,518),R('hbar','地域访问排行',1532,112,360,300),R('table','实时事件流',1532,432,360,568)]
  },
  {
    id: 'showcase-15', templateId: 'bigdata-analytics-grid', code: 'TEMPLATE-15', name: '大数据综合分析矩阵', title: '智慧数据可视化大屏', shortName: '大数据分析', preview: 'jimu-screen/templates/template-15.jpg', theme: THEMES.t15,
    summary: '蔚蓝分析看板采用严格三列多层网格，中央趋势柱图更大，两侧结构、排行和状态组件高密度排布。',
    layout: L('顶部标题带下方直接进入三列网格', '中央列上方双指标、中部主趋势、下部宽面积图', '左右列各三层，结构、排行、趋势与表格交替'),
    components: C('双指标摘要', '主趋势柱线', '结构环图', '排行横条', '面积趋势', '状态列表'),
    constraints: L('三列网格必须严格对齐但中央列更宽', '每列至少包含三种不同图表角色', '蓝青色层次清晰，避免高饱和紫色'),
    regions: [R('metric','运行总量',28,112,300,160),R('metric','增长速度',348,112,300,160),R('bar','业务规模',28,292,620,320),R('table','业务明细',28,632,620,368),R('line','核心趋势',670,112,580,300),R('bar','周期对比',670,432,580,260),R('area','综合变化',670,712,580,288),R('pie','结构占比',1272,112,300,280),R('hbar','对象排行',1592,112,300,280),R('hbar','进度状态',1272,412,620,250),R('line','变化趋势',1272,682,620,318)]
  },
  {
    id: 'showcase-16', templateId: 'annual-black-gold', code: 'TEMPLATE-16', name: '企业年度黑金看板', title: '企业年度数据看板', shortName: '年度黑金', preview: 'jimu-screen/templates/template-16.jpg', theme: THEMES.t16,
    summary: '黑金年度经营看板以顶部四指标和左中柱状主图为重心，右侧地图与排行、底部环形指标呈现稳重质感。',
    layout: L('顶部横排四张黑金指标卡', '中部左侧大柱图、右侧地图与说明栏形成7:3分区', '底部左侧三环状态、中央总量环、右侧排行纵向排列'),
    components: C('黑金指标卡', '年度柱状主图', '区域地图', '圆环总量', '三联进度环', '横向排行'),
    constraints: L('背景接近纯黑，金色只用于数据和边界高光', '主柱图占据中部最大面积，地图保持克制', '禁止使用青蓝霓虹替代黑金视觉'),
    regions: [R('metric','年度收入',28,112,440,160),R('metric','订单总量',488,112,440,160),R('metric','客户总数',948,112,440,160),R('metric','平均客单价',1408,112,484,160),R('bar','年度经营趋势',28,292,1120,420,'hero-bars'),R('map','区域经营分布',1170,292,722,420),R('pie','目标完成率',28,732,330,268),R('pie','回款完成率',378,732,330,268),R('pie','客户活跃率',728,732,330,268),R('metric','年度综合值',1080,732,360,268),R('hbar','重点区域排行',1460,732,432,268)]
  },
  {
    id: 'showcase-17', templateId: 'finance-light-dashboard', code: 'TEMPLATE-17', name: '财务分析明亮看板', title: '财务分析综合看板', shortName: '明亮财务', preview: 'jimu-screen/templates/template-17.jpg', theme: THEMES.t17,
    summary: '唯一浅色模板，以六张彩色财务指标卡和白色圆角分析卡构成清爽的财务经营报告。',
    layout: L('顶部浅色标题区下方横排六张彩色指标卡', '中部两块宽趋势图左右并排', '底部左侧流程/结构、中部柱图、右侧横向排行形成三段布局'),
    components: C('彩色财务指标卡', '收入利润趋势', '费用结构', '经营流程', '期间对比柱图', '科目排行'),
    constraints: L('保持浅灰蓝背景与白色圆角卡片，不得转为深色大屏', '指标卡允许多种浅色强调但正文保持深蓝灰', '图表网格线和阴影必须轻量'),
    regions: [R('metric','营业收入',28,112,290,150),R('metric','营业成本',338,112,290,150),R('metric','营业利润',648,112,290,150),R('metric','净利润',958,112,290,150),R('metric','现金余额',1268,112,290,150),R('metric','应收余额',1578,112,314,150),R('line','收入利润趋势',28,282,920,310),R('area','现金流趋势',968,282,924,310),R('pie','费用结构',28,612,500,388),R('bar','期间经营对比',548,612,760,388),R('hbar','重点科目排行',1328,612,564,388)]
  },
  {
    id: 'showcase-18', templateId: 'workshop-production-orange', code: 'TEMPLATE-18', name: '车间生产数据总览', title: '车间生产数据综合看板', shortName: '车间生产', preview: 'jimu-screen/templates/template-18.jpg', theme: THEMES.t18,
    summary: '深蓝车间看板以顶部三指标、中央宽趋势和底部三仪表为主，橙色横条突出设备与物料状态。',
    layout: L('顶部三张指标卡与一块状态区横向排列', '中部左侧环形结构、右侧宽幅生产趋势', '底部三枚仪表与右侧两块橙色横向排行组成生产闭环'),
    components: C('生产指标卡', '宽幅生产趋势', '三联仪表', '橙色横向排行', '状态结构环', '工单明细'),
    constraints: L('蓝色背景中橙色只承担设备和物料告警角色', '底部三仪表大小一致并保持水平对齐', '中部趋势图宽度至少为左侧结构图两倍'),
    regions: [R('metric','计划产量',28,112,420,160),R('metric','实际产量',468,112,420,160),R('metric','完成率',908,112,420,160),R('table','当班状态',1348,112,544,160),R('pie','产品结构',28,292,520,330),R('line','生产变化趋势',568,292,1324,330,'hero-trend'),R('gauge','设备开动率',28,642,330,358),R('gauge','计划达成率',378,642,330,358),R('gauge','质量合格率',728,642,330,358),R('hbar','设备运行排行',1078,642,814,169),R('hbar','物料消耗排行',1078,831,814,169)]
  },
  {
    id: 'showcase-19', templateId: 'equipment-digital-ops', code: 'TEMPLATE-19', name: '设备数字运维大屏', title: '设备数字运维大屏', shortName: '设备运维', preview: 'jimu-screen/templates/template-19.jpg', theme: THEMES.t19,
    summary: '深蓝设备运维大屏以中央超大设备总量和折线趋势为核心，左右状态、排行、明细与底部统计环绕。',
    layout: L('顶部标题下方中央显示超大设备总量，两侧各三张小状态卡', '中部中央为宽幅运行趋势，左右各一列状态与排行', '底部中央明细表，右下结构环与左下告警趋势补充'),
    components: C('超大设备总量', '设备状态卡组', '运行趋势折线', '故障排行', '运维明细表', '状态结构环'),
    constraints: L('核心总量位于顶部中央且宽度大于两侧状态组', '中部折线是主要数据图，不能被多个小图切碎', '蓝色分层面板使用细边框，黄色只用于故障预警'),
    regions: [R('metric','在线设备',28,112,300,145),R('metric','运行设备',348,112,300,145),R('table','设备状态',28,277,620,280),R('line','告警变化',28,577,620,423),R('metric','设备总量',670,112,580,145,'hero-number'),R('line','设备运行趋势',670,277,580,390,'hero-trend'),R('table','运维工单明细',670,687,580,313),R('metric','停机设备',1272,112,300,145),R('metric','故障设备',1592,112,300,145),R('hbar','故障设备排行',1272,277,620,280),R('pie','设备状态结构',1272,577,620,423)]
  },
  {
    id: 'showcase-20', templateId: 'factory-monitoring-center', code: 'TEMPLATE-20', name: '生产监控中心态势图', title: '生产监控中心数据可视化', shortName: '生产监控中心', preview: 'jimu-screen/templates/template-20.jpg', theme: THEMES.t20,
    summary: '青绿生产监控中心以中央立体区域图为核心，左右和底部密集分布趋势、环形指标、状态列表及生产明细。',
    layout: L('顶部标题下方左右各放趋势图与环形指标', '中央区域图占据中部主视觉，周围悬浮多个圆形状态指标', '底部三段明细表与一条宽折线形成高密度监控收口'),
    components: C('中央区域态势图', '环形状态指标', '生产趋势', '设备排行', '状态明细表', '宽幅关系折线'),
    constraints: L('中央态势图必须保持独立轮廓并占据最大视觉层级', '周围圆形指标以地图为中心分布而非排成单行', '青绿主色中黄色仅用于注意状态和关键节点'),
    regions: [R('line','生产趋势',28,112,440,250),R('pie','计划达成率',28,382,210,210),R('pie','设备运行率',258,382,210,210),R('table','工单状态',28,612,440,388),R('map','生产区域态势',490,112,940,570,'hero-map'),R('line','生产关系趋势',490,702,940,298),R('area','质量变化',1452,112,440,250),R('pie','质量合格率',1452,382,210,210),R('pie','交付完成率',1682,382,210,210),R('table','设备运行明细',1452,612,440,388)]
  }
])

const chartComponents = Object.freeze({
  line: 'JLine', area: 'JArea', bar: 'JBar', hbar: 'JHorizontalBar', pie: 'JRing',
  gauge: 'JGauge', radar: 'JRadar', table: 'JScrollBoard', map: 'JAreaMap', hero: 'JFocusOrb', metric: 'JStatsSummary',
  ranking: 'JReplicaRankingList', weather: 'JReplicaWeatherStrip', mapOperations: 'JReplicaMapOperations'
})

function seedFor(text = '') {
  return [...String(text)].reduce((sum, char) => (sum + char.charCodeAt(0)) % 97, 23)
}

function chartRows(title) {
  const seed = seedFor(title)
  return ['一月', '二月', '三月', '四月', '五月', '六月'].map((name, index) => ({
    name,
    value: 28 + ((seed + index * 17) % 64),
    target: 36 + ((seed + index * 11) % 58)
  }))
}

function tableRows(title) {
  const seed = seedFor(title)
  return Array.from({ length: 6 }, (_, index) => [
    String(index + 1).padStart(2, '0'),
    `${title.slice(0, 6)}${index + 1}`,
    ['正常', '运行中', '关注', '已完成'][index % 4],
    String(60 + ((seed + index * 13) % 39))
  ])
}

function frameWidget(showcase, region, index) {
  const id = `${showcase.id}-frame-${index + 1}`
  return {
    id, i: id, component: 'JDragBorder', componentName: `${region.title}框架`, type: 'border',
    x: region.x, y: region.y, w: region.w, h: region.h, orderNum: 2, locked: false, visible: true,
    visualRole: 'frame', panelVariant: `showcase-${showcase.code.toLowerCase()}-frame`,
    style: { background: showcase.theme.panel, borderColor: `${showcase.theme.primary}55`, mainColor: showcase.theme.primary, subColor: showcase.theme.secondary },
    config: { dataType: 1, background: showcase.theme.panel, borderColor: `${showcase.theme.primary}55`, option: { type: index % 3 === 0 ? '12' : '4', mainColor: showcase.theme.primary, subColor: showcase.theme.secondary } }
  }
}

function contentWidget(showcase, region, index) {
  const id = `${showcase.id}-widget-${index + 1}`
  const rows = region.kind === 'weather'
    ? [
        { day: '今天', icon: 'sun', weather: '晴', temp: '15°~18°C' },
        { day: '明天', icon: 'cloud-sun', weather: '小雨', temp: '17°~21°C' },
        { day: '后天', icon: 'cloud-rain', weather: '小雨', temp: '18°~24°C' }
      ]
    : region.kind === 'mapOperations'
      ? [
          { name: '北京运营中心', tone: 'amber' },
          { name: '上海运营中心', tone: 'teal' },
          { name: '深圳运营中心', tone: 'violet' }
        ]
      : region.kind === 'ranking'
        ? ['华东大区', '华南大区', '华北大区', '华中大区', '西北大区'].map((name, rank) => ({ name, rank: rank + 1, value: 6848 - rank * 827 }))
        : region.kind === 'table'
    ? tableRows(region.title)
    : region.progress
      ? [{ name: '已完成', value: region.progress }, { name: '待完成', value: 100 - region.progress }]
      : chartRows(region.title)
  const metricValue = region.metricValue ?? (1000 + seedFor(region.title) * 137)
  const config = {
    dataType: 1,
    background: '#00000000',
    borderColor: '#00000000',
    chartData: JSON.stringify(rows),
    chartDataParsed: rows,
    option: {
      color: showcase.theme.palette,
      title: { show: !region.hideTitle, text: region.hideTitle ? '' : region.title, left: 12, top: 8, textStyle: { color: showcase.theme.text, fontSize: 14, fontWeight: 600 } },
      legend: { show: !region.progress, textStyle: { color: showcase.theme.muted } },
      xAxis: { axisLabel: { color: showcase.theme.muted }, splitLine: { lineStyle: { color: showcase.theme.grid } } },
      yAxis: { axisLabel: { color: showcase.theme.muted }, splitLine: { lineStyle: { color: showcase.theme.grid } } },
      series: []
    }
  }
  if (region.kind === 'table') {
    config.option = {
      header: [{ label: '序号' }, { label: '对象' }, { label: '状态' }, { label: '指标' }],
      headerBGC: `${showcase.theme.secondary}55`, oddRowBGC: `${showcase.theme.panelStrong}CC`,
      evenRowBGC: `${showcase.theme.panel}88`, fontColor: showcase.theme.text
    }
  }
  if (region.kind === 'metric') {
    const metricRows = [{ name: region.title, value: metricValue }]
    config.chartData = JSON.stringify(metricRows)
    config.chartDataParsed = metricRows
    config.option = { title: region.title, unit: region.unit || (index % 2 ? '%' : '项'), valueColor: showcase.theme.accent }
  }
  if (region.kind === 'hero') {
    config.chartData = '[]'
    config.chartDataParsed = []
    config.option = { color: showcase.theme.primary, secondaryColor: showcase.theme.secondary, rings: 5, glow: true }
  }
  return {
    id, i: id, component: region.progress ? 'JReplicaProgressRing' : (region.component || chartComponents[region.kind] || 'JLine'), componentName: region.title, title: region.title,
    type: region.kind, x: region.x + (region.noInset ? 0 : 10), y: region.y + (region.noInset ? 0 : 10), w: region.w - (region.noInset ? 0 : 20), h: region.h - (region.noInset ? 0 : 20),
    orderNum: 10 + index, locked: false, visible: true, visualRole: region.role,
    panelVariant: `showcase-${showcase.code.toLowerCase()}-${region.kind}`,
    value: region.kind === 'metric' ? metricValue : undefined,
    content: region.kind === 'metric' ? String(metricValue) : undefined,
    style: { color: showcase.theme.text, background: '#00000000', borderColor: '#00000000', glow: showcase.theme.primary },
    config
  }
}

function sectionBandWidget(showcase, band, index) {
  const id = `${showcase.id}-section-${index + 1}`
  return {
    id, i: id, component: 'JText', componentName: band.title, title: band.title,
    type: 'section-title', x: band.x, y: band.y, w: band.w, h: band.h,
    orderNum: 40 + index, locked: false, visible: true, visualRole: 'section-title',
    panelVariant: `showcase-${showcase.code.toLowerCase()}-section-band`,
    style: { color: showcase.theme.text, background: `${showcase.theme.secondary}48`, borderColor: '#00000000' },
    config: {
      dataType: 1, background: `${showcase.theme.secondary}48`, borderColor: '#00000000',
      option: { body: { text: `${band.title}  /${band.subtitle || ''}`, color: showcase.theme.text, fontSize: 13, fontWeight: 600, textAlign: 'left', letterSpacing: 0 } }
    }
  }
}

function buildShowcaseContent(showcase) {
  const reference = DASHBOARD_REFERENCE_MANIFEST.find(item => item.code === showcase.code)
  const canvasWidth = reference?.width || 1920
  const canvasHeight = reference?.height || 1080
  const scaleX = canvasWidth / 1920
  const scaleY = canvasHeight / 1080
  const scaleRect = ({ x, y, w, h }) => ({
    x: Math.round(x * scaleX), y: Math.round(y * scaleY),
    w: Math.round(w * scaleX), h: Math.round(h * scaleY)
  })
  const scaleWidget = widget => {
    if (showcase.coordinateSpace === 'native') return widget
    const rect = scaleRect(widget)
    return { ...widget, ...rect }
  }
  const ambientId = `${showcase.id}-ambient`
  const titleId = `${showcase.id}-title`
  const timeId = `${showcase.id}-time`
  const widgets = [
    {
      id: ambientId, i: ambientId, component: 'JDataConstellation', componentName: '环境背景', type: 'decoration',
      x: 0, y: 0, w: canvasWidth, h: canvasHeight, orderNum: 0, locked: false, visible: true, visualRole: 'ambient',
      config: { dataType: 1, background: showcase.theme.background, borderColor: '#00000000', option: { color: showcase.theme.primary, secondaryColor: showcase.theme.secondary, density: 32, opacity: showcase.theme.id === 'light-finance' ? 0.05 : 0.18 } }
    },
    {
      id: titleId, i: titleId, component: 'JText', componentName: showcase.title, title: showcase.title, content: showcase.title,
      type: 'title', x: showcase.header?.title?.x ?? 420, y: showcase.header?.title?.y ?? 12, w: showcase.header?.title?.w ?? 1080, h: showcase.header?.title?.h ?? 76, orderNum: 50, locked: false, visible: true, visualRole: 'dashboard-title', panelVariant: `showcase-${showcase.code.toLowerCase()}-title`,
      style: { color: showcase.theme.text, background: '#00000000', borderColor: '#00000000', glow: showcase.theme.primary },
      config: { dataType: 1, background: '#00000000', borderColor: '#00000000', option: { body: { text: showcase.title, color: showcase.theme.text, fontSize: showcase.header?.title?.fontSize ?? 34, fontWeight: 700, textAlign: showcase.header?.title?.textAlign ?? 'center', letterSpacing: showcase.header?.title?.letterSpacing ?? 3 } } }
    },
    {
      id: timeId, i: timeId, component: 'JCurrentTime', componentName: '实时更新时间', type: 'time',
      x: showcase.header?.time?.x ?? 1570, y: showcase.header?.time?.y ?? 20, w: showcase.header?.time?.w ?? 320, h: showcase.header?.time?.h ?? 54, orderNum: 51, locked: false, visible: true, visualRole: 'live-clock',
      style: { color: showcase.theme.text, background: '#00000000', borderColor: '#00000000' },
      config: { dataType: 1, background: '#00000000', borderColor: '#00000000', option: { body: { color: showcase.theme.text } } }
    }
  ]
  showcase.sectionBands?.forEach((band, index) => widgets.push(sectionBandWidget(showcase, band, index)))
  showcase.regions.forEach((region, index) => {
    if (region.frame !== false) widgets.push(frameWidget(showcase, region, index))
    widgets.push(contentWidget(showcase, region, index))
  })
  return {
    canvas: {
      width: canvasWidth, height: canvasHeight, backgroundColor: showcase.theme.background, backgroundImage: '',
      showcaseId: showcase.id, templateId: showcase.templateId, visualBlueprint: showcase.theme.id,
      themeId: showcase.theme.id, theme: showcase.theme,
      sourceWidth: canvasWidth, sourceHeight: canvasHeight, sourceAspectRatio: reference?.aspectRatio
    },
    widgets: widgets.map(scaleWidget)
  }
}

export const DASHBOARD_SHOWCASES = Object.freeze(DASHBOARD_SHOWCASE_SPECS.map(spec => Object.freeze({
  ...spec,
  reference: DASHBOARD_REFERENCE_MANIFEST.find(item => item.code === spec.code),
  geometrySignature: `${DASHBOARD_REFERENCE_MANIFEST.find(item => item.code === spec.code)?.width}x${DASHBOARD_REFERENCE_MANIFEST.find(item => item.code === spec.code)?.height}|${spec.regions.map(region => `${region.kind}:${region.x},${region.y},${region.w},${region.h}`).join('|')}`,
  content: Object.freeze(buildShowcaseContent(spec))
})))

export function getDashboardShowcase(showcaseOrTemplateId) {
  return DASHBOARD_SHOWCASES.find(item => item.id === showcaseOrTemplateId || item.templateId === showcaseOrTemplateId) || null
}

export function createDashboardShowcaseContent(showcaseOrTemplateId) {
  const showcase = typeof showcaseOrTemplateId === 'string' ? getDashboardShowcase(showcaseOrTemplateId) : showcaseOrTemplateId
  return showcase?.content ? clone(showcase.content) : null
}
