-- 0001_init: the initial schema (PRD §10).
--
-- Every index says which query it serves, in a COMMENT ON INDEX so a test can check that none
-- is missing. Indexes that back a PRIMARY KEY or UNIQUE constraint serve that constraint.

-- Keys of metrics, flags, experiments, and variants: lowercase, URL-safe, and never ":",
-- because assignment hashes join inputs with ":" (PRD §9).
CREATE DOMAIN entity_key AS text CHECK (VALUE ~ '^[a-z0-9][a-z0-9_-]{0,63}$');

-- Event names, as the ingestion API accepts them (PRD §11).
CREATE DOMAIN event_name_text AS text CHECK (VALUE ~ '^[A-Za-z0-9_$.:-]{1,100}$');

-- Opaque user ids from the SDK. Length is counted in characters, as in the API.
CREATE DOMAIN user_id_text AS text CHECK (char_length(VALUE) BETWEEN 1 AND 200);

CREATE TABLE projects (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name           text NOT NULL,
    -- Bumped by every flag, experiment, or variant change, in the same transaction. It is
    -- the config ETag, so SDKs refetch exactly when something changed.
    config_version bigint NOT NULL DEFAULT 1,
    created_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE api_keys (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
    kind       text NOT NULL CHECK (kind IN ('client', 'server')),
    key_prefix text NOT NULL, -- the first characters, so people can tell keys apart
    key_hash   bytea NOT NULL UNIQUE CHECK (octet_length(key_hash) = 32), -- SHA-256 of the key
    created_at timestamptz NOT NULL DEFAULT now(),
    revoked_at timestamptz
);
CREATE INDEX api_keys_project ON api_keys (project_id, created_at);
COMMENT ON INDEX api_keys_project IS
    'GET /admin/api-keys: a project''s keys, oldest first. Also the project_id foreign key.';

CREATE TABLE metrics (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id   uuid NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
    key          entity_key NOT NULL,
    name         text NOT NULL,
    kind         text NOT NULL CHECK (kind IN ('conversion', 'mean')),
    event_name   event_name_text NOT NULL,
    direction    text NOT NULL CHECK (direction IN ('increase', 'decrease')),
    window_hours integer NOT NULL DEFAULT 168 CHECK (window_hours > 0),
    created_at   timestamptz NOT NULL DEFAULT now(),
    -- Its index (project_id, key) also serves the project_id foreign key.
    UNIQUE (project_id, key)
);

CREATE TABLE flags (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id  uuid NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
    key         entity_key NOT NULL,
    description text NOT NULL DEFAULT '',
    enabled     boolean NOT NULL DEFAULT false,
    rollout_bp  integer NOT NULL DEFAULT 0 CHECK (rollout_bp BETWEEN 0 AND 10000),
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (project_id, key)
);

CREATE TABLE experiments (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id    uuid NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
    key           entity_key NOT NULL,
    name          text NOT NULL,
    hypothesis    text NOT NULL DEFAULT '',
    status        text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'running', 'stopped')),
    traffic_bp    integer NOT NULL CHECK (traffic_bp BETWEEN 0 AND 10000),
    analysis_type text NOT NULL DEFAULT 'sequential'
                  CHECK (analysis_type IN ('fixed_horizon', 'sequential')),
    alpha         numeric NOT NULL DEFAULT 0.05 CHECK (alpha > 0 AND alpha < 1),
    mde_relative  numeric CHECK (mde_relative > 0), -- required to start (PRD §11)
    started_at    timestamptz,
    stopped_at    timestamptz,
    stop_reason   text,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (project_id, key),
    -- The lifecycle timestamps can't disagree with the status.
    CHECK ((status = 'draft') = (started_at IS NULL)),
    CHECK ((status = 'stopped') = (stopped_at IS NOT NULL))
);
CREATE INDEX experiments_running ON experiments (project_id) WHERE status = 'running';
COMMENT ON INDEX experiments_running IS
    'GET /v1/config: the running experiments of a project.';

