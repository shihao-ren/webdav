"""WebDAV 服务入口：组装 Provider + WsgiDAVApp + cheroot 并启动。"""

import os

from cheroot import wsgi
from wsgidav.fs_dav_provider import FilesystemProvider
from wsgidav.wsgidav_app import WsgiDAVApp

from .config import Config
from .oss_provider import OssProvider


def build_provider():
    """按配置构造存储后端 Provider。"""
    if Config.backend == "local":
        root = os.path.abspath(Config.root)
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
    return OssProvider(auth, endpoint, Config.oss_bucket)


def build_app(provider):
    domain = "webdav"
    config = {
        "host": Config.host,
        "port": Config.port,
        "provider_mapping": {"/": provider},
        "verbose": 1,
        "http_authenticator": {
            "domain": domain,
            "accept_basic": True,
            "accept_digest": False,
            "default_to_digest": False,
        },
        "simple_dc": {
            "user_mapping": {"*": {Config.username: {"password": Config.password}}}
        },
    }
    if Config.mount_path:
        config["mount_path"] = Config.mount_path
    return WsgiDAVApp(config)


def main():
    Config.validate()
    provider = build_provider()
    app = build_app(provider)

    server = wsgi.Server((Config.host, Config.port), app)
    print(f"WebDAV 服务已启动: http://{Config.host}:{Config.port}/")
    print(f"  后端: {Config.backend}" + (f" (bucket={Config.oss_bucket})" if Config.backend == "oss" else f" (root={os.path.abspath(Config.root)})"))
    print(f"  用户: {Config.username}")
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
