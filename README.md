# Personal Wellness

Four trackers behind one sidebar, on one database, with two front-ends over the
top. The **weigh-in tracker** is the standalone app of the same name, moved in
whole; the **run tracker** is built from seven years of runs scraped off
Strava; the **workout tracker** is built from the gym programme workbook; the
**diet tracker** is built from the food planner workbook and two and a half
years of diary.

Built the same way as the CD dashboard next door: one shared `core` package, a
**Flask** front-end light enough to run on the NAS, and a **Streamlit** one for
a machine with memory to spare.

```bash
pip install -r requirements.txt
python -m core.excel_import --rebuild     # the weigh-in history, once
python -m core.strava_import --rebuild    # the runs, once
python -m core.gym_import                 # the gym programme, once
python -m core.food_import --catalogue    # the food lists, once
python serve.py                           # Flask, http://localhost:8503
streamlit run app.py                      # Streamlit, same data
```

All three importers need a desktop — openpyxl opening a 1.5 MB workbook needs far
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
| 🏋️ **Workout plan** | 19 weeks, 38 sessions, 564 sets. [What it does](#the-workout-tracker) |
| 🥗 **Diet tracker** | 2,065 diary lines over 290 days, 187 foods. [What it does](#the-diet-tracker) |

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

## The workout tracker

Built from `2026 Gym Programme.xlsx` — an 18-week powerlifting block plus a
deload week, three phases, a rotating pairing of lifts, and a tick per workout.
`python -m core.gym_import` folds it into one plan: **19 weeks, 38 sessions, 116
exercises, 564 sets**, with weeks 1–10 already ticked off.

### The shape

```
plan          a programme with a name — "2026 Gym Programme"
 phase        a stretch of weeks sharing a set/rep scheme and a working %
 week         numbered within the plan, belonging to one phase
  session     up to 10 exercises; two per week in the workbook
   exercise   one movement, in order, from the catalogue
    set       one line of the sheet: type, reps, weight, rest, cue
```

**A cycle is not a level.** The workbook's six-week rotation is six week shapes
that repeat, and the weeks are still numbered 1–19 — so a cycle is a pattern you
copy, which `weeks.cycle_type` labels, rather than a container that would then
have to be kept in step with the numbering. Build week 1, copy it into weeks 7
and 13, change the percentages.

### Prescribing a weight

Four ways, because the workbook uses all four:

| mode | column used | reads as |
| --- | --- | --- |
| `explicit` | `weight_kg` | `87.5 kg` |
| `percent` | `percent_1rm` | `65% — 62.5 kg` |
| `bodyweight` | `added_kg` | `Bodyweight`, `Bodyweight +10 kg` |
| `choose` | none | `Choose weight` |

Pull-Ups and Tricep Dips are main lifts in half the sessions and neither has a
1RM, which is why `bodyweight` exists; the accessories say *Choose weight* and
mean it, which is why `choose` does. A percentage of a bodyweight movement is
refused — it would be a percentage of nothing.

**The kilograms are worked out, never stored.** `v_exercise_sets` turns a
percentage into a weight: `% × 1RM`, rounded to the plan's step. That is the same
rule the run tracker follows for pace, and it matters more here than it looks,
because **the rounding has to happen in exactly one place**. SQLite's `ROUND` is
half-away-from-zero and Python's `round()` is half-to-even, so 61.25 kg at a
2.5 kg step is 62.5 to one and 60.0 to the other. `core.workouts.round_to()`
reproduces SQLite's rule for the form's preview, and `workout_test.py` asserts
the two agree — including on the exact half-way cases where they would not.

**1RMs belong to the plan, not to you.** Retest, start the next programme with
the new numbers, and last year's block still says what it said at the time. One
global 1RM per lift would silently restate history as a percentage of a max that
did not exist yet.

### Reps, and what they are counted in

Reps are a low and an optional high, so `10` and `10–12` are one column rather
than a number and a string that only one of them parses. Whether they are **per
side** and whether the weight is **per dumbbell** are properties of the movement
— a Bulgarian split squat is per leg wherever it appears — so they live on the
catalogue entry, with a nullable override on the session for the time it is done
the other way round.

That pair is also why the catalogue is asked for them at all: the sheet records
`10 each leg` in passing and says nothing whatsoever about the weight, so `27.5`
against an incline dumbbell press could be two 27.5s or one, and only you know.

### Phases

A phase carries defaults — warm-up percentages, working percentages, sets and
reps, rest per set type — and the session builder pre-fills from whichever phase
the week sits in. They are **defaults, not constraints**: once a set exists it
carries its own percentage, and editing the phase afterwards does not reach back
into sessions already built. A plan half-completed should not change shape
because a later phase was re-planned.

### The pages

| | |
| --- | --- |
| **Session** | One session, picked with three dropdowns. The landing page |
| **Plan** | Pick a plan, see its phases, 1RMs and weeks; copy it as a template |
| **Build** | The session builder, one session at a time |
| **Tracker** | Tick sessions off; the workbook's grid, a row per week |
| **Exercises** | The movement catalogue behind every dropdown |

**Session owns `/workouts/`** — the section's front door is the workout that is
due, not the machinery for planning one. It opens on the first session not
ticked off, the tick sits above the sets rather than below them, and moving
either of the other two dropdowns re-picks within the new choice rather than
landing on nothing.

That page and the two beside it are built for a 375px phone, which is where they
actually get read. A set is three columns and never more — what kind of set, how
many reps, what to put on the bar — with rest and the cue on a line underneath
where they cost no width; a phase is a dropdown and a list of labelled values
rather than seven columns; and the exercise catalogue is a card per movement
rather than a row whose name and three dropdowns fall off the side.

The builder takes one session at a time on purpose. Generating a whole cycle from
a form would need every variation expressed as parameters before you could see
any of it; building one and copying the week gets there faster and shows you what
you are getting.

**Historic plans** are the dropdown at the top of every page — every plan ever
saved, newest first, found by the name it was saved under. *Copy into a new plan*
reproduces the phases, weeks, sessions and sets, and the 1RMs if you want them.
It does **not** copy the tick-offs: a template is what you intend to do, and
inheriting last block's completed sessions would be a lie about this one.

### The catalogue

A closed list, for the same reason the run types are. **Retire** takes a movement
out of the dropdowns and leaves every plan using it reading exactly as it did;
**Delete** is only offered for the ones no session has ever used, because
anything else would leave a hole in a plan from two years ago.

### Getting a plan onto the NAS

A plan cannot follow either of the other sections' routes onto the NAS: the
importer reads a workbook, the workbooks are never pushed there, and openpyxl
opening one needs more memory than the DS218play has free. So a plan is built or
imported on a desktop and carried across with `deploy/send_plan.py`, which moves
only the workout tables, matches movements to the NAS catalogue **by name**
rather than by id, and reads every prescribed weight back afterwards to prove it
arrived. See [DEPLOY.md](deploy/DEPLOY.md).

### What the import checks rather than imports

The workbook's *Weight (kg)* column, wherever a percentage sits beside it. It is
`% × 1RM` rounded to 2.5 and nothing else, so importing it would be storing a
derived value. All 268 of those cells are recomputed and compared instead, and
the import says either *every weight matches* or lists the ones that do not.

---

## The diet tracker

Built from `Food Planner v0.1.xlsx`: a food catalogue, two and a half years of
diary, a weekly planner and a macro calculator. Sodium and sugar are **not**
imported — the workbook has columns for them and no data in them, so there are
four macros and only four: calories, carbs, fat and protein.

**Day** is the landing page and the workbook's DailyCheck sheet, with one change
that was the point of rebuilding it: it is addressed **by date**, not by week and
then day within it. The diary holds 48 of a possible 137 weeks, so counting from
the start is not a way of finding anything, and "what did I eat on the 12th" is
the question being asked anyway. The four totals, what is left of each and how
far through the target that is all sit above the editor.

**Week** is seven days starting on whichever day `config.WEEK_STARTS_ON` names.
The workbook ran Monday to Sunday and every "W/C" header in its diary is a
Monday, so that is the default — but which day a planning week turns over on is
a habit rather than a fact, and the picker changes the view without changing the
setting. *Fill the week* is the workbook's Planner in one button: set one day up
properly, then paste it across the rest. Days that already have entries are left
alone unless you say otherwise, which is what makes it safe to press twice.

**Calculator** is the workbook's Calculator sheet. Components in — either a
catalogue food and a quantity, or a name and four numbers off a packet — and the
total comes out, times a scaling factor. The scale applies to the **total**
rather than to any one row, because that is the question being asked: the pan of
pancake mix makes rather more than the 200 g that gets eaten. The answer can be
saved to the catalogue when it turns out to be a recipe rather than a one-off.

### Portions are the only arithmetic that can silently be wrong

A catalogue row records its macros *for* a portion — 266 kcal per 75 g of rice,
not per gram — so eating 150 g is twice it and 37.5 g is half. That division is
one function, `core.food.scale_macros()`, called from one place, and
`food_test.py` checks it against the foods whose portions are not 1. The Flask
day form previews the same arithmetic in JavaScript, and the server recomputes
it on save either way, so a browser with no scripting gets the same answer more
slowly rather than a different one.

### Macros are stored, not looked up

The opposite of the rule the run tracker follows for pace, and right here for a
reason the imported history makes plain: **the diary records what was eaten, the
catalogue records what a food is now, and the second changes.** Edit a recipe
because you started using less oil and every dinner from last year would quietly
restate itself. `food_id` still links a line to the catalogue where the name
matches, so a food's history can be followed; it is nullable because a meal out
is not in any list.

### Targets are named and dated

Named, so a training day and a rest day can want different things. Dated, so
raising today's protein target does not restate a day from 2024 as a failure
against a number that did not exist then — a day picks the newest version dated
on or before it, which is why the seeded profile starts in 2000. `v_food_days`
resolves that per day and exposes both `target_name` (what the day is measured
against) and `chosen_target` (what the day itself asked for, which for all 290
imported days is nothing).

### The diary came back through a CSV

The catalogue went straight in — 183 rows, all numeric, nothing to interpret.
The diary did not. `Food_Diary` is 49 week blocks of seven columns, and every
line of it is a *rendered string* — `"All Real bar - 1 Bar"` — rather than a food
and a quantity: 287 distinct strings against 182 catalogue entries, the
difference being spelling, portions written into names, and things eaten once.
Guessing would have quietly rewritten two years of history, so it was exported
to a CSV, corrected by hand and read back.

```bash
python -m core.food_import --catalogue        # the Food sheet -> foods
python -m core.food_import --export           # the diary -> a CSV to correct
python -m core.food_import --load FILE --replace
```

The export splits each string on the **last** `" - number units"` it finds, not
the first, or `"Nature Valley - Salted Caramel Nut - 1 Bar"` becomes a food
called *Nature Valley* eaten in units of *Salted Caramel Nut*. It writes the
untouched original beside its guess so a wrong one is visible rather than baked
in, and flags the rows worth a second look. The loader parses every row before
writing any, and reports **all** the bad lines rather than the first — correcting
a two-year diary is one pass through a spreadsheet, and being told about the
problems one at a time turns that into twenty.

It also accepts `dd/mm/yyyy`, because opening the export in Excel and saving it
rewrites every date. Which way round `03/08/2026` reads is decided by the
`weekday` column written beside it rather than by assuming a locale.

### What the workbook turned out to contain

`food_test.py` separates three things that a single comparison would hide:

**The load is faithful.** All 290 days match the CSV exactly.

**17 days were deliberately restated** by the corrections — dinners out
re-estimated onto one standing figure, a few recipes refreshed against current
macros, and a "Pizza Night" the workbook recorded as zero calories.

**Six of the workbook's own `Consumed` cells contradict the entry rows directly
above them** — a stale cached formula, off by as much as 910 kcal on 02/12/2024.
The entries are the record and the total is a formula, so the entries win; the
disagreement is pinned in the test rather than smoothed over.

Every other day reproduces the sheet's own arithmetic to the penny.

Two things in the history are worth knowing about and were left as they are.
`W/C 27/07/2026` appears **twice**; the fuller block (54 entries) is kept and the
import says so. And seventeen 2024 rice and pasta lines are recorded at a cooked
rate while every other one in the diary is dry — the same `"85 grams"` means dry
in fifteen rows and cooked in seventeen. Both are in the source, not in the
import.

### Getting the food onto the NAS

Same problem as a workout plan, same answer. The importer reads a workbook, the
workbooks are never pushed to the NAS, and openpyxl opening one needs more
memory than the DS218play has free — and pushing the whole database instead is
not an option once the dashboard is in use, because the NAS copy is the live one
and holds weigh-ins and runs entered from a phone that no desktop has seen.

`deploy/send_food.py` moves **only the four food tables**, matches foods to the
NAS catalogue on **(list, name)** rather than by id, re-links every diary line
to whatever the target's id turns out to be, and reads every day's four macro
totals back afterwards to prove it arrived. A food already on the NAS is left
alone unless you ask otherwise — a portion corrected on a phone should not be
undone by a push from a stale desktop — while a day is replaced, because a day
is saved wholesale everywhere else in this section. See
[DEPLOY.md](deploy/DEPLOY.md).

### Four extra catalogue rows

`config.FOOD_EXTRA_FOODS` holds four foods the corrected diary refers to and the
Food sheet never had. Two are ordinary — a Grenade bar eaten 167 times, and a
jasmine rice — and two are placeholders: **Dinner** covers 261 lines and
**Lunch** eight, nearly all of them meals out. Linking to a placeholder does not
restate anything, because a diary line carries its own macros and the link only
says what kind of thing it was; the 160 different dinners behind that label keep
their own figures.

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
python workout_test.py      # every set and weight against the gym workbook
python food_test.py         # every catalogue row and diary line against the food workbook
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
  food.py               food: macros, portions, weeks, validation, the calculator
  food_queries.py       food: days, weeks, the catalogue, the averages
  food_mutations.py     food: saving a day, planning a week, the catalogue
  food_import.py        the food workbook -> SQLite, and the diary CSV round trip
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
  weigh_in/ runs/ workouts/ diet/    the pages
  placeholders.py       kept for a section that has not been designed yet
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
