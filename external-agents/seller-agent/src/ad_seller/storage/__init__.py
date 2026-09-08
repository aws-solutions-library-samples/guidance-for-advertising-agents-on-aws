# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Storage backends for the Ad Seller System.

Supports SQLite (default), Redis, PostgreSQL, and Hybrid (Postgres + Redis) for persistence.
"""

from ad_seller.storage.base import StorageBackend
from ad_seller.storage.factory import get_storage_backend

__all__ = ["StorageBackend", "get_storage_backend"]
