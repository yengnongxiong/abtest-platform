# Performance

What this system does with 1 million and 10 million events on one laptop: how fast events load, what the attribution query costs and why, how much batching inserts is worth, and how much traffic the ingestion API takes. Every number below was printed by the command shown next to it, in this repo, on 2026-09-28 (PRD §18). None are estimates. They describe this machine, not a production server, and they vary from run to run; section 6 lists what they don't show.

## Contents
1. [Setup](#1-setup)
2. [Loading events with COPY](#2-loading-events-with-copy)
3. [The attribution query, before and after its indexes](#3-the-attribution-query-before-and-after-its-indexes)
4. [The results path: a worker look, and reading four weeks of looks](#4-the-results-path-a-worker-look-and-reading-four-weeks-of-looks)
5. [Inserts: row by row vs one statement per batch](#5-inserts-row-by-row-vs-one-statement-per-batch)
6. [Load test: the ingestion API under Locust](#6-load-test-the-ingestion-api-under-locust)
7. [What these numbers don't show](#7-what-these-numbers-dont-show)

## Summary

| Measurement | 1M events | 10M events |
|---|---:|---:|
| Loading events with COPY (indexes in place) | 48,322 events/s | 48,133 events/s |
| Attribution query, large experiment (30,080 / 300,121 users), covering index | 18 ms | 299 ms |
| Attribution query, small experiment (654 / 5,976 users), covering index | 2 ms | 20 ms |
| Pages the large experiment's query touches: no index / 0001 index / covering index | 10,016 / 9,670 / 1,215 | 98,424 / 83,746 / 12,138 |
| Inserting batches of 50, one connection: row by row | 5,136 events/s | 3,750 events/s |
| Inserting batches of 50, one connection: one `unnest` statement (what the API does) | 22,435 events/s | 12,645 events/s |
| Ingestion through the API (Locust), best of the runs | not run | 17,198 events/s (8 senders, p50 18 ms) |

## 1. Setup

**Machine.** MacBook Air (Mac16,12), Apple M4 (4 performance and 6 efficiency cores), 24 GB, macOS 27.0. Docker 29.5.2 runs in a Colima 0.10.3 VM with 4 vCPUs and 8 GiB. Postgres and the API run in that VM. Locust and the benchmark scripts run on the Mac itself and reach the VM through Colima's port forwarding, like the rest of the dev stack.

**Software.** PostgreSQL 16.15 (`postgres:16.15-alpine`) with the image's default settings, including `shared_buffers` 128MB and `work_mem` 4MB: nothing was tuned. The API is the project's own image, run by `uvicorn` without `--reload`, one worker process unless a table says otherwise, with psycopg_pool's default 4 connections per process. Python 3.14, psycopg 3.3.6, FastAPI 0.141.1, uvicorn 0.54.0, Locust 2.46.6.

**Data.** `make seed EVENTS=N` (`loadtest/seed_events.py`) recreates a separate database, `abtest_perf`, so the dev database is untouched. It applies the real migrations and bootstrap, then loads with `COPY`:
- N / 20 users (random UUIDs, like the SDK's anonymous ids), active over the last 7 days, about 20 events each;
- two running 50/50 experiments whose metrics count `purchase`: a **large** one (`checkout-button`: every user, started 3 days ago) and a **small** one (`free-shipping-banner`: 2% of users, started 1 day ago). 60% of the users each one includes are exposed, at a random time since its start, to the variant the real assignment code picks: one `exposures` row and one `$exposure` event each;
- the other events, named in a store's proportions (`loadtest/workload.py`): 55% `page_view`, 17% `search`, 15% `add_to_cart`, 5% `signup`, 8% `purchase` (with an order value).

Rows are written in time order, as live ingestion appends them. The seed ends with `VACUUM ANALYZE`, as autovacuum eventually would, so plans see fresh statistics and the visibility map is set.

**Reproduce.** Warning: on a fanless laptop these runs get it hot. The 10M seed keeps the CPUs busy for about 4 minutes, `make perf` on 10M for several more, and each load-test run for a minute.
```sh
make seed EVENTS=1000000 && make perf                   # sections 2-5 at 1M (about 35 s here)
make seed EVENTS=10000000 && make perf                  # sections 2-5 at 10M
make loadtest LOADTEST_USERS=8 LOADTEST_WORKERS=1       # section 6, one row per run
```

## 2. Loading events with COPY

| Events | Time | Events/s | Table (rows) | `events_attribution` | `events_pkey` |
|---:|---:|---:|---:|---:|---:|
| 1,000,000 | 20.7 s | 48,322 | 130 MB | 128 MB | 71 MB |
| 10,000,000 | 207.8 s | 48,133 | 1,282 MB | 1,285 MB | 732 MB |

`make seed` prints these. The rate held from 1M to 10M. Rows arrive in time order, so at any moment only one day's partition and its indexes are being written, and the random keys (event ids, user ids) land in that day's indexes only. The covering index is as large as the table itself.

## 3. The attribution query, before and after its indexes

The worker's attribution query (`AGGREGATE` in `server/src/abtest/db/results.py`, PRD §13) joins an experiment's exposures to the metric's events inside each user's window and aggregates per variant. `loadtest/explain_attribution.py` (part of `make perf`) runs that exact query, with the worker's parameters, for both experiments, in three states:
- **no index**: only the primary key, which can't help this query;
- **0001 index**: M3's `(project_id, event_name, user_id, occurred_at)`, built for the measurement in one go;
- **0002 index**: M9's covering index, the same key plus `INCLUDE (value)` (migration `0002`, ADR-021). This is the migrated index, which grew as the rows were loaded, like a live table's.

Each state is set up inside a transaction that is rolled back. Times are of the query itself with a warm cache: one run first, then the median (and range) of 7. `EXPLAIN (ANALYZE, BUFFERS)` then runs once for the plan and its page counts. "Buffers" counts 8 KB pages: `hit` came from Postgres's cache, `read` from the operating system, `temp` from files Postgres wrote because an operation outgrew `work_mem`.

**1M events** (`make seed EVENTS=1000000 && make perf`):

| Experiment (exposed users) | Index (size) | Plan | Median time (range) | Buffers |
|---|---|---|---:|---|
| checkout-button (30,080) | no index (none) | Hash Right Join; Seq Scan (4) | 25 ms (24 to 26) | shared hit=10016 |
| checkout-button (30,080) | 0001 index (89 MB) | Hash Right Join; Index Scan (1), Bitmap Heap Scan (3) | 25 ms (24 to 28) | shared hit=9670 |
| checkout-button (30,080) | 0002 index (128 MB) | Hash Right Join; Index Only Scan (4) | 18 ms (17 to 20) | shared hit=1215 |
| free-shipping-banner (654) | no index (none) | Hash Right Join; Seq Scan (2) | 7 ms (7 to 8) | shared hit=4880 |
| free-shipping-banner (654) | 0001 index (89 MB) | Hash Right Join; Index Scan (1), Bitmap Heap Scan (1) | 4 ms (4 to 6) | shared hit=4167 |
| free-shipping-banner (654) | 0002 index (128 MB) | Hash Right Join; Index Only Scan (2) | 2 ms (2 to 2) | shared hit=520 |

The index sizes compare two things at once: the covering index's extra column, and how each index was built. The 0001 index is built in one pass over the finished table, which packs its pages nearly full. The migrated covering index grew one row at a time during the load, as a live index does, which leaves pages partly empty.

**10M events** (`make seed EVENTS=10000000 && make perf`; this run came before the script printed index sizes, and `make seed` measured the covering index at 1,285 MB):

| Experiment (exposed users) | Index | Plan | Median time (range) | Buffers |
|---|---|---|---:|---|
| checkout-button (300,121) | no index | Hash Right Join; Seq Scan (4) | 437 ms (414 to 881) | shared hit=4144 read=94280, temp read=8113 written=8197 |
| checkout-button (300,121) | 0001 index | Hash Right Join; Bitmap Heap Scan (4) | 372 ms (366 to 521) | shared hit=48 read=83698, temp read=8112 written=8192 |
| checkout-button (300,121) | 0002 index | Hash Right Join; Index Only Scan (4) | 299 ms (290 to 320) | shared hit=7983 read=4155, temp read=8116 written=8188 |
| free-shipping-banner (5,976) | no index | Hash Right Join; Seq Scan (2) | 62 ms (61 to 64) | shared hit=6770 read=40783 |
| free-shipping-banner (5,976) | 0001 index | Nested Loop; Index Scan (2) | 21 ms (20 to 28) | shared hit=30130 |
| free-shipping-banner (5,976) | 0002 index | Hash Right Join; Index Only Scan (2) | 20 ms (19 to 28) | shared hit=5127 |

What the plans show:

1. **Partition pruning works on both ends.** Of the 22 daily partitions (7 days back, 14 ahead) and the default partition, the large experiment's query reads the 4 from its start to now, and the small experiment's the 2 from its start. The bound on the start was already there (PRD §13). M9 added the constant bound `occurred_at < cutoff`. Without it, the plan also listed every future partition and the default partition: the regression test `test_only_partitions_between_the_start_and_the_cutoff_are_read` failed on the old query with `events_20260929`, `events_20260930`, `events_20261001`, and `events_default` as extra partitions read. They were empty, so they cost little here, but a stopped experiment's cutoff is its stop time, and without the bound its query would read every partition since.
2. **For a small experiment, the index is what matters.** Without it, Postgres reads every event since the start to find the few it needs (62 ms at 10M); with either index it reads only purchases (20–21 ms).
3. **For a large experiment, the 0001 index barely helped.** It still made Postgres visit the table for every matching event, to read `value`. Purchases are 8% of the rows, spread over the whole table, so those visits reached most of its pages anyway: 83,746 pages against 98,424 with no index at all, and 372 ms against 437 ms.
4. **The covering index touches about 7 times fewer pages than the 0001 index** (12,138 against 83,746 at 10M; 1,215 against 9,670 at 1M). With `value` in the index, the query is an index-only scan with `Heap Fetches: 0` (excerpt below), and the events themselves are only 7,068 of those pages. The median fell to 299 ms.
5. **Where the large query's time goes now.** Grouping 300,121 users doesn't fit in the default 4 MB `work_mem`, so the sort and the hash join spill to temporary files (`Sort Method: external merge`, `Batches: 8`, about 8,000 temp pages each way). The rest goes to the hash join and to reading the exposures table. A one-off trial with `SET LOCAL work_mem = '64MB'` (not part of `make perf`) removed the spill, but the time moved by less than the run-to-run noise, so the setting stays at its default (ADR-021).
6. **At 10M, the small experiment's plan changed from a nested loop (0001 index) to a hash join (covering index).** Both take about 20 ms. At 1M both are hash joins, and the covering index halves the time (4 ms to 2 ms).

Excerpt, verbatim: the large experiment at 10M with the covering index. Only the 4 partitions since the start are read, each by an index-only scan that never visits the table:
```text
                                ->  Parallel Append  (cost=0.55..36460.82 rows=137844 width=49) (actual time=0.048..19.125 rows=110902 loops=3)
                                      Buffers: shared hit=7067 read=1
                                      ->  Parallel Index Only Scan using events_20260926_project_id_event_name_user_id_occurred_at_v_idx on events_20260926 ev_2  (cost=0.55..9540.27 rows=46636 width=49) (actual time=0.053..11.511 rows=110900 loops=1)
                                            Index Cond: ((project_id = 'bf77195e-2340-47a9-8d45-9d46c6b583ba'::uuid) AND (event_name = 'purchase'::text) AND (occurred_at >= '2026-09-25 20:05:07.107161+00'::timestamp with time zone) AND (occurred_at < '2026-09-28 20:13:10.790489+00'::timestamp with time zone))
                                            Heap Fetches: 0
                                            Buffers: shared hit=1820
                                      ->  Parallel Index Only Scan using events_20260927_project_id_event_name_user_id_occurred_at_v_idx on events_20260927 ev_3  (cost=0.55..9329.51 rows=45425 width=49) (actual time=0.051..12.034 rows=110938 loops=1)
                                            Index Cond: ((project_id = 'bf77195e-2340-47a9-8d45-9d46c6b583ba'::uuid) AND (event_name = 'purchase'::text) AND (occurred_at >= '2026-09-25 20:05:07.107161+00'::timestamp with time zone) AND (occurred_at < '2026-09-28 20:13:10.790489+00'::timestamp with time zone))
                                            Heap Fetches: 0
                                            Buffers: shared hit=1827
                                      ->  Parallel Index Only Scan using events_20260925_project_id_event_name_user_id_occurred_at_v_idx on events_20260925 ev_1  (cost=0.55..9069.09 rows=7887 width=49) (actual time=0.044..3.847 rows=5956 loops=3)
                                            Index Cond: ((project_id = 'bf77195e-2340-47a9-8d45-9d46c6b583ba'::uuid) AND (event_name = 'purchase'::text) AND (occurred_at >= '2026-09-25 20:05:07.107161+00'::timestamp with time zone) AND (occurred_at < '2026-09-28 20:13:10.790489+00'::timestamp with time zone))
                                            Heap Fetches: 0
                                            Buffers: shared hit=1878 read=1
                                      ->  Parallel Index Only Scan using events_20260928_project_id_event_name_user_id_occurred_at_v_idx on events_20260928 ev_4  (cost=0.55..7832.72 rows=37896 width=49) (actual time=0.038..9.292 rows=92999 loops=1)
                                            Index Cond: ((project_id = 'bf77195e-2340-47a9-8d45-9d46c6b583ba'::uuid) AND (event_name = 'purchase'::text) AND (occurred_at >= '2026-09-25 20:05:07.107161+00'::timestamp with time zone) AND (occurred_at < '2026-09-28 20:13:10.790489+00'::timestamp with time zone))
                                            Heap Fetches: 0
                                            Buffers: shared hit=1542
```

The complete plans at 10M, verbatim from the script:

<details><summary>checkout-button, no index</summary>

```text
GroupAggregate  (cost=171460.77..216710.80 rows=200 width=48) (actual time=406.032..488.347 rows=2 loops=1)
  Group Key: x.variant_id
  Buffers: shared hit=4144 read=94280, temp read=8113 written=8197
  ->  Finalize GroupAggregate  (cost=171460.77..208606.61 rows=294625 width=65) (actual time=330.143..469.256 rows=300121 loops=1)
        Group Key: x.variant_id, x.user_id
        Buffers: shared hit=4144 read=94280, temp read=8113 written=8197
        ->  Gather Merge  (cost=171460.77..203158.10 rows=250226 width=65) (actual time=330.129..422.433 rows=301945 loops=1)
              Workers Planned: 2
              Workers Launched: 2
              Buffers: shared hit=4144 read=94280, temp read=8113 written=8197
              ->  Partial GroupAggregate  (cost=170460.75..173275.79 rows=125113 width=65) (actual time=311.109..345.256 rows=100648 loops=3)
                    Group Key: x.variant_id, x.user_id
                    Buffers: shared hit=4144 read=94280, temp read=8113 written=8197
                    ->  Sort  (cost=170460.75..170773.53 rows=125113 width=65) (actual time=311.071..327.436 rows=106335 loops=3)
                          Sort Key: x.variant_id, x.user_id
                          Sort Method: external merge  Disk: 7520kB
                          Buffers: shared hit=4144 read=94280, temp read=8113 written=8197
                          Worker 0:  Sort Method: external merge  Disk: 5952kB
                          Worker 1:  Sort Method: external merge  Disk: 7248kB
                          ->  Parallel Hash Right Join  (cost=9520.17..154733.64 rows=125113 width=65) (actual time=184.869..219.381 rows=106335 loops=3)
                                Hash Cond: ((ev.user_id)::text = (x.user_id)::text)
                                Join Filter: ((ev.occurred_at >= x.first_exposed_at) AND (ev.occurred_at < LEAST((x.first_exposed_at + '7 days'::interval), '2026-09-28 20:13:10.790489+00'::timestamp with time zone)))
                                Rows Removed by Join Filter: 33181
                                Buffers: shared hit=4056 read=94280, temp read=5523 written=5600
                                ->  Parallel Append  (cost=0.00..140813.63 rows=137844 width=49) (actual time=0.043..130.247 rows=110902 loops=3)
                                      Buffers: shared hit=1670 read=91626
                                      ->  Parallel Seq Scan on events_20260927 ev_3  (cost=0.00..37182.36 rows=45425 width=49) (actual time=0.045..103.824 rows=110938 loops=1)
                                            Filter: ((occurred_at >= '2026-09-25 20:05:07.107161+00'::timestamp with time zone) AND (occurred_at < '2026-09-28 20:13:10.790489+00'::timestamp with time zone) AND (project_id = 'bf77195e-2340-47a9-8d45-9d46c6b583ba'::uuid) AND ((event_name)::text = 'purchase'::text))
                                            Rows Removed by Filter: 1373985
                                            Buffers: shared hit=288 read=24520
                                      ->  Parallel Seq Scan on events_20260926 ev_2  (cost=0.00..37180.82 rows=46636 width=49) (actual time=0.042..108.202 rows=110900 loops=1)
                                            Filter: ((occurred_at >= '2026-09-25 20:05:07.107161+00'::timestamp with time zone) AND (occurred_at < '2026-09-28 20:13:10.790489+00'::timestamp with time zone) AND (project_id = 'bf77195e-2340-47a9-8d45-9d46c6b583ba'::uuid) AND ((event_name)::text = 'purchase'::text))
                                            Rows Removed by Filter: 1373838
                                            Buffers: shared hit=288 read=24520
                                      ->  Parallel Seq Scan on events_20260925 ev_1  (cost=0.00..34429.71 rows=7887 width=49) (actual time=19.745..24.818 rows=5956 loops=3)
                                            Filter: ((occurred_at >= '2026-09-25 20:05:07.107161+00'::timestamp with time zone) AND (occurred_at < '2026-09-28 20:13:10.790489+00'::timestamp with time zone) AND (project_id = 'bf77195e-2340-47a9-8d45-9d46c6b583ba'::uuid) AND ((event_name)::text = 'purchase'::text))
                                            Rows Removed by Filter: 461152
                                            Buffers: shared hit=800 read=21952
                                      ->  Parallel Seq Scan on events_20260928 ev_4  (cost=0.00..31331.53 rows=37896 width=49) (actual time=0.037..89.733 rows=92999 loops=1)
                                            Filter: ((occurred_at >= '2026-09-25 20:05:07.107161+00'::timestamp with time zone) AND (occurred_at < '2026-09-28 20:13:10.790489+00'::timestamp with time zone) AND (project_id = 'bf77195e-2340-47a9-8d45-9d46c6b583ba'::uuid) AND ((event_name)::text = 'purchase'::text))
                                            Rows Removed by Filter: 1155424
                                            Buffers: shared hit=294 read=20634
                                ->  Parallel Hash  (cost=6612.26..6612.26 rows=125113 width=57) (actual time=35.361..35.362 rows=100040 loops=3)
                                      Buckets: 131072  Batches: 8  Memory Usage: 4608kB
                                      Buffers: shared hit=2364 read=2654, temp written=2736
                                      ->  Parallel Seq Scan on exposures x  (cost=0.00..6612.26 rows=125113 width=57) (actual time=11.932..21.618 rows=100040 loops=3)
                                            Filter: ((NOT conflicted) AND (experiment_id = '0305ab4c-d4fd-4dab-970b-e6ecf88c62fd'::uuid))
                                            Rows Removed by Filter: 1992
                                            Buffers: shared hit=2364 read=2654
Planning:
  Buffers: shared hit=3
Planning Time: 0.332 ms
JIT:
  Functions: 105
  Options: Inlining false, Optimization false, Expressions true, Deforming true
  Timing: Generation 4.344 ms, Inlining 0.000 ms, Optimization 1.533 ms, Emission 34.439 ms, Total 40.316 ms
Execution Time: 490.156 ms
```

</details>

<details><summary>checkout-button, 0001 index</summary>

```text
GroupAggregate  (cost=153143.78..198393.81 rows=200 width=48) (actual time=340.043..416.961 rows=2 loops=1)
  Group Key: x.variant_id
  Buffers: shared hit=48 read=83698, temp read=8112 written=8192
  ->  Finalize GroupAggregate  (cost=153143.78..190289.62 rows=294625 width=65) (actual time=251.600..395.660 rows=300121 loops=1)
        Group Key: x.variant_id, x.user_id
        Buffers: shared hit=48 read=83698, temp read=8112 written=8192
        ->  Gather Merge  (cost=153143.78..184841.11 rows=250226 width=65) (actual time=251.587..340.754 rows=301875 loops=1)
              Workers Planned: 2
              Workers Launched: 2
              Buffers: shared hit=48 read=83698, temp read=8112 written=8192
              ->  Partial GroupAggregate  (cost=152143.76..154958.80 rows=125113 width=65) (actual time=234.441..272.188 rows=100625 loops=3)
                    Group Key: x.variant_id, x.user_id
                    Buffers: shared hit=48 read=83698, temp read=8112 written=8192
                    ->  Sort  (cost=152143.76..152456.54 rows=125113 width=65) (actual time=234.419..251.955 rows=106335 loops=3)
                          Sort Key: x.variant_id, x.user_id
                          Sort Method: external merge  Disk: 6112kB
                          Buffers: shared hit=48 read=83698, temp read=8112 written=8192
                          Worker 0:  Sort Method: external merge  Disk: 7152kB
                          Worker 1:  Sort Method: external merge  Disk: 7448kB
                          ->  Parallel Hash Right Join  (cost=16267.62..136416.64 rows=125113 width=65) (actual time=124.306..151.167 rows=106335 loops=3)
                                Hash Cond: ((ev.user_id)::text = (x.user_id)::text)
                                Join Filter: ((ev.occurred_at >= x.first_exposed_at) AND (ev.occurred_at < LEAST((x.first_exposed_at + '7 days'::interval), '2026-09-28 20:13:10.790489+00'::timestamp with time zone)))
                                Rows Removed by Join Filter: 33181
                                Buffers: shared hit=21 read=83695, temp read=5523 written=5596
                                ->  Parallel Append  (cost=6747.45..122496.64 rows=137844 width=49) (actual time=10.683..82.280 rows=110902 loops=3)
                                      Buffers: shared read=78676
                                      ->  Parallel Bitmap Heap Scan on events_20260926 ev_2  (cost=6827.77..32568.49 rows=46636 width=49) (actual time=4.660..27.078 rows=36967 loops=3)
                                            Recheck Cond: ((project_id = 'bf77195e-2340-47a9-8d45-9d46c6b583ba'::uuid) AND ((event_name)::text = 'purchase'::text) AND (occurred_at >= '2026-09-25 20:05:07.107161+00'::timestamp with time zone) AND (occurred_at < '2026-09-28 20:13:10.790489+00'::timestamp with time zone))
                                            Heap Blocks: exact=568
                                            Buffers: shared read=25824
                                            ->  Bitmap Index Scan on events_20260926_project_id_event_name_user_id_occurred_at_idx  (cost=0.00..6799.79 rows=111927 width=0) (actual time=11.879..11.879 rows=110900 loops=1)
                                                  Index Cond: ((project_id = 'bf77195e-2340-47a9-8d45-9d46c6b583ba'::uuid) AND ((event_name)::text = 'purchase'::text) AND (occurred_at >= '2026-09-25 20:05:07.107161+00'::timestamp with time zone) AND (occurred_at < '2026-09-28 20:13:10.790489+00'::timestamp with time zone))
                                                  Buffers: shared read=1280
                                      ->  Parallel Bitmap Heap Scan on events_20260925 ev_1  (cost=6747.45..29637.23 rows=7887 width=49) (actual time=7.946..20.052 rows=17868 loops=1)
                                            Recheck Cond: ((project_id = 'bf77195e-2340-47a9-8d45-9d46c6b583ba'::uuid) AND ((event_name)::text = 'purchase'::text) AND (occurred_at >= '2026-09-25 20:05:07.107161+00'::timestamp with time zone) AND (occurred_at < '2026-09-28 20:13:10.790489+00'::timestamp with time zone))
                                            Buffers: shared read=5278
                                            ->  Bitmap Index Scan on events_20260925_project_id_event_name_user_id_occurred_at_idx  (cost=0.00..6742.72 rows=18929 width=0) (actual time=7.635..7.635 rows=17868 loops=1)
                                                  Index Cond: ((project_id = 'bf77195e-2340-47a9-8d45-9d46c6b583ba'::uuid) AND ((event_name)::text = 'purchase'::text) AND (occurred_at >= '2026-09-25 20:05:07.107161+00'::timestamp with time zone) AND (occurred_at < '2026-09-28 20:13:10.790489+00'::timestamp with time zone))
                                                  Buffers: shared read=1280
                                      ->  Parallel Bitmap Heap Scan on events_20260927 ev_3  (cost=6651.45..32367.96 rows=45425 width=49) (actual time=6.450..37.083 rows=55469 loops=2)
                                            Recheck Cond: ((project_id = 'bf77195e-2340-47a9-8d45-9d46c6b583ba'::uuid) AND ((event_name)::text = 'purchase'::text) AND (occurred_at >= '2026-09-25 20:05:07.107161+00'::timestamp with time zone) AND (occurred_at < '2026-09-28 20:13:10.790489+00'::timestamp with time zone))
                                            Heap Blocks: exact=7041
                                            Buffers: shared read=25820
                                            ->  Bitmap Index Scan on events_20260927_project_id_event_name_user_id_occurred_at_idx  (cost=0.00..6624.20 rows=109021 width=0) (actual time=10.876..10.876 rows=110938 loops=1)
                                                  Index Cond: ((project_id = 'bf77195e-2340-47a9-8d45-9d46c6b583ba'::uuid) AND ((event_name)::text = 'purchase'::text) AND (occurred_at >= '2026-09-25 20:05:07.107161+00'::timestamp with time zone) AND (occurred_at < '2026-09-28 20:13:10.790489+00'::timestamp with time zone))
                                                  Buffers: shared read=1280
                                      ->  Parallel Bitmap Heap Scan on events_20260928 ev_4  (cost=5547.81..27233.73 rows=37896 width=49) (actual time=10.271..56.944 rows=92999 loops=1)
                                            Recheck Cond: ((project_id = 'bf77195e-2340-47a9-8d45-9d46c6b583ba'::uuid) AND ((event_name)::text = 'purchase'::text) AND (occurred_at >= '2026-09-25 20:05:07.107161+00'::timestamp with time zone) AND (occurred_at < '2026-09-28 20:13:10.790489+00'::timestamp with time zone))
                                            Heap Blocks: exact=20681
                                            Buffers: shared read=21754
                                            ->  Bitmap Index Scan on events_20260928_project_id_event_name_user_id_occurred_at_idx  (cost=0.00..5525.07 rows=90950 width=0) (actual time=8.611..8.611 rows=92999 loops=1)
                                                  Index Cond: ((project_id = 'bf77195e-2340-47a9-8d45-9d46c6b583ba'::uuid) AND ((event_name)::text = 'purchase'::text) AND (occurred_at >= '2026-09-25 20:05:07.107161+00'::timestamp with time zone) AND (occurred_at < '2026-09-28 20:13:10.790489+00'::timestamp with time zone))
                                                  Buffers: shared read=1073
                                ->  Parallel Hash  (cost=6612.26..6612.26 rows=125113 width=57) (actual time=27.664..27.665 rows=100040 loops=3)
                                      Buckets: 131072  Batches: 8  Memory Usage: 4576kB
                                      Buffers: shared read=5018, temp written=2732
                                      ->  Parallel Seq Scan on exposures x  (cost=0.00..6612.26 rows=125113 width=57) (actual time=9.482..17.572 rows=100040 loops=3)
                                            Filter: ((NOT conflicted) AND (experiment_id = '0305ab4c-d4fd-4dab-970b-e6ecf88c62fd'::uuid))
                                            Rows Removed by Filter: 1992
                                            Buffers: shared read=5018
Planning:
  Buffers: shared read=3
Planning Time: 0.274 ms
JIT:
  Functions: 105
  Options: Inlining false, Optimization false, Expressions true, Deforming true
  Timing: Generation 3.086 ms, Inlining 0.000 ms, Optimization 0.954 ms, Emission 27.691 ms, Total 31.731 ms
Execution Time: 418.547 ms
```

</details>

<details><summary>checkout-button, 0002 index</summary>

```text
GroupAggregate  (cost=67107.96..112357.99 rows=200 width=48) (actual time=260.891..341.803 rows=2 loops=1)
  Group Key: x.variant_id
  Buffers: shared hit=7983 read=4155, temp read=8116 written=8188
  ->  Finalize GroupAggregate  (cost=67107.96..104253.81 rows=294625 width=65) (actual time=184.609..322.355 rows=300121 loops=1)
        Group Key: x.variant_id, x.user_id
        Buffers: shared hit=7983 read=4155, temp read=8116 written=8188
        ->  Gather Merge  (cost=67107.96..98805.30 rows=250226 width=65) (actual time=184.604..274.499 rows=301710 loops=1)
              Workers Planned: 2
              Workers Launched: 2
              Buffers: shared hit=7983 read=4155, temp read=8116 written=8188
              ->  Partial GroupAggregate  (cost=66107.94..68922.98 rows=125113 width=65) (actual time=167.941..201.921 rows=100570 loops=3)
                    Group Key: x.variant_id, x.user_id
                    Buffers: shared hit=7983 read=4155, temp read=8116 written=8188
                    ->  Sort  (cost=66107.94..66420.72 rows=125113 width=65) (actual time=167.915..183.649 rows=106335 loops=3)
                          Sort Key: x.variant_id, x.user_id
                          Sort Method: external merge  Disk: 7584kB
                          Buffers: shared hit=7983 read=4155, temp read=8116 written=8188
                          Worker 0:  Sort Method: external merge  Disk: 7144kB
                          Worker 1:  Sort Method: external merge  Disk: 5984kB
                          ->  Parallel Hash Right Join  (cost=9520.72..50380.83 rows=125113 width=65) (actual time=58.289..85.045 rows=106335 loops=3)
                                Hash Cond: ((ev.user_id)::text = (x.user_id)::text)
                                Join Filter: ((ev.occurred_at >= x.first_exposed_at) AND (ev.occurred_at < LEAST((x.first_exposed_at + '7 days'::interval), '2026-09-28 20:13:10.790489+00'::timestamp with time zone)))
                                Rows Removed by Join Filter: 33181
                                Buffers: shared hit=7953 read=4155, temp read=5527 written=5592
                                ->  Parallel Append  (cost=0.55..36460.82 rows=137844 width=49) (actual time=0.048..19.125 rows=110902 loops=3)
                                      Buffers: shared hit=7067 read=1
                                      ->  Parallel Index Only Scan using events_20260926_project_id_event_name_user_id_occurred_at_v_idx on events_20260926 ev_2  (cost=0.55..9540.27 rows=46636 width=49) (actual time=0.053..11.511 rows=110900 loops=1)
                                            Index Cond: ((project_id = 'bf77195e-2340-47a9-8d45-9d46c6b583ba'::uuid) AND (event_name = 'purchase'::text) AND (occurred_at >= '2026-09-25 20:05:07.107161+00'::timestamp with time zone) AND (occurred_at < '2026-09-28 20:13:10.790489+00'::timestamp with time zone))
                                            Heap Fetches: 0
                                            Buffers: shared hit=1820
                                      ->  Parallel Index Only Scan using events_20260927_project_id_event_name_user_id_occurred_at_v_idx on events_20260927 ev_3  (cost=0.55..9329.51 rows=45425 width=49) (actual time=0.051..12.034 rows=110938 loops=1)
                                            Index Cond: ((project_id = 'bf77195e-2340-47a9-8d45-9d46c6b583ba'::uuid) AND (event_name = 'purchase'::text) AND (occurred_at >= '2026-09-25 20:05:07.107161+00'::timestamp with time zone) AND (occurred_at < '2026-09-28 20:13:10.790489+00'::timestamp with time zone))
                                            Heap Fetches: 0
                                            Buffers: shared hit=1827
                                      ->  Parallel Index Only Scan using events_20260925_project_id_event_name_user_id_occurred_at_v_idx on events_20260925 ev_1  (cost=0.55..9069.09 rows=7887 width=49) (actual time=0.044..3.847 rows=5956 loops=3)
                                            Index Cond: ((project_id = 'bf77195e-2340-47a9-8d45-9d46c6b583ba'::uuid) AND (event_name = 'purchase'::text) AND (occurred_at >= '2026-09-25 20:05:07.107161+00'::timestamp with time zone) AND (occurred_at < '2026-09-28 20:13:10.790489+00'::timestamp with time zone))
                                            Heap Fetches: 0
                                            Buffers: shared hit=1878 read=1
                                      ->  Parallel Index Only Scan using events_20260928_project_id_event_name_user_id_occurred_at_v_idx on events_20260928 ev_4  (cost=0.55..7832.72 rows=37896 width=49) (actual time=0.038..9.292 rows=92999 loops=1)
                                            Index Cond: ((project_id = 'bf77195e-2340-47a9-8d45-9d46c6b583ba'::uuid) AND (event_name = 'purchase'::text) AND (occurred_at >= '2026-09-25 20:05:07.107161+00'::timestamp with time zone) AND (occurred_at < '2026-09-28 20:13:10.790489+00'::timestamp with time zone))
                                            Heap Fetches: 0
                                            Buffers: shared hit=1542
                                ->  Parallel Hash  (cost=6612.26..6612.26 rows=125113 width=57) (actual time=26.031..26.032 rows=100040 loops=3)
                                      Buckets: 131072  Batches: 8  Memory Usage: 4608kB
                                      Buffers: shared hit=864 read=4154, temp written=2740
                                      ->  Parallel Seq Scan on exposures x  (cost=0.00..6612.26 rows=125113 width=57) (actual time=7.213..15.566 rows=100040 loops=3)
                                            Filter: ((NOT conflicted) AND (experiment_id = '0305ab4c-d4fd-4dab-970b-e6ecf88c62fd'::uuid))
                                            Rows Removed by Filter: 1992
                                            Buffers: shared hit=864 read=4154
Planning:
  Buffers: shared hit=3
Planning Time: 0.278 ms
JIT:
  Functions: 81
  Options: Inlining false, Optimization false, Expressions true, Deforming true
  Timing: Generation 2.193 ms, Inlining 0.000 ms, Optimization 0.675 ms, Emission 21.095 ms, Total 23.964 ms
Execution Time: 343.194 ms
```

</details>

<details><summary>free-shipping-banner, no index</summary>

```text
GroupAggregate  (cost=75655.38..76539.51 rows=200 width=48) (actual time=64.562..67.587 rows=2 loops=1)
  Group Key: x.variant_id
  Buffers: shared hit=6770 read=40783
  ->  Finalize GroupAggregate  (cost=75655.38..76377.32 rows=5825 width=65) (actual time=63.544..67.202 rows=5976 loops=1)
        Group Key: x.variant_id, x.user_id
        Buffers: shared hit=6770 read=40783
        ->  Gather Merge  (cost=75655.38..76270.51 rows=4856 width=65) (actual time=63.541..66.381 rows=6005 loops=1)
              Workers Planned: 2
              Workers Launched: 2
              Buffers: shared hit=6770 read=40783
              ->  Partial GroupAggregate  (cost=74655.35..74709.98 rows=2428 width=65) (actual time=54.248..54.686 rows=2002 loops=3)
                    Group Key: x.variant_id, x.user_id
                    Buffers: shared hit=6770 read=40783
                    ->  Sort  (cost=74655.35..74661.42 rows=2428 width=65) (actual time=54.245..54.317 rows=2009 loops=3)
                          Sort Key: x.variant_id, x.user_id
                          Sort Method: quicksort  Memory: 56kB
                          Buffers: shared hit=6770 read=40783
                          Worker 0:  Sort Method: quicksort  Memory: 641kB
                          Worker 1:  Sort Method: quicksort  Memory: 25kB
                          ->  Parallel Hash Right Join  (cost=5661.02..74518.83 rows=2428 width=65) (actual time=15.998..52.832 rows=2009 loops=3)
                                Hash Cond: ((ev.user_id)::text = (x.user_id)::text)
                                Join Filter: ((ev.occurred_at >= x.first_exposed_at) AND (ev.occurred_at < LEAST((x.first_exposed_at + '7 days'::interval), '2026-09-28 20:13:10.790489+00'::timestamp with time zone)))
                                Rows Removed by Join Filter: 230
                                Buffers: shared hit=6740 read=40783
                                ->  Parallel Append  (cost=0.00..68739.41 rows=45106 width=49) (actual time=14.733..48.923 rows=37037 loops=3)
                                      Buffers: shared hit=4953 read=40783
                                      ->  Parallel Seq Scan on events_20260927 ev_1  (cost=0.00..37182.36 rows=7210 width=49) (actual time=18.080..22.637 rows=6038 loops=3)
                                            Filter: ((occurred_at >= '2026-09-27 20:05:07.107161+00'::timestamp with time zone) AND (occurred_at < '2026-09-28 20:13:10.790489+00'::timestamp with time zone) AND (project_id = 'bf77195e-2340-47a9-8d45-9d46c6b583ba'::uuid) AND ((event_name)::text = 'purchase'::text))
                                            Rows Removed by Filter: 488937
                                            Buffers: shared hit=4440 read=20368
                                      ->  Parallel Seq Scan on events_20260928 ev_2  (cost=0.00..31331.53 rows=37896 width=49) (actual time=0.011..37.263 rows=46500 loops=2)
                                            Filter: ((occurred_at >= '2026-09-27 20:05:07.107161+00'::timestamp with time zone) AND (occurred_at < '2026-09-28 20:13:10.790489+00'::timestamp with time zone) AND (project_id = 'bf77195e-2340-47a9-8d45-9d46c6b583ba'::uuid) AND ((event_name)::text = 'purchase'::text))
                                            Rows Removed by Filter: 577712
                                            Buffers: shared hit=513 read=20415
                                ->  Parallel Hash  (cost=5630.67..5630.67 rows=2428 width=57) (actual time=0.797..0.798 rows=1992 loops=3)
                                      Buckets: 8192  Batches: 1  Memory Usage: 640kB
                                      Buffers: shared hit=1759
                                      ->  Parallel Bitmap Heap Scan on exposures x  (cost=333.57..5630.67 rows=2428 width=57) (actual time=0.412..1.726 rows=5976 loops=1)
                                            Recheck Cond: (experiment_id = '2ea5253d-caf6-405f-acb4-502c02506501'::uuid)
                                            Filter: (NOT conflicted)
                                            Heap Blocks: exact=1684
                                            Buffers: shared hit=1759
                                            ->  Bitmap Index Scan on exposures_pkey  (cost=0.00..332.12 rows=5826 width=0) (actual time=0.304..0.304 rows=5976 loops=1)
                                                  Index Cond: (experiment_id = '2ea5253d-caf6-405f-acb4-502c02506501'::uuid)
                                                  Buffers: shared hit=75
Planning:
  Buffers: shared hit=3
Planning Time: 0.171 ms
Execution Time: 67.637 ms
```

</details>

<details><summary>free-shipping-banner, 0001 index</summary>

```text
GroupAggregate  (cost=43280.34..44164.47 rows=200 width=48) (actual time=19.183..22.513 rows=2 loops=1)
  Group Key: x.variant_id
  Buffers: shared hit=30130
  ->  Finalize GroupAggregate  (cost=43280.34..44002.28 rows=5825 width=65) (actual time=17.429..22.076 rows=5976 loops=1)
        Group Key: x.variant_id, x.user_id
        Buffers: shared hit=30130
        ->  Gather Merge  (cost=43280.34..43895.47 rows=4856 width=65) (actual time=17.425..21.042 rows=5976 loops=1)
              Workers Planned: 2
              Workers Launched: 2
              Buffers: shared hit=30130
              ->  Partial GroupAggregate  (cost=42280.32..42334.95 rows=2428 width=65) (actual time=8.922..9.377 rows=1992 loops=3)
                    Group Key: x.variant_id, x.user_id
                    Buffers: shared hit=30130
                    ->  Sort  (cost=42280.32..42286.39 rows=2428 width=65) (actual time=8.919..9.020 rows=2009 loops=3)
                          Sort Key: x.variant_id, x.user_id
                          Sort Method: quicksort  Memory: 396kB
                          Buffers: shared hit=30130
                          Worker 0:  Sort Method: quicksort  Memory: 139kB
                          Worker 1:  Sort Method: quicksort  Memory: 140kB
                          ->  Nested Loop Left Join  (cost=334.13..42143.79 rows=2428 width=65) (actual time=0.227..7.479 rows=2009 loops=3)
                                Buffers: shared hit=30100
                                ->  Parallel Bitmap Heap Scan on exposures x  (cost=333.57..5630.67 rows=2428 width=57) (actual time=0.203..0.892 rows=1992 loops=3)
                                      Recheck Cond: (experiment_id = '2ea5253d-caf6-405f-acb4-502c02506501'::uuid)
                                      Filter: (NOT conflicted)
                                      Heap Blocks: exact=1037
                                      Buffers: shared hit=1759
                                      ->  Bitmap Index Scan on exposures_pkey  (cost=0.00..332.12 rows=5826 width=0) (actual time=0.434..0.434 rows=5976 loops=1)
                                            Index Cond: (experiment_id = '2ea5253d-caf6-405f-acb4-502c02506501'::uuid)
                                            Buffers: shared hit=75
                                ->  Append  (cost=0.56..15.02 rows=2 width=49) (actual time=0.003..0.003 rows=0 loops=5976)
                                      Buffers: shared hit=28341
                                      ->  Index Scan using events_20260927_project_id_event_name_user_id_occurred_at_idx on events_20260927 ev_1  (cost=0.56..7.58 rows=1 width=49) (actual time=0.002..0.002 rows=0 loops=943)
                                            Index Cond: ((project_id = 'bf77195e-2340-47a9-8d45-9d46c6b583ba'::uuid) AND ((event_name)::text = 'purchase'::text) AND ((user_id)::text = (x.user_id)::text) AND (occurred_at >= x.first_exposed_at) AND (occurred_at < LEAST((x.first_exposed_at + '7 days'::interval), '2026-09-28 20:13:10.790489+00'::timestamp with time zone)) AND (occurred_at >= '2026-09-27 20:05:07.107161+00'::timestamp with time zone) AND (occurred_at < '2026-09-28 20:13:10.790489+00'::timestamp with time zone))
                                            Buffers: shared hit=3789
                                      ->  Index Scan using events_20260928_project_id_event_name_user_id_occurred_at_idx on events_20260928 ev_2  (cost=0.56..7.42 rows=1 width=49) (actual time=0.002..0.002 rows=0 loops=5976)
                                            Index Cond: ((project_id = 'bf77195e-2340-47a9-8d45-9d46c6b583ba'::uuid) AND ((event_name)::text = 'purchase'::text) AND ((user_id)::text = (x.user_id)::text) AND (occurred_at >= x.first_exposed_at) AND (occurred_at < LEAST((x.first_exposed_at + '7 days'::interval), '2026-09-28 20:13:10.790489+00'::timestamp with time zone)) AND (occurred_at >= '2026-09-27 20:05:07.107161+00'::timestamp with time zone) AND (occurred_at < '2026-09-28 20:13:10.790489+00'::timestamp with time zone))
                                            Buffers: shared hit=24552
Planning:
  Buffers: shared hit=3
Planning Time: 0.216 ms
Execution Time: 22.621 ms
```

</details>

<details><summary>free-shipping-banner, 0002 index</summary>

```text
HashAggregate  (cost=24239.87..24241.87 rows=200 width=48) (actual time=27.625..27.627 rows=2 loops=1)
  Group Key: x.variant_id
  Batches: 1  Memory Usage: 40kB
  Buffers: shared hit=5127
  ->  HashAggregate  (cost=24021.44..24079.69 rows=5825 width=65) (actual time=26.817..27.194 rows=5976 loops=1)
        Group Key: x.variant_id, x.user_id
        Batches: 1  Memory Usage: 1489kB
        Buffers: shared hit=5127
        ->  Hash Right Join  (cost=5746.53..23963.18 rows=5826 width=65) (actual time=2.165..25.892 rows=6026 loops=1)
              Hash Cond: ((ev.user_id)::text = (x.user_id)::text)
              Join Filter: ((ev.occurred_at >= x.first_exposed_at) AND (ev.occurred_at < LEAST((x.first_exposed_at + '7 days'::interval), '2026-09-28 20:13:10.790489+00'::timestamp with time zone)))
              Rows Removed by Join Filter: 690
              Buffers: shared hit=5127
              ->  Append  (cost=0.55..17933.03 rows=108255 width=49) (actual time=0.009..17.896 rows=111112 loops=1)
                    Buffers: shared hit=3368
                    ->  Index Only Scan using events_20260927_project_id_event_name_user_id_occurred_at_v_idx on events_20260927 ev_1  (cost=0.55..9028.50 rows=17305 width=49) (actual time=0.009..4.944 rows=18113 loops=1)
                          Index Cond: ((project_id = 'bf77195e-2340-47a9-8d45-9d46c6b583ba'::uuid) AND (event_name = 'purchase'::text) AND (occurred_at >= '2026-09-27 20:05:07.107161+00'::timestamp with time zone) AND (occurred_at < '2026-09-28 20:13:10.790489+00'::timestamp with time zone))
                          Heap Fetches: 0
                          Buffers: shared hit=1826
                    ->  Index Only Scan using events_20260928_project_id_event_name_user_id_occurred_at_v_idx on events_20260928 ev_2  (cost=0.55..8363.26 rows=90950 width=49) (actual time=0.011..8.651 rows=92999 loops=1)
                          Index Cond: ((project_id = 'bf77195e-2340-47a9-8d45-9d46c6b583ba'::uuid) AND (event_name = 'purchase'::text) AND (occurred_at >= '2026-09-27 20:05:07.107161+00'::timestamp with time zone) AND (occurred_at < '2026-09-28 20:13:10.790489+00'::timestamp with time zone))
                          Heap Fetches: 0
                          Buffers: shared hit=1542
              ->  Hash  (cost=5673.15..5673.15 rows=5826 width=57) (actual time=1.733..1.733 rows=5976 loops=1)
                    Buckets: 8192  Batches: 1  Memory Usage: 625kB
                    Buffers: shared hit=1759
                    ->  Bitmap Heap Scan on exposures x  (cost=333.57..5673.15 rows=5826 width=57) (actual time=0.315..1.293 rows=5976 loops=1)
                          Recheck Cond: (experiment_id = '2ea5253d-caf6-405f-acb4-502c02506501'::uuid)
                          Filter: (NOT conflicted)
                          Heap Blocks: exact=1684
                          Buffers: shared hit=1759
                          ->  Bitmap Index Scan on exposures_pkey  (cost=0.00..332.12 rows=5826 width=0) (actual time=0.225..0.225 rows=5976 loops=1)
                                Index Cond: (experiment_id = '2ea5253d-caf6-405f-acb4-502c02506501'::uuid)
                                Buffers: shared hit=75
Planning:
  Buffers: shared hit=3
Planning Time: 0.165 ms
Execution Time: 27.775 ms
```

</details>

## 4. The results path: a worker look, and reading four weeks of looks

`loadtest/results_benchmark.py` (part of `make perf`) measures what the worker does every 5 minutes, and what the dashboard does on every page view:
- **A look**: `compute_snapshots` for one experiment, as the worker and `POST .../recompute` run it: the attribution query for both metrics (conversion and revenue), the statistics, and the snapshot inserts, committed. Median of 5, after one warm-up.
- **Reading results**: `GET /admin/experiments/{key}/results` through the real app, in-process, with the large experiment's primary metric holding 1 snapshot, then 8,064: four weeks of looks every 5 minutes (ADR-018), each a full-size copy of the latest.

| | 1M events | 10M events |
|---|---:|---:|
| A look at the large experiment | 57 ms | 623 ms and 873 ms (two runs) |
| A look at the small experiment | 18 ms | 45 ms and 53 ms (two runs) |
| `GET .../results`, 1 snapshot | 6 ms | 5 ms |
| `GET .../results`, 8,064 snapshots | 73 ms | 69 ms (142 ms before the change below) |

The 1M column comes from the committed script and code (`uv run --project server python loadtest/results_benchmark.py`, run after `make perf`). The 10M column comes from two earlier runs, before two small changes: the script now opens a connection per look, and the series query now stops at the latest snapshot it read.

- A look at a 300,121-user experiment takes under a second on 10M events, so a worker run every 5 minutes has plenty of room. ADR-017 had only measured a 38 ms run with one small experiment. At 10M, two runs of the same command differed by 250 ms; both reused one database connection for every look. The script now opens a connection per look, as each worker run does.
- **The results read was narrowed in M9.** The series used to read every snapshot's whole `data` column (per-variant summaries, comparisons, and mSPRT state) to draw a chart that needs a few fields per look. It now reads `total_users`, `srm_flag`, and `data->'comparisons'` for the series, and the whole row only for the latest snapshot. The response is unchanged (1,915 KB before and after, and a test checks each series point against its snapshot), and `GET .../results` with four weeks of looks went from 142 ms to 69 ms at 10M. The response is still about 2 MB and grows by one point every 5 minutes. Downsampling the series for experiments that run for months is future work (ADR-018).

## 5. Inserts: row by row vs one statement per batch

`loadtest/insert_benchmark.py` (the last step of `make perf`, because it adds rows) stores batches of 50 events, the SDK's default batch size, into the seeded table, one transaction per batch as the API does. It uses two strategies, alternating batch by batch so both meet the same table and cache:
- **row by row**: one `INSERT ... ON CONFLICT DO NOTHING RETURNING` per event, the obvious code;
- **batch**: the API's own statement (`INSERT_EVENTS` in `server/src/abtest/db/events.py`): `INSERT ... SELECT FROM unnest(...)`, one per batch.

| Table | Strategy | Events/s | Batch p50 | Batch p95 | Batch p99 |
|---|---|---:|---:|---:|---:|
| 1M events | row by row | 5,136 | 9.28 ms | 10.77 ms | 13.70 ms |
| 1M events | batch (`unnest`) | 22,435 | 2.15 ms | 2.61 ms | 3.60 ms |
| 10M events | row by row | 3,750 | 11.67 ms | 19.01 ms | 46.26 ms |
| 10M events | batch (`unnest`) | 12,645 | 3.51 ms | 6.33 ms | 8.77 ms |

The batch is 3.4 to 4.4 times faster. Row by row makes 50 round trips to the database per batch where the batch makes one. At the median round trip the script measured (0.222 ms in the 10M run, 0.225 ms in the 1M run), 50 trips come to about 11 ms, the same size as row by row's median batch (9.3 to 11.7 ms). psycopg prepares a statement after its fifth execution, so row by row isn't paying for planning; it pays for the trips and for running 50 statements. Both strategies return the same thing, the keys of the new rows, and a test (`server/tests/loadtest/test_insert_benchmark.py`) checks that they store and skip exactly the same events, so the comparison is fair. Both strategies are slower on the 10M-row table, likely because the indexes they write to no longer fit in Postgres's 128 MB cache (not measured separately).

## 6. Load test: the ingestion API under Locust

`make loadtest` starts a second API container on `abtest_perf`, with the production command (no `--reload`) and the rate limit raised so that it measures ingestion, not the limiter. It then runs Locust (`loadtest/locustfile.py`) from the Mac for 60 s after all senders have started (`--reset-stats`). Each simulated SDK sends batches of 50 events to `POST /v1/events` (2 exposures to the large experiment and 48 other events, all from new random users) and polls `GET /v1/config` with its last ETag (a 304 when nothing changed): 6 batches per poll, the ratio of the SDK's defaults (a flush every 5 s, a poll every 30 s). Senders don't wait between requests, so each row is the server's capacity at that many concurrent senders. A batch counts as an error unless all 50 events are stored.

These runs came after `make perf` on the 10M seed and a 15-second trial run. The table grew from 10,316,800 to 14,454,750 events during them.

| Senders | API processes | Events/s | POST p50 / p95 / p99 (ms) | GET p50 / p95 / p99 (ms) | Errors |
|---:|---:|---:|---:|---:|---:|
| 1 | 1 | 8,515 | 4 / 8 / 20 | 1 / 2 / 4 | 0 of 11,886 |
| 8 | 1 | 17,198 | 18 / 34 / 82 | 12 / 22 / 54 | 0 of 23,974 |
| 32 | 1 | 15,948 | 72 / 170 / 310 | 66 / 150 / 260 | 0 of 22,296 |
| 32 | 4 | 15,591 | 75 / 210 / 400 | 14 / 110 / 240 | 0 of 21,904 |
| 64 | 4 | 11,861 | 200 / 460 / 900 | 120 / 340 / 810 | 0 of 16,629 |

(`make loadtest LOADTEST_USERS=<senders> LOADTEST_WORKERS=<processes>`. Locust reports these percentiles rounded, to 10 ms above 100 ms and to 100 ms above 1 s.)

**Where the ceiling is.**
- One sender is limited by latency: a batch of 50 takes 4 ms end to end.
- Throughput peaks at about 17,000 events/s with 8 senders. `docker stats`, sampled 45 s into that run, showed the API process at 94% of one CPU and Postgres at 112%.
- More API processes don't raise the ceiling, because the VM's 4 CPUs are what runs out. During a 32-sender, 4-process run, the VM's CPU counters (`colima ssh -- head -1 /proc/stat`, read twice, 5 s apart) showed 74% busy and 12% waiting on I/O, with a quarter of all time spent in the kernel. The API processes, Postgres, and the network path from the Mac into the VM all share those CPUs, so a 64-sender run only adds queueing: less throughput and a 900 ms p99.
- Postgres wasn't waiting on row or transaction locks. Sampling `pg_stat_activity` every 2 s during a 32-sender run showed at most 4 active queries (the API's pool size), each on CPU, reading or writing data files, or writing WAL (`LWLock:WALWrite`, `IO:WALSync`). No sample showed a heavyweight lock wait.
- Runs vary: the 32-sender, 1-process configuration gave 15,948 events/s in this sequence, and 14,691 in a later 40 s run made while sampling wait events.

**Compared with the rate limit.** The default limit is 100 requests/s per client key (PRD §11), which is 5,000 events/s at 50 per batch: below what this setup takes. In the default configuration, one project's ingestion is capped by the limiter first, which is its job. Raising `RATE_LIMIT_PER_SECOND` (now passed through by docker-compose) lets a project use more.

## 7. What these numbers don't show

- **Warm caches only.** Every timing is taken after a warm-up run. Cold-cache times (after a restart, with the OS cache dropped) weren't measured; the page counts in section 3 show how much a cold run would have to read.
- **One laptop.** Postgres and the API share a 4-vCPU VM, and the load generator runs on the same Mac, reaching the VM through port forwarding. A server with its own database machine would give different numbers, not necessarily in the same proportions.
- **Default Postgres settings.** Nothing was tuned: `shared_buffers` 128 MB and `work_mem` 4 MB. Both affect section 3 and section 5.
- **A simulated workload.** Uniform users, a fixed event mix, and two experiments. Real traffic has heavy users, bursts, and many experiments at once.
- **Variance.** Where the same command was run more than once, both results are shown. The largest gap was a look at the large experiment: 873 ms in one run, 623 ms in the next (section 4).
