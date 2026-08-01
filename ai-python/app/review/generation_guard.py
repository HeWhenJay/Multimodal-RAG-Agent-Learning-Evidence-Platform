"""复习资料级生成短锁，避免多实例重复调用昂贵的 LLM。"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import logging
import os
from threading import Lock, RLock
import uuid
from typing import Any, Protocol


logger = logging.getLogger(__name__)


class RedisLike(Protocol):
    """复习生成锁使用的最小 Redis 接口。"""

    def set(self, name: str, value: str, ex: int | None = None, nx: bool = False) -> Any: ...

    def eval(self, script: str, numkeys: int, *keys_and_args: str) -> Any: ...


@dataclass
class _GenerationLease:
    """记录本地锁和 Redis 锁的释放信息。"""

    key: str
    token: str
    local_lock: Lock
    redis_client: RedisLike | None

    def release(self) -> None:
        """只释放自己持有的 Redis 锁，并释放进程内互斥锁。"""
        try:
            if self.redis_client is not None:
                script = "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end"
                try:
                    self.redis_client.eval(script, 1, self.key, self.token)
                except Exception:
                    # GET 后 DELETE 存在误删新持有者锁的竞态，失败时只等待 TTL 回收。
                    logger.warning("Redis 复习生成锁原子释放失败，将等待 TTL 自动过期")
        finally:
            self.local_lock.release()


class ReviewGenerationGuard:
    """优先使用 Redis 跨实例互斥，未配置时退化为进程内互斥。"""

    _registry_guard = RLock()
    _local_locks: dict[str, Lock] = {}

    def __init__(
        self,
        *,
        redis_url: str | None = None,
        redis_client: RedisLike | None = None,
        ttl_seconds: int | None = None,
    ) -> None:
        self.redis_url = (redis_url if redis_url is not None else os.getenv("REDIS_URL", "")).strip()
        self.redis_client = redis_client
        if ttl_seconds is not None:
            configured_ttl = ttl_seconds
        else:
            try:
                configured_ttl = int(os.getenv("REVIEW_GENERATION_LOCK_TTL_SECONDS", "180"))
            except ValueError:
                configured_ttl = 180
        self.ttl_seconds = min(max(configured_ttl, 30), 900)
        self._redis_unavailable = False

    def acquire(self, raw_key: str) -> _GenerationLease | None:
        """非阻塞获取一把资料级锁，调用方必须在 finally 中释放。"""
        key = self.redis_key(raw_key)
        local_lock = self._local_lock(key)
        if not local_lock.acquire(blocking=False):
            return None
        token = uuid.uuid4().hex
        client = self._get_redis_client()
        if client is not None:
            try:
                claimed = client.set(key, token, ex=self.ttl_seconds, nx=True)
            except Exception:
                self._redis_unavailable = True
                logger.warning("Redis 复习生成锁不可用，已退化为进程内锁")
                client = None
                claimed = True
            if not claimed:
                local_lock.release()
                return None
        return _GenerationLease(key, token, local_lock, client)

    @staticmethod
    def redis_key(raw_key: str) -> str:
        """将业务键哈希化，避免资料标题等内容进入 Redis 键。"""
        digest = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
        return f"learning-review:generation:{digest}"

    @classmethod
    def _local_lock(cls, key: str) -> Lock:
        """获取同一进程内稳定复用的互斥锁。"""
        with cls._registry_guard:
            return cls._local_locks.setdefault(key, Lock())

    def _get_redis_client(self) -> RedisLike | None:
        """延迟创建 Redis 客户端，Redis 故障不阻断复习功能。"""
        if self.redis_client is not None:
            return self.redis_client
        if self._redis_unavailable or not self.redis_url:
            return None
        try:
            from redis import Redis

            self.redis_client = Redis.from_url(
                self.redis_url,
                decode_responses=True,
                socket_connect_timeout=1,
                socket_timeout=1,
            )
            return self.redis_client
        except Exception:
            self._redis_unavailable = True
            logger.warning("无法创建 Redis 复习生成锁客户端，已退化为进程内锁")
            return None
