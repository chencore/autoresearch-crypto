## 上下文

v0.1 已交付 4 个业务模块(策略管理 / 回测 / 实盘 / 进化)的 API + UI 共 8 个变更 + 2 个骨架变更。当前开发者启动工作台需手动:

1. 终端 A:`cd backend && uv run uvicorn app.main:app --reload --port 8000`
2. 终端 B:`cd frontend && pnpm dev`
3. 浏览器开 `http://localhost:5173`

痛点:两个终端 / 两条命令 / Ctrl+C 两次 / 异常退出端口残留。README 仍停留在 v0.0 命令行模式,无 Web 工作台入口。

关键技术约束:
- 后端 pydantic-settings `env_file=".env"` 相对 CWD,若 `cd backend` 启动会读 `backend/.env`(不存在),必须从根目录启动后端
- vite proxy 已配 `/api → 127.0.0.1:8000` 与 `ws: true`,5173 是唯一前端入口
- macOS arm64 装不上 torch(`torch==2.6.0+cu124` 无 wheel),`live_*.py` 启动会立即退出,但 backend uvicorn 本身可正常起(后端不依赖 torch,evolution 仅依赖 numpy/pandas)
- 项目根 `uv.lock` 覆盖 `dex/` 交易核心(含 torch),backend 独立 `backend/uv.lock` 仅 FastAPI 栈

## 目标 / 非目标

**目标:**
- 根目录 `./dev.sh` 一键启动前后端,合并日志带前缀,Ctrl+C 干净退出
- 根目录 `./dev-stop.sh` 兜底清理 8000/5173 端口
- README 新增「Web 工作台(v0.1)」章节,原命令行内容保留为「命令行模式(原 v0.0 工作流)」
- 脚本可执行权限正确设置,克隆即可运行

**非目标:**
- 不做生产模式(backend serve frontend/dist 静态文件)—— 留给 v0.2
- 不做 Docker / docker-compose 封装 —— v0.1 单机个人用,shell 脚本足够
- 不做 Windows 支持 —— 用户平台 macOS,Windows 用户可手起两个终端
- 不做日志文件持久化 —— dev 模式直接看终端输出,需要查历史日志用 `./dev.sh 2>&1 | tee dev.log`
- 不动后端 / 前端业务代码 —— 仅工程化封装
- 不做 .env 自动生成 —— 用户从 `.env.example` 复制,实盘 API key 用户自己填

## 决策

### 决策 1:单脚本前台运行 + trap 清理,不用后台 daemon

- **选择**:`dev.sh` 前台启动两个子进程,`trap 'kill $BACKEND_PID $FRONTEND_PID; wait' SIGINT SIGTERM`,Ctrl+C 同时停两个
- **替代方案 A**:启动为后台 daemon,写 PID 文件,`dev-stop.sh` 读 PID 停止(类似 `start_nado.sh`)
- **替代方案 B**:用 `tmux` 开两个 pane 各跑一个进程
- **理由**:dev 模式开发者要看实时日志,前台运行日志直接输出最直观;daemon 模式适合生产(v0.2);tmux 引入额外依赖 + 学习成本。Ctrl+C 是开发者本能操作,trap 干净清理即可。

### 决策 2:后端用 `uv run --project backend` 从根目录启动

- **选择**:`uv run --project backend uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload`,CWD 保持根目录
- **替代方案 A**:`cd backend && uv run uvicorn ...` —— pydantic-settings 读 `backend/.env` 不存在,API key 全空(实盘模块虽不阻塞启动但配置错乱)
- **替代方案 B**:symlink `backend/.env → ../.env` —— 污染 backend 目录,git 容易误提交
- **替代方案 C**:改 `backend/app/core/config.py` 用绝对路径读 `.env` —— 改业务代码违反「不动业务」非目标
- **理由**:`uv run --project <path>` 是 uv 0.4+ 原生功能,让 CWD 与 project 解耦;pydantic-settings 从 CWD(根)读 `.env` 拿到真实 API key;backend 独立 uv.lock 仍生效。`--host 127.0.0.1` 避免暴露局域网(v0.1 单机)。

### 决策 3:前端用 `cd frontend && pnpm dev` 子 shell

- **选择**:`(cd frontend && pnpm dev) &`,子 shell 不影响主脚本 CWD
- **替代方案**:`pnpm --dir frontend dev` —— pnpm 0.10+ 支持,但 vite 对 CWD 敏感(vite.config.ts 的相对路径 alias),`--dir` 让 vite 在根目录找配置会出错
- **理由**:子 shell 隔离 CWD 最可靠,vite 在 frontend/ 下正常解析 `vite.config.ts` 与 `index.html`。

### 决策 4:日志合并带前缀,不分离文件

