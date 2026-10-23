#!/bin/sh
# luce/ fica em /usr/share/project-luce (não em site-packages) pra não
# depender da versão exata do python no build; só precisa estar no PYTHONPATH.
exec env PYTHONPATH="/usr/share/project-luce${PYTHONPATH:+:$PYTHONPATH}" python -m luce "$@"
