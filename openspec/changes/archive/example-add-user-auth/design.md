# Design: Add User Authentication

## 架构概览

```
[ Client ] ──POST /api/auth/login──▶ [ Auth Controller ]
                                           │
                                           ▼
                                    [ Auth Service ]
                                           │
                         ┌─────────────────┼─────────────────┐
                         ▼                 ▼                 ▼
                   [ User Repo ]     [ JWT Util ]      [ Password Util ]
                         │                                   │
                         ▼                                   ▼
                    [ Database ]                     [ bcrypt (cost=12) ]
```

## 关键决策

### 1. JWT vs Session —— 选 JWT

**选择**：JWT（双 Token：Access + Refresh）

**放弃的方案**：
- Server-side Session + Redis：需要额外基础设施，且本项目无横向扩展需求
- Long-lived JWT：安全性差，被窃取难以撤销

**理由**：双 Token 模式在无状态 + 可撤销之间取得平衡，是主流 SaaS 的标配。

### 2. 密码哈希 —— bcrypt cost 12

**选择**：bcrypt，cost 因子 12

**理由**：
- bcrypt 久经考验，主流语言有成熟实现
- cost=12 在 2026 年仍能提供足够安全性（~250ms/次），可接受的登录延迟
- argon2 更现代但生态成熟度略逊

### 3. Token 过期时间

| Token | 过期 | 存储位置 | 撤销方式 |
|-------|------|---------|---------|
| Access Token | 15 min | HTTP Header | 自然过期 |
| Refresh Token | 7 days | HttpOnly Cookie | DB 黑名单 |

**理由**：Access 短期降低泄漏影响，Refresh 长期减少登录频次，撤销通过 DB 黑名单实现。

## 数据模型变更

### 新增：`users` 表

```sql
CREATE TABLE users (
  id            BIGSERIAL PRIMARY KEY,
  email         VARCHAR(255) UNIQUE NOT NULL,
  password_hash VARCHAR(255) NOT NULL,
  created_at    TIMESTAMP NOT NULL DEFAULT NOW(),
  updated_at    TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_users_email ON users(email);
```

### 新增：`refresh_token_blacklist` 表

```sql
CREATE TABLE refresh_token_blacklist (
  jti        VARCHAR(64) PRIMARY KEY,
  expires_at TIMESTAMP NOT NULL
);

CREATE INDEX idx_blacklist_expires ON refresh_token_blacklist(expires_at);
```

## 接口契约

### `POST /api/auth/register`

**请求**：
```json
{ "email": "user@example.com", "password": "string(>=8)" }
```

**响应 201**：
```json
{ "user": { "id": 1, "email": "user@example.com" } }
```

**错误**：
- 400 邮箱格式非法 / 密码不满足强度
- 409 邮箱已注册

### `POST /api/auth/login`

**请求**：`{ "email", "password" }`

**响应 200**：
```json
{
  "access_token": "eyJ...",
  "refresh_token": "eyJ...",
  "expires_in": 900
}
```

**错误**：
- 401 邮箱或密码错误（统一错误文案，防止账号枚举）

### `POST /api/auth/refresh`

**请求**：Cookie 中的 `refresh_token`

**响应 200**：新的 access_token

**错误**：
- 401 refresh_token 无效、过期或已黑名单

## 风险与缓解

| 风险 | 缓解 |
|------|------|
| 暴力破解登录 | 登录接口加 rate limit（5 次/分钟/IP） |
| JWT 密钥泄漏 | 密钥存环境变量，不入仓库；轮换机制待后续变更 |
| Refresh Token 被盗 | HttpOnly + Secure Cookie，无法被 JS 读取 |
| 密码弱 | 注册时强制校验：最少 8 位，需含字母+数字 |

## 非目标

- 第三方登录（OAuth / Google / GitHub）
- 多因素认证（TOTP / SMS）
- 邮箱验证（注册即可用）
- 密码重置
