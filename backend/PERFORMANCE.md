# Backend performance and reliability verification

Measured locally on the existing seeded SQLite dataset (62,149 bookings, 5,760 sensor readings, 584 reviews, 12 assets). Baseline and updated code used separate copies of the same database. Values are median endpoint computation time over 10 warm requests, excluding HTTP transport. These are local measurements, not production latency guarantees.

| Endpoint | Baseline | Updated |
| --- | ---: | ---: |
| Dashboard | 27.96 ms | 13.00 ms |
| Action queue | 0.97 ms | 0.87 ms |
| Roster | 13.85 ms | 11.14 ms |
| Engine status | 1.94 ms | 0.69 ms |
| Simulator | 2.71 ms | 2.31 ms |

Engine status now uses two queries instead of eight. The dashboard aggregates request/action counts in SQL and loads only its five highest-ranked recommendations. Its query count increases from 12 to 13, but it no longer hydrates every pending action and open request. Queue ranking happens before the limit, so older critical recommendations remain visible. Review summaries select only needed columns, roster coverage uses SQL aggregation, and decision outcomes are restricted to the displayed decisions. Supporting indexes are installed on existing and new databases at startup.

Forecast and maintenance caches now share in-flight calculations across threads, expire after five minutes, have bounded entry counts, and are scoped to the database. Forecast results are copied for callers; cached assessments do not retain ORM instances. Cold fits still take seconds (about 8.3 seconds for the first dashboard calculation in this run). Startup warm-up remains asynchronous; a request that arrives before warm-up or after cache expiry can wait for a fit. These are per-process caches, not a shared Redis cache. Importers can explicitly call `clear_forecast_cache()` and `clear_assess_cache()` when refreshing source data.

The action watcher performs database work in a thread, closes its session before sending, sends to sockets concurrently with a two-second deadline, and reports removals as well as additions. Expired snoozes wake even without Celery. Changes are detected on its existing four-second polling interval. Model runs no longer insert an engine-run row before expensive computation, releasing SQLite's writer slot during read-only fitting. Overlapping runs of the same engine are rejected within one process; separate Celery/API processes still require deployment-level scheduling coordination.

Decisions use an atomic conditional update to prevent duplicate execution. Executor writes run inside a savepoint so failures retain the approval without committing partial artifacts. Terminal decisions cannot be overwritten; conflicting decisions return HTTP 409. Republishing preserves snooze audit records and deduplicates each proposal batch. Unassigned roster slots no longer count as staffed shifts.

The frontend no longer sends a JSON Content-Type on bodyless requests, avoiding unnecessary CORS preflights for normal GET requests. Failed or busy engine results now surface as errors instead of appearing to succeed.

Validation:

- 17 regression tests using disposable databases: concurrent cache misses, expiry/eviction/invalidation, database isolation, forecast copy protection, concurrent approval, failed-executor rollback, audit retention, ranking, independent writes during model work, duplicate runs, staffing gaps, invalid input, slow sockets, and snooze wake-up.
- Full seeded smoke workflow passed on a disposable database copy, including all four engines, all six executor types, feedback learning, SOP retrieval, and simulation. External LLM calls were disabled during validation.
- All 12 checked HTTP endpoints returned 200; the WebSocket returned its hello event. Twelve simultaneous warm dashboard HTTP requests completed with a 260 ms median and 284 ms maximum on this machine.

From `backend/`:

```powershell
..\.venv\Scripts\python.exe -m unittest discover -s tests -v
..\.venv\Scripts\python.exe -m scripts.benchmark
```

The benchmark is read-only. `python -m scripts.smoke` executes recommendations and changes its database: run it against a disposable copy via `DATABASE_URL`.
