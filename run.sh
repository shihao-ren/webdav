#!/usr/bin/env bash
# WebDAV 服务启动脚本：创建 venv、安装依赖、加载 .env 并启动
set -e
cd "$(dirname "$0")"

if [ ! -d venv ]; then
    echo "创建虚拟环境..."
    python3 -m venv venv 2>/dev/null || python3 -m venv --without-pip venv
    if ! ./venv/bin/python -m ensurepip -q 2>/dev/null; then
        echo "引导安装 pip..."
        curl -sSL https://bootstrap.pypa.io/get-pip.py | ./venv/bin/python -
    fi
fi

# .env 由 python-dotenv 解析加载（见 webdav_server/config.py），
# 这里不要用 bash source 加载，密码含空格/引号/$ 时会解析失败或注入命令。
if [ ! -f .env ]; then
    echo "提示: 未发现 .env，将使用环境变量/默认配置。可执行 cp .env.example .env 后修改。"
fi

./venv/bin/python -m webdav_server.server
