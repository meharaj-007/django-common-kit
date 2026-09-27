"""Path rules shared by every tracking writer and by the IP blocker (PRD §8).

**One exclusion list, read by every writer.** Two lists drift, and the day they
drift the audit log and the visit log disagree about how many requests happened,
with no way to tell which is right.

**Every pattern is anchored to a path-segment boundary.** ``^/health`` would
also swallow the blog post ``/health-and-safety-when-moving/``; ``^/api`` would
swallow ``/apartment-moving-guide/``. On the blocker side the cost is worse
than lost traffic: a bare substring ``s3`` matches the random
``secrets.token_urlsafe(32)`` in an email verification link about once every
fifty signups and bans the customer's whole office NAT for clicking the link we
sent them. Build a pattern with
``segment``/``prefix``/``filename`` or write the boundaries by hand. **Match
nothing rather than match loosely** — a missed probe costs a 404, an invented
one costs a customer.

Paths are lower-cased before matching; write patterns lower-case.
"""

import re

from django_common_utils.conf import app_settings


def segment(token: str) -> str:
    """Match ``token`` only as a COMPLETE path segment: ``/s3`` or ``/s3/``."""
    return rf"(?:^|/){token}(?:/|$)"


def prefix(token: str) -> str:
    """Match a segment that STARTS with ``token``: ``/phpmyadmin4.8.5/``."""
    return rf"(?:^|/){token}[^/]*(?:/|$)"


def filename(token: str) -> str:
    """Match a segment whose filename ends in ``token``, plus one further
    extension: ``/.env``, ``/app/.env.production``, ``/dump.sql.gz``."""
    return rf"(?:^|/)[^/]*{token}(?:\.[^/]*)?(?:/|$)"


#: Requests no tracking writer records at all.
DEFAULT_EXCLUDED_PATHS = [
    r"^/admin/",                 # staff tooling: high volume, low signal, bodies full of everything
    r"^/favicon\.ico$",
    r"^/static/",                # served by WhiteNoise/CDN; volume dwarfs real traffic
    r"^/media/",
    r"^/robots\.txt$",
    r"^/sitemap[\w.-]*\.xml$",
    r"^/health(/|$)",            # load-balancer probes: constant rate, zero meaning
    r"^/ping(/|$)",
    r"^/api/webhooks/",          # server-to-server; payloads are third-party secrets
]

#: Response content types whose bodies are never stored: binary, huge, or both.
DEFAULT_SKIP_BODY_CONTENT_TYPES = (
    "application/pdf", "application/octet-stream", "application/zip",
    "image/", "video/", "audio/", "font/",
)

