# Development Log

> **维护规则**：每次 PR 合并后，由 AI 自动追加一条记录。
>
> 每条记录应包含：日期、变更名、摘要、关键决策/坑点。

---

## Entry Template

```markdown
### YYYY-MM-DD · <change-name>

**摘要**：一句话说清楚这次变更做了什么。

**关键决策**：
- 决策点 1 — 选了什么、放弃了什么、为什么
- 决策点 2 — ...

**踩坑 / 经验**：
- 坑点描述 + 如何解决（可选）

**相关产出**：
- 归档位置：`openspec/changes/archive/<change-name>/`
- PR：#xxx（如适用）
```

---

## Log Entries

<!-- 最新条目在最上面 -->

### 2026-07-03 · v0.1-kickoff

**摘要**：版本 v0.1 kickoff，确立 v0.1 为单机本地量化工作台，在现有 `dex/` 交易核心之上新增 FastAPI + Vue 3 Web 界面。

**关键决策**：
- v0.1 范围聚焦四个 MVP 模块：策略管理（只读）、回测可视化、实盘监控面板、参数调优 / 进化
- 技术栈：FastAPI + SQLite + Vue 3/Vite/Naive UI + WebSocket
- 实盘用 subprocess 拉起 `live_*.py`，不动现有交易核心
- 策略参数 v0.1 只读，编辑留给 v0.2
- 回测结果 v0.1 不持久化，关闭即丢
- 明确非目标：多用户 / 鉴权 / SaaS / 移动端 / 自定义数据上传

**相关产出**：
- `spec/requirements.md`：新增 R-v0.1-ck-1 ~ R-v0.1-ck-9
- `spec/tasks.md`：新增 `## 版本 v0.1`，拆分 11 个 tasks
- `spec/design.md`：填入技术栈、系统架构、模块划分、数据模型、5 项关键决策

---

### 2026-04-16 · bootstrap-speccoding-template

**摘要**：从 SpecCoding Template 初始化项目骨架。

**关键决策**：
- 采用「两级 Spec 体系」：`spec/` 管全局、`openspec/` 管单次变更
- 开发工作流固化为七阶段：git branch → scaffold → brainstorm → plan → execute → archive → merge

**相关产出**：
- 项目级 spec 文档骨架（requirements / design / tasks / devlog / structure）
- OpenSpec 配置 + 示例归档变更 `example-add-user-auth`
