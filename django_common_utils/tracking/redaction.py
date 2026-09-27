"""Credential redaction for request tracking (PRD §8).

Everything written into ``request_logs`` and ``ip_tracking`` passes through
here first. Redaction happens on the way *into* the row — never at display
time — so a credential is never on disk, in a backup, or in an admin CSV export.

Two rules that are easy to get wrong:

* Redaction runs **before** truncation. Reverse them and a token survives by
  sitting past the cut-off in a body that no longer parses as JSON.
* Bodies larger than ``MAX_PARSE_SIZE`` are not deserialised. A file upload is
  not worth parsing to find a password in; the regex pass still covers it.

A project adds keys through ``DJANGO_COMMON_UTILS["TRACKING"]["REDACT_KEYS"]``; the
set below is the floor and cannot be lowered.
"""

import json
import logging
import re
from typing import Any, Iterable, Optional
from urllib.parse import parse_qsl, quote, urlencode

from django_common_utils.conf import app_settings

logger = logging.getLogger(__name__)

MASK = "***REDACTED***"

# Above this size the structured parse is skipped and the regex pass runs alone.
MAX_PARSE_SIZE = 200 * 1024

# Masked wherever they appear, in bodies and in query strings.
SENSITIVE_KEYS = frozenset({
    "password", "password1", "password2", "old_password", "new_password",
    "confirm_password", "current_password",
    "access", "refresh", "token", "access_token", "refresh_token", "id_token",
    "secret", "client_secret", "api_key", "apikey", "authorization",
    "credential", "credentials", "private_key",
    "auth_token", "webhook_token", "signing_secret", "secret_key", "api_secret",
    "otp", "pin", "card_number", "cvv",
})

# Query-string-only additions. Signed URLs put credentials in the path, and the
# path is the field everyone assumes is safe.
SENSITIVE_QUERY_KEYS = SENSITIVE_KEYS | frozenset({
    "signature", "sig", "magic_link", "reset_token", "verification_token", "uidb64",
})

# Keys that are only credentials under certain paths. `code` is an OAuth
# credential under /auth/ and a discount code everywhere else — masking it
# globally guts the audit trail, masking it nowhere leaks tokens.
PATH_SCOPED_KEYS = {
    "code": ("/auth", "/api/auth", "/oauth", "/accounts"),
    "state": ("/auth", "/api/auth", "/oauth"),
}


def _project_keys() -> frozenset:
    return frozenset(
        (key or "").strip().lower().replace("-", "_")
        for key in app_settings.get("TRACKING", "REDACT_KEYS")
    )


def _is_sensitive(key: str, path: str, extra: Iterable[str] = ()) -> bool:
    normalised = (key or "").strip().lower().replace("-", "_")
    if normalised in SENSITIVE_KEYS or normalised in _project_keys() or normalised in set(extra):
        return True
    prefixes = PATH_SCOPED_KEYS.get(normalised)
    if prefixes:
        lowered = (path or "").lower()
        return any(lowered.startswith(p) for p in prefixes)
    return False


def redact_structure(value: Any, path: str = "") -> Any:
    """Recursively mask sensitive keys in a parsed JSON structure."""
    if isinstance(value, dict):
        return {
            key: MASK if _is_sensitive(str(key), path) else redact_structure(item, path)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_structure(item, path) for item in value]
    return value


def _regex_redact(text: str, path: str) -> str:
    """Best-effort masking for content that could not be parsed: oversized
    bodies, form-encoded payloads, multipart uploads, malformed JSON."""
    keys = set(SENSITIVE_KEYS) | _project_keys()
    keys.update(key for key in PATH_SCOPED_KEYS if _is_sensitive(key, path))
    alternation = "|".join(re.escape(key) for key in sorted(keys))

    # "password": "value"  /  'password': 'value'
    text = re.sub(
        rf'(["\']({alternation})["\']\s*:\s*)(["\']).*?(?<!\\)\3',
        rf'\g<1>"{MASK}"', text, flags=re.IGNORECASE | re.DOTALL,
    )
    # password=value (form-encoded / query string fragments)
    text = re.sub(
        rf'\b({alternation})=[^&\s"\']*', rf"\g<1>={MASK}", text, flags=re.IGNORECASE,
    )
    # Content-Disposition: form-data; name="password"\r\n\r\nvalue
    text = re.sub(
        rf'(name=["\']({alternation})["\'][^\r\n]*\r?\n\r?\n)[^\r\n]*',
        rf"\g<1>{MASK}", text, flags=re.IGNORECASE,
    )
    return text


def redact_text(text: Optional[str], path: str = "") -> Optional[str]:
    """Mask credentials in a request or response body. Call *before* truncating."""
    if not text:
        return text
    if len(text) <= MAX_PARSE_SIZE:
        try:
            parsed = json.loads(text)
        except (ValueError, TypeError):
            parsed = None
        if isinstance(parsed, (dict, list)):
            try:
                return json.dumps(redact_structure(parsed, path), default=str)
            except (TypeError, ValueError):
                pass
    return _regex_redact(text, path)


def scrub_query_string(query_string: Optional[str], path: str = "") -> Optional[str]:
    """Mask credentials in a raw query string, keeping order and the
    non-sensitive values so a human can still read the row."""
    if not query_string:
        return query_string
    try:
        pairs = parse_qsl(query_string, keep_blank_values=True)
    except (ValueError, UnicodeDecodeError):
        return _regex_redact(query_string, path)
    if not pairs:
        return _regex_redact(query_string, path)
    scrubbed = [
        (key, MASK if _is_sensitive(key, path, extra=SENSITIVE_QUERY_KEYS) else value)
        for key, value in pairs
    ]
    # safe='*' keeps the mask readable as ***REDACTED*** instead of %2A%2A%2A.
    return urlencode(scrubbed, quote_via=quote, safe="*")


def scrub_url(url: Optional[str], path: str = "") -> Optional[str]:
    """Mask credentials inside the query string of a full URL."""
    if not url or "?" not in url:
        return url
    base, _, query = url.partition("?")
    return f"{base}?{scrub_query_string(query, path or base)}"


def truncate(content: Optional[str], max_size: int, suffix: str = "... [truncated]") -> Optional[str]:
    """Truncate after redaction — never before it."""
    if not content:
        return None
    if len(content) > max_size:
        return content[:max_size] + suffix
    return content
