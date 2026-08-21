# Deploying

This is the **second** dashboard on the NAS, and it replaces the standalone
weigh-in tracker rather than joining it — the weigh-in section here is that
app, so running both would be two front doors onto two copies of the same six
years of readings. Stop the old one on 8502 before starting this one on 8503;
see [Replacing the weigh-in tracker](#replacing-the-weigh-in-tracker).

The CD dashboard is already there on port 8501, and everything about the
hardware that shaped how it was deployed applies here too — the same box, the same 149 MB of free RAM, the same absent
Docker. What follows assumes the CD dashboard's setup is in place; if it is
not, [cd_dashboard/deploy/DEPLOY.md](../../cd_dashboard/deploy/DEPLOY.md) is
the long version.

The short version of the constraints:

| | |
| --- | --- |
| Model | Synology DS218play, Realtek RTD1296, aarch64 |
| DSM | 7.2.2 build 72806 |
| **RAM** | **644 MB total, ~149 MB free before anything of ours runs** |
| Python | 3.8.15 (`/bin/python3`) and 3.9.22 (`/usr/local/bin/python3.9`) |
| Docker | **not available** — Synology ships no Container Manager for Realtek |

`deploy/Dockerfile` and `deploy/docker-compose.yml` are kept for reference in
case this ever moves to a machine that can run containers. They are not the
path here.

---

## 1. Memory: this is the constraint that matters

| | |
| --- | --- |
| Free before anything of ours | ~149 MB |
| Tailscale | −25 MB (approx) |
| CD dashboard | −48 MB |
| **This dashboard** | **−30 MB** |
| Left over | **~46 MB** |

That works, and it is roughly where the DS218play stops having room. A third
Flask dashboard would leave almost nothing and DSM would start swapping to
disk, which on this hardware is painful. Check before adding anything:

```bash
free -m
```

Anything under ~40 MB free means it is time to stop, or time for a Raspberry Pi.

**Streamlit will not run here.** It measures ~172 MB resident and needs Python
3.10+, which DSM does not have. That is why the Flask front-end exists, and why
`web_test.py` fails if pandas is ever pulled onto its import path.

---

## 2. Install

The venv is shared with the CD dashboard and the dependencies are identical
(Flask, waitress, openpyxl), so if the CD dashboard is already running there is
**nothing to install**. Confirm it is there:

```bash
ls /volume1/dashboards/venv/bin/python
```

If it is missing:

```bash
cd /volume1/dashboards
python3.9 -m venv venv
venv/bin/pip install -r personal_wellness/requirements-nas.txt
```

`run.sh` looks for a venv inside its own app folder first and falls back to the
shared one alongside, so if this dashboard ever needs something incompatible it
can be given its own `venv/` without disturbing the CD one. Split when a
conflict actually appears, not in anticipation of one.

### Layout

```
/volume1/dashboards/
├── venv/                    shared interpreter and packages
├── cd_dashboard/            port 8501
└── personal_wellness/
    ├── deploy/run.sh        starts this dashboard
    └── data/wellness.db     every section's data, in one file
```

---

## 3. First deployment

The database is built on the PC, not on the NAS — openpyxl opening the workbook
needs far more memory than the NAS has free.

```bash
python -m core.excel_import --rebuild     # the weigh-in history
python -m core.strava_import --rebuild    # the runs
python -m core.gym_import                 # the gym programme
python -m core.food_import --catalogue    # the food lists
python -m core.food_import --load "data/imports/food_diary_cleaned.csv" --replace
python reconcile_test.py
python run_test.py
python workout_test.py
python food_test.py
python deploy/push_to_nas.py --with-db
```

Every importer writes into the same `data/wellness.db`, and all of them are safe
to re-run: the weigh-in one keys on date and slot, the run one on date, distance
and elapsed time, the food catalogue on list and name.

The food diary is the one that does not come straight out of its workbook. Its
lines are rendered strings rather than a food and a quantity, so they were
exported to a CSV, corrected by hand and loaded back — see the README. The
corrected file is what `--load` reads, and it lives in `data/imports/`.

`push_to_nas.py` writes Unix line endings (DSM's `/bin/sh` fails on CRLF with
misleading `not found` errors), skips caches and anything the NAS owns, leaves
the workbook itself behind, and copies the database through SQLite's backup API
rather than as a plain file, so it is a consistent snapshot. `--dry-run` lists
what would change.

Then, over SSH (enable it first: DSM → Control Panel → Terminal & SNMP):

```bash
sh /volume1/dashboards/personal_wellness/deploy/run.sh
```

Browse to `http://synork807:8503` from the LAN. Logs go to `data/server.log`.

### Keep it running

DSM → **Control Panel → Task Scheduler → Create → Triggered Task → User Defined
Script**, event *Boot-up*, user *root*:

```bash
sh /volume1/dashboards/personal_wellness/deploy/run.sh
```

Invoking it through `sh` avoids needing the executable bit, which an SMB copy
cannot set. Run the task manually once before relying on the trigger. This is a
second boot entry alongside the CD dashboard's, not a replacement for it.

### Setting a password

```bash
printf 'something-long' > /volume1/dashboards/personal_wellness/deploy/password.txt
chmod 600 /volume1/dashboards/personal_wellness/deploy/password.txt
```

`run.sh` picks it up automatically. Restart the app afterwards.

---

## 4. Pushing changes later

```bash
python deploy/push_to_nas.py
sh /volume1/dashboards/personal_wellness/deploy/stop.sh
sh /volume1/dashboards/personal_wellness/deploy/run.sh
```

The database is **never** pushed unless you ask. Once the dashboard is in use,
the copy on the NAS is the live one and the copy on the PC is stale — pushing it
would destroy every weigh-in entered from your phone since.

### Sending a workout plan across

A plan cannot get to the NAS the way the other sections' data did. `gym_import`
reads a workbook, the workbooks are never pushed to the NAS, and openpyxl
opening one needs more memory than the DS218play has free — so a plan is always
built or imported on a desktop and carried across afterwards.

```bash
python deploy/send_plan.py                          # what is here, what is there
python deploy/send_plan.py --plan "2026 Gym Programme"
python deploy/send_plan.py --plan "..." --replace    # overwrite one of that name
python deploy/send_plan.py --plan "..." --dry-run
```

**Stop the dashboard first.** The script refuses while port 8503 answers,
because this writes SQLite over SMB and that is only safe with exactly one
writer:

```bash
sh /volume1/dashboards/personal_wellness/deploy/stop.sh
python deploy/send_plan.py --plan "2026 Gym Programme"
sh /volume1/dashboards/personal_wellness/deploy/run.sh
```

It touches **only the eight workout tables, and only the rows of the plans
named**. The weigh-ins, the runs and the run option lists are never read from
the source and never written. It backs the target up first, and reads every
prescribed weight back afterwards to prove the transfer arrived intact — if that
comparison fails it says so and names the backup to restore.

Ids are not carried across: movements are matched **by name**, and anything the
NAS catalogue has not got is added to it. That is what makes the script safe to
run against a NAS whose catalogue has been edited since, and safe to run twice.

Pointing `--target` at a local copy is a rehearsal, and needs nothing stopped.

### Sending food across

The same problem as a workout plan, and the same answer. `food_import` reads a
workbook, the workbooks are never pushed to the NAS, and openpyxl opening one
needs more memory than the DS218play has free.

Pushing the whole database instead is not an option once the dashboard is in
use — the NAS copy is the live one and holds weigh-ins and runs entered from a
phone that this PC has never seen.

```bash
python deploy/send_food.py                  # what is here, what is there
python deploy/send_food.py --all            # the catalogue and the diary
python deploy/send_food.py --catalogue      # just the foods and the targets
python deploy/send_food.py --all --dry-run
```

**Stop the dashboard first** — the script refuses while port 8503 answers,
because this writes SQLite over SMB and that is only safe with exactly one
writer:

```bash
sh /volume1/dashboards/personal_wellness/deploy/stop.sh
python deploy/send_food.py --all
sh /volume1/dashboards/personal_wellness/deploy/run.sh
```

It touches **only the four food tables**. The weigh-ins, the runs and every
workout table are never read from the source and never written. It backs the
target up first and reads every day's four macro totals back afterwards to prove
the transfer arrived intact.

Ids are not carried across: foods are matched on **(list, name)** — the pair the
table is unique on, because the workbook has "Mashed potato" as both an Item and
a Recipe — and each diary line is re-linked to whatever the target's id turns out
to be. Anything the target has not got is added.

Two rules about overwriting, and they differ on purpose. A **food or target**
already on the NAS is left alone unless `--overwrite-foods` is given: the NAS
copy is the live one, and a portion corrected on a phone should not be undone by
a push from a stale desktop. A **day** is replaced, because a day is saved
wholesale everywhere else in this section and half a day merged from two sources
is not a day anybody ate — `--skip-existing-days` keeps the target's version.

Pointing `--target` at a local copy is a rehearsal, and needs nothing stopped.

### Re-importing the workbook

Only if you deliberately want to re-baseline from the spreadsheet. On the PC:

```bash
python -m core.excel_import --rebuild
python deploy/push_to_nas.py --with-db --force
```

`--force` is required because this **discards everything entered through the
dashboard** since the last import. Take a backup first, and expect to re-enter
anything recent.

---

## 5. Reaching it from your phone — Tailscale

Already set up for the CD dashboard; this needs nothing new. Browse to
`http://synork807:8503` with Tailscale running on the phone. Add it to the home
screen and it behaves like an app.

The reasoning, unchanged from the CD dashboard: Tailscale is a mesh VPN, so the
phone and the NAS join one private network regardless of connection — mobile
data, someone else's wifi, abroad, all identical to being on the LAN. Nothing is
exposed to the public internet, no port forwarding, free for personal use.

`run.sh` binds to `0.0.0.0` because Tailscale traffic arrives on the NAS's
Tailscale interface, not loopback. The NAS is not port-forwarded, so this does
not put the app on the public internet — but keep `PW_APP_PASSWORD` set as a
second layer.

If MagicDNS is off, use the `100.x.y.z` address instead of the short name.

---

## Replacing the weigh-in tracker

This dashboard's weigh-in section *is* the standalone tracker, so the two must
not run side by side against separate databases — whichever one you happen to
open would be missing whatever you entered in the other.

The tracker on the NAS holds readings this PC has never seen — that is where
the phone has been writing — so the migration runs **from the NAS to here**, not
the other way round. Copying `wellness.db` up first would destroy them.

**1. Stop the old dashboard.** Over SSH:

```bash
sh /volume1/dashboards/weigh_in_tracker/deploy/stop.sh
```

Do this before the copy, not after. `sqlite3`'s backup API will read a database
that is being written to quite happily, but a weigh-in entered between the copy
and the switch lands in a file nothing will ever read again.

**2. Adopt its database, on the PC:**

```bash
python deploy/adopt_weigh_ins.py --dry-run
python deploy/adopt_weigh_ins.py
```

This takes the live `weigh_ins.db`, makes it the base of `wellness.db`, and
re-imports the runs on top. It refuses if the old dashboard is still answering
on 8502, if the live database is *older* than the local one, or if `wellness.db`
holds a run that was entered by hand rather than imported — that last one being
the only thing a rebuild would lose. What it replaces is kept beside it as
`wellness.before-adopt-<timestamp>.db`.

The runs are rebuilt rather than merged. Every one of them comes from
`Final_data` and can be reproduced from it exactly, so there is nothing to
reconcile; merging two databases would mean reconciling two audit logs, two
sets of back-filled estimates and two ideas of which readings are real.

**3. Check, then push:**

```bash
python run_test.py && python reconcile_test.py
python deploy/push_to_nas.py --with-db
```

**4. Start it, over SSH:**

```bash
sh /volume1/dashboards/personal_wellness/deploy/run.sh
```

Then browse to `http://synork807:8503` and confirm the weigh-in section shows
today's reading and the run section shows the runs.

**5. Stop the old one coming back.** Its `run.sh` binds 8502 and this one binds
8503, so a boot-up task still pointing at the old folder will quietly resurrect
it at the next reboot — and then two dashboards are writing to two databases
again. In DSM → Control Panel → Task Scheduler, point the boot-up task at
`/volume1/dashboards/personal_wellness/deploy/run.sh`, or delete it and add a
new one.

Keep `/volume1/dashboards/weigh_in_tracker/` until you are satisfied, then
delete it. Its database is the only copy of anything, and by then it is a
superseded copy — but there is no hurry.

---

## 6. Backups

The whole thing is one SQLite file. DSM does not ship the `sqlite3` CLI, so use
Python's own backup API — same mechanism, always available:

```bash
/volume1/dashboards/venv/bin/python - <<'PY'
import sqlite3, datetime, pathlib
src = sqlite3.connect("/volume1/dashboards/personal_wellness/data/wellness.db")
out = pathlib.Path("/volume1/backups") / f"weigh_ins_{datetime.date.today():%Y%m%d}.db"
out.parent.mkdir(parents=True, exist_ok=True)
dst = sqlite3.connect(out)
src.backup(dst)
dst.close(); src.close()
print("backed up to", out)
PY
```

Use this rather than copying the file — a plain copy of a live WAL database can
be inconsistent. Schedule it nightly in DSM Task Scheduler.

The **Admin** page also writes a plain `.xlsx` snapshot on demand, with the same
tabs the original workbook had, so there is always a copy readable in Excel
without the app.

---

## Troubleshooting

### "FAILED to start - it launched but is not listening"

Check whether it actually failed before doing anything about it:

```bash
netstat -ln | grep :8503
curl -s localhost:8503/healthz
```

If it is listening, it started and the script gave up too early. `run.sh` now
waits up to 45 seconds (`PW_STARTUP_TIMEOUT`) rather than a fixed three, because
startup applies the schema, adds any missing columns and views and writes the
seed rows before it binds — which on this NAS takes longer than three seconds
since the workout tables were added.

If it is **not** listening, the log is worth reading now: `run.sh` runs Python
with `-u`, so everything printed on the way up reaches `data/server.log` instead
of sitting in a buffer. A start that fails with an empty log used to be the
normal outcome and is now a real signal.

Either way `stop.sh` can find the process, with or without a pid file — it falls
back to scanning `/proc` for one running `serve.py --port 8503`.



**`run.sh` reports it failed to start.** It waits three seconds and checks
whether anything is listening, printing the last few log lines if not. The most
likely cause is code that needs Python 3.10 reaching the NAS's 3.9 — run
`python py39_check.py` on the desktop before pushing.

**Reading the log.** `data/server.log` is appended to, so it holds every run.
Each start writes a timestamped banner; anything above the last banner is
history, not a current failure. To see only the current run:

```bash
awk '/^=====/{buf=""} {buf=buf $0 ORS} END{printf "%s", buf}' /volume1/dashboards/personal_wellness/data/server.log
```

**Reachable by ping but not on port 8503.** The process is not running, or DSM's
firewall is blocking the port. Control Panel → Security → Firewall; if it is
enabled, add a rule allowing 8503 on the LAN interface. Note that the CD
dashboard's rule covers 8501 only.

**The wrong dashboard stopped.** `stop.sh` matches on the port as well as
`serve.py`, so it will not touch the CD dashboard — but if you have overridden
`PW_WEB_PORT` in the shell, export the same value before running it.

---

## If the NAS is ever upgraded

Both front-ends share `core`, the same database and the same interpolation, so
switching is a matter of which process you start:

```bash
streamlit run app.py     # richer UI, ~172 MB
python serve.py          # lightweight, ~30 MB
```

On a roomier machine install `requirements.txt` instead of
`requirements-nas.txt` and point the Task Scheduler entry at the Streamlit
command. Nothing needs migrating.

A Raspberry Pi 4/5 is the other easy upgrade path — an ordinary Linux box, so
Docker and the container files in this folder become usable again. Keep
`PW_DB_PATH` on the Pi's own storage, **not** on an SMB mount from the NAS:
SQLite locking over SMB is unreliable and will eventually corrupt the file.