#: Paths that are probes. Deliberately absent: ``console``, ``identity``,
#: ``terraform``, ``docker`` and ``hudson`` — each is an ordinary English word
#: or a real Australian suburb, so as a whole-segment match each would 403 a
#: reader of a page we published.
DEFAULT_IP_BLOCK_PATTERNS = [
    # Dotfiles, dumps and backups
    filename(r"\.env(?:[.\-_][^/]*)?"),
    filename(r"\.git(?:ignore|attributes|modules|config|-credentials)?"),
    filename(r"\.htaccess"),
    filename(r"\.htpasswd"),
    filename(r"wp-config\.php"),
    filename(r"config\.php"),
    filename(r"\.sql"),
    filename(r"\.bak"),
    filename(r"\.old"),
    filename(r"\.backup"),
    filename(r"\.swp"),
    filename(r"\.config"),
    filename(r"\.production"),
    filename(r"\.dockerenv"),
    # Infrastructure endpoints probed by name
    segment(r"geoserver"),
    segment(r"nginx"),
    filename(r"nginx\.conf"),
    segment(r"nginx_status"),
    segment(r"webui"),
    segment(r"s3"),
    segment(r"configs"),
    segment(r"k8s"),
    prefix(r"subdomains"),
    # Config and credential files
    filename(r"\.aws"),
    prefix(r"aws[-_]config"),
    prefix(r"aws[-_]credentials"),
    filename(r"credentials\.(?:json|yml|yaml|xml|ini|csv)"),
    filename(r"config\.(?:js|json|yml|yaml|xml|ini|env|php|conf|toml)"),
    filename(r"\.circleci"),
    r"(?:^|/)\.github/workflows(?:/|$)",
    filename(r"\.gitlab-ci"),
    filename(r"\.terraformrc"),
    filename(r"\.tfvars"),
    prefix(r"secrets\."),
    filename(r"parameters\.(?:yml|yaml|xml|json|ini)"),
    prefix(r"sendgrid[-_]keys"),
    r"(?:^|/)\.kube/config(?:/|$)",
    # Database and admin access
    prefix(r"phpmyadmin"),
    prefix(r"php[-_.]?info"),
    filename(r"info\.php"),
    prefix(r"db(?:admin|web|manager)"),
    prefix(r"mysql(?:admin|manager)"),
    filename(r"adminer\.php"),
    prefix(r"sqladmin"),
    segment(r"wp-admin"),
    segment(r"administrator"),
    filename(r"admin\.php"),
    # System/file access attempts
    filename(r"\.ssh"),
    filename(r"\.vscode"),
    prefix(r"_profiler"),
    filename(r"\.svn"),
    filename(r"\.idea"),
    filename(r"\.ds_store"),
    segment(r"actuator"),
    r"(?:^|/)solr/admin(?:/|$)",
    segment(r"jenkins"),
    segment(r"wp-content"),
    segment(r"wp-includes"),
    # API and web vulnerabilities
    segment(r"cgi-bin"),
    r"(?:^|/)owa/auth(?:/|$)",
    segment(r"dana-na"),
    prefix(r"boaform"),
    segment(r"hnap1"),
    segment(r"phpunit"),
    filename(r"eval-stdin\.php"),
    r"(?:^|/)api/sonicos(?:/|$)",
    prefix(r"autodiscover"),
    segment(r"wp-json"),
    r"(?:^|/)rest/applinks(?:/|$)",
    r"(?:^|/)confluence/rest(?:/|$)",
    filename(r"telerik\.web\.ui"),
    segment(r"ckeditor"),
    r"(?:^|/)portal/redlion(?:/|$)",
    segment(r"nmaplowercheck"),
    segment(r"logincheck"),
    # Cloud metadata
    r"(?:^|/)latest/meta-data(?:/|$)",
    r"(?:^|/)169\.254\.169\.254(?:/|$)",
    # Webshells
    r"(?:^|/)(?:sh|up|tz|token|time|test[^/]*|temp|old_phpinfo|lindex|jo|inf|in|i)\.php$",
    # Server status and diagnostics
    prefix(r"server[-_]status"),
    filename(r"status\.php"),
    prefix(r"server[-_]info"),
    # Docker/Kubernetes
    filename(r"docker-compose\.(?:yml|yaml)"),
    filename(r"dockerfile"),
    r"(?:^|/)containers/json(?:/|$)",
    r"(?:^|/)pools/default/buckets(?:/|$)",
    # Authentication endpoints of other products
    filename(r"login\.(?:php|jsp|do|action|htm|html|aspx|cc)"),
    prefix(r"logon\."),
    prefix(r"auth\."),
    segment(r"j_spring_security_check"),
]


class _Compiled:
    """Patterns compiled once per settings value, re-read on `setting_changed`.

    Not at import — a module-level ``[re.compile(p) for p in settings.X]``
    freezes the list at first import and makes ``override_settings`` a lie.
    """

    def __init__(self, *path):
        self.path = path
        self._source = None
        self._compiled = ()

    def get(self):
        source = app_settings.get(*self.path)
        if source is not self._source:
            self._compiled = tuple(re.compile(p, re.IGNORECASE) for p in source if p)
            self._source = source
        return self._compiled


_excluded = _Compiled("TRACKING", "EXCLUDED_PATHS")
_block = _Compiled("TRACKING", "IP_BLOCK_PATTERNS")
_exempt = _Compiled("TRACKING", "IP_BLOCK_EXEMPT_PATHS")


def is_excluded_path(path: str) -> bool:
    """True if no tracking writer should record this request at all."""
    lowered = (path or "").lower()
    return any(pattern.match(lowered) for pattern in _excluded.get())


def is_exempt_from_blocking(path: str) -> bool:
    """Our own token-bearing routes, which are never treated as probing."""
    return any(pattern.search(path) for pattern in _exempt.get())


def matching_block_pattern(path: str):
    """The first probe pattern ``path`` matches, or ``None``."""
    lowered = (path or "").lower()
    if is_exempt_from_blocking(lowered):
        return None
    for pattern in _block.get():
        if pattern.search(lowered):
            return pattern.pattern
    return None
