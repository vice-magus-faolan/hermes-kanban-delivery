# Hermes Kanban Delivery

Deterministic delivery tools for Hermes Agent's native Kanban workflow.

**Status: repository bootstrap. No installable plugin or extracted runtime is
included yet.**

## Initial extraction scope

The first implementation will focus on read-only capabilities:

- Validate explicitly configured board delivery policy.
- Present phase-aware delivery context: current role, exact artifact identity,
  repository/source location, applicable boundaries, and next owner.
- Reuse native task, run, event, and review records instead of creating another
  workflow database or scheduler.

Native Kanban remains authoritative for execution and review. Existing review
recording and outcome tools keep their responsibilities. Target advancement,
cleanup, publication, and runtime activation are not part of this initial slice.

## Development

Changes use feature branches and pull requests. Executable verification and
supported Hermes versions will be declared with the first implementation; this
bootstrap does not claim runtime compatibility or passing plugin checks.

See [CONTRIBUTING.md](CONTRIBUTING.md). Installation instructions will be added
only when an installable, verified plugin exists.

## License

GPL-3.0-or-later. See [LICENSE](LICENSE).
