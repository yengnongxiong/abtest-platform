# loadtest

The performance measurements behind [docs/performance.md](../docs/performance.md). They run against a separate database, `abtest_perf`, so your dev data is never touched: `make seed` fills it with simulated events, `make perf` runs the query plans and insert benchmarks, and `make loadtest` runs Locust against the ingestion API.

Read first:
- [explain_attribution.py](explain_attribution.py): `EXPLAIN (ANALYZE, BUFFERS)` of the attribution query, with and without its indexes
- [locustfile.py](locustfile.py): the load test, sending what SDKs send
- [workload.py](workload.py): the simulated event mix shared by the seed and the load test
