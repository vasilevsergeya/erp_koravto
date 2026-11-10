#!/bin/sh
set -eu
exec python3 /scripts/backup.py restore "$@"
