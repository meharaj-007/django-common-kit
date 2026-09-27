# django-common-utils — Product Requirements

Status: specification. The code cites these section numbers (`§6`, `§9.3`); read
the section a module names before changing that module.

---

## §1 Purpose and scope

The foundation a Django REST backend starts from, as one package:

- a UUID `BaseModel` with audit columns and soft delete;
- a change-history trail;
- a typed system-parameter table with a cache in front of it;
- request logging, visit tracking and IP blocking;
- a generic file attachment and a storage layer;
- short links;
- platform notices, shown to the people using a platform (§16);
- one REST response envelope, with the views, pagination, permissions,
  throttling and exception handling that produce it;
- phone-number parsing;
- encrypting secrets at rest (§17, `v0.10.0`).

Every project installs it, configures it and uses it the same way. There is one
settings dict, one name per thing, and no project-specific path through any of
it.

Non-goals. This package does not own business vocabulary (§2). It does not send
anything over a network; delivery — email, SMS, push — is another package's
boundary.

## §2 The boundary

**If it has no business meaning, this package owns it. The moment it names a
lead, a job, an order, a crew, a quote or a role, it does not.**

When reviewing a change, that question is the test. A scope helper that filters
by crew membership, a status enum for quotes, a report, an email template — all
project, however generic the mechanism underneath looks.

Where a project genuinely needs to inject vocabulary into package mechanism, it
arrives through a dotted-path hook in settings, resolved at first use
(`conf.AppSettings.hook`), never as an import in this package. Examples: the
parameter seed catalogue (§7), permission classes (§5.2), the authored-exception
package list (§5.1).

**Nothing under `django_common_utils/` may import a host project module.** Enforced by
`tests/test_import_purity.py`. The one permitted indirection is
`settings.AUTH_USER_MODEL`.

## §3 Distribution, layout and the model base

### §3.1 Packaging

Python distribution `django-common-utils`, import package `django_common_utils`, installed
from a git tag:

```
django-common-utils @ git+https://github.com/meharaj-007/django-common-utils.git@v0.10.0
```

`django_common_utils/__init__.py:__version__` is the single source of truth;
`pyproject.toml` reads it dynamically. Optional extras: `s3`, `phone`, `images`,
`tracking`, `celery`, `crypto` (`v0.10.0`, §17), `all`. The package imports cleanly with none of them
installed; each degrades in a documented way.

### §3.2 App label — `common_control`

The Django app label is `common_control`, following the `*_control` convention.
It is not `common`: a project may already have an app of that label with its own
migrations, and two apps cannot share one.

### §3.3 Layout

```
django_common_utils/
  __init__.py          __version__, lazy exports
  apps.py              AppConfig, label = "common_control"
  conf.py              the DJANGO_COMMON_UTILS dict (§4)
  models.py            BaseModel and every concrete table
  history.py           HistoryMixin, the diff engine (§6)
  transitions.py       track_status_transition (§6.4)
  signals.py           receivers, all connected from apps.ready()
  middleware.py        CurrentRequestMiddleware (§6.2)
  request_context.py   the request in flight: actor, IP, correlation id
  api/                 response, errors, exceptions, pagination, views,
                       permissions, throttling, rate_limiters (§5)
  parameters/          ParameterCache (§7)
  tracking/            patterns, redaction, writers, middleware, purge, metrics (§8)
  shortlinks/          service, views (§10)
  notices/             service, views (§16)
  storage.py           MediaStorage and path helpers (§9.3)
  files.py             validate_upload, the FILES limits (§9.4)
  phone.py             Phone (§11)
  crypto/              encrypt, decrypt, mask, EncryptedTextField, SecretField,
                       SecretFieldsMixin, checks (§17)
  admin.py             admin bases and registrations (§12)
  constants/           ErrorMessage
  management/commands/ seed_parameters, purge_history, purge_tracking, unblock_ip,
                       adopt_tables
  migrations/          one table per initial migration (§13)
```

### §3.4 `BaseModel`

```python
class BaseModel(HistoryMixin, models.Model):
    id          = UUIDField(default=uuid4, editable=False, unique=True, primary_key=True)
    is_active   = BooleanField(default=True, db_index=True)
    is_deleted  = BooleanField(default=False)
    created_at  = DateTimeField(auto_now_add=True)
    updated_at  = DateTimeField(auto_now=True)
    created_by  = FK(settings.AUTH_USER_MODEL, related_name='%(class)s_created_by',
                     on_delete=SET_NULL, null=True, blank=True)
    updated_by  = FK(settings.AUTH_USER_MODEL, related_name='%(class)s_updated_by',
                     on_delete=SET_NULL, null=True, blank=True)
```

