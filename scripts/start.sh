#!/bin/sh
# Start the Aither Cinema Studio backend on http://127.0.0.1:8765 -- one command:
#   curl -fsSL https://raw.githubusercontent.com/Aitherium/aither-cinema-studio/main/scripts/start.sh | sh
# Re-run it any time: it updates the checkout in place and starts again.
# Needs git and Python 3.10+. With an NVIDIA GPU (or a Mac) it also installs ComfyUI and the
# models on first run (scripts/setup_comfy.py); with no GPU, render on Comfy Cloud instead:
#   curl -fsSL .../scripts/start.sh | CINEMA_COMFY=cloud sh
# Settings that should stick go in $CINEMA_HOME/cinema.env as KEY=VALUE lines.
set -eu

DIR="${CINEMA_HOME:-$HOME/aither-cinema-studio}"
REPO="https://github.com/Aitherium/aither-cinema-studio.git"
PAGES="https://aitherium.github.io"

command -v git >/dev/null 2>&1 || { echo "Aither Cinema needs git: https://git-scm.com/downloads" >&2; exit 1; }
PY="$(command -v python3 || command -v python || true)"
[ -n "$PY" ] || { echo "Aither Cinema needs Python 3.10+: https://www.python.org/downloads/" >&2; exit 1; }
"$PY" -c 'import sys; sys.exit(sys.version_info < (3, 10))' \
  || { echo "Aither Cinema needs Python 3.10+ (found $("$PY" -V 2>&1))" >&2; exit 1; }

if [ -d "$DIR/.git" ]; then
  echo "updating $DIR"
  git -C "$DIR" pull --ff-only -q || echo "(could not update; starting the copy you have)"
else
  echo "downloading to $DIR"
  git clone -q --depth 1 "$REPO" "$DIR"
fi

# Sticky settings: KEY=VALUE lines (COMFY_BASE_URL, COMFY_CA_BUNDLE, CINEMA_MODELS, ...).
if [ -f "$DIR/cinema.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$DIR/cinema.env"
  set +a
fi

cd "$DIR/server"
[ -x .venv/bin/python ] || "$PY" -m venv .venv
echo "installing (first run takes a minute)"
.venv/bin/python -m pip install -q --disable-pip-version-check -e .

# ComfyUI: Comfy Cloud, yours, one already running, or installed + started here.
if COMFY_URL="$("$PY" "$DIR/scripts/setup_comfy.py")"; then
  if [ -n "$COMFY_URL" ]; then COMFY_BASE_URL="$COMFY_URL"; export COMFY_BASE_URL; fi
else
  echo "(ComfyUI is not set up -- the studio opens, but renders need ComfyUI or Comfy Cloud; see above)" >&2
fi

# The studio page is served from $PAGES; the backend must allow it, or the browser refuses.
case ",${CINEMA_CORS_ORIGINS:-}," in
  *",$PAGES,"*) ;;
  *) CINEMA_CORS_ORIGINS="${CINEMA_CORS_ORIGINS:+$CINEMA_CORS_ORIGINS,}$PAGES" ;;
esac
export CINEMA_CORS_ORIGINS

echo "Aither Cinema backend -> http://127.0.0.1:8765  (Ctrl+C stops it; go back to the studio page and press Test)"
if [ -x .venv/bin/cinema-studio ]; then
  exec .venv/bin/cinema-studio --host 127.0.0.1 --port 8765 --no-browser
fi
exec .venv/bin/python -m uvicorn cinema.app:app --host 127.0.0.1 --port 8765
