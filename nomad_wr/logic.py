"""Pure computation over the loaded frames: PRs, tiers, leaderboards, formatting.

Nothing here touches the filesystem, which keeps the rules that matter (what
counts as a PR, which direction is "better") in one testable place.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Iterable

import pandas as pd

from .config import CATEGORY_ORDER, POSITIONS, TIERS

POSITION_SEP = " / "


# --------------------------------------------------------------------------
# Lookups
# --------------------------------------------------------------------------


def metric_map(metrics: pd.DataFrame) -> dict[str, dict[str, Any]]:
    return {r["metric_id"]: dict(r) for _, r in metrics.iterrows()}


def athlete_map(athletes: pd.DataFrame) -> dict[str, dict[str, Any]]:
    return {r["athlete_id"]: dict(r) for _, r in athletes.iterrows()}


def metric_label(metric: dict[str, Any] | None) -> str:
    if not metric:
        return "Unknown metric"
    unit = str(metric.get("unit") or "").strip()
    return f"{metric['name']} ({unit})" if unit else str(metric["name"])


def athlete_label(athlete: dict[str, Any] | None) -> str:
    if not athlete:
        return "Unknown athlete"
    bits = [str(athlete.get("position") or "").strip()]
    grad = athlete.get("grad_year")
    if grad is not None and not pd.isna(grad):
        bits.append(f"'{int(grad) % 100:02d}")
    tail = " ".join(b for b in bits if b)
    return f"{athlete['name']} · {tail}" if tail else str(athlete["name"])


def metric_choices(metrics: pd.DataFrame) -> dict[str, dict[str, str]]:
    """Nested {category: {metric_id: label}} so the select renders optgroups."""
    grouped: dict[str, dict[str, str]] = {}
    ordered = metrics.assign(
        _cat_rank=metrics["category"].map(lambda c: CATEGORY_ORDER.get(c, len(CATEGORY_ORDER)))
    ).sort_values(["_cat_rank", "category", "name"], kind="stable")
    for _, row in ordered.iterrows():
        grouped.setdefault(str(row["category"] or "Other"), {})[row["metric_id"]] = metric_label(dict(row))
    return grouped


def athlete_choices(athletes: pd.DataFrame, include_inactive: bool = False) -> dict[str, str]:
    df = athletes if include_inactive else athletes[athletes["active"]]
    return {r["athlete_id"]: athlete_label(dict(r)) for _, r in df.iterrows()}


# --------------------------------------------------------------------------
# Positions — stored as one free-text string, edited as a primary + a second
# --------------------------------------------------------------------------


def split_position(position: str) -> tuple[str, str]:
    """'RHP / 1B-OF' -> ('RHP', '1B-OF'). Splits once, so extra parts survive."""
    primary, _, secondary = str(position or "").partition("/")
    return primary.strip(), secondary.strip()


def join_position(primary: str, secondary: str) -> str:
    return POSITION_SEP.join(p for p in (str(primary).strip(), str(secondary).strip()) if p)


def position_choices(athletes: pd.DataFrame, extra: Iterable[str] = ()) -> dict[str, str]:
    """The standard list, plus whatever is already in use (and anything passed in).

    `extra` carries the values loaded into a form, so a position typed straight
    into athletes.csv survives an edit-and-save round trip instead of silently
    resetting to blank.
    """
    known: set[str] = set()
    for value in athletes["position"].dropna().tolist():
        primary, secondary = split_position(value)
        known.update(p for p in (primary, secondary) if p)
    known.update(p.strip() for p in extra if p and p.strip())
    ordered = POSITIONS + sorted(p for p in known if p not in POSITIONS)
    return {"": "—", **{p: p for p in ordered}}


# --------------------------------------------------------------------------
# Bests and PRs
# --------------------------------------------------------------------------


def is_better(new: float, old: float | None, higher_is_better: bool) -> bool:
    """Strictly better. A tie is not a PR — you have to beat the number."""
    if old is None or (isinstance(old, float) and math.isnan(old)):
        return False
    return new > old if higher_is_better else new < old


def best_value(values: pd.Series, higher_is_better: bool) -> float | None:
    clean = pd.to_numeric(values, errors="coerce").dropna()
    if clean.empty:
        return None
    return float(clean.max() if higher_is_better else clean.min())


def best_row(df: pd.DataFrame, higher_is_better: bool) -> pd.Series | None:
    """The row holding the best value; ties resolve to the earliest one set."""
    clean = df[pd.to_numeric(df["value"], errors="coerce").notna()]
    if clean.empty:
        return None
    ordered = clean.sort_values(
        ["value", "ts"], ascending=[not higher_is_better, True], na_position="last"
    )
    return ordered.iloc[0]


def mark_prs(df: pd.DataFrame, higher_is_better: bool) -> pd.DataFrame:
    """Add `is_pr` (beat every earlier entry) and `is_first` to a single-metric frame.

    Expects the frame sorted oldest first.
    """
    out = df.copy()
    running: float | None = None
    flags: list[bool] = []
    for value in out["value"]:
        if running is None:
            flags.append(False)
            running = float(value)
        else:
            better = is_better(float(value), running, higher_is_better)
            flags.append(better)
            if better:
                running = float(value)
    out["is_pr"] = flags
    out["is_first"] = [i == 0 for i in range(len(out))]
    return out


def athlete_metric_summary(
    entries: pd.DataFrame, metrics: pd.DataFrame, athlete_id: str
) -> pd.DataFrame:
    """One row per metric the athlete has logged, newest activity first."""
    mine = entries[entries["athlete_id"] == athlete_id]
    mmap = metric_map(metrics)
    rows = []
    for metric_id, group in mine.groupby("metric_id", sort=False):
        metric = mmap.get(metric_id)
        hib = bool(metric["higher_is_better"]) if metric else True
        group = group.sort_values("ts", na_position="first")
        best = best_row(group, hib)
        latest = group.iloc[-1]
        first = group.iloc[0]
        rows.append(
            {
                "metric_id": metric_id,
                "metric_name": metric["name"] if metric else f"[missing metric {metric_id}]",
                "category": metric["category"] if metric else "Other",
                "unit": metric["unit"] if metric else "",
                "higher_is_better": hib,
                "known_metric": metric is not None,
                "entries": len(group),
                "pr_value": None if best is None else float(best["value"]),
                "pr_ts": None if best is None else best["ts"],
                "latest_value": float(latest["value"]),
                "latest_ts": latest["ts"],
                "first_value": float(first["value"]),
                "change": float(latest["value"]) - float(first["value"]),
            }
        )
    if not rows:
        return pd.DataFrame(
            columns=[
                "metric_id", "metric_name", "category", "unit", "higher_is_better",
                "known_metric", "entries", "pr_value", "pr_ts", "latest_value",
                "latest_ts", "first_value", "change",
            ]
        )
    out = pd.DataFrame(rows)
    return out.sort_values("latest_ts", ascending=False, na_position="last").reset_index(drop=True)


def reference_for(
    entries: pd.DataFrame, athlete_id: str, metric_id: str, higher_is_better: bool
) -> dict[str, Any]:
    """Most recent value + current PR for the Quick Entry reference tiles."""
    mine = entries[(entries["athlete_id"] == athlete_id) & (entries["metric_id"] == metric_id)]
    if mine.empty:
        return {"count": 0, "last_value": None, "last_ts": None, "pr_value": None, "pr_ts": None}
    mine = mine.sort_values("ts", na_position="first")
    latest = mine.iloc[-1]
    best = best_row(mine, higher_is_better)
    return {
        "count": len(mine),
        "last_value": float(latest["value"]),
        "last_ts": latest["ts"],
        "pr_value": None if best is None else float(best["value"]),
        "pr_ts": None if best is None else best["ts"],
        "recent": mine.iloc[::-1].head(5),
    }


# --------------------------------------------------------------------------
# Benchmark tiers
# --------------------------------------------------------------------------


def benchmark_map(benchmarks: pd.DataFrame) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for _, row in benchmarks.iterrows():
        cuts = {k: float(row[k]) for k in ("elite", "advanced", "average") if not pd.isna(row[k])}
        if cuts:
            out[row["metric_id"]] = cuts
    return out


def tier_of(value: float | None, cutoffs: dict[str, float] | None, higher_is_better: bool) -> str | None:
    """Elite / Advanced / Average / Below Average, or None when no cutoffs exist."""
    if value is None or cutoffs is None or not cutoffs:
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    for tier, key in (("Elite", "elite"), ("Advanced", "advanced"), ("Average", "average")):
        cut = cutoffs.get(key)
        if cut is None or not math.isfinite(cut):
            continue
        if (value >= cut) if higher_is_better else (value <= cut):
            return tier
    return "Below Average"


def cutoffs_are_ordered(cutoffs: dict[str, float], higher_is_better: bool) -> bool:
    """Elite should be the hardest number to hit. Warn the coach when it isn't."""
    present = [cutoffs[k] for k in ("elite", "advanced", "average") if k in cutoffs]
    if len(present) < 2:
        return True
    return all(
        (a > b if higher_is_better else a < b) for a, b in zip(present, present[1:])
    )