`created_by`/`updated_by` target `settings.AUTH_USER_MODEL`. A project whose
models currently point at a literal `'app.UserModel'` gets a no-op `AlterField`
per model when it switches to this base, because Django records a swappable
dependency differently. Those must be generated and applied, not faked.

`_track_history = True` and `_history_exclude_fields = ['updated_at', 'updated_by']`
are set on the base, overridable per model, and switched off entirely on
telemetry tables (§6.3).

### §3.5 Tenancy

Every table the package owns carries `tenant_id`: a nullable UUID, indexed, and
**never a foreign key** — the package does not know what a project's tenant is
(an organisation, a workspace, a business), so it cannot point at one. A project
without tenants leaves it `NULL`.

It is filled on save when the caller left it empty (`tenancy.resolve_tenant`):

- from the row a record is *about* — a history row, a status transition, a file
  attached to something — through `TENANT["INSTANCE_ATTRIBUTE"]`, the attribute
  on the project's models that holds their tenant (`"organization_id"`);
- otherwise, for rows about a request rather than a row — request logs,
  visits, a contact form, a file attached to nothing — from
  `TENANT["RESOLVER"]`, a dotted path to `callable(request)`.

A row about another row never falls back to the resolver: the history of a
record with no tenant belongs to no tenant, not to whichever one the person
making the change was working in. With tenancy configured, an attached file's
path starts with its tenant (`<tenant_id | system>/<UPLOAD_ROOT>/…`, §9.2).

Global tables — `parameters`, `blocked_ips` — are not filled by default.
`model_history` and `request_logs` carry a `(tenant_id, -created_at)` index,
for "this tenant's history, newest first". It is on the package's concrete
tables, not on `BaseModel`: a project's own tables keep their own tenant column.

## §4 Settings

One `DJANGO_COMMON_UTILS` dict, and only that. Every knob is a key in it; every
project configures it the same way; there is no second place a value can come
from. Settings the package does not own — `AUTH_USER_MODEL`, `MEDIA_ROOT`,
`AWS_BUCKET_NAME`, `DEBUG` — are read from Django directly.

Two rules:

1. **Nothing is read at import time.** Every lookup goes through
   `app_settings.get(...)` so `override_settings` is honoured. A module-level
   `PAGE_SIZE = settings.DJANGO_COMMON_UTILS[...]` freezes the value at first import
   and makes every override in a test suite a lie.
2. **Every key has a default**, and the default is what a project that
   configures nothing gets. `conf.py` holds the full tree and the reasoning
   per key.

## §5 REST surface

### §5.1 The response envelope

`ApiResponse` renders one shape:

```json
{"status": "success|error", "status_code": 200, "message": "...",
 "data": {...}, "errors": {...}, "meta": {...}}
```

Two keys are opt-in: `code` on every error body (`RESPONSE["ERROR_CODES"]`,
a default per status, overridable with `code=`) and the request's correlation
id under `RESPONSE["ERROR_TRACE_ID_KEY"]`. Off by default — a key added to the
envelope is a contract change for every client that did not ask for it.

`message`, `data`, `errors` and `meta` are omitted when unset. `data` is omitted
only when `None`, so an empty list still renders as `"data": []`. `to_dict()`
returns the body without a DRF `Response` for Django views that need
`JsonResponse`.

`validation_error` answers **400**, matching what DRF raises for the same
failure through `is_valid(raise_exception=True)`; a client sees one status for a
validation failure whichever code path produced it. `RESPONSE["VALIDATION_ERROR_STATUS"]`
overrides it.

**Internal exceptions never reach a client.** If an error response is built
inside an `except` block and the exception is not one whose message was authored
for a person — a `ValueError` from a service, a DRF `APIException`, a class
defined in the project's own packages — its text is cut out of the response, a
generic message stands in, and the traceback is logged. With `DEBUG` on it is
also returned under `debug`. `errors` is never populated on a 5xx.

### §5.2 Pagination, views, permissions, throttling

- `CustomPageNumberPagination` and `CustomCursorSetPagination` render paging into
  `meta`. Page size is whitelisted (`PAGINATION["ALLOWED_PAGE_SIZES"]`) rather
  than clamped: a selector that offers four sizes has no use for the other 196.
- `CustomModelViewSet`, `CustomListAPIView`, `CustomWOPListAPIView`,
  `CustomRetrieveAPIView`, `CustomCreateAPIView`, `CustomUpdateAPIView`,
  `CustomDestroyAPIView`: envelope-aware, soft-deleting, schema-safe. Permissions
  default to `IsAuthenticated` plus `PERMISSION_CLASSES` from settings.
