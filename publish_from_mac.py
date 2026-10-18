#!/usr/bin/env python3
"""Send the slow-moving half of the site from Aaron's Mac: the background charts
(daily data from the Macro Dashboard panel) and the morning note's four theses.

    python3 publish_from_mac.py            build data/background.json and
                                           data/note.json, commit, push
    python3 publish_from_mac.py --no-push  build only

The weekday note job in Macro Dashboard runs this after it writes the note.
The push starts the site's deploy workflow, so the page updates within minutes.

Every chart is cut at its series' last real print (`last_real` in the panel),
so a forward-filled value is never drawn as if it were today's.
"""
import json, re, subprocess, sys, datetime as dt
from pathlib import Path

HERE = Path(__file__).resolve().parent
DASH = HERE.parent / "Macro Dashboard"
PANEL = DASH / "data" / "panel.json"
BRIEFS = DASH / "briefs"
DAYS = 260   # about a year of trading days

# Each question gets the charts that answer it. "rebase" puts lines on 100 at
# the start so different price levels share one axis; "invert" flips a USD/xxx
# quote so up means the Asian currency got stronger.
QUESTIONS = [
    ("Is the Fed going to hike?", [
        {"title": "Fed funds futures: rate priced for each month", "kind": "strip"},
        {"title": "Overnight rates", "series": ["EFFR", "SOFR", "IORB"]},
    ]),
    ("What is driving the 10-year?", [
        {"title": "10y yield = real yield + breakeven inflation", "series": ["DGS10", "DFII10", "T10YIE"]},
        {"title": "Curve slope", "series": ["T10Y2Y", "DGS10-DGS3MO"]},
    ]),
    ("Is oil tight?", [
        {"title": "Brent and WTI, front month", "series": ["BZ=F", "CL=F"]},
        {"title": "Brent: first month minus second (above zero = tight now)", "series": ["BRENT-M1M2"]},
        {"title": "Diesel crack: refiners' margin on diesel", "series": ["CRACK-DIST"]},
    ]),
    ("Is the dollar squeezing Asia?", [
        {"title": "Dollar index (DXY)", "series": ["DX-Y.NYB"]},
        {"title": "Asia FX vs the dollar, start = 100, up = Asian currency stronger",
         "series": ["JPY=X", "KRW=X", "TWD=X", "SGD=X", "CNY=X"], "rebase": True, "invert": True},
    ]),
    ("Is the AI trade cracking?", [
        {"title": "Chips, Nasdaq and Asia tech, start = 100",
         "series": ["SMH", "QQQ", "^TWII", "^KS11"], "rebase": True},
    ]),
]
SECTIONS = ["Rates", "FX", "Equities", "Commodities"]


def series(panel, key, dates):
    """(date, value) pairs for the last year, cut at the last real print."""
    vals = panel["series"][key][-DAYS:]
    last_real = panel["meta"][key].get("last_real") or dates[-1]
    return [(d, v) for d, v in zip(dates, vals) if v is not None and d <= last_real]


def line_chart(panel, spec):
    dates = panel["dates"][-DAYS:]
    out, sources, asof = [], set(), []
    for key in spec["series"]:
        m = panel["meta"][key]
        pts = series(panel, key, dates)
        if not pts:
            continue
        if spec.get("invert"):
            pts = [(d, 1 / v) for d, v in pts]
        if spec.get("rebase"):
            base = pts[0][1]
            pts = [(d, 100 * v / base) for d, v in pts]
        label = m["label"].replace("onshore", "").strip()
        out.append({"label": label, "d": [p[0] for p in pts], "v": [round(p[1], 4) for p in pts]})
        sources.add(m["source"])
        asof.append(pts[-1][0])
    unit = "start = 100" if spec.get("rebase") else panel["meta"][spec["series"][0]]["unit"]
    return {"title": spec["title"], "kind": "line", "unit": unit, "lines": out,
            "asof": min(asof) if asof else None, "source": ", ".join(sorted(sources))}


def strip_chart(panel, spec):
    """Implied fed funds rate by contract month, today against a month ago."""
    strip = panel["ffstrip"]
    today = strip[0]["hist"][-1][0]
    cutoff = (dt.date.fromisoformat(today) - dt.timedelta(days=30)).isoformat()
    months, now, then = [], [], []
    for f in strip:
        past = [v for d, v in f["hist"] if d <= cutoff]
        months.append(f["month"].replace(" 20", " '"))
        now.append(round(f["implied"], 3))
        then.append(round(past[-1], 3) if past else None)
    return {"title": spec["title"], "kind": "strip", "unit": "%", "x": months,
            "lines": [{"label": "Now", "v": now}, {"label": "A month ago", "v": then}],
            "asof": today, "source": "CME fed funds futures via Yahoo Finance"}


def background():
    panel = json.loads(PANEL.read_text())
    qs = []
    for q, specs in QUESTIONS:
        charts = [strip_chart(panel, s) if s.get("kind") == "strip" else line_chart(panel, s) for s in specs]
        qs.append({"question": q, "charts": charts})
    return {"built": dt.datetime.now().astimezone().isoformat(timespec="minutes"), "questions": qs}


def note():
    """The latest note's four sections, each with its first sentence (the thesis)."""
    folders = sorted(p.parent for p in BRIEFS.glob("20??-??-??/note.md"))
    if not folders:
        return None
    folder = folders[-1]
    text = (folder / "note.md").read_text()
    out = []
    for name, body in re.findall(r"\*\d\. ([A-Za-z]+)\*\n(.*?)(?=\n\*\d\.|\n_|\Z)", text, re.S):
        body = body.strip()
        sentences = re.findall(r"\[([^\]]+)\]\((https?://[^)]+)\)([^\[]*)", body)
        if not sentences:
            continue
        first_word, url, rest = sentences[0]
        out.append({"section": name, "thesis": (first_word + rest).strip(), "url": url,
                    "sentences": [{"text": (w + r).strip(), "url": u} for w, u, r in sentences]})
    return {"date": folder.name, "sections": out}


def main():
    (HERE / "data").mkdir(exist_ok=True)
    bg = background()
    (HERE / "data" / "background.json").write_text(json.dumps(bg, separators=(",", ":")))
    n = note()
    if n:
        (HERE / "data" / "note.json").write_text(json.dumps(n, indent=1))
    print(f"background: {sum(len(q['charts']) for q in bg['questions'])} charts; "
          f"note: {n['date'] + ', ' + str(len(n['sections'])) + ' sections' if n else 'none found'}")
    if "--no-push" in sys.argv:
        return
    git = ["git", "-C", str(HERE)]
    subprocess.run(git + ["add", "data/background.json", "data/note.json"], check=True)
    if subprocess.run(git + ["diff", "--cached", "--quiet"]).returncode == 0:
        print("nothing changed; not pushed")
        return
    subprocess.run(git + ["commit", "-q", "-m", f"Daily data {dt.date.today():%Y-%m-%d}"], check=True)
    subprocess.run(git + ["push", "-q"], check=True)
    print("pushed; the site redeploys in a few minutes")


if __name__ == "__main__":
    main()
