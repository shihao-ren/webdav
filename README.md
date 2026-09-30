# WebDAV 服务

> **English**: A WebDAV server based on [WsgiDAV](https://github.com/mar10/wsgidav) with pluggable storage backends — local disk or **Aliyun OSS** (directories emulated via object key prefixes). Pure Python, no Docker. Multi-account support with per-service prefix isolation (realm-based). Originally built for **Zotero attachment sync**, works with any WebDAV client. Ships with unit tests (pytest) and a full end-to-end test script. Personal project — provided as-is, use at your own risk.

基于 [WsgiDAV](https://github.com/mar10/wsgidav) 的 WebDAV 服务器，支持两种存储后端：

- **local（默认）**：文件存服务器本地磁盘
- **oss**：文件存阿里云 OSS（通过对象 key 前缀模拟目录）

支持**多账号按服务拆分权限**：每个服务一个账号，只能读写自己前缀，互不可见（详见下文）。纯 Python，无 Docker，`run.sh` 一键启动。典型用途：**Zotero/Obsidian 同步**、服务器定时备份。

> 个人项目，按现状提供，请自行评估风险后使用。

## 快速开始

```bash
cp .env.example .env   # 改配置（见下）
./run.sh               # 自动创建 venv、装依赖并启动
```

启动后访问 `http://<服务器>:8080/`，用配置的账号密码登录。

## 配置（.env）

| 变量 | 默认 | 说明 |
|---|---|---|
| `WEBDAV_HOST` | `0.0.0.0` | 监听地址 |
| `WEBDAV_PORT` | `8080` | 监听端口 |
| `WEBDAV_BACKEND` | `local` | `local` 或 `oss` |
| `WEBDAV_ROOT` | `./data` | local 存储根目录 |
| `WEBDAV_ACCOUNTS_FILE` | `accounts.json` | 多账号文件路径（推荐，见下） |
| `WEBDAV_MOUNT_PATH` | （空） | 反向代理子路径，如 `/webdav`，留空=挂根路径 |
| `WEBDAV_MAX_UPLOAD_SIZE` | `1073741824` | 单次上传上限（字节，oss 模式强制；0=不限） |
| `WEBDAV_USERNAME` / `WEBDAV_PASSWORD` | 单账号 | **仅**未配多账号时作为根账号回退 |
| `OSS_ACCESS_KEY_ID` / `OSS_ACCESS_KEY_SECRET` | 空 | OSS 凭证（oss 模式必填） |
| `OSS_ENDPOINT` | `oss-cn-hangzhou...` | OSS Endpoint |
| `OSS_BUCKET` | 空 | Bucket 名称（oss 模式必填） |

## 多账号（按服务最小权限，推荐）

> 每个服务一个独立账号，只能读写桶内自己的前缀。单个账号被攻破，波及范围被关进它自己的前缀，不横到其它服务或桶根。

配置文件（默认 `accounts.json`，**权限必须 0600**，已 gitignore）内容为 JSON，每账号一项：

```json
{
  "admin":    { "password": "<强密码>", "prefix": "" },
  "svc-a":    { "password": "<强密码>", "prefix": "svc-a-backups" },
  "svc-b":    { "password": "<强密码>", "prefix": "svc-b-backups" },
  "obsidian": { "password": "<强密码>", "prefix": "obsidian" }
}
```

- `prefix` 为空字符串 = 桶根 admin，全量读写；非空 = 该服务只能读写自己的前缀。
- `prefix` 必须为**小写字母/数字/连字符** `[a-z0-9-]`（服务端启动时校验，拒绝 `\`、`.`、`..`、空格、大写）。
- 配置了多账号后，`WEBDAV_USERNAME/PASSWORD` 单账号设置被忽略。
- 官方 multi-service 用途：每个客户端连 `https://<域名>/webdav/<自己的前缀>`，用对应账号。

## 客户端连接

- **Zotero**（桌面端）：WebDAV URL → `https://<域名>/webdav/<服务前缀>`，账号填对应服务账号。
- **Obsidian**（Remotely Save 之类）：同上，指向自己的前缀。
- **macOS Finder**：⌘K → `http://<服务器>:8080/`
- **Windows**：`\\<服务器IP>@8080\DavWWWRoot\`（或直接 `http://<服务器IP>:8080/`）
- **Linux**：`sudo mount -t davfs http://<服务器>:8080/ /mnt/webdav`

## 生产部署建议

- 用 **nginx/caddy 反代并启用 HTTPS**（Basic Auth 密码明文传输，必须加密）。
- 反向代理子路径挂载：nginx `location /webdav/ { proxy_pass http://127.0.0.1:8080/; }` 的**尾斜杠会剥掉 `/webdav` 前缀**——服务端 `WEBDAV_MOUNT_PATH=/webdav` 只影响生成的 href，不剥入站请求。
- location 内**必须 `proxy_cache off;`**（否则缓冲会剥 `If-Modified-Since` 等条件头）。
- `client_max_body_size` 至少对齐 `WEBDAV_MAX_UPLOAD_SIZE`。
- 可加 `limit_req` 限速与 fail2ban 封禁反复 401 的 IP。

## 测试

```bash
./venv/bin/pip install -r requirements-dev.txt

# 单元测试（纯函数/内部组件，无需网络）
./venv/bin/python -m pytest tests/ -q

# 端到端（需先启动服务；<user:pass> 为任意有效账号）
./run.sh &
./tests/e2e_test.sh http://127.0.0.1:8080 用户名:密码
```

## 项目结构

```
webdav_server/
├── config.py        # 环境变量/accounts.json 配置加载与校验
├── accounts.py      # 多账号 → realm 用户映射（纯逻辑，可单测）
├── server.py        # 入口：组装 Provider + WsgiDAVApp + cheroot、多挂载点
└── oss_provider.py  # 阿里云 OSS 后端（DAVProvider 实现，前缀虚拟根）
```

更多面向二次开发/智能体的说明见 [AGENTS.md](AGENTS.md)。

## 已知限制

- OSS 模式单文件**整段缓冲在内存**后一次性上传，受 `WEBDAV_MAX_UPLOAD_SIZE`（默认 1 GiB）限制；超大文件请用 local 模式或调高上限（不推荐无上限）。
- OSS 模式分段下载用 `byte_range` 重新开流实现 seek，每次 seek 一个额外 HTTP 请求（WsgiDAV 每个 Range 请求只 seek 一次）。
- 锁（LOCK）为内存存储，多进程部署不共享。