- Permission classes are mechanism only: Django's own flags and
  `IsOwnerOrReadOnly`. Role-shaped classes are built in the project with
  `user_type_permission()`.
- `api/throttling.py`: DRF throttles that log the scope, rate and key on
  rejection; fixed-scope classes for credential and session endpoints; scoped
  throttles keyed on IP or hashed email.
- `api/rate_limiters.py`: limits inside a view or on a non-DRF view — one
  `enforce_cooldown` guard, three decorators. The counter is an atomic
  `add`+`incr`; a request rejected on identity refunds its IP slot. A guard's
  own kill switch arrives as a value (`enabled=`), never as the name of a
  setting for the package to read.
- Both count in one cache, `RATE_LIMIT["CACHE_ALIAS"]`, so a `DummyCache`
  default is overridden for every limit at once.
- The create views log the request body at DEBUG only, redacted with the
  request log's key set. A body is personal data; INFO logs travel further
  than the database.

### §5.3 Exception handling

One `custom_exception_handler` that renders every DRF and Django exception into
the envelope: validation errors with a readable top-level message and per-field
errors; `Throttled` with `Retry-After` in both the header and `meta`;
`IntegrityError` with the offending column named and its value withheld; the
package's own `BusinessLogicException`, `ValidationException`,
`ResourceNotFoundException`, `PermissionDeniedException`.

## §6 Change history

`HistoryMixin` + `ModelHistory` (table `model_history`). A `pre_save`/`post_save`
pair diffs tracked fields and writes one row per change with the actor, the
model label, the object id, and the field-level before/after; `pre_delete`/
`post_delete` record the deletion with a before snapshot.

### §6.1 What is recorded

Fields are the model's concrete forward fields minus `_history_exclude_fields`,
`HISTORY["EXCLUDE_FIELDS"]`, and the audit columns. Values longer than
`HISTORY["MAX_VALUE_LENGTH"]` are truncated. A save that changes nothing tracked
writes nothing. `purge_history` sweeps past `HISTORY["RETENTION_DAYS"]`.

### §6.2 The actor

Taken from a thread-local request context populated by
`CurrentRequestMiddleware`, not from a `user` argument threaded through every
`save()`. A save outside a request records no actor rather than guessing one.
The same middleware stamps a correlation id, honouring an incoming
`X-Request-Id`, and echoes it on the response; history rows and tracking rows
carry it.

### §6.3 Tracking must be off on telemetry tables

`RequestLog`, `IPTrackingModel`, `BlockedIPModel` and `ModelHistory` itself set
`_track_history = False`. With tracking on, a retention sweep writes a full
`ModelHistory` snapshot of every row it purges — the sweep grows the database,
preserves exactly the data it exists to erase, and blocks Django's fast-delete
path.

### §6.4 Status transitions

`StatusTransitionModel` (table `status_transitions`) records one status change
on any row: `field_name`, `previous_status`, `new_status`, the actor, a
`transition_source` in the project's own words, a reason and notes.
`track_status_transition(instance, new_status, transition_source, …)` writes
it and `get_status_transitions(instance)` reads it back. Unlike history it is
written only when a caller says so — a signal cannot tell a customer accepting
something from a script backfilling it — and it raises on failure, because the
caller is inside the service that changed the status and decides whether the
change stands without its record. The actor defaults to the request's, as in
§6.2; the tenant is read from the instance in hand.

## §7 System parameters

`ParameterModel` (table `parameters`) is a typed key/value row — one column per
type, because a `JSONField` and an `IntegerField` are queryable in ways a string
is not — with `ParameterCache` in front of it.

- Read-through, invalidated by signal on any save or delete.
- Optional warm load at startup on a daemon thread, guarded so a database that
  is not there yet logs and moves on (`PARAMETERS["AUTO_LOAD_ON_STARTUP"]`).
- Typed accessors `get_str`, `get_int`, `get_float`, `get_bool`, `get_json`,
  each returning the default for a missing key *and* for a value that will not
  cast.
- Only `is_system=True` rows are served. A non-system parameter is admin data,
  not runtime configuration.
- **Parameter names are project vocabulary.** The package ships none.
  `seed_parameters` reads a catalogue at `PARAMETERS["SEED_CATALOG"]` and never
  overwrites an existing row without `--update`.

## §8 Request and IP tracking

Tables `request_logs`, `ip_tracking`, `blocked_ips`; middleware
`RequestLogMiddleware`, `IPTrackingMiddleware`, `IPBlockerMiddleware`.

