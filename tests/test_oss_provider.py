"""纯函数与内部组件的单元测试（无需 OSS 凭证/网络）。

端到端测试见 tests/e2e_test.sh。
"""

import io
from datetime import datetime, timezone
from types import SimpleNamespace

import oss2
import pytest
from wsgidav.dav_error import DAVError

from webdav_server.oss_provider import (
    OssFile,
    _WriteBuffer,
    _last_modified_ts,
    _normalize_path,
    _OssContentReader,
    _path_to_key,
    _path_to_prefix,
    _with_prefix,
    _wrap_oss_error,
)
from wsgidav.dav_error import HTTP_REQUEST_ENTITY_TOO_LARGE


class TestPathHelpers:
    @pytest.mark.parametrize(
        "path,expected",
        [
            ("/", ""),
            ("", ""),
            ("/a/b", "a/b"),
            ("/a/b/", "a/b"),
            ("//a//b//", "a/b"),
            ("/a/b.txt", "a/b.txt"),
        ],
    )
    def test_normalize_path(self, path, expected):
        assert _normalize_path(path) == expected

    def test_path_to_key(self):
        assert _path_to_key("/a/b.txt") == "a/b.txt"
        assert _path_to_key("/") == ""

    def test_path_to_prefix(self):
        assert _path_to_prefix("/a/b") == "a/b/"
        assert _path_to_prefix("/") == ""  # 根目录前缀为空
        assert _path_to_prefix("/a") != "a"  # 目录前缀必须带尾斜杠


class TestWithPrefix:
    def test_no_root_returns_rel(self):
        assert _with_prefix("", "a/b.txt") == "a/b.txt"
        assert _with_prefix("", "") == ""

    def test_root(self):
        assert _with_prefix("svc-a-backups", "a/b.txt") == "svc-a-backups/a/b.txt"
        assert _with_prefix("svc-a-backups", "") == "svc-a-backups"

    def test_root_slashes_trimmed(self):
        assert _with_prefix("/svc-a-backups/", "a") == "svc-a-backups/a"

    def test_prefix_helper(self):
        # 目录前缀纯函数 _dir_prefix：相对路径 + 虚拟根 -> 完整前缀（根加尾斜杠）
        from webdav_server.oss_provider import _dir_prefix

        assert _dir_prefix("", "/a/b") == "a/b/"
        assert _dir_prefix("", "/") == ""              # 无虚拟根时根为 ''
        assert _dir_prefix("root", "/a/b") == "root/a/b/"
        assert _dir_prefix("root/", "/a/b") == "root/a/b/"   # 虚拟根去首尾斜杠
        assert _dir_prefix("root", "/") == "root/"     # 虚拟根目录带尾斜杠


class TestLastModifiedTs:
    def test_int(self):
        head = SimpleNamespace(last_modified=1_700_000_000)
        assert _last_modified_ts(head) == 1_700_000_000.0

    def test_float(self):
        head = SimpleNamespace(last_modified=1_700_000_000.5)
        assert _last_modified_ts(head) == 1_700_000_000.5

    def test_datetime(self):
        dt = datetime(2026, 9, 18, 12, 0, 0, tzinfo=timezone.utc)
        head = SimpleNamespace(last_modified=dt)
        assert _last_modified_ts(head) == dt.timestamp()

    def test_rfc1123_string(self):
        head = SimpleNamespace(last_modified="Fri, 18 Sep 2026 11:00:00 GMT")
        assert _last_modified_ts(head) == datetime(
            2026, 9, 18, 11, 0, 0, tzinfo=timezone.utc
        ).timestamp()

    def test_none(self):
        assert _last_modified_ts(SimpleNamespace(last_modified=None)) is None


class TestWriteBuffer:
    def test_close_保留数据(self):
        buf = _WriteBuffer()
        buf.write(b"abc")
        buf.close()  # WsgiDAV 会在 end_write 前调用 close
        assert buf.getvalue() == b"abc"

    def test_超限抛413并打失败标记(self):
        buf = _WriteBuffer(max_size=5)
        with pytest.raises(DAVError) as ei:
            buf.write(b"0123456")  # 6 > 5
        assert ei.value.value == HTTP_REQUEST_ENTITY_TOO_LARGE
        assert buf._failed

    def test_分块累计超限(self):
        buf = _WriteBuffer(max_size=4)
        buf.write(b"ab")
        with pytest.raises(DAVError):
            buf.write(b"cde")  # 累计 5 > 4
        assert buf._failed

    def test_未超限不触发(self):
        buf = _WriteBuffer(max_size=10)
        buf.write(b"hello")
        assert not buf._failed
        assert buf.getvalue() == b"hello"

    def test_无上限兼容(self):
        buf = _WriteBuffer()
        buf.write(b"x" * 100)
        assert buf.getvalue() == b"x" * 100


def _fake_provider(max_upload_size):
    return SimpleNamespace(
        max_upload_size=max_upload_size,
        _file_key=lambda p: p.strip("/"),
        bucket=SimpleNamespace(
            put_object=lambda *a, **k: _FakeRecordingBucket.calls.append(
                (a, k)
            ),
        ),
    )


