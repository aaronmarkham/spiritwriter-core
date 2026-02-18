"""Tests for spiritwriter.secrets — keychain abstraction."""

import os
from unittest.mock import patch, MagicMock

from spiritwriter.secrets.keychain import (
    get_api_key,
    set_api_key,
    delete_api_key,
    list_api_keys,
    configure,
    register_keys,
    is_keyring_available,
    KNOWN_KEYS,
    SERVICE_NAME,
)


class TestGetApiKey:
    def test_env_fallback(self):
        with patch.dict(os.environ, {"TEST_KEY_123": "secret-val"}):
            val = get_api_key("TEST_KEY_123")
            assert val == "secret-val"

    def test_missing_key_returns_none(self):
        assert get_api_key("NONEXISTENT_KEY_XYZ_999") is None

    def test_no_env_fallback(self):
        with patch.dict(os.environ, {"TEST_KEY_456": "val"}, clear=False):
            # With fallback disabled and no keyring, should return None
            with patch("spiritwriter.secrets.keychain._get_keyring", return_value=None):
                val = get_api_key("TEST_KEY_456", fallback_to_env=False)
                assert val is None

    def test_keyring_takes_priority(self):
        mock_keyring = MagicMock()
        mock_keyring.get_password.return_value = "keyring-val"
        with patch("spiritwriter.secrets.keychain._get_keyring", return_value=mock_keyring):
            with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "env-val"}):
                val = get_api_key("ANTHROPIC_API_KEY")
                assert val == "keyring-val"


class TestConfigure:
    def test_configure_adds_keys(self):
        original = dict(KNOWN_KEYS)
        try:
            configure(extra_keys={"MY_CUSTOM_KEY": "Custom key"})
            assert "MY_CUSTOM_KEY" in KNOWN_KEYS
        finally:
            # Restore
            KNOWN_KEYS.clear()
            KNOWN_KEYS.update(original)

    def test_register_keys(self):
        original = dict(KNOWN_KEYS)
        try:
            register_keys({"ANOTHER_KEY": "Another"})
            assert "ANOTHER_KEY" in KNOWN_KEYS
        finally:
            KNOWN_KEYS.clear()
            KNOWN_KEYS.update(original)


class TestSetApiKey:
    def test_no_keyring(self):
        with patch("spiritwriter.secrets.keychain._get_keyring", return_value=None):
            assert set_api_key("X", "val") is False

    def test_with_keyring(self):
        mock_keyring = MagicMock()
        with patch("spiritwriter.secrets.keychain._get_keyring", return_value=mock_keyring):
            assert set_api_key("X", "val") is True
            mock_keyring.set_password.assert_called_once()


class TestDeleteApiKey:
    def test_no_keyring(self):
        with patch("spiritwriter.secrets.keychain._get_keyring", return_value=None):
            assert delete_api_key("X") is False


class TestListApiKeys:
    def test_lists_known_keys(self):
        with patch("spiritwriter.secrets.keychain._get_keyring", return_value=None):
            status = list_api_keys()
            for key in KNOWN_KEYS:
                assert key in status


class TestKeyringAvailable:
    def test_no_keyring_module(self):
        with patch("spiritwriter.secrets.keychain._get_keyring", return_value=None):
            assert is_keyring_available() is False