- **Client IP** honours `X-Forwarded-For` only as far as
  `TRACKING["TRUSTED_PROXY_COUNT"]` allows. The default is 0 — take
  `REMOTE_ADDR`, ignore the header — because a client can forge any hop the
  count lets it forge, and the blocker hands out site-wide bans on it.
  `TRACKING["TRUSTED_PROXY_IPS"]` (addresses or CIDRs) narrows it further: set,
  the header is believed only on a connection from one of those proxies, so a
  request that reached the app without passing through the proxy cannot name
  its own address. Empty, any peer is taken to be the proxy — the right answer
  behind a unix socket, where `REMOTE_ADDR` is not an address. Whatever the
  header yields must parse as an IP (`ipaddress.ip_address`), or `REMOTE_ADDR`
  is used instead: a port suffix, `unknown` or junk never reaches a
  `GenericIPAddressField` or the ban list.
- **Redaction runs before truncation**, on the way into the row, never at
  display time. The key set in `tracking/redaction.py` is a floor;
  `TRACKING["REDACT_KEYS"]` raises it. Keys like `code` are credentials only
  under auth paths.
- **One exclusion list** (`TRACKING["EXCLUDED_PATHS"]`) shared by every writer,
  every pattern anchored to a path-segment boundary.
- **Rows are written from a worker** when Celery is installed, inline when it is
  not or the broker is down; the fallback bumps a counter and skips enrichment
  so no HTTP call ever happens in a response cycle. Geo lookup goes to
  `TRACKING["GEO_LOOKUP_URL"]` behind a 24h per-IP cache (15 min for a
  failed answer). An unreachable provider or a 429 pauses every lookup rather
  than being retried per IP, and ip-api's `X-Rl`/`X-Ttl` headers are honoured.
- **The blocker** bans on the `IP_BLOCK_MAX_ATTEMPTS`th probe. Patterns match
  on segment boundaries only; the project's own token-bearing routes are listed
  in `IP_BLOCK_EXEMPT_PATHS` and never inspected. Strikes decay after
  `IP_BLOCK_ATTEMPT_WINDOW_SECONDS`; bans lift after `IP_BLOCK_DURATION_SECONDS`;
  the in-process ban set is re-read every `IP_BLOCK_CACHE_TTL_SECONDS` and
  dropped — failing open — after `IP_BLOCK_CACHE_MAX_STALE_SECONDS` without a
  successful refresh. `unblock_ip` is the escape hatch when the banned address
  is your own.
- **Retention** is `purge_tracking`, batched by primary key.

## §9 Files and storage

### §9.1 `CommonFileModel`

Table `common_files`. A `GenericForeignKey` attachment so no app needs its own
file table; `content_type`/`object_id` nullable for standalone files.

`CommonFileQuerySet.delete()` loops per row: Django's `QuerySet.delete()` never
calls `Model.delete()`, so a bulk delete would orphan every blob.
`Model.delete()` removes the blob before the row and logs, rather than raises,
on a storage failure.

### §9.2 Upload paths

`<FILES["UPLOAD_ROOT"]>/{tag}/%Y/%m/{basename}`. `upload_to` must return a path
*including* the basename — a directory alone raises `SuspiciousFileOperation` —
and the basename is the client's, stripped of any path it supplied.

### §9.3 Storage backends

`MediaStorage` is a proxy that resolves `STORAGE["TYPE"]` (`local` or `s3`) on
first *use*, not at import, so a model that captured it at definition time
still follows the settings in force. `django-storages` is imported inside the
factory; the package imports with it absent, and a project that asked for S3
without installing it gets a message naming the extra. `deconstruct()` returns
the proxy with no arguments, so no bucket name is baked into a migration.

Paths are tenant-scoped: `scoped_path(owner_id, ...)` puts the owner's id first
so "everything this tenant owns" is one prefix, and a file with no owner goes
under `system/` and is never guessed into one.

### §9.4 Upload limits

`FILES["MAX_UPLOAD_BYTES"]` (`0`: no limit) and `FILES["ALLOWED_MIME_TYPES"]`
(empty: any type; `"image/*"` allows a family) are enforced by one validator,
`files.validate_upload`, usable on a form or serializer field.
`CommonFileModel.clean()` applies it to a file being uploaded, so forms and the
admin enforce it. It is never applied in `save()`: a stored file was accepted
under the limits of its day, and a lowered limit must not make its row
unsaveable. The type is the client's declared one, else a guess from the name —
a policy filter, not proof of what the bytes are.

## §10 Short links

`ShortLinkModel` (table `short_links`). `shorten()` mints a 62-alphabet slug,
reuses an unexpired row for the same target and purpose, and **returns the long
URL unchanged on any failure** — a shortener that can make a message fail to
send has the wrong priorities. `short_link_redirect` answers 301, 404 or 410 and
counts clicks best-effort. `SHORT_LINK["BASE_URL"]` is the public origin;
unset, nothing is shortened.