class _FakeRecordingBucket:
    calls = []


class TestBeginWriteLimit:
    """begin_write 的 Content-Length 预检（DoS 防护）。"""

    def _open(self, environ):
        f = OssFile("/x.txt", environ)
        return f

    def test_content_length_超限拒绝(self):
        prov = _fake_provider(5)
        f = OssFile("/x.txt", {"wsgidav.provider": prov, "CONTENT_LENGTH": "99"})
        with pytest.raises(DAVError) as ei:
            f.begin_write()
        assert ei.value.value == HTTP_REQUEST_ENTITY_TOO_LARGE
        assert f._write_buf is None  # 未分配缓冲

    def test_content_length_在限内放行(self):
        prov = _fake_provider(5)
        f = OssFile("/x.txt", {"wsgidav.provider": prov, "CONTENT_LENGTH": "3"})
        buf = f.begin_write()
        assert buf is not None

    def test_未声明长度由缓冲兜底(self):
        prov = _fake_provider(5)
        f = OssFile("/x.txt", {"wsgidav.provider": prov})  # 无 CONTENT_LENGTH
        assert f.begin_write() is not None

    def test_无限制时预检放行(self):
        prov = _fake_provider(None)
        f = OssFile("/x.txt", {"wsgidav.provider": prov, "CONTENT_LENGTH": "999"})
        assert f.begin_write() is not None


class TestEndWriteFailed:
    """写入中途超限（_failed）时 end_write 不得上传半截数据。"""

    def test_failed_不上传(self):
        _FakeRecordingBucket.calls.clear()
        prov = _fake_provider(5)
        f = OssFile("/x.txt", {"wsgidav.provider": prov})
        buf = _WriteBuffer(max_size=5)
        buf._failed = True
        f._write_buf = buf
        f.end_write(with_errors=False)  # 即便 with_errors=False 也不上传
        assert _FakeRecordingBucket.calls == []


class TestWrapOssError:
    def test_oss_error_映射为_dav_error(self):
        @_wrap_oss_error("写入")
        def boom():
            raise oss2.exceptions.OssError(500, {}, b"", {"Message": "server error"})

        with pytest.raises(DAVError) as exc_info:
            boom()
        assert "OSS 写入失败" in str(exc_info.value)
        # M3：对外错误文案中性，不泄漏 OSS 细节
        assert "server error" not in str(exc_info.value)

    def test_dav_error_原样透传(self):
        @_wrap_oss_error("写入")
        def passthru():
            raise DAVError(403, "原有错误")

        with pytest.raises(DAVError) as exc_info:
            passthru()
        assert "原有错误" in str(exc_info.value)


class _FakeResp:
    def __init__(self, data):
        self._buf = io.BytesIO(data)

    def read(self, size=-1):
        return self._buf.read(size)

    def close(self):
        self._buf.close()


class _FakeBucket:
    """记录 byte_range 请求并返回对应切片数据的假 bucket。"""

    def __init__(self, data):
        self.data = data
        self.requests = []

    def get_object(self, key, byte_range=None):
        assert byte_range is not None
        self.requests.append(byte_range)
        start, end = byte_range
        return _FakeResp(self.data[start : end + 1])


class TestOssContentReader:
    DATA = bytes(range(256)) * 10  # 2560 字节

    def _make(self, size=None):
        bucket = _FakeBucket(self.DATA)
        reader = _OssContentReader(bucket, "k", size if size is not None else len(self.DATA))
        return reader, bucket

    def test_整读(self):
        reader, _ = self._make()
        assert reader.read() == self.DATA

    def test_分块读(self):
        reader, _ = self._make()
        chunks = []
        while True:
            chunk = reader.read(100)
            if not chunk:
                break
            chunks.append(chunk)
        assert b"".join(chunks) == self.DATA

    def test_seek_set_后按范围请求(self):
        reader, bucket = self._make()
        assert reader.seek(100) == 100
        assert reader.read(10) == self.DATA[100:110]
        assert bucket.requests[-1] == (100, len(self.DATA) - 1)

    def test_seek_cur(self):
        reader, _ = self._make()
        reader.read(50)
        assert reader.seek(20, 1) == 70
        assert reader.read(5) == self.DATA[70:75]

    def test_seek_end(self):
        reader, _ = self._make()
        assert reader.seek(-10, 2) == len(self.DATA) - 10
        assert reader.read() == self.DATA[-10:]

    def test_seek_越界钳制到_eof(self):
        reader, bucket = self._make()
        assert reader.seek(10**9) == len(self.DATA)
        assert reader.read() == b""
        # 越界 seek 不应产生新的 OSS 请求
        assert len(bucket.requests) == 1

    def test_seek_负位置报错(self):
        reader, _ = self._make()
        with pytest.raises(ValueError):
            reader.seek(-1)

    def test_零字节文件(self):
        reader, bucket = self._make(size=0)
        assert reader.read() == b""
        assert reader.seek(0) == 0
        assert bucket.requests == []

    def test_close_后可安全关闭(self):
        reader, _ = self._make()
        reader.read(10)
        reader.close()
        assert reader.read() == b""
