"""从环境变量 / .env 文件加载配置。

注意：本模块在 import 时不做必填校验（允许无环境变量导入），
所有必填项检查集中在 validate() 中，由服务启动时调用。
"""

import json
import os
import re
import stat as _stat

from dotenv import load_dotenv

load_dotenv()


def _get(name, default=""):
    return os.environ.get(name, default).strip()


# WsgiDAV 路由按小写匹配：前缀若含大写，不同大小写会折叠成同一租户。
# 强制 [a-z0-9-]（小写字母数字连字符）同时杜绝 . / .. 穿越与大小写折叠。
_PREFIX_RE = re.compile(r"[a-z0-9][a-z0-9-]*")


def _assert_accounts_file_safe(path):
    """账号文件必须 600（owner rw，他人不可读）；内含明文密码。"""
    try:
        st = os.stat(path)
    except OSError:
        raise RuntimeError(f"账号文件不可读：{path!r}")
    mode = _stat.S_IMODE(st.st_mode)
    if mode & 0o077:  # group/other 有任何权限位
        raise RuntimeError(
            f"账号文件 {path!r} 权限过于宽松（{mode:04o}），"
            f"必须为 0600（chmod 600 {path}）。内含明文密码。"
        )
    if st.st_uid != os.getuid():
        raise RuntimeError(
            f"账号文件 {path!r} 属主不是当前服务用户，建议 0600 且属主改为服务用户。"
        )


def _parse_accounts(path):
    """解析 accounts 账号文件，返回 {user: {'password':…, 'prefix':…}}。

    文件不存在或内容为空字典则返回 None（调用方退回单账号模式）。
    文件存在但格式/内容非法则抛 RuntimeError，让配置错误在启动时暴露。
    """
    if not os.path.exists(path):
        return None
    _assert_accounts_file_safe(path)
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
        if prefix and not _PREFIX_RE.fullmatch(prefix):
            raise RuntimeError(
                f"账号 {user!r} 的 prefix {prefix!r} 非法：必须为小写字母/数字/连字符"
                f"（不含 /、.、..、空格、大写），如 kg-viewer-backups"
            )
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

    # 单次上传大小上限（字节，默认 1 GiB，0=不限制）。超限的 PUT 直接拒绝（DoS 防护）
    try:
        max_upload_size = int(_get("WEBDAV_MAX_UPLOAD_SIZE", "1073741824"))
    except ValueError:
        raise RuntimeError(
            "WEBDAV_MAX_UPLOAD_SIZE 必须是整数（字节），"
            f"当前为 {_get('WEBDAV_MAX_UPLOAD_SIZE')!r}"
        )

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
        if cls.max_upload_size < 0:
            raise RuntimeError(
                f"WEBDAV_MAX_UPLOAD_SIZE 不能为负数，当前为 {cls.max_upload_size}"
            )
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
