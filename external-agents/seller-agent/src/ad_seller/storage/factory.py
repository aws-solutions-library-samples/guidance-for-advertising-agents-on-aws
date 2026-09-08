# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Storage backend factory."""

from typing import Optional

from ad_seller.storage.base import StorageBackend
from ad_seller.storage.sqlite_backend import SQLiteBackend


def get_storage_backend(
    storage_type: Optional[str] = None,
    database_url: Optional[str] = None,
    redis_url: Optional[str] = None,
) -> StorageBackend:
    """Create and return the appropriate storage backend.

    Args:
        storage_type: Type of storage ("sqlite", "redis", or "hybrid").
                      If None, auto-detects from available config.
        database_url: Database URL (for sqlite or hybrid backends)
        redis_url: Redis connection URL (for redis or hybrid backends)

    Returns:
        StorageBackend instance

    Raises:
        ValueError: If invalid storage type or missing configuration
    """
    # Import settings for defaults
    from ad_seller.config.settings import get_settings

    settings = get_settings()

    # Use settings as defaults
    storage_type = storage_type or settings.storage_type
    database_url = database_url or settings.database_url
    redis_url = redis_url or settings.redis_url

    # Auto-detect based on redis_url presence
    if storage_type is None:
        storage_type = "redis" if redis_url else "sqlite"

    storage_type = storage_type.lower()

    if storage_type == "sqlite":
        if not database_url:
            database_url = "sqlite:///./ad_seller.db"
        return SQLiteBackend(database_url=database_url)

    elif storage_type == "redis":
        if not redis_url:
            raise ValueError(
                "Redis URL required for redis storage. "
                "Set REDIS_URL environment variable or pass redis_url parameter."
            )

        from ad_seller.storage.redis_backend import RedisBackend

        return RedisBackend(redis_url=redis_url)

    elif storage_type == "hybrid":
        if not database_url or not database_url.startswith("postgresql"):
            raise ValueError(
                "PostgreSQL URL required for hybrid storage. "
                "Set DATABASE_URL=postgresql+asyncpg://user:pass@host/db"
            )
        if not redis_url:
            raise ValueError(
                "Redis URL required for hybrid storage. Set REDIS_URL=redis://host:6379/0"
            )

        from ad_seller.storage.hybrid_backend import HybridBackend
        from ad_seller.storage.postgres_backend import PostgresBackend
        from ad_seller.storage.redis_backend import RedisBackend

        pg = PostgresBackend(
            database_url=database_url,
            pool_min=settings.postgres_pool_min,
            pool_max=settings.postgres_pool_max,
        )
        redis = RedisBackend(redis_url=redis_url)
        return HybridBackend(postgres=pg, redis=redis)

    else:
        raise ValueError(
            f"Unknown storage type: {storage_type}. Supported types: sqlite, redis, hybrid"
        )


# Global storage instance (lazy initialization)
_storage_instance: Optional[StorageBackend] = None


async def get_storage() -> StorageBackend:
    """Get the global storage instance, creating it if needed.

    This is a convenience function for getting a connected storage backend.
    """
    global _storage_instance

    if _storage_instance is None:
        _storage_instance = get_storage_backend()
        await _storage_instance.connect()

    return _storage_instance


async def close_storage() -> None:
    """Close the global storage instance."""
    global _storage_instance

    if _storage_instance is not None:
        await _storage_instance.disconnect()
        _storage_instance = None