def tier_counts(tiers: list[str | None]) -> dict[str, int]:
    return {t: sum(1 for x in tiers if x == t) for t in TIERS}


# --------------------------------------------------------------------------
# Leaderboard
# --------------------------------------------------------------------------


def leaderboard(
    entries: pd.DataFrame,
    athletes: pd.DataFrame,
    metric: dict[str, Any],
    benchmarks: dict[str, float] | None = None,
    only_active: bool = True,
    grad_year: str = "All",
) -> pd.DataFrame:
    """Best value per athlete for one metric, ranked best first."""
    roster = athletes[athletes["active"]] if only_active else athletes
    if grad_year and grad_year != "All":
        roster = roster[roster["grad_year"].astype("string") == str(grad_year)]
    if roster.empty:
        return pd.DataFrame()

    hib = bool(metric["higher_is_better"])
    mine = entries[
        (entries["metric_id"] == metric["metric_id"])
        & (entries["athlete_id"].isin(set(roster["athlete_id"])))
    ]
    if mine.empty:
        return pd.DataFrame()

    rows = []
    amap = athlete_map(roster)
    for athlete_id, group in mine.groupby("athlete_id", sort=False):
        best = best_row(group.sort_values("ts", na_position="first"), hib)
        if best is None:
            continue
        athlete = amap.get(athlete_id, {})
        rows.append(
            {
                "athlete_id": athlete_id,
                "name": athlete.get("name", "Unknown"),
                "grad_year": athlete.get("grad_year"),
                "position": athlete.get("position", ""),
                "best": float(best["value"]),
                "best_ts": best["ts"],
                "entries": len(group),
                "tier": tier_of(float(best["value"]), benchmarks, hib),
            }
        )
    if not rows:
        return pd.DataFrame()

    out = pd.DataFrame(rows).sort_values(
        ["best", "best_ts"], ascending=[not hib, True], na_position="last"
    )
    out.insert(0, "rank", range(1, len(out) + 1))
    return out.reset_index(drop=True)


