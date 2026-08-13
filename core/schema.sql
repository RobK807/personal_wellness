-- Schema for the personal wellness dashboard.
--
-- One database, one section at a time. The weigh-in tables come first and are
-- unchanged from the standalone tracker, so its data/weigh_ins.db can simply be
-- renamed and the run tables appear alongside it on first start. The run tables
-- follow. Workouts and diet have no tables yet - their pages are placeholders,
-- and inventing a schema for a tracker that has not been designed would be
-- guessing in a place that is expensive to change later.

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
