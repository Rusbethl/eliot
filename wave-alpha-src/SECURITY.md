# Security policy

## Supported versions

`wave-alpha` follows semantic versioning. Only the latest minor release receives security fixes.

| Version | Supported          |
| ------- | ------------------ |
| 0.6.x   | :white_check_mark: |
| < 0.6   | :x:                |

## Reporting a vulnerability

**Please do not file public GitHub issues for security vulnerabilities.**

Email **jxchen31@gmail.com** with:

- A description of the vulnerability and its impact.
- Steps to reproduce, ideally with a minimal proof-of-concept.
- Any suggested mitigation.

You should receive a response within 7 days. If the report is accepted, we will:

1. Acknowledge receipt and assign a tracking ID.
2. Develop and test a fix in a private branch.
3. Coordinate a release and public disclosure with the reporter.

## Scope

In-scope:

- The `wave_alpha` Python package and its CLI.
- The local FastAPI web UI shipped under `wave_alpha.web`.
- The `~/.wave_alpha/` SQLite caches and config files (write-path issues, injection, etc.).

Out-of-scope:

- Third-party dependencies (report upstream).
- Yahoo Finance / external data correctness.
- Trading losses or strategy performance — see [`DISCLAIMER.md`](DISCLAIMER.md).
