# 报表与模板模块

读者为报表、前后端开发与测试人员。入口`router.py`，`service.py`负责结构化模板、画布校验、查询绑定与版本生命周期；前端渲染和编辑器独立实现，可继续增删组件。

## 复用与运行行为

- `resources/hifi_templates.json`提取自用户Test5快照同路径资源，保留20套模板的canvas/widgets/config与可编辑布局；未整体复制原仓库、依赖、大图或设备包。
- 模板中的参考示例仍标记`sample_data/referenceSample`。克隆可以编辑；真实发布前，数据组件须绑定保存的查询并刷新，不把示例数字当企业结果。
- 报表、每次修改产生的不可变修订、已发布修订分开保存。恢复创建新修订，保留被恢复前的历史；软删不抹历史。
- HTML/PNG/PDF沿用绝对坐标、尺寸、层级、参考皮肤孔洞遮罩与裁片；已支持类型按真实柱/折/面积/饼环/仪表/雷达/表格输出，不把柱画成折线。PNG/PDF通过本机独立Chrome离线渲染，禁用JS和所有网络；超时清理进程树。CSV/JSON保持真实数据，DOCX/XLSX生成真实表格与单元格，PPTX包含原生图表和分页原生表格。CSV/Excel防公式注入，HTML文本转义。没有把这些静态导出当成全部原ECharts交互/动画或全部源渲染器完成。

## 主要API

所有路径均以`/api/v1`开头，编辑和生命周期请求带当前`revision`。

| 路径 | 方法 | 作用 |
|---|---|---|
| `/reports`、`/{id}` | GET/POST、GET/PATCH/DELETE | 画布与组件编辑；新建可选择template_id |
| `/reports/{id}/revisions` | GET | 不可变历史快照 |
| `/reports/{id}/restorations` | POST | revision、target_revision；生成新草稿修订 |
| `/reports/{id}/publications` | POST | 验证真实查询数据与证据一致后发布 |
| `/reports/{id}/data-refreshes` | POST | 重执行各绑定查询，写入真实data与来源证明 |
| `/reports/{id}/drilldowns` | POST | widget_id、filters；只在当前授权查询快照筛选 |
| `/reports/{id}/exports` | POST | format=html/csv/json/pdf/png/docx/xlsx/pptx，可显式revision |
| `/reports/{id}/image-assets` | GET | 当前报表授权图片file_id/data_uri；逐文件授权、有限位图预算 |
| `/report-generations` | POST | name、template_id、bindings；按模板与真实查询组装 |
| `/report-templates`、`/{id}` | GET/POST、GET/PATCH/DELETE | 内置模板只读；另存为自定义模板后可编辑 |
| `/report-components`、`/catalog` | GET | 支持类型与完整源目录；逐项canonical、renderer_supported、依赖和后续提取路径 |
| `/report-component-instances` | POST | source_id、可选id/x/y/order_num；保留同类型不同原始变体和独立配置 |
| `/prompt-templates` | GET/POST/PATCH/DELETE | 提示模板管理，未配置模型时不生成假模型输出 |

数据组件形状：

```json
{
  "id": "yield-trend",
  "component": "JLine",
  "x": 24, "y": 80, "w": 640, "h": 320,
  "binding": {"query_id": "已授权查询ID"}
}
```

增加组件应在`SUPPORTED_COMPONENTS`登记、前端组件注册表接入实际渲染器、后端导出器实现必要输出，并补绑定/导出测试。原生复杂渲染器可通过精确提取新增；不先预建无用途抽象层。

验证见`tests/test_reports_lifecycle.py`：20模板结构、真实换数、示例发布阻止、数据篡改拒绝、版本冲突与恢复、HTML转义、CSV公式转义。测试只创建隔离SQLite，不调用模型或原系统。

## 源目录与后续完整复用

326是源工具栏项，不是326种渲染器；源目录含111种源叶类型。当前代码登记51个canonical(含模板用JDragImg)和5个英文别名；在源工具栏中对应254项/50源类型。T-web10新增26类型/28源项有离线与静态证据，尚待真实浏览器验收；未支持的72项不会静默当折线或通用卡片。

