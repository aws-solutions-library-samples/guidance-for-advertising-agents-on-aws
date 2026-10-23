# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Unit tests for API Key authentication models, service, and dependencies."""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from ad_seller.auth.api_key_service import ApiKeyService
from ad_seller.auth.dependencies import _extract_key_from_headers
from ad_seller.models.api_key import (
    API_KEY_INDEX_PREFIX,
    API_KEY_PREFIX,
    API_KEY_STORAGE_PREFIX,
    ApiKeyCreateRequest,
    ApiKeyCreateResponse,
    ApiKeyInfo,
    ApiKeyRecord,
    generate_api_key,
    hash_api_key,
)
from ad_seller.models.buyer_identity import (
    AccessTier,
    BuyerContext,
    BuyerIdentity,
    IdentityLevel,
)

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_storage():
    """Mock StorageBackend with dict-based KV store."""
    store = {}
    storage = AsyncMock()
    storage.get = AsyncMock(side_effect=lambda k: store.get(k))
    storage.set = AsyncMock(side_effect=lambda k, v: store.__setitem__(k, v))
    storage._store = store  # expose for test assertions
    return storage


@pytest.fixture
def api_key_service(mock_storage):
    return ApiKeyService(mock_storage)


@pytest.fixture
def sample_identity():
    return BuyerIdentity(
        agency_id="agency-001",
        agency_name="Acme Agency",
        agency_holding_company="Acme Holding",
    )


@pytest.fixture
def sample_key_record(sample_identity):
    return ApiKeyRecord(
        key_id="key-abc12345",
        key_hash="fakehash123",
        key_prefix_hint="ask_live_Ab...",
        identity=sample_identity,
        label="Test key",
    )


@pytest.fixture
def expired_key_record(sample_identity):
    return ApiKeyRecord(
        key_id="key-expired1",
        key_hash="expiredhash",
        key_prefix_hint="ask_live_Ex...",
        identity=sample_identity,
        label="Expired key",
        expires_at=datetime.utcnow() - timedelta(hours=1),
    )


@pytest.fixture
def revoked_key_record(sample_identity):
    return ApiKeyRecord(
        key_id="key-revoked1",
        key_hash="revokedhash",
        key_prefix_hint="ask_live_Re...",
        identity=sample_identity,
        label="Revoked key",
        revoked=True,
        revoked_at=datetime.utcnow() - timedelta(hours=1),
    )


# =============================================================================
# Model tests: generate_api_key, hash_api_key
# =============================================================================


class TestGenerateApiKey:
    def test_has_prefix(self):
        key = generate_api_key()
        assert key.startswith(API_KEY_PREFIX)

    def test_sufficient_length(self):
        key = generate_api_key()
        # prefix + 43 chars from token_urlsafe(32)
        assert len(key) > 40

    def test_unique(self):
        keys = {generate_api_key() for _ in range(50)}
        assert len(keys) == 50


class TestHashApiKey:
    def test_deterministic(self):
        key = "ask_live_testkey123"
        assert hash_api_key(key) == hash_api_key(key)

    def test_hex_digest(self):
        h = hash_api_key("ask_live_test")
        assert len(h) == 64  # SHA-256 hex digest
        assert all(c in "0123456789abcdef" for c in h)

    def test_different_keys_different_hashes(self):
        assert hash_api_key("key_a") != hash_api_key("key_b")


# =============================================================================
# Model tests: ApiKeyRecord properties
# =============================================================================


class TestApiKeyRecord:
    def test_is_active_new_key(self, sample_key_record):
        assert sample_key_record.is_active is True
        assert sample_key_record.is_expired is False

    def test_is_expired_past(self, expired_key_record):
        assert expired_key_record.is_expired is True
        assert expired_key_record.is_active is False

    def test_is_expired_future(self, sample_identity):
        record = ApiKeyRecord(
            key_id="key-future",
            key_hash="futurehash",
            key_prefix_hint="ask_live_Fu...",
            identity=sample_identity,
            expires_at=datetime.utcnow() + timedelta(days=30),
        )
        assert record.is_expired is False
        assert record.is_active is True

    def test_is_expired_none_means_never(self, sample_key_record):
        assert sample_key_record.expires_at is None
        assert sample_key_record.is_expired is False

    def test_is_active_revoked(self, revoked_key_record):
        assert revoked_key_record.is_active is False

    def test_defaults(self, sample_key_record):
        assert sample_key_record.use_count == 0
        assert sample_key_record.last_used_at is None
        assert sample_key_record.revoked is False


