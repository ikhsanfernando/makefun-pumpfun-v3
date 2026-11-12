import bisect
import json
import math
import os
import random
from datetime import datetime, timezone


INPUT_ROOT = os.environ.get("MAKEFUN_INPUT_DIR") or "/input"
OUTPUT_ROOT = os.environ.get("MAKEFUN_OUTPUT_DIR") or "/output"
TRIALS = 4000
PODIUM = 5
TICKS_PER_UNIT = 1_000_000
ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def read_json_task():
    location = os.path.join(INPUT_ROOT, "task.json")
    with open(location, encoding="utf-8") as source:
        payload = json.load(source)
    if not isinstance(payload, dict):
        raise TypeError("task payload must be a mapping")
    return payload


def report_edges(task_data):
    try:
        supplied = task_data["size"]["edges"]
        if not isinstance(supplied, list) or len(supplied) != 81:
            raise ValueError("the report needs exactly 81 edges")
        parsed = [float(point) for point in supplied]
        if any(not math.isfinite(point) for point in parsed):
            raise ValueError("report edges must be finite")
        if any(left >= right for left, right in zip(parsed, parsed[1:])):
            raise ValueError("report edges must increase")
        return parsed
    except Exception:
        return [2 + 0.05 * position for position in range(81)]


def timestamp_in_hours(value):
    numeric = isinstance(value, (int, float)) and math.isfinite(value)
    if numeric:
        return float(value)
    stamp = str(value).strip()
    if stamp.endswith("Z"):
        stamp = stamp[:-1] + "+00:00"
    moment = datetime.fromisoformat(stamp)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.timestamp() / 3600.0


def read_snapshots():
    collected = []
    location = os.path.join(INPUT_ROOT, "history.ndjson")
    with open(location, encoding="utf-8") as source:
        for raw_line in source:
            line = raw_line.strip()
            if not line:
                continue
            try:
                snapshot = json.loads(line)
                if not isinstance(snapshot, dict):
                    continue
                if snapshot.get("ok") is False:
                    continue
                if not isinstance(snapshot.get("rows"), list):
                    continue
                collected.append((timestamp_in_hours(snapshot["at"]), snapshot))
            except Exception:
                continue
    if not collected:
        raise ValueError("history has no usable snapshots")
    collected.sort(key=lambda pair: pair[0])
    return collected


def market_cap_dex(record):
    lamports = int(str(record["market_cap_lamports"]).strip())
    if lamports <= 0:
        raise ValueError("market cap must be positive")
    return math.log10(lamports) - 9.0


def quantize_distribution(entries, ceiling):
    if not entries:
        return []
    mass = math.fsum(prob for _, prob in entries)
    if not math.isfinite(mass) or mass <= 0.0:
        return []
    factor = ceiling / mass if mass > ceiling else 1.0
    rounded = []
    for label, mass_fraction in entries:
        ticks = int(round(mass_fraction * factor * TICKS_PER_UNIT))
        if ticks >= 2000:
            rounded.append([label, ticks])
    if not rounded:
        return []
    allowance = int(round(ceiling * TICKS_PER_UNIT))
    extra = sum(ticks for _, ticks in rounded) - allowance
    if extra > 0:
        biggest = max(range(len(rounded)), key=lambda pos: rounded[pos][1])
        rounded[biggest][1] -= extra
        rounded = [pair for pair in rounded if pair[1] >= 2000]
    return [(label, ticks / TICKS_PER_UNIT) for label, ticks in rounded]


def generated_mints(amount=5):
    return [
        "1" * 43 + ALPHABET[offset % len(ALPHABET)]
        for offset in range(amount)
    ]


def pad_top_five(preferred, alternatives):
    result = []
    seen = set()
    pool = [*preferred, *alternatives, *generated_mints(5)]
    for item in pool:
        if isinstance(item, str) and item and item not in seen:
            seen.add(item)
            result.append(item)
            if len(result) == 5:
                break
    return result


