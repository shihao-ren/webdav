"""从环境变量 / .env 文件加载配置。

注意：本模块在 import 时不做必填校验（允许无环境变量导入），
所有必填项检查集中在 validate() 中，由服务启动时调用。
"""

import os

from dotenv import load_dotenv

load_dotenv()


def _get(name, default=""):
    return os.environ.get(name, default).strip()


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

    backend = (_get("WEBDAV_BACKEND", "local") or "local").lower()
    root = _get("WEBDAV_ROOT", "./data") or "./data"

    # OSS 配置
    oss_access_key_id = _get("OSS_ACCESS_KEY_ID")
    oss_access_key_secret = _get("OSS_ACCESS_KEY_SECRET")
    oss_endpoint = _get("OSS_ENDPOINT")
    oss_bucket = _get("OSS_BUCKET")

    @classmethod
    def validate(cls):
        if not cls.username:
            raise RuntimeError(
                "缺少必需配置 WEBDAV_USERNAME，请在 .env 文件中设置（参考 .env.example）"
            )
        if not cls.password:
            raise RuntimeError(
                "缺少必需配置 WEBDAV_PASSWORD，请在 .env 文件中设置（参考 .env.example）"
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
