# Contributing

This project is licensed GPL-3.0-or-later.

- Keep changes focused and submit them through pull requests.
- Prefer existing native Hermes capabilities and small explicit interfaces.
- Separate observation from mutation; do not add a second dispatcher or workflow
  state machine.
- Preserve artifact identity, independent review, and operator effect boundaries.
- Add behavioral regression tests for changed behavior. Declare and test supported
  stock Hermes versions when runtime code is introduced; do not patch core to
  make plugin verification pass.
- Keep credentials, real board/session data, host-specific policy, databases,
  logs, and runtime state out of commits. Use explicitly synthetic fixtures.
- Keep ignores surgical so future documentation, schemas, and fixtures remain
  visible.

The repository currently contains documentation and licensing only. Test and CI
commands will accompany the implementation rather than placeholders that imply
nonexistent runtime coverage.