CREATE TABLE variants (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    experiment_id uuid NOT NULL REFERENCES experiments (id) ON DELETE CASCADE,
    key           entity_key NOT NULL,
    name          text NOT NULL,
    -- At least 1: a zero-weight variant never gets users, and the SRM check needs every
    -- expected share to be positive.
    weight_bp     integer NOT NULL CHECK (weight_bp BETWEEN 1 AND 10000),
    is_control    boolean NOT NULL DEFAULT false,
    position      integer NOT NULL CHECK (position >= 0),
    UNIQUE (experiment_id, key),
    UNIQUE (experiment_id, position)
);
-- At most one control per experiment; "at least one" is checked when the experiment starts.
CREATE UNIQUE INDEX variants_one_control ON variants (experiment_id) WHERE is_control;
COMMENT ON INDEX variants_one_control IS
    'Enforces at most one control per experiment; also finds the control of an experiment.';

CREATE TABLE experiment_metrics (
    experiment_id     uuid NOT NULL REFERENCES experiments (id) ON DELETE CASCADE,
    metric_id         uuid NOT NULL REFERENCES metrics (id),
    role              text NOT NULL CHECK (role IN ('primary', 'secondary', 'guardrail')),
    -- The PM's pre-registered baseline: a rate for conversion metrics, a per-user mean for
    -- mean metrics. It sets the metric's mSPRT tau, so it must be positive. Required to start.
    expected_baseline numeric CHECK (expected_baseline > 0),
    PRIMARY KEY (experiment_id, metric_id)
);
-- At most one primary metric; "exactly one" is checked when the experiment starts.
CREATE UNIQUE INDEX experiment_metrics_one_primary ON experiment_metrics (experiment_id)
    WHERE role = 'primary';
COMMENT ON INDEX experiment_metrics_one_primary IS
    'Enforces at most one primary metric per experiment.';
CREATE INDEX experiment_metrics_metric ON experiment_metrics (metric_id);
COMMENT ON INDEX experiment_metrics_metric IS
    'PATCH /admin/metrics: is this metric attached to a started experiment? Also the metric_id foreign key.';

