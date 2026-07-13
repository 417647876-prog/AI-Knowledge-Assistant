# 阶段 2B：认证、角色权限与知识库隔离设计

## 1. 目标

阶段 2B 在现有 FastAPI、PostgreSQL 和 Vue 单页工作台上增加可用于后续公网部署的身份认证与角色权限控制。

本阶段完成后：

- 系统不再允许匿名访问知识库、文档和问答接口。
- 管理员可以创建和管理账号。
- 普通用户只能访问自己的知识库及其文档和问答。
- 管理员可以查看和操作所有用户的知识库。
- 前端可以安全恢复登录、自动刷新短期令牌，并在手机窄屏上正常操作。
- 认证架构兼容未来的同域 HTTPS 和反向代理部署，但本阶段不实际部署公网环境。

## 2. 已确认范围

### 2.1 用户与角色

系统只定义两个角色：

- `admin`：管理所有用户、知识库、文档和问答。
- `user`：只能管理自己的知识库、文档和问答。

系统不提供公开注册。账号只能由管理员创建。

管理员页面支持：

- 查看用户列表；
- 创建用户；
- 启用或停用用户；
- 在 `admin` 与 `user` 之间切换角色；
- 重置用户密码。

本阶段不删除用户，避免级联数据处理和误删除风险。

### 2.2 令牌方案

- Access Token 使用短期 JWT，默认有效期 15 分钟。
- Access Token 只保存在 Pinia 内存中，通过 `Authorization: Bearer <token>` 发送。
- Refresh Token 使用随机生成的不可预测明文，默认有效期 7 天。
- Refresh Token 只通过 HttpOnly Cookie 发送，数据库只保存其哈希值。
- 每次刷新都轮换 Refresh Token，旧令牌立即撤销。

这种设计保留 JWT 的跨进程验证能力，同时让长期会话可以撤销和审计。

## 3. 总体架构

现有分层保持不变，在后端增加认证与授权边界：

```text
Vue 页面
  -> auth store / API client
  -> Vite 或生产反向代理的同域 /api
  -> FastAPI auth dependency
  -> role / ownership authorization service
  -> SQLAlchemy models
  -> PostgreSQL
```

新增模块按职责拆分：

- `core/security.py`：密码哈希、Access JWT、Refresh Token 生成与哈希。
- `auth/service.py`：登录、刷新、退出和会话撤销。
- `auth/schemas.py`：认证请求与响应契约。
- `api/auth_dependencies.py`：当前用户、管理员依赖。
- `authorization/service.py`：知识库归属检查。
- `api/v1/auth.py`：登录相关接口。
- `api/v1/admin_users.py`：管理员用户接口。

不把认证逻辑复制到每个路由中。路由只声明依赖并调用统一服务。

## 4. 数据模型

### 4.1 users

新增 `users` 表：

| 字段 | 类型 | 规则 |
|---|---|---|
| `id` | UUID | 主键 |
| `username` | varchar(50) | 唯一、非空，保存规范化小写值 |
| `password_hash` | text | Argon2id 哈希，绝不保存明文 |
| `role` | varchar(20) | `admin` 或 `user` |
| `is_active` | boolean | 默认 `true` |
| `created_at` | timestamptz | UTC |
| `updated_at` | timestamptz | UTC |

用户名长度为 3–50，只允许英文字母、数字、点、下划线和连字符。密码长度为 12–128 个字符。

密码使用 `pwdlib[argon2]` 的推荐配置。登录时即使用户名不存在，也执行一次虚拟哈希校验，降低通过响应时间枚举账号的风险。

### 4.2 refresh_sessions

新增 `refresh_sessions` 表：

| 字段 | 类型 | 规则 |
|---|---|---|
| `id` | UUID | 主键，也是 Cookie 中令牌的一部分 |
| `user_id` | UUID | 外键关联 `users.id` |
| `token_hash` | char(64) | Refresh Token 的 SHA-256 哈希 |
| `expires_at` | timestamptz | 到期时间 |
| `revoked_at` | timestamptz/null | 撤销时间 |
| `replaced_by_id` | UUID/null | 轮换后的会话 ID |
| `created_at` | timestamptz | 创建时间 |

数据库不保存 Refresh Token 明文。退出、密码重置、账号停用都会撤销该用户的全部有效 Refresh Session。

### 4.3 知识库归属

现有 `knowledge_bases.owner_id` 改为：

- 非空；
- 外键关联 `users.id`；
- 建立查询索引；
- 新建知识库时由当前用户身份赋值，客户端不能指定 `owner_id`。

文档、处理任务和分块继续通过 `knowledge_base_id` 间接继承权限，不重复保存 `owner_id`。

## 5. 配置

新增后端配置：

