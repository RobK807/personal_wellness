-- Schema for the personal wellness dashboard.
--
-- One database, one section at a time. The weigh-in tables come first and are
-- unchanged from the standalone tracker, so its data/weigh_ins.db can simply be
-- renamed and the rest appear alongside it on first start. Then the run tables,
-- then the workout ones, then the food ones. Each section was designed from the
-- workbook it replaces rather than guessed at, which is why they arrived in that
-- order and why none of them is a general-purpose shape.

PRAGMA foreign_keys = ON;


-- ===========================================================================
-- WEIGH-IN TRACKER
--
-- Replaces the workbook's Data sheet (the readings) and every sheet derived
-- from it. Summary, Weekly Results, Monthly average and the three "change"
-- sheets were only ever formulas over Data, so none of them is a table here:
-- they are the views below plus the window functions in core/queries.py.
-- ===========================================================================

-- ---------------------------------------------------------------------------
-- The readings themselves: two per day, exactly as the Data sheet held them
-- (columns B-G were weigh-in 1, H-M weigh-in 2).
--
-- Stored as REAL rather than INTEGER for rm_kcal and visceral_fat even though
-- the scale reports them whole. Two reasons: the imported history contains
-- half-values from back-fills the workbook rounded to 1dp, and storing what
-- was actually recorded beats silently rewriting it. Input precision is
-- enforced on the way in instead - see core/metrics.py.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS readings (
    day                 TEXT    NOT NULL,          -- ISO date, 'YYYY-MM-DD'
    slot                INTEGER NOT NULL CHECK (slot IN (1, 2)),
    weight              REAL    NOT NULL,
    bmi                 REAL    NOT NULL,
    body_fat_pct        REAL    NOT NULL,
    skeletal_muscle_pct REAL    NOT NULL,
    rm_kcal             REAL    NOT NULL,
    visceral_fat        REAL    NOT NULL,
    -- 1 = linear back-fill for a day that was missed, not a real weigh-in.
    -- These are recomputed whenever a neighbouring real reading changes, so
    -- the flag is also what makes them safe to throw away and rebuild.
    estimated           INTEGER NOT NULL DEFAULT 0 CHECK (estimated IN (0, 1)),
    note                TEXT,
    created_at          TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at          TEXT    NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (day, slot)
);

CREATE INDEX IF NOT EXISTS ix_readings_day       ON readings (day);
CREATE INDEX IF NOT EXISTS ix_readings_estimated ON readings (estimated);

-- ---------------------------------------------------------------------------
-- Append-only trail of every change, so a mistyped weight entered from a phone
-- is traceable. The workbook had no equivalent.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS audit_log (
    id        INTEGER PRIMARY KEY,
    ts        TEXT NOT NULL DEFAULT (datetime('now')),
    action    TEXT NOT NULL,
    entity    TEXT,
    entity_id TEXT,
    detail    TEXT
);

-- ---------------------------------------------------------------------------
-- The daily figures - the whole of the Summary sheet.
--
-- The workbook averaged the two weigh-ins with =(B+H)/2, which quietly assumes
-- both are present. AVG() over the rows that exist is the same answer when
-- they are, and the right answer when only one weigh-in was taken.
--
-- Columns T and U of Data become body_fat_kg / muscle_kg: the percentages
-- applied to the averaged weight, not the average of two separately derived
-- masses. That is what the sheet did (=N*P/100) and the two differ slightly.
--
-- ROUND(..., 6) is not a display choice - it clears binary-float dust such as
-- 14.299999999999999 from (14.1 + 14.5) / 2, which would otherwise show up in
-- comparisons and test assertions.
-- ---------------------------------------------------------------------------
CREATE VIEW IF NOT EXISTS v_daily AS
SELECT
    day,
    ROUND(AVG(weight), 6)                                       AS weight,
    ROUND(AVG(bmi), 6)                                          AS bmi,
    ROUND(AVG(body_fat_pct), 6)                                 AS body_fat_pct,
    ROUND(AVG(skeletal_muscle_pct), 6)                          AS skeletal_muscle_pct,
    ROUND(AVG(rm_kcal), 6)                                      AS rm_kcal,
    ROUND(AVG(visceral_fat), 6)                                 AS visceral_fat,
    ROUND(AVG(weight) * AVG(body_fat_pct) / 100.0, 6)           AS body_fat_kg,
    ROUND(AVG(weight) * AVG(skeletal_muscle_pct) / 100.0, 6)    AS muscle_kg,
    COUNT(*)                                                    AS readings,
    -- A day counts as estimated only if nothing real was recorded on it.
    MIN(estimated)                                              AS estimated
FROM readings
GROUP BY day;

