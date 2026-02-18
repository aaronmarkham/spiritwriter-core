"""
Secure API key management using OS keychain.

Supports:
- Windows: Windows Credential Manager
- macOS: macOS Keychain
- Linux: Secret Service API (GNOME Keyring, KWallet)

Usage:
    from spiritwriter.secrets import get_api_key, set_api_key

    # Get key (checks keychain first, falls back to env var)
    key = get_api_key("OPENAI_API_KEY")

    # Store key in keychain
    set_api_key("OPENAI_API_KEY", "sk-...")
"""

import os
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Service name for keychain entries — override via configure()
SERVICE_NAME = "spiritwriter"

# Known API key registry — projects can extend via register_keys()
# Core keys that any spiritwriter-based project might use
KNOWN_KEYS = {
    "ANTHROPIC_API_KEY": "Anthropic API key",
    "OPENAI_API_KEY": "OpenAI API key",
}


def configure(service_name: str = None, extra_keys: dict = None):
    """Configure the secrets module for your project.

    Args:
        service_name: Keychain service name (default: "spiritwriter").
        extra_keys: Additional key names to register {name: description}.
    """
    global SERVICE_NAME, KNOWN_KEYS
    if service_name:
        SERVICE_NAME = service_name
    if extra_keys:
        KNOWN_KEYS.update(extra_keys)


def register_keys(keys: dict):
    """Register additional known API key names.

    Args:
        keys: Dict mapping key names to descriptions.
    """
    KNOWN_KEYS.update(keys)


def _get_keyring():
    """Lazy import keyring to handle missing dependency gracefully."""
    try:
        import keyring
        return keyring
    except ImportError:
        return None


def get_api_key(key_name: str, fallback_to_env: bool = True) -> Optional[str]:
    """
    Get an API key, checking keychain first then environment variables.

    Args:
        key_name: Name of the API key (e.g., "OPENAI_API_KEY")
        fallback_to_env: If True, check environment variables if not in keychain

    Returns:
        The API key value, or None if not found
    """
    # Try keychain first
    keyring = _get_keyring()
    if keyring:
        try:
            value = keyring.get_password(SERVICE_NAME, key_name)
            if value:
                logger.debug(f"Retrieved {key_name} from secure keychain")
                return value
        except Exception as e:
            logger.debug(f"Keychain access failed for {key_name}: {e}")

    # Fall back to environment variable
    if fallback_to_env:
        value = os.environ.get(key_name)
        if value:
            logger.debug(f"Retrieved {key_name} from environment variable")
            return value

    return None


def set_api_key(key_name: str, value: str) -> bool:
    """
    Store an API key in the OS keychain.

    Args:
        key_name: Name of the API key (e.g., "OPENAI_API_KEY")
        value: The API key value to store

    Returns:
        True if successful, False otherwise
    """
    keyring = _get_keyring()
    if not keyring:
        logger.error("keyring package not installed. Run: pip install keyring")
        return False

    try:
        keyring.set_password(SERVICE_NAME, key_name, value)
        logger.info(f"Stored {key_name} in secure keychain")
        return True
    except Exception as e:
        logger.error(f"Failed to store {key_name} in keychain: {e}")
        return False


def delete_api_key(key_name: str) -> bool:
    """
    Delete an API key from the OS keychain.

    Args:
        key_name: Name of the API key to delete

    Returns:
        True if successful, False otherwise
    """
    keyring = _get_keyring()
    if not keyring:
        logger.error("keyring package not installed. Run: pip install keyring")
        return False

    try:
        keyring.delete_password(SERVICE_NAME, key_name)
        logger.info(f"Deleted {key_name} from secure keychain")
        return True
    except keyring.errors.PasswordDeleteError:
        logger.warning(f"{key_name} not found in keychain")
        return False
    except Exception as e:
        logger.error(f"Failed to delete {key_name} from keychain: {e}")
        return False


def list_api_keys() -> dict:
    """
    List all known API keys and their status.

    Returns:
        Dict mapping key names to their status:
        - "keychain": stored in secure keychain
        - "env": available in environment variable
        - "not_set": not configured
    """
    status = {}
    keyring = _get_keyring()

    for key_name in KNOWN_KEYS:
        # Check keychain
        if keyring:
            try:
                if keyring.get_password(SERVICE_NAME, key_name):
                    status[key_name] = "keychain"
                    continue
            except Exception:
                pass

        # Check environment
        if os.environ.get(key_name):
            status[key_name] = "env"
        else:
            status[key_name] = "not_set"

    return status


def import_from_env_file(env_path: str) -> dict:
    """
    Import API keys from a .env file into the keychain.

    Args:
        env_path: Path to the .env file

    Returns:
        Dict mapping key names to success status
    """
    from pathlib import Path

    results = {}
    env_file = Path(env_path)

    if not env_file.exists():
        raise FileNotFoundError(f"File not found: {env_path}")

    with open(env_file, "r") as f:
        for line in f:
            line = line.strip()
            # Skip comments and empty lines
            if not line or line.startswith("#"):
                continue

            # Parse KEY=value
            if "=" in line:
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")

                # Only import known API keys
                if key in KNOWN_KEYS and value:
                    results[key] = set_api_key(key, value)

    return results


def is_keyring_available() -> bool:
    """Check if keyring functionality is available."""
    keyring = _get_keyring()
    if not keyring:
        return False

    # Try a test operation to verify backend works
    try:
        # This will fail gracefully if no backend is available
        keyring.get_keyring()
        return True
    except Exception:
        return False