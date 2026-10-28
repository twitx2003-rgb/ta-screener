"""Where the money flows, and which stocks ride it (owner, 2026-10-28: "stocks fitted to where
the money flows and where sentiment goes; stocks whose overall trend is up, not drifting
sideways for a long time"). Computed every night from the stored daily bars, no TradingView call.

Per stock (at least MIN_BARS sessions):
- relative strength: 0.4 x the 3-month return + 0.2 x each of the 6-, 9- and 12-month returns
  (the weighting of Investor's Business Daily's RS rating, in our own words), as a rating
  1..99 against every followed stock;
- trend template (Mark Minervini's, restated): the close above the 50-, 150- and 200-day
  averages, the 50 above the 150 above the 200, the 200-day rising over a month, the close at
  least `above_low_pct` above the 52-week low and within `near_high_pct` of the 52-week high;
- steadiness: the last `steady_sessions` log-closes fit a rising straight line with R2 of at
  least `steady_r2` (a stock that drifts sideways or swings has a low R2);
- volume: the volume of up days over the volume of down days, last `ud_sessions` sessions.

Per industry and sector (TradingView's classification): the members' median 1-, 3- and
6-month returns against the benchmark (the S&P 500 fund), their median rating, the share of
them in the trend template, their median up/down volume, and the money flow: their share of
all the followed stocks' traded dollar volume in the last 20 sessions against the 60 before.
The rotation quadrant compares the 3-month move with the benchmark (ahead / behind) and the
1-month move likewise (gaining / losing): leading, weakening, lagging, improving (after
Julius de Kempenaer's relative rotation graphs, simplified). The groups are ranked by the
average percentile of rating, template share, up/down volume and flow; the top
`group_top_share` lead. An industry under `industry_min_members` stocks is judged by its sector.

A stock passes when all hold: template, steady, rating >= `rs_min`, up/down >= `ud_min`, and
its group leads (the owner chose the strict filter, 2026-10-28). A first look at the past
months (today's stocks only, so survivors): the strong and steady stocks beat the average stock
in most months, with one sharp losing month (a momentum crash). Not advice.

The snapshot is data/flow/<day>.json (the newest SNAPSHOTS_KEPT days).
"""
from __future__ import annotations

import html
import json
import math
from datetime import date
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

MIN_BARS = 253
SNAPSHOTS_KEPT = 120
SECTOR_MIN_MEMBERS = 8
FLOW_WORDS = ((0.05, "כסף נכנס"), (-0.05, "יציב"), (-math.inf, "כסף יוצא"))
QUADRANTS = {"leading": "מובילים", "improving": "משתפרים", "weakening": "נחלשים", "lagging": "מפגרים"}
CHECKS = {"template": "מגמה עולה", "steady": "יציבה", "rs": "חוזק יחסי", "ud": "נפח קנייה", "group": "ענף מוביל"}


def _ret(c: np.ndarray, n: int) -> float:
    return float(c[-1] / c[-1 - n] - 1) if len(c) > n and c[-1 - n] > 0 else math.nan


