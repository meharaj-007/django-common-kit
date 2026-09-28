# Repository Guidelines

## Project Structure & Module Organization

`django_common_kit/` is the installable Django package, with API helpers, tracking, parameters, management commands, and migrations. `tests/` includes the Django test settings and regression suite. Packaging and optional extras are declared in `pyproject.toml`; CI lives in `.github/workflows/`.

## Build, Test, and Development Commands

Use Python 3.10 or newer in a virtual environment. `python -m pip install -e .` installs the base package; `python -m pip install -e ".[all]"` enables optional integrations. Run `python -m django test tests --settings=tests.settings`. Build distributions with `python -m build` after installing the build tool.

## Coding Style & Naming Conventions

Use four-space Python indentation, snake_case functions/modules, and PascalCase classes. Follow existing Django and DRF interfaces. Keep optional imports optional so the base package works without every SDK. Add migrations for model changes; the package version comes from `django_common_kit.__version__`.

## Testing Guidelines

Name regression files `tests/test_*.py`. Cover base installs and extras; CI also checks migration consistency and PostgreSQL behavior across supported versions. Before a release, run the PostgreSQL leg with `DJANGO_COMMON_KIT_TEST_DB=postgres` and the documented database setup. SQLite alone does not validate PostgreSQL column behavior.

## Commit & Pull Request Guidelines

History mixes imperative subjects with scoped messages such as `docs(readme): add license badge` and `release: cut 0.11.1`. Keep commits focused on one change. In pull requests, explain the problem, resulting behavior, and validation performed; link an issue when applicable. Include screenshots for visible UI changes and call out configuration or migration changes. These are contributor expectations, not a claim of enforced branch rules.

## Security & Compatibility

Do not expose encrypted parameters, credentials, or request data in logs or fixtures. Explain compatibility changes and update the changelog for externally visible behavior.
