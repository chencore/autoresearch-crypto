# Tasks: Add User Authentication

> 实现任务清单。由 `/superpowers:writing-plans` 生成，`/superpowers:executing-plans` 逐项执行。

---

## 1. 数据层

- [x] 1.1 创建 `users` 表 migration
- [x] 1.2 创建 `refresh_token_blacklist` 表 migration
- [x] 1.3 编写 User repository（CRUD by id / email）
- [x] 1.4 单测：repository 基础操作

## 2. 工具类

- [x] 2.1 密码工具 `password.hash(plain)` / `password.verify(plain, hash)`
- [x] 2.2 JWT 工具 `jwt.sign(payload, type)` / `jwt.verify(token, type)`
- [x] 2.3 JWT 密钥从环境变量加载（`JWT_SECRET`，启动时校验非空）
- [x] 2.4 单测：密码哈希一致性、JWT 签发/验证

## 3. 业务服务

- [x] 3.1 AuthService.register(email, password)
- [x] 3.2 AuthService.login(email, password)
- [x] 3.3 AuthService.refresh(refreshToken)
- [x] 3.4 AuthService.revokeRefresh(jti, expiresAt)
- [x] 3.5 单测：每个方法的正向 + 异常路径

## 4. HTTP 层

- [x] 4.1 路由 `POST /api/auth/register`
- [x] 4.2 路由 `POST /api/auth/login`
- [x] 4.3 路由 `POST /api/auth/refresh`（从 Cookie 读取 refresh_token）
- [x] 4.4 鉴权中间件 `requireAuth`
- [x] 4.5 登录限流中间件（5 次/分钟/IP）
- [x] 4.6 错误响应统一格式 + 错误码常量

## 5. 集成测试

- [x] 5.1 完整注册 → 登录 → 受保护接口 happy path
- [x] 5.2 场景 2~4：注册异常路径
- [x] 5.3 场景 6~8：登录异常与限流
- [x] 5.4 场景 9~11：Refresh 异常路径
- [x] 5.5 场景 12~15：鉴权中间件

## 6. 文档 & 归档

- [x] 6.1 更新 `spec/design.md` 中的鉴权章节
- [x] 6.2 `spec/tasks.md` 勾选 `add-user-auth`
- [x] 6.3 `spec/devlog.md` 追加变更记录
- [x] 6.4 运行 `/opsx:archive` 归档本变更

---

**完成状态**：✅ 全部完成（2026-04-16）