-- ---------------------------------------------------------------------------
-- Weekly averages - the Weekly Results sheet.
--
-- The sheet's weeks ran from Monday 25 May 2020 in fixed 7-day steps, so
-- ISO weeks (Monday start) reproduce them exactly. `date(day,'-6 days',
-- 'weekday 1')` is the Monday of the week containing `day`: step back six days
-- so a Monday cannot jump forward, then take the next Monday.
--
-- A part-finished week averages the days recorded so far, which is what the
-- sheet's MIN(TODAY()-start+1, 7) window did.
-- ---------------------------------------------------------------------------
CREATE VIEW IF NOT EXISTS v_weekly AS
SELECT
    date(day, '-6 days', 'weekday 1')                AS period,
    ROUND(AVG(weight), 6)                            AS weight,
    ROUND(AVG(bmi), 6)                               AS bmi,
    ROUND(AVG(body_fat_pct), 6)                      AS body_fat_pct,
    ROUND(AVG(skeletal_muscle_pct), 6)               AS skeletal_muscle_pct,
    ROUND(AVG(rm_kcal), 6)                           AS rm_kcal,
    ROUND(AVG(visceral_fat), 6)                      AS visceral_fat,
    ROUND(AVG(body_fat_kg), 6)                       AS body_fat_kg,
    ROUND(AVG(muscle_kg), 6)                         AS muscle_kg,
    COUNT(*)                                         AS days,
    SUM(estimated)                                   AS estimated_days
FROM v_daily
GROUP BY period;

-- ---------------------------------------------------------------------------
-- Monthly averages - the Monthly average sheet, which was SUMIFS/COUNTIFS over
-- Summary between the first of one month and the first of the next.
-- ---------------------------------------------------------------------------
CREATE VIEW IF NOT EXISTS v_monthly AS
SELECT
    date(day, 'start of month')                      AS period,
    ROUND(AVG(weight), 6)                            AS weight,
    ROUND(AVG(bmi), 6)                               AS bmi,
    ROUND(AVG(body_fat_pct), 6)                      AS body_fat_pct,
    ROUND(AVG(skeletal_muscle_pct), 6)               AS skeletal_muscle_pct,
    ROUND(AVG(rm_kcal), 6)                           AS rm_kcal,
    ROUND(AVG(visceral_fat), 6)                      AS visceral_fat,
    ROUND(AVG(body_fat_kg), 6)                       AS body_fat_kg,
    ROUND(AVG(muscle_kg), 6)                         AS muscle_kg,
    COUNT(*)                                         AS days,
    SUM(estimated)                                   AS estimated_days
FROM v_daily
GROUP BY period;


-- ===========================================================================
-- RUN TRACKER
--
-- The Final_data sheet of strava_runs.xlsx is one row per (run, breakdown):
-- nine runs with a 5K split produce nine rows repeating the same date,
-- distance, time, pace, run type and effort type. That is a join flattened for
-- a spreadsheet, and it is split back into its two tables here - `runs` for
-- what the run was, `run_bests` for the ladder of best efforts inside it.
-- ===========================================================================

-- ---------------------------------------------------------------------------
-- The values the Run type and Effort type dropdowns offer, in the order they
-- offer them.
--
-- In the database rather than in config.py because they are data, not code: a
-- new kind of session should be something added on the Admin page, not
-- something that needs a code edit, a push to the NAS and a restart. config.py
-- keeps a list of each, but only as the seed for a database that has none -
-- see core/db.py _seed_run_options, which also picks up anything the imported
-- runs already use.
--
-- `runs.run_type` and `runs.effort_type` stay plain TEXT rather than becoming
-- foreign keys into this. A key would be the tidier schema and the wrong
-- trade: the runs are the record of what happened and this is a convenience
-- for a form, and the day the two disagree it should be the dropdown that is
-- wrong, not the history. core/run_options.py holds the rule that keeps them
-- in step - an option in use cannot be removed.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS run_options (
    kind     TEXT    NOT NULL CHECK (kind IN ('run_type', 'effort_type')),
    value    TEXT    NOT NULL CHECK (length(trim(value)) > 0),
    position INTEGER NOT NULL,
    PRIMARY KEY (kind, value)
);

-- ---------------------------------------------------------------------------
-- One row per run.
--
-- `duration_s` rather than a time string: the sheet stored elapsed time as an
-- Excel time-of-day, which is fine until a run passes 24 hours or you want to
-- add two of them together. Seconds sort, sum and average correctly, and the
-- mm:ss and h:mm:ss shapes are put back on in core/runs.py.
--
-- Pace is not stored. It is duration over distance and nothing else, and a
-- stored copy is one edit away from disagreeing with the two columns it is
-- derived from - see v_runs.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY,
    day         TEXT    NOT NULL,          -- ISO date, 'YYYY-MM-DD'
    distance_km REAL    NOT NULL CHECK (distance_km > 0),
    duration_s  INTEGER NOT NULL CHECK (duration_s > 0),
    run_type    TEXT    NOT NULL,
    effort_type TEXT    NOT NULL,
    -- Free text, and the only field the sheet did not have. The run's Strava
    -- name lives here when the importer can find one.
    note        TEXT,
    source      TEXT    NOT NULL DEFAULT 'manual',  -- 'manual' or 'strava'

    -- ---- structured sessions ------------------------------------------------
    -- Null on every run that is not an interval session, which is almost all of
    -- them. Held here rather than in a table of their own because there is at
    -- most one of these per run: a separate table would be a one-to-one join
    -- that could only ever go wrong.
    --
    -- `interval_type` says how the session was set, and so which of the two
    -- lengths below applies: 8 x 1k prescribes the kilometre and has no time
    -- per rep, 6 x 3:00 prescribes the three minutes and has no distance per
    -- rep. Exactly one of them is filled in, and the other is refused on the
    -- way in rather than quietly stored against a session it means nothing for.
    --
    -- `interval_pace_s` is entered, not derived, and it is the one figure that
    -- applies to both kinds. This is the only stored pace in the database, and
    -- the exception is deliberate: for a session set by time there is no
    -- distance to divide by, so there is nothing to derive it from. Ten-second
    -- sprints have a time of 0:10 and a pace of something like 3:00/km, and no
    -- arithmetic available here connects the two. Nothing else stores the same
    -- fact, so there is no second copy for it to drift from - which is the
    -- reason pace is derived everywhere else, not a preference for division.
    interval_type       TEXT    CHECK (interval_type IN ('distance', 'time')),
    interval_count      INTEGER CHECK (interval_count > 0),
    interval_distance_m REAL    CHECK (interval_distance_m > 0),
    interval_time_s     INTEGER CHECK (interval_time_s > 0),
    interval_pace_s     INTEGER CHECK (interval_pace_s > 0),

    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT    NOT NULL DEFAULT (datetime('now')),

    -- Only the length matching the type may be set. A table constraint rather
    -- than a column one because it reads two columns, which is also why a
    -- database brought forward with ALTER TABLE ADD COLUMN cannot carry it -
    -- core/runs.py enforces the same rule on every write, which is where a bad
    -- value would actually come from.
    CHECK (interval_type IS NOT 'distance' OR interval_time_s IS NULL),
    CHECK (interval_type IS NOT 'time'     OR interval_distance_m IS NULL)
);

