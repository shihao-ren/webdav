"""从环境变量 / .env 文件加载配置。

注意：本模块在 import 时不做必填校验（允许无环境变量导入），
所有必填项检查集中在 validate() 中，由服务启动时调用。
"""

import json
import os

from dotenv import load_dotenv

load_dotenv()


def _get(name, default=""):
    return os.environ.get(name, default).strip()


def _parse_accounts(path):
    """解析 accounts 账号文件，返回 {user: {'password':…, 'prefix':…}}。

    文件不存在或内容为空字典则返回 None（调用方退回单账号模式）。
    文件存在但格式/内容非法则抛 RuntimeError，让配置错误在启动时暴露。
    """
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    if not isinstance(raw, dict):
        raise RuntimeError(f"账号文件 {path!r} 顶层必须是 JSON 对象")
    if not raw:
        return None
    accounts = {}
    for user, conf in raw.items():
        user = str(user).strip()
        if not user:
            raise RuntimeError("账号文件含空用户名")
        if user in accounts:
            raise RuntimeError(f"账号文件用户名重复：{user!r}")
        conf = conf or {}
        password = str(conf.get("password", ""))
        prefix = str(conf.get("prefix", "")).strip().strip("/")
        if not password:
            raise RuntimeError(f"账号 {user!r} 缺少非空 password")
        if "/" in prefix:
            raise RuntimeError(f"账号 {user!r} 的 prefix 不允许含斜杠：{prefix!r}")
        accounts[user] = {"password": password, "prefix": prefix}
    return accounts


class Config:
    host = _get("WEBDAV_HOST", "0.0.0.0") or "0.0.0.0"
    try:
        port = int(_get("WEBDAV_PORT", "8080") or "8080")
    except ValueError:
        raise RuntimeError(
            f"WEBDAV_PORT 必须是整数，当前为 {_get('WEBDAV_PORT')!r}"
        )

    username = _get("WEBDAV_USERNAME")
    password = _get("WEBDAV_PASSWORD")

    # 多账号（按服务最小权限拆分）：账号文件 JSON，见 _parse_accounts。
    # 未配置时退回单账号模式（WEBDAV_USERNAME/PASSWORD）。
    accounts_file = _get("WEBDAV_ACCOUNTS_FILE", "") or os.path.abspath(
        "accounts.json"
    )
    accounts = _parse_accounts(accounts_file)

    backend = (_get("WEBDAV_BACKEND", "local") or "local").lower()
    root = _get("WEBDAV_ROOT", "./data") or "./data"

    # 反向代理子路径挂载（如 nginx location /webdav/），留空表示根路径挂载
    mount_path = _get("WEBDAV_MOUNT_PATH")

    # OSS 配置
    oss_access_key_id = _get("OSS_ACCESS_KEY_ID")
    oss_access_key_secret = _get("OSS_ACCESS_KEY_SECRET")
    oss_endpoint = _get("OSS_ENDPOINT")
    oss_bucket = _get("OSS_BUCKET")

    @classmethod
    def validate(cls):
        if cls.accounts is None:
            # 单账号模式：必须提供 WEBDAV_USERNAME/PASSWORD
            if not cls.username:
                raise RuntimeError(
                    "缺少必需配置 WEBDAV_USERNAME，请在 .env 文件中设置（参考 .env.example）"
                )
            if not cls.password:
                raise RuntimeError(
                    "缺少必需配置 WEBDAV_PASSWORD，请在 .env 文件中设置（参考 .env.example）"
                )
        if cls.mount_path:
            if not cls.mount_path.startswith("/") or cls.mount_path.endswith("/"):
                raise RuntimeError(
                    f"WEBDAV_MOUNT_PATH 必须以 / 开头且不以 / 结尾，当前为 {cls.mount_path!r}"
                )
        if cls.backend not in ("local", "oss"):
            raise RuntimeError(f"WEBDAV_BACKEND 必须是 local 或 oss，当前为 {cls.backend!r}")
        if cls.backend == "oss":
            missing = [
                name
                for name, value in (
                    ("OSS_ACCESS_KEY_ID", cls.oss_access_key_id),
                    ("OSS_ACCESS_KEY_SECRET", cls.oss_access_key_secret),
                    ("OSS_ENDPOINT", cls.oss_endpoint),
                    ("OSS_BUCKET", cls.oss_bucket),
                )
                if not value
            ]
            if missing:
                raise RuntimeError(
                    "WEBDAV_BACKEND=oss 时以下配置必填：" + "、".join(missing)
                )
