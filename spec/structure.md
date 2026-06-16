# Project Structure

```
<project-root>/
├── backend/             # 后端代码
├── frontend/            # 前端代码
├── prototype/           # 原型设计
├── spec/                # 项目级 spec 文档
│   ├── requirements.md  # 整体需求
│   ├── design.md        # 整体设计
│   ├── tasks.md         # 里程碑级任务清单
│   ├── devlog.md        # 开发日志
│   └── structure.md     # 项目结构（本文件）
├── openspec/            # OpenSpec 管理的需求级 spec
│   ├── config.yaml      # OpenSpec 配置
│   ├── changes/         # 变更记录
│   │   └── archive/     # 已归档的变更
│   └── specs/           # 从变更提炼的长期规格
├── docs/                # 文档资源（README 引用的图片/二维码等）
├── .claude/             # Claude Code 配置与技能
├── .codebuddy/          # CodeBuddy 配置（可选）
├── CLAUDE.md            # Claude Code 工作指引
├── LICENSE              # 开源协议（MIT）
└── README.md            # 项目说明
```

> **维护规则**：仅在**添加或删除顶层目录**时更新本文件。