# =============================================================================
# Model tests: ApiKeyCreateRequest, ApiKeyCreateResponse, ApiKeyInfo
# =============================================================================


class TestApiKeyModels:
    def test_create_request_minimal(self):
        req = ApiKeyCreateRequest()
        assert req.seat_id is None
        assert req.label == ""
        assert req.expires_in_days is None

    def test_create_request_full(self):
        req = ApiKeyCreateRequest(
            seat_id="seat-1",
            agency_id="agency-1",
            advertiser_id="adv-1",
            label="Production key",
            expires_in_days=90,
        )
        assert req.expires_in_days == 90
        assert req.label == "Production key"

    def test_create_response_has_warning(self, sample_identity):
        resp = ApiKeyCreateResponse(
            key_id="key-1",
            api_key="ask_live_secret",
            identity=sample_identity,
            label="test",
        )
        assert "securely" in resp.warning.lower()

    def test_key_info_from_record(self, sample_key_record):
        info = ApiKeyInfo(
            key_id=sample_key_record.key_id,
            key_prefix_hint=sample_key_record.key_prefix_hint,
            identity=sample_key_record.identity,
            label=sample_key_record.label,
            created_at=sample_key_record.created_at,
            expires_at=sample_key_record.expires_at,
            revoked=sample_key_record.revoked,
            is_active=sample_key_record.is_active,
        )
        assert info.key_id == "key-abc12345"
        assert info.is_active is True


# =============================================================================
# Service tests: ApiKeyService
# =============================================================================


class TestApiKeyServiceCreate:
    @pytest.mark.asyncio
    async def test_create_returns_full_key(self, api_key_service):
        req = ApiKeyCreateRequest(
            agency_id="agency-1",
            agency_name="Test Agency",
            label="test",
        )
        resp = await api_key_service.create_key(req)
        assert resp.api_key.startswith(API_KEY_PREFIX)
        assert resp.key_id.startswith("key-")
        assert resp.label == "test"

    @pytest.mark.asyncio
    async def test_create_stores_by_hash(self, api_key_service, mock_storage):
        req = ApiKeyCreateRequest(agency_id="a1", label="x")
        resp = await api_key_service.create_key(req)

        key_hash = hash_api_key(resp.api_key)
        stored = mock_storage._store.get(f"{API_KEY_STORAGE_PREFIX}{key_hash}")
        assert stored is not None
        assert stored["key_id"] == resp.key_id

    @pytest.mark.asyncio
    async def test_create_stores_index(self, api_key_service, mock_storage):
        req = ApiKeyCreateRequest(agency_id="a1")
        resp = await api_key_service.create_key(req)

        index_val = mock_storage._store.get(f"{API_KEY_INDEX_PREFIX}{resp.key_id}")
        assert index_val == hash_api_key(resp.api_key)

    @pytest.mark.asyncio
    async def test_create_adds_to_list(self, api_key_service, mock_storage):
        req = ApiKeyCreateRequest(agency_id="a1")
        resp = await api_key_service.create_key(req)

        key_list = mock_storage._store.get("api_key_list")
        assert resp.key_id in key_list

    @pytest.mark.asyncio
    async def test_create_with_expiry(self, api_key_service):
        req = ApiKeyCreateRequest(agency_id="a1", expires_in_days=30)
        resp = await api_key_service.create_key(req)
        assert resp.expires_at is not None
        assert resp.expires_at > datetime.utcnow()

    @pytest.mark.asyncio
    async def test_create_without_expiry(self, api_key_service):
        req = ApiKeyCreateRequest(agency_id="a1")
        resp = await api_key_service.create_key(req)
        assert resp.expires_at is None

    @pytest.mark.asyncio
    async def test_create_identity_propagated(self, api_key_service):
        req = ApiKeyCreateRequest(
            seat_id="seat-1",
            agency_id="agency-1",
            agency_name="Test",
            advertiser_id="adv-1",
        )
        resp = await api_key_service.create_key(req)
        assert resp.identity.agency_id == "agency-1"
        assert resp.identity.advertiser_id == "adv-1"
        assert resp.identity.seat_id == "seat-1"


