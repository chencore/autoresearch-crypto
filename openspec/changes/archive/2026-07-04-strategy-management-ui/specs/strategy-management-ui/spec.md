## 新增需求

### 需求:策略列表页

前端必须在 `/strategies` 路由展示策略列表页。页面挂载时必须调用 `GET /api/v1/strategy` 拉取列表，加载中必须显示加载态（NSpin 或 NDataTable loading），加载失败必须用 NMessage 弹错误提示且不崩页。列表必须用 NDataTable 展示，列至少包含：类名（name）、模块（module）、参数数量（params_count）、实盘状态（live_status）、操作（查看详情）。列表必须按后端返回顺序展示（后端已按类名字母序）。

#### 场景:首次进入策略管理页
- **当** 用户从侧边栏点「策略管理」进入 `/strategies`
- **那么** 页面发起 `GET /api/v1/strategy`，加载态显示，请求成功后表格渲染 10 行策略

#### 场景:列表加载失败
- **当** 后端返回 500 或网络错误
- **那么** 页面顶部弹 NMessage error 提示「策略列表加载失败：<message>」，表格显示空状态

#### 场景:实盘状态占位
- **当** 列表渲染某行 `live_status: "unknown"`
- **那么** 该行实盘状态列显示灰色 NTag「unknown」

### 需求:策略详情抽屉

用户在列表点「查看详情」时，前端必须打开 NDrawer 展示该策略的完整元数据。抽屉打开时必须调用 `GET /api/v1/strategy/{name}` 拉取详情，加载中显示 NSpin。抽屉内容必须分四段：基本信息（NDescriptions：name / module / file / description）、class docstring（NCode 或 pre 块保留换行）、参数定义表（NDataTable：name / type / default / annotation / required）、信号类型与运行时参数（NTag 组）。抽屉关闭后再次打开另一策略必须重新拉取详情，禁止缓存旧数据。

#### 场景:打开详情抽屉
- **当** 用户在列表行点「查看详情」
- **那么** NDrawer 从右侧滑入，发起 `GET /api/v1/strategy/{name}`，加载完成后展示四段元数据

#### 场景:详情请求返回 404
- **当** 抽屉打开后后端返回 404（`{"error":{"code":"not_found","message":"strategy not found: X"}}`)
- **那么** 抽屉内显示 NEmpty「策略不存在」，不崩页

#### 场景:切换查看不同策略
- **当** 用户先看 A 策略详情，关闭抽屉后再看 B 策略详情
- **那么** 第二次打开必须重新发起 `GET /api/v1/strategy/B`，不展示 A 的残留数据

### 需求:策略 API 客户端

前端必须新增 `src/api/strategy.ts` 模块，导出 `fetchStrategyList` 与 `fetchStrategyDetail` 两个异步函数，分别对应两个后端接口。模块必须导出与后端 `app.schemas.strategy` 对齐的 TypeScript 类型：`ParamDef`、`StrategySummary`、`StrategyDetail`、`StrategyListResponse`。函数失败时必须 reject `{ code, message }` 结构（由 `client.ts` 拦截器保证）。

#### 场景:调用列表接口
- **当** 调用 `fetchStrategyList()`
- **那么** 返回 `StrategyListResponse`（含 `strategies: StrategySummary[]` 与 `total: number`）

#### 场景:调用详情接口
- **当** 调用 `fetchStrategyDetail('GridStrategy')`
- **那么** 返回 `StrategyDetail`，含 `signal_kind: "position_target"`、`params: ParamDef[]`

#### 场景:接口失败
- **当** 后端返回 404
- **那么** 函数 reject `{ code: "not_found", message: "strategy not found: ..." }`，调用方可在 catch 里读 `code` 分支处理

### 需求:参数定义展示

详情抽屉的参数定义表必须展示所有参数，列含 name / type / default / annotation / required。`required` 列必须用 NTag 表示（true → 红色「必填」，false → 默认色「可选」）。`default` 列必须能展示任意 JSON 值（null / 数字 / 字符串 / 嵌套 dict 的字符串表示），不可序列化的值后端已转字符串，前端直接展示。`annotation` 为 null 必须显示「—」破折号占位。

#### 场景:必填参数
- **当** 详情表渲染某行 `required: true`
- **那么** required 列显示红色 NTag「必填」

#### 场景:可选参数带 null 默认值
- **当** 渲染某行 `required: false, default: null, annotation: "float | None"`
- **那么** default 列显示「null」，annotation 列显示「float | None」

#### 场景:无 annotation 的参数
- **当** 渲染某行 `annotation: null`
- **那么** annotation 列显示「—」破折号

### 需求:信号类型与运行时参数展示

详情抽屉必须用 NTag 展示 `signal_kind` 与 `runtime_params`。`signal_kind` 为 `"position_target"` 必须用橙色 tag，为 `"discrete"` 必须用蓝色 tag。`runtime_params` 为空数组必须显示灰色 tag「无运行时参数」，非空必须为每个参数名显示一个蓝色 tag。

#### 场景:GridStrategy 信号类型
- **当** 查看 GridStrategy 详情
- **那么** 信号类型显示橙色 NTag「position_target」

#### 场景:TrendStrategy 信号类型
- **当** 查看 TrendStrategy 详情
- **那么** 信号类型显示蓝色 NTag「discrete」

#### 场景:有运行时参数的策略
- **当** 查看 HybridMeanRevMomentumStrategy 详情，`runtime_params: ["enable_short"]`
- **那么** 运行时参数区显示一个蓝色 NTag「enable_short」

#### 场景:无运行时参数的策略
- **当** 查看 TrendFollowStrategy 详情，`runtime_params: []`
- **那么** 运行时参数区显示灰色 NTag「无运行时参数」
