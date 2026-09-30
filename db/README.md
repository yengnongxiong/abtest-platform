# db

The database schema, as plain numbered SQL migrations for PostgreSQL 16. A small runner of our own ([server/src/abtest/db/migrate.py](../server/src/abtest/db/migrate.py), [ADR-010](../docs/decisions.md#adr-010-a-small-migration-runner-of-our-own-m3)) applies them in order, each in its own transaction, and refuses to run if an applied file has changed. Every index carries a comment naming the query it serves.

Read first:
- [migrations/0001_init.sql](migrations/0001_init.sql): every table, the daily-partitioned `events` table, and `ensure_event_partitions`
- [migrations/0002_covering_attribution_index.sql](migrations/0002_covering_attribution_index.sql): the covering index found with EXPLAIN ([ADR-021](../docs/decisions.md#adr-021-a-covering-attribution-index-and-a-query-bounded-on-both-sides-m9))