class TestApiKeyServiceValidate:
    @pytest.mark.asyncio
    async def test_validate_valid_key(self, api_key_service):
        req = ApiKeyCreateRequest(agency_id="a1", label="valid")
        resp = await api_key_service.create_key(req)

        record = await api_key_service.validate_key(resp.api_key)
        assert record is not None
        assert record.key_id == resp.key_id
        assert record.use_count == 1
        assert record.last_used_at is not None

    @pytest.mark.asyncio
    async def test_validate_not_found(self, api_key_service):
        result = await api_key_service.validate_key("ask_live_nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_validate_revoked_raises(self, api_key_service):
        req = ApiKeyCreateRequest(agency_id="a1")
        resp = await api_key_service.create_key(req)
        await api_key_service.revoke_key(resp.key_id)

        with pytest.raises(ValueError, match="revoked"):
            await api_key_service.validate_key(resp.api_key)

    @pytest.mark.asyncio
    async def test_validate_expired_raises(self, api_key_service, mock_storage):
        req = ApiKeyCreateRequest(agency_id="a1", expires_in_days=1)
        resp = await api_key_service.create_key(req)

        # Manually expire the stored record
        key_hash = hash_api_key(resp.api_key)
        data = mock_storage._store[f"{API_KEY_STORAGE_PREFIX}{key_hash}"]
        data["expires_at"] = (datetime.utcnow() - timedelta(hours=1)).isoformat()

        with pytest.raises(ValueError, match="expired"):
            await api_key_service.validate_key(resp.api_key)

    @pytest.mark.asyncio
    async def test_validate_bumps_usage(self, api_key_service):
        req = ApiKeyCreateRequest(agency_id="a1")
        resp = await api_key_service.create_key(req)

        for _ in range(3):
            await api_key_service.validate_key(resp.api_key)

        record = await api_key_service.validate_key(resp.api_key)
        assert record.use_count == 4


class TestApiKeyServiceRevoke:
    @pytest.mark.asyncio
    async def test_revoke_existing(self, api_key_service):
        req = ApiKeyCreateRequest(agency_id="a1")
        resp = await api_key_service.create_key(req)

        result = await api_key_service.revoke_key(resp.key_id)
        assert result is True

    @pytest.mark.asyncio
    async def test_revoke_nonexistent(self, api_key_service):
        result = await api_key_service.revoke_key("key-doesnotexist")
        assert result is False

    @pytest.mark.asyncio
    async def test_revoke_sets_timestamp(self, api_key_service, mock_storage):
        req = ApiKeyCreateRequest(agency_id="a1")
        resp = await api_key_service.create_key(req)
        await api_key_service.revoke_key(resp.key_id)

        key_hash = hash_api_key(resp.api_key)
        data = mock_storage._store[f"{API_KEY_STORAGE_PREFIX}{key_hash}"]
        assert data["revoked"] is True
        assert data["revoked_at"] is not None


class TestApiKeyServiceList:
    @pytest.mark.asyncio
    async def test_list_empty(self, api_key_service):
        keys = await api_key_service.list_keys()
        assert keys == []

    @pytest.mark.asyncio
    async def test_list_multiple(self, api_key_service):
        for i in range(3):
            req = ApiKeyCreateRequest(agency_id=f"a{i}", label=f"key{i}")
            await api_key_service.create_key(req)

        keys = await api_key_service.list_keys()
        assert len(keys) == 3
        assert all(isinstance(k, ApiKeyInfo) for k in keys)

    @pytest.mark.asyncio
    async def test_get_key_info(self, api_key_service):
        req = ApiKeyCreateRequest(agency_id="a1", label="lookup-test")
        resp = await api_key_service.create_key(req)

        info = await api_key_service.get_key_info(resp.key_id)
        assert info is not None
        assert info.label == "lookup-test"
        assert info.key_id == resp.key_id

    @pytest.mark.asyncio
    async def test_get_key_info_nonexistent(self, api_key_service):
        info = await api_key_service.get_key_info("key-nope")
        assert info is None


# =============================================================================
# Dependency tests: _extract_key_from_headers
# =============================================================================


class TestExtractKeyFromHeaders:
    def test_bearer_header(self):
        key = _extract_key_from_headers(authorization="Bearer ask_live_abc123", x_api_key=None)
        assert key == "ask_live_abc123"

    def test_x_api_key_header(self):
        key = _extract_key_from_headers(authorization=None, x_api_key="ask_live_xyz789")
        assert key == "ask_live_xyz789"

    def test_x_api_key_takes_precedence(self):
        key = _extract_key_from_headers(
            authorization="Bearer ask_live_bearer",
            x_api_key="ask_live_header",
        )
        assert key == "ask_live_header"

    def test_no_headers_returns_none(self):
        key = _extract_key_from_headers(authorization=None, x_api_key=None)
        assert key is None

    def test_empty_authorization(self):
        key = _extract_key_from_headers(authorization="", x_api_key=None)
        assert key is None

    def test_non_bearer_scheme(self):
        key = _extract_key_from_headers(authorization="Basic dXNlcjpwYXNz", x_api_key=None)
        assert key is None

    def test_bearer_case_insensitive(self):
        key = _extract_key_from_headers(authorization="bearer ask_live_lower", x_api_key=None)
        assert key == "ask_live_lower"

    def test_malformed_authorization(self):
        key = _extract_key_from_headers(authorization="JustOneToken", x_api_key=None)
        assert key is None


# =============================================================================
# BuyerContext integration: auth + tier capping
# =============================================================================


class TestAuthBuyerContext:
    def test_api_key_agency_identity_level(self):
        identity = BuyerIdentity(
            agency_id="a1",
            agency_name="Test Agency",
        )
        assert identity.identity_level == IdentityLevel.AGENCY_ONLY

    def test_api_key_advertiser_identity_level(self):
        identity = BuyerIdentity(
            agency_id="a1",
            advertiser_id="adv1",
        )
        assert identity.identity_level == IdentityLevel.AGENCY_AND_ADVERTISER

    def test_authenticated_buyer_context(self):
        identity = BuyerIdentity(agency_id="a1", agency_name="Test")
        ctx = BuyerContext(
            identity=identity,
            is_authenticated=True,
            authentication_method="api_key",
        )
        assert ctx.is_authenticated is True
        assert ctx.authentication_method == "api_key"
        assert ctx.effective_tier == AccessTier.AGENCY

    def test_tier_capped_by_max_access_tier(self):
        """Advertiser key with SEAT ceiling → effective tier = SEAT."""
        identity = BuyerIdentity(
            agency_id="a1",
            advertiser_id="adv1",
        )
        ctx = BuyerContext(
            identity=identity,
            is_authenticated=True,
            authentication_method="api_key",
            max_access_tier=AccessTier.SEAT,
        )
        # Identity claims ADVERTISER but ceiling is SEAT
        assert ctx.effective_tier == AccessTier.SEAT

    def test_tier_not_elevated_by_ceiling(self):
        """SEAT identity with ADVERTISER ceiling → still SEAT."""
        identity = BuyerIdentity(seat_id="s1")
        ctx = BuyerContext(
            identity=identity,
            is_authenticated=True,
            max_access_tier=AccessTier.ADVERTISER,
        )
        assert ctx.effective_tier == AccessTier.SEAT

    def test_anonymous_buyer_is_public(self):
        ctx = BuyerContext(
            identity=BuyerIdentity(),
            is_authenticated=False,
        )
        assert ctx.effective_tier == AccessTier.PUBLIC

    def test_auth_plus_registry_composition(self):
        """REGISTERED agent (SEAT ceiling) + ADVERTISER API key → SEAT."""
        identity = BuyerIdentity(
            agency_id="a1",
            agency_name="Full Agency",
            advertiser_id="adv1",
            advertiser_name="Big Brand",
        )
        # Registry resolved max_access_tier=SEAT for this agent
        ctx = BuyerContext(
            identity=identity,
            is_authenticated=True,
            authentication_method="api_key",
            max_access_tier=AccessTier.SEAT,
        )
        assert identity.identity_level == IdentityLevel.AGENCY_AND_ADVERTISER
        assert ctx.effective_tier == AccessTier.SEAT

    def test_unregistered_agent_with_key(self):
        """Unregistered agent (PUBLIC ceiling) + AGENCY key → PUBLIC."""
        identity = BuyerIdentity(agency_id="a1")
        ctx = BuyerContext(
            identity=identity,
            is_authenticated=True,
            authentication_method="api_key",
            max_access_tier=AccessTier.PUBLIC,
        )
        assert ctx.effective_tier == AccessTier.PUBLIC
