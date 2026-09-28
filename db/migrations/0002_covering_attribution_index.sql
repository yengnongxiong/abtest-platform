-- 0002: make the attribution index covering (M9; measured in docs/performance.md).
--
-- The attribution query also reads events.value, which mean metrics sum. Without it in the
-- index, Postgres visits a table page for every matching event, and since a metric's events
-- are spread over nearly every page, that means most of the table. INCLUDE (value) stores
-- the value in the index's leaf entries (it isn't part of the sort key), so Postgres can
-- answer the query from the index alone: an index-only scan. The key columns are unchanged,
-- so this index serves everything the old one did.
--
-- Postgres can't build an index on a partitioned table CONCURRENTLY, so this blocks writes
-- to events while it builds. On a live system with a large table, you would build each
-- partition's index CONCURRENTLY and then attach it (ALTER INDEX ... ATTACH PARTITION).
DROP INDEX events_attribution;
CREATE INDEX events_attribution ON events (project_id, event_name, user_id, occurred_at)
    INCLUDE (value);
COMMENT ON INDEX events_attribution IS
    'Attribution (PRD §13): a metric''s events for exposed users, within each user''s window. INCLUDE (value) lets the query read only the index.';
