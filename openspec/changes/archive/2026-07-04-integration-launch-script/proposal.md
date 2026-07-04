## 为什么

v0.1 已完成 10 个变更(后端骨架 / 前端骨架 / 4 个业务模块各 2 个 API+UI),开发者体验割裂:启动工作台需要手动开两个终端、分别 `cd backend && uv run uvicorn ...` 与 `cd frontend && pnpm dev`,且 README 仍停留在 v0.0 命令行 + 实盘脚本说明,无 Web 工作台入口。本变更作为 v0.1 收官,提供「一条命令拉起前后端」的根目录启动脚本,并在 README 中新增「Web 工作台」章节,降低 v0.1 上手门槛。

## 变更内容

- 新增根目录 `dev.sh`:一键启动后端(uvicorn 8000)+ 前端(vite 5173),`Ctrl+C` 同时停止;启动前做工具链检查(uv / pnpm)、端口占用检查、`.env` 文件存在性提示
- 新增根目录 `dev-stop.sh`(可选):手动停止 8000/5173 端口上的进程(脚本异常退出兜底)
- **修改** `README.md`:在「快速开始」之前新增「Web 工作台(v0.1)」章节,展示 `./dev.sh` 启动 + 浏览器访问 `http://localhost:5173` + 四模块入口截图位 + v0.1 已知限制(单机 / 无鉴权 / 回测不持久化);原「快速开始」章节保留为「命令行模式(原 v0.0 工作流)」
- **修改** `frontend/vite.config.ts`:无改动(dev 模式 vite proxy 已配 `/api → 127.0.0.1:8000` 与 `ws: true`,5173 端口可直接访问后端)
- **修改** `backend/app/main.py`:无改动(v0.1 不做生产模式静态文件 serve,留给 v0.2)

## 功能 (Capabilities)

### 新增功能

- `integration-launch-script`: 根目录启动脚本(`dev.sh` / `dev-stop.sh`)+ README Web 工作台章节,统一前后端 dev 模式启动入口与停止信号处理

### 修改功能

无。本变更不修改任何已交付能力的 spec 行为,仅做工程化封装与文档补全。

## 影响

- 受影响代码:无业务代码改动;新增 `dev.sh` / `dev-stop.sh` 两个 shell 脚本(根目录),修改 `README.md`(根目录)
- 受影响依赖:无新增(脚本仅依赖 bash / uv / pnpm,均为项目已用工具)
- 受影响 API:无
- 受影响部署:无(v0.1 仍为 dev 模式;生产部署留待 v0.2 加 backend serve 静态文件)
- 受影响文档:`README.md` 新增章节,原命令行章节降级为「原 v0.0 工作流」保留