## §11 Phone and validators

`Phone` keeps three questions apart: `normalise` (storage — E.164 or nothing),
`normalise_serviceable` (validation — a number we take work from, per
`PHONE["SERVICEABLE_REGIONS"]`), `contact_key` (identity — never rejects).
Numbers written without a country code are read against
`PHONE["DEFAULT_REGION"]`.

`phonenumbers` is optional. Absent, `normalise` falls back to E.164 *syntax*,
which cannot tell that a well-formed string is undialable, and warns once. A
project that sends SMS installs the `phone` extra.

## §12 Admin

`BaseModelAdmin` — audit columns read-only, soft-delete filter, actor stamped on
save. Every package table is registered; telemetry read-only, `BlockedIPModel`
editable because unticking `is_active` is how a ban is lifted. Notices are
posted in the admin; their dismissals are read-only, and deleting one shows the
notice to that user again. Third-party
sidebar relabelling in `apps.py` is display-only and switchable.

## §13 Migrations and adopting existing tables

A new project runs `migrate` and is done. The migration layout exists for a
project that already has some of these tables under the same `db_table` names
and wants to adopt them in place. `manage.py adopt_tables --old-app <label>`
does it (dry run unless `--execute`), on the rules below:

- **Every initial migration creates exactly one table.** `--fake-initial` fakes
  a migration only when *every* table it creates exists. One table per file
  means a project lacking a given table gets it created for real and the rest
  faked.
- **An initial migration holds the table's original shape and nothing else.**
  `--fake-initial` matches on table name and never compares columns, so a column
  added to an initial migration is silently skipped wherever the table
  pre-exists and fails at the first write. Later columns live in later,
  non-initial migrations. `adopt_tables` fakes one once its columns exist,
  adding a missing nullable column first without its index.
- A project's own model on the same table leaves migration state in the same
  change that installs the app, and leaves it *state-only*
  (`SeparateDatabaseAndState`). Two installed models on one `db_table` fail
  `manage.py check`; a plain `DeleteModel` drops the table.
- A project's own historical migrations that created these tables become
  state-only too; otherwise a fresh database gets `CREATE TABLE` twice.
- Content types move from the old app *before* any `migrate`, which would
  otherwise create second rows beside the ones `model_history` points at.
- `--fake-initial` leaves an adopted table without any index the migration
  declares but the live table lacks, or holds it under another name.
  `adopt_tables` renames a same-definition index and builds a missing one with
  `CREATE INDEX CONCURRENTLY`.
- Before adopting, diff a `pg_dump --schema-only` of the **production** database
  against `tests/test_postgres.py`, which pins the column types the package
  expects. A `NOT NULL` column the package does not fill fails every insert.

`tests/test_migrations.py` enforces the first two rules and simulates the whole
adoption; `tests/test_adopt_tables.py` covers the command.

### §13.3 The rule, restated for the reader who skipped the section

**Never add a column to a migration marked `initial = True`.**

## §14 Versioning and releasing

`__version__` is the single source; `tests/test_version.py` fails if the
CHANGELOG heading or the README install pins drift from it. Consumers pin a
**git tag**, so the tag is the release artifact: it must equal `__version__`,
and a published tag is never moved.

Work happens on `dev`; `main` moves only when a release is fast-forwarded to it.
Any new migration, any change to the response envelope's keys, and any change to
a `BaseModel` column is at least a minor bump.

## §15 Known gaps

- CI (`.github/workflows/tests.yml`) runs the SQLite matrix, the Postgres leg
  and the migration-state check on every push and pull request. The Postgres
  service is `postgres:18`, matching the version the column types were pinned
  against.
- `adopt_tables` has been rehearsed on a copy of a development database, not yet
  run against a production schema.

## §16 Platform notices

A notice is a message to the people using a platform — a maintenance window, an
outage, a change of terms. It has no business meaning, so the package owns it
(§2). It is shown, not sent: the frontend asks for the notices live for its
viewer, and delivery by email or push stays outside the package (§1).

`PlatformNoticeModel` (table `platform_notices`): `title`, `body` (plain text or
markdown, rendered by the frontend), `severity` (`info`, `success`, `warning`,
`critical`), `starts_at`/`ends_at` (either empty: from now, until switched off),
`priority` (higher first), `is_dismissible`, `action_label`/`action_url`, and
`metadata` for anything else a client needs, such as a minimum app version.
History is on: a notice is content, and who changed its wording is worth
knowing.

Three columns narrow who sees a notice, and each **empty means every**:

