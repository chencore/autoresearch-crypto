## 为什么

`data-download-api`(R-v0.1-ck-10 后端部分)已交付 4 个 REST 接口 + 1 个 WebSocket 端点,但用户只能用 curl 测,前端没有可视化入口。v0.1 的目标是「桌面浏览器里一站式管理」,数据下载必须能在 Web 界面完成。本变更新增前端「数据下载」页:表单(symbol/interval/days/proxy_url/force)→ POST /start → WebSocket 接进度 → 展示 stdout 行日志 + 已下载文件列表。

## 变更内容

- **新增** `frontend/src/api/data-download.ts`:API client + WS URL 构造 + 事件类型定义
- **新增** `frontend/src/pages/DataDownload.vue`:数据下载页(表单 + 运行状态 + 事件日志 + 文件列表)
- **修改** `frontend/src/router/index.ts`:新增 `/data-download` 路由
- **修改** `frontend/src/App.vue`:侧边栏菜单加「数据下载」项

## 功能 (Capabilities)

### 新增功能

- `data-download-ui`: 前端「数据下载」页,表单触发后端 prepare_crypto.py 子进程,WebSocket 接 stdout 行进度,展示已下载文件列表

### 修改功能

无。

## 影响

- 受影响代码:`frontend/src/` 新增 2 个文件 + 修改 2 个文件(router / App.vue)
- 受影响依赖:无新增(复用已有 vue / naive-ui / axios)
- 受影响 API:消费已交付的 4 个 REST + 1 个 WS
- 受影响部署:无
- 受影响文档:无
