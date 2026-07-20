"""Python RAG 原始资料对象存储。

本模块只处理原始文件的保存和读取，不负责资料表或索引状态。local 与 OSS
实现共享同一套文件名、对象 key 和用户隔离校验，避免控制面和 Kafka worker
分别实现一套不一致的路径安全规则。
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path, PurePosixPath
import shutil
import tempfile
from typing import Any, Protocol
from urllib.parse import quote, urlsplit
from uuid import uuid4

from app.core.result import BusinessError


@dataclass(frozen=True)
class StoredObject:
    """上传成功后的对象定位信息。"""

    storage_type: str
    source_path: str
    object_key: str | None = None
    public_url: str | None = None


@dataclass
class OpenedStorageObject:
    """worker 解析时打开的对象，OSS 临时文件由 `cleanup` 负责删除。"""

    path: Path
    filename: str | None
    content_type: str | None
    source_path: str | None
    _temporary: bool = False

    def cleanup(self) -> None:
        """只删除本次下载创建的临时文件，绝不删除本地原始对象。"""
        if not self._temporary:
            return
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            return


class RagObjectStorage(Protocol):
    """控制面所需的最小对象存储契约。"""

    def store_file(
        self,
        source: str | Path,
        filename: str,
        user_id: str,
        document_type: str,
        content_type: str | None = None,
    ) -> StoredObject: ...

    def store_bytes(self, content: bytes, filename: str, user_id: str, document_type: str) -> StoredObject: ...

    def load_bytes(self, material: Any) -> bytes: ...

    def local_path(self, material: Any) -> Path | None: ...

    def delete(self, material: Any) -> None: ...


class LocalRagObjectStorage:
    """开发和单机部署使用的受控本地对象存储。"""

    storage_type = "local"

    def __init__(self, root: str | Path | None = None) -> None:
        configured = root or os.getenv("EVIDENCE_UPLOAD_ROOT", "uploads/rag")
        self.root = Path(configured).expanduser().resolve()

    def store_file(
        self,
        source: str | Path,
        filename: str,
        user_id: str,
        document_type: str,
        content_type: str | None = None,
    ) -> StoredObject:
        """以流式复制方式保存文件，避免把大文件加载进内存。"""
        source_path = Path(source).expanduser().resolve()
        if not source_path.is_file():
            raise BusinessError("上传临时文件不存在")
        target = self._new_target(filename, user_id, document_type)
        _copy_file_atomic(source_path, target)
        resolved = str(target.resolve())
        return StoredObject(storage_type=self.storage_type, source_path=resolved, object_key=resolved)

    def store_bytes(self, content: bytes, filename: str, user_id: str, document_type: str) -> StoredObject:
        """兼容小型内部调用；公开上传路径使用 `store_file`。"""
        if not content:
            raise BusinessError("上传文件不能为空")
        target = self._new_target(filename, user_id, document_type)
        _write_bytes_atomic(content, target)
        resolved = str(target.resolve())
        return StoredObject(storage_type=self.storage_type, source_path=resolved, object_key=resolved)

    def load_bytes(self, material: Any) -> bytes:
        """从受控本地根目录读取原始文件。"""
        path = self.local_path(material)
        if path is None:
            raise BusinessError("当前资料没有可读取的原始上传文件，无法重建索引")
        try:
            return path.read_bytes()
        except OSError as exc:
            raise BusinessError("读取原始资料失败") from exc

    def local_path(self, material: Any) -> Path | None:
        """校验资料路径属于上传根目录，阻断路径穿越。"""
        if str(getattr(material, "storage_type", "") or "").lower() != self.storage_type:
            return None
        source_path = getattr(material, "original_file_path", None)
        if not source_path:
            return None
        path = Path(source_path).expanduser().resolve()
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise BusinessError("原始资料路径不在受控上传目录内") from exc
        if not path.is_file():
            raise BusinessError("原始资料不存在，无法重建索引")
        return path

    def delete(self, material: Any) -> None:
        """删除本地原文件；清理失败不覆盖主业务错误。"""
        try:
            path = self.local_path(material)
            if path is not None:
                path.unlink(missing_ok=True)
        except Exception:
            return

    def _new_target(self, filename: str, user_id: str, document_type: str) -> Path:
        """生成按用户隔离的本地目标路径。"""
        day = __import__("datetime").datetime.now().strftime("%Y%m%d")
        directory = self.root / safe_storage_segment(user_id) / safe_storage_segment(document_type) / day
        directory.mkdir(parents=True, exist_ok=True)
        return directory / f"{uuid4().hex}-{safe_filename(filename)}"


class OssRagObjectStorage:
    """阿里云 OSS 对象存储适配，使用用户隔离的受控 object key。"""

    storage_type = "oss"

    def __init__(
        self,
        *,
        endpoint: str | None = None,
        bucket_name: str | None = None,
        access_key_id: str | None = None,
        access_key_secret: str | None = None,
        object_prefix: str | None = None,
        public_base_url: str | None = None,
        bucket: Any | None = None,
    ) -> None:
        self.endpoint = (endpoint or os.getenv("ALIYUN_OSS_ENDPOINT", "")).strip().rstrip("/")
        self.bucket_name = (bucket_name or os.getenv("ALIYUN_OSS_BUCKET", "")).strip()
        self.access_key_id = access_key_id or os.getenv("ALIYUN_OSS_ACCESS_KEY_ID", "")
        self.access_key_secret = access_key_secret or os.getenv("ALIYUN_OSS_ACCESS_KEY_SECRET", "")
        self.object_prefix = normalize_object_prefix(
            object_prefix or os.getenv("ALIYUN_OSS_OBJECT_PREFIX", "learning-evidence")
        )
        self.public_base_url = (public_base_url or os.getenv("ALIYUN_OSS_PUBLIC_BASE_URL", "")).strip().rstrip("/")
        if bucket is not None:
            self.bucket = bucket
            return
        if not self.endpoint or not self.bucket_name or not self.access_key_id or not self.access_key_secret:
            raise BusinessError("OSS 存储未配置完整，请检查 Endpoint、Bucket 和访问密钥")
        try:
            import oss2  # type: ignore
        except ImportError as exc:
            raise BusinessError("OSS 存储需要安装 oss2 依赖") from exc
        try:
            self.bucket = oss2.Bucket(
                oss2.Auth(self.access_key_id, self.access_key_secret),
                self.endpoint,
                self.bucket_name,
            )
        except Exception as exc:
            raise BusinessError("初始化 OSS 存储失败") from exc

    def store_file(
        self,
        source: str | Path,
        filename: str,
        user_id: str,
        document_type: str,
        content_type: str | None = None,
    ) -> StoredObject:
        """流式上传到 OSS，不在 Python 控制面构造整文件 bytes。"""
        source_path = Path(source).expanduser().resolve()
        if not source_path.is_file():
            raise BusinessError("上传临时文件不存在")
        key = self.build_object_key(user_id, document_type, filename)
        headers = {"Content-Type": content_type} if content_type else None
        try:
            self.bucket.put_object_from_file(key, str(source_path), headers=headers)
        except TypeError:
            # 兼容旧版 oss2 或测试替身不接受 headers 的签名。
            try:
                self.bucket.put_object_from_file(key, str(source_path))
            except Exception as exc:
                raise BusinessError("上传文件到 OSS 失败") from exc
        except Exception as exc:
            raise BusinessError("上传文件到 OSS 失败") from exc
        return StoredObject(
            storage_type=self.storage_type,
            source_path=self.public_url(key) or f"oss://{self.bucket_name}/{key}",
            object_key=key,
            public_url=self.public_url(key),
        )

    def store_bytes(self, content: bytes, filename: str, user_id: str, document_type: str) -> StoredObject:
        """兼容小型内部调用；公开上传路径使用 `store_file`。"""
        if not content:
            raise BusinessError("上传文件不能为空")
        key = self.build_object_key(user_id, document_type, filename)
        try:
            self.bucket.put_object(key, content)
        except Exception as exc:
            raise BusinessError("上传文件到 OSS 失败") from exc
        return StoredObject(
            storage_type=self.storage_type,
            source_path=self.public_url(key) or f"oss://{self.bucket_name}/{key}",
            object_key=key,
            public_url=self.public_url(key),
        )

    def load_bytes(self, material: Any) -> bytes:
        """读取受控 OSS 对象；文本预览才使用该小范围读取接口。"""
        key = self.object_key_for_material(material)
        try:
            response = self.bucket.get_object(key)
            return response.read()
        except Exception as exc:
            raise BusinessError("读取 OSS 原始资料失败") from exc

    def local_path(self, material: Any) -> Path | None:
        """OSS 对象没有长期本地路径，由 worker 按需下载临时文件。"""
        if str(getattr(material, "storage_type", "") or "").lower() == self.storage_type:
            self.object_key_for_material(material)
        return None

    def delete(self, material: Any) -> None:
        """删除当前资料对应的 OSS 对象。"""
        try:
            key = self.object_key_for_material(material)
            self.delete_object_key(key)
        except Exception:
            return

    def delete_object_key(self, key: str) -> None:
        """删除已按调用方校验过的 OSS key。"""
        try:
            self.bucket.delete_object(normalize_object_key(key, self.bucket_name))
        except Exception as exc:
            raise BusinessError("删除 OSS 原始资料失败") from exc

    def build_object_key(self, user_id: str, document_type: str, filename: str) -> str:
        """生成包含用户和资料类型的 object key，文件名不参与路径解析。"""
        return "/".join(
            (
                self.object_prefix,
                safe_storage_segment(user_id),
                safe_storage_segment(document_type),
                __import__("datetime").datetime.now().strftime("%Y%m%d"),
                f"{uuid4().hex}-{safe_filename(filename)}",
            )
        )

    def object_key_for_material(self, material: Any) -> str:
        """校验资料对象 key 与其用户归属一致。"""
        key = normalize_object_key(getattr(material, "object_key", None), self.bucket_name)
        user_id = str(getattr(material, "user_id", "") or "")
        self.validate_object_key(key, user_id)
        return key

    def validate_object_key(self, key: str, user_id: str) -> str:
        """拒绝绝对路径、点段、前缀绕过和跨用户 OSS 对象读取。"""
        normalized = normalize_object_key(key, self.bucket_name)
        parts = normalized.split("/")
        expected = [*self.object_prefix.split("/"), safe_storage_segment(user_id)]
        if len(parts) <= len(expected) or parts[: len(expected)] != expected:
            raise BusinessError("OSS 对象不属于当前用户")
        return normalized

    def public_url(self, key: str) -> str | None:
        """生成可选 CDN/公开域名地址，不泄露访问密钥。"""
        if not self.public_base_url:
            return None
        return f"{self.public_base_url}/{quote(key, safe='/')}"

    def download_to_temp(
        self,
        *,
        key: str,
        user_id: str,
        filename: str | None,
        content_type: str | None,
    ) -> OpenedStorageObject:
        """把用户所属 OSS 对象下载到受控临时文件，并由调用方负责清理。"""
        validated = self.validate_object_key(key, user_id)
        suffix = Path(filename or "").suffix[:16]
        temp_root = Path(os.getenv("EVIDENCE_UPLOAD_TEMP_ROOT", tempfile.gettempdir())).expanduser().resolve()
        temp_root.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix="rag-oss-", suffix=suffix, dir=temp_root, delete=False) as handle:
            temp_path = Path(handle.name)
        try:
            self.bucket.get_object_to_file(validated, str(temp_path))
        except Exception as exc:
            temp_path.unlink(missing_ok=True)
            raise BusinessError("从 OSS 下载原始资料失败") from exc
        return OpenedStorageObject(
            path=temp_path,
            filename=filename or temp_path.name,
            content_type=content_type,
            source_path=f"oss://{self.bucket_name}/{validated}",
            _temporary=True,
        )


def build_rag_object_storage() -> RagObjectStorage:
    """按 EVIDENCE_STORAGE_PROVIDER 选择实际对象存储实现。"""
    provider = os.getenv("EVIDENCE_STORAGE_PROVIDER", "local").strip().lower() or "local"
    if provider == "local":
        return LocalRagObjectStorage()
    if provider == "oss":
        return OssRagObjectStorage()
    raise BusinessError("EVIDENCE_STORAGE_PROVIDER 仅支持 local 或 oss")


def download_storage_source(
    source_ref: Any,
    *,
    user_id: str,
    object_storage: RagObjectStorage | None = None,
) -> OpenedStorageObject:
    """根据 Kafka sourceRef 打开本地原文件或下载 OSS 临时文件。"""
    storage_type = str(getattr(source_ref, "storageType", "") or "").strip().lower()
    filename = getattr(source_ref, "filename", None)
    content_type = getattr(source_ref, "contentType", None)
    if storage_type == "local":
        root = Path(os.getenv("EVIDENCE_UPLOAD_ROOT", "uploads/rag")).expanduser().resolve()
        source = getattr(source_ref, "sourcePath", None)
        if not source:
            raise BusinessError("当前索引任务没有本地原文件路径")
        path = Path(source).expanduser().resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise BusinessError("原始资料路径不在受控上传目录内") from exc
        if not path.is_file():
            raise BusinessError("原始资料不存在")
        return OpenedStorageObject(path=path, filename=filename or path.name, content_type=content_type, source_path=str(path))
    if storage_type == "oss":
        storage = object_storage or build_rag_object_storage()
        if not callable(getattr(storage, "download_to_temp", None)):
            raise BusinessError("OSS sourceRef 与当前存储适配器不匹配")
        key = getattr(source_ref, "objectKey", None)
        if not key:
            raise BusinessError("OSS 索引任务缺少 objectKey")
        return storage.download_to_temp(
            key=key,
            user_id=user_id,
            filename=filename,
            content_type=content_type,
        )
    raise BusinessError("索引任务的存储类型不受支持")


def normalize_object_prefix(value: str) -> str:
    """规范化 OSS 前缀并拒绝路径穿越片段。"""
    prefix = str(value or "").replace("\\", "/").strip().strip("/")
    if not prefix:
        return "learning-evidence"
    parts = prefix.split("/")
    if any(not part or part in {".", ".."} for part in parts):
        raise BusinessError("OSS 对象前缀不合法")
    return "/".join(safe_storage_segment(part) for part in parts)


def normalize_object_key(value: str | None, bucket_name: str | None = None) -> str:
    """把 key 规范化为相对 POSIX 路径并拒绝穿越。"""
    if not value or not str(value).strip():
        raise BusinessError("OSS 对象 key 不能为空")
    raw = str(value).strip()
    if "\\" in raw:
        raise BusinessError("OSS 对象 key 不允许反斜杠")
    if raw.startswith("oss://"):
        parsed = urlsplit(raw)
        if parsed.query or parsed.fragment:
            raise BusinessError("OSS 对象 key 不允许 query 或 fragment")
        if bucket_name and parsed.netloc and parsed.netloc != bucket_name:
            raise BusinessError("OSS 对象 bucket 不匹配")
        raw = parsed.path.lstrip("/")
    elif "://" in raw or raw.startswith("/"):
        raise BusinessError("OSS 对象 key 不合法")
    path = PurePosixPath(raw)
    parts = path.parts
    if not parts or any(part in {"", ".", ".."} for part in parts) or any("\\" in part for part in parts):
        raise BusinessError("OSS 对象 key 不允许路径穿越")
    return "/".join(parts)


def safe_storage_segment(value: str | None) -> str:
    """把用户、类型等路径段压缩为单个安全组件。"""
    text = str(value or "").strip()
    cleaned = "".join(char if char.isalnum() or char in "_-" else "_" for char in text)
    return cleaned or "anonymous"


def safe_filename(value: str | None) -> str:
    """清理上传文件名，避免文件名携带路径语义。"""
    text = str(value or "").strip()
    cleaned = "".join("_" if char in '\\/:*?"<>|' else char for char in text)
    cleaned = cleaned.replace("..", "_").strip(" .")
    return cleaned or "material"


def _copy_file_atomic(source: Path, target: Path) -> None:
    """以受控缓冲区复制文件并原子替换目标。"""
    temp = target.with_name(f".{target.name}.tmp")
    try:
        with source.open("rb") as input_file, temp.open("wb") as output_file:
            shutil.copyfileobj(input_file, output_file, length=1024 * 1024)
            output_file.flush()
            os.fsync(output_file.fileno())
        temp.replace(target)
    except Exception:
        temp.unlink(missing_ok=True)
        raise


def _write_bytes_atomic(content: bytes, target: Path) -> None:
    """兼容小文件调用的原子写入。"""
    temp = target.with_name(f".{target.name}.tmp")
    try:
        with temp.open("wb") as output_file:
            output_file.write(content)
        temp.replace(target)
    except Exception:
        temp.unlink(missing_ok=True)
        raise
