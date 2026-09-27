# Changelog

All notable changes to `django-common-kit` are recorded here. The version headings
must match `django_common_kit.__version__`; `tests/test_version.py` fails if they,
or the README's install pins, drift from it.

## [Unreleased]

Nothing yet.

## [0.11.1] - 2026-09-27

### Added

- PyPI classifiers: Python 3.10 to 3.13, Django 4.2 to 5.2, and `Typing ::
  Typed`. Packaging metadata only; no code changes.

## [0.11.0] - 2026-09-27

### Changed

- **Renamed to `django-common-kit`**, because `django-common-utils` is taken on
  PyPI by an unrelated package with the same import name. This is a breaking
  change for anyone on 0.10.0:
  - distribution `django-common-utils` is now `django-common-kit`;
  - import package `django_common_utils` is now `django_common_kit`;
  - settings dict `DJANGO_COMMON_UTILS` is now `DJANGO_COMMON_KIT`;
  - app config `CommonUtilsConfig` is now `CommonKitConfig`.

  The app label `common_control`, the migrations and every table name are
  unchanged, so an existing database needs no migration.

### Added

- Published on PyPI, by trusted publishing from a GitHub Release.

## [0.10.0] - 2026-09-27

First public release. The package was developed privately up to this version;
this entry summarises what it contains rather than how it got here.

### Added

- **`BaseModel`** (PRD §3.4): UUID primary key, `created_at`/`updated_at`,
  `created_by`/`updated_by` against `settings.AUTH_USER_MODEL`, `is_active`,
  and `tenant_id`.
- **Change history** (§6): `ModelHistory` snapshots with the actor taken from
  `CurrentRequestMiddleware`, many-to-many fields and encrypted fields left out.
- **Typed system parameters** (§7) with a cache in front of the table, and
  `seed_parameters`.
- **Request and IP tracking** (§8): `request_logs`, `ip_tracking` and
  `blocked_ips`, with redaction before truncation, `TRUSTED_PROXY_COUNT` and
  `TRUSTED_PROXY_IPS`, optional geo-IP and user-agent enrichment, and retention
  commands.
- **File attachments and short links**: `CommonFileModel` on local disk or S3,
  tenant-first upload paths and enforced upload limits.
- **Status transitions, contact-us and platform notices**, each in its own
  table.
- **The REST response envelope** (§9): `ApiResponse`, the base views,
  pagination, permission classes and throttles that produce it, with opt-in
  `code` and trace id on error bodies.
- **Phone helpers** built on the optional `phonenumbers`.
- **Encryption at rest** (§17): `django_common_kit.crypto`,
  `EncryptedTextField`, the DRF `SecretField`, `rotate_encrypted_fields` and
  system checks `common_control.E001`–`E003` and `W001`.
- **`adopt_tables`**: hands a project's existing tables to the package in the
  right order, for `--fake-initial` adoption.
- Extras `s3`, `phone`, `images`, `tracking`, `celery`, `crypto` and `all`.