- `tenant_id` — one tenant's notice, else every tenant's. It is **not filled
  from the request** (`_fill_tenant = False`): an operator posting a notice for
  everyone while working in one tenant would otherwise scope it to that tenant
  without being told.
- `audience` — who, in the project's words ("admins", "customers"). A plain
  indexed string, not `choices`, for the reason `transition_source` is one
  (§2). The viewer's audiences come from `NOTICES["AUDIENCE_RESOLVER"]`,
  `callable(request) -> iterable of str`. Unset or failing, the viewer has none
  and sees only notices with no audience — a notice for admins must not reach
  everyone because a resolver was not wired.
- `surface` — where ("web", "ios"), as the client says in `?surface=`. A client
  that names none sees only notices for every surface, never another surface's
  "please update the app".

A blank `audience` or `surface` is stored as `NULL`; an empty string would match
no viewer.

`PlatformNoticeDismissalModel` (table `platform_notice_dismissals`): one row per
user per notice, unique on the pair, so a dismissal holds on every device. Its
tenant is its notice's. History is off — a dismissal is a click, and its row is
its own record. A notice with `is_dismissible = False` cannot be dismissed.

`notices.live_notices(request, surface)` answers from one cached list of the
notices switched on and not yet ended (`NOTICES["CACHE_ALIAS"]`,
`["CACHE_TTL_SECONDS"]`, default 60; 0 = no cache). Being live *now* and being
for this tenant, audience and surface are checked per request against that list,
so a scheduled notice starts and ends on time while cached; a save or delete
drops it. Only the viewer's dismissals are read per request. The model's
`is_live`/`is_visible_to` and the queryset's `live()`/`visible_to()` ask the same
questions of a row and of the table, and a test holds them to the same answers.

`LiveNoticeListView` (`GET`) and `NoticeDismissView` (`POST <id>/dismiss/`) answer
in the envelope, and the project mounts them. Both require
`IsAuthenticatedActive` rather than the project's `PERMISSION_CLASSES`: every
signed-in user may read the notices meant for them. A viewer can dismiss only a
notice live for them (404 otherwise). A project that shows notices before
sign-in subclasses the list view with `AllowAny`; an anonymous viewer then sees
only notices for every tenant and audience, unless the resolvers say otherwise.

Both tables are new to every project, so each is born in its own initial
migration (`0017`, `0018`) with `tenant_id` already in its original shape.

## §17 Encryption at rest — `v0.10.0`

Status: built in `v0.10.0` (2026-09-25), from a spec decided with the owner the
same day: one helper here, used by every package that stores a secret. Where the build settled a detail the
spec left open, the text below says what was built.

### §17.1 Why here

A secret a project must be able to read back — a provider auth token, an OAuth
refresh token, a webhook signing secret — was being stored with Fernet by a
separate hand-written module in each project and package that needed one.

Each had its own key setting, its own error handling and its own idea of
rotation. Encryption has no business meaning,
so by §2 it belongs here.

### §17.2 Scope

In: symmetric encryption of short secrets with a key list that supports
rotation; a model field; a DRF serializer field; history, admin and tracking
behaving safely around both; a rotation command; system checks.

Not in:

- **Password hashing.** Passwords are hashed by Django's auth, never encrypted.
- **Per-tenant keys, KMS or envelope encryption.** One key set per project.
  Revisit when a customer contract asks for it.
- **Searchable or deterministic encryption.** An encrypted column is never
  filtered on (§17.5).
- **Encrypting files.** Storage-level encryption (S3 SSE) is the answer there.
- **Deriving a key from `SECRET_KEY`.** Some hand-written copies fall back to one. Rotating
  `SECRET_KEY` — a routine response to a leak — would then make every stored
  secret unreadable, silently, at the next read. There is no fallback: no key,
  no encryption.

### §17.3 Settings

```python
DJANGO_COMMON_UTILS = {
    "ENCRYPTION": {
        # Fernet keys, newest first. The first encrypts; every key decrypts.
        # A single string is accepted and treated as a one-item list.
        "KEYS": [],
    },
}
```

- A key is exactly the output of `Fernet.generate_key()` (44 characters of
  url-safe base64). Anything else is a configuration error, not a key to hash
  into shape.
- Keys come from the environment in every real deployment
  (`env.list("ENCRYPTION_KEYS")`); `docs/usage.md` says how to generate one and
  never shows a real one.
- Read through `app_settings`, never at import (§4). The built `MultiFernet` is
  cached per process, keyed on the key list itself, so `override_settings` works
  in tests and a changed key list builds a new one without a signal.

### §17.4 Functions — `django_common_utils.crypto`