def stock_metrics(bars: pd.DataFrame | None, cfg) -> dict[str, Any] | None:
    """One stock's numbers from its daily bars (oldest first), or None with too few."""
    if bars is None or len(bars) < MIN_BARS:
        return None
    c, h, l, v = (bars[k].to_numpy(float) for k in ("close", "high", "low", "volume"))
    if not np.isfinite(c[-MIN_BARS:]).all() or c[-1] <= 0:
        return None
    sma = lambda n, end=None: float(np.mean(c[-n:] if end is None else c[-n - end:-end]))   # noqa: E731
    hi52, lo52 = float(np.max(h[-252:])), float(np.min(l[-252:]))
    template = bool(c[-1] > sma(50) > sma(150) > sma(200) and sma(200) > sma(200, 21)
                    and c[-1] >= (1 + cfg.above_low_pct / 100) * lo52
                    and c[-1] >= (1 - cfg.near_high_pct / 100) * hi52)
    y = np.log(c[-cfg.steady_sessions:])
    x = np.arange(len(y)) - (len(y) - 1) / 2
    slope = float(np.sum((y - y.mean()) * x) / np.sum(x * x))
    spread = float(np.sum((y - y.mean()) ** 2))
    r2 = 1 - float(np.sum((y - y.mean() - slope * x) ** 2)) / spread if spread > 0 else 0.0
    up = np.diff(c[-cfg.ud_sessions - 1:]) > 0
    last_v = v[-cfg.ud_sessions:]
    down_v = float(last_v[~up].sum())
    score = 0.4 * _ret(c, 63) + 0.2 * _ret(c, 126) + 0.2 * _ret(c, 189) + 0.2 * _ret(c, 252)
    return {"r21": _ret(c, 21), "r63": _ret(c, 63), "r126": _ret(c, 126), "score": score,
            "template": template, "r2": round(r2, 3), "trend_yr": math.exp(slope * 252) - 1,
            "steady": bool(slope > 0 and r2 >= cfg.steady_r2),
            "ud": float(last_v[up].sum()) / down_v if down_v > 0 else 9.99,
            "dv20": float(np.sum(c[-20:] * v[-20:])), "dv60": float(np.sum(c[-80:-20] * v[-80:-20])) / 3,
            "from_high_pct": (c[-1] / hi52 - 1) * 100}


def rating(score: float, cuts: list[float]) -> int:
    """A 1..99 rating: the share of followed stocks with a lower score (`cuts`: the scores'
    percentiles 0..100)."""
    if not math.isfinite(score) or not cuts:
        return 0
    return int(min(99, max(1, round(np.searchsorted(np.asarray(cuts), score) * 99 / len(cuts)))))


def _quadrant(ahead: float, gaining: float) -> str:
    if ahead > 0:
        return "leading" if gaining > 0 else "weakening"
    return "improving" if gaining > 0 else "lagging"


def groups(frame: pd.DataFrame, by: str, bench: dict[str, float], min_members: int) -> pd.DataFrame:
    """The groups (`by`: sector or industry) of the rated stocks, best first."""
    g = frame.dropna(subset=[by]).groupby(by)
    total_now, total_before = frame["dv20"].sum(), frame["dv60"].sum()
    out = pd.DataFrame({
        "n": g.size(), "r21": g["r21"].median(), "r63": g["r63"].median(), "r126": g["r126"].median(),
        "rs": g["rs"].median(), "template_pct": g["template"].mean() * 100, "ud": g["ud"].median(),
        "flow": (g["dv20"].sum() / total_now) / (g["dv60"].sum() / total_before) - 1,
    })
    out = out[out["n"] >= min_members].copy()
    if out.empty:
        return out
    out["quadrant"] = [_quadrant(a - bench["r63"], m - bench["r21"]) for a, m in zip(out["r63"], out["r21"])]
    out["composite"] = sum(out[k].rank(pct=True) for k in ("rs", "template_pct", "ud", "flow")) / 4
    return out.sort_values("composite", ascending=False)


