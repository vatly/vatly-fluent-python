# Changelog

## 0.1.0a2 — Unreleased

- Derive grace expiry from period/trial deadlines, not cancellation request time.
- Share lifecycle mapping across signed reactions, sync and Django reconciliation;
  repair legacy expiry on sync and clear it after missed resume.
- Reject unknown grace expiry transactionally rather than guessing a deadline.
- Reject conflicting trial/cycle grace deadlines; the API omits core's trial flag.
- Require corrected SDK 0.1.0a2; refresh the unpublished bootstrap wheel.

## 0.1.0a1 — Unreleased

> Alpha — under active development. Every alpha release may include breaking changes. Pin exact versions during alpha.

- Initial async-first Fluent Python port: builders, handles, customer services,
  pluggable storage, transactional webhook reactions and signed snapshots.
- Host-owned checkout/entity linkage, PR #2910-compatible subscription references,
  bounded recovery, historical plan-line reporting, and no customerId ownership fallback.
- Typed package, SQLite transaction/outbox example, tests, guides, CI, trusted
  publishing workflow and idempotent orb setup. No publication or tags.
