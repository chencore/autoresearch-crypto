# README 中英文切换设计

## 背景

项目已同时存在英文 ([README.md](../../README.md)) 和中文 ([README_CN.md](../../README_CN.md)) 两个 README 文件，需要在两者之间提供便捷的切换入口。

## 目标

- 在 README 顶部提供清晰的中英文切换导航
- 保持简洁，不引入外部依赖（如 shields.io）
- 遵循 GitHub Markdown 渲染的最佳实践

## 方案

采用纯文本链接方式，在标题下方添加语言切换栏：

**README.md 顶部：**
```markdown
<div align="right"><a href="README_CN.md">中文</a></div>
```

**README_CN.md 顶部：**
```markdown
<div align="right"><a href="README.md">English</a></div>
```

## 设计决策

1. **纯文本而非 Badge**：避免外部图片依赖，GitHub 渲染更稳定，移动端友好
2. **右对齐 (`align="right"`)**：放在标题旁不喧宾夺主，视觉上自然
3. **单向链接**：当前语言不显示为链接，仅提供跳转到另一语言的入口，减少视觉噪音
4. **位置**：标题（`# autoresearch-crypto`）正下方，用户进入页面第一眼即可看到

## 验收标准

- [ ] README.md 顶部有指向 README_CN.md 的中文切换链接
- [ ] README_CN.md 顶部有指向 README.md 的 English 切换链接
- [ ] 链接在 GitHub 上正常渲染且可点击
