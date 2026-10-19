#!/usr/bin/env python3
"""Build data/week.json: the background charts over the last five trading days,
from hourly prices, so a chart can show the week behind the night's moves.

Runs on GitHub with collect.py (.github/workflows/refresh.yml). Standard library
only. The page shows a 5D / 1Y switch on every chart that has a week version;
charts without one (real yield and breakeven, Fed overnight rates) are daily
data and stay on the 1Y view from the Mac.

    python3 week.py            write data/week.json (at most once an hour)
    python3 week.py --force    rebuild now

GUARDRAILS, kept deliberately light so they cannot become the failure:
  - Rebuilt at most once an hour; the workflow caches the file between runs.
  - A symbol Yahoo refuses keeps its last good series from the previous file.
  - A chart left with too little data, or lines 50x apart on one axis, is
    dropped from week.json; the page then shows its 1Y view.
  - A time budget stops fetching before the workflow's limit.
  - This script always exits 0: the week view is an extra, so it can never
    stop the overnight prices from deploying.
"""
import json, sys, time, traceback
import datetime as dt
from pathlib import Path

import collect

HERE = Path(__file__).resolve().parent
OUT = HERE / "data" / "week.json"
BUDGET = 150          # seconds of fetching before giving up for this hour
MIN_POINTS = 12       # fewer hourly points than this and the line is not drawn
STALE_DAYS = 4        # a line whose last price is older than this is left out
UTC = dt.timezone.utc

# The charts, keyed by the exact titles publish_from_mac.py gives the 1Y
# versions, so the page can pair them. Each line: (label, how to get it).
#   sym: a Yahoo symbol, or "@KEY" for collect.py's resolved contract for KEY
#   "@BZ+1" is the Brent contract after the front one
CHARTS = {
    "Brent and WTI, front month": {"unit": "$/bbl", "lines": [
        ("Brent", ["@BZ"]), ("WTI", ["@CL"])]},
    "Brent: first month minus second (above zero = tight now)": {"unit": "$/bbl", "lines": [
        ("Brent first minus second month", ["@BZ", "-", "@BZ+1"])]},
    "Diesel crack: refiners' margin on diesel": {"unit": "$/bbl", "lines": [
        ("Diesel (x42) minus Brent", ["@HO", "*42", "-", "@BZ"])]},
    "Dollar index (DXY)": {"unit": "idx", "lines": [("Dollar index", ["DX-Y.NYB"])]},
    "Asia FX vs the dollar, start = 100, up = Asian currency stronger": {
        "unit": "start = 100", "rebase": True, "invert": True, "lines": [
            ("Yen", ["JPY=X"]), ("Won", ["KRW=X"]), ("Taiwan dollar", ["TWD=X"]),
            ("Singapore dollar", ["SGD=X"]), ("Yuan", ["CNY=X"])]},
    "Chips, Nasdaq and Asia tech, start = 100": {"unit": "start = 100", "rebase": True, "lines": [
        ("Chips (SMH)", ["SMH"]), ("Nasdaq 100 futures", ["@NQ"]),
        ("Taiwan (TAIEX)", ["^TWII"]), ("Korea (KOSPI)", ["^KS11"])]},
    "Curve slope": {"unit": "%", "lines": [
        ("10-year yield minus 3-month bill", ["^TNX", "-", "^IRX"])]},
}
STRIP = "Fed funds futures: rate priced for each month"


def hour(t):
    return int(t) // 3600 * 3600


def load_old():
    try:
        return json.loads(OUT.read_text())
    except Exception:
        return {}


class Fetcher:
    """Hourly closes by symbol, fetched once each, within the time budget."""
    def __init__(self, items):
        self.items, self.cache, self.t0, self.failed = items, {}, time.time(), []

    def symbol(self, ref):
        if not ref.startswith("@"):
            return ref
        key, _, step = ref[1:].partition("+")
        sym = self.items[key]["symbol"]
        if step:  # the contract `step` listings after the resolved one
            root, rest = sym[:len(key)], sym[len(key):]
            exch = rest.split(".")[1]
            how = next(i[3] for i in collect.INSTRUMENTS if i[0] == key)
            listed = collect.candidates(root, exch, how[1], dt.datetime.now(UTC))
            listed += collect.candidates(root, exch, how[1], dt.datetime.now(UTC) + dt.timedelta(days=150))
            listed = list(dict.fromkeys(listed))
            sym = listed[listed.index(sym) + int(step)] if sym in listed else None
        return sym

    def series(self, ref):
        sym = self.symbol(ref)
        if sym is None:
            raise ValueError(f"no contract after {ref}")
        if sym not in self.cache:
            if time.time() - self.t0 > BUDGET:
                raise TimeoutError("time budget used up")
            ts, cl, _ = collect.chart(sym, "60m", "5d")
            self.cache[sym] = {hour(t): c for t, c in zip(ts, cl)}
            time.sleep(0.3)
        return self.cache[sym]