```python
from django_common_utils.crypto import encrypt, decrypt, mask, is_configured, rotate_token

token = encrypt("sk_live_…")        # str -> Fernet token (str)
plain = decrypt(token)              # tries every key in KEYS
mask("sk_live_abcd1234")            # "••••1234"
is_configured()                     # KEYS set, all valid, cryptography installed
rotate_token(token)                 # re-encrypted with the first key
```

- `encrypt("")` and `encrypt(None)` return the input unchanged: an empty secret
  is "not set", and the column must still answer `isnull` / empty checks.
- Errors, both importable from `django_common_utils.crypto`:
  - `EncryptionNotConfigured` (a subclass of `ImproperlyConfigured`) — no keys,
    or a key that is not a Fernet key. Raised by `encrypt` and `decrypt`, with a
    message naming `DJANGO_COMMON_UTILS["ENCRYPTION"]["KEYS"]` and how to generate a
    key.
  - `DecryptionError` — no configured key opens the token (wrong key, dropped
    key, corrupted value). The message never contains the token.
- `mask(value, keep=4)` shows four bullets and the last `keep` characters — a
  fixed width, so the mask does not give away the length. Values of 8
  characters or fewer are fully masked (`"••••"`), empty is `""`. It never
  raises.
- `cryptography` is imported inside the functions, so the package still imports
  without the extra (§3.1, `tests/test_import_purity.py`). Calling a function
  without it raises `EncryptionNotConfigured` naming `django-common-utils[crypto]`.
- Tokens are plain Fernet tokens with no prefix, so a column written by any of
  the twelve copies in §17.1 reads back unchanged once its key is in `KEYS`.

### §17.5 The model field — `EncryptedTextField`

```python
from django_common_utils.crypto import EncryptedTextField

class ProviderAccountModel(BaseModel):
    secret = EncryptedTextField(blank=True)
```

- **At rest:** a `text` column holding the Fernet token (or empty / `NULL`).
- **On the instance:** plaintext, decrypted **lazily** on first attribute access
  through a descriptor and cached on the instance. Loading a row, listing a
  queryset, or rendering an admin changelist never decrypts and never needs a
  key; only code that reads the secret does. A row whose token no key opens
  raises `DecryptionError` at that access, not while the queryset loads.
- **On save:** plaintext assigned since the load is encrypted with the first
  key; an untouched value is written back as the same token (no re-encryption,
  no spurious history). The plaintext handed out remembers its token, so a
  whole-row `refresh_from_db()` or a `full_clean()` — which read every
  attribute and set it again — leave it untouched (both do decrypt). A
  queryset `update()`, `bulk_create` and `bulk_update` encrypt too. Pickling
  an instance keeps the token and drops any decrypted plaintext.
- **Lookups:** only `isnull`, and `exact` against `""`. Anything else raises
  `FieldError` at query build time. A `filter(secret="…")` could only ever
  match nothing, because Fernet tokens are randomised; failing loudly is the
  kinder answer.
- **`legacy_plaintext=False`** (field option). With it on, a stored value that
  is not a Fernet token is returned as-is and logged once per model and field —
  the adoption path for a column that held plaintext. `rotate_encrypted_fields`
  then encrypts those rows. Off by default.
- **Migrations:** the field deconstructs under its public path,
  `django_common_utils.crypto.EncryptedTextField`, with its options; changing a
  `TextField` to it is an `AlterField` with no schema change, followed by
  `rotate_encrypted_fields --include-plaintext`.
- **Fixtures:** `dumpdata` writes the token; `loaddata` would read it as a new
  plaintext and encrypt it again. Fixtures set secrets from code.
- **`max_length`** is not accepted: a token is about 1.4 × the plaintext plus
  57 bytes, and a length cap would reject a long secret after encrypting it.

### §17.6 Everything that touches a row

- **History (§6).** An encrypted field is always excluded from snapshots. When
  it changes, the history entry records the field in the usual diff shape with
  `"***"` for each side that holds a secret and the empty value for a side that
  does not (`{"before": "", "after": "***"}` when one is first set) — you can
  see *that* a secret was set, changed or cleared and who did it, never what
  it was or became. Two different tokens of the same plaintext are not a
  change. `HISTORY["EXCLUDE_FIELDS"]` cannot put it back in; naming it in
  `_history_exclude_fields` drops the `"***"` entry as well.
- **Serialization.** `django_common_utils.crypto.SecretField` is the DRF field for
  it: write-only for the value, and on read returns `{"is_set": bool,
  "masked": "••••1234" | null}`. `SecretFieldsMixin`, for any `ModelSerializer`,
  maps every `EncryptedTextField` to it (through `serializer_field_mapping`), so
  a package's staff API cannot echo a secret by accident. The package has no base
  serializer today, and this does not add one.
