# Spec: Authentication

**规格风格**：场景式（Scenario-based）——每条规格描述一个可验证的业务场景。

---

## 注册（Registration）

### 场景 1：新用户成功注册
- **前置**：邮箱在系统中未被注册
- **动作**：用户提交合法邮箱 + 密码（8 位以上，含字母和数字）
- **预期**：
  - 返回 201，响应包含用户 ID 和邮箱
  - `users` 表新增一条记录，`password_hash` 字段为 bcrypt 哈希（**永远不存明文**）
  - 响应**不包含**密码或哈希字段

### 场景 2：邮箱已被注册
- **前置**：邮箱 `user@example.com` 已存在
- **动作**：再次用该邮箱注册
- **预期**：返回 409，错误消息 `"email_already_registered"`

### 场景 3：邮箱格式非法
- **动作**：提交 `email="notanemail"`
- **预期**：返回 400，错误消息 `"invalid_email_format"`

### 场景 4：密码不满足强度要求
- **动作**：提交密码 `"123"`（太短）或 `"12345678"`（无字母）
- **预期**：返回 400，错误消息 `"weak_password"`，提示最少 8 位并需含字母和数字

---

## 登录（Login）

### 场景 5：合法凭证登录
- **前置**：用户已注册，邮箱密码正确
- **动作**：`POST /api/auth/login`
- **预期**：
  - 返回 200
  - 响应包含 `access_token`（JWT，15 分钟过期）
  - `refresh_token` 通过 HttpOnly + Secure Cookie 下发（7 天过期）
  - JWT Payload 包含 `user_id`、`exp`、`iat`、`jti`

### 场景 6：邮箱不存在
- **动作**：登录邮箱 `nonexistent@example.com`
- **预期**：返回 401，统一错误消息 `"invalid_credentials"`（**不得暴露"邮箱不存在"，防止账号枚举**）

### 场景 7：密码错误
- **动作**：已注册邮箱 + 错误密码
- **预期**：返回 401，错误消息 `"invalid_credentials"`（同场景 6）

### 场景 8：登录限流
- **前置**：同一 IP 在 1 分钟内已失败登录 5 次
- **动作**：再次尝试登录
- **预期**：返回 429，错误消息 `"too_many_attempts"`，Header 包含 `Retry-After`

---

## Token 刷新（Refresh）

### 场景 9：合法 Refresh Token 刷新
- **前置**：用户持有未过期、未黑名单的 refresh_token
- **动作**：`POST /api/auth/refresh`（Cookie 携带 refresh_token）
- **预期**：
  - 返回 200，响应包含新的 `access_token`
  - refresh_token **保持不变**（不做 Rotation，降低实现复杂度）

### 场景 10：Refresh Token 已过期
- **动作**：使用过期 token 请求刷新
- **预期**：返回 401，错误消息 `"refresh_token_expired"`

### 场景 11：Refresh Token 已被撤销
- **前置**：用户触发登出，refresh_token 的 `jti` 已加入 `refresh_token_blacklist`
- **动作**：使用该 token 请求刷新
- **预期**：返回 401，错误消息 `"refresh_token_revoked"`

---

## 受保护接口鉴权（Authorization）

### 场景 12：有效 Access Token 访问受保护接口
- **前置**：用户持有未过期 access_token
- **动作**：请求 `GET /api/me`，Header 含 `Authorization: Bearer <token>`
- **预期**：返回 200，响应包含当前用户信息

### 场景 13：无 Token 访问受保护接口
- **动作**：不带 Authorization Header
- **预期**：返回 401，错误消息 `"missing_token"`

### 场景 14：Access Token 已过期
- **动作**：用过期 token 访问
- **预期**：返回 401，错误消息 `"token_expired"`，提示客户端走 refresh 流程

### 场景 15：Access Token 签名无效
- **动作**：篡改 token 后请求
- **预期**：返回 401，错误消息 `"invalid_token"`
