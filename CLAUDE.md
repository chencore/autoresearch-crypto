# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概览

这是一个 **SpecCoding 全栈开发模板**，基于 Claude Code + OpenSpec + Superpowers 三件套工作流。

- `backend/` — 后端服务（技术栈自选）
- `frontend/` — 前端（Web / H5 / App 皆可）
- `prototype/` — 原型设计稿
- `spec/` — **项目级** spec 文档（整体需求/设计/结构/进度）
- `openspec/` — **需求级** spec 文档（由 OpenSpec 管理的单个变更）

项目结构详见 `spec/structure.md`。

**使用方式**：克隆本仓库后，先填 `spec/requirements.md` 和 `spec/design.md` 定全局，再在 `spec/tasks.md` 拆任务，之后每个任务走一次「七阶段工作流」（见下文）。

## 协作模式：设计靠人决策，执行尽量自主

把工作切成两段，AI 在两段里采取不同的姿态：

| 阶段 | 对应工具 | AI 姿态 |
|------|----------|---------|
| **方案制定** | brainstorming、writing-plans、需求澄清、架构决策 | **多问、列 tradeoff、设 checkpoint**——关键判断交给人类，不要替人类拍板 |
| **执行落地** | executing-plans、写代码、跑测试、修 bug、走 git/openspec 流程 | **尽量自主推进**，方案确认后不要每一步都请示 |

**执行阶段必须停下来请示**的场景仅限：

1. 实际情况与已确认方案出现冲突（方案里没覆盖的关键分叉）
2. 不可逆 / 高破坏性操作（`git push --force`、`git reset --hard`、删共享分支、改 `main`、改外部状态）
3. 反复尝试同一思路仍失败，需要换方向
4. CLAUDE.md / spec 明确要求人工确认的节点（如 openspec 归档时的 design 提升、版本分支推送远程等）

**一句话**：设计前多问，执行中少问。

## Spec 文档两级体系

不要混淆这两层：

| 层级 | 位置 | 作用 | 何时读写 |
|------|------|------|----------|
| 项目级 | `spec/` | 整体需求、设计、任务清单、开发日志、项目结构 | 见下方「项目级 spec 文档」章节 |
| 需求级 | `openspec/changes/<name>/` | 单个变更的提案、设计、规格、任务 | 进入 OpenSpec 工作流的特定阶段时读取/修改 |

### 项目级 spec 文档（`spec/`）

| 文件 | 作用 |
|------|------|
| `requirements.md` | 项目整体需求（**累积式**：全生命周期一篇文件，按版本标签追加） |
| `design.md` | 项目整体设计、架构决策 |
| `tasks.md` | 项目级任务清单（**按版本分块**，每条 task ↔ 一个 openspec 提案） |
| `devlog.md` | 开发日志 |
| `structure.md` | 项目目录结构 |

**开始工作前**，先读取 `spec/` 下的文件以理解项目全局；动笔修改前，核对下面的维护节奏，避免越权改动。

#### 维护节奏

项目级 spec 仅在两个边界上同步：**版本 kickoff** 和 **openspec 归档**。变更开发过程中不动；openspec 变更内部的 proposal/design/spec/tasks 可自由书写，不污染项目级文档。

**① 版本 kickoff / 追加需求（纯人工口头触发）**

触发语示例："开始做这个版本的 kickoff"、"给当前版本加个需求 …"。需求往往**无法一次说清**，AI 听到触发后**必须先进入讨论阶段，禁止立即修改任何 spec 文档**。

**前置检查（按序执行，任何一步失败立即停）**：

1. **读当前分支名，抽取版本号**——匹配 `^version/(v\d+(\.\d+)*)$`：
   - 匹配成功：版本号 = 捕获组，例如 `v1.2`
   - 匹配失败（在 `main` 或其他非版本分支上）：降级为"无版本"模式，用当日日期作标签，如 `[2026-04-23 新增]`；R-ID 里的版本位也用日期替代（见下）
2. **读发起人缩写**：`git config --get user.initials`
   - 读不到：**问一次**本人想用的缩写（2–4 字母），并建议他执行 `git config --local user.initials <缩写>` 永久保存；未得到缩写不得继续
3. **读 spec 当前状态**：读 `spec/requirements.md` 与 `spec/tasks.md`，判断本版本是**首次 kickoff**（版本块不存在）还是**增量追加**（版本块已存在）
4. **提醒本人 `git pull`**：在动笔前提示"请先 `git pull` 同步版本分支，以降低与他人并发写入的冲突概率"，得到"已 pull / 继续"后才进下一步

**流程**：

