#!/bin/bash
cd /home/claude/.cc-connect/workspace/pygitnexus
exec /home/claude/.cc-connect/workspace/pygitnexus/.venv/bin/pygitnexus web --host 0.0.0.0 --port 8000 --no-open
