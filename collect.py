#!/usr/bin/env python3
"""Build data/overnight.json: what moved overnight, how unusual it was, and the
wire headline that hit when it moved.

Runs on GitHub every 15 minutes (.github/workflows/refresh.yml). Standard
library only, so the runner needs no installs.

    python3 collect.py            write data/overnight.json
    python3 collect.py --print    also print the ranked moves

THE WINDOW. "Overnight" to a Singapore reader is everything since Asia last
closed: from 16:00 SGT on the previous weekday to now. On a Monday that is
Friday 16:00 SGT, so the weekend is skipped rather than drawn as a flat line.

THE CONTRACT. Yahoo's continuous futures (CL=F, BZ=F) switch contract without
warning: on 2026-10-16 BZ=F jumped 2.5% in ten minutes on a switch, and CL=F was
December while every headline quoted November. So each future is resolved to a
named contract once a day:
    energy (front)   the nearest contract still trading, which is the one
                     headlines quote until it expires
    others (active)  the contract with the most volume
"""
import json, math, re, sys, html, time, urllib.request, urllib.parse, urllib.error
import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
OUT = HERE / "data" / "overnight.json"
CACHE = HERE / "data" / "contracts.json"
SGT, NY, UTC = ZoneInfo("Asia/Singapore"), ZoneInfo("America/New_York"), dt.timezone.utc
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"}

# Headline matching. A headline is tagged to a move only if it names the market
# itself, or a driver specific enough to move it. Broad words ("inflation",
# "china", "fed") tagged Swiss inflation to the Nasdaq and soybean sales to the
# Aussie in the first test (2026-10-19), so every pattern here is narrow.
FED = r"fomc|powell|warsh|\bfed('s)? (chair|governor|official|president|minutes|decision|hike|cut)|rate (hike|cut)"
USDATA = r"(u\.?s\.?|us) (cpi|pce|inflation|payrolls|jobs report|retail sales|gdp|industrial production|jobless claims)|nonfarm"
OIL = (r"\boil\b|crude|brent|\bwti\b|opec|hormuz|tanker|refiner|barrel|\bspr\b|strategic (petroleum )?reserve|"
       r"(saudi|iran|israel|yemen|houthi|iraq|gulf|red sea)[^.]{0,60}(missile|drone|attack|strike|intercept|mine)|"
       r"(missile|drone|attack|strike|intercept|mine)[^.]{0,60}(saudi|iran|israel|yemen|houthi|iraq|gulf|red sea)")
# (key, name, group, how to find the symbol, unit, headline pattern)
INSTRUMENTS = [
    ("ES", "S&P 500 futures", "Equities", ("CME", "HMUZ", "active"), "", rf"s&p 500|s&p futures|us stocks|wall st|stock futures|{FED}|{USDATA}"),
    ("NQ", "Nasdaq 100 futures", "Equities", ("CME", "HMUZ", "active"), "", rf"nasdaq|nvidia|semiconductor|chip stocks|chipmaker|\bai (stocks|trade|spending)|coreweave|big tech|megacap|{FED}|{USDATA}"),
    ("BZ", "Brent crude", "Commodities", ("NYM", "FGHJKMNQUVXZ", "front"), "$/bbl", OIL),
    ("CL", "WTI crude", "Commodities", ("NYM", "FGHJKMNQUVXZ", "front"), "$/bbl", OIL),
    ("HO", "Diesel (ULSD)", "Commodities", ("NYM", "FGHJKMNQUVXZ", "front"), "$/gal", OIL + r"|diesel|distillate|gasoil"),
    ("RB", "Gasoline (RBOB)", "Commodities", ("NYM", "FGHJKMNQUVXZ", "front"), "$/gal", OIL + r"|gasoline"),
    ("NG", "US natural gas", "Commodities", ("NYM", "FGHJKMNQUVXZ", "front"), "$/MMBtu", r"natural gas|natgas|\blng\b|henry hub|gas storage"),
    ("GC", "Gold", "Commodities", ("CMX", "FGHJKMNQUVXZ", "active"), "$/oz", rf"\bgold\b|bullion|{FED}|{USDATA}"),
    ("SI", "Silver", "Commodities", ("CMX", "FGHJKMNQUVXZ", "active"), "$/oz", r"silver"),
    ("HG", "Copper", "Commodities", ("CMX", "FGHJKMNQUVXZ", "active"), "$/lb", r"copper|\blme\b"),
    ("ZN", "US 10y note futures", "Rates", ("CBT", "HMUZ", "active"), "price", rf"treasur|10-year|bond (market|yields)|auction|{FED}|{USDATA}"),
    ("^TNX", "US 10y yield", "Rates", None, "bp", rf"treasur|10-year|bond (market|yields)|auction|{FED}|{USDATA}"),
    ("DX-Y.NYB", "Dollar index", "FX", None, "", rf"dollar index|\bdxy\b|greenback|{FED}|{USDATA}"),
    ("JPY=X", "USD/JPY", "FX", None, "", r"\byen\b|\bjpy\b|\bboj\b|ueda|katayama|intervention"),
    ("AUDUSD=X", "AUD/USD", "FX", None, "", r"\baud\b|aussie|\brba\b|australia"),
    ("KRW=X", "USD/KRW", "FX", None, "", r"\bwon\b|\bkrw\b|\bbok\b|korea"),
    ("SGD=X", "USD/SGD", "FX", None, "", r"\bsgd\b|singapore|\bmas\b"),
    ("BTC-USD", "Bitcoin", "Crypto", None, "$", r"bitcoin|crypto|\bbtc\b"),
]
# What each FX quote means for the currency that is not the dollar.
PLAIN = {"JPY=X": ("yen", -1), "KRW=X": ("won", -1), "SGD=X": ("Singapore dollar", -1),
         "AUDUSD=X": ("Aussie", 1), "DX-Y.NYB": ("dollar", 1)}