1. **讨论与澄清（多轮对话）**：通过问答梳理**本次新增/修订**的需求边界（不要重新讨论已确认过的条目）——哪些是新增、哪些是修订老需求、是否影响架构。允许多轮往返，AI 主动补问遗漏点（场景、约束、优先级、验收标准等）。可配合 `/superpowers:brainstorming` 使用。
2. **确认 checkpoint（本地化）**：AI 汇总**本次**要写入的条目（新增 X 条、修订 Y 条、架构是否动），请**触发人本人**显式确认（"确认" / "OK 写入" 等）。**不需要跨人签字**——版本整体范围由团队自行对齐，AI 不做仲裁。未得到明确确认不得动笔。
3. **批量写入 spec**（确认后一次完成）：
   - `requirements.md`：每条需求前加版本标签 `[v1.2 新增]`（或 `[2026-04-23 新增]`，无版本时）并赋唯一 ID `R-v1.2-<缩写>-<序号>`（无版本时为 `R-2026-04-23-<缩写>-<序号>`，**序号为当前发起人在本版本内已有条目数 + 1**，逐条递增）；若是修订老需求，新条目标签 `[v1.2 修订：取代 <旧 ID>]`，**原条目保留并在其末尾追加"已由 <新 ID> 取代"**（不得直接删改原条目）；文末维护"修订历史"区块汇总版本级变更
   - `tasks.md`：若首次 kickoff，新增二级标题 `## 版本 v1.2` 并在其下拆分 tasks；若增量追加，找到已存在的 `## 版本 v1.2` 并在其下追加 tasks——**不得改动他人已写的 tasks**
   - `design.md`：仅当本次涉及新架构决策时才动
   - `devlog.md`：追加一条本次写入摘要，注明"版本 v1.2 · 由 <缩写> 追加：新增 A/B，修订 C"

**② openspec 归档（`/opsx:archive` 时）**

AI 自动：

1. 在 `tasks.md` 对应 task 勾选 ✅
2. 扫描 `openspec/changes/<name>/design.md`：若含 **跨模块影响 / 新增外部依赖 / 数据模型变更**，**提请人工确认**是否提升到 `spec/design.md`；人工点头才写入，否则跳过
3. feature 合回父分支时追加 `devlog.md`（规则见"开发工作流"章节）

**③ 独立自动维护**

`structure.md` 仅在**添加或删除顶层目录**时即时更新。

## 开发工作流（严格按此流程）

```
git branch → openspec scaffold → brainstorming → writing-plans → executing-plans → openspec archive → git merge
```

### 分支模型（两级）

本项目采用 **"版本分支 + 特性分支"** 的两级模型，类似 release-branch 流：

```
main ── <版本分支>(一批需求) ── feature/<需求A>
                              ── feature/<需求B>
```

- **版本分支**由人工从 `main` 创建，承载一批需求。**命名必须为 `version/v<semver>`**（如 `version/v1.2`、`version/v1.2.3`）；匹配正则 `^version/(v\d+(\.\d+)*)$`。当人工让 AI 建版本分支（如"帮我起个 v1.3 版本分支"）时，AI 必须用此格式创建：`git checkout main && git pull && git checkout -b version/v1.3`，推送远程前须征得确认。
- **feature 分支**从**当前所在分支**拉出——可能是版本分支，也可能直接是 `main`。合并时回到**它被拉出时的那条分支**，不一定是 `main`。
- **版本分支 → `main` 的合并**由人工处理；AI 默认不碰 `main`，除非被显式要求。

### 父分支的识别（显式记录）

第 1 步创建 feature 分支时，必须把父分支作为该分支的元数据显式记下来：

```bash
parent=$(git rev-parse --abbrev-ref HEAD)
git checkout -b feature/<name>
git config branch.feature/<name>.parent "$parent"
```

第 7 步合并时用 `git config --get branch.<current>.parent` 读回目标分支。

**若 feature 分支是手工创建的、读不到 `.parent` 配置**：**必须先向用户确认父分支**，不得靠 reflog / merge-base 自行猜测。确认后用上面的 `git config` 命令补写元数据，再执行合并。

### 流程步骤

| 阶段 | 工具 | 说明 |
|------|------|------|
| 1. 创建分支 | `git checkout -b feature/<name>` + `git config branch.feature/<name>.parent <当前分支>` | 从当前分支拉出，并显式记录父分支 |
| 2. 脚手架 | `openspec-cn new change "<name>"` | 只创建变更目录和 `.openspec.yaml`，不填内容 |
| 3. 设计 | `/superpowers:brainstorming` | 探索设计，产出写入 `openspec/changes/<name>/`（proposal.md、design.md、specs/、tasks.md） |
| 4. 制定计划 | `/superpowers:writing-plans` | **产出的 plan.md 必须写入 `openspec/changes/<name>/plan.md`**，与同变更的 proposal / design / specs / tasks 放在同一目录下，**不要**散落到仓库根或其他位置 |
| 5. 执行 | `/superpowers:executing-plans` | 严格按 `openspec/changes/<name>/plan.md` 执行代码变更 |
| 6. 归档 | `/opsx:archive` | 变更移入 `openspec/changes/archive/`（在合并回父分支前完成） |
| 7. 合并代码 | 读 `git config --get branch.<current>.parent` → `git merge` 回该父分支 → 按上文「维护节奏」规则追加 `devlog.md`（注明父分支名） | 合并目标是**父分支**，不一定是 `main`；删除特性分支 |

## OpenSpec 变更管理

**每个变更的所有产出物必须统一存放在 `openspec/changes/<name>/` 目录下**，禁止散落到仓库其他位置：

- `proposal.md` — 是什么、为什么
- `design.md` — 如何做、架构决策、风险权衡
- `specs/<feature>/spec.md` — 需求规格（场景式）
- `plan.md` — 由 `/superpowers:writing-plans` 生成的详细实现计划（**必须落在本目录**）
- `tasks.md` — 实现任务清单（checkable）

相关命令：
- `openspec-cn new change "<name>"` — 创建变更目录
- `/opsx:apply` — 实施变更
- `/opsx:archive` — 归档变更