# --------------------------------------------------------------------------
# Display formatting
# --------------------------------------------------------------------------


def fmt_value(value: float | None, unit: str = "") -> str:
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return "—"
    unit = (unit or "").strip().lower()
    if unit == "sec":
        return f"{value:.2f}"
    if unit in ("mph", "%"):
        return f"{value:.1f}"
    if float(value) == int(value):
        return str(int(value))
    return f"{value:.1f}"


def fmt_with_unit(value: float | None, unit: str = "") -> str:
    text = fmt_value(value, unit)
    if text == "—":
        return text
    return f"{text} {unit}".strip()


def fmt_delta(delta: float | None, unit: str = "") -> str:
    if delta is None or (isinstance(delta, float) and not math.isfinite(delta)) or delta == 0:
        return "—"
    sign = "+" if delta > 0 else "−"
    return f"{sign}{fmt_value(abs(delta), unit)}"


def _strip_zeros(text: str) -> str:
    """'Aug 05, 2026 · 03:07 PM' -> 'Aug 5, 2026 · 3:07 PM' (%-d is POSIX-only)."""
    return text.replace(" 0", " ").replace("·0", "·")


def fmt_date(ts: Any) -> str:
    if ts is None or pd.isna(ts):
        return "—"
    return _strip_zeros(pd.Timestamp(ts).strftime("%b %d"))


def fmt_day(ts: Any) -> str:
    if ts is None or pd.isna(ts):
        return "—"
    return _strip_zeros(pd.Timestamp(ts).strftime("%b %d, %Y"))


def fmt_datetime(ts: Any) -> str:
    if ts is None or pd.isna(ts):
        return "—"
    return _strip_zeros(pd.Timestamp(ts).strftime("%b %d, %Y · %I:%M %p"))


def time_ago(ts: Any, now: datetime | None = None) -> str:
    if ts is None or pd.isna(ts):
        return "no date"
    now = now or datetime.now()
    delta = now - pd.Timestamp(ts).to_pydatetime()
    seconds = delta.total_seconds()
    if seconds < 0:
        return "just now"
    if seconds < 90:
        return "just now"
    minutes = seconds / 60
    if minutes < 60:
        return f"{int(minutes)} min ago"
    hours = minutes / 60
    if hours < 24:
        return f"{int(hours)} hr ago"
    days = hours / 24
    if days < 2:
        return "yesterday"
    if days < 31:
        return f"{int(days)} days ago"
    months = days / 30.44
    if months < 12:
        return f"{int(months)} mo ago"
    return f"{days / 365.25:.1f} yr ago"
