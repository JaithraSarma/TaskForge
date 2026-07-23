#!/bin/sh
# TaskForge container entrypoint.
# Runs as root only long enough to make the Prometheus multiprocess directory
# (a volume mounted at runtime, created root-owned) writable by the non-root
# app user, then drops privileges and execs the given command.
set -e

if [ -n "$PROMETHEUS_MULTIPROC_DIR" ]; then
    mkdir -p "$PROMETHEUS_MULTIPROC_DIR"
    chown -R taskforge:taskforge "$PROMETHEUS_MULTIPROC_DIR"
fi

exec gosu taskforge "$@"
