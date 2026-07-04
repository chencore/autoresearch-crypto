## 上下文

`live-monitor-api` 已完成后端 5 个接口,响应格式见 `backend/app/schemas/live.py`:
- `GET /exchanges` → `ExchangeListResponse {exchanges: [ExchangeInfo{name, status, pid, script, state_file, log_file}], total}`
- `POST /start` → `StartResponse {exchange, pid, status}` 或 `{error: {code, message}}` 错误码 `invalid_exchange` / `invalid_mode` / `already_running`
- `POST /stop` → `StopResponse {exchange, status}` 或错误码 `not_running`
- `GET /status/{exchange}` → `LiveStatus {exchange, running, state: dict|null, updated_at: str|null}` 或错误码 `not_found`
- `GET /logs/{exchange}?tail=200` → `LogResponse {exchange, lines: str[], total_lines}` (Query `ge=1, le=1000`)

前端 axios 客户端 `client.ts` 已配拦截器:`response.data` 直接返回、错误转 `{code, message}` reject。Strategies.vue / Backtest.vue 已建立模式:`useMessage()` 弹错、`ref<X | null>` 管理状态、`onMounted` 拉数据。App.vue 已包 `NMessageProvider`。

实盘页与回测页区别:实盘是长跑进程,需要 2s 轮询持续刷新;回测是一次性请求。轮询竞态(用户快速切换交易所 / 旧响应慢于新响应)必须显式处理,否则会闪现错误数据。

## 目标 / 非目标

**目标:**
- 3 交易所卡片网格(running/stopped 状态 + PID + 启停按钮)
- 启动 Modal(symbol/mode/capital/leverage 表单)
- 状态区(NDescriptions 透传 state dict)
- 日志区(NCode/pre 末尾 200 行,2s 轮询)
- 2s 轮询 exchanges + 选中交易所的 status/logs,离开页面清理
- 轮询竞态用请求序号丢弃过期响应
- 错误按 code 分级(message error / warning)

**非目标:**
- 不实现 WebSocket(轮询够用,留给 evolution-api)
- 不实现日志过滤 / 搜索 / 跳行(v0.1 简单末尾 200 行)
- 不实现 state 字段裁剪 / 翻译(透传 dict,前端按需展示)
- 不实现多 symbol 同交易所(脚本文件锁限制)
- 不实现策略参数编辑(v0.1 用脚本默认参数)
- 不动后端、router、其他页

## 决策

### 决策 1:卡片网格用 NGrid `cols=3` 响应式 `xs=1 / s=2 / l=3`
- **选择**:`<NGrid :cols="3" :x-gap="16" :y-gap="16" responsive="screen">`
- **替代方案**:用 flex / grid css 手写
- **理由**:NGrid 自带响应式,3 张卡片在宽屏一行排开、窄屏堆叠;naive-ui 已是项目依赖,无新增。

### 决策 2:启动表单用 NModal + NForm,不用 NDrawer
- **选择**:NModal(居中弹窗,4 个字段)
- **替代方案 A**:NDrawer 侧滑
- **替代方案 B**:卡片内联展开表单
- **理由**:4 字段表单小,Modal 居中聚焦;Drawer 适合长表单(如回测参数 + 高级选项);内联展开会让卡片高度不一致破坏网格美观。

### 决策 3:状态区用 NDescriptions 透传 state dict,不定义固定字段
- **选择**:`v-for` 遍历 `Object.entries(state)` 渲染 NDescriptionsItem
- **替代方案**:固定渲染 `position / strategy_size / entry_price / last_signal / bar_count` 5 字段
- **理由**:3 个脚本 state 字段不一致(binance 含 `pending_open` 等,nado 字段更少);透传避免前端跟脚本字段变化同步;前端按需展示,未定义字段也展示(用户可看到脚本真实状态)。

