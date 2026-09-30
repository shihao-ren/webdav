"""WebDAV 服务入口：组装 Provider + WsgiDAVApp + cheroot 并启动。"""

import os

from cheroot import wsgi
from wsgidav.fs_dav_provider import FilesystemProvider
from wsgidav.wsgidav_app import WsgiDAVApp

from .accounts import build_user_mapping
from .config import Config
from .oss_provider import OssProvider


def _resolved_accounts():
    """多账号配置；未配置时回退到旧的单账号（桶根，全量）。"""
    if Config.accounts is not None:
        return Config.accounts
    if not (Config.username and Config.password):
        return {}
    return {Config.username: {"password": Config.password, "prefix": ""}}


def _provider_for(prefix):
    """按前缀构造存储后端 Provider（虚拟根 = 桶内该前缀子树）。"""
    if Config.backend == "local":
        root = os.path.abspath(Config.root)
        if prefix:
            root = os.path.join(root, prefix)
        os.makedirs(root, exist_ok=True)
        return FilesystemProvider(root)

    # OSS 后端
    import oss2

    endpoint = Config.oss_endpoint
    if endpoint.startswith("http://"):
        raise RuntimeError(
            f"OSS_ENDPOINT 不允许使用 http://（AccessKey 将明文传输），"
            f"请改用 https://：{endpoint.replace('http://', 'https://', 1)}"
        )
    auth = oss2.Auth(Config.oss_access_key_id, Config.oss_access_key_secret)
    return OssProvider(
        auth, endpoint, Config.oss_bucket,
        root_prefix=prefix, max_upload_size=Config.max_upload_size,
    )


def build_provider_mapping(accounts):
    """每个账号的前缀 -> 一个 provider。同前缀复用统一 provider。"""
    mapping = {}
    for conf in accounts.values():
        prefix = conf["prefix"].strip("/")
        realm = "/" + prefix if prefix else "/"
        if realm not in mapping:
            mapping[realm] = _provider_for(prefix)
    return mapping


def build_app():
    domain = "webdav"
    accounts = _resolved_accounts()
    user_mapping = build_user_mapping(accounts) if accounts else {"*": {}}
    config = {
        "host": Config.host,
        "port": Config.port,
        "provider_mapping": build_provider_mapping(accounts),
        "verbose": 1,
        "http_authenticator": {
            "domain": domain,
            "accept_basic": True,
            "accept_digest": False,
            "default_to_digest": False,
        },
        "simple_dc": {"user_mapping": user_mapping},
    }
    if Config.mount_path:
        config["mount_path"] = Config.mount_path
    return WsgiDAVApp(config)


def main():
    Config.validate()
    app = build_app()

    server = wsgi.Server((Config.host, Config.port), app)
    backend = ("OSS" if Config.backend == "oss" else
               f"local({os.path.abspath(Config.root)})")
    n_accounts = len(_resolved_accounts())
    print(f"WebDAV 服务已启动: http://{Config.host}:{Config.port}/")
    print(f"  后端: {backend}")
    print(f"  账号数: {n_accounts}")
    if Config.host not in ("127.0.0.1", "localhost", "::1"):
        print("  ⚠️  警告: Basic Auth 凭据明文传输，对外提供服务必须通过 nginx/caddy 等反向代理启用 HTTPS！")
    try:
        server.start()
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()


if __name__ == "__main__":
    main()