-- What makes two rows the same run. The sheet has no activity id in Final_data,
-- so date + distance + elapsed time is the identity it can offer - and it is a
-- good one: three runs on 07/06/2026 are distinguishable, and re-importing the
-- workbook updates the existing rows rather than doubling them.
CREATE UNIQUE INDEX IF NOT EXISTS ux_runs_identity
    ON runs (day, distance_km, duration_s);

CREATE INDEX IF NOT EXISTS ix_runs_day     ON runs (day);
CREATE INDEX IF NOT EXISTS ix_runs_type    ON runs (run_type);
CREATE INDEX IF NOT EXISTS ix_runs_effort  ON runs (effort_type);

-- ---------------------------------------------------------------------------
-- The Breakdown columns: the fastest 400m, 1K, 5K and so on found inside a run.
--
-- `km` is denormalised rather than joined from a ladder table, and `ordinal` is
-- the rung's position in it. Both come from config.BREAKDOWNS at write time.
-- The alternative - a reference table seeded from the same list - would put the
-- ladder in two places, and this way the pace views need no join at all.
--
-- A run carries only the rungs it was long enough to reach, so this table is
-- sparse by design: 400m appears against every run, the half-marathon against
-- eleven. The four runs the sheet has no breakdown for simply have no rows.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS run_bests (
    run_id    INTEGER NOT NULL REFERENCES runs (id) ON DELETE CASCADE,
    breakdown TEXT    NOT NULL,            -- '400m', '1K', 'Half-Marathon', ...
    ordinal   INTEGER NOT NULL,            -- position in the ladder, for sorting
    km        REAL    NOT NULL CHECK (km > 0),
    seconds   INTEGER NOT NULL CHECK (seconds > 0),
    PRIMARY KEY (run_id, breakdown)
);

CREATE INDEX IF NOT EXISTS ix_bests_breakdown ON run_bests (breakdown, seconds);

-- The run views are dropped and recreated rather than created IF NOT EXISTS.
-- A view is code, not data: leaving an old definition in place because the name
-- already exists is how a database ends up running last month's SQL against
-- this month's tables. It costs nothing - there is nothing in a view to rebuild
-- - and it means editing the SQL below is enough to change what the app sees.
--
-- The weigh-in views above keep IF NOT EXISTS, unchanged from the standalone
-- tracker, so an existing database is not touched by opening it here.
DROP VIEW IF EXISTS v_runs;
DROP VIEW IF EXISTS v_run_bests;

-- ---------------------------------------------------------------------------
-- A run with its pace worked out, plus how many rungs of the ladder it reached.
--
-- Pace is seconds per kilometre, kept as a float here and formatted as mm:ss
-- on the way out. Rounding it to the whole second in the view would make
-- "average pace across these runs" the average of eleven rounded numbers.
-- ---------------------------------------------------------------------------
CREATE VIEW v_runs AS
SELECT
    r.id,
    r.day,
    r.distance_km,
    r.duration_s,
    r.duration_s / r.distance_km                             AS pace_s,
    r.run_type,
    r.effort_type,
    r.note,
    r.source,
    r.interval_type,
    r.interval_count,
    r.interval_distance_m,
    r.interval_time_s,
    r.interval_pace_s,
    -- How much of the run was actually the session rather than warm-up,
    -- recovery and warm-down. Which of the two applies follows the type, for
    -- the same reason the two lengths do: a session set by time has no
    -- distance per rep to add up, and one set by distance has no time per rep.
    -- Multiplying out what was entered, never dividing one entry by another.
    CASE WHEN r.interval_count IS NOT NULL
              AND r.interval_distance_m IS NOT NULL
         THEN r.interval_count * r.interval_distance_m / 1000.0
    END                                                      AS interval_total_km,
    CASE WHEN r.interval_count IS NOT NULL
              AND r.interval_time_s IS NOT NULL
         THEN r.interval_count * r.interval_time_s
    END                                                      AS interval_total_s,
    (SELECT COUNT(*) FROM run_bests b WHERE b.run_id = r.id) AS breakdowns
