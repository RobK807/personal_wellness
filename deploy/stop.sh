#!/bin/sh
# Stop the weigh-in tracker.
#
#   sh /volume1/dashboards/personal_wellness/deploy/stop.sh
#
# Needed before replacing data/wellness.db, and when restarting after an
# update - run.sh refuses to start a second copy on the same port.
#
# Finding the process is deliberately not done with `ps | grep`: DSM's BusyBox
# ps truncates the command line to the terminal width, and the path to the venv
# python is long enough that "serve.py" falls off the end. /proc/<pid>/cmdline
# holds the untruncated command, so that is what we read.

APP_DIR=$(cd "$(dirname "$0")/.." && pwd)
PORT="${PW_WEB_PORT:-8503}"
PIDFILE="$APP_DIR/data/server.pid"

# Echo the pid of every process that is this dashboard. Matching on the port as
# well as serve.py means the CD dashboard on 8501 is left alone.
find_pids() {
    if [ -f "$PIDFILE" ]; then
        pid=$(cat "$PIDFILE" 2>/dev/null)
        if [ -n "$pid" ] && [ -d "/proc/$pid" ]; then
            echo "$pid"
            return
        fi
    fi

    for entry in /proc/[0-9]*; do
        [ -r "$entry/cmdline" ] || continue
        cmd=$(tr '\0' ' ' < "$entry/cmdline" 2>/dev/null)
        case "$cmd" in
            *serve.py*--port\ "$PORT"*) echo "${entry#/proc/}" ;;
        esac
    done
}

PIDS=$(find_pids)

if [ -z "$PIDS" ]; then
    if netstat -ln 2>/dev/null | grep -q ":$PORT "; then
        echo "Nothing of ours found, but something is still listening on $PORT." >&2
        echo "Find it with:" >&2
        echo "  for p in /proc/[0-9]*; do tr '\\0' ' ' < \$p/cmdline | grep -q serve.py && echo \$p; done" >&2
        exit 1
    fi
    echo "Not running."
    rm -f "$PIDFILE"
    exit 0
fi

echo "Stopping: $PIDS"
kill $PIDS 2>/dev/null

# Give it a few seconds to shut down cleanly before forcing it.
i=0
while [ $i -lt 10 ]; do
    if ! netstat -ln 2>/dev/null | grep -q ":$PORT "; then
        break
    fi
    sleep 1
    i=$((i + 1))
done

if netstat -ln 2>/dev/null | grep -q ":$PORT "; then
    echo "Still listening after SIGTERM; sending SIGKILL." >&2
    kill -9 $PIDS 2>/dev/null
    sleep 2
fi

rm -f "$PIDFILE"

if netstat -ln 2>/dev/null | grep -q ":$PORT "; then
    echo "Something is still on port $PORT." >&2
    exit 1
fi

echo "Stopped."
