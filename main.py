import bisect
import json
import math
import os
import random
from datetime import datetime, timezone


IN = os.environ.get("MAKEFUN_INPUT_DIR") or "/input"
OUT = os.environ.get("MAKEFUN_OUTPUT_DIR") or "/output"
PATHS = 4000
PLACES = 5
MICRO = 1_000_000
BASE58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def hour(value):
    if isinstance(value, (int, float)) and math.isfinite(value):
        return float(value)
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    moment = datetime.fromisoformat(text)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.timestamp() / 3600.0


def cap_of(row):
    lamports = int(str(row["market_cap_lamports"]).strip())
    if lamports <= 0:
        raise ValueError("non-positive market cap")
    return math.log10(lamports) - 9.0


def edges_of(task):
    try:
        raw = task["size"]["edges"]
        if not isinstance(raw, list) or len(raw) != 81:
            raise ValueError("bad edges")
        edges = [float(x) for x in raw]
        if any(not math.isfinite(x) for x in edges):
            raise ValueError("bad edge")
        if any(a >= b for a, b in zip(edges, edges[1:])):
            raise ValueError("unsorted edges")
        return edges
    except Exception:
        return [2.0 + 0.05 * i for i in range(81)]


def load_task():
    with open(os.path.join(IN, "task.json"), encoding="utf-8") as handle:
        task = json.load(handle)
    if not isinstance(task, dict):
        raise ValueError("task is not an object")
    return task


