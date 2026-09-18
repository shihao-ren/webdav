"""基于阿里云 OSS 的 WsgiDAV Provider。

OSS 没有真正的目录概念，这里用对象 key 的前缀模拟目录：
- 目录 ``/a/b`` 对应前缀 ``a/b/``
- 目录用一个以 ``/`` 结尾的零字节对象作为标记（MKCOL 时创建）
- 直接 PUT ``a/b/c.txt`` 而没有标记对象时，目录视为"隐式存在"，
  判断目录存在性时会回退到按前缀列举
"""

import io
import time

import oss2
from wsgidav.dav_error import (
    DAVError,
    HTTP_FORBIDDEN,
    HTTP_INTERNAL_ERROR,
    HTTP_SERVICE_UNAVAILABLE,
)
from wsgidav.dav_provider import DAVCollection, DAVNonCollection, DAVProvider
from wsgidav.util import join_uri

_NO_SUCH_KEY = oss2.exceptions.NoSuchKey
# 可重试的瞬时错误：限流与 5xx
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}
_MAX_RETRIES = 3


def _normalize_path(path):
    """把 DAV 路径规范化为不带首尾斜杠的形式（根路径返回 ''）。"""
    return "/".join(seg for seg in path.split("/") if seg)


def _path_to_key(path):
    """文件对象 key：'/a/b.txt' -> 'a/b.txt'"""
    return _normalize_path(path)


def _path_to_prefix(path):
    """目录前缀：'/a/b' -> 'a/b/'，根目录 -> ''"""
    key = _normalize_path(path)
    return key + "/" if key else ""


