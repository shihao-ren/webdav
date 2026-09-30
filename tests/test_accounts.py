"""多账号：账号文件解析 + 账号→realm 映射（纯逻辑，无网络/OSS 依赖）。"""

import pytest

from webdav_server import accounts
from webdav_server.config import _parse_accounts


class TestMountRealm:
    def test_root(self):
        assert accounts.mount_realm_of("") == "/"

    def test_none(self):
        assert accounts.mount_realm_of(None) == "/"

    def test_normal(self):
        assert accounts.mount_realm_of("vault-backups") == "/vault-backups"

    def test_slashes_trimmed(self):
        assert accounts.mount_realm_of("/kg-viewer-backups/") == "/kg-viewer-backups"


class TestParseAccounts:
    def test_missing_file_returns_none(self, tmp_path):
        assert _parse_accounts(str(tmp_path / "none.json")) is None

    def test_empty_object_returns_none(self, tmp_path):
        p = tmp_path / "a.json"
        p.write_text("{}")
        assert _parse_accounts(str(p)) is None

    def test_valid(self, tmp_path):
        p = tmp_path / "a.json"
        p.write_text(
            '{"admin":{"password":"x","prefix":""},'
            '"kv":{"password":"y","prefix":"kg-viewer-backups"}}'
        )
        a = _parse_accounts(str(p))
        assert a["admin"] == {"password": "x", "prefix": ""}
        assert a["kv"] == {"password": "y", "prefix": "kg-viewer-backups"}

    def test_prefix_slashes_trimmed(self, tmp_path):
        p = tmp_path / "a.json"
        p.write_text('{"u":{"password":"p","prefix":"/x/"}}')
        assert _parse_accounts(str(p))["u"]["prefix"] == "x"

    def test_missing_password_raises(self, tmp_path):
        p = tmp_path / "a.json"
        p.write_text('{"u":{"prefix":"x"}}')
        with pytest.raises(RuntimeError):
            _parse_accounts(str(p))

    def test_empty_password_raises(self, tmp_path):
        p = tmp_path / "a.json"
        p.write_text('{"u":{"password":"","prefix":"x"}}')
        with pytest.raises(RuntimeError):
            _parse_accounts(str(p))

    def test_prefix_with_slash_raises(self, tmp_path):
        p = tmp_path / "a.json"
        p.write_text('{"u":{"password":"p","prefix":"a/b"}}')
        with pytest.raises(RuntimeError):
            _parse_accounts(str(p))

    def test_top_level_not_object_raises(self, tmp_path):
        p = tmp_path / "a.json"
        p.write_text("[1, 2]")
        with pytest.raises(RuntimeError):
            _parse_accounts(str(p))


class TestBuildUserMapping:
    ACCOUNTS = {
        "admin": {"password": "a", "prefix": ""},
        "kgviewer": {"password": "k", "prefix": "kg-viewer-backups"},
        "vault": {"password": "v", "prefix": "vault-backups"},
    }

    def test_service_realm_isolated_plus_admin(self):
        u = accounts.build_user_mapping(self.ACCOUNTS)
        assert u["/kg-viewer-backups"] == {
            "kgviewer": {"password": "k"},
            "admin": {"password": "a"},
        }
        assert "vault" not in u["/kg-viewer-backups"]

    def test_root_realm_has_admin_only(self):
        u = accounts.build_user_mapping(self.ACCOUNTS)
        assert u["/"] == {"admin": {"password": "a"}}
        assert "kgviewer" not in u["/"]

    def test_no_admin(self):
        u = accounts.build_user_mapping(
            {"kgviewer": {"password": "k", "prefix": "kg-viewer-backups"}}
        )
        assert u["/kg-viewer-backups"] == {"kgviewer": {"password": "k"}}

    def test_admin_is_full_on_all_realms(self):
        u = accounts.build_user_mapping(self.ACCOUNTS)
        for realm, users in u.items():
            assert "admin" in users