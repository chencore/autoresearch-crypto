# Plan: Add User Authentication

> 由 `/superpowers:writing-plans` 生成的**详细实现计划**。
>
> 位置铁律：本文件必须位于 `openspec/changes/<change-name>/plan.md`，与同变更的 proposal / design / specs / tasks 放在一起。**不要**存到仓库根或 `docs/` 等其他位置。

---

## 计划总览

本次实现分 **6 个阶段**，每个阶段产出可独立验证的增量。阶段之间以"**✅ 完成验证**"卡点推进，不跳步。

| 阶段 | 目标 | 关键输出 | 估时 |
|------|------|----------|------|
| S1 | 数据层与 migrations | `users` / `refresh_token_blacklist` 两张表 + repository | 0.5d |
| S2 | 密码 & JWT 工具类 | `password.ts` / `jwt.ts` + 单测 | 0.5d |
| S3 | AuthService 业务层 | register / login / refresh / revoke + 单测 | 1d |
| S4 | HTTP 路由与中间件 | `/api/auth/*` 三个路由 + `requireAuth` + 限流 | 0.5d |
| S5 | 集成测试 | 15 个场景全覆盖 | 0.5d |
| S6 | 文档 & 归档 | 更新 spec/devlog、执行 /opsx:archive | 0.25d |

**总估时**：约 3.25 人日（不含 code review 与返工缓冲）。

---

## S1. 数据层与 Migrations

### 目标
落地 `users` 与 `refresh_token_blacklist` 两张表，完成 User repository。

### 实施步骤

1. 创建 `backend/migrations/20260416_create_users.sql`，内容见 `design.md §数据模型`
2. 创建 `backend/migrations/20260416_create_refresh_blacklist.sql`
3. 本地起 Postgres，跑 `npm run migrate:up`，验证表结构
4. 创建 `backend/src/repositories/user.repository.ts`，实现：
   - `findById(id: bigint): Promise<User | null>`
   - `findByEmail(email: string): Promise<User | null>`
   - `create(data: { email, passwordHash }): Promise<User>`
5. 单测：使用测试库（独立 schema 或内存 SQLite），覆盖上述 3 个方法的正/异常路径

### ✅ 完成验证
- [ ] `npm run migrate:up` 与 `migrate:down` 双向可用
- [ ] `npm test -- user.repository` 全绿
- [ ] 手动 `psql` 查 `users` 表，字段类型与索引符合预期

---

## S2. 密码 & JWT 工具类

### 目标
独立、可测试的纯函数工具，后续 AuthService 直接调用。

### 实施步骤

1. `backend/src/utils/password.ts`：
   - `hash(plain: string): Promise<string>` — bcrypt cost=12
   - `verify(plain: string, hash: string): Promise<boolean>`
2. `backend/src/utils/jwt.ts`：
   - `sign(payload: JwtPayload, type: 'access' | 'refresh'): string`
   - `verify(token: string, type: 'access' | 'refresh'): JwtPayload`
   - 读取环境变量 `JWT_SECRET`，启动时校验非空
   - 常量：`ACCESS_TTL = 15 * 60`，`REFRESH_TTL = 7 * 24 * 3600`
3. 单测：
   - hash 同一明文两次结果不同（salt 正确）；verify 对应通过
   - sign → verify 能拿回相同 payload
   - 过期 token verify 抛 `TokenExpiredError`
   - 被篡改 token verify 抛 `JsonWebTokenError`

### ✅ 完成验证
- [ ] `npm test -- utils` 全绿
- [ ] 无 `JWT_SECRET` 环境变量时服务启动直接 crash（fail-fast）

---

## S3. AuthService 业务层

### 目标
编排数据层 + 工具类，输出业务级 API。

### 实施步骤

1. `backend/src/services/auth.service.ts` 实现：
   - `register(email, password)` → 创建用户，返回 `{ id, email }`
   - `login(email, password)` → 返回 `{ accessToken, refreshToken, expiresIn }`
   - `refresh(refreshToken)` → 返回新 accessToken
   - `revokeRefresh(jti, expiresAt)` → 写入黑名单
