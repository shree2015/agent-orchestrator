# Changelog

## Unreleased

### Fixed
- BUG-142: deleting a short link now removes its click analytics, so a re-created alias starts at zero clicks. The repository deletes clicks explicitly and re-enables SQLite foreign key enforcement.
- BUG-142: on start-up, click rows whose link no longer exists (left by older versions) are purged. The purge is idempotent and touches only unreachable data.

### Notes
- No API changes. Existing clients are unaffected.
- Added three regression tests; no existing tests were changed or removed.