-- The dashboard's changelog: start, stop, traffic changes.
CREATE TABLE experiment_changes (
    id            bigserial PRIMARY KEY,
    experiment_id uuid NOT NULL REFERENCES experiments (id) ON DELETE CASCADE,
    action        text NOT NULL,
    details       jsonb NOT NULL DEFAULT '{}',
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX experiment_changes_experiment ON experiment_changes (experiment_id, created_at);
COMMENT ON INDEX experiment_changes_experiment IS
    'Experiment page: its changelog in time order. Also the experiment_id foreign key.';

-- One row per (experiment, user): when the user was first exposed, and to which variant.
-- Derived from "$exposure" events at ingest time (PRD §10).
CREATE TABLE exposures (
    project_id          uuid NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
    experiment_id       uuid NOT NULL REFERENCES experiments (id) ON DELETE CASCADE,
    variant_id          uuid NOT NULL REFERENCES variants (id) ON DELETE CASCADE,
    user_id             user_id_text NOT NULL,
    first_exposed_at    timestamptz NOT NULL,
    -- The user was seen in more than one variant; excluded from analysis.
    conflicted          boolean NOT NULL DEFAULT false,
    -- The server's own assignment disagreed with the SDK's.
    assignment_mismatch boolean NOT NULL DEFAULT false,
    PRIMARY KEY (experiment_id, user_id)
);
CREATE INDEX exposures_project ON exposures (project_id);
COMMENT ON INDEX exposures_project IS
    'The project_id foreign key: deleting a project cascades here without a full scan.';
CREATE INDEX exposures_variant ON exposures (variant_id);
COMMENT ON INDEX exposures_variant IS
    'The variant_id foreign key: deleting a variant cascades here without a full scan.';

-- Every accepted event, including "$exposure" events: the raw audit log and the single
-- duplicate check (by event_id). Range-partitioned by day on occurred_at, so time-bounded
-- queries skip old partitions and old days can be dropped cheaply.
CREATE TABLE events (
    project_id  uuid NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
    event_id    uuid NOT NULL,
    user_id     user_id_text NOT NULL,
    event_name  event_name_text NOT NULL,
    occurred_at timestamptz NOT NULL,
    received_at timestamptz NOT NULL DEFAULT now(),
    value       double precision,
    properties  jsonb NOT NULL DEFAULT '{}',
    -- A unique constraint on a partitioned table must include the partition key, so a
    -- duplicate is only caught if occurred_at is identical too. Retries are idempotent
    -- because the SDK fixes event_id and occurred_at once, at track() time (ADR-008).
    -- The index (project_id, ...) also serves the project_id foreign key.
    PRIMARY KEY (project_id, event_id, occurred_at)
) PARTITION BY RANGE (occurred_at);

-- Catches rows outside every daily partition. It should stay empty: Postgres won't create a
-- daily partition while the default partition holds rows that belong to that day.
CREATE TABLE events_default PARTITION OF events DEFAULT;

CREATE INDEX events_attribution ON events (project_id, event_name, user_id, occurred_at);
COMMENT ON INDEX events_attribution IS
    'Attribution (PRD §13): a metric''s events for exposed users, within each user''s window.';

CREATE TABLE results_snapshots (
    id            bigserial PRIMARY KEY,
    experiment_id uuid NOT NULL REFERENCES experiments (id) ON DELETE CASCADE,
    -- No cascade: a metric with results can't be deleted (the API never deletes metrics).
    metric_id     uuid NOT NULL REFERENCES metrics (id),
    computed_at   timestamptz NOT NULL DEFAULT now(),
    total_users   integer NOT NULL CHECK (total_users >= 0),
    srm_p_value   double precision, -- NULL when the SRM check was skipped for lack of data
    srm_flag      boolean NOT NULL,
    data          jsonb NOT NULL -- per-variant summaries and comparisons (PRD §14)
);
CREATE INDEX results_snapshots_latest
    ON results_snapshots (experiment_id, metric_id, computed_at DESC);
COMMENT ON INDEX results_snapshots_latest IS
    'GET /admin/experiments/{key}/results: the latest snapshot and the time series. Also the experiment_id foreign key.';
CREATE INDEX results_snapshots_metric ON results_snapshots (metric_id);
COMMENT ON INDEX results_snapshots_metric IS
    'The metric_id foreign key: checking for results before a metric is deleted, without a full scan.';

-- Creates any missing daily partitions of events, for UTC days from today - 7 (the API
-- accepts events up to 7 days old) through today + days_ahead. Returns how many it created.
-- The worker calls it daily with 14, and so does the bootstrap after every migration.
CREATE FUNCTION ensure_event_partitions(days_ahead integer) RETURNS integer
LANGUAGE plpgsql AS $$
DECLARE
    today   date := (now() AT TIME ZONE 'UTC')::date;
    day            date;
    partition_name text;
    created        integer := 0;
BEGIN
    -- Two callers at once would race to create the same partition; take turns instead.
    PERFORM pg_advisory_xact_lock(hashtext('ensure_event_partitions'));
    FOR day IN SELECT generate_series(today - 7, today + days_ahead, interval '1 day')::date
    LOOP
        partition_name := 'events_' || to_char(day, 'YYYYMMDD');
        IF to_regclass(partition_name) IS NULL THEN
            -- The bounds are timestamptz literals with their UTC offset, so the partition
            -- covers the same UTC day whatever the session's time zone.
            EXECUTE format(
                'CREATE TABLE %I PARTITION OF events FOR VALUES FROM (%L) TO (%L)',
                partition_name,
                day::timestamp AT TIME ZONE 'UTC',
                (day + 1)::timestamp AT TIME ZONE 'UTC'
            );
            created := created + 1;
        END IF;
    END LOOP;
    RETURN created;
END;
$$;