| 分组 | 源项/类型 | 当前可选项 | 下一步精提 |
|---|---:|---:|---|
| 图表 | 56/52 | 30 | 已有笛卡尔变体、6种专用图、进度；其余按真实几何/协议逐项接入 |
| 素材 | 205/5 | 205 | 已提取原工厂、13边框/12装饰变体、178图片项(177本地素材+空白项)、图标与轮播 |
| 文字 | 15/10 | 5 | 已接时钟、数值、翻牌与卡片；云图/轮播卡片仍待各自实际行为 |
| 表格 | 11/9 | 9 | 已接真实字段表格/排名/列表与滚动；透视和开发历程仍待专用协议 |
| 自定义 | 5/5 | 2 | 已接颜色卡片/真实排名；其他对应渲染器与授权交互，不执行任意脚本 |
| 地图 | 8/8 | 0 | 本地GeoJSON/实际地图与3D渲染，不能用平面柱图顶替 |
| 视频 | 2/2 | 0 | 受控文件与真实播放器，不任意外链 |
| 其他 | 8/7 | 1 | 已接安全富文本编辑；表单/外页/按钮必须接真实授权行为 |
| 业务 | 16/13 | 2 | 已接统计/简洁卡片；任务、项目、日历和筛选须具体业务协议 |

`component_catalog.json`原始分组、源ID、类型、尺寸和引用完整保留。`resources/source/`仅精提原`widgetFactory.js`、`widgetSizing.js`与`jimuScreenPage.json`；`build_presets.mjs`执行这几个可信纯函数，生成326项逐项配置种子。新建实例统一清空所有业务样例值、连接字段和凭据；不执行原`customOption`脚本。每个实例深拷贝并生成独立ID，修改一种边框不会覆盖另一种。

前端素材与后端离线导出使用相同变体语义；保留位置、尺寸、层级和旋转。轮播在前端按配置定时切换，减少动效时停止；PDF/PNG固定导出首帧。原13边框/12装饰使用实际SVG/CSS；图片实际内嵌本地PNG；图标按当前提供的6种图标选择输出实际图形，不把其他类型硬画成星形。

用户图片使用`config.file_id`，轮播使用`config.images:[{file_id}]`或提取素材路径。保存时校验当前文件授权并登记引用，历史版本也不能绕过撤权；预览和导出仅允许PNG/JPEG/WebP，每张2MB，图片预览合计5MB。明确提取的静态素材有一张3.17MB图片，静态素材单张允许4MB；整份离线打印输入仍受5MB预算约束，超过时返回失败。任意外链、用户磁盘路径或原数据源配置不会被导出器访问。

T-domain03验证见`test_reports_materials.py`：逐项205素材原配置/变体/独立ID/清样例、177素材实际读取、6图标区分、轮播首帧、组合画布实际PDF/800×400 PNG、私有图片授权与历史拒绝、原生DOCX无空表崩溃、布局错误与伪装装饰绕过拒绝。前端205项逐项创建及真实浏览器渲染由web作者单独验收；后端测试不替代该证据。

20套模板已逐套用一份2行本地小数据替换数据组件并通过发布验证；仅证明结构/绑定/数据生命周期，未运行外部企业库或真实模型，也不等于全部源类型已实现。后续地图/3D、视频和表单行为须各自接真正渲染器或授权接口。

## 多系列图表协议与本轮验收

`chart_data.py`适配原JimuChart宽表系列方式，字段选择改为明确绑定，避免按列顺序猜业务含义。绑定兼容`value_field`（默认value）与`label_field`（默认name）；多系列使用`value_fields:["合格数","不合格数"]`，最多8列。组合图第一列为柱、其余为线；只有显式`secondary_value_fields`中的折线字段使用右轴，普通分组、堆叠、多折线共用主轴。

真实刷新及后续保存核验查询证据后，服务端派生`widget.series_model`，客户端提交的该字段先剥除再重算。结构为`{status,categories,percentage,horizontal,series}`，每系列包括`field/name/values/raw_values/axis/type/stack/smooth/step`。`values`与`raw_values`为十进制字符串或null，前者用于几何，后者用于原值提示；图形坐标允许显示层浮点近似，判定证据与原始数据不因此改写。

