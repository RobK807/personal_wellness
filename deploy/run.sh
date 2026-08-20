#!/bin/sh
# Start the weigh-in tracker (Flask front-end) on the Synology NAS.
#
# Wire this to DSM -> Control Panel -> Task Scheduler -> Create ->
# Triggered Task -> User Defined Script, event "Boot-up", user "root".
#
# Realtek models have no Container Manager, and the NAS has ~149 MB of RAM
# free with the CD dashboard already taking ~48 MB of it, so this runs the
# lightweight Flask front-end (~30 MB) rather than Streamlit (~172 MB).
# See DEPLOY.md.

set -e

# Work out where the app lives from this script's own location, so the folder
# can be moved without editing anything. deploy/run.sh -> the parent is the app.
APP_DIR=$(cd "$(dirname "$0")/.." && pwd)
LOG="$APP_DIR/data/server.log"

# How long to wait for it to bind before giving up, in seconds. Startup does
# real work before it listens: the schema is applied, missing columns and views
# are created and the seed rows go in, and on this NAS that has taken longer
# than three seconds ever since the workout tables were added. Waiting a fixed
# three reported a perfectly healthy app as a failure.
STARTUP_TIMEOUT="${PW_STARTUP_TIMEOUT:-45}"

# Is a pid a live process rather than a zombie? A background child that has
# exited still has a /proc entry until the shell reaps it, so `[ -d /proc/$pid ]`
# and `kill -0` both say yes to something that is already dead. A zombie's
# cmdline is empty, which is the difference that matters here - and is how
# stop.sh identifies our processes too.
alive() {
    [ -r "/proc/$1/cmdline" ] || return 1
    cmd=$(tr '\0' ' ' < "/proc/$1/cmdline" 2>/dev/null)
    [ -n "$cmd" ]
}

# Find the virtualenv. A venv inside this app wins, so one dashboard can be
# given its own dependencies later without disturbing the others; otherwise fall
# back to the one shared with the CD dashboard. Override with PW_VENV.
if [ -n "$PW_VENV" ]; then
    VENV="$PW_VENV"
elif [ -x "$APP_DIR/venv/bin/python" ]; then
    VENV="$APP_DIR/venv"
else
    VENV="$(dirname "$APP_DIR")/venv"
fi

# --- settings ---------------------------------------------------------------
export PW_DB_PATH="$APP_DIR/data/wellness.db"
export PW_EXPORT_DIR="$APP_DIR/data/exports"
# 8501 is the CD dashboard. Give every dashboard its own port.
export PW_WEB_PORT=8503

# Read the shared password from a file kept out of version control, so it is
# never baked into a script. Create it with:
#   printf 'your-password' > "$APP_DIR/deploy/password.txt"
#   chmod 600 "$APP_DIR/deploy/password.txt"
if [ -f "$APP_DIR/deploy/password.txt" ]; then
    PW_APP_PASSWORD=$(cat "$APP_DIR/deploy/password.txt")
    export PW_APP_PASSWORD
fi

# --- checks -----------------------------------------------------------------
if [ ! -x "$VENV/bin/python" ]; then
    echo "No virtualenv found." >&2
    echo "  looked in: $APP_DIR/venv" >&2
    echo "         and $(dirname "$APP_DIR")/venv" >&2
    echo "Run the install steps in DEPLOY.md first." >&2
    exit 1
fi

if [ ! -f "$PW_DB_PATH" ]; then
    echo "No database at $PW_DB_PATH." >&2
    echo "Import the workbook on a desktop and copy wellness.db across." >&2
    exit 1
fi

mkdir -p "$APP_DIR/data"

# Don't start a second copy if one is already running.
if netstat -ln 2>/dev/null | grep -q ":$PW_WEB_PORT "; then
    echo "Something is already listening on $PW_WEB_PORT; not starting again."
    exit 0
fi

# --- go ---------------------------------------------------------------------
cd "$APP_DIR"

# The log is appended to, so it keeps the history of previous runs. Without a
# marker it is easy to read an old traceback as a current failure. Timestamp
# every start.
{
    echo ""
    echo "=================================================================="
    echo "Starting $(date '+%Y-%m-%d %H:%M:%S')  port $PW_WEB_PORT  venv $VENV"
    echo "=================================================================="
} >> "$LOG"

# 0.0.0.0 so Tailscale can reach it. The NAS is not port-forwarded, so this does
# not expose the app to the public internet - PW_APP_PASSWORD is the second layer.
#
# -u because Python block-buffers stdout when it is a file rather than a
# terminal, so everything printed on the way up - including "Serving on ..." -
# sat in a 4 KB buffer instead of the log. That is exactly backwards: the log is
# the only thing you have when a start goes wrong, and it was empty precisely
# then. stderr was never the problem; a traceback always arrived.
nohup "$VENV/bin/python" -u serve.py --host 0.0.0.0 --port "$PW_WEB_PORT" \
    >> "$LOG" 2>&1 &

# Record the pid so stop.sh does not have to hunt for it. DSM's BusyBox ps
# truncates long command lines, which makes `ps | grep serve.py` unreliable.
PID=$!
echo "$PID" > "$APP_DIR/data/server.pid"

# nohup returns immediately, so "started" only means "launched". Wait for it to
# actually bind, checking every second rather than assuming a fixed wait is
# long enough, and stop early if the process dies.
i=0
while [ "$i" -lt "$STARTUP_TIMEOUT" ]; do
    if netstat -ln 2>/dev/null | grep -q ":$PW_WEB_PORT "; then
        echo "Running on port $PW_WEB_PORT (pid $PID), listening after ${i}s"
        echo "Using $VENV"
        echo "Logs: $LOG"
        exit 0
    fi
    alive "$PID" || break
    sleep 1
    i=$((i + 1))
done

# Two different failures, and telling them apart is the point. A process that is
# still alive but has not bound yet is not something to clean up after: deleting
# its pid file is how a running app gets orphaned from the script meant to stop
# it, which is what used to happen here.
if alive "$PID"; then
    echo "Started (pid $PID) but not listening after ${i}s." >&2
    echo "It may still be coming up. Check with:" >&2
    echo "  netstat -ln | grep :$PW_WEB_PORT" >&2
    echo "The pid file is left in place, so stop.sh can still find it." >&2
else
    echo "FAILED to start - the process exited." >&2
    rm -f "$APP_DIR/data/server.pid"
fi
echo "Last few log lines:" >&2
tail -n 20 "$LOG" >&2
exit 1
