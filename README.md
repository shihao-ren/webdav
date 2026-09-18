# WebDAV 服务

基于 [WsgiDAV](https://github.com/mar10/wsgidav) 的 WebDAV 服务器，支持两种存储后端：

- **local（默认）**：文件存服务器本地磁盘
- **oss**：文件存阿里云 OSS（通过对象 key 前缀模拟目录）

纯 Python 实现，无需 Docker，`run.sh` 一键启动。

## 快速开始

```bash
cp .env.example .env   # 修改用户名/密码等配置
./run.sh               # 自动创建 venv、装依赖并启动
```

服务启动后访问 `http://<服务器IP>:8080/`，用配置的用户名密码登录。

## 配置（.env）

| 变量 | 默认 | 说明 |
|---|---|---|
| `WEBDAV_HOST` | `0.0.0.0` | 监听地址 |
| `WEBDAV_PORT` | `8080` | 监听端口 |
| `WEBDAV_USERNAME` / `WEBDAV_PASSWORD` | 必填 | Basic Auth 认证 |
| `WEBDAV_BACKEND` | `local` | `local` 或 `oss` |
| `WEBDAV_ROOT` | `./data` | 本地存储目录 |
| `OSS_ACCESS_KEY_ID` / `OSS_ACCESS_KEY_SECRET` | 空 | OSS 凭证（oss 模式必填） |
| `OSS_ENDPOINT` | `oss-cn-hangzhou.aliyuncs.com` | OSS Endpoint |
| `OSS_BUCKET` | 空 | Bucket 名称（oss 模式必填） |

## 客户端连接

- **macOS Finder**：菜单 → 前往 → 连接服务器（⌘K），输入 `http://<服务器IP>:8080/`
- **Windows**：资源管理器地址栏输入 `\\<服务器IP>@8080\DavWWWRoot\`（新版 Win10/11 可直接 `http://<服务器IP>:8080/`）；若提示无法连接，需启用「WebClient 服务」
- **Linux**：`davfs2` 挂载：`sudo mount -t davfs http://<服务器IP>:8080/ /mnt/webdav`
- **手机**：Documents、ES 文件浏览器等支持 WebDAV 的 App

## 生产部署建议

- 用 nginx/caddy 反向代理并开启 **HTTPS**（Basic Auth 明文传输密码，必须加密）
- 开启访问限速（防 Basic Auth 暴力破解）。nginx 示例：
  ```nginx
  limit_req_zone $binary_remote_addr zone=webdav:10m rate=10r/s;
  server {
      location / {
          limit_req zone=webdav burst=20 nodelay;
          proxy_pass http://127.0.0.1:8080;
          proxy_set_header Host $host;
          client_max_body_size 1024m;   # 允许大文件上传
      }
  }
  ```
- 用 fail2ban 封禁反复 401 的 IP。`/etc/fail2ban/filter.d/webdav.conf`：
  ```ini
  [Definition]
  failregex = ^.*" [A-Z]+ .*" 401 .*$
  ```
  `/etc/fail2ban/jail.local`：
  ```ini
  [webdav]
  enabled  = true
  port     = http,https
  filter   = webdav
  logpath  = /var/log/nginx/access.log
  maxretry = 5
  findtime = 600
  bantime  = 3600
  ```
- 用 systemd 管理服务：

```ini
[Unit]
Description=WebDAV Server
After=network.target

[Service]
WorkingDirectory=/home/hirsh/project-new/webdav
EnvironmentFile=/home/hirsh/project-new/webdav/.env
ExecStart=/home/hirsh/project-new/webdav/venv/bin/python -m webdav_server.server
Restart=always

[Install]
WantedBy=multi-user.target
```

## 测试

```bash
./venv/bin/pip install -r requirements-dev.txt

# 单元测试（纯函数/内部组件，无需网络）
./venv/bin/python -m pytest tests/ -q

# 端到端测试（需先启动服务）
./run.sh &
./tests/e2e_test.sh http://127.0.0.1:8080 用户名:密码
```

## 项目结构

```
webdav_server/
├── config.py        # 环境变量配置加载
├── server.py        # 入口：组装 Provider + WsgiDAVApp + cheroot
└── oss_provider.py  # 阿里云 OSS 后端（DAVProvider 实现）
```

## 已知限制

- OSS 模式上传文件整体缓冲在内存后一次性上传，超大文件（GB 级）请使用本地模式或后续接入分片上传
- OSS 模式分段下载通过 `byte_range` 重新开流实现 seek，一次 seek 产生一个额外 HTTP 请求（WsgiDAV 每个 Range 请求只 seek 一次）
- 锁（LOCK）使用内存存储，多进程部署时不共享