百分比堆叠按每个分类用Decimal归一化。任一组成缺失或全部为0时该分类保留空缺；负值拒绝。一般线图缺点形成断线，不能补0或跨过断点连线。发布时再次计算模型并拒绝未选择字段、无可展示数据等状态。

本轮已启用的8个源类型为`JStackBar/JMultipleBar/JPercentBar/JMixLineBar/JMultipleLine/JSmoothLine/JStepLine/JNegativeBar`，保留原compType。`cartesian.py`已实现对应SVG；`exports.py`新增多系列PPT、原生堆叠/百分比、柱折组合/显式右轴、原生XY阶梯。源option仅提供白名单外观，不执行`customOption`脚本。

后端10条图表测试与八类真实ECharts行为验证通过，包含缺值断线、原值提示、百分比分母、双轴、负值和阶梯坐标；8类型已加入支持集。浏览器最终复用已成功刷新的保存快照，4辅助API、0业务查询。Office XML/数据有断言，Office桌面视觉及剩余72源目录项未验收。

## 真实展示、字段映射与模板换数（T-web10）

`presentation.py`为26个精提类型生成可信`presentation_model`：3种表格、2种排名、2种列表、3种卡片、2种数值、6种进度、6种非笛卡尔图、时钟与富文本。新模型在保存时剥除客户端值，只从已匹配的查询证据重算；前端`DataPresentationWidget.vue`与`specialChartOption.ts`直接消费该模型。原工厂每项独立尺寸/option、来源ID和类型保留，不执行源customOption代码。

表格用`binding.fields`选择至多20列，`config.column_labels`设置列名；列表可绑定`description_field/time_field`，数值可绑定`unit_field`，进度可绑定`total_field`。未指定进度目标时量纲为0—100；超界/无效目标拒绝。单值组件要求一行，不静默丢弃其他行；排名用Decimal精确排序，null留空，雷达指标缺失时阻止错误图形。漏斗、玫瑰、矩形、半仪表和圆形雷达有独立ECharts与离线SVG分支。Office输出真实明细，保留显示列、排名与十进制原文；这些专用图尚未提供原生Office图形，不偷换为柱图。

`POST /report-generations`的`bindings`兼容`{"组件ID":"查询ID"}`，也接受显式查询映射对象；对象只允许查询与字段，不能注入检验来源、客户端事实或凭据。前端`TemplateDataBindings.vue`支持统一选查询、逐组件映射并直接生成；同一个查询在一次刷新中只执行一次。新表单只读已保存查询列表，选择操作不自动取数。旧模板保留原布局，绑定内容由服务端刷新和来源权限保护。

富文本使用有限排版标签：前端格式工具与服务端同一白名单，删除事件/样式/外链/图片/可执行嵌入；纯文本Office输出不含标签。时钟只接受北京时间或UTC，离线文档记录实际渲染时刻。设计器增加实际画布尺寸/底色、根据视口适配、组件落点插入、属性变更撤销及Ctrl/Cmd+Z、Shift+Z/Y，输入区域保留原生文字编辑快捷键。

新增验证`test_reports_presentation.py`与`apps/web/tests/report-presentation-options.mjs`：新临时SQLite、原工厂配置、防伪投影、明确字段、目标边界、精确排序、零/缺失、HTML清洗、模板映射生成和真实Office数据；ECharts使用Node SVG SSR，不启动浏览器。新增26类型、富文本编辑、画布/生成表单的浏览器交互和视觉验收仍由root串行安排。

## 正式质检报告

`POST /api/v1/inspection-report-generations`绑定不可变检验run，正文保留固定标准、全部样本和精确复核事件。读取/历史/导出重查原run字段权限；复制、模板和删除组件不能洗掉来源依赖。正式正文只读，改名、刷新审阅快照和发布携带revision；7格式导出，其中PDF为自然分页流式文档，原自由画布保持独立。详见`inspection_source.py`、`inspection_document.py`和12条来源/展示回归。