- `JWT_SECRET_KEY`：生产环境必须显式提供的高强度随机密钥；
- `JWT_ALGORITHM`：固定为受支持的服务端配置，默认 `HS256`，解码时不接受令牌自行声明的算法集合；
- `JWT_ISSUER`；
- `JWT_AUDIENCE`；
- `ACCESS_TOKEN_EXPIRE_MINUTES=15`；
- `REFRESH_TOKEN_EXPIRE_DAYS=7`；
- `REFRESH_COOKIE_SECURE`：本地开发为 `false`，HTTPS 环境为 `true`；
- `TRUSTED_ORIGINS`：刷新和退出接口允许的来源。

JWT 包含 `sub`、`role`、`iat`、`exp`、`iss`、`aud` 和唯一 `jti`。权限判断仍以数据库中的当前用户状态和角色为准，不只相信令牌里的角色。

## 6. 后端接口

### 6.1 认证接口

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/v1/auth/login` | 校验用户名密码，返回 Access Token 并设置 Refresh Cookie |
| POST | `/api/v1/auth/refresh` | 校验并轮换 Refresh Token，返回新 Access Token |
| POST | `/api/v1/auth/logout` | 撤销当前 Refresh Session 并清除 Cookie |
| GET | `/api/v1/auth/me` | 返回当前用户信息 |

登录失败统一返回“用户名或密码错误”，不区分用户不存在、密码错误或账号状态。

Refresh Cookie 建议属性：

- `HttpOnly=true`；
- 生产环境 `Secure=true`；
- `SameSite=Lax`；
- `Path=/api/v1/auth`；
- 设置明确的 `Max-Age`。

刷新和退出接口额外验证 `Origin`。未来部署时，前端与 API 使用同一 HTTPS 域名。

### 6.2 管理员用户接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/admin/users` | 用户列表 |
| POST | `/api/v1/admin/users` | 创建用户 |
| PATCH | `/api/v1/admin/users/{user_id}` | 修改启用状态或角色 |
| POST | `/api/v1/admin/users/{user_id}/reset-password` | 管理员设置新密码 |

保护规则：

- 管理员不能停用自己；
- 系统始终至少保留一个启用的管理员；
- 用户名冲突返回 409；
- 密码重置后撤销目标用户全部 Refresh Session；
- 被停用用户的后续受保护请求立即失败。

### 6.3 现有接口保护

公开接口仅保留：

- `/health`；
- `/ready`；
- OpenAPI/Swagger 文档。

以下接口必须认证：

- 知识库列表和创建；
- 文档上传；
- 文档与任务状态查询；
- 知识库问答。

普通用户访问别人的资源返回 404，避免暴露资源存在性。普通用户调用管理员接口返回 403。

## 7. 权限矩阵

| 操作 | 未登录 | 普通用户 | 管理员 |
|---|---:|---:|---:|
| 健康检查 | 允许 | 允许 | 允许 |
| 登录/刷新 | 允许 | 允许 | 允许 |
| 查看自己的知识库 | 拒绝 | 允许 | 允许 |
| 查看其他用户知识库 | 拒绝 | 404 | 允许 |
| 向自己的知识库上传/提问 | 拒绝 | 允许 | 允许 |
| 向其他用户知识库上传/提问 | 拒绝 | 404 | 允许 |
| 用户管理 | 拒绝 | 403 | 允许 |

管理员创建知识库时默认归属管理员自己。本阶段不增加“替其他用户创建知识库”。

## 8. 前端设计

### 8.1 路由

引入 Vue Router：

- `/login`：登录页；
- `/`：现有知识库工作台；
- `/admin/users`：管理员用户管理；
- `/forbidden`：403 页面。

未登录访问受保护页面时跳转 `/login`。普通用户访问管理员页时跳转 `/forbidden`。

### 8.2 认证状态

新增 `auth` Pinia store，保存：

- 当前 Access Token；
- 当前用户；
- 登录恢复状态；
- 登录和退出方法。

页面首次加载时调用 `/auth/refresh` 恢复会话。API Client 遇到 401 时：

1. 只发起一个共享的刷新请求，避免多个并发请求重复轮换同一个 Refresh Token；
2. 刷新成功后只重放原请求一次；
3. 刷新失败后清空认证状态并跳转登录页；
4. 刷新接口自身不再触发自动刷新，防止循环。

退出或切换账号时，同时清空工作区的知识库、会话文档、问题和答案，防止上一用户的数据留在界面。

### 8.3 页面

- 登录页包含用户名、密码和统一错误提示。
- 工作台顶部显示用户名、角色和退出按钮。
- 管理员用户页支持列表、创建、启停、角色切换和密码重置。
- 普通用户不显示管理员入口。
- 320px 宽度下登录页、工作台和管理员页不得产生横向溢出。

管理员查看知识库列表时显示所有者信息；普通用户只收到自己的知识库。

