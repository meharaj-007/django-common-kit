"""Settings access (PRD §4).

One ``DJANGO_COMMON_UTILS`` dict, and only that: every knob the package has is a key
in it, every project configures it the same way, and there is no second place a
value can come from. A setting the package does not own — ``AUTH_USER_MODEL``,
``MEDIA_ROOT``, ``AWS_BUCKET_NAME``, ``DEBUG`` — is read from Django directly.

Nothing here is read at import time. Every lookup goes through ``app_settings``
so that ``django.test.override_settings`` is seen immediately — a module-level
``PAGE_SIZE = settings.DJANGO_COMMON_UTILS[...]`` freezes the value at first import
and makes every override in the test suite a lie.
"""

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.signals import setting_changed
from django.utils.module_loading import import_string

SETTINGS_KEY = "DJANGO_COMMON_UTILS"

DEFAULTS = {
    # Display-only admin sidebar relabelling (see apps.py). Off for a project
    # that has already named these itself and would see its labels overwritten.
    "RENAME_THIRD_PARTY_ADMIN_APPS": True,

    # -- tenancy (§3.5, tenancy.py) ------------------------------------------
    "TENANT": {
        # The attribute on the project's own models that holds their tenant's
        # id ("organization_id"). A history row or status transition about such
        # a row takes its tenant from it. Empty = not used.
        "INSTANCE_ATTRIBUTE": "",
        # Dotted path to callable(request) -> tenant id | None, for rows about a
        # request (request logs, visits) and as the fallback. Empty = not used.
        "RESOLVER": "",
    },

    # -- change history (§6) ------------------------------------------------
    "HISTORY": {
        "ENABLED": True,
        # Applied on top of each model's own ``_history_exclude_fields``. These
        # two change on every save and would double the trail's size to record
        # that a row was saved.
        "EXCLUDE_FIELDS": ["updated_at", "updated_by"],
        # A diff value longer than this is truncated before it is stored. A
        # TextField column will happily accept a 5MB pasted document on every
        # edit; the trail exists to answer "what changed", not to be a second
        # copy of the table.
        "MAX_VALUE_LENGTH": 1000,
        # 0 = keep forever. Anything else is swept by ``purge_history``.
        "RETENTION_DAYS": 0,
    },

    # -- system parameters (§7) ---------------------------------------------
    "PARAMETERS": {
        "AUTO_LOAD_ON_STARTUP": True,
        "CACHE_ALIAS": "default",
        "CACHE_TTL_SECONDS": 3600,
        # Dotted path to the project's seed catalogue, consumed by
        # ``seed_parameters``. Unset means the project seeds nothing and the
        # table starts empty — which is correct, because a parameter's *name*
        # is project vocabulary.
        "SEED_CATALOG": "",
    },

    # -- request and IP tracking (§8) ---------------------------------------
    "TRACKING": {
        "REQUEST_LOG_ENABLED": True,
        "IP_TRACKING_ENABLED": True,
        "IP_BLOCKER_ENABLED": True,
        # Write the referer and UTM columns on ip_tracking. Off by default:
        # most sites want first-touch attribution in a session, which is the
        # project's policy, not a per-request column.
        "CAPTURE_ATTRIBUTION": False,
        # Days. 0 = keep forever. A request_logs row carries a full request and
        # response body, so its window is much shorter than a visit row's.
        "REQUEST_LOG_RETENTION_DAYS": 30,
        "IP_TRACKING_RETENTION_DAYS": 365,
        # Requests no writer records. Anchored patterns, lower-case — see
        # tracking/patterns.py. Replaces the list, so include the defaults.
        "EXCLUDED_PATHS": None,  # resolved to patterns.DEFAULT_EXCLUDED_PATHS
        "SKIP_BODY_CONTENT_TYPES": None,  # patterns.DEFAULT_SKIP_BODY_CONTENT_TYPES
        # Characters of body stored per row, after redaction.
        "MAX_CONTENT_SIZE": 10_000,
        # Extra keys masked out of bodies and query strings, on top of the set
        # in tracking/redaction.py, which cannot be lowered.
        "REDACT_KEYS": [],
        # Geo-IP provider, with {ip} substituted. Empty = no lookups. The
        # default is a free, HTTP, non-commercial endpoint; production points
        # this at its own provider.
        "GEO_LOOKUP_URL": (
            "http://ip-api.com/json/{ip}?fields=status,message,continent,continentCode,"
            "country,countryCode,region,regionName,city,district,zip,lat,lon,timezone,"
            "currency,isp,org,as,mobile,query"
        ),
        # How many proxy hops to trust in X-Forwarded-For. 0 means take
        # REMOTE_ADDR and ignore the header entirely — the only safe default,
        # since a client can forge any hop the count lets it forge.
        "TRUSTED_PROXY_COUNT": 0,
        # The proxies themselves, as addresses or CIDRs ("10.0.0.0/8"). Set, the
        # header is honoured only on a connection from one of them, so a client
        # that reaches the app directly cannot forge its address. Empty = any
        # peer is taken to be the proxy. Leave it empty when the proxy talks to
        # the app over a unix socket, where REMOTE_ADDR is not an address.
        "TRUSTED_PROXY_IPS": [],
        # -- the IP blocker --
        "IP_WHITELIST": ["127.0.0.1", "::1"],
        "IP_BLOCK_PATTERNS": None,  # patterns.DEFAULT_IP_BLOCK_PATTERNS
        # Our own token-bearing routes, never inspected. A project lists its
        # verify-email and password-reset shapes here.
        "IP_BLOCK_EXEMPT_PATHS": [],
        "IP_BLOCK_MAX_ATTEMPTS": 3,
        "IP_BLOCK_ATTEMPT_WINDOW_SECONDS": 24 * 60 * 60,
        "IP_BLOCK_DURATION_SECONDS": 7 * 24 * 60 * 60,
        "IP_BLOCK_CACHE_TTL_SECONDS": 60,
        "IP_BLOCK_CACHE_MAX_STALE_SECONDS": 600,
    },

    # -- file attachments (§9) ----------------------------------------------
    "FILES": {
        "MAX_UPLOAD_BYTES": 10 * 1024 * 1024,
        # Empty = accept anything. A project that accepts only PDFs says so.
        "ALLOWED_MIME_TYPES": [],
        "UPLOAD_ROOT": "common/files",
    },

    # -- REST surface (§5) --------------------------------------------------
    "PAGINATION": {
        "PAGE_SIZE": 25,
        "MAX_PAGE_SIZE": 200,
        "PAGE_SIZE_QUERY_PARAM": "page_size",
        # The whitelist the frontend's page-size selector offers. A requested
        # size outside it falls back to PAGE_SIZE rather than being clamped —
        # see pagination.py.
        "ALLOWED_PAGE_SIZES": [10, 25, 50, 100, 150, 200],
    },
    "RESPONSE": {
        # None = include the request id only when DEBUG. True/False force it.
        "INCLUDE_REQUEST_ID": None,
        "INCLUDE_STATUS_CODE_IN_BODY": True,
        # 400 matches what DRF raises for the same failure via
        # is_valid(raise_exception=True), so a client sees one status for a
        # validation failure whichever code path produced it. A project whose
        # clients already expect 422 sets it here. See §5.1.
        "VALIDATION_ERROR_STATUS": 400,
        # A machine-readable ``code`` on every error body, defaulting per status
        # (response.DEFAULT_ERROR_CODES) and overridable with ``code=`` on each
        # error helper. Off by default: a new key in the envelope is a contract
        # change for every client that did not ask for it.
        "ERROR_CODES": False,
        # {status: code}, over DEFAULT_ERROR_CODES. Only read with ERROR_CODES on.
        "ERROR_CODE_BY_STATUS": {},
        # When set, every error body carries the request's correlation id under
        # this key ("trace_id", say) — always, not only under DEBUG like
        # INCLUDE_REQUEST_ID. None = not at all.
        "ERROR_TRACE_ID_KEY": None,
        # Top-level packages whose exception messages were written for people and
        # may reach a client. Empty = discover them by listing BASE_DIR for
        # packages; name them for a src-layout project, where the scan finds
        # nothing useful.
        "AUTHORED_EXCEPTION_PACKAGES": [],
    },
    "PERMISSION_CLASSES": [],
    "ADMIN_PERMISSION_CLASSES": [],

    # -- view-level rate limiting (§5.2, api/rate_limiters.py) ---------------
    "RATE_LIMIT": {
        # Master switch. Off in a test suite that does not want to reason
        # about cache state between cases.
        "ENABLED": True,
        # The cache every limit counts in — the DRF throttles in throttling.py
        # and the view-level limiters here alike. Point it at a real cache when
        # ``default`` is DummyCache (common under DEBUG), or no limit holds.
        "CACHE_ALIAS": "default",
        # Where html_rate_limit sends a browser with no Referer. Unset = a
        # plain 429 page.
        "HTML_FALLBACK_URL": "",
    },

    # -- phone (§11) --------------------------------------------------------
    "PHONE": {
        "DEFAULT_REGION": "AU",
        "SERVICEABLE_REGIONS": ["AU"],
    },

    # -- storage (§9.3) -----------------------------------------------------
    "STORAGE": {
        # "local" | "s3". Named rather than inferred from AWS_* being present,
        # because a project with S3 credentials configured for something else
        # should not silently start writing its media there.
        "TYPE": "local",
        "MEDIA_LOCATION": "media",
        "FILE_OVERWRITE": False,
    },

    # -- short links (§10) --------------------------------------------------
    "SHORT_LINK": {
        # The public origin the redirect is served from. Unset = shorten()
        # returns the long URL and warns.
        "BASE_URL": "",
        "PATH_PREFIX": "s",
        "CODE_LENGTH": 7,
    },

    # -- encryption at rest (§17, crypto/) ------------------------------------
    "ENCRYPTION": {
        # Fernet keys, newest first: the first encrypts, every key decrypts.
        # A single string is a list of one. Set from the environment, never in
        # source. No keys = no encryption; nothing is derived from SECRET_KEY.
        "KEYS": [],
    },

    # -- platform notices (§16) ---------------------------------------------
    "NOTICES": {
        # Dotted path to callable(request) -> iterable of the audience names
        # the viewer belongs to. The names are project vocabulary. Unset = the
        # viewer belongs to none and sees only notices with no audience — a
        # notice aimed at "admins" must not reach everyone because nobody
        # wired the resolver.
        "AUDIENCE_RESOLVER": "",
        "CACHE_ALIAS": "default",
        # Every page load asks for the live notices. 0 = no cache, every read
        # goes to the database. A save or delete drops the cache either way.
        "CACHE_TTL_SECONDS": 60,
    },
}

