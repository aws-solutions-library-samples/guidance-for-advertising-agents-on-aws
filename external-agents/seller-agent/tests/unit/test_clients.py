# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Unit tests for Ad Seller System clients."""

from unittest.mock import MagicMock, patch

from ad_seller.clients import Protocol


class TestProtocolEnum:
    """Tests for Protocol enum."""

    def test_protocol_values(self):
        """Test protocol enum values."""
        assert Protocol.OPENDIRECT_21.value == "opendirect21"
        assert Protocol.A2A.value == "a2a"


class TestUnifiedClientProtocol:
    """Tests for UnifiedClient protocol handling."""

    @patch("ad_seller.clients.unified_client.get_settings")
    def test_client_initialization_od21(self, mock_settings):
        """Test client initialization with OpenDirect 2.1."""
        mock_settings.return_value = MagicMock(
            opendirect_base_url="https://example.com",
            default_protocol="opendirect21",
        )

        from ad_seller.clients import UnifiedClient

        client = UnifiedClient(
            base_url="https://example.com",
            protocol=Protocol.OPENDIRECT_21,
        )
        assert client.default_protocol == Protocol.OPENDIRECT_21
        assert client.base_url == "https://example.com"

    @patch("ad_seller.clients.unified_client.get_settings")
    def test_default_protocol_from_settings(self, mock_settings):
        """Test default protocol is read from settings."""
        mock_settings.return_value = MagicMock(
            opendirect_base_url="https://test.example.com",
            default_protocol="opendirect21",
        )

        from ad_seller.clients import UnifiedClient

        client = UnifiedClient(base_url="https://test.example.com")
        assert client.default_protocol == Protocol.OPENDIRECT_21

    @patch("ad_seller.clients.unified_client.get_settings")
    def test_a2a_protocol(self, mock_settings):
        """Test A2A protocol selection."""
        mock_settings.return_value = MagicMock(
            opendirect_base_url="https://example.com",
            default_protocol="a2a",
        )

        from ad_seller.clients import UnifiedClient

        client = UnifiedClient(
            base_url="https://example.com",
            protocol=Protocol.A2A,
        )
        assert client.default_protocol == Protocol.A2A


class TestUnifiedResult:
    """Tests for UnifiedResult model."""

    def test_unified_result_success(self):
        """Test UnifiedResult for successful response."""
        from ad_seller.clients.unified_client import UnifiedResult

        result = UnifiedResult(
            success=True,
            data={"organizations": [{"name": "Test Org"}]},
            protocol=Protocol.OPENDIRECT_21,
        )
        assert result.success is True
        assert result.data["organizations"][0]["name"] == "Test Org"
        assert result.error is None

    def test_unified_result_failure(self):
        """Test UnifiedResult for failed response."""
        from ad_seller.clients.unified_client import UnifiedResult

        result = UnifiedResult(
            success=False,
            error="Connection refused",
            protocol=Protocol.OPENDIRECT_21,
        )
        assert result.success is False
        assert result.error == "Connection refused"
        assert result.data is None
