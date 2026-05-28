"""Generate the deterministic Sobral delivery fixture.

Produces `fixtures/deliveries_2026-04-16.csv`:
  - 1,200 rows total
  - 8 trucks (id 1001..1008)
  - 2 deliberately malformed rows (bad lat/lon) — exact rows: 247 and 891
    (0-indexed in the CSV body, header excluded).

The seed is hard-coded so every fork and every CI run sees the SAME bytes.
Don't touch the constants below — `tests/test_evaluate.py` asserts:

    rows_total - rows_malformed == 1198

If you change the count, you must also change the rubric. We don't want that.

Run directly:

    python -m fixtures.generate_fixtures
"""

from __future__ import annotations

import csv
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

SEED = 42
TOTAL_ROWS = 1200
MALFORMED_INDICES = (247, 891)  # 0-indexed positions of the malformed rows
TRUCKS = [1001, 1002, 1003, 1004, 1005, 1006, 1007, 1008]
STATUSES = ["delivered", "delivered", "delivered", "delivered", "failed", "pending"]
BASE_DAY = datetime(2026, 4, 16, 5, 30, 0, tzinfo=timezone.utc)

FIXTURES_DIR = Path(__file__).resolve().parent
OUTPUT_CSV = FIXTURES_DIR / "deliveries_2026-04-16.csv"


def _row(rng: random.Random, idx: int) -> dict:
    truck_id = TRUCKS[idx % len(TRUCKS)]
    # Each delivery_id is globally unique. Format: D2026041600001..D2026041601200.
    delivery_id = f"D20260416{idx + 1:05d}"
    # Spread timestamps across the day so partitioning would be plausible.
    ts = BASE_DAY + timedelta(seconds=int(idx * 60 + rng.randint(0, 59)))
    lat = round(48.85 + rng.uniform(-0.25, 0.25), 6)
    lon = round(2.35 + rng.uniform(-0.30, 0.30), 6)
    status = rng.choice(STATUSES)
    weight_kg = round(rng.uniform(0.5, 35.0), 2)
    return {
        "truck_id": truck_id,
        "delivery_id": delivery_id,
        "ts": ts.isoformat().replace("+00:00", "Z"),
        "lat": lat,
        "lon": lon,
        "status": status,
        "weight_kg": weight_kg,
    }


def _corrupt(row: dict) -> dict:
    """Introduce a malformed lat/lon. The Lambda must quarantine, not crash."""
    bad = dict(row)
    # Two different failure modes so a learner can't pattern-match on a single
    # pattern: one row has a non-numeric latitude, the other has an
    # out-of-range longitude (>180).
    bad["lat"] = "NaN-broken"
    bad["lon"] = "999.999"
    return bad


def generate() -> Path:
    rng = random.Random(SEED)
    rows = [_row(rng, i) for i in range(TOTAL_ROWS)]
    for i in MALFORMED_INDICES:
        rows[i] = _corrupt(rows[i])

    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    with OUTPUT_CSV.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["truck_id", "delivery_id", "ts", "lat", "lon", "status", "weight_kg"],
        )
        writer.writeheader()
        writer.writerows(rows)
    return OUTPUT_CSV


if __name__ == "__main__":
    out = generate()
    print(f"Wrote fixture: {out} ({TOTAL_ROWS} rows, {len(MALFORMED_INDICES)} malformed)")
