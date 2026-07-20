# 认证接口文档

更新日期：2026-07-20

## 变更摘要

Python FastAPI 接管原 Spring `AuthController` 的对外认证接口。接口路径、Bearer Token 传递方式、业务错误信封和 PostgreSQL 会话表均保持兼容，前端无需修改。

所有认证接口返回统一信封：

```json
{
  "code": 1,
  "msg": null,
  "data": {}
}
```

`code=1` 表示成功，`code=0` 表示可预期的业务或参数错误；这两类响应均使用 HTTP `200`，与原 Java `Result<T>` 一致。网关不可达等 HTTP 层故障仍由 HTTP 状态码表达。

## 运行配置与数据边界

- 数据库 URL 按 `AUTH_DATABASE_URL`、`RAG_DATABASE_URL`、`DATABASE_URL` 的顺序读取。
- 数据库 schema 读取 `RAG_DATABASE_SCHEMA`，默认 `learning_evidence`。
- 依赖既有表：`app_user`、`auth_session`、`auth_login_record`，不创建或迁移表结构。
- Token 只以 SHA-256 十六进制哈希保存到 `auth_session.token_hash`；响应中只返回一次原始 Token。
- 密码校验兼容 Java：读取 `password_algorithm`、`password_salt`、`password_iterations`，默认采用 `PBKDF2WithHmacSHA256`、120000 次迭代、256 bit 派生结果和小写十六进制存储值。

## 接口列表

| 方法 | 路径 | 鉴权 | 用途 |
| --- | --- | --- | --- |
| `POST` | `/api/auth/login` | 否 | 校验账号密码并创建数据库会话 |
| `GET` | `/api/auth/me` | `Authorization: Bearer <token>` | 查询当前有效用户 |
| `POST` | `/api/auth/logout` | 可选 `Authorization: Bearer <token>` | 撤销当前会话 |

## POST /api/auth/login

请求体：

```json
{
  "account": "admin",
  "password": "123456",
  "remember": true
}
```

- `account`、`password` 均不能为空。
- `remember` 缺省为 `true`；为 `true` 时会话有效期为 30 天，为 `false` 时为 12 小时。
- 账号会先去除首尾空白并按 Unicode 小写规范化，与 Java `Locale.ROOT` 小写处理的用途一致。
- 请求的 `X-Forwarded-For` 首个地址优先写入登录记录，否则记录客户端地址；`User-Agent` 也会被记录。

成功示例：

```json
{
  "code": 1,
  "msg": null,
  "data": {
    "token": "base64url-token",
    "expiresAt": "2026-08-19T10:30:00",
    "user": {
      "id": 1,
      "account": "admin",
      "displayName": "系统管理员",
      "email": "admin@example.com",
      "role": "ADMIN",
      "loginAt": "2026-07-20T10:30:00"
    }
  }
}
```

失败示例：

```json
{
  "code": 0,
  "msg": "账号或密码错误",
  "data": null
}
```

错误消息：`账号不能为空`、`密码不能为空`、`账号或密码错误`、`账号已停用`、`认证服务暂不可用`。

## GET /api/auth/me

请求头：

```http
Authorization: Bearer base64url-token
```

`Bearer ` 前缀可省略，服务会按原 Java 规则提取 Token。会话不存在、已撤销、已过期，或用户不存在/已停用时返回：

```json
{
  "code": 0,
  "msg": "登录状态已失效",
  "data": null
}
```

成功时 `data` 为登录响应中的 `user` 对象。

## POST /api/auth/logout

请求头可携带当前 Token。缺少、未知或已撤销 Token 均幂等返回成功：

```json
{
  "code": 1,
  "msg": null,
  "data": null
}
```

## 测试替换边界

路由通过 FastAPI 的 `get_auth_service` 依赖创建认证服务。测试可使用 `app.dependency_overrides[get_auth_service]` 注入内存仓储服务，无需 PostgreSQL；生产环境默认仓储使用 `psycopg` 和上述既有表。
