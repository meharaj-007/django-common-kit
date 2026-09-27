# Changelog

All notable changes to `django-common-utils` are recorded here. The version headings
must match `django_common_utils.__version__`; `tests/test_version.py` fails if they,
or the README's install pins, drift from it.

## [Unreleased]

Nothing yet.

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
- **Encryption at rest** (§17): `django_common_utils.crypto`,
  `EncryptedTextField`, the DRF `SecretField`, `rotate_encrypted_fields` and
  system checks `common_control.E001`–`E003` and `W001`.
- **`adopt_tables`**: hands a project's existing tables to the package in the
  right order, for `--fake-initial` adoption.
- Extras `s3`, `phone`, `images`, `tracking`, `celery`, `crypto` and `all`.
