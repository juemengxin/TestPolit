#!/bin/sh
set -eu

case "${1:-web}" in
  web)
    # Each recording owns its noVNC process, behind the authenticated frontend proxy.
    /opt/bin/start-xvfb.sh >/tmp/testpilot-xvfb.log 2>&1 &
    xvfb_pid=$!
    ready=false
    for attempt in $(seq 1 30); do
      if xdpyinfo -display "${DISPLAY:-:99.0}" >/dev/null 2>&1; then ready=true; break; fi
      sleep 1
    done
    if [ "$ready" != true ]; then
      kill "$xvfb_pid" 2>/dev/null || true
      echo "Xvfb failed to start; headed browser recording is unavailable" >&2
      exit 1
    fi
    /opt/bin/start-vnc.sh >/tmp/testpilot-vnc.log 2>&1 &
    python manage.py migrate --noinput
    exec gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 1 --timeout 120 --access-logfile - --error-logfile -
    ;;
  worker)
    exec python manage.py runworker
    ;;
  *)
    exec "$@"
    ;;
esac
