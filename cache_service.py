"""
查询结果缓存服务 — LRU + TTL 双策略
======================================
- LRU (Least Recently Used)：超过容量时淘汰最久未访问的条目
- TTL (Time To Live)：每条缓存有固定有效期，过期自动失效
- 查询标准化：小写 + 去标点，保证"焊缝裂纹？"和"焊缝裂纹"命中同一缓存
"""

import re
import time
import logging
from collections import OrderedDict
from typing import Any, Optional

logger = logging.getLogger("welding_qa.cache")


# ============================================================
# 查询标准化
# ============================================================

def normalize_query(query: str) -> str:
    """将查询标准化为缓存键：小写、去标点、压缩空白"""
    q = query.lower().strip()
    # 去除常见标点符号和多余空白
    q = re.sub(r'[？?！!，,。.、；;：:""\'\'《》【】\(\)（）\s]+', ' ', q)
    q = re.sub(r'\s+', ' ', q).strip()
    return q


# ============================================================
# LRU + TTL Cache
# ============================================================

class LRUTTLCache:
    """
    LRU + TTL 双策略缓存

    内部结构：
      OrderedDict: key → (value, expire_at)
      最近访问的条目移到尾部，淘汰时从头部移除（LRU）
      每次 get 时检查 expire_at（TTL）
    """

    def __init__(self, max_size: int = 200, ttl_seconds: int = 3600):
        self.max_size = max_size
        self.ttl = ttl_seconds
        self._cache: OrderedDict[str, tuple] = OrderedDict()
        # 统计
        self._hits = 0
        self._misses = 0
        self._evictions = 0   # LRU 淘汰次数
        self._expirations = 0  # TTL 过期次数

    # ----------------------------------------------------------
    # 核心读写
    # ----------------------------------------------------------

    def get(self, query: str) -> Optional[Any]:
        """
        读取缓存。
        - 未命中 → 返回 None，misses+1
        - TTL 过期 → 删除，返回 None，misses+1
        - 命中 → 移到末尾，hits+1，返回 value
        """
        key = normalize_query(query)
        if key not in self._cache:
            self._misses += 1
            return None

        value, expire_at = self._cache[key]
        if time.time() > expire_at:
            del self._cache[key]
            self._expirations += 1
            self._misses += 1
            logger.debug("Cache EXPIRED: %s", key[:60])
            return None

        # 命中：移到末尾（最近使用）
        self._cache.move_to_end(key)
        self._hits += 1
        logger.debug("Cache HIT: %s", key[:60])
        return value

    def set(self, query: str, value: Any) -> None:
        """
        写入缓存。
        - 已存在 → 更新值和过期时间，移到末尾
        - 不存在 + 已满 → 淘汰头部（LRU），再写入
        """
        key = normalize_query(query)
        expire_at = time.time() + self.ttl

        if key in self._cache:
            self._cache.move_to_end(key)
            self._cache[key] = (value, expire_at)
        else:
            if len(self._cache) >= self.max_size:
                self._cache.popitem(last=False)  # 淘汰最久未用
                self._evictions += 1
                logger.debug("Cache EVICT (LRU): size=%d", self.max_size)
            self._cache[key] = (value, expire_at)

    # ----------------------------------------------------------
    # 管理
    # ----------------------------------------------------------

    def invalidate(self) -> int:
        """清空所有缓存（知识库更新后调用），返回被清除的条目数"""
        count = len(self._cache)
        self._cache.clear()
        logger.info("Cache INVALIDATED: %d entries cleared", count)
        return count

    def _evict_expired(self) -> int:
        """清除所有已过期条目（内部清理用）"""
        now = time.time()
        expired_keys = [k for k, (_, exp) in self._cache.items() if now > exp]
        for k in expired_keys:
            del self._cache[k]
            self._expirations += 1
        return len(expired_keys)

    # ----------------------------------------------------------
    # 统计
    # ----------------------------------------------------------

    def stats(self) -> dict:
        """返回缓存状态统计"""
        # 先清一下过期条目，size 更准确
        self._evict_expired()
        total = self._hits + self._misses
        return {
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": round(self._hits / total, 4) if total > 0 else 0.0,
            "size": len(self._cache),
            "max_size": self.max_size,
            "ttl_seconds": self.ttl,
            "evictions": self._evictions,
            "expirations": self._expirations,
        }

    def reset_stats(self) -> None:
        """重置统计数据（不清除缓存内容）"""
        self._hits = 0
        self._misses = 0
        self._evictions = 0
        self._expirations = 0


# ============================================================
# 单例
# ============================================================

_cache_instance: Optional[LRUTTLCache] = None


def get_cache() -> LRUTTLCache:
    global _cache_instance
    if _cache_instance is None:
        _cache_instance = LRUTTLCache(max_size=200, ttl_seconds=3600)
    return _cache_instance