FROM runs r;

-- ---------------------------------------------------------------------------
-- Every best effort, carrying the run it came from - what the records page
-- ranks and what the per-distance analysis averages.
--
-- `suspect` marks a split that cannot be true, and there are two ways to be:
--
--   * longer than the whole run it sits inside;
--   * quicker than a shorter split of the same run.
--
-- Neither currently catches anything. Eighteen splits of the first kind came
-- across from the scrape and were corrected at source on 12/08/2026, by
-- putting the runs' elapsed times right; one of the second kind was corrected
-- the day before.
--
-- The rules stay. They are what core/runs.py parse_breakdowns() enforces on
-- anything entered by hand, and a refreshed scrape that reintroduces one
-- should surface on the Admin page rather than at the top of a records table.
-- Anything they do catch is kept - the run happened, and quietly dropping rows
-- would leave the dashboard holding less than the sheet it was built from -
-- but held out of the rankings and the per-distance averages, because a table
-- of bests should only contain times that could have happened.
-- core.run_queries.anomalies() lists them on the Admin page.
--
-- Nothing entered through the input form can add to either category; see
-- core/runs.py parse_breakdowns().
-- ---------------------------------------------------------------------------
CREATE VIEW v_run_bests AS
SELECT
    b.run_id,
    r.day,
    r.distance_km,
    r.duration_s,
    r.run_type,
    r.effort_type,
    r.note,
    b.breakdown,
    b.ordinal,
    b.km,
    b.seconds,
    b.seconds / b.km    AS pace_s,
    CASE
        WHEN b.seconds > r.duration_s THEN 1
        WHEN EXISTS (SELECT 1 FROM run_bests inner_b
                     WHERE inner_b.run_id = b.run_id
                       AND inner_b.ordinal < b.ordinal
                       AND inner_b.seconds > b.seconds) THEN 1
        ELSE 0
    END                 AS suspect
FROM run_bests b
JOIN runs r ON r.id = b.run_id;


-- ===========================================================================
-- WORKOUT PLAN AND TRACKER
--
-- Built from `2026 Gym Programme.xlsx`, which is one sheet per week plus a
-- Programme Overview holding the 1RMs, the phases and the rotating pairings.
-- That workbook is the shape this has to reproduce, so it is worth naming what
-- it actually is before the tables make sense:
--
--     plan          a programme with a name - "2026 Gym Programme"
--      phase        a stretch of weeks sharing a set/rep scheme and a working %
--      week         numbered within the plan, belonging to one phase
--       session     up to 10 exercises; two per week in the workbook
--        exercise   one movement, in order, from the catalogue
--         set       one line of the sheet: type, reps, weight, rest, cue
--
-- A "cycle" is not a level here. The workbook's six-week rotation is six week
-- shapes that repeat, and the weeks are still numbered 1..19 - so a cycle is a
-- pattern you copy, which `weeks.cycle_type` labels, rather than a container
-- that would then have to be kept in step with the numbering.
-- ===========================================================================