- **Admin (§12).** The form widget is a password input that never renders the
  current value, whatever `formfield_overrides` says for `TextField`;
  submitting it blank keeps the stored secret, so a secret cannot be cleared
  from the admin. `BaseModelAdmin` shows an encrypted field named in
  `list_display` as its mask — which decrypts that row — and a row no key
  opens as `(unreadable)` rather than failing the page. Django displays
  `readonly_fields` as plain text, so an encrypted field must not be listed
  there.
- **Request tracking (§8).** `SENSITIVE_KEYS` gains `auth_token`,
  `webhook_token`, `signing_secret`, `secret_key` and `api_secret`, so a request
  body that *sets* a secret is redacted in `request_logs`. A project adds its
  own names through `TRACKING["REDACT_KEYS"]` as before.
- **Logging.** Neither the functions nor the field log a value, a token or a
  key, at any level.

### §17.7 Rotation — `manage.py rotate_encrypted_fields`

The procedure `docs/usage.md` documents:

1. Generate a new key; deploy with `KEYS = [new, old]`. New writes use `new`;
   everything still reads.
2. Run `rotate_encrypted_fields`. It walks every `EncryptedTextField` of every
   installed model — the project's and every package's — and re-encrypts each
   value not already on the first key.
3. Deploy with `KEYS = [new]`.

The command:

- updates the column with a queryset `update()` per row, so it fires no save
  signals, writes no history and does not touch `updated_at` or `updated_by`.
  The update is a compare-and-swap on the token it read, so a row the app
  changed meanwhile is left alone;
- works in batches (`--batch-size`, default 500), each in its own transaction,
  so it can be stopped and re-run; a re-run skips rows already done;
- takes `--dry-run` (counts only), `--app` / `--model` to narrow it, and
  `--include-plaintext` for fields with `legacy_plaintext=True`;
- includes soft-deleted rows (`is_deleted=True`): a key that is later dropped
  must not strand them;
- reports per field: rotated, current, empty, plaintext skipped, failed. A row
  no key opens is counted as failed and named by pk, and the command exits
  non-zero — step 3 must not happen until it is zero.

### §17.8 System checks

Registered from `apps.ready()`, the package's first:

- `common_control.E001` — an installed model has an `EncryptedTextField` and
  `KEYS` is empty.
- `common_control.E002` — a configured key is not a valid Fernet key (the
  message names its position in the list, never its value).
- `common_control.E003` — an `EncryptedTextField` is installed and the
  `cryptography` package is not.
- `common_control.W001` — the same key appears twice in `KEYS`.

A project with no encrypted field and no keys gets no message.

### §17.9 Packaging and versioning

- New extra `crypto = ["cryptography>=42,<52"]`, added to `all`. (The spec said
  `<47`; 50.0.1 was current when it was built, and the suite passes on 42.0.0
  and 50.0.1.)
- No new table, no migration. It is a minor bump (`0.10.0`) because it adds a
  public API, a settings key, an extra and system checks.
- A package that stores secrets depends on `django-common-utils[crypto]` `>= 0.10`.

### §17.10 Adoption

- **New users:** use the field from their first migration.
- **Project copies** (§17.1) move when their owners choose. Because tokens are
  plain Fernet, the move is: put the old key in `KEYS`, change the column to
  `EncryptedTextField` (`AlterField`, no schema change), delete the local crypto
  module. A copy that derived its key from `SECRET_KEY` must add that derived
  key to `KEYS` for the move, rotate, then drop it.

### §17.11 Tests and acceptance

- Round trip; empty and `None` pass through; `mask` edge cases.
- Rotation with two keys: a token made with the old key decrypts; after
  `rotate_encrypted_fields` every token opens with the new key alone; a second
  run changes nothing; history and `updated_at` are untouched.
- A row with an unreadable token loads in a queryset and in the admin changelist;
  reading the attribute raises `DecryptionError`; the command reports it and
  exits non-zero.
- `filter(secret="x")` raises `FieldError`; `filter(secret__isnull=True)` works.
- History of a changed secret holds `"***"` on both sides, and no snapshot holds
  the plaintext or the token.
- `SecretField` never returns the value; a `ModelSerializer` with `SecretFieldsMixin` picks it for every encrypted field.
- A logged `POST` that sets `auth_token` is redacted in `request_logs`.
- The package imports, and the suite outside §17 passes, without `cryptography`.
- The four system checks fire as described and stay silent otherwise.
- Tokens produced by an existing plain-Fernet module with a given key decrypt
  with that key in `KEYS`.