def conservative_forecast(task_data, snapshots):
    bounds = report_edges(task_data)
    latest_rows = []
    seen_mints = []
    if snapshots:
        latest_rows = [
            row
            for row in snapshots[-1][1].get("rows", [])
            if isinstance(row, dict)
        ]
        try:
            latest_rows.sort(key=lambda row: (int(row.get("rank", 10**9)), str(row.get("mint", ""))))
        except Exception:
            pass
        for _, snapshot in reversed(snapshots):
            for row in snapshot.get("rows", []):
                mint = row.get("mint") if isinstance(row, dict) else None
                if isinstance(mint, str) and mint and mint not in seen_mints:
                    seen_mints.append(mint)

    ordered_mints = []
    for row in latest_rows:
        mint = row.get("mint")
        if isinstance(mint, str) and mint and mint not in ordered_mints:
            ordered_mints.append(mint)
    top_five = pad_top_five(
        ordered_mints[:5],
        ordered_mints + seen_mints,
    )

    probabilities = []
    sizes = []
    for place in range(1, PODIUM + 1):
        candidates = {}
        if place <= len(latest_rows):
            candidates[latest_rows[place - 1]["mint"]] = 0.60
        if place > 1 and place - 2 < len(latest_rows):
            mint = latest_rows[place - 2]["mint"]
            candidates[mint] = candidates.get(mint, 0.0) + 0.15
        if place < len(latest_rows):
            mint = latest_rows[place]["mint"]
            candidates[mint] = candidates.get(mint, 0.0) + 0.15
        ranked = sorted(candidates.items(), key=lambda pair: (-pair[1], pair[0]))
        for mint, chance in quantize_distribution(ranked, 0.68):
            probabilities.append({"mint": mint, "place": place, "p": chance})
        if place <= len(latest_rows):
            try:
                bucket = bisect.bisect_right(
                    bounds,
                    market_cap_dex(latest_rows[place - 1]),
                )
                sizes.append({"place": place, "bin": bucket, "p": 0.97})
            except Exception:
                pass
    return {"probabilities": probabilities, "sizes": sizes, "top5": top_five}