def snapshot(stocks: pd.DataFrame, bars_of: Callable[[str], pd.DataFrame | None],
             benchmark: pd.DataFrame | None, cfg, day: date) -> dict[str, Any]:
    """The night's map. `stocks`: the followed stocks with symbol, ticker, sector and industry."""
    rows = []
    for r in stocks.to_dict("records"):
        m = stock_metrics(bars_of(r["symbol"]), cfg)
        if m is not None:
            rows.append({"symbol": r["symbol"], "ticker": r.get("ticker") or r["symbol"].split(":")[-1],
                         "sector": r.get("sector"), "industry": r.get("industry"), **m})
    frame = pd.DataFrame(rows)
    b = stock_metrics(benchmark, cfg) if benchmark is not None else None
    bench = {k: (b or {}).get(k, 0.0) for k in ("r21", "r63", "r126")}
    if frame.empty:
        return {"day": day.isoformat(), "benchmark": bench, "stocks": {}, "sectors": [], "industries": [],
                "cuts": [], "passing": []}
    scores = frame["score"].dropna()
    cuts = [float(np.percentile(scores, q)) for q in range(101)] if len(scores) else []
    frame["rs"] = [rating(s, cuts) for s in frame["score"]]
    sectors = groups(frame, "sector", bench, SECTOR_MIN_MEMBERS)
    industries = groups(frame, "industry", bench, cfg.industry_min_members)
    lead = lambda t: set(t.index[:max(1, math.ceil(len(t) * cfg.group_top_share))]) if len(t) else set()  # noqa: E731
    lead_sectors, lead_industries = lead(sectors), lead(industries)
    out_stocks: dict[str, dict] = {}
    for r in frame.to_dict("records"):
        judged_by_industry = r["industry"] in industries.index
        group_ok = r["industry"] in lead_industries if judged_by_industry else r["sector"] in lead_sectors
        checks = {"template": r["template"], "steady": r["steady"], "rs": r["rs"] >= cfg.rs_min,
                  "ud": r["ud"] >= cfg.ud_min, "group": bool(group_ok)}
        out_stocks[r["symbol"]] = {
            "ticker": r["ticker"], "sector": r["sector"], "industry": r["industry"], "rs": int(r["rs"]),
            "r2": r["r2"], "ud": round(r["ud"], 2), "r63": round(r["r63"], 4), "from_high_pct": round(r["from_high_pct"], 1),
            "missing": [k for k, ok in checks.items() if not ok], "passes": all(checks.values())}
    table = lambda t, kind: [{"name": name, "kind": kind, **{k: (round(float(v), 4) if isinstance(v, (int, float, np.floating, np.integer)) else v)  # noqa: E731
                                                             for k, v in row.items()}}
                             for name, row in t.iterrows()]
    return {"day": day.isoformat(), "benchmark": {k: round(v, 4) for k, v in bench.items()},
            "stocks": out_stocks, "cuts": cuts,
            "sectors": table(sectors, "sector"), "industries": table(industries, "industry"),
            "leading_sectors": sorted(lead_sectors), "leading_industries": sorted(lead_industries),
            "passing": sorted(s for s, x in out_stocks.items() if x["passes"])}


def extra_passes(snap: dict[str, Any], bars: pd.DataFrame | None, cfg) -> bool:
    """A stock outside the followed ones (the owner's own lists): the same checks against the
    night's ratings, without the group (its industry is not known here)."""
    m = stock_metrics(bars, cfg)
    if m is None or not snap.get("cuts"):
        return False
    return bool(m["template"] and m["steady"] and rating(m["score"], snap["cuts"]) >= cfg.rs_min
                and m["ud"] >= cfg.ud_min)


# ------------------------------------------------------------------ storage
def path(root: Path, day: str) -> Path:
    return root / "flow" / f"{day}.json"


def save(root: Path, snap: dict[str, Any]) -> None:
    folder = root / "flow"
    folder.mkdir(parents=True, exist_ok=True)
    target = path(root, snap["day"])
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")
    tmp.replace(target)
    for old in sorted(folder.glob("????-??-??.json"))[:-SNAPSHOTS_KEPT]:
        old.unlink(missing_ok=True)


