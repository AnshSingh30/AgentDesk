# Security policy

## Reporting a vulnerability

Please **do not open a public issue** for security problems. Report them privately through
[GitHub Security Advisories](https://github.com/AnshSingh30/AgentDesk/security/advisories/new).

Include what you found, how to reproduce it, and the impact you expect. We aim to acknowledge reports
within 3 working days and to ship a fix or mitigation for confirmed high-severity issues within 30 days.
Please give us a reasonable window to fix the issue before you disclose it publicly.

## Supported versions

Only the latest commit on `main` receives security fixes while the project is pre-1.0.

## Scope

In scope: the API, webhook and worker services (`apps/api`), the web app (`apps/web`), and the Docker
Compose / Caddy configuration in `infra/`. Areas that matter most:

- tenant isolation (one organisation reading or changing another's data)
- the SQL safety layer (`apps/api/app/sql/validator.py`): anything that gets a write, file read or
  hidden column past it
- authentication, sessions, CSRF, API keys and WhatsApp webhook signature checks
- SSRF through custom model endpoints, database hosts or other user-supplied URLs
- prompt injection that leaks secrets or other tenants' data

Out of scope: findings that need a compromised host or admin credentials, missing hardening headers
on local development servers, and denial of service by volume.

## How AgentDesk protects data

See [docs/security.md](docs/security.md) for the security model: tenant scoping, envelope encryption of
customer secrets, the read-only SQL validator, SSRF guards and audit logging.