- **选择**:后端 stdout/stderr 直接输出,前端 stdout/stderr 通过 `sed 's/^/[frontend] /'` 加前缀,合并到主终端
- **替代方案 A**:各自重定向到 `logs/backend.log` / `frontend.log`,主终端只打 status
- **替代方案 B**:用 `tee` 同时输出终端 + 文件
- **理由**:dev 模式看终端即可;文件日志会让开发者忘记看;Ctrl+C 后想查历史用 `./dev.sh 2>&1 | tee dev.log` 自己组合。前缀区分来源足够。

### 决策 5:端口检查用 `lsof -ti :PORT`,不用 `nc -z`

- **选择**:启动前 `lsof -ti :8000` 与 `lsof -ti :5173` 检查端口,有输出则占用
- **替代方案 A**:`nc -z 127.0.0.1 8000` —— nc 在 macOS 默认装但 Linux 不一定
- **替代方案 B**:尝试 bind 端口 —— 需写 Python 一行,过度工程化
- **理由**:`lsof` macOS / Linux 都自带;`-ti` 直接拿 PID 列表,可复用给 `dev-stop.sh`;不依赖外部命令。

### 决策 6:`dev-stop.sh` 独立脚本,不集成到 `dev.sh stop`

- **选择**:两个独立脚本 `dev.sh` + `dev-stop.sh`
- **替代方案**:`dev.sh start|stop` 双子命令(类似 `start_nado.sh`)
- **理由**:`dev.sh` 设计为前台运行,Ctrl+C 即停;`dev-stop.sh` 是「异常退出兜底」场景,与 start 是两个心智模型。合并会让用户以为 `./dev.sh stop` 是正常停止方式,实际正常停止就是 Ctrl+C。两个脚本职责清晰。

### 决策 7:README 不删原命令行章节,降级标题保留

- **选择**:原「快速开始」改名为「命令行模式(原 v0.0 工作流)」,内容不动;新增「Web 工作台(v0.1)」放在「概述」之后
- **替代方案 A**:删除原命令行章节 —— 实盘 / 训练 / 进化 CLI 仍是核心功能,Web 工作台只覆盖 4 模块,删了丢失信息
- **替代方案 B**:把命令行内容挪到 docs/ —— 改动范围大,影响外部链接
- **理由**:Web 工作台是 v0.1 增量,原命令行用户不应被强制迁移;保留 + 改名让两类用户各取所需。

## 风险 / 权衡

- **[uv 版本过低不支持 `--project`]** → `uv run --project <path>` 在 uv 0.4+ 支持(2024-08 发布),用户若装的是旧版会报错。缓解:启动前检查 `uv --version`,若 < 0.4 打印升级提示。v0.1 假设用户近期安装的 uv。
- **[Ctrl+C 后子进程未清理]** → uvicorn `--reload` 模式有 reloader 子进程,SIGTERM 主进程后 reloader 可能残留。缓解:`dev-stop.sh` 兜底;`kill 0` 杀进程组(但会杀掉 dev.sh 自身,不采用)。
- **[vite proxy 偶发连接后端失败]** → 后端启动需 1~2s,vite 启动 < 500ms,前端首次访问可能后端未就绪。缓解:vite proxy 失败时浏览器显示 502,刷新即可;不在脚本里加 wait(增加复杂度,v0.1 接受)。
- **[日志前缀混合换行]** → vite 输出多行带 ANSI 颜色,`sed 's/^/[frontend] /'` 每行加前缀,但 ANSI 控制序列可能错位。缓解:dev 模式可读性够用;若严重影响体验 v0.2 加 `chokidar` 或专用 log 工具。
- **[macOS arm64 后端启动后实盘模块报错]** → backend uvicorn 本身可起(无 torch 依赖),但 `POST /live/start` 启动 `live_binance_quant.py` 子进程会立即退出(torch ImportError)。缓解:这是已知限制(v0.1 接受),README 「已知限制」说明,实盘模块用户需 Linux/WSL。
- **[`lsof` 在某些 Linux 发行版未默认安装]** → 极少见,大部分发行版默认装。缓解:若 `command -v lsof` 失败,提示用户安装 `lsof` 包。

## 迁移计划

1. 创建 `dev.sh`(根目录)+ `chmod +x`
2. 创建 `dev-stop.sh`(根目录)+ `chmod +x`
3. 修改 `README.md`:插入「Web 工作台(v0.1)」章节,原「快速开始」改名
4. 验证:`./dev.sh` 启动 → `curl http://127.0.0.1:8000/health` 返 200 → `curl http://localhost:5173/` 返 HTML → Ctrl+C 退出 → `lsof -ti :8000 :5173` 无输出
5. 验证 `dev-stop.sh`:启动后 `kill -9 $BACKEND_PID` 模拟异常 → `./dev-stop.sh` 清理 vite
6. 回滚:`git rm dev.sh dev-stop.sh && git checkout README.md`

## 待解决问题

- 是否需要 Docker 封装?v0.1 不做(单机 shell 足够),v0.2 评估
- 是否需要生产模式 build 脚本?留待 v0.2(backend serve 静态文件)
- 是否需要 Windows .bat / .ps1 脚本?v0.1 不做(用户平台 macOS),v0.2 按需加
