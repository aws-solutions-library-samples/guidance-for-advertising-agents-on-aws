# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Unit tests for Ad Seller System storage backends."""

import os
import tempfile

import pytest

from ad_seller.storage.sqlite_backend import SQLiteBackend


class TestSQLiteBackend:
    """Tests for SQLiteBackend."""

    @pytest.fixture
    async def sqlite_backend(self):
        """Create a SQLite backend with temp database."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            backend = SQLiteBackend(f"sqlite:///{db_path}")
            await backend.connect()
            yield backend
            await backend.disconnect()

    @pytest.mark.asyncio
    async def test_connect_creates_tables(self, sqlite_backend):
        """Test that connect creates the necessary tables."""
        # Connection should succeed without error
        assert sqlite_backend._connection is not None

    @pytest.mark.asyncio
    async def test_set_and_get(self, sqlite_backend):
        """Test basic set and get operations."""
        await sqlite_backend.set("test_key", {"value": "test_data"})
        result = await sqlite_backend.get("test_key")

        assert result is not None
        assert result["value"] == "test_data"

    @pytest.mark.asyncio
    async def test_get_nonexistent_key(self, sqlite_backend):
        """Test getting a key that doesn't exist."""
        result = await sqlite_backend.get("nonexistent_key")
        assert result is None

    @pytest.mark.asyncio
    async def test_delete(self, sqlite_backend):
        """Test delete operation."""
        await sqlite_backend.set("delete_me", {"data": "test"})

        # Verify it exists
        assert await sqlite_backend.exists("delete_me") is True

        # Delete it
        deleted = await sqlite_backend.delete("delete_me")
        assert deleted is True

        # Verify it's gone
        assert await sqlite_backend.exists("delete_me") is False

    @pytest.mark.asyncio
    async def test_exists(self, sqlite_backend):
        """Test exists operation."""
        assert await sqlite_backend.exists("new_key") is False

        await sqlite_backend.set("new_key", "value")
        assert await sqlite_backend.exists("new_key") is True

    @pytest.mark.asyncio
    async def test_keys_pattern(self, sqlite_backend):
        """Test keys pattern matching."""
        await sqlite_backend.set("product:001", {"name": "Product 1"})
        await sqlite_backend.set("product:002", {"name": "Product 2"})
        await sqlite_backend.set("proposal:001", {"name": "Proposal 1"})

        product_keys = await sqlite_backend.keys("product:*")
        assert len(product_keys) == 2
        assert "product:001" in product_keys
        assert "product:002" in product_keys

        all_keys = await sqlite_backend.keys("*")
        assert len(all_keys) == 3

    @pytest.mark.asyncio
    async def test_ttl_expiration(self, sqlite_backend):
        """Test that TTL causes expiration."""
        import time

        # Set with 1 second TTL
        await sqlite_backend.set("expiring_key", "value", ttl=1)

        # Should exist immediately
        result = await sqlite_backend.get("expiring_key")
        assert result == "value"

        # Wait for expiration
        time.sleep(1.5)

        # Should be gone
        result = await sqlite_backend.get("expiring_key")
        assert result is None

    @pytest.mark.asyncio
    async def test_product_operations(self, sqlite_backend):
        """Test product convenience methods."""
        product_data = {
            "product_id": "test-001",
            "name": "Test Product",
            "base_cpm": 15.0,
        }

        await sqlite_backend.set_product("test-001", product_data)
        result = await sqlite_backend.get_product("test-001")

        assert result is not None
        assert result["name"] == "Test Product"
        assert result["base_cpm"] == 15.0

    @pytest.mark.asyncio
    async def test_list_products(self, sqlite_backend):
        """Test listing all products."""
        await sqlite_backend.set_product("prod-001", {"name": "Product 1"})
        await sqlite_backend.set_product("prod-002", {"name": "Product 2"})

        products = await sqlite_backend.list_products()
        assert len(products) == 2

    @pytest.mark.asyncio
    async def test_proposal_operations(self, sqlite_backend):
        """Test proposal convenience methods."""
        proposal_data = {
            "proposal_id": "prop-001",
            "status": "draft",
            "buyer_id": "buyer-001",
        }

        await sqlite_backend.set_proposal("prop-001", proposal_data)
        result = await sqlite_backend.get_proposal("prop-001")

        assert result is not None
        assert result["status"] == "draft"

    @pytest.mark.asyncio
    async def test_deal_operations(self, sqlite_backend):
        """Test deal convenience methods."""
        deal_data = {
            "deal_id": "deal-001",
            "deal_type": "preferred_deal",
            "price": 12.0,
        }

        await sqlite_backend.set_deal("deal-001", deal_data)
        result = await sqlite_backend.get_deal("deal-001")

        assert result is not None
        assert result["deal_type"] == "preferred_deal"


class TestStorageFactory:
    """Tests for storage factory."""

    def test_get_storage_backend_sqlite(self):
        """Test factory creates SQLite backend."""
        from ad_seller.storage.factory import get_storage_backend

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            backend = get_storage_backend(
                storage_type="sqlite",
                database_url=f"sqlite:///{db_path}",
            )

            assert isinstance(backend, SQLiteBackend)

    def test_get_storage_backend_redis_requires_url(self):
        """Test factory raises error for Redis without URL."""
        from ad_seller.storage.factory import get_storage_backend

        with pytest.raises(ValueError, match="Redis URL required"):
            get_storage_backend(storage_type="redis", redis_url=None)

    def test_get_storage_backend_unknown_type(self):
        """Test factory raises error for unknown type."""
        from ad_seller.storage.factory import get_storage_backend

        with pytest.raises(ValueError, match="Unknown storage type"):
            get_storage_backend(storage_type="mongodb")
