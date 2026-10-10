"""Desktop secret service (KWallet through Secret Service/keyring) or explicit environment."""
import os


def get_key(provider):
    value = os.environ.get(f"KIZURIUM_{provider.upper()}_API_KEY", "")
    if value:
        return value
    try:
        import keyring
        return keyring.get_password("kizurium-translator", provider) or ""
    except Exception:
        return ""


def set_key(provider, value):
    try:
        import keyring
        # Refuse plaintext/null keyring implementations.
        backend = type(keyring.get_keyring()).__module__.lower()
        if not any(name in backend for name in ("secretservice", "kwallet")):
            raise RuntimeError
        if value:
            keyring.set_password("kizurium-translator", provider, value)
        else:
            keyring.delete_password("kizurium-translator", provider)
    except Exception:
        raise RuntimeError("KWallet / Secret Service unavailable. Install keyring or set KIZURIUM_<PROVIDER>_API_KEY.") from None
