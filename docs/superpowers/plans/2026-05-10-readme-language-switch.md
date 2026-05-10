# README 中英文切换实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 README.md 和 README_CN.md 顶部添加语言切换导航链接

**Architecture:** 在标题行下方添加 HTML div 右对齐链接，README.md 指向 README_CN.md，README_CN.md 指向 README.md。当前语言不显示为链接，减少视觉噪音。

**Tech Stack:** Markdown + HTML

---

## File Structure

| 文件 | 操作 | 说明 |
|------|------|------|
| `README.md` | 修改 | 在标题下方添加中文切换链接 |
| `README_CN.md` | 修改 | 在标题下方添加 English 切换链接 |

---

### Task 1: 在 README.md 添加中文切换链接

**Files:**
- Modify: `README.md:1-3`

- [ ] **Step 1: 修改 README.md 顶部**

  将第 1-3 行从：
  ```markdown
  # autoresearch-crypto

  Autonomous cryptocurrency quantitative trading strategy research framework...
  ```

  改为：
  ```markdown
  # autoresearch-crypto

  <div align="right"><a href="README_CN.md">中文</a></div>

  Autonomous cryptocurrency quantitative trading strategy research framework...
  ```

- [ ] **Step 2: 本地预览验证**

  在 VS Code 或本地 Markdown 预览中检查渲染效果，确认链接可点击且右对齐。

- [ ] **Step 3: Commit**

  ```bash
  git add README.md
  git commit -m "docs: add Chinese language switch link to README"
  ```

---

### Task 2: 在 README_CN.md 添加 English 切换链接

**Files:**
- Modify: `README_CN.md:1-3`

- [ ] **Step 1: 修改 README_CN.md 顶部**

  将第 1-3 行从：
  ```markdown
  # autoresearch-crypto

  自主加密货币量化交易策略研究框架...
  ```

  改为：
  ```markdown
  # autoresearch-crypto

  <div align="right"><a href="README.md">English</a></div>

  自主加密货币量化交易策略研究框架...
  ```

- [ ] **Step 2: 本地预览验证**

  在 VS Code 或本地 Markdown 预览中检查渲染效果，确认链接可点击且右对齐。

- [ ] **Step 3: Commit**

  ```bash
  git add README_CN.md
  git commit -m "docs: add English language switch link to README_CN"
  ```

---

### Task 3: GitHub 线上验证

**Files:**
- None (纯验证)

- [ ] **Step 1: 推送分支到远程**

  ```bash
  git push origin dev-2.0
  ```

- [ ] **Step 2: 在 GitHub 网页验证**

  打开 `https://github.com/<owner>/autoresearch-crypto/blob/dev-2.0/README.md`，确认：
  - 页面右上角显示 `中文` 链接
  - 点击 `中文` 跳转到 `README_CN.md`
  - `README_CN.md` 页面右上角显示 `English` 链接
  - 点击 `English` 跳回 `README.md`

---

## Self-Review

1. **Spec coverage:** 设计方案中的所有要求（纯文本链接、右对齐、双向切换）均已覆盖。
2. **Placeholder scan：** 无 TBD/TODO/"implement later"。
3. **Type consistency：** 不涉及代码类型，HTML 标签在两个文件中一致。