def _wrap_oss_error(desc):
    """OSS 写操作统一错误包装装饰器（OssError -> 500 DAVError）。"""

    def decorator(fn):
        def wrapper(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except DAVError:
                raise
            except oss2.exceptions.OssError as e:
                raise DAVError(HTTP_INTERNAL_ERROR, f"OSS {desc}失败: {e}")

        return wrapper

    return decorator


def _last_modified_ts(head_result):
    """从 head_object 结果中取出 epoch 时间戳。

    不同 oss2 版本类型不一：可能是 datetime、int/float（epoch 秒）或 RFC1123 字符串。
    """
    lm = head_result.last_modified
    if lm is None:
        return None
    if isinstance(lm, (int, float)):
        return float(lm)
    if hasattr(lm, "timestamp"):
        return lm.timestamp()
    from email.utils import parsedate_to_datetime

    return parsedate_to_datetime(lm).timestamp()


class OssProvider(DAVProvider):
    """把 Bucket 包装成 WsgiDAV 的 DAVProvider。"""

    def __init__(self, auth, endpoint, bucket_name):
        super().__init__()
        self.bucket = oss2.Bucket(auth, endpoint, bucket_name)
        self._bucket_name = bucket_name
        # 启动时验证连通性，配置错误尽早失败
        self.bucket.get_bucket_info()

    # -- 工具方法 ---------------------------------------------------------

    def head_object(self, key):
        """返回 head_object 结果，不存在时返回 None。

        限流/5xx 等瞬时错误做有限重试，仍失败则抛出 503 DAVError。
        """
        for attempt in range(_MAX_RETRIES + 1):
            try:
                return self.bucket.head_object(key)
            except _NO_SUCH_KEY:
                return None
            except oss2.exceptions.OssError as e:
                if (
                    attempt < _MAX_RETRIES
                    and getattr(e, "status", None) in _RETRYABLE_STATUS
                ):
                    time.sleep(0.5 * (attempt + 1))
                    continue
                raise DAVError(
                    HTTP_SERVICE_UNAVAILABLE,
                    f"OSS 暂时不可用（head_object {key!r}）: {e}",
                )

    def is_collection_path(self, path):
        """该路径是否为（已存在的）目录。根目录恒为 True。"""
        prefix = _path_to_prefix(path)
        if not prefix:
            return True
        if self.head_object(prefix) is not None:
            return True
        # 隐式目录：前缀下有任何对象
        for _ in oss2.ObjectIteratorV2(self.bucket, prefix=prefix, max_keys=1):
            return True
        return False

    def is_file_path(self, path):
        return self.head_object(_path_to_key(path)) is not None

    def delete_prefix(self, prefix):
        """递归删除前缀下所有对象（含目录标记）。"""
        keys = [obj.key for obj in oss2.ObjectIterator(self.bucket, prefix=prefix)]
        if keys:
            # batch_delete_objects 每次最多 1000 个
            for i in range(0, len(keys), 1000):
                self.bucket.batch_delete_objects(keys[i : i + 1000])

    # -- DAVProvider 接口 --------------------------------------------------

    def get_resource_inst(self, path, environ):
        path = "/" + _normalize_path(path)
        if self.is_collection_path(path):
            return OssCollection(path, environ)
        if self.is_file_path(path):
            return OssFile(path, environ)
        return None

    def exists(self, path, environ):
        path = "/" + _normalize_path(path)
        return self.is_collection_path(path) or self.is_file_path(path)

    def is_collection(self, path, environ):
        return self.is_collection_path(path)


class _OssResourceMixin:
    """通过 provider 访问 bucket 的便捷属性。"""

    @property
    def _bucket(self):
        return self.provider.bucket

    def _join_child(self, name):
        return join_uri(self.path, name)


class OssCollection(_OssResourceMixin, DAVCollection):
    """OSS 前缀模拟的目录。"""

    def __init__(self, path, environ):
        super().__init__(path, environ)

    @property
    def _prefix(self):
        return _path_to_prefix(self.path)

    def get_member_names(self):
        prefix = self._prefix
        names = []
        for obj in oss2.ObjectIteratorV2(self._bucket, prefix=prefix, delimiter="/"):
            if obj.is_prefix():
                # 子目录：去掉前缀与末尾斜杠
                names.append(obj.key[len(prefix) :].rstrip("/"))
            elif obj.key != prefix:
                names.append(obj.key[len(prefix) :])
        return names

    def create_empty_resource(self, name):
        key = self._prefix + name
        self._put_object(key, b"")
        return OssFile(self._join_child(name), self.environ)

    def create_collection(self, name):
        self._put_object(self._prefix + name + "/", b"")

    def support_recursive_delete(self):
        return True

    def delete(self):
        if _normalize_path(self.path) == "":
            raise DAVError(HTTP_FORBIDDEN, "不允许删除根目录")
        try:
            self.provider.delete_prefix(self._prefix)
        except oss2.exceptions.OssError as e:
            raise DAVError(HTTP_INTERNAL_ERROR, f"OSS 删除失败: {e}")
        return None  # 无错误

    def copy_move_single(self, dest_path, *, is_move):
        # 非递归语义：只创建目标目录标记，成员由调用方逐个处理
        dest_prefix = _path_to_prefix(dest_path)
        if dest_prefix:
            self._put_object(dest_prefix, b"")

    @_wrap_oss_error("写入")
    def _put_object(self, key, data):
        self._bucket.put_object(key, data)


class _WriteBuffer(io.BytesIO):
    """WsgiDAV 在 end_write 之前会 close 写入流，改为保留数据由 end_write 上传。"""

    def close(self):
        pass  # 数据在 end_write 中读取，真正释放靠丢弃引用


class _OssContentReader:
    """可 seek 的 OSS 对象读取流。

    WsgiDAV 处理 Range 请求时会先 seek 到起始位置再顺序读取；
    这里用 OSS 的 byte_range 重新开流来实现 seek（每次 seek 一个 HTTP 请求）。
    """

    def __init__(self, bucket, key, size):
        self._bucket = bucket
        self._key = key
        self._size = size
        self._pos = 0
        self._resp = None
        if size > 0:
            self._open()

    def _open(self):
        self._resp = self._bucket.get_object(
            self._key, byte_range=(self._pos, self._size - 1)
        )

    def read(self, size=-1):
        if self._size == 0 or self._resp is None:
            return b""
        if size is None or size < 0:
            size = self._size - self._pos
        data = self._resp.read(size)
        self._pos += len(data)
        return data

    def seek(self, offset, whence=0):
        if whence == 0:
            new_pos = offset
        elif whence == 1:
            new_pos = self._pos + offset
        elif whence == 2:
            new_pos = self._size + offset
        else:
            raise ValueError(f"无效的 whence: {whence}")
        if new_pos < 0:
            raise ValueError("不允许负的 seek 位置")
        # 越界位置（含 EOF）不产生 OSS 请求，读操作自然返回 b""
        new_pos = min(new_pos, self._size)
        if new_pos != self._pos:
            self._pos = new_pos
            if self._resp is not None:
                self._resp.close()
                self._resp = None
            if self._pos < self._size:
                self._open()
        return self._pos

    def tell(self):
        return self._pos

    def close(self):
        if self._resp is not None:
            try:
                self._resp.close()
            except Exception:
                pass
            self._resp = None


class OssFile(_OssResourceMixin, DAVNonCollection):
    """OSS 对象文件。"""

    def __init__(self, path, environ):
        super().__init__(path, environ)
        self._key = _path_to_key(path)
        self._head = None
        self._write_buf = None

    def _get_head(self):
        if self._head is None:
            self._head = self.provider.head_object(self._key)
            if self._head is None:
                raise DAVError(HTTP_INTERNAL_ERROR, f"对象不存在: {self._key}")
        return self._head

    def get_content_length(self):
        return self._get_head().content_length

    def get_last_modified(self):
        return _last_modified_ts(self._get_head())

    def get_content(self):
        return _OssContentReader(self._bucket, self._key, self.get_content_length())

    def support_ranges(self):
        return True

    def get_etag(self):
        return self._get_head().etag

    def support_etag(self):
        return True

    def begin_write(self, *, content_type=None):
        # 简单实现：整体缓冲后一次性上传
        self._write_buf = _WriteBuffer()
        return self._write_buf

    def support_recursive_move(self, dest_path):
        # 非集合资源需覆盖此方法（基类实现带 is_collection 断言）
        return False

    def end_write(self, *, with_errors):
        if self._write_buf is None:
            return
        if not with_errors:
            self._put_object(self._key, self._write_buf.getvalue())
        self._write_buf = None

    def delete(self):
        self._delete_object(self._key)

    def copy_move_single(self, dest_path, *, is_move):
        dest_key = _path_to_key(dest_path)
        self._copy_object(self._key, dest_key)

    @_wrap_oss_error("写入")
    def _put_object(self, key, data):
        self._bucket.put_object(key, data)

    @_wrap_oss_error("删除")
    def _delete_object(self, key):
        self._bucket.delete_object(key)

    @_wrap_oss_error("复制")
    def _copy_object(self, src_key, dest_key):
        self._bucket.copy_object(self.provider._bucket_name, src_key, dest_key)
