# AGENTS.md — 面向智能体/二次开发的项目说明

本文件面向**后续接手开发的 AI 智能体或工程师**，记录非显然的架构决策、WsgiDAV/oss2 契约坑与安全约定。按现状更新，先说结论、再给坑。

> 安全提示：这是公开仓库，**不要写真实的部署主机名 / 域名 / 账号清单 / 内网路径 / 端口**。需要记例时一律用 `<deploy-dir>`、`<domain>`、`<svc>` 之类占位。

## 一句话

WsgiDAV + cheroot + oss2 的 WebDAV 服务器，`local`(本地磁盘)/`oss`(阿里云 OSS) 双后端，多账号按服务做前缀隔离。常见部署：公网主机 + nginx HTTPS 反代 + systemd 管理。

## 模块职责

| 文件 | 职责 |
|---|---|
| `config.py` | 加载 `.env` + `accounts.json` 并**启动时校验**（prefix 白名单、账号文件 0600、上传上限）。import 不做必填校验，`validate()` 由服务启动调 |
| `accounts.py` | 纯逻辑：prefix→挂载路径 `mount_realm_of`、账号→realm 用户映射 `build_user_mapping`（admin 追加到所有 realm 全量）。可单测无网络 |
| `server.py` | 装配：每前缀一个 Provider（层次挂载 `/`+`/<prefix>`）、`simple_dc.user_mapping`、启动 cheroot |
| `oss_provider.py` | OSS 后端 DAVProvider，用对象 key 前缀模拟目录，`root_prefix` 实现虚拟根（只暴露桶内该前缀下子树） |

## 多账号隔离机制（关键）

- 采用 **WsgiDAV `SimpleDomainController` 的原生 realm 隔离**，不自写 ACL：它**以挂载路径(share_path)作为 realm**，请求落到哪个 provider 就只认证该 realm 的用户表。
- `build_user_mapping`：每前缀一个 realm 只放该服务账号；admin(prefix 空) 追加到所有 realm。越界访问他人前缀 → 落进对方 realm → 无此用户 → **401**。
- **隔离自洽的关键**：必须在 `config.py` 把 `prefix` 锁死在 `[a-z0-9-]`。原因是 WsgiDAV 路由**按小写匹配 share_path**：若允大写，不同大小写会折叠成同一租户，绕过隔离。
- local 后端某前缀对应磁盘根下同名子目录。

## 关键契约坑（来自实现与线上踩坑，改动前必读）

1. **WsgiDAV 会先 `close()` 再 `end_write()`**：写入流 `_WriteBuffer` 的 `close()` 是 no-op，数据留到 `end_write` 取 `getvalue()` 上传。
2. **文件资源必须覆盖 `support_recursive_move(dest_path)`**（基类实现带 `is_collection` 断言，不覆盖会崩）。
3. **oss2 该版本 `head_object().last_modified` 是 int(epoch 秒)**，不是 datetime——`_last_modified_ts` 已做适配，别改回去。
4. **`WEBDAV_MOUNT_PATH` 只生成 href 前缀，不剥入站请求**：nginx `location X/ { proxy_pass ...:8080/ }` 的尾斜杠剥 `/webdav`。e2e 测多挂载需自行模拟该剥前缀代理。
5. **上传 DoS 防护**在 `OssFile.begin_write`（Content-Length 预检）与 `_WriteBuffer.write`（累计上限、413 + `_failed` 标记，`end_write` 不再上传半截）。上限 `WEBDAV_MAX_UPLOAD_SIZE` 默认 1GiB。**仅 OSS 后端生效**；local 走 WsgiDAV 原生流式写盘。

## 凭证与安全约定（硬约束，违反即目录级违规）

- 真实密码 / OSS AccessKey **只存在部署机器**：`.env`、`<conf>/accounts.json`、各服务 `*.webdav-creds`。本地开发目录与 git **绝不含**任何真实值，一律占位/空/“强密码”。
- `accounts.json` **必须 0600 且属主为服务运行用户**——`config._assert_accounts_file_safe` 启动校验，否则拒绝启动。改动文件权限校验逻辑要保留该强约束。
- 上报/日志/错误对用户**不外泄 OSS 细节**（`_wrap_oss_error` 只把 bucket/key/endpoint 写日志，响应文案中性）。别把这些细节塞回 DAVError 响应体。
- 不要把 `.env`/`accounts.json` 等加进 git；`.gitignore` 已覆盖。

## 配置与部署要点（用占位，勿填真实值）

- 多账号优先；未配 `accounts.json` 时回退 `WEBDAV_USERNAME/PASSWORD` 单账号（桶根），向后兼容。
- OSS 模式 `WEBDAV_BACKEND=oss`，`OSS_ENDPOINT` 不允许 `http://`（AccessKey 明文传输风险，server.py 启动拒绝）。
- 生产部署一般形态：主机上 `<deploy-dir>/` + systemd 服务（专用低权用户，监听回环 `127.0.0.1:PORT`），nginx 反代到 `<domain>/webdav`（location 内 `proxy_cache off;`）。
- **备份脚本原则**：每个服务各持一个**仅自己账号**的 0600 凭据文件，**不得**共享单账号读 `.env`（共享凭证面是最该避免的放大点）。

## 测试

```bash
./venv/bin/python -m pytest tests/ -q            # 59 项：accounts 映射、prefix 校验、_WriteBuffer 上限、对象读取等
./tests/e2e_test.sh http://127.0.0.1:8080 u:p     # 端到端（需服务运行）
```

修改多账号/隔离/上传相关逻辑后，务必补对应单测并跑全量。