def simulate_forecast(task_data, snapshots):
    bounds = report_edges(task_data)
    requested = task_data.get("reads")
    if not isinstance(requested, list):
        requested = []
    cutoff = snapshots[-1][0]
    horizons = []
    for read_at in requested:
        try:
            distance = timestamp_in_hours(read_at) - cutoff
            if distance > 0.0 and math.isfinite(distance):
                horizons.append(distance)
        except Exception:
            pass
    if len(horizons) == 0:
        horizons = [1.0] * (len(requested) or 7)

    observations = {}
    for moment, snapshot in snapshots:
        for row in snapshot["rows"]:
            try:
                mint = row["mint"]
                if not isinstance(mint, str) or not mint:
                    continue
                level = market_cap_dex(row)
                observations.setdefault(mint, []).append((moment, level))
            except Exception:
                continue
    if not observations:
        raise ValueError("no token has a usable market cap")

    assets = []
    step_variances = []
    for mint, history in observations.items():
        history.sort(key=lambda point: point[0])
        start_time, start_level = history[0]
        end_time, end_level = history[-1]
        elapsed = end_time - start_time
        if len(history) >= 2 and elapsed > 0.0:
            individual_drift = min(
                0.35,
                max(-0.25, (end_level - start_level) / elapsed),
            )
        else:
            individual_drift = 0.03
        if len(history) >= 2:
            for (time_a, level_a), (time_b, level_b) in zip(history, history[1:]):
                interval = time_b - time_a
                if interval > 0.0:
                    error = level_b - level_a - individual_drift * interval
                    step_variances.append(error**2 / interval)
        assets.append((mint, end_level, individual_drift))

    ordered_drifts = sorted(asset[2] for asset in assets)
    median_drift = ordered_drifts[len(assets) // 2]
    # age hazard: coin yang sudah tua kehilangan tenaga (log10 per jam)
    cutoff_hours = timestamp_in_hours(snapshots[-1][0])
    ages = {}
    for row in snapshots[-1][1]["rows"]:
        stamp = row.get("created_at")
        ages[row["mint"]] = cutoff_hours - timestamp_in_hours(stamp) if stamp else 0.0
    assets = [
        (mint, level, median_drift - 0.004 * min(6.0, max(0.0, ages.get(mint, 0.0) - 18.0) / 12.0))
        for mint, level, _ in assets
    ]

    if step_variances:
        pooled_sigma = math.sqrt(
            math.fsum(step_variances) / len(step_variances)
        )
        pooled_sigma = min(0.60, max(0.02, pooled_sigma))
    else:
        pooled_sigma = 0.12

    generator = random.Random(12345)
    ranks = {}
    for row in snapshots[-1][1]["rows"]:
        ranks[row["mint"]] = int(row["rank"])
    per_token_sigma = {}
    for mint, _, _ in assets:
        multiplier = 1.8 if ranks.get(mint, 10**9) > 15 else 1.3
        per_token_sigma[mint] = pooled_sigma * multiplier

    place_counts = [dict() for _ in range(PODIUM)]
    place_buckets = [dict() for _ in range(PODIUM)]
    draws = 0
    for _ in range(TRIALS):
        for horizon in horizons:
            time_scale = math.sqrt(horizon)
            projected = []
            for mint, current_level, shared_drift in assets:
                capped_sigma = min(
                    0.44,
                    per_token_sigma[mint] * time_scale,
                )
                shock = generator.gauss(0.0, 1.0)
                next_level = (
                    current_level
                    + shared_drift * horizon
                    + capped_sigma * shock
                )
                projected.append(
                    (mint, min(7.0, max(1.0, next_level)))
                )
            projected.sort(key=lambda pair: (-pair[1], pair[0]))
            for place, (mint, level) in enumerate(projected[:PODIUM]):
                token_counts = place_counts[place]
                token_counts[mint] = token_counts.get(mint, 0) + 1
                bucket = bisect.bisect_right(bounds, level)
                bucket_counts = place_buckets[place]
                bucket_counts[bucket] = bucket_counts.get(bucket, 0) + 1
            draws += 1

    probabilities = []
    for place in range(PODIUM):
        ranked = [
            (mint, count / draws)
            for mint, count in place_counts[place].items()
        ]
        ranked = [pair for pair in ranked if pair[1] >= 0.002]
        ranked.sort(key=lambda pair: (-pair[1], pair[0]))
        for mint, chance in quantize_distribution(ranked[:60], 0.68):
            probabilities.append(
                {"mint": mint, "place": place + 1, "p": chance}
            )

    sizes = []
    for place in range(PODIUM):
        ranked = [
            (bucket, count / draws)
            for bucket, count in place_buckets[place].items()
        ]
        ranked = [pair for pair in ranked if pair[1] >= 0.002]
        ranked.sort(key=lambda pair: (-pair[1], pair[0]))
        for bucket, chance in sorted(
            quantize_distribution(ranked[:60], 0.97)
        ):
            sizes.append(
                {"place": place + 1, "bin": bucket, "p": chance}
            )

    totals = {}
    best_place = {}
    for entry in probabilities:
        mint = entry["mint"]
        place = entry["place"]
        chance = entry["p"]
        totals[mint] = totals.get(mint, 0.0) + chance
        previous = best_place.get(mint)
        if (
            previous is None
            or place < previous[0]
            or (place == previous[0] and chance > previous[1])
        ):
            best_place[mint] = (place, chance)
    selected = sorted(
        totals,
        key=lambda mint: (-totals[mint], mint),
    )[:5]
    selected.sort(
        key=lambda mint: (
            best_place[mint][0],
            -best_place[mint][1],
            -totals[mint],
            mint,
        )
    )
    by_price = sorted(
        assets,
        key=lambda asset: (-asset[1], asset[0]),
    )
    known = [mint for mint, _, _ in by_price]
    top_five = pad_top_five(selected, known)
    return {"probabilities": probabilities, "sizes": sizes, "top5": top_five}


def main():
    try:
        os.makedirs(OUTPUT_ROOT, exist_ok=True)
        task_data = {}
        snapshots = []
        try:
            task_data = read_json_task()
        except Exception:
            pass
        try:
            snapshots = read_snapshots()
        except Exception:
            pass
        try:
            forecast = simulate_forecast(task_data, snapshots)
        except Exception:
            try:
                forecast = conservative_forecast(task_data, snapshots)
            except Exception:
                forecast = {
                    "probabilities": [],
                    "sizes": [],
                    "top5": pad_top_five([], []),
                }
        destination = os.path.join(OUTPUT_ROOT, "forecast.json")
        with open(destination, "w", encoding="utf-8") as target:
            json.dump(
                forecast,
                target,
                ensure_ascii=True,
                allow_nan=False,
                separators=(",", ":"),
            )
    except Exception:
        try:
            os.makedirs(OUTPUT_ROOT, exist_ok=True)
            destination = os.path.join(OUTPUT_ROOT, "forecast.json")
            with open(destination, "w", encoding="utf-8") as target:
                json.dump(
                    {
                        "probabilities": [],
                        "sizes": [],
                        "top5": pad_top_five([], []),
                    },
                    target,
                    separators=(",", ":"),
                )
        except Exception:
            pass


if __name__ == "__main__":
    main()
