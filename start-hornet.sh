#!/bin/sh
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
if ! python3 -c 'import tkinter' 2>/dev/null; then
    echo 'Hornet needs Python 3.9+ with Tk. On Ubuntu/Debian: sudo apt install python3 python3-tk' >&2
    exit 1
fi
exec python3 -m hornet.desktop "$@"
