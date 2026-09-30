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
        assert accounts.mount_realm_of("svc-b-backups") == "/svc-b-backups"

    def test_slashes_trimmed(self):
        assert accounts.mount_realm_of("/svc-a-backups/") == "/svc-a-backups"


class TestParseAccounts:
    @staticmethod
    def _write(tmp_path, content, mode=0o600):
        p = tmp_path / "a.json"
        p.write_text(content)
        p.chmod(mode)
        return p

    def test_missing_file_returns_none(self, tmp_path):
        assert _parse_accounts(str(tmp_path / "none.json")) is None

    def test_empty_object_returns_none(self, tmp_path):
        p = self._write(tmp_path, "{}")
        assert _parse_accounts(str(p)) is None

    def test_valid(self, tmp_path):
        p = self._write(
            tmp_path,
            '{"admin":{"password":"x","prefix":""},'
            '"kv":{"password":"y","prefix":"svc-a-backups"}}',
        )
        a = _parse_accounts(str(p))
        assert a["admin"] == {"password": "x", "prefix": ""}
        assert a["kv"] == {"password": "y", "prefix": "svc-a-backups"}

    def test_prefix_slashes_trimmed(self, tmp_path):
        p = self._write(tmp_path, '{"u":{"password":"p","prefix":"/x/"}}')
        assert _parse_accounts(str(p))["u"]["prefix"] == "x"

    def test_missing_password_raises(self, tmp_path):
        p = self._write(tmp_path, '{"u":{"prefix":"x"}}')
        with pytest.raises(RuntimeError):
            _parse_accounts(str(p))

    def test_empty_password_raises(self, tmp_path):
        p = self._write(tmp_path, '{"u":{"password":"","prefix":"x"}}')
        with pytest.raises(RuntimeError):
            _parse_accounts(str(p))

    def test_prefix_with_slash_raises(self, tmp_path):
        p = self._write(tmp_path, '{"u":{"password":"p","prefix":"a/b"}}')
        with pytest.raises(RuntimeError):
            _parse_accounts(str(p))

    def test_top_level_not_object_raises(self, tmp_path):
        p = self._write(tmp_path, "[1, 2]")
        with pytest.raises(RuntimeError):
            _parse_accounts(str(p))

    def test_rejects_dot_prefix(self, tmp_path):
        for bad in (".", "..", "a/../b"):
            p = self._write(tmp_path, f'{{"u":{{"password":"p","prefix":"{bad}"}}}}')
            with pytest.raises(RuntimeError, match="prefix"):
                _parse_accounts(str(p))

    def test_rejects_uppercase_prefix(self, tmp_path):
        p = self._write(tmp_path, '{"u":{"password":"p","prefix":"Data"}}')
        with pytest.raises(RuntimeError, match="prefix"):
            _parse_accounts(str(p))

    def test_rejects_special_chars(self, tmp_path):
        p = self._write(tmp_path, '{"u":{"password":"p","prefix":"vault backups"}}')
        with pytest.raises(RuntimeError, match="prefix"):
            _parse_accounts(str(p))

    def test_same_prefix_multi_accounts_ok(self, tmp_path):
        p = self._write(
            tmp_path,
            '{"a":{"password":"p1","prefix":"obsidian"},'
            '"b":{"password":"p2","prefix":"obsidian"}}',
        )
        a = _parse_accounts(str(p))
        assert a["a"]["prefix"] == a["b"]["prefix"] == "obsidian"

    def test_permissive_mode_raises(self, tmp_path):
        p = self._write(tmp_path, '{"u":{"password":"p","prefix":"x"}}', mode=0o644)
        with pytest.raises(RuntimeError, match="0600"):
            _parse_accounts(str(p))


class TestBuildUserMapping:
    ACCOUNTS = {
        "admin": {"password": "a", "prefix": ""},
        "svc-a": {"password": "k", "prefix": "svc-a-backups"},
        "svc-b": {"password": "v", "prefix": "svc-b-backups"},
    }

    def test_service_realm_isolated_plus_admin(self):
        u = accounts.build_user_mapping(self.ACCOUNTS)
        assert u["/svc-a-backups"] == {
            "svc-a": {"password": "k"},
            "admin": {"password": "a"},
        }
        assert "svc-b" not in u["/svc-a-backups"]

    def test_root_realm_has_admin_only(self):
        u = accounts.build_user_mapping(self.ACCOUNTS)
        assert u["/"] == {"admin": {"password": "a"}}
        assert "svc-a" not in u["/"]

    def test_no_admin(self):
        u = accounts.build_user_mapping(
            {"svc-a": {"password": "k", "prefix": "svc-a-backups"}}
        )
        assert u["/svc-a-backups"] == {"svc-a": {"password": "k"}}

    def test_admin_is_full_on_all_realms(self):
        u = accounts.build_user_mapping(self.ACCOUNTS)
        for realm, users in u.items():
            assert "admin" in users