## 1. 启动脚本 dev.sh

- [x] 1.1 创建根目录 `dev.sh`,shebang `#!/usr/bin/env bash`,set `-euo pipefail`
- [x] 1.2 实现预检查函数 `check_commands()`:
  - `command -v uv >/dev/null` 缺失则 `echo '错误: 未找到 uv,请先安装: https://docs.astral.sh/uv/' >&2; exit 1`
  - `command -v pnpm >/dev/null` 缺失则 `echo '错误: 未找到 pnpm,请先安装: https://pnpm.io/installation' >&2; exit 1`
  - `command -v lsof >/dev/null` 缺失则 `echo '错误: 未找到 lsof' >&2; exit 1`
- [x] 1.3 实现预检查函数 `check_ports()`:
  - `lsof -ti :8000` 有输出 → `echo '错误: 端口 8000 被占用' >&2; lsof -i :8000 >&2; echo '提示: 请先执行 ./dev-stop.sh 或手动 kill' >&2; exit 1`
  - `lsof -ti :5173` 有输出 → 同上针对 5173
- [x] 1.4 实现预检查函数 `check_env()`:
  - `[ -f .env ] || echo '警告: 未找到 .env 文件,实盘交易功能将不可用,回测/进化可正常使用' >&2`(不退出)
- [x] 1.5 实现启动逻辑:
  - `echo '启动后端 (uvicorn 8000)...'`
  - `uv run --project backend uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload 2>&1 | sed 's/^/[backend] /' &`
  - `BACKEND_PID=$!`
  - `echo '启动前端 (vite 5173)...'`
  - `(cd frontend && pnpm dev) 2>&1 | sed 's/^/[frontend] /' &`
  - `FRONTEND_PID=$!`
- [x] 1.6 实现 trap 清理:
  - `cleanup() { echo; echo '停止子进程...'; kill "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null || true; wait "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null || true; echo '已停止'; exit 0 }`
  - `trap cleanup SIGINT SIGTERM`
- [x] 1.7 实现启动后提示:
  - `sleep 1`(让两个进程初步起来)
  - `echo '═══════════════════════════════════════════════════'`
  - `echo 'Web 工作台已启动'`
  - `echo '  前端: http://localhost:5173'`
  - `echo '  后端: http://127.0.0.1:8000/health'`
  - `echo '  停止: Ctrl+C'`
  - `echo '═══════════════════════════════════════════════════'`
- [x] 1.8 末尾 `wait` 阻塞主进程,子进程退出时 trap 触发

## 2. 停止脚本 dev-stop.sh

- [x] 2.1 创建根目录 `dev-stop.sh`,shebang `#!/usr/bin/env bash`,set `-euo pipefail`
- [x] 2.2 实现停止逻辑:
  - `STOPPED=0`
  - 对 PORT in 8000 5173 循环:
    - `PIDS=$(lsof -ti :$PORT 2>/dev/null || true)`
    - 若 `PIDS` 为空 → continue
    - `echo "停止端口 $PORT 上的进程: $PIDS"`
    - 对每个 PID 发 `kill -TERM`(`for pid in $PIDS; do kill -TERM "$pid" 2>/dev/null || true; done`)
    - `sleep 3`
    - 对仍存活的 PID 发 `kill -KILL`(`for pid in $PIDS; do kill -0 "$pid" 2>/dev/null && kill -KILL "$pid" 2>/dev/null && echo "强制 kill 端口 $PORT 上的进程: $pid" || true; done`)
    - `STOPPED=1`
  - `[ $STOPPED -eq 0 ] && echo '端口 8000/5173 均空闲'`
  - `exit 0`

## 3. 可执行权限

- [x] 3.1 `chmod +x dev.sh dev-stop.sh`
- [x] 3.2 `git ls-files -s dev.sh dev-stop.sh` 确认 mode 为 100755(若 100644 则 `git update-index --chmod=+x`)

## 4. README 更新

- [x] 4.1 在 `README.md`「概述」章节之后(第 17 行后,「## 功能特性」之前)插入「## Web 工作台(v0.1)」章节:
  - 一句话定位:单机本地量化工作台,浏览器访问,无鉴权
  - 4 模块入口列表(策略管理 / 回测可视化 / 实盘监控 / 参数调优)
  - 前置条件:根环境 `uv sync`(实盘依赖)+ `cd backend && uv sync` + `cd frontend && pnpm install`
  - 启动:`./dev.sh` → 浏览器访问 `http://localhost:5173`
  - 停止:`Ctrl+C` 或 `./dev-stop.sh`
  - 已知限制:单机 / 无鉴权 / 回测不持久化 / 实盘依赖根环境 torch(arm64 macOS 装不上)
- [x] 4.2 把原「## 快速开始」章节标题改为「## 命令行模式(原 v0.0 工作流)」,内容不动
- [x] 4.3 在「Web 工作台」章节末尾加一行跳转:`> 命令行模式(原 search_eth_optimal.py / live_*.py / backtest_quant.py 工作流)见 [下方章节](#命令行模式原-v00-工作流)`

## 5. 验证

- [x] 5.1 `./dev.sh` 启动成功:终端输出 `[backend] INFO: Uvicorn running on http://127.0.0.1:8000` + `[frontend] VITE v5.x ready in xxx ms` + 启动横幅
- [x] 5.2 `curl -s http://127.0.0.1:8000/health` 返 `{"status":"ok","version":"0.1.0"}`
- [x] 5.3 `curl -s http://localhost:5173/` 返 HTML(`<div id="app">` + script tag)
- [x] 5.4 `curl -s http://localhost:5173/api/v1/strategy` 通过 vite proxy 返策略列表 JSON(验证 proxy 生效)
- [x] 5.5 在 dev.sh 运行终端按 Ctrl+C:输出「停止子进程... 已停止」+ 退出码 0
- [x] 5.6 `lsof -ti :8000 :5173` 无输出(端口已释放)
- [x] 5.7 启动 `./dev.sh` 后,`kill -9 $BACKEND_PID` 模拟异常,执行 `./dev-stop.sh` 输出「停止端口 5173 上的进程」+「端口 8000 均空闲」(或仍显示 8000 PID 已被 kill)
- [x] 5.8 端口被占用场景:启动一个 `python -m http.server 8000` 后执行 `./dev.sh` → 输出「错误: 端口 8000 被占用」+ lsof 输出 + 退出码 1(手动验证后清理 http.server)
- [x] 5.9 README 章节:Visual check「Web 工作台(v0.1)」在「概述」后、「命令行模式」原「快速开始」改名后内容不变(手动)
