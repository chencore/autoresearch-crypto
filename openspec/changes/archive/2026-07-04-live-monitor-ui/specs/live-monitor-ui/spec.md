## 新增需求

### 需求:交易所卡片网格

`Live.vue` 加载时必须调 `GET /api/v1/live/exchanges` 获取 3 个交易所状态,以 3 列网格(NGrid `cols=3`,响应式 `xs=1 / s=2 / l=3`)展示。每张卡片含:交易所名(h3)、状态 NTag(running → success 绿、stopped → default 灰)、PID(运行中显示 `PID: 12345`、停止显示 `—`)、脚本文件名(小字灰色)、操作按钮区(运行中显示「停止」危险按钮、停止显示「启动」主按钮)。卡片必须每 2 秒自动刷新一次状态(调 `GET /exchanges`),用户也可点「刷新」按钮手动刷新。停止的交易所卡片按钮 disabled false,运行中的卡片按钮 disabled false(都可点)。

#### 场景:首次加载
- **当** 用户进入 `/live` 页
- **那么** 调 `GET /api/v1/live/exchanges`,3 张卡片显示 binance / okx / nado,全 stopped,按钮显示「启动」

#### 场景:运行中卡片
- **当** binance 已启动 PID 12345
- **那么** binance 卡片状态 tag 绿色「running」,显示 `PID: 12345`,按钮显示「停止」(danger type)

#### 场景:子进程退出后自动刷新
- **当** binance 子进程因 torch 缺失立刻退出,2 秒后下次轮询
- **那么** binance 卡片状态变灰「stopped」,PID `—`,按钮变回「启动」

### 需求:启动实盘 Modal

点击「启动」按钮必须弹 NModal,标题 `启动 ${exchange} 实盘`,含 NForm:
- `symbol`:NInput,默认 `BTCUSDT`,placeholder `如 BTCUSDT`
- `mode`:NSelect,选项 `[{label:'demo 模拟盘', value:'demo'}, {label:'live 真实盘', value:'live'}]`,默认 `demo`
- `capital`:NInputNumber,默认 100,min 1,step 10
- `leverage`:NInputNumber,默认 1,min 1,max 100,step 1

底部「取消」「确定」按钮。点「确定」必须调 `POST /api/v1/live/start`,成功后关闭 Modal + NMessage success `已启动 ${exchange}(PID ${pid})` + 立即手动刷新 exchanges。失败时根据 error.code 显示:
- `already_running` → warning `${exchange} 已在运行,请先停止`
- `invalid_exchange` / `invalid_mode` → error `${message}`
- 网络错误 → error `启动失败:网络错误`

Modal 打开时表单恢复默认值;确定按钮 loading 期间禁用取消与确定。

#### 场景:正常启动
- **当** 用户在 binance 卡片点「启动」→ Modal 输入 symbol ETHUSDT / mode demo / capital 200 → 确定
- **那么** 调 `POST /start {exchange:'binance', symbol:'ETHUSDT', mode:'demo', capital:200, leverage:1}`,成功关闭 Modal,显示 success `已启动 binance(PID 12345)`,2s 内刷新看到 binance running

#### 场景:已启动重复启动
- **当** binance 已运行,用户点「启动」→ 确定(子进程未退出前)
- **那么** 调 `POST /start` 返 409 already_running,显示 warning `binance 已在运行,请先停止`,Modal 不关

#### 场景:网络错误
- **当** 后端未启动,用户点「启动」→ 确定
- **那么** axios 抛 network 错,显示 error `启动失败:网络错误`,Modal 不关,确定按钮 loading 复位

### 需求:停止实盘

点击「停止」按钮必须调 `POST /api/v1/live/stop`(body `{exchange}`),成功后 NMessage success `已停止 ${exchange}` + 立即刷新 exchanges。失败时:
- `not_running` → warning `${exchange} 未在运行`(可能是轮询间隙子进程刚退出)
- 网络错误 → error `停止失败:网络错误`

停止期间按钮 loading,避免重复点击。

