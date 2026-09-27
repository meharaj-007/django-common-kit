# django-common-kit

[![tests](https://github.com/meharaj-007/django-common-kit/actions/workflows/tests.yml/badge.svg?branch=main)](https://github.com/meharaj-007/django-common-kit/actions/workflows/tests.yml)
[![PyPI](https://img.shields.io/pypi/v/django-common-kit)](https://pypi.org/project/django-common-kit/)
[![Python](https://img.shields.io/pypi/pyversions/django-common-kit)](https://pypi.org/project/django-common-kit/)

The foundation a Django REST backend starts from: a UUID `BaseModel`, a
change-history trail, typed system parameters with a cache, request and IP
tracking, file attachments, short links, one REST response envelope with the
views, pagination, permissions and throttling that produce it, and encryption
for the secrets a project must store.

Every project installs it, configures it and uses it the same way.

- [PRD.md](https://github.com/meharaj-007/django-common-kit/blob/main/PRD.md) — the specification, cited by section number from the code.
- [CHANGELOG.md](https://github.com/meharaj-007/django-common-kit/blob/main/CHANGELOG.md) — what changed.

## Install

From PyPI:

```bash
pip install "django-common-kit==0.11.1"
```

Or pinned to a git tag, which is the same release:

```
django-common-kit @ git+https://github.com/meharaj-007/django-common-kit.git@v0.11.1
```

Extras, all optional. The package imports and works with none of them; each
degrades in the way its row says.

| Extra | Pulls in | Without it |
| --- | --- | --- |
| `s3` | `django-storages`, `boto3` | `STORAGE["TYPE"] = "s3"` raises naming the extra |
| `phone` | `phonenumbers` | phone validation is syntax-only — see below |
| `tracking` | `requests`, `user-agents` | geo and device columns on `ip_tracking` stay blank |
| `celery` | `celery` | tracking rows are written inline instead of by a worker |
| `images` | `Pillow` | no image handling on attachments |
| `crypto` | `cryptography` | `django_common_kit.crypto` raises naming the extra |
| `all` | everything above | |

```python
INSTALLED_APPS = [
    ...
    "django_common_kit",
]

MIDDLEWARE = [
    "django_common_kit.middleware.CurrentRequestMiddleware",       # first
    "django_common_kit.tracking.middleware.IPBlockerMiddleware",   # early: a ban costs nothing
    ...
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django_common_kit.tracking.middleware.RequestLogMiddleware",  # after auth
    "django_common_kit.tracking.middleware.IPTrackingMiddleware",
]

REST_FRAMEWORK = {
    "EXCEPTION_HANDLER": "django_common_kit.api.exceptions.custom_exception_handler",
    "DEFAULT_PAGINATION_CLASS": "django_common_kit.api.pagination.CustomPageNumberPagination",
}
```

Then `manage.py migrate`. The app label is `common_control`.

## What you get

| Module | Contents |
| --- | --- |
| `django_common_kit.models` | `BaseModel`, `ParameterModel`, `ModelHistory`, `RequestLog`, `IPTrackingModel`, `BlockedIPModel`, `ContactUsModel`, `StatusTransitionModel`, `CommonFileModel`, `ShortLinkModel`, `PlatformNoticeModel`, `PlatformNoticeDismissalModel` |
| `django_common_kit.api.response` | `ApiResponse` — the envelope |
| `django_common_kit.api.exceptions` | `custom_exception_handler`, `reraise_handled`, `BusinessLogicException` and friends |
| `django_common_kit.api.errors` | `normalize_field_errors`, `extract_first_error_message` |
| `django_common_kit.api.pagination` | `CustomPageNumberPagination`, `CustomCursorSetPagination` |
| `django_common_kit.api.views` | `CustomModelViewSet`, `CustomListAPIView`, `CustomWOPListAPIView`, … |
| `django_common_kit.api.permissions` | `AdminUserPermission`, `IsOwnerOrReadOnly`, `user_type_permission()` |
| `django_common_kit.api.throttling` | logged DRF throttles, `reset_user_throttles` |
| `django_common_kit.api.rate_limiters` | `enforce_cooldown`, `api_rate_limit`, `html_rate_limit` |
| `django_common_kit.history` | `HistoryMixin`, `get_model_history`, `create_history_entry` |
| `django_common_kit.transitions` | `track_status_transition`, `get_status_transitions` |
| `django_common_kit.parameters` | `ParameterCache`, `get_parameter` |
| `django_common_kit.tracking` | redaction, patterns, writers, middleware, purge |
| `django_common_kit.shortlinks` | `shorten`, `short_link_redirect` |
| `django_common_kit.notices` | `live_notices`, `dismiss`, `LiveNoticeListView`, `NoticeDismissView` |
| `django_common_kit.phone` | `Phone` |
| `django_common_kit.storage` | `MediaStorage`, `scoped_path`, `safe_basename` |
| `django_common_kit.files` | `validate_upload` — the `FILES` size and type limits |
| `django_common_kit.request_context` | the current request, actor, client IP, correlation id |
| `django_common_kit.middleware` | `CurrentRequestMiddleware` |
| `django_common_kit.admin` | `BaseModelAdmin` |
| `django_common_kit.constants` | `ErrorMessage` |
| commands | `seed_parameters`, `purge_history`, `purge_tracking`, `unblock_ip`, `adopt_tables` |

## The response envelope

Every endpoint answers with one shape:

```json
{
  "status": "success",
  "status_code": 200,
  "message": "Results retrieved successfully",
  "data": [],
  "meta": {"total_records": 0}
}
```

`message`, `data`, `errors` and `meta` are omitted when unset. `data` is omitted
only when it is `None`, so an empty list still renders as `"data": []`.

```python
from django_common_kit.api.response import ApiResponse

return ApiResponse.success(data=serializer.data)
return ApiResponse.created(data=serializer.data)
return ApiResponse.validation_error(serializer.errors)   # 400, {field: message}
return ApiResponse.not_found()
```

**Internal exceptions never reach a client.** An error response built inside an
`except` block has the exception's text cut out unless the exception was written
for a person (a `ValueError` from a service, a DRF `APIException`, a class from
your own packages). The traceback is logged; with `DEBUG` on it also comes back
under `debug`.

## Settings

One dict. Everything has a default; the full tree and the reasoning per key are
in [`django_common_kit/conf.py`](https://github.com/meharaj-007/django-common-kit/blob/main/django_common_kit/conf.py).

```python
DJANGO_COMMON_KIT = {
    "PAGINATION": {"PAGE_SIZE": 25, "ALLOWED_PAGE_SIZES": [10, 25, 50, 100]},
    "PHONE": {"DEFAULT_REGION": "AU", "SERVICEABLE_REGIONS": ["AU"]},
    "STORAGE": {"TYPE": "s3", "MEDIA_LOCATION": "prod/media"},
    "TRACKING": {"TRUSTED_PROXY_COUNT": 1, "IP_BLOCK_EXEMPT_PATHS": [r"^/api/auth/verify/[^/]*/?$"]},
    "SHORT_LINK": {"BASE_URL": "https://example.com"},
    "PERMISSION_CLASSES": ["myapp.permissions.ResourceActionPermission"],
    "PARAMETERS": {"SEED_CATALOG": "myapp.parameters.CATALOGUE"},
    "RATE_LIMIT": {"CACHE_ALIAS": "throttle"},
}
```

A multi-tenant project fills the `tenant_id` every package table carries: set
`TENANT["INSTANCE_ATTRIBUTE"]` to the attribute holding a row's tenant
(`"organization_id"`) and `TENANT["RESOLVER"]` to a dotted path to
`callable(request) -> tenant id`. Unset, `tenant_id` stays empty.

A project whose clients branch on a machine-readable error code, or quote a
trace id back, turns on `RESPONSE["ERROR_CODES"]` and sets
`RESPONSE["ERROR_TRACE_ID_KEY"]` (say, `"trace_id"`). Both are off by default,
so the envelope gains no key a client did not ask for.

Dotted paths (`PERMISSION_CLASSES`, `SEED_CATALOG`, `AUTHORED_EXCEPTION_PACKAGES`)
resolve at first use, never at import, so your own modules are not imported
while the app registry is still loading.

Three defaults to decide on rather than inherit:

- **`TRACKING["TRUSTED_PROXY_COUNT"]` is 0**, which ignores `X-Forwarded-For`
  entirely — the only safe default, since a client can forge any hop the count
  trusts. Behind nginx or a load balancer, set it to the number of proxies you
  run. Left at 0, every client appears as the proxy's address, so every rate
  limit and IP block applies to all of them at once. If the app's port is
  reachable without the proxy, also list the proxies in
  `TRACKING["TRUSTED_PROXY_IPS"]` (addresses or CIDRs), so a direct request
  cannot pick its own address.
- **`PERMISSION_CLASSES` is empty**, so the `Custom*` views require a signed-in
  user and nothing more. Name your RBAC class here.
- **`RATE_LIMIT["CACHE_ALIAS"]` is `default`.** If `default` is `DummyCache`
  (common under `DEBUG`), no throttle or rate limit holds; point it at a real
  cache.

## Models

Inherit `BaseModel` and every row gets a UUID id, `is_active`, `is_deleted`,
`created_at`/`updated_at`, `created_by`/`updated_by`, and a history trail.

```python
from django_common_kit.models import BaseModel

class Job(BaseModel):
    title = models.CharField(max_length=200)
```

Every save writes a `model_history` row with field-level before/after and the
acting user, read from the request in flight — which is why
`CurrentRequestMiddleware` must be installed. A save outside a request records
no actor rather than guessing one. Set `_track_history = False` on a model to
switch it off, and do so on every telemetry table.

```python
from django_common_kit.history import get_model_history

for row in get_model_history(job):
    print(row.action, row.changed_by, row.field_changes)
```

## Status transitions

Written when your service changes a status, not by a signal. Call it *before*
assigning the new value, so the previous one is read from the instance:

```python
from django_common_kit.transitions import track_status_transition, get_status_transitions

track_status_transition(job, JobStatus.DONE, "employee", transition_reason="Signed off")
job.status = JobStatus.DONE
job.save()

get_status_transitions(job)                       # newest first
get_status_transitions(job, field_name="status")  # one field only
```

`transition_source` is your own word, at most 20 characters; a longer or empty
one raises `ValueError`. `field_name` defaults to `"status"`.

## File uploads

`FILES["MAX_UPLOAD_BYTES"]` (default 10 MB; `0` for no limit) and
`FILES["ALLOWED_MIME_TYPES"]` (default any; `"image/*"` for a family) are
checked by one validator. The admin and model forms apply it through
`CommonFileModel.clean()`; a serializer puts it on its field:

```python
from django_common_kit.files import validate_upload

file = serializers.FileField(validators=[validate_upload])   # 400 on that field
```

Files already stored are never re-checked, so lowering a limit does not lock
old rows.

## Parameters

```python
from django_common_kit.parameters import ParameterCache

ParameterCache.get_int("MAX_JOBS_PER_DAY", default=10)
ParameterCache.get_bool("MAINTENANCE_MODE")
```

Only `is_system=True` rows are cached and served. Parameter *names* are yours:
point `PARAMETERS["SEED_CATALOG"]` at a list of dicts and run
`manage.py seed_parameters`.

## Request tracking

Bodies are redacted **before** they are written and **before** they are
truncated; a credential is never on disk. Rows go to Celery when it is installed
and are written inline when it is not.

The IP blocker bans on the third probe for `.env`, `.git`, `wp-admin` and the
rest of the list in `tracking/patterns.py`. Every pattern is anchored to a path
segment — a bare substring matches the random token in a verification link and
bans the customer's whole office. List your own token-bearing routes in
`TRACKING["IP_BLOCK_EXEMPT_PATHS"]`. A ban lifts after seven days, is reversible
from the admin without a deploy, and `manage.py unblock_ip <ip>` is the escape
hatch when the banned address is your own.

Sweep daily: `manage.py purge_tracking`, `manage.py purge_history`.

## Phone numbers

```python
from django_common_kit.phone import Phone

Phone.normalise("0401773013")                    # '+61401773013'
Phone.normalise("ask for Dave")                  # None
Phone.normalise_or_original("ask for Dave")      # 'ask for Dave'  <- use this on save
Phone.is_serviceable("+1 415 555 0132")          # False, with SERVICEABLE_REGIONS = ["AU"]
Phone.is_same_number("0412 345 678", "+61412345678")  # True
Phone.search_q("phone_number", "0400")           # a Q matching either spelling
```

> **Install `django-common-kit[phone]` before you send SMS.** Without
> `phonenumbers`, validation falls back to checking E.164 *syntax*, which cannot
> tell that a well-formed string is undialable.

## Short links

```python
from django_common_kit.shortlinks import shorten
shorten("https://example.com/quote/abc?sig=…", purpose="quote")  # 'https://example.com/s/Ab3xK9q/'
```

Mount the redirect: `path("s/<slug:slug>/", short_link_redirect)`. Returns the
long URL unchanged on any failure, so a message still goes out.

## Platform notices

A maintenance window, an outage or a change of terms, shown to the people using
the platform. Posted in the admin ("Platform Notices"); shown by the frontend,
never sent.

```python
from django_common_kit.notices.views import LiveNoticeListView, NoticeDismissView

path("notices/", LiveNoticeListView.as_view()),                              # GET ?surface=web
path("notices/<uuid:notice_id>/dismiss/", NoticeDismissView.as_view()),      # POST
```

A notice is live between `starts_at` and `ends_at` (either may be empty) while
`is_active`. Three columns narrow who sees it, and each left empty means
*every*:

- `tenant_id` — one tenant, else all of them. Never filled from the request.
- `audience` — a string in your own vocabulary (`"admins"`). A viewer's
  audiences come from `NOTICES["AUDIENCE_RESOLVER"]`, a dotted path to
  `callable(request) -> iterable of str`. Unset, the viewer has none and sees
  only notices with no audience.
- `surface` — where it shows (`"web"`, `"ios"`), as the client passes it in
  `?surface=`. A client that names none sees only notices for every surface.

A dismissal is stored per user, so it holds on every device; a notice with
`is_dismissible=False` stays until it ends. The live list is cached for
`NOTICES["CACHE_TTL_SECONDS"]` (60) and dropped on every save.

## Encrypted secrets

For a secret the project must read back — a provider auth token, an OAuth
refresh token, a webhook signing secret. Passwords are hashed by Django's auth,
never encrypted. Needs the `crypto` extra. PRD §17 has the full contract.

```python
DJANGO_COMMON_KIT = {
    # Newest first: the first key encrypts, every key decrypts.
    "ENCRYPTION": {"KEYS": env.list("ENCRYPTION_KEYS")},
}
```

Generate a key with
`python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
and keep it in the environment, never in source. There is no fallback derived
from `SECRET_KEY`: no key, no encryption, and `manage.py check` says so
(`common_control.E001`–`E003`, `W001`).

```python
from django_common_kit.crypto import EncryptedTextField, encrypt, decrypt, mask

class ProviderAccountModel(BaseModel):
    auth_token = EncryptedTextField(blank=True)
```

- The column holds a Fernet token; the attribute is the plaintext, decrypted
  the first time it is read. Loading rows never needs a key, and a row no key
  opens raises `DecryptionError` only where its secret is read.
- A value read and saved back unchanged keeps its token. A new value is
  encrypted on `save()`, `update()`, `bulk_create` and `bulk_update`.
- Only `isnull` and `=""` are lookups; any other filter on it raises
  `FieldError`, since a randomised token can never match.
- History records a change as `"***"` and never snapshots the value.
- Forms render a password input that never shows the stored value; submitted
  blank, it keeps it. `BaseModelAdmin` shows the mask in `list_display`. Do not
  put the field in `readonly_fields`: Django displays those as plain text.
- For DRF, add `SecretFieldsMixin` before `ModelSerializer`: the field accepts
  the plaintext and reads back as `{"is_set": true, "masked": "••••1234"}`.
- `dumpdata` writes the token; `loaddata` would encrypt it a second time, so
  set secrets in fixtures from code instead.

**Moving an existing column in.** The tokens are plain Fernet, so a column
written by any other Fernet helper reads back once its key is in `KEYS`: add
the key, change the field to `EncryptedTextField` (an `AlterField` with no
schema change), delete the local helper. A column that held plaintext takes
`legacy_plaintext=True` until `rotate_encrypted_fields --include-plaintext`
has encrypted it.

**Rotating a key.** Deploy with `KEYS = [new, old]`, run
`manage.py rotate_encrypted_fields` (every encrypted field of every installed
model; `--dry-run`, `--app`, `--model`, `--batch-size`), and deploy with
`KEYS = [new]` once it reports no failures. It writes no history and does not
touch `updated_at`, includes soft-deleted rows, and can be stopped and run
again.

## Development

```bash
python3 -m django test tests --settings=tests.settings
DJANGO_COMMON_KIT_TEST_DB=postgres python3 -m django test tests --settings=tests.settings
```

The Postgres leg is not optional before a tag: `test_postgres` pins the column
types and runs only there.

## Branches and releasing

Work on `dev`. `main` moves only when a release is fast-forwarded to it. The tag
must equal `django_common_kit.__version__`, and a published tag is never moved.

Publishing a GitHub Release for a tag uploads that version to PyPI
(`.github/workflows/publish.yml`, trusted publishing, no token). The workflow
can also be started by hand with the tag as its ref.
