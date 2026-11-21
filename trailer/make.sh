#!/bin/sh
# Makes both trailers from the app as it is now: Lakshmi's lines (Sarvam Bulbul), a fresh Nipta on the sample bills, the recording
# (trailer-kit, in Docker), then the edit, mastered to -14 LUFS. Uses Docker Desktop, so it runs trailer-kit's steps itself instead of
# `trailer-kit rebuild` (which wants Colima). ./make.sh [--force-voice]
set -e
cd "$(dirname "$0")"
KIT=${KIT:-$HOME/dev/trailer-kit}
[ -e node_modules ] || ln -s "$KIT/node_modules" node_modules # the edit uses the kit's Remotion
node voice.mjs $([ "$1" = --force-voice ] && echo --force)

# a fresh app on the sample bills, stopped again when this script ends
pkill -f "uvicorn backend.app" 2>/dev/null || true
(cd .. && ./run.sh --reset > trailer/out/app.log 2>&1) &
trap 'pkill -f "uvicorn backend.app" 2>/dev/null || true' EXIT
until curl -s -o /dev/null http://localhost:8000/; do sleep 0.5; done

docker build -q -t trailer-capture "$KIT/engine/capture" >/dev/null
WATCH=0 node "$KIT/bin/trailer-kit.mjs" record .
KIT=$KIT TRAILER_OUT=$PWD/out sh "$KIT/engine/mux.sh"
WATCH=0 node "$KIT/bin/trailer-kit.mjs" master .
