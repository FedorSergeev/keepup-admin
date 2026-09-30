"""Drive the load stand and report what it did (keepup-53).

Every virtual user signs in once, on one replica, and then loops over a mix of
what the panel does all day: who am I, my sections, the event log, the health
check, and now and then a write to the event log. Once a second the pools of
every replica are sampled. What comes out: per operation the count, errors,
throughput and latency percentiles, and the peaks of both pools.

    python tests/load/drive.py --replicas http://localhost:18001,http://localhost:18002 \\
        --users 40 --duration 60 --out result.json
"""

import argparse
import asyncio
import json
import random
import statistics
import time
from collections import defaultdict

import httpx

#: The mix, by weight: what a panel session asks for, roughly in proportion.
MIX = (
    ("me", 40, "GET", "/api/auth/me", None),
    ("sections", 25, "GET", "/api/modules", None),
    ("events", 15, "GET", "/api/events?page_size=20", None),
    ("health", 10, "GET", "/api/health", None),
    ("write_event", 10, "POST", "/api/events",
     {"event_type": "load_note", "event_text": "load stand"}),
)
ADMIN = "admin"


def percentile(values, share):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(share * (len(ordered) - 1))))]


async def sign_in(client, password):
    answer = await client.post("/api/auth/login",
                               json={"username": ADMIN, "password": password})
    answer.raise_for_status()
    return answer.json()["access_token"]


async def virtual_user(base, password, stop_at, measure_from, results, started):
    names, weights = [m[0] for m in MIX], [m[1] for m in MIX]
    by_name = {m[0]: m for m in MIX}
    async with httpx.AsyncClient(base_url=base, timeout=30) as client:
        token = await sign_in(client, password)
        started.append(True)
        headers = {"Authorization": f"Bearer {token}"}
        while time.monotonic() < stop_at:
            name = random.choices(names, weights)[0]
            _, _, method, path, body = by_name[name]
            began = time.monotonic()
            try:
                answer = await client.request(method, path, headers=headers, json=body)
                ok = answer.status_code < 400
            except httpx.HTTPError:
                ok = False
            took = time.monotonic() - began
            if began >= measure_from:
                results[name]["latencies"].append(took)
                results[name]["errors"] += 0 if ok else 1


async def sample_pools(replicas, password, stop_at, peaks):
    clients = [httpx.AsyncClient(base_url=base, timeout=10) for base in replicas]
    try:
        tokens = [await sign_in(client, password) for client in clients]
        while time.monotonic() < stop_at:
            for client, token in zip(clients, tokens):
                try:
                    answer = await client.get("/loadtest/pools",
                                              headers={"Authorization": f"Bearer {token}"})
                    pools = answer.json()
                except (httpx.HTTPError, ValueError):
                    continue
                peak = peaks[str(client.base_url)]
                peak["db_checked_out"] = max(peak["db_checked_out"], pools["database"]["checked_out"])
                peak["db_overflow"] = max(peak["db_overflow"], pools["database"]["overflow"])
                peak["db_capacity"] = pools["database"]["size"]
                peak["threads_borrowed"] = max(peak["threads_borrowed"], pools["threads"]["borrowed"])
                peak["threads_waiting"] = max(peak["threads_waiting"], pools["threads"]["waiting"])
                peak["threads_total"] = pools["threads"]["total"]
            await asyncio.sleep(1)
    finally:
        for client in clients:
            await client.aclose()


async def run(replicas, users, duration, warmup, password):
    results = defaultdict(lambda: {"latencies": [], "errors": 0})
    peaks = defaultdict(lambda: {"db_checked_out": 0, "db_overflow": 0, "db_capacity": 0,
                                 "threads_borrowed": 0, "threads_waiting": 0, "threads_total": 0})
    started = []
    begin = time.monotonic()
    measure_from = begin + warmup
    stop_at = measure_from + duration
    tasks = [virtual_user(replicas[i % len(replicas)], password, stop_at, measure_from,
                          results, started) for i in range(users)]
    tasks.append(sample_pools(replicas, password, stop_at, peaks))
    outcomes = await asyncio.gather(*tasks, return_exceptions=True)
    failures = [repr(o) for o in outcomes if isinstance(o, Exception)]
    return summarise(results, peaks, duration, users, len(replicas), failures)


def summarise(results, peaks, duration, users, replicas, failures):
    operations = {}
    total = 0
    for name, data in sorted(results.items()):
        latencies = data["latencies"]
        total += len(latencies)
        operations[name] = {
            "count": len(latencies),
            "errors": data["errors"],
            "per_second": round(len(latencies) / duration, 1),
            "p50_ms": round(1000 * percentile(latencies, 0.50), 1) if latencies else None,
            "p95_ms": round(1000 * percentile(latencies, 0.95), 1) if latencies else None,
            "p99_ms": round(1000 * percentile(latencies, 0.99), 1) if latencies else None,
            "mean_ms": round(1000 * statistics.fmean(latencies), 1) if latencies else None,
        }
    return {"users": users, "replicas": replicas, "duration_s": duration,
            "per_second": round(total / duration, 1), "operations": operations,
            "pools": dict(peaks), "failed_users": failures[:5],
            "failed_user_count": len(failures)}


def print_report(report):
    print(f"\n{report['users']} users on {report['replicas']} replicas, "
          f"{report['duration_s']} s measured: {report['per_second']} requests/s")
    print(f"{'operation':<12}{'count':>8}{'err':>6}{'rps':>8}{'p50':>9}{'p95':>9}{'p99':>9}")
    for name, op in report["operations"].items():
        print(f"{name:<12}{op['count']:>8}{op['errors']:>6}{op['per_second']:>8}"
              f"{op['p50_ms']:>9}{op['p95_ms']:>9}{op['p99_ms']:>9}")
    for replica, peak in report["pools"].items():
        print(f"{replica}: db checked out {peak['db_checked_out']}/{peak['db_capacity']} "
              f"(+{peak['db_overflow']} overflow), threads {peak['threads_borrowed']}/"
              f"{peak['threads_total']}, waiting {peak['threads_waiting']}")
    if report["failed_user_count"]:
        print(f"{report['failed_user_count']} users failed: {report['failed_users']}")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--replicas", required=True, help="comma-separated base addresses")
    parser.add_argument("--users", type=int, default=40)
    parser.add_argument("--duration", type=int, default=60)
    parser.add_argument("--warmup", type=int, default=10)
    parser.add_argument("--password", default="load-stand-admin-password")
    parser.add_argument("--out", help="where to write the report as JSON")
    args = parser.parse_args()
    report = asyncio.run(run(args.replicas.split(","), args.users, args.duration,
                             args.warmup, args.password))
    print_report(report)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as out:
            json.dump(report, out, indent=2)


if __name__ == "__main__":
    main()