### 决策 4:日志区用 `<pre>` 不用 NCode
- **选择**:`<pre class="log-pre">{{ lines.join('\n') }}</pre>` + scoped CSS
- **替代方案**:NCode
- **理由**:NCode 会对每行加行号 / 高亮,200 行渲染开销大且不需要;`<pre>` 保留换行 + 等宽字体足够。横向滚动用 `overflow-x: auto`,纵向固定高度 400px 滚动。

### 决策 5:轮询用 setInterval 2s,不用 setTimeout 递归
- **选择**:`setInterval(fetchExchanges, 2000)` + `onUnmounted clearInterval`
- **替代方案**:setTimeout 递归(等上次请求完成再排下一次)
- **理由**:axios 30s 超时远小于 2s 间隔,不会堆积;setInterval 简单直观;递归 setTimeout 在请求慢时会漂移。竞态用请求序号(`pollSeq.value++`)丢弃过期响应。

### 决策 6:轮询竞态用 ref 序号,不用 AbortController
- **选择**:每次发起请求前 `const seq = ++pollSeq.value`,响应回来比对 `seq === pollSeq.value` 才更新
- **替代方案**:AbortController 取消旧请求
- **理由**:AbortController 需管理 controller 生命周期 + 取消后 axios 抛 CanceledError 需额外处理;序号法 3 行代码搞定,旧响应虽浪费但无副作用。

### 决策 7:轮询失败不弹 message,只在控制台 warn
- **选择**:`catch (e) { console.warn('poll failed', e) }`
- **替代方案**:每次失败弹 NMessage error
- **理由**:2s 轮询 × 失败 = 每 2s 弹一次,用户被刷屏;控制台 warn 调试时可见,用户无感。手动操作(启动 / 停止)失败才弹 message。

### 决策 8:状态/日志区合并轮询,共用一个 interval
- **选择**:一个 `setInterval` 同时拉 status + logs(并行 `Promise.allSettled`)
- **替代方案**:两个独立 interval
- **理由**:都是 2s 间隔 + 都依赖 `selectedExchange`,合并简化生命周期;Promise.allSettled 让一个失败不影响另一个。

## 风险 / 权衡

- **[macOS arm64 子进程立刻退出]** → 启动后 2s 内卡片变 stopped,用户看到状态闪烁。缓解:这是预期行为(start 返 200 表示 Popen 成功,子进程退出是脚本环境问题),success message 已显示「已启动」,用户从状态变化理解失败。
- **[轮询请求堆积]** → 后端慢时 2s 内未响应,下次 interval 又发请求。缓解:axios 30s 超时远大于 2s,单机本地几乎不会堆积;v0.1 不优化。
- **[日志文件 200 行渲染卡顿]** → 200 行 `<pre>` 渲染约 5ms,无卡顿。缓解:已选 `<pre>` 不用 NCode;若 v0.2 增到 1000 行考虑虚拟滚动。
- **[state dict 字段含嵌套对象]** → NDescriptions 嵌套 dict 会显示 `[object Object]`。缓解:`JSON.stringify(value)` 处理非 primitive 值。
- **[启动 Modal 表单未校验]** → symbol 空字符串会发请求后端报错。缓解:NForm rules 校验 symbol 非空 + capital>0 + leverage>=1,前端校验失败不发请求。

## 迁移计划

- 新增 `frontend/src/api/live.ts`:5 个接口 + 8 个 TS 类型
- 重写 `frontend/src/pages/Live.vue`:卡片网格 + 启动 Modal + 状态区 + 日志区 + 2s 轮询
- typecheck:`cd frontend && pnpm typecheck`
- dev server:`cd frontend && pnpm dev`,浏览器访问 `/live` 目视验证(手动)
- 回滚:`git checkout frontend/src/pages/Live.vue` 恢复占位 + 删 `api/live.ts`

## 待解决问题

- 是否需要「重启」按钮?v0.1 不做,用户调 stop + start 即可
- 是否持久化选中交易所?v0.1 不做,刷新页面重置为 null(显示 NEmpty 提示选择)
- 是否需要日志搜索 / 过滤?v0.1 不做,200 行直接展示