#### 场景:正常停止
- **当** binance 运行中,用户点「停止」
- **那么** 调 `POST /stop {exchange:'binance'}`,成功显示 success `已停止 binance`,刷新看到 stopped

#### 场景:停止已退出的进程
- **当** binance 子进程已退出但前端尚未轮询到,用户点「停止」
- **那么** 调 `POST /stop` 返 404 not_running,显示 warning `binance 未在运行`,刷新看到 stopped

### 需求:实盘状态展示

选中某交易所(点卡片任意区域或「查看状态」链接)→ 下方 `状态` 区必须调 `GET /api/v1/live/status/{exchange}` 展示:
- `running`:NTag 绿/灰
- `state`:NDescriptions 列出 `position` / `strategy_size` / `entry_price` / `last_signal` / `bar_count` 等字段(state 为 null 时显示 NEmpty「暂无状态数据,实盘启动后稍候」)
- `updated_at`:NText 小字灰色(为 null 显示 `—`)

状态区必须 2s 轮询(与卡片网格同步)。切换选中交易所时,旧轮询立即取消,新轮询启动。

#### 场景:运行中且有 state
- **当** 选中 binance,binance 运行中,`logs/live_binance_state.json` 含 `{position:1, strategy_size:0.5, entry_price:2000}`
- **那么** 状态区显示 running 绿 tag + NDescriptions 列出 position=1 / strategy_size=0.5 / entry_price=2000 + updated_at ISO 字符串

#### 场景:运行中但 state 未就绪
- **当** 选中 binance,binance 刚启动 state 文件尚未写入
- **那么** 状态区显示 running 绿 tag + NEmpty「暂无状态数据,实盘启动后稍候」+ updated_at `—`

#### 场景:不支持交易所
- **当** 通过 URL 直接访问或异常情况,选中 exchange 不在 3 个之列(理论上不会发生)
- **那么** 状态区显示 NEmpty「请选择交易所」

### 需求:实盘日志展示

选中某交易所 → 下方 `日志` 区必须调 `GET /api/v1/live/logs/{exchange}?tail=200` 展示末尾 200 行,用 NCode 或 `<pre>` 渲染(等宽字体,横向滚动)。日志区必须 2s 轮询(与状态同步)。`total_lines` 在标题旁显示小字 `(共 1234 行)`。`lines` 为空时显示 NEmpty「暂无日志」。

#### 场景:正常日志
- **当** 选中 binance,`logs/live_binance_log.txt` 有 500 行
- **那么** 日志区显示末尾 200 行(等宽字体),标题旁 `(共 500 行)`

#### 场景:日志文件不存在
- **当** 选中 okx,okx 从未运行过日志文件不存在
- **那么** 日志区显示 NEmpty「暂无日志」,标题旁 `(共 0 行)`

#### 场景:nado 日志取最新
- **当** 选中 nado,`logs/` 下含 `live_nado_log_20260704_120000.txt` 与 `live_nado_log_20260704_150000.txt`
- **那么** 日志区展示后者内容(后端已实现取最新)

### 需求:轮询生命周期

页面 `onMounted` 必须启动两个轮询(2s 间隔):`exchanges` 轮询刷新卡片网格、`status + logs` 轮询刷新选中交易所的下方区域。`onUnmounted` 必须清理所有 `setInterval` 定时器。轮询请求竞态用「最后一次响应优先」策略(用 ref 记录最新请求序号,过期响应丢弃)。轮询失败(网络错误)不弹 message,只在控制台 warn(避免每 2s 弹一次)。

#### 场景:离开页面清理
- **当** 用户从 `/live` 切到 `/backtest`
- **那么** `onUnmounted` 清理 setInterval,后端不再收到该用户的轮询请求

#### 场景:轮询竞态
- **当** 用户快速切换选中交易所 binance → okx,binance 的 status 请求慢于 okx 返回
- **那么** binance 的迟到响应被丢弃(okx 的响应保留),状态区不会闪现 binance 数据

#### 场景:轮询网络错误
- **当** 后端重启,轮询请求失败
- **那么** 控制台 warn,不弹 message,下次轮询继续尝试