def _tracking_default(name):
    """Defaults that live in tracking/patterns.py, resolved lazily so conf.py
    does not import a module that imports conf.py."""
    def resolve():
        from django_common_utils.tracking import patterns

        return getattr(patterns, name)
    return resolve


#: Settings whose default is `None` above and is looked up here instead.
_LATE_DEFAULTS = {
    ("TRACKING", "EXCLUDED_PATHS"): _tracking_default("DEFAULT_EXCLUDED_PATHS"),
    ("TRACKING", "SKIP_BODY_CONTENT_TYPES"): _tracking_default("DEFAULT_SKIP_BODY_CONTENT_TYPES"),
    ("TRACKING", "IP_BLOCK_PATTERNS"): _tracking_default("DEFAULT_IP_BLOCK_PATTERNS"),
}


def _dig(mapping, path):
    """Walk ``path`` through nested dicts. Returns ``(found, value)``."""
    node = mapping
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return False, None
        node = node[key]
    return True, node


def _merge(default, override):
    """Deep-merge ``override`` onto ``default`` without mutating either."""
    if not isinstance(default, dict) or not isinstance(override, dict):
        return override
    merged = dict(default)
    for key, value in override.items():
        merged[key] = _merge(default.get(key), value) if key in default else value
    return merged


class AppSettings:
    """Resolve one setting path, defaults included.

    Nothing is cached across requests beyond a per-instance memo that
    ``django.test.override_settings`` clears, so tests see changes immediately.
    """

    def __init__(self):
        self._cache = {}

    # -- django signal hook -------------------------------------------------
    def reset(self, **kwargs):
        self._cache.clear()

    # -- lookup -------------------------------------------------------------
    def get(self, *path):
        if not path:
            raise TypeError("get() needs at least one key")
        if path in self._cache:
            return self._cache[path]

        configured = getattr(settings, SETTINGS_KEY, None) or {}
        found, value = _dig(configured, path)
        if not found or value is None:
            found_default, value = _dig(DEFAULTS, path)
            if not found_default:
                raise ImproperlyConfigured(
                    f"Unknown {SETTINGS_KEY} setting: {'.'.join(path)}"
                )
            late = _LATE_DEFAULTS.get(path)
            if value is None and late is not None:
                value = late()
        elif isinstance(value, dict):
            _, default_branch = _dig(DEFAULTS, path)
            value = _merge(default_branch, value)

        self._cache[path] = value
        return value

    def __getattr__(self, name):
        """``app_settings.RENAME_THIRD_PARTY_ADMIN_APPS`` for top-level keys."""
        if name.startswith("_"):
            raise AttributeError(name)
        return self.get(name)

    # -- convenience --------------------------------------------------------
    def include_request_id(self):
        configured = self.get("RESPONSE", "INCLUDE_REQUEST_ID")
        return settings.DEBUG if configured is None else bool(configured)

    def permission_classes(self, admin=False):
        """Resolve permission classes lazily — dotted paths stay unimported
        until the first request, so a project's RBAC module is never imported
        while the app registry is still loading."""
        key = "ADMIN_PERMISSION_CLASSES" if admin else "PERMISSION_CLASSES"
        return [
            import_string(entry) if isinstance(entry, str) else entry
            for entry in self.get(key)
        ]

    def hook(self, *path):
        """Resolve one optional dotted-path hook, or ``None`` when unset.

        Returning ``None`` rather than a no-op is deliberate: each caller's
        "unset" behaviour differs, and a no-op that returns ``None`` is
        indistinguishable from a project hook that genuinely returned ``None``.
        An already-imported object is accepted as-is so a test can pass the
        callable itself.
        """
        value = self.get(*path)
        if not value:
            return None
        return import_string(value) if isinstance(value, str) else value


app_settings = AppSettings()

# Connected here, at import, and not only in ``AppConfig.ready()``. The memo
# above belongs to this module, so its invalidation does too — and a project
# adopting the package rewrites its imports before it adds the app to
# INSTALLED_APPS (§13), which is exactly the window in which `ready()` never
# runs. Without this, every ``override_settings(DJANGO_COMMON_UTILS=...)`` in that
# project's suite is served a stale value and the test passes against the
# default instead of the override. ``dispatch_uid`` makes the second connect in
# ``ready()`` a no-op.
setting_changed.connect(app_settings.reset, dispatch_uid="django_common_utils.conf")