def read(root: Path, day: str | None = None) -> dict[str, Any] | None:
    """The snapshot of `day`, or the newest one on or before it (None: the newest)."""
    folder = root / "flow"
    files = sorted(folder.glob("????-??-??.json")) if folder.exists() else []
    if day is not None:
        files = [f for f in files if f.stem <= day]
    if not files:
        return None
    try:
        return json.loads(files[-1].read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def passing(snap: dict[str, Any] | None) -> set[str] | None:
    """The stocks that pass (None when there is no map: nothing is filtered)."""
    return None if snap is None else set(snap.get("passing") or [])


# ------------------------------------------------------------------ the weekly map
def _pct(x: Any) -> str:
    if not (isinstance(x, (int, float)) and math.isfinite(x)):
        return "?"
    return "0%" if round(x * 100) == 0 else f"{x * 100:+.0f}%"


def _name(group: dict[str, Any]) -> str:
    from .web.labels import INDUSTRIES, SECTORS

    names = INDUSTRIES if group["kind"] == "industry" else SECTORS
    return names.get(group["name"], group["name"])


def _flow_word(x: float) -> str:
    return next(word for floor, word in FLOW_WORDS if x > floor)


def weekly_message(snap: dict[str, Any], previous: dict[str, Any] | None, top: int = 8) -> str:
    """The owner's weekly map (Telegram HTML), from this week's snapshot and last week's."""
    e = html.escape
    d = snap["day"]
    bench = snap.get("benchmark") or {}
    inds = snap.get("industries") or []
    lines = [f"🧭 <b>לאן הכסף זורם</b> · השבוע שהסתיים ב-{d[8:10]}/{d[5:7]}",
             f"S&amp;P 500: חודש {_pct(bench.get('r21'))} · שלושה חודשים {_pct(bench.get('r63'))}", ""]
    leading = [g for g in inds if g["name"] in set(snap.get("leading_industries") or [])][:5]
    if leading:
        lines.append("🟢 <b>ענפים מובילים</b> (חזקים מהשוק, יותר נפח בעליות, מגמות עולות):")
        lines += [f"• {e(_name(g))}: שלושה חודשים {_pct(g['r63'])} · {_flow_word(g['flow'])}" for g in leading]
    rising = [g for g in inds if g["quadrant"] == "improving" and g["flow"] > 0.05
              and g["name"] not in set(snap.get("leading_industries") or [])][:4]
    if rising:
        lines += ["", "🔼 <b>מתחילים להתחזק</b>: " + " · ".join(e(_name(g)) for g in rising)]
    falling = [g for g in reversed(inds) if g["quadrant"] in ("lagging", "weakening") and g["flow"] < -0.05][:4]
    if falling:
        lines += ["🔻 <b>כסף יוצא</b>: " + " · ".join(e(_name(g)) for g in falling)]
    if previous:
        before, now = set(previous.get("leading_industries") or []), set(snap.get("leading_industries") or [])
        names = {g["name"]: g for g in inds}
        came = [e(_name(names[n])) for n in sorted(now - before) if n in names][:4]
        went = [e(_name({"name": n, "kind": "industry"})) for n in sorted(before - now)][:4]
        if came:
            lines.append("➕ <b>הצטרפו להובלה השבוע</b>: " + " · ".join(came))
        if went:
            lines.append("➖ <b>יצאו מההובלה</b>: " + " · ".join(went))
    stocks = snap.get("stocks") or {}
    best = sorted((s for s in snap.get("passing") or [] if s in stocks), key=lambda s: -stocks[s]["rs"])[:top]
    if best:
        from .web.labels import INDUSTRIES

        lines += ["", "⭐ <b>מניות שעוברות את כל התנאים</b> (מגמה עולה ויציבה, חזקות מ-80% מהשוק, "
                  "נפח קנייה, ענף מוביל):"]
        lines += [f"• <b>{e(stocks[s]['ticker'])}</b> · {e(INDUSTRIES.get(stocks[s]['industry'] or '', stocks[s]['industry'] or ''))}"
                  f" · חוזק יחסי {stocks[s]['rs']}" for s in best]
    lines += ["", f"{len(snap.get('passing') or [])} מניות עוברות את כל התנאים מתוך {len(stocks)}.",
              "<i>לא ייעוץ השקעות. חוזק עבר אינו מבטיח המשך.</i>"]
    return "\n".join(lines)