# Yahoo answers 429 to a full Chrome user agent that carries no cookies, and
# normally to this plain one (measured 2026-10-19).
YAHOO_UA = {"User-Agent": "Mozilla/5.0"}
MONTHS = "FGHJKMNQUVXZ"
TELEGRAM = [("firstsquaw", "First Squawk"), ("WalterBloomberg", "Walter Bloomberg")]


def get(url, timeout=20, headers=UA):
    """Fetch a page. A 4xx answer (no such contract, no such page) fails at once;
    timeouts and 5xx are retried twice."""
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code < 500 and e.code != 429 or attempt == 2:
                raise
        except Exception:
            if attempt == 2:
                raise
        time.sleep(2 * (attempt + 1))


def chart(sym, interval, rng):
    """(timestamps, closes, volumes) from Yahoo, rows with no close dropped."""
    last = None
    for host in ("query1", "query2"):
        try:
            u = (f"https://{host}.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(sym)}"
                 f"?interval={interval}&range={rng}")
            d = json.loads(get(u, headers=YAHOO_UA))["chart"]["result"][0]
            q = d["indicators"]["quote"][0]
            rows = [(t, c, v) for t, c, v in zip(d.get("timestamp") or [], q.get("close") or [],
                                                 q.get("volume") or [None] * 10**5) if c is not None]
            return [r[0] for r in rows], [r[1] for r in rows], [r[2] or 0 for r in rows]
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise          # the symbol does not exist; the other host won't have it
            last = e
        except Exception as e:
            last = e
    raise last


def window(now):
    """Start of the overnight window: 16:00 SGT on the last weekday, at least
    six hours ago (so an evening visit still sees the night before)."""
    start = now.astimezone(SGT).replace(hour=16, minute=0, second=0, microsecond=0)
    while start > now - dt.timedelta(hours=6):
        start -= dt.timedelta(days=1)
    while start.weekday() >= 5:
        start -= dt.timedelta(days=1)
    return start


def candidates(root, exch, cycle, now):
    """The next few listed contracts, nearest first, e.g. CLX26.NYM."""
    out, y, m = [], now.year, now.month
    while len(out) < 4:
        code = MONTHS[m - 1]
        if code in cycle:
            out.append(f"{root}{code}{y % 100:02d}.{exch}")
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return out


def resolve(root, exch, cycle, rule, now):
    seen = []
    for sym in candidates(root, exch, cycle, now):
        try:
            ts, cl, vol = chart(sym, "1d", "5d")
        except Exception:
            continue
        if ts:
            seen.append((sym, ts[-1], vol[-1] if vol else 0))
    if not seen:
        return f"{root}=F"  # last resort; the page says which symbol it used
    newest = max(s[1] for s in seen)
    # still trading = printed in the latest session; an expired contract stops
    live = [s for s in seen if s[1] >= newest - 6 * 3600]
    if rule == "front":
        return live[0][0]
    return max(live, key=lambda s: s[2])[0]


