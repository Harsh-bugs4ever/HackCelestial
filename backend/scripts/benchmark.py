"""Read-only endpoint computation benchmark (no HTTP/network overhead).
Run from backend: python -m scripts.benchmark
"""
import json
import statistics
import time
from sqlalchemy import event
from app.api import routes
from app.core.db import SessionLocal, engine


def main():
    counter = [0]
    def query(*args): counter[0] += 1
    event.listen(engine, 'before_cursor_execute', query)
    endpoints = {
        'dashboard': lambda db: routes.dashboard(db),
        'actions': lambda db: routes.list_actions('pending', None, 50, db),
        'roster': lambda db: routes.roster(7, db),
        'status': routes.engine_status,
        'simulate': lambda db: routes.simulate(routes.SimulationRequest(), db),
    }
    try:
        for name, request in endpoints.items():
            timings, queries = [], []
            for _ in range(11):
                counter[0] = 0
                started = time.perf_counter()
                with SessionLocal() as db: request(db)
                timings.append((time.perf_counter()-started)*1000)
                queries.append(counter[0])
            print(json.dumps({'endpoint':name, 'first_ms':round(timings[0],2),
                  'warm_median_ms':round(statistics.median(timings[1:]),2),
                  'warm_max_ms':round(max(timings[1:]),2), 'warm_queries':queries[-1]}), flush=True)
    finally:
        event.remove(engine, 'before_cursor_execute', query)


if __name__ == '__main__': main()
