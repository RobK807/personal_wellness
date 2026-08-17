# Personal Wellness

Four trackers behind one sidebar, on one database, with two front-ends over the
top. The **weigh-in tracker** is the standalone app of the same name, moved in
whole; the **run tracker** is new, built from seven years of runs scraped off
Strava; the **workout plan** and the **diet tracker** are placeholders with
nothing behind them yet, and say so.

Built the same way as the CD dashboard next door: one shared `core` package, a
**Flask** front-end light enough to run on the NAS, and a **Streamlit** one for
a machine with memory to spare.

```bash
pip install -r requirements.txt
python -m core.excel_import --rebuild     # the weigh-in history, once
python -m core.strava_import --rebuild    # the runs, once
python serve.py                           # Flask, http://localhost:8503
streamlit run app.py                      # Streamlit, same data
```

Both importers need a desktop — openpyxl opening a 1.5 MB workbook needs far
more memory than the NAS has free. Everything else runs anywhere.

---

## The sections

The sidebar picks the tracker; the tab strip picks the page inside it. Both
front-ends read that from `config.SECTIONS`, so adding the fourth tracker means
adding a blueprint, a views subpackage and one line — not editing two
navigations that can drift apart.

| | |
| --- | --- |
| ⚖️ **Weigh-in tracker** | Six years of weigh-ins, unchanged. [What it does](#the-weigh-in-tracker) |
| 🏃 **Run tracker** | 229 runs, 1,542 best efforts. [What it does](#the-run-tracker) |
| 🏋️ **Workout plan** | Placeholder. [Why there is no schema yet](#the-two-that-are-not-built) |
| 🥗 **Diet tracker** | Placeholder. Same. |

---

## The weigh-in tracker

Moved across as it was designed, and it still reconciles with the workbook.

**Input.** Weight, BMI, body fat %, skeletal muscle %, RM Kcal and visceral
fat, twice a day, for any date. The first four are entered to one decimal place
and the last two as whole numbers, because that is what the scale reports;
anything finer is rounded on the way in and anything wild is refused outright —
715 typed for 71.5 does not get saved.

**Averaging.** The two weigh-ins are averaged into the day's figures, and
everything downstream is built on that average. If only one weigh-in was taken,
the average is that one reading rather than half of it.

**Derived masses.** Body fat and skeletal muscle in kilograms, from the
averaged weight and the averaged percentage — the workbook's `Data!T` and
`Data!U`.

**Back-filling missed days.** Miss a Tuesday and a Wednesday, and entering
Thursday's weigh-in fills both of them in on a straight line between Monday and
Thursday. This is the Adjustments sheet, done automatically and for any length
of gap. See [How back-filling works](#how-back-filling-works).

**Charts.** Every metric on its own, plus the four paired charts from the
workbook — weight against BMI, body fat against skeletal muscle, RM Kcal
against visceral fat, weight against body fat — each pair on two independent
y-axes. Any range, averaged daily, weekly or monthly. Hovering — or dragging a
finger — gives a crosshair and the values for that date; see
[Reading values off the chart](#reading-values-off-the-chart).

**Changes.** Day on day, week on week, month on month, the rolling seven-day
change, and the weekly average of that rolling change. The last three are the
workbook's three change sheets; the first two are new.

**Data.** The figures as a table, daily, weekly or monthly, reading from the
same views the charts are drawn from so the two can never disagree about the
same week. Weekly and monthly rows carry a **Days** and an **Estimated** count,
so a period leaning heavily on interpolated days is visible rather than hidden
inside its average — which is how you find out that 31/05/2024 to 06/07/2024 is
a 37-day straight line rather than five weeks of weigh-ins.

### Does it agree with the spreadsheet?

Yes, and there is a test that proves it:

```bash
python reconcile_test.py
```

It recomputes every derived series from the database and compares it against
the cached values in the corresponding sheet — 13,638 daily figures, 1,944
weekly, 456 monthly, plus the three change sheets and the derived masses.

It also found four things wrong with the workbook. None of them is dramatic,
but two are worth knowing about:

1. **The Summary sheet silently drops three days.** Column A is `=Data!A3`
   filled down, and at row 818 the references slipped by one and never
   recovered — twice more after that. **09/08/2022, 29/01/2023 and 04/09/2025
   were recorded and then left out** of every average built on Summary: the
   monthly figures and all three change sheets. The dashboard includes them, so
   those periods legitimately disagree with the sheet.

2. **The rolling weekly change is a row offset, not a date offset.** The sheet
   computes `Summary!B10 - Summary!B3` — seven *rows* apart, which is seven days
   only while Summary has one row per day. After the first dropped day it is
   quietly comparing eight. The dashboard joins on the date.

3. **The current part-week average is divided by seven regardless.** Weekly
   Results averages a fixed seven-cell window, and the cells below the last
   reading hold formulas evaluating to `0` rather than being empty, so `AVERAGE`
   counts them. The part-week at the end reads about 6/7 of the truth.

4. Monthly rows exist for months that have not happened yet. Harmless.

The test asserts each of these rather than tolerating them: if one ever stops
being true, it fails and says so.

---

## The run tracker

228 runs between 01/09/2019 and 11/08/2026 — 2,383 km — scraped off Strava into
`strava_webscrape/strava_runs.xlsx` and imported from its `Final_data` tab.

### What the sheet is, and what it becomes

`Final_data` is one row per (run, breakdown): a run that reached nine rungs of
Strava's best-effort ladder is nine rows repeating the same date, distance,
time, pace, run type and effort type. That is a join flattened for a
spreadsheet, and the importer folds it back into two tables — `runs` for what
the run was, `run_bests` for the ladder inside it. 1,546 rows become 228 runs
and 1,542 best efforts.

`Final_data` has no activity id, so a run is identified by its date, distance
and elapsed time. That is enough: the dates carrying more than one run — three
of them on 07/06/2026 — separate cleanly, and re-importing updates rather than
duplicates.

Two things in the tab are read defensively rather than trusted. The oldest 51
rows have lost their date and time **number formats**, so they arrive as bare
Excel serials; the values are intact and the importer converts them rather than
refusing, because losing a format is not losing a value. And one run's type and
effort come through as `#N/A` — `Clean_data` looks both up with `OFFSET`/`MATCH`
over a fixed range and that run's id falls outside it — which is imported as
*Unclassified* rather than as a category called `#N/A`. Both are counted in the
import summary so they get fixed at source.

**Neither pace column is imported.** Both are quotients of columns that are, and
importing a derived value is how a database ends up disagreeing with itself —
which is exactly what has happened in the sheet: `Final_data`'s Pace column is a
pasted value, and correcting sixteen runs' times left all sixteen paces reading
the old figure. The dashboard is unaffected, and `run_test.py` says so until
they are re-pasted.

The breakdown pace is Strava's own figure, taken from a time it knows to better
than a second where the sheet kept only the whole second, so 100 of the 1,542
differ from a derived one by exactly one second. Deriving it is still right,
because a run entered by hand has no Strava figure to copy.

### Input

The spreadsheet's fields, minus the two it worked out for itself: date,
distance, time, run type, effort type, an optional name, and a breakdown time
against each rung the run was long enough to reach. Times are `mm:ss` or
`h:mm:ss`. Everything is stored as whole seconds, because an Excel time-of-day
sorts fine right up to the point where a run passes 24 hours or you want to
average two of them.

Four things are refused outright, and each one is refused because it would make
the records table lie: a rung longer than the run, a split longer than the whole
run, a split quicker than a shorter split inside the same run, and a date in
the future. Entering a run that already exists corrects it rather than
duplicating it.

### Interval sessions

A run flagged **Intervals** can carry the shape of the session as well as the
run. Five nullable columns on `runs`, so every run recorded before this existed
simply reads blank.

| column | `8 x 1k @ 3:50/km` | `10 x 0:10 @ 3:00/km` |
| --- | --- | --- |
| `interval_type` | `distance` | `time` |
| `interval_count` | 8 | 10 |
| `interval_distance_m` | 1000 | — *(no set distance)* |
| `interval_time_s` | — *(no set time)* | 0:10 |
| `interval_pace_s` | 3:50 | 3:00 |

`interval_type` says how the session was set, and so **which of the two length
boxes applies**. A session set by distance fixes how far each rep is and lets
the clock fall where it may; one set by time does the reverse. The box that
does not apply is refused rather than quietly dropped, because silently
discarding something typed into a form is how an afternoon's entry disappears.

**Every field is entered; none is worked out from another.** That is a
deliberate exception to the rule the rest of the tracker follows, and
ten-second sprints are the case that forces it: the reps are 0:10 each at a
pace of something like 3:00/km, and with no distance recorded there is nothing
to divide. `interval_pace_s` is the only stored pace in the database. It is
safe to store precisely because nothing else holds the same fact — there is no
second copy for it to drift from, which is the actual reason pace is derived
everywhere else.

Distances are typed as `400m`, `1k` or `1.6km`, because that is how the
sessions are named; times and paces as `mm:ss`. The form refuses:

- a length with no type — the type is what says which box applies;
- a distance per rep on a session set by time, or a time per rep on one set by
  distance;
- a session whose reps add up to more than the run they sit inside, in
  kilometres or on the clock;
- a time in the distance box — `3:00` and `3min` are caught by shape, and the
  message points at the box that wanted them;
- a pace outside 1:30–20:00 per kilometre, which is what catches the rep time
  typed into the pace box. `0:10` there is ten seconds per kilometre.

The Log page lists the runs the spreadsheet called intervals that have no
detail yet, with each one's best 400m and best 1K beside it as a prompt — a
session of 1k reps usually has a best 1K close to its pace per interval. The
list empties itself as they are filled in.

An earlier cut of this stored a time per rep and divided out the pace. It only
worked for sessions set by distance, and `core/db.py _convert_interval_splits`
brings a database holding that column forward: the pace it should always have
had is worked out per row, the rep time is kept only where the session was set
by time, and the old column is dropped.

None of this is in the spreadsheet, so **`--rebuild` preserves it**: the
importer keeps the interval columns against each run's identity and puts them
back after reloading. A run whose date, distance or time has since changed in
the sheet cannot be matched, and the import says so rather than dropping it
quietly.

### Run type and effort type

Both are **closed lists**, chosen from a dropdown and edited on the **Admin
page** — one option per line, in the order the dropdown offers them. They live
in the `run_options` table rather than in `config.py`, because adding a kind of
session should not need a code edit, a push to the NAS and a restart.

A free-text box was the first cut and the wrong one. It eventually produces
`VO2 max`, `VO2 Max` and `VO2max` as three effort types, each owning a slice of
the analysis and none of them telling the truth about the training.

`config.RUN_TYPES` and `config.EFFORT_TYPES` are the **seed**, used once on a
database that has no list yet, alongside whatever the imported runs already use
— which is how a value like `Unclassified` gets onto the list rather than
leaving the run carrying it impossible to edit. Editing those constants
afterwards changes nothing.

Two rules keep the lists and the history in step, since `runs.run_type` is plain
`TEXT` rather than a foreign key:

- **An option in use cannot be removed.** The message names it and says how
  many runs have it. A rename reads as a removal, so `VO2 max` → `VO2 Max` is
  refused too, rather than stranding thirty-two runs.
- **The importer extends the list rather than bypassing it.** The spreadsheet is
  where this vocabulary came from, so a new word in it becomes a new option
  (`core/run_options.register`). The importer itself still does not validate —
  its job is to reproduce the sheet.

**Reset** puts one list back to the seed plus anything runs still use. Anything
in use but not offered is highlighted on the Admin page, which is how you find
out that all of the above has gone wrong somehow.

### Analysis

Split by **run type** and by **effort type**, with a grid of the two crossed, a
scatter of every run — how far against how fast — and volume and pace over time.
The filter dropdowns read the values the data actually contains, not the option
lists, so a type nothing uses does not clutter them.

**Pace across a group is its total time over its total distance, not the mean of
each run's pace.** A 20 km plod and a 2 km sprint should not count equally
towards how fast the running was.

**Every pace axis is inverted, so faster is higher.** Drawn the usual way round,
a pace chart reads exactly backwards: the line falls as you get quicker.

There is also a per-distance table — the best, average and slowest at each rung
of the ladder. *5K* there is not a 5K race: it is the fastest 5K found inside
each run of at least 5 km, which is why the count falls away as the distances
grow (225 runs reach 400m, eleven reach the half-marathon).

### Records

The top five at each of the eleven breakdown distances. A single run appears in
as many tables as it reached rungs — a half-marathon holds a fastest 400m as
well as a fastest 20K — and never twice in the same one, because `run_bests` is
keyed on `(run_id, breakdown)`. A time that has been matched but not beaten
keeps its place.

The page opens on everything rather than the last year, because a personal best
that expires after twelve months is not what the word means. The range and type
filters narrow it — *the fastest 5K inside a weighted run* is a fair question.

### Splits that cannot be true

A best effort can be impossible in two ways: **longer than the whole run it sits
inside**, or **quicker than a shorter split of the same run**. Both are checked,
by `v_run_bests.suspect`, and **both are currently zero**.

They were not always. The scrape produced eighteen of the first kind and one of
the second, and both were corrected at source in August 2026 — the second by
putting a mangled half-marathon time right, the first by correcting the runs'
elapsed times, which moved 16 runs and added 2:43 to the recorded total.

The rules stay. They are what the input form enforces on anything entered by
hand, and a refreshed scrape that reintroduces one should surface on the Admin
page rather than at the top of a records table. Anything they do catch is
**imported as it stands** — the run happened, and quietly dropping rows would
leave the dashboard holding less than the sheet it was built from — and then
**held out of the records and the per-distance averages**, because a table of
bests should only contain times that could have happened. The Records page has
a toggle that puts them back if you want to look.

One thing is reported but not flagged: **49 best efforts are slower than their
own run's average pace.** That is odd, but unlike the two rules above it is not
strictly impossible — the fastest 5 km inside a 5.4 km run really can be slower
than the whole thing, if the last 400 m was quick. 42 of the 49 are on runs less
than twice the split distance, which is where that argument applies.

One that has been fixed: eleven breakdown *paces* were truncated in the sheet.
`strava_runs!Q` and `!U` took a fixed four characters out of Strava's
`5:16/km`, one short of what `12:35/km` needs, so every pace of ten minutes or
more lost its last digit. They now cut at the `/`:

```
=IFERROR(LEFT(I2,SEARCH("/",I2)-1),"")
```

The dashboard never read that column — it derives pace from the split and the
distance — so nothing about the data changed, but the sheet is right now and
`run_test.py` keeps it that way.

```bash
python run_test.py
```

---

## The two that are not built

There are no workout or diet tables in `core/schema.sql`, deliberately. Guessing
at a schema for a tracker that has not been designed is expensive to undo once
there is data in it, and the run tracker shows what designed looks like — a
ladder of best efforts is a very particular shape, and it came from knowing what
the sheet held.

Each placeholder page lists what has to be decided first, and the order things
get built in. The sections already exist in `config.SECTIONS`, so both
front-ends already have the sidebar entry.

---

## How back-filling works

The Adjustments sheet was a scratch pad: you pasted the reading before the gap
above and the reading after it below, and it produced the missing days as a
straight line. For a gap of `n` days its formula was

```
day i of n  =  ROUND(previous + i × (next − previous) / (n + 1), 1)
```

That is exactly what `core/metrics.interpolate()` does, with three deliberate
differences.

**It rounds to each metric's own precision, not to 1dp across the board.** The
sheet's blanket `ROUND(...,1)` produced values like 1667.5 kcal and 6.5 visceral
fat, which no reading could ever be. New back-fills are whole numbers where the
metric is a whole number. (The sheet's one-day case used a bare `AVERAGE` with
no rounding at all, which is why 14% of the imported history carries two
decimal places.)

**It writes one reading, not two.** The sheet filled both weigh-in columns.
Interpolating a value and then calling it two independent measurements
overstates what is known: there is one estimate for that day, and it is flagged
as such. The daily average — which is all anything downstream uses — is
identical either way.

**Estimates are derived, not entered.** Every one sits between two real
readings and is recomputed whenever either of them changes. Enter the weigh-in
you forgot on Tuesday and Wednesday's estimate is redrawn on the spot; delete a
day and it reverts to an estimate; correct a run of entries and **Admin →
Rebuild** recomputes the lot. Real readings are never touched.

**Long gaps are left alone.** Four missed days on a straight line is a
reasonable estimate; four missed months is not, and inventing 120 rows would
quietly poison every average built on them. Gaps longer than
`PW_MAX_BACKFILL_DAYS` (60 by default) stay gaps, are reported on the Admin
page, and say so when you save.

---

## Reading values off the chart

Both front-ends give you the numbers on hover. Streamlit gets it from Altair;
the Flask weigh-in charts do it themselves, in about a hundred lines of
`web/static/chart-hover.js`. The run charts use SVG `<title>` elements instead —
a bar chart of twelve months does not need a crosshair.

The NAS's memory is a **server-side** constraint, and a readout runs in the
browser, so it costs the NAS nothing. What will not fit is a charting *library*
— Altair, Plotly and the rest all arrive with a Python stack attached, and
pandas alone is 85 MB.

- Pointer events rather than mouse events, so a finger works. On a phone the
  readout follows a drag across the chart; a vertical scroll fires
  `pointercancel` instead, so the page still scrolls normally.
- The charts are complete without it. Turn JavaScript off and you keep the
  lines, both axes, the ticks and the legend, and lose only the readout.
- **The readout cannot disagree with the axes.** Values are rounded in Python
  before they are sent, because JavaScript rounds halves away from zero and
  Python rounds them to even — averaging two whole numbers produces halves
  constantly, so 1667.5 kcal would otherwise read one way on the axis and
  another in the readout. `web_test.py` checks every value in every chart
  against `core.metrics`.

One thing to know: at **All + daily** the weigh-in charts page is around 1.6 MB,
because 2,276 points × 12 charts is a lot of coordinates however they are
encoded. It loads fine over Tailscale but it is not instant, and at four days
per pixel you cannot point at a specific day anyway — switch the averaging to
weekly or monthly for multi-year spans, which is what the selector is for. The
default 90-day view is about 60 KB.

---

## Tests

```bash
python reconcile_test.py    # every weigh-in figure against the workbook
python run_test.py          # every run figure against the Strava sheet
python smoke_test.py        # the weigh-in write path, especially back-filling
python web_test.py          # every Flask route, form and both navigations
python streamlit_test.py    # every Streamlit page, with data and without
python py39_check.py        # nothing on the NAS path needs Python 3.10
```

`web_test.py` also asserts that pandas, numpy and Streamlit are **not** imported
by the Flask front-end. That is not tidiness — pandas alone costs 85 MB, and the
NAS has around 76 MB free once the CD dashboard is running. Without the check,
an innocent-looking import would break the deployment silently.

`streamlit_test.py` renders every page twice, once against the real data and
once against an empty database, and then runs `app.py` itself. The last part is
not redundant: `st.Page` rejects a nested `url_path`, and a slug it refuses
takes down every page at once while each of them passes in isolation.

---

## How it is put together

```
config.py               sections, metric definitions, the breakdown ladder, paths
core/
  schema.sql            weigh-in tables, run tables, and the views over both
  db.py                 sqlite3 plumbing, no pandas anywhere near it
  metrics.py            weigh-ins: precision, validation, interpolation
  mutations.py          weigh-ins: saving, deleting, back-filling
  queries.py            weigh-ins: every series and every difference
  runs.py               runs: durations, paces, the ladder, validation
  run_mutations.py      runs: saving a run and its splits, atomically
  run_queries.py        runs: the splits by type, and the records
  excel_import.py       the weigh-in workbook -> SQLite
  strava_import.py      Final_data -> SQLite
  excel_export.py       SQLite -> .xlsx, on demand
web/                    Flask front-end
  app.py                the shell: factory, auth, filters, sidebar context
  nav.py                what the sidebar and the tab strip contain
  blueprints/           one module per section
  charts.py             hand-rolled SVG for the weigh-in charts
  run_charts.py         hand-rolled SVG for the run charts
  templates/            base.html, then one directory per section
views/                  Streamlit front-end
  frames.py             weigh-in queries -> DataFrames
  run_frames.py         run queries -> DataFrames
  altair_charts.py      the weigh-in charts
  run_charts.py         the run charts
  weigh_in/  runs/      the pages
  placeholders.py       the two sections that are not built
deploy/                 NAS scripts, and the container files that are not used
```

`core` never imports pandas, Streamlit or Altair. Both front-ends sit on top of
it, so the schema, the averaging, the interpolation and the ranking are shared
rather than written twice — the only difference is how they draw.

### The database

One file, `data/wellness.db`. It is the weigh-in tracker's schema with the run
tables added, so `data/weigh_ins.db` can simply be renamed and the run tables
appear alongside on first start.

**Weigh-ins.** `readings` holds what was entered: one row per weigh-in, keyed on
`(day, slot)`, with an `estimated` flag marking the back-filled ones. `v_daily`
averages the slots and derives the two masses. `v_weekly` and `v_monthly`
average that, grouped by ISO week (Monday start, matching the workbook's weeks,
which ran from Monday 25 May 2020) and by calendar month. Differences are window
functions over those views, not stored columns.

`rm_kcal` and `visceral_fat` are `REAL` rather than `INTEGER` even though the
scale reports them whole. The imported history contains half-values from the
sheet's own back-fills, and storing what was actually recorded beats silently
rewriting it; input precision is enforced on the way in instead.

**Runs.** `runs` holds one row per run, unique on `(day, distance_km,
duration_s)`, which is the identity the sheet can offer and what makes
re-importing safe. `run_bests` holds the ladder, keyed on `(run_id, breakdown)`
and cascading on delete. `v_runs` works out pace; `v_run_bests` works out pace
and flags the splits that contradict the run they came from.

Pace is never stored anywhere. It is duration over distance, and a stored copy
is one edit away from disagreeing with the two columns it came from.

The run views are `DROP`ped and recreated on every start rather than created
`IF NOT EXISTS`. A view is code, not data, and leaving an old definition in
place because the name already exists is how a database ends up running last
month's SQL against this month's tables.

### Configuration

Every path is an environment variable, so the same code runs unchanged here and
on the NAS. The `WI_` names the weigh-in tracker used are still read as a
fallback, so a half-migrated NAS does not silently point at the wrong database
or drop its password gate.

| | |
| --- | --- |
| `PW_DB_PATH` | where the SQLite file lives |
| `PW_EXCEL_DIR` | the source workbooks — `excel_versions/` by default |
| `PW_SOURCE_XLSX` | the weigh-in workbook, read-only, importer only |
| `PW_RUNS_XLSX` | the Strava workbook, read-only, importer only |
| `PW_RUNS_SHEET` | which tab of it to read — `Final_data` |
| `PW_EXPORT_DIR` | where .xlsx snapshots are written |
| `PW_APP_PASSWORD` | shared password; unset disables the gate |
| `PW_WEB_PORT` | Flask port, 8503 by default |
| `PW_MAX_BACKFILL_DAYS` | longest weigh-in gap that gets interpolated, 60 by default |

---

## Deploying

See [deploy/DEPLOY.md](deploy/DEPLOY.md). Short version: it runs on the Synology
NAS on port 8503 alongside the CD dashboard on 8501, sharing one venv, reachable
from a phone over Tailscale. The NAS cannot run Streamlit — not enough memory,
and DSM's Python is too old — which is why the Flask front-end exists.

It **replaces** the standalone weigh-in tracker on 8502 rather than joining it:
the weigh-in section here is that app, and running both would be two front doors
onto two copies of the same six years of readings. DEPLOY.md has the three
commands.
