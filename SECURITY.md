# Security Policy

## Scope

Kālsangati is a local-first desktop application. It does not transmit data over the network, does not have user accounts, and does not connect to external services. The attack surface is therefore limited to:

- The local SQLite database file and its permissions
- CSV file parsing (potential for malformed input)
- The `plyer` notification library (system-level calls)
- The `watchdog` file system watcher (folder monitoring)

## Supported Versions

Security fixes are applied to the latest release only.

| Version | Supported |
|---------|-----------|
| Latest  | Yes       |
| Older   | No        |

## Reporting a Vulnerability

If you discover a security vulnerability, **do not open a public GitHub issue**.

Instead, please report it privately by emailing the maintainers at:

> security@chronos-project.example *(replace with real address)*

Include:

- A description of the vulnerability and its potential impact
- Steps to reproduce
- Any relevant code, files, or screenshots

You will receive a response within 72 hours acknowledging receipt. We will work with you to understand and address the issue before any public disclosure.

## Data Security Notes for Users

- The Kālsangati database (`kalsangati.db`) contains your personal time tracking data. It is stored unencrypted on disk.
- Restrict access to the database file using standard file permissions if you share your machine.
- Back up the database file regularly — Kālsangati does not do this automatically.
- CSV files imported into Kālsangati are read-only; the originals are not modified or deleted.
