# CLAUDE.md — ONEUP Overnight Tape

A phone page for Aaron's Telegram group ONEUP (179 members): what moved
overnight, how unusual each move was, the wire headline when it moved, and
background charts for the questions behind the moves. Split out of the Macro
Dashboard project on 2026-10-19; the morning note links here.

Live at https://alloutaaron.github.io/oneup-overnight-tape/ (public repo, public
data, no secrets anywhere in it).

## How data flows

| Part | Made by | When |
|---|---|---|
| `data/overnight.json` (moves, headlines) | `collect.py` on GitHub | every 15 min |
| `data/week.json` (5-day hourly views of the background charts) | `week.py` on GitHub | at most hourly, cached between runs |
| `data/background.json`, `data/note.json` | `publish_from_mac.py` on Aaron's Mac | after the 07:28 note job, then pushed |
| the page | `index.html` reads the three files | each visit, and on Refresh |

`check.py` runs before every deploy; if it fails nothing deploys and the last
good version stays live.

## Commands

| Task | Command |
|---|---|
| Build the overnight data locally | `python3 collect.py --print` |
| Check the data | `python3 check.py` |
| Rebuild the 5-day views now | `python3 week.py --force` |
| Push background and note from the Mac | `/Library/Frameworks/Python.framework/Versions/3.12/bin/python3 publish_from_mac.py` |
| Run the site now | `gh workflow run refresh` |
| See it locally | `python3 -m http.server 8799` here, open http://localhost:8799/ |

## Facts that are not obvious

- **Yahoo answers 429 to a full Chrome user agent without cookies** and
  normally to plain `Mozilla/5.0` (measured 2026-10-19). Keep `YAHOO_UA`.
- **Never use Yahoo's continuous futures (`CL=F`) for overnight moves.** They
  switch contract silently: on 2026-10-16 `BZ=F` jumped 2.5% in ten minutes on
  a switch and `CL=F` was December while headlines quoted November.
  `collect.py` resolves named contracts daily (energy: front; others: most
  volume).
- **A headline tag is "on the wire at the time", never "the cause".** Patterns
  in `INSTRUMENTS` are deliberately narrow; broad words tagged Swiss inflation
  to the Nasdaq in the first test.
- **Scheduled GitHub runs can be late**, sometimes by 30+ minutes. The page
  shows when prices were last updated and flags anything over an hour old.
- **Public repos lose scheduled runs after 60 days with no commits.** The Mac's
  weekday push keeps it active.
- Every chart must be readable (Aaron's rule). `check.py` covers mixed
  magnitudes and stuck feeds; the page takes decimals from the axis span and
  never forces a level to start at zero. Look at the page after changing a chart.
- **The 5D / 1Y switch pairs charts by title.** `week.py`'s `CHARTS` keys must
  match the titles in `publish_from_mac.py`; a chart whose titles drift simply
  shows its 1Y view. Real yield, breakeven and Fed overnight rates are daily
  data and have no 5D view on purpose.
- **`week.py` can never stop a deploy.** It exits 0 always, keeps a symbol's
  last good line when Yahoo refuses it, drops a chart it cannot build, and on a
  total failure leaves the file and its timestamp alone so the next run
  retries. `check.py` only warns about it.
- In the 5D view, lines join across hours their market is shut (US and Asian
  sessions share one hourly timeline), and weekend hours are never labelled:
  Saturday SGT is Friday's US session.