def load_marks():
    marks = []
    with open(os.path.join(IN, "history.ndjson"), encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                mark = json.loads(line)
                if not isinstance(mark, dict) or mark.get("ok") is False:
                    continue
                rows = mark.get("rows")
                if not isinstance(rows, list):
                    continue
                at = hour(mark["at"])
                marks.append((at, mark))
            except Exception:
                continue
    if not marks:
        raise ValueError("no usable marks")
    marks.sort(key=lambda item: item[0])
    return marks


def normalise(items, cap):
    """Round to 6 places, keep p >= .002, and keep the total at or below cap."""
    if not items:
        return []
    total = math.fsum(p for _, p in items)
    if not math.isfinite(total) or total <= 0.0:
        return []
    scale = cap / total if total > cap else 1.0
    kept = []
    for key, p in items:
        micro = int(round(p * scale * MICRO))
        if micro >= 2000:
            kept.append([key, micro])
    if not kept:
        return []
    limit = int(round(cap * MICRO))
    excess = sum(micro for _, micro in kept) - limit
    if excess > 0:
        index = max(range(len(kept)), key=lambda i: kept[i][1])
        kept[index][1] -= excess
        kept = [item for item in kept if item[1] >= 2000]
    return [(key, micro / MICRO) for key, micro in kept]


def placeholders(count=5):
    return ["1" * 43 + BASE58[i % len(BASE58)] for i in range(count)]


def fill_top(selected, known):
    result = []
    seen = set()
    for mint in list(selected) + list(known) + placeholders(5):
        if isinstance(mint, str) and mint and mint not in seen:
            seen.add(mint)
            result.append(mint)
            if len(result) == 5:
                break
    return result


def compute(task, marks):
    edges = edges_of(task)
    reads = task.get("reads")
    reads = reads if isinstance(reads, list) else []
    last_hour = marks[-1][0]
    ahead = []
    for read in reads:
        try:
            delta = hour(read) - last_hour
            if delta > 0.0 and math.isfinite(delta):
                ahead.append(delta)
        except Exception:
            pass
    if not ahead:
        ahead = [1.0] * (len(reads) or 7)

    series = {}
    for at, mark in marks:
        for row in mark["rows"]:
            try:
                mint = row["mint"]
                if not isinstance(mint, str) or not mint:
                    continue
                series.setdefault(mint, []).append((at, cap_of(row)))
            except Exception:
                continue
    if not series:
        raise ValueError("no priced coins")

    coins = []
    residuals = []
    for mint, observations in series.items():
        observations.sort(key=lambda item: item[0])
        first_t, first_x = observations[0]
        last_t, last_x = observations[-1]
        dt = last_t - first_t
        if len(observations) >= 2 and dt > 0.0:
            mu = min(0.35, max(-0.25, (last_x - first_x) / dt))
        else:
            mu = 0.03
        if len(observations) >= 2:
            for (t1, x1), (t2, x2) in zip(observations, observations[1:]):
                step = t2 - t1
                if step > 0.0:
                    residuals.append(((x2 - x1) - mu * step) ** 2 / step)
        coins.append((mint, last_x, mu))

    # calibrasi: pakai median drift lintas coin (drift per-coin terlalu noise di 6 snapshot)
    if coins:
        med_mu = sorted(c[2] for c in coins)[len(coins) // 2]
        coins = [(m, x, med_mu) for (m, x, _mu) in coins]

    if residuals:
        sigma = math.sqrt(math.fsum(residuals) / len(residuals))
        sigma = min(0.60, max(0.02, sigma))
    else:
        sigma = 0.12

    rng = random.Random(12345)
    place_counts = [dict() for _ in range(PLACES)]
    place_bins = [dict() for _ in range(PLACES)]
    samples = 0
    for _ in range(PATHS):
        for hours in ahead:
            root_hours = math.sqrt(hours)
            simulated = []
            for mint, start, mu in coins:
                x = start + mu * hours + sigma * root_hours * rng.gauss(0.0, 1.0)
                simulated.append((mint, min(7.0, max(1.0, x))))
            simulated.sort(key=lambda item: (-item[1], item[0]))
            for place, (mint, x) in enumerate(simulated[:PLACES]):
                counts = place_counts[place]
                counts[mint] = counts.get(mint, 0) + 1
                bin_number = bisect.bisect_right(edges, x)
                bins = place_bins[place]
                bins[bin_number] = bins.get(bin_number, 0) + 1
            samples += 1

    probabilities = []
    for place in range(PLACES):
        raw = [(mint, count / samples) for mint, count in place_counts[place].items()]
        raw = [item for item in raw if item[1] >= 0.002]
        raw.sort(key=lambda item: (-item[1], item[0]))
        for mint, p in normalise(raw[:60], 0.75):
            probabilities.append({"mint": mint, "place": place + 1, "p": p})

    sizes = []
    for place in range(PLACES):
        raw = [(bin_number, count / samples) for bin_number, count in place_bins[place].items()]
        raw = [item for item in raw if item[1] >= 0.002]
        raw.sort(key=lambda item: (-item[1], item[0]))
        for bin_number, p in sorted(normalise(raw[:60], 0.97)):
            sizes.append({"place": place + 1, "bin": bin_number, "p": p})

    totals = {}
    best = {}
    for row in probabilities:
        mint, place, p = row["mint"], row["place"], row["p"]
        totals[mint] = totals.get(mint, 0.0) + p
        old = best.get(mint)
        if old is None or place < old[0] or (place == old[0] and p > old[1]):
            best[mint] = (place, p)
    selected = sorted(totals, key=lambda mint: (-totals[mint], mint))[:5]
    selected.sort(key=lambda mint: (best[mint][0], -best[mint][1], -totals[mint], mint))
    known = [mint for mint, _, _ in sorted(coins, key=lambda item: (-item[1], item[0]))]
    top5 = fill_top(selected, known)
    return {"probabilities": probabilities, "sizes": sizes, "top5": top5}


def fallback(task, marks):
    edges = edges_of(task)
    chart = []
    known = []
    if marks:
        rows = [row for row in marks[-1][1].get("rows", []) if isinstance(row, dict)]
        try:
            rows.sort(key=lambda row: (int(row.get("rank", 10**9)), str(row.get("mint", ""))))
        except Exception:
            pass
        chart = rows
        for _, mark in reversed(marks):
            for row in mark.get("rows", []):
                mint = row.get("mint") if isinstance(row, dict) else None
                if isinstance(mint, str) and mint and mint not in known:
                    known.append(mint)

    chart_mints = []
    for row in chart:
        mint = row.get("mint")
        if isinstance(mint, str) and mint and mint not in chart_mints:
            chart_mints.append(mint)
    top5 = fill_top(chart_mints[:5], chart_mints + known)

    probabilities = []
    sizes = []
    for place in range(1, PLACES + 1):
        entries = {}
        if place <= len(chart):
            entries[chart[place - 1]["mint"]] = 0.60
        if place > 1 and place - 2 < len(chart):
            mint = chart[place - 2]["mint"]
            entries[mint] = entries.get(mint, 0.0) + 0.15
        if place < len(chart):
            mint = chart[place]["mint"]
            entries[mint] = entries.get(mint, 0.0) + 0.15
        rows = sorted(entries.items(), key=lambda item: (-item[1], item[0]))
        for mint, p in normalise(rows, 0.75):
            probabilities.append({"mint": mint, "place": place, "p": p})
        if place <= len(chart):
            try:
                bin_number = bisect.bisect_right(edges, cap_of(chart[place - 1]))
                sizes.append({"place": place, "bin": bin_number, "p": 0.97})
            except Exception:
                pass
    return {"probabilities": probabilities, "sizes": sizes, "top5": top5}


def main():
    try:
        os.makedirs(OUT, exist_ok=True)
        task = {}
        marks = []
        try:
            task = load_task()
        except Exception:
            pass
        try:
            marks = load_marks()
        except Exception:
            pass
        try:
            forecast = compute(task, marks)
        except Exception:
            try:
                forecast = fallback(task, marks)
            except Exception:
                forecast = {
                    "probabilities": [],
                    "sizes": [],
                    "top5": fill_top([], []),
                }
        with open(os.path.join(OUT, "forecast.json"), "w", encoding="utf-8") as handle:
            json.dump(forecast, handle, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
    except Exception:
        try:
            os.makedirs(OUT, exist_ok=True)
            with open(os.path.join(OUT, "forecast.json"), "w", encoding="utf-8") as handle:
                json.dump({"probabilities": [], "sizes": [], "top5": fill_top([], [])}, handle, separators=(",", ":"))
        except Exception:
            pass


if __name__ == "__main__":
    main()
