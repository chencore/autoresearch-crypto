# Proposal: Add User Authentication

> 这是一个**示例变更**，演示完整的 OpenSpec 变更四件套。新任务可以照着这个结构起稿。

---

## 是什么（What）

为系统添加用户认证能力：

- 用户可以通过邮箱 + 密码注册账号
- 用户可以通过邮箱 + 密码登录，获得 JWT Token
- 所有需要身份识别的 API 通过 Token 鉴权
- Token 过期后支持刷新

## 为什么（Why）

当前系统所有接口都是公开访问，无法区分用户、无法做数据隔离、无法支持任何"我的 XXX"类功能。这是 MVP 之后所有业务功能的前置依赖。

**本次变更是后续所有用户相关功能（个人资料、订阅管理、账单等）的基础设施。**

## 范围（Scope）

### 包含
- 用户注册 API `POST /api/auth/register`
- 用户登录 API `POST /api/auth/login`
- Token 刷新 API `POST /api/auth/refresh`
- JWT 签发、验证中间件
- 密码 bcrypt 哈希存储
- User 数据模型（`id`, `email`, `password_hash`, `created_at`）

### 不包含（明确排除）
- 第三方 OAuth 登录（留到后续变更）
- 多因素认证（留到后续变更）
- 密码重置流程（留到 `add-password-reset` 变更）
- 邮箱验证（留到 `add-email-verification` 变更）

## 成功标准

- [x] 新用户可注册并立即登录
- [x] 已注册用户可登录获得有效 Token
- [x] 受保护接口在无 Token / Token 无效时返回 401
- [x] Token 过期时间合理（Access Token 15 min，Refresh Token 7 天）
- [x] 密码永远不以明文存储或返回
- [x] 集成测试覆盖率 > 80%