## 9. 错误契约

沿用现有错误信封、中文消息、错误码和 request ID。新增错误码：

- `INVALID_CREDENTIALS`；
- `TOKEN_EXPIRED`；
- `TOKEN_REVOKED`；
- `ACCOUNT_DISABLED`；
- `AUTHENTICATION_REQUIRED`；
- `PERMISSION_DENIED`；
- `USERNAME_ALREADY_EXISTS`；
- `LAST_ADMIN_REQUIRED`；
- `INVALID_ORIGIN`。

401 可以触发一次自动刷新；403、404、409 不自动重试。

## 10. 本地数据重置与初始化

用户已确认当前本地测试数据可以清空。该操作必须满足：

- 只通过显式开发命令执行；
- 命令要求确认目标数据库连接；
- 不把删除数据写进 Alembic 升级逻辑；
- 迁移检测到仍有无所有者的知识库时明确失败，提示先备份并执行显式重置或回填；
- 重置后执行最新迁移，再通过命令行创建首个管理员；
- 管理员密码通过交互输入或安全环境变量提供，不写入仓库和命令历史示例。

生产和共享环境绝不自动清空。

## 11. 公网部署兼容

阶段 2B 不购买服务器、不配置域名，也不正式部署，但实现需要满足：

- 生产前端静态资源和 `/api` 使用同一 HTTPS 域名；
- Nginx 或 Caddy 终止 TLS，并反向代理到内部 FastAPI；
- PostgreSQL 不暴露公网；
- Uvicorn 和 Vite 开发端口不直接暴露公网；
- 只信任明确配置的代理来源与 `Origin`；
- 生产 Cookie 必须使用 `Secure`。

这样后续手机通过互联网访问时不需要重写登录和刷新流程。

## 12. 测试策略

### 12.1 后端

- 密码哈希与校验；
- JWT 签发、过期、签名、issuer 和 audience 校验；
- Refresh Token 哈希、轮换、重放拒绝和撤销；
- 登录不存在用户时仍执行虚拟哈希校验；
- 停用账号、重置密码和退出后会话失效；
- 最后一个启用管理员保护；
- 用户名唯一与输入边界；
- 普通用户与管理员的完整权限矩阵；
- 文档状态和问答不能绕过知识库归属校验；
- 健康检查保持匿名可用。

### 12.2 前端

- 登录成功与统一失败提示；
- 已登录账号被停用后的状态清理和提示；
- 首次刷新恢复登录；
- 并发 401 只触发一次刷新；
- 原请求最多重放一次；
- 刷新失败跳转登录；
- 403 页面；
- 退出清空认证与工作区；
- 普通用户不显示管理员入口；
- 管理员创建、启停、切换角色和重置密码。

### 12.3 真实浏览器验收

1. 初始化管理员并登录。
2. 管理员创建两个普通用户。
3. 两个用户分别创建知识库、上传文档并提问。
4. 验证普通用户互相看不到对方的知识库。
5. 验证管理员可以查看和操作两者的知识库。
6. 停用一个用户，验证其后续请求立即失败。
7. 页面刷新后通过 Refresh Cookie 恢复会话。
8. 退出后刷新会话失效。
9. 在 320px 浏览器下验证登录、工作台和用户管理页无横向溢出。

## 13. 非目标

阶段 2B 不实现：

- 公开注册；
- 邮件验证和找回密码；
- 首次登录强制改密；
- MFA；
- OAuth/社交登录；
- 三层以上的细粒度角色；
- 用户删除；
- 审计日志；
- 文档删除；
- 流式回答；
- 聊天历史；
- 公网部署。

## 14. 完成标准

- 未登录用户无法访问业务 API。
- 普通用户无法读取或操作其他用户的知识库、文档和问答。
- 管理员可以完成用户管理并操作所有知识库。
- Access Token、Refresh Token 轮换、撤销和退出流程通过测试。
- 账号停用和密码重置能使长期会话失效。
- 前端登录恢复、401 单次刷新、403 和退出清理通过测试。
- 真实浏览器权限隔离和 320px 移动端验收通过。
- 前后端测试、类型检查、构建和 Ruff 检查通过。
- 本地数据重置是显式操作，Alembic 迁移不自动删除业务数据。

## 15. 技术依据

- FastAPI 官方 JWT 安全教程：<https://fastapi.tiangolo.com/tutorial/security/oauth2-jwt/>
- pwdlib 官方文档：<https://frankie567.github.io/pwdlib/>
- PyJWT 官方文档：<https://pyjwt.readthedocs.io/>

FastAPI 官方教程推荐使用带 Argon2 的 `pwdlib` 处理密码哈希。JWT 解码必须使用服务端固定的算法白名单，不能信任令牌头自行决定验证算法。