def combine(f, recipe):
    """Evaluate a line recipe like ["@HO", "*42", "-", "@BZ"] hour by hour."""
    out, sign, scale = None, 1, 1.0
    for tok in recipe:
        if tok == "-":
            sign = -1
        elif tok.startswith("*"):
            out = {h: v * float(tok[1:]) for h, v in out.items()}
        else:
            s = f.series(tok)
            if out is None:
                out = dict(s)
            else:
                out = {h: out[h] + sign * s[h] for h in out if h in s}
            sign = 1
    return out or {}


def line_ok(points):
    if len(points) < MIN_POINTS:
        return False
    newest = max(points)
    return time.time() - newest < STALE_DAYS * 86400


def build_charts(f, old):
    charts = {}
    for title, spec in CHARTS.items():
        lines = []
        for label, recipe in spec["lines"]:
            try:
                pts = combine(f, recipe)
            except Exception as e:
                f.failed.append(f"{label}: {type(e).__name__}")
                pts = {}
            if not line_ok(pts):
                # keep last hour's line for this label, if it is still fresh
                prev = next((l for l in (old.get("charts", {}).get(title, {}).get("lines", []))
                             if l.get("key", l["label"]) == label), None)
                if prev and line_ok(dict(zip(prev["t"], prev["raw"]))):
                    lines.append(prev)
                continue
            t = sorted(pts)
            raw = [pts[h] for h in t]
            v = raw
            if spec.get("invert"):
                v = [1 / x for x in v]
            if spec.get("rebase"):
                v = [100 * x / v[0] for x in v]
            # a single futures contract is named, so a roll to the next month
            # (a jump in level) is explained on the chart itself
            refs = [tok for tok in recipe if tok.startswith("@")]
            shown = f"{label} ({f.symbol(refs[0])})" if len(recipe) == 1 and refs else label
            lines.append({"key": label, "label": shown, "t": t, "raw": [round(x, 6) for x in raw],
                          "v": [round(x, 4) for x in v]})
        if not lines:
            continue
        levels = [max(abs(x) for x in l["v"]) or 1 for l in lines]
        if len(levels) > 1 and max(levels) / min(levels) > 50 and spec["unit"] != "start = 100":
            f.failed.append(f"{title}: lines too far apart for one axis; left on the 1Y view")
            continue
        charts[title] = {"kind": "line", "unit": spec["unit"], "lines": lines,
                         "asof": max(max(l["t"]) for l in lines)}
    return charts


def build_strip(f, now):
    """Implied fed funds rate by contract month, now against five sessions ago."""
    rows = []
    for sym in collect.candidates("ZQ", "CBT", collect.MONTHS, now)[:2] + \
               collect.candidates("ZQ", "CBT", collect.MONTHS, now + dt.timedelta(days=62))[:4]:
        if any(r[0] == sym for r in rows):
            continue
        if time.time() - f.t0 > BUDGET:
            break
        try:
            ts, cl, _ = collect.chart(sym, "1d", "5d")
        except Exception:
            continue
        if len(cl) >= 2:
            code = sym[2:5]
            month = dt.date(2000 + int(code[1:]), collect.MONTHS.index(code[0]) + 1, 1)
            rows.append((sym, month.strftime("%b '%y"), round(100 - cl[-1], 3), round(100 - cl[0], 3), ts[-1]))
        time.sleep(0.3)
    if len(rows) < 3:
        return None
    return {"kind": "strip", "unit": "%", "x": [r[1] for r in rows],
            "lines": [{"label": "Now", "v": [r[2] for r in rows]},
                      {"label": "Five sessions ago", "v": [r[3] for r in rows]}],
            "asof": max(r[4] for r in rows)}


def main():
    old = load_old()
    built = old.get("built", 0)
    if "--force" not in sys.argv and time.time() - built < 55 * 60:
        print(f"week.json is {int((time.time() - built) / 60)} min old; not rebuilt")
        return
    now = dt.datetime.now(UTC)
    f = Fetcher(collect.contracts(now))
    charts = build_charts(f, old)
    strip = build_strip(f, now)
    if strip:
        charts[STRIP] = strip
    elif STRIP in old.get("charts", {}):
        charts[STRIP] = old["charts"][STRIP]
    total = sum(len(s["lines"]) for s in CHARTS.values())
    if old.get("charts") and (not charts or (len(f.failed) >= total and not strip)):
        # Nothing new at all (Yahoo refusing, network down): leave the file and
        # its timestamp alone, so the age stays honest and the next run retries.
        print("nothing fetched; keeping the previous week.json")
        return
    OUT.write_text(json.dumps({"built": int(time.time()), "charts": charts, "failed": f.failed},
                              separators=(",", ":")))
    print(f"week.json: {len(charts)} charts" + (f"; not updated: {'; '.join(f.failed)}" if f.failed else ""))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        print("week.py failed; the site deploys without a new week view")
    sys.exit(0)