2. 错误类型（`src/errors/auth.errors.ts`）：
   - `EmailAlreadyRegisteredError`
   - `InvalidCredentialsError`
   - `RefreshTokenExpiredError`
   - `RefreshTokenRevokedError`
3. 单测（mock repository，真实 utils）：每个方法覆盖正向 + 至少 1 条异常路径

### ✅ 完成验证
- [ ] `npm test -- auth.service` 全绿
- [ ] 密码永远不出现在返回值 / 日志中（手动 grep 确认）

---

## S4. HTTP 路由与中间件

### 目标
对外 API 上线，具备限流与鉴权能力。

### 实施步骤

1. `backend/src/routes/auth.routes.ts`：
   - `POST /api/auth/register` → `AuthController.register`
   - `POST /api/auth/login` → `AuthController.login`
   - `POST /api/auth/refresh` → `AuthController.refresh`
2. `backend/src/middleware/require-auth.ts`：
   - 从 `Authorization: Bearer <token>` 解析 accessToken
   - verify 失败 → 401 `invalid_token` / `missing_token` / `token_expired`
   - 挂载 `req.user = { id }`
3. `backend/src/middleware/rate-limit.ts`：
   - 登录接口 5 次/分钟/IP，用 `express-rate-limit` + 内存存储
4. 错误响应统一格式：`{ error: { code: string, message: string } }`
5. 注册/登录使用 Zod schema 校验入参，失败返回 400 + `weak_password` / `invalid_email_format`

### ✅ 完成验证
- [ ] `curl` 手测完整注册 → 登录 → 访问 `/api/me` → refresh 流程
- [ ] 无 Authorization 访问 `/api/me` 返回 401 `missing_token`
- [ ] 连续失败登录 6 次第 6 次返回 429

---

## S5. 集成测试

### 目标
覆盖 `specs/auth/spec.md` 里的 15 个场景。

### 实施步骤

1. 使用 supertest + 独立测试库
2. 按规格顺序逐条实现：
   - 场景 1~4：注册
   - 场景 5~8：登录（含限流）
   - 场景 9~11：refresh
   - 场景 12~15：鉴权中间件
3. 每个测试用例命名模式 `it('spec scenario N: <描述>')`，便于对照规格

### ✅ 完成验证
- [ ] 15 个场景全部通过
- [ ] 覆盖率报告显示 `src/services/auth.*` 行覆盖 > 80%

---

## S6. 文档 & 归档

### 实施步骤

1. 更新 `spec/design.md` 的「鉴权」章节，指向本次变更的 design.md
2. 在 `spec/tasks.md` 勾选 `- [x] add-user-auth`
3. 在 `spec/devlog.md` 追加一条 2026-04-16 的条目
4. 运行 `/opsx:archive` 将整个 `openspec/changes/add-user-auth/` 移入 `openspec/changes/archive/`
5. `git commit` 并合并到 main，删除 feature 分支

### ✅ 完成验证
- [ ] `openspec/changes/archive/add-user-auth/` 存在且完整（5 份产出物）
- [ ] `openspec/changes/add-user-auth/` 已不存在（已被归档搬走）
- [ ] main 分支包含所有代码变更

---

## 风险与回滚

| 风险 | 触发条件 | 回滚方式 |
|------|----------|----------|
| migration 在生产失败 | 表已存在 / 权限不足 | `npm run migrate:down` → 修复后重跑 |
| JWT 密钥误配置 | 启动时 crash | 补环境变量重启（fail-fast 已生效） |
| bcrypt cost 过高影响登录延迟 | 登录 > 500ms | 调低 cost 到 10（需重新评估安全影响） |

---

## 执行中铁律

1. **每个阶段完成后必须跑"✅ 完成验证"**，全部通过才能进入下一阶段
2. **plan.md 本身不可被 executing-plans 修改**——只可追加"执行笔记"到 devlog.md
3. 如发现设计需要调整，**先修 design.md 再改 plan.md**，不可在实现中静默偏离
