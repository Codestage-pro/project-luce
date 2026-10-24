#!/bin/sh
exec env PYTHONPATH="/app/share/project-luce${PYTHONPATH:+:$PYTHONPATH}" python3 -m luce "$@"