-- ---------------------------------------------------------------------------
-- The exercise catalogue: what the dropdown offers.
--
-- The same closed-list idea as run_options, for the same reason - a movement
-- typed freely three different ways is three movements as far as any total is
-- concerned - but a table rather than a list of strings, because an exercise
-- carries facts about itself.
--
-- `reps_mode` and `weight_mode` are among those facts: a Bulgarian split squat
-- is per leg wherever it appears, and a dumbbell curl is per dumbbell. A
-- session may still override either, which is what the nullable pair on
-- session_exercises is for.
--
-- `is_bodyweight` marks the movements with no 1RM to take a percentage of.
-- Pull-Ups and Tricep Dips are main lifts in half the workbook's sessions and
-- neither has a number in its 1RM table; they progress by added weight.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS exercises (
    id            INTEGER PRIMARY KEY,
    name          TEXT    NOT NULL UNIQUE CHECK (length(trim(name)) > 0),
    reps_mode     TEXT    NOT NULL DEFAULT 'total'
                          CHECK (reps_mode IN ('total', 'per_side')),
    weight_mode   TEXT    NOT NULL DEFAULT 'total'
                          CHECK (weight_mode IN ('total', 'per_dumbbell')),
    is_bodyweight INTEGER NOT NULL DEFAULT 0 CHECK (is_bodyweight IN (0, 1)),
    position      INTEGER NOT NULL,
    retired       INTEGER NOT NULL DEFAULT 0 CHECK (retired IN (0, 1)),
    note          TEXT,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- ---------------------------------------------------------------------------
-- One programme.
--
-- `rounding_kg` is the increment a prescribed weight is rounded to - 2.5 in the
-- workbook, and every one of its percentages reproduces exactly at that step.
-- Per plan because it is a property of the plates in the gym you are in.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS plans (
    id          INTEGER PRIMARY KEY,
    name        TEXT    NOT NULL UNIQUE CHECK (length(trim(name)) > 0),
    started_on  TEXT,                                  -- ISO date, optional
    rounding_kg REAL    NOT NULL DEFAULT 2.5 CHECK (rounding_kg > 0),
    note        TEXT,
    archived    INTEGER NOT NULL DEFAULT 0 CHECK (archived IN (0, 1)),
    source      TEXT    NOT NULL DEFAULT 'manual',     -- 'manual' or 'xlsx'
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- ---------------------------------------------------------------------------
-- The 1RMs, held per plan rather than once globally.
--
-- A plan's prescribed weights have to stay where they were put. Retest, start
-- the next programme with the new numbers, and last year's block still says
-- what it said at the time - which one global 1RM per lift would silently
-- rewrite the moment it changed, quietly restating history as a percentage of
-- a max that did not exist yet.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS plan_maxes (
    plan_id     INTEGER NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
    exercise_id INTEGER NOT NULL REFERENCES exercises(id),
    one_rm_kg   REAL    NOT NULL CHECK (one_rm_kg > 0),
    PRIMARY KEY (plan_id, exercise_id)
);

-- ---------------------------------------------------------------------------
-- A phase: the stretch of weeks sharing a scheme.
--
-- The percentage lists are JSON arrays - '[0.5, 0.7]' - because they are a
-- short ordered list, read and written whole, and a table of one number per row
-- would be four joins to answer "what does a Phase 1 warm-up look like".
--
-- Everything here is a **default the session builder pre-fills from**, never a
-- constraint on what a session may hold: once a set exists it carries its own
-- percentage, and editing the phase afterwards does not reach back into it.
-- That is deliberate. A plan half-completed should not change shape because a
-- later phase was re-planned.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS phases (
    id             INTEGER PRIMARY KEY,
    plan_id        INTEGER NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
    name           TEXT    NOT NULL CHECK (length(trim(name)) > 0),
    focus          TEXT,
    position       INTEGER NOT NULL,
    warmup_pcts    TEXT,        -- JSON, e.g. '[0.5, 0.7]'
    working_pcts   TEXT,        -- JSON, e.g. '[0.65]' or '[0.87, 0.92, 0.97]'
    working_sets   INTEGER CHECK (working_sets IS NULL OR working_sets > 0),
    working_reps   TEXT,        -- '10' or '10-12'
    accessory_sets INTEGER CHECK (accessory_sets IS NULL OR accessory_sets > 0),
    accessory_reps TEXT,
    rest_warmup    TEXT,
    rest_working   TEXT,
    rest_accessory TEXT,
    UNIQUE (plan_id, name)
);

-- ---------------------------------------------------------------------------
-- A week. Numbered within the plan; `label` is for the ones that are not just a
-- number, like Deload. `cycle_type` is the workbook's A/B rotation, kept as a
-- label so the pattern is visible without being enforced.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS weeks (
    id         INTEGER PRIMARY KEY,
    plan_id    INTEGER NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
    number     INTEGER NOT NULL CHECK (number > 0),
    label      TEXT,
    phase_id   INTEGER REFERENCES phases(id) ON DELETE SET NULL,
    cycle_type TEXT,
    note       TEXT,            -- the sheet's per-week coaching notes
    UNIQUE (plan_id, number)
);

-- ---------------------------------------------------------------------------
-- A session. Up to ten exercises, which is the brief; the workbook uses two
-- main lifts, or one main lift and three accessories.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sessions (
    id      INTEGER PRIMARY KEY,
    week_id INTEGER NOT NULL REFERENCES weeks(id) ON DELETE CASCADE,
    number  INTEGER NOT NULL CHECK (number BETWEEN 1 AND 10),
    name    TEXT,               -- blank means "name it after its main lifts"
    note    TEXT,
    UNIQUE (week_id, number)
);

-- ---------------------------------------------------------------------------
-- One movement inside a session, in order.
--
-- The two mode columns are NULL almost always, meaning "whatever the catalogue
-- says". They exist for the movement done the other way round this once, so
-- that recording it does not mean redefining the exercise everywhere it has
-- ever appeared.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS session_exercises (
    id          INTEGER PRIMARY KEY,
    session_id  INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    exercise_id INTEGER NOT NULL REFERENCES exercises(id),
    position    INTEGER NOT NULL CHECK (position BETWEEN 1 AND 10),
    reps_mode   TEXT CHECK (reps_mode IS NULL
                            OR reps_mode IN ('total', 'per_side')),
    weight_mode TEXT CHECK (weight_mode IS NULL
                            OR weight_mode IN ('total', 'per_dumbbell')),
    note        TEXT,
    UNIQUE (session_id, position)
);

-- ---------------------------------------------------------------------------
-- One line of a week sheet.
--
-- `load_mode` is the four ways that workbook prescribes a weight, and each one
-- uses exactly one of the three columns after it:
--
--     explicit     weight_kg      "87.5"
--     percent      percent_1rm    "0.65" -> 65% of the plan's 1RM, rounded
--     bodyweight   added_kg       "Bodyweight", or "+10 kg added"
--     choose       none           "Choose weight" / "Light weight"
--
-- Reps are a low and an optional high, so that '10' and '10-12' are the same
-- column rather than a number and a string that only one of them parses.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS exercise_sets (
    id                  INTEGER PRIMARY KEY,
    session_exercise_id INTEGER NOT NULL
                        REFERENCES session_exercises(id) ON DELETE CASCADE,
    set_type            TEXT    NOT NULL
                        CHECK (set_type IN ('warmup', 'working', 'accessory')),
    position            INTEGER NOT NULL CHECK (position > 0),
    reps_low            INTEGER CHECK (reps_low IS NULL OR reps_low > 0),
    reps_high           INTEGER CHECK (reps_high IS NULL OR reps_high > 0),
    load_mode           TEXT    NOT NULL CHECK (load_mode IN
                            ('explicit', 'percent', 'bodyweight', 'choose')),
    weight_kg           REAL    CHECK (weight_kg IS NULL OR weight_kg >= 0),
    percent_1rm         REAL    CHECK (percent_1rm IS NULL
                                    OR (percent_1rm > 0 AND percent_1rm <= 2)),
    added_kg            REAL    CHECK (added_kg IS NULL OR added_kg >= 0),
    rest                TEXT,
    cue                 TEXT,
    UNIQUE (session_exercise_id, set_type, position),
    CHECK (reps_high IS NULL OR reps_low IS NULL OR reps_high >= reps_low)
);

-- ---------------------------------------------------------------------------
-- The tracker: one row per session actually done.
--
-- A row rather than a flag on `sessions`, so "not done yet" is the absence of a
-- record instead of a zero that would have to be written for all thirty-eight
-- sessions of a nineteen-week plan before any of them happened. The workbook
-- ticked a whole workout at a time and this is the same grain.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS session_log (
    session_id INTEGER PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
    done_on    TEXT    NOT NULL,          -- ISO date
    note       TEXT,
    created_at TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_weeks_plan    ON weeks(plan_id, number);
CREATE INDEX IF NOT EXISTS idx_sessions_week ON sessions(week_id, number);
CREATE INDEX IF NOT EXISTS idx_sx_session    ON session_exercises(session_id, position);
CREATE INDEX IF NOT EXISTS idx_sets_sx       ON exercise_sets(session_exercise_id, set_type, position);

-- ---------------------------------------------------------------------------
-- Every prescribed set, with its weight worked out.
--
-- This is the one place a percentage becomes kilograms, and it is here rather
-- than in Python for a reason worth stating: rounding half-way cases has to
-- happen once. SQLite's ROUND is half-away-from-zero and Python's round() is
-- half-to-even, so 61.25 kg at a 2.5 step is 62.5 to one and 60.0 to the other.
-- Two implementations would disagree on exactly the weights a lifter notices.
-- core/workouts.py round_to() reproduces this rule for the input form's
-- preview, and workout_test.py asserts the two agree.
--
-- `weight_kg` on the row is what an explicit set holds; `prescribed_kg` is what
-- to actually put on the bar, whichever mode was used, and NULL when the answer
-- is genuinely "your choice" or the 1RM has not been entered yet.
-- ---------------------------------------------------------------------------
DROP VIEW IF EXISTS v_exercise_sets;
CREATE VIEW v_exercise_sets AS
SELECT
    s.id,
    s.session_exercise_id,
    sx.session_id,
    sx.position                                        AS exercise_position,
    se.week_id,
    w.plan_id,
    w.number                                           AS week_number,
    se.number                                          AS session_number,
    e.id                                               AS exercise_id,
    e.name                                             AS exercise_name,
    e.is_bodyweight,
    COALESCE(sx.reps_mode, e.reps_mode)                AS reps_mode,
    COALESCE(sx.weight_mode, e.weight_mode)            AS weight_mode,
    s.set_type,
    s.position,
    s.reps_low,
    s.reps_high,
    s.load_mode,
    s.weight_kg,
    s.percent_1rm,
    s.added_kg,
    s.rest,
    s.cue,
    m.one_rm_kg,
    p.rounding_kg,
    CASE s.load_mode
        WHEN 'explicit'   THEN s.weight_kg
        WHEN 'bodyweight' THEN s.added_kg
        WHEN 'percent'    THEN
            CASE WHEN m.one_rm_kg IS NULL THEN NULL
                 ELSE ROUND(s.percent_1rm * m.one_rm_kg / p.rounding_kg)
                      * p.rounding_kg
            END
    END                                                AS prescribed_kg
FROM exercise_sets s
JOIN session_exercises sx ON sx.id = s.session_exercise_id
JOIN sessions se          ON se.id = sx.session_id
JOIN weeks w              ON w.id  = se.week_id
JOIN plans p              ON p.id  = w.plan_id
JOIN exercises e          ON e.id  = sx.exercise_id
LEFT JOIN plan_maxes m    ON m.plan_id = w.plan_id AND m.exercise_id = e.id;

-- ---------------------------------------------------------------------------
-- Every session, with what is in it and whether it has been done.
-- ---------------------------------------------------------------------------
DROP VIEW IF EXISTS v_sessions;
CREATE VIEW v_sessions AS
SELECT
    se.id,
    se.week_id,
    se.number,
    se.name,
    se.note,
    w.plan_id,
    w.number                                    AS week_number,
    w.label                                     AS week_label,
    w.cycle_type,
    w.note                                      AS week_note,
    ph.name                                     AS phase_name,
    ph.position                                 AS phase_position,
    p.name                                      AS plan_name,
    (SELECT COUNT(*) FROM session_exercises sx
      WHERE sx.session_id = se.id)              AS exercises,
    (SELECT COUNT(*) FROM exercise_sets s
      JOIN session_exercises sx ON sx.id = s.session_exercise_id
      WHERE sx.session_id = se.id)              AS sets,
    -- What the session is, in the workbook's own shorthand: the movements that
    -- carry working sets, which is what makes "Bench Press + Squats" the name
    -- and leaves the three accessories out of it.
    (SELECT group_concat(name, ' + ') FROM
       (SELECT e.name AS name FROM session_exercises sx
          JOIN exercises e ON e.id = sx.exercise_id
         WHERE sx.session_id = se.id
           AND EXISTS (SELECT 1 FROM exercise_sets s
                        WHERE s.session_exercise_id = sx.id
                          AND s.set_type = 'working')
         ORDER BY sx.position))                 AS main_lifts,
    l.done_on,
    l.note                                      AS done_note,
    CASE WHEN l.session_id IS NULL THEN 0 ELSE 1 END AS done
FROM sessions se
JOIN weeks w           ON w.id = se.week_id
JOIN plans p           ON p.id = w.plan_id
LEFT JOIN phases ph    ON ph.id = w.phase_id
LEFT JOIN session_log l ON l.session_id = se.id;

-- ---------------------------------------------------------------------------
-- Every plan, with how big it is and how far through it you are.
-- ---------------------------------------------------------------------------
DROP VIEW IF EXISTS v_plans;
CREATE VIEW v_plans AS
SELECT
    p.*,
    (SELECT COUNT(*) FROM weeks w WHERE w.plan_id = p.id)      AS weeks,
    (SELECT COUNT(*) FROM phases ph WHERE ph.plan_id = p.id)   AS phases,
    (SELECT COUNT(*) FROM v_sessions v WHERE v.plan_id = p.id) AS sessions,
    (SELECT COUNT(*) FROM v_sessions v
      WHERE v.plan_id = p.id AND v.done = 1)                   AS sessions_done,
    (SELECT MAX(done_on) FROM v_sessions v WHERE v.plan_id = p.id) AS last_done
FROM plans p;


-- ===========================================================================
-- FOOD PLANNER AND DIARY
--
-- Built from `Food Planner v0.1.xlsx`: a Planner sheet that lays out one day,
-- a Food_Diary that keeps 49 weeks of them side by side, a Food sheet holding
-- the catalogue, and a DailyCheck for going back over a day already recorded.
--
--     food                 one thing you can eat, with its macros per portion
--     macro_target         a named set of macros, from a date - "Base" etc
--     food_day             one day: which target applies, and any note
--      food_entry          one line of it: meal, what, how much, its macros
--
-- Four macros and no more. The workbook carried sodium and sugar columns and
-- they are deliberately not here - see the note on food_entries about why the
-- macros are stored rather than derived, which is the only place that decision
-- has any teeth.
-- ===========================================================================

-- ---------------------------------------------------------------------------
-- The catalogue: everything with macros attached.
--
-- `list` is the workbook's three-way split and it is kept, because it is how
-- the thing is looked up rather than a category of food: Items are single
-- things, Meals are bought or assembled, Recipes are cooked. `grouping` is the
-- sub-heading within a list - Snack, Dessert, Dinner - and the two are not
-- interchangeable, which is why both are here.
--
-- Unique on (list, name) rather than on name: the workbook has "Mashed potato"
-- twice, once as an Item and once as a Recipe, and they are different things
-- with different macros.
--
-- `portion` and `units` are what the macros are FOR - 100 grams, 1 Bar, 1
-- Portion - so an entry of 50 grams of something recorded per 100 g is half.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS foods (
    id         INTEGER PRIMARY KEY,
    list       TEXT    NOT NULL CHECK (list IN ('Items', 'Meals', 'Recipes')),
    name       TEXT    NOT NULL CHECK (length(trim(name)) > 0),
    grouping   TEXT,
    portion    REAL    NOT NULL DEFAULT 1 CHECK (portion > 0),
    units      TEXT    NOT NULL DEFAULT 'Portion',
    calories   REAL    NOT NULL CHECK (calories >= 0),
    carbs      REAL    NOT NULL CHECK (carbs   >= 0),
    fat        REAL    NOT NULL CHECK (fat     >= 0),
    protein    REAL    NOT NULL CHECK (protein >= 0),
    retired    INTEGER NOT NULL DEFAULT 0 CHECK (retired IN (0, 1)),
    note       TEXT,
    created_at TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (list, name)
);

CREATE INDEX IF NOT EXISTS ix_foods_name ON foods (name);

-- ---------------------------------------------------------------------------
-- A named set of target macros, in force from a date.
--
-- Named because the workbook had two - Base and Workout - and a training day
-- is not a rest day. Dated because a target that changes must not rewrite
-- history: a day in 2024 is compared against the numbers that applied in 2024,
-- not against today's. That is the same reason a plan's 1RMs live on the plan.
--
-- The row in force for a day is the latest `starts_on` on or before it, which
-- is what v_food_days works out.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS macro_targets (
    id        INTEGER PRIMARY KEY,
    name      TEXT NOT NULL CHECK (length(trim(name)) > 0),
    starts_on TEXT NOT NULL,                 -- ISO date
    calories  REAL NOT NULL CHECK (calories > 0),
    carbs     REAL NOT NULL CHECK (carbs   >= 0),
    fat       REAL NOT NULL CHECK (fat     >= 0),
    protein   REAL NOT NULL CHECK (protein >= 0),
    note      TEXT,
    UNIQUE (name, starts_on)
);

-- ---------------------------------------------------------------------------
-- One day.
--
-- A day is planned ahead and then corrected in place - which is what the
-- workbook's Planner -> Food_Diary -> DailyCheck round trip amounted to, and
-- how all 297 recorded days were actually produced. There is no separate
-- "planned" and "actual": there is the day, and it changes until it stops.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS food_days (
    day         TEXT PRIMARY KEY,            -- ISO date
    target_name TEXT,                        -- NULL means the default profile
    note        TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ---------------------------------------------------------------------------
-- One line of a day.
--
-- **The macros are stored, not looked up.** That is the opposite of the rule
-- the run tracker follows for pace, and it is right here for a reason the
-- imported history makes plain: the diary records what was eaten, the catalogue
-- records what a food is now, and the second changes. Edit a recipe because you
-- started using less oil and every dinner you ate last year would silently
-- restate itself. `food_id` still links to the catalogue where the name matches
-- so a food's history can be followed; it is nullable because two years of
-- diary entries are text and some of them name things no longer in the list.
--
-- `quantity` and `units` are what was eaten - 50 grams, 2 Portion - against the
-- food's own portion size. Both nullable: the historic entries record a name
-- like "All Real bar - 1 Bar" and nothing separable.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS food_entries (
    id       INTEGER PRIMARY KEY,
    day      TEXT    NOT NULL REFERENCES food_days(day) ON DELETE CASCADE,
    meal     TEXT    NOT NULL,
    position INTEGER NOT NULL CHECK (position > 0),
    food_id  INTEGER REFERENCES foods(id),
    name     TEXT    NOT NULL CHECK (length(trim(name)) > 0),
    quantity REAL    CHECK (quantity IS NULL OR quantity > 0),
    units    TEXT,
    calories REAL    NOT NULL CHECK (calories >= 0),
    carbs    REAL    NOT NULL CHECK (carbs   >= 0),
    fat      REAL    NOT NULL CHECK (fat     >= 0),
    protein  REAL    NOT NULL CHECK (protein >= 0),
    source   TEXT    NOT NULL DEFAULT 'manual',   -- 'manual', 'xlsx', 'csv'
    UNIQUE (day, meal, position)
);

CREATE INDEX IF NOT EXISTS ix_entries_day  ON food_entries (day, meal, position);
CREATE INDEX IF NOT EXISTS ix_entries_food ON food_entries (food_id);

-- ---------------------------------------------------------------------------
-- Every day, with its macros added up and the target that applied to it.
--
-- The target is resolved per day rather than joined once: it is the latest
-- version of the named profile that had started by then, so a day keeps being
-- measured against what was true at the time however often the numbers change
-- afterwards.
--
-- Two target columns, because they answer different questions and a page that
-- confuses them tells a lie. `target_name` is what the day is *measured*
-- against and is almost never NULL; `chosen_target` is what the day itself
-- asked for, and is NULL for every one of the 290 imported days - they were
-- recorded before profiles existed and picked nothing. Reading the first as the
-- second puts "this day names its own target" under every day in the history.
-- ---------------------------------------------------------------------------
DROP VIEW IF EXISTS v_food_days;
CREATE VIEW v_food_days AS
SELECT
    d.day,
    d.note,
    COALESCE(d.target_name, t.name)                       AS target_name,
    d.target_name                                         AS chosen_target,
    (SELECT COUNT(*) FROM food_entries e WHERE e.day = d.day) AS entries,
    ROUND(COALESCE((SELECT SUM(calories) FROM food_entries e
                     WHERE e.day = d.day), 0), 2)         AS calories,
    ROUND(COALESCE((SELECT SUM(carbs) FROM food_entries e
                     WHERE e.day = d.day), 0), 2)         AS carbs,
    ROUND(COALESCE((SELECT SUM(fat) FROM food_entries e
                     WHERE e.day = d.day), 0), 2)         AS fat,
    ROUND(COALESCE((SELECT SUM(protein) FROM food_entries e
                     WHERE e.day = d.day), 0), 2)         AS protein,
    t.calories                                            AS target_calories,
    t.carbs                                               AS target_carbs,
    t.fat                                                 AS target_fat,
    t.protein                                             AS target_protein
FROM food_days d
LEFT JOIN macro_targets t
       ON t.id = (SELECT id FROM macro_targets m
                   WHERE m.starts_on <= d.day
                     AND (d.target_name IS NULL OR m.name = d.target_name)
                   ORDER BY m.starts_on DESC, m.id DESC LIMIT 1);

-- ---------------------------------------------------------------------------
-- Every entry, carrying the day and the catalogue row it came from.
-- ---------------------------------------------------------------------------
DROP VIEW IF EXISTS v_food_entries;
CREATE VIEW v_food_entries AS
SELECT
    e.*,
    f.list      AS food_list,
    f.grouping  AS food_grouping,
    f.name      AS catalogue_name,
    f.retired   AS food_retired
FROM food_entries e
LEFT JOIN foods f ON f.id = e.food_id;
