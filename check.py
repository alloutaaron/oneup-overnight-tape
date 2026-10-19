#!/usr/bin/env python3
"""Check every chart's data before the site deploys. Exits 1 on a failure, so
GitHub keeps the last good version live instead of publishing a broken one.

    python3 check.py

What it checks, from Aaron's chart rules:
  - enough prices to draw, and a recent update
  - mixed magnitudes: lines on one axis must be within 50x of each other,
    or rebased to 100
  - stale data drawn as live: an overnight line whose last two hours are
    identical is a stuck feed. (Background lines are cut at their last real
    print by publish_from_mac.py, and policy rates like IORB really are flat
    for weeks, so they are not tested this way.)
  - background charts say which date their data runs to
Decimals and baselines are handled by the page itself (decimals from the axis
span; levels never forced to zero).
"""
import json, sys, datetime as dt
from pathlib import Path

HERE = Path(__file__).resolve().parent
fails, warns = [], []


def flat_tail(v, n=8):
    tail = [x for x in v if x is not None][-n:]
    return len(tail) == n and max(tail) == min(tail)


o = json.loads((HERE / "data" / "overnight.json").read_text())
updated = dt.datetime.fromisoformat(o["updated"])
if dt.datetime.now(dt.timezone.utc) - updated > dt.timedelta(hours=1):
    fails.append(f"overnight.json is old: updated {o['updated']}")
if len(o["rows"]) < 10:
    fails.append(f"only {len(o['rows'])} instruments priced (failed: {o['failed']})")
for r in o["rows"]:
    if len(r["v"]) < 12:
        fails.append(f"{r['name']}: only {len(r['v'])} points")
    if r["z"] is None:
        warns.append(f"{r['name']}: no typical-move figure, so it cannot be ranked")
    if r["group"] != "Crypto" and flat_tail(r["v"], 24):
        warns.append(f"{r['name']}: no change in the last two hours of prices (stuck feed, or market shut)")
    if len(r["t"]) != len(r["v"]):
        fails.append(f"{r['name']}: times and prices differ in length")
    for m in r["marks"]:
        if not 0 <= m["i"] < len(r["v"]):
            fails.append(f"{r['name']}: headline marker outside the chart")

bg_path = HERE / "data" / "background.json"
if bg_path.exists():
    b = json.loads(bg_path.read_text())
    for q in b["questions"]:
        for c in q["charts"]:
            name = f"{q['question']} / {c['title']}"
            if not c.get("asof"):
                fails.append(f"{name}: no as-of date")
            levels = []
            for l in c["lines"]:
                v = [x for x in l["v"] if x is not None]
                if len(v) < 5:
                    fails.append(f"{name} / {l['label']}: only {len(v)} points")
                    continue
                levels.append(max(abs(x) for x in v) or 1)
            if len(levels) > 1 and max(levels) / min(levels) > 50 and c["unit"] != "start = 100":
                fails.append(f"{name}: lines {max(levels)/min(levels):.0f}x apart on one axis")
            if c.get("asof"):
                age = (dt.date.today() - dt.date.fromisoformat(c["asof"])).days
                if age > 7:
                    warns.append(f"{name}: data is {age} days old")
else:
    warns.append("no background.json yet")

# The 5-day views are an extra: week.py drops any chart it cannot build, and
# the page falls back to the 1Y view. So problems here only warn.
wk_path = HERE / "data" / "week.json"
if wk_path.exists():
    try:
        wk = json.loads(wk_path.read_text())
        age_h = (dt.datetime.now().timestamp() - wk.get("built", 0)) / 3600
        if age_h > 3:
            warns.append(f"week.json is {age_h:.0f}h old (Yahoo may be refusing hourly prices)")
        for f in wk.get("failed", []):
            warns.append(f"5-day view: {f}")
    except Exception as e:
        warns.append(f"week.json unreadable ({type(e).__name__}); the page shows 1Y views only")
else:
    warns.append("no week.json yet; the page shows 1Y views only")

for w in warns:
    print("warn:", w)
for f in fails:
    print("FAIL:", f)
print(f"{len(o['rows'])} instruments checked; {len(fails)} failures, {len(warns)} warnings")
sys.exit(1 if fails else 0)