def daily_sd(sym, bp=False):
    """Typical daily move over the last year: % change, or bp for a yield."""
    ts, cl, _ = chart(sym, "1d", "1y")
    ch = [((b - a) * 100 if bp else (b / a - 1) * 100) for a, b in zip(cl, cl[1:]) if a]
    if len(ch) < 60:
        return None
    mu = sum(ch) / len(ch)
    return math.sqrt(sum((x - mu) ** 2 for x in ch) / len(ch))


def contracts(now):
    """Symbols and typical moves, resolved once per SGT day and cached."""
    today = now.astimezone(SGT).strftime("%Y-%m-%d")
    try:
        c = json.loads(CACHE.read_text())
        if c.get("date") == today and len(c.get("items", {})) == len(INSTRUMENTS):
            return c["items"]
    except Exception:
        pass
    items = {}
    for key, name, group, how, unit, words in INSTRUMENTS:
        sym = resolve(key, *how, now) if how else key
        sd_sym = f"{key}=F" if how else key
        try:
            sd = daily_sd(sd_sym, bp=(unit == "bp"))
        except Exception:
            sd = None
        items[key] = {"symbol": sym, "sd": sd}
    CACHE.write_text(json.dumps({"date": today, "items": items}, indent=1))
    return items


def squawk(start):
    """Wire-style headlines (all caps) since `start`, from public channel previews."""
    out = []
    for slug, name in TELEGRAM:
        before = None
        for _ in range(30):
            try:
                h = get(f"https://t.me/s/{slug}" + (f"?before={before}" if before else ""))
            except Exception:
                break
            oldest, ids = None, []
            for b in h.split('class="tgme_widget_message_wrap')[1:]:
                pid = re.search(rf'data-post="{re.escape(slug)}/(\d+)"', b, re.I)
                ts = re.search(r'<time datetime="([^"]+)"', b)
                tx = re.search(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', b, re.S)
                if pid:
                    ids.append(int(pid.group(1)))
                if not ts:
                    continue
                when = dt.datetime.fromisoformat(ts.group(1))
                oldest = when if oldest is None else min(oldest, when)
                if when < start or not tx:
                    continue
                t = html.unescape(re.sub(r"<[^>]+>", " ", tx.group(1)))
                t = re.sub(r"\s*\(\s*@\w+\s*\)|https?://\S+", "", t)
                t = re.sub(r"\s+", " ", t).strip()
                caps = sum(ch.isupper() for ch in t[:60])
                if len(t) > 20 and caps > 30:
                    out.append((when, t))
            if not ids or (oldest and oldest < start):
                break
            before = min(ids)
            time.sleep(0.4)
    out.sort()
    return out


KEEP = {"US", "U.S.", "U.S", "UK", "U.K.", "EU", "NY", "SEC", "IMF", "NATO", "AI", "OPEC", "WTI", "SPR", "LNG", "BOJ", "ECB", "RBA", "BOK", "MAS",
        "GDP", "CPI", "PCE", "PMI", "S&P", "IRGC", "UAE", "ETF", "IEA", "EIA", "ULSD", "RBOB", "FOMC"}


def sentence_case(t):
    words = t.split(" ")
    out = []
    for i, w in enumerate(words):
        bare = re.sub(r"[^\w&$%.]", "", w)
        if bare.upper() in KEEP or re.search(r"\d|\$", w) or (w.startswith("$") and len(w) > 1):
            out.append(w)
        else:
            out.append(w.lower() if i else w.capitalize())
    s = " ".join(out)
    return s[:110].rstrip() + ("…" if len(s) > 110 else "")


def sharpest(ts, cl, n=3, span=6):
    """Up to n non-overlapping 30-minute moves (6 five-minute bars), biggest first."""
    moves = []
    for i in range(span, len(cl)):
        if cl[i - span]:
            moves.append((abs(cl[i] / cl[i - span] - 1), i - span, i))
    picked = []
    for size, a, b in sorted(moves, reverse=True):
        if all(b <= pa or a >= pb for _, pa, pb in picked):
            picked.append((size, a, b))
        if len(picked) == n:
            break
    return sorted(picked, key=lambda p: p[1])


def build(now=None):
    now = now or dt.datetime.now(UTC)
    start = window(now)
    items = contracts(now)
    heads = squawk(start)
    rows, failed = [], []
    for key, name, group, how, unit, words in INSTRUMENTS:
        sym, sd = items[key]["symbol"], items[key]["sd"]
        try:
            ts, cl, _ = chart(sym, "5m", "5d")
        except Exception as e:
            failed.append(f"{name}: {type(e).__name__}")
            continue
        pts = [(t, c) for t, c in zip(ts, cl) if t >= start.timestamp()]
        if len(pts) < 6:
            failed.append(f"{name}: no prices since {start:%a %H:%M} SGT")
            continue
        ts, cl = [p[0] for p in pts], [p[1] for p in pts]
        a, b = cl[0], cl[-1]
        move = (b - a) * 100 if unit == "bp" else (b / a - 1) * 100
        z = move / sd if sd else None
        # sessions, in SGT: Europe until the US cash open, US until futures
        # settle at 17:00 New York, then Asia
        ny_day = start.astimezone(NY).date()
        us_open = dt.datetime.combine(ny_day, dt.time(9, 30), NY).timestamp()
        us_close = dt.datetime.combine(ny_day, dt.time(17, 0), NY).timestamp()
        def at(t):
            before = [c for tt, c in zip(ts, cl) if tt <= t]
            return before[-1] if before else None
        def pct(x, y):
            if x is None or y is None:
                return None
            return round((y - x) * 100, 1) if unit == "bp" else round((y / x - 1) * 100, 2)
        e_end, u_end = at(us_open), at(us_close)
        sessions = {"Europe": pct(a, e_end), "US": pct(e_end, u_end)}
        if ts[-1] > us_close + 3600:
            sessions["Asia"] = pct(u_end, b)
        marks = []
        # A tag needs a real move (a fifth of a normal day inside 30 minutes)
        # and a headline from shortly before or during it. One that lands after
        # the move is a report on it, not news behind it.
        floor = (sd or 0) / 5
        for size, i, j in sharpest(ts, cl):
            if unit == "bp":
                size = abs(cl[j] - cl[i]) * 100
            else:
                size *= 100
            if size < floor:
                continue
            t0, t1 = ts[i] - 15 * 60, ts[j]
            hit = [h for h in heads if t0 <= h[0].timestamp() <= t1 and re.search(words, h[1], re.I)]
            if hit:
                when, text = hit[0]
                marks.append({"i": j, "time": when.astimezone(SGT).strftime("%H:%M"),
                              "text": sentence_case(text)})
        step = max(1, len(pts) // 160)
        keep = list(range(0, len(pts), step))
        if keep[-1] != len(pts) - 1:
            keep.append(len(pts) - 1)
        idx = {k: n for n, k in enumerate(keep)}
        for m in marks:
            m["i"] = idx[min(keep, key=lambda k: abs(k - m["i"]))]
        plain = None
        if key in PLAIN:
            word, sign = PLAIN[key]
            plain = f"{word} {'stronger' if move * sign > 0 else 'weaker'}" if abs(move) > 0.005 else None
        rows.append({
            "key": key, "symbol": sym, "name": name, "group": group, "unit": unit,
            "first": a, "last": b, "move": round(move, 2), "z": None if z is None else round(z, 2),
            "sessions": sessions, "plain": plain, "marks": marks,
            "t": [ts[k] for k in keep], "v": [round(cl[k], 6) for k in keep],
        })
    rows.sort(key=lambda r: -abs(r["z"] or 0))
    return {
        "updated": now.astimezone(SGT).isoformat(timespec="minutes"),
        "start": start.isoformat(timespec="minutes"),
        "headlines": len(heads), "failed": failed, "rows": rows,
    }


if __name__ == "__main__":
    data = build()
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(data, separators=(",", ":")))
    print(f"{len(data['rows'])} instruments, {data['headlines']} headlines, window from {data['start']}")
    for f in data["failed"]:
        print("  FAILED", f)
    if "--print" in sys.argv:
        for r in data["rows"]:
            print(f"  {r['name']:20} {r['symbol']:11} {r['move']:+7.2f}{'bp' if r['unit']=='bp' else '%'}"
                  f"  z={r['z']}  {r['sessions']}  marks={len(r['marks'])}")
