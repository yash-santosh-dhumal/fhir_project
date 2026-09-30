#!/bin/bash
# ==============================================================================
# start.sh - Multi-cloud startup script (Render.com, Hugging Face, or Docker)
#
# Runs both the Medical Data Toolkit backend (port 8088) and the
# interactive Hospital/Insurance Web Portal within the same container.
# ==============================================================================

set -eo pipefail

export PYTHONUNBUFFERED=1
export PYTHONDONTWRITEBYTECODE=1

# Configure Ports and Endpoints (Render sets $PORT, default 7860/5000)
WEB_PORT="${PORT:-7860}"
export TOOLKIT_DEV_PORT="${TOOLKIT_DEV_PORT:-8088}"
export TOOLKIT_URL="http://127.0.0.1:${TOOLKIT_DEV_PORT}"
export DEMO_PORT="${WEB_PORT}"

# Locate Configuration
if [ -f "config.demo.yaml" ]; then
  export TOOLKIT_CONFIG_FILE="config.demo.yaml"
elif [ -f "src/config.yaml" ]; then
  export TOOLKIT_CONFIG_FILE="src/config.yaml"
fi

# Worker tuning for Render 512 MB Free Tier:
# - Gemini API calls are pure network I/O (not CPU/RAM bound), so high parallelism is safe.
# - Images are already compressed to ≤1400px / 300KB before sending, keeping RAM footprint low.
# - GEMINI_OCR_BATCH_SIZE=5: smaller batches → shorter per-call latency → better parallel throughput.
# - GEMINI_OCR_MAX_WORKERS=8: run up to 8 batches simultaneously (40 docs in one parallel round).
# - TOOLKIT_CONCURRENT_WORKERS=2 is safe since each call passes small compressed bytes.
export GEMINI_OCR_MAX_WORKERS="${GEMINI_OCR_MAX_WORKERS:-8}"
export GEMINI_OCR_BATCH_SIZE="${GEMINI_OCR_BATCH_SIZE:-5}"
export TOOLKIT_CONCURRENT_WORKERS="${TOOLKIT_CONCURRENT_WORKERS:-2}"

echo "======================================================================"
echo " Starting Medical Data Toolkit & FHIR Portal"
echo "======================================================================"
echo " Web Public Port:            ${WEB_PORT}"
echo " Toolkit Backend Port:       ${TOOLKIT_DEV_PORT}"
echo " Toolkit Config File:        ${TOOLKIT_CONFIG_FILE}"
echo " Python Version:             $(python3 --version 2>&1)"
echo " Working Directory:          $(pwd)"

# Warn if GEMINI_API_KEY is missing
if [ -z "${GEMINI_API_KEY}" ]; then
  echo ""
  echo " [!] WARNING: GEMINI_API_KEY is not set in environment variables."
  echo "     Please configure GEMINI_API_KEY in your hosting dashboard."
  echo ""
fi

# 1. Start Medical Data Toolkit backend in background
echo "==> [1/3] Launching Medical Data Toolkit REST API on port ${TOOLKIT_DEV_PORT}..."
python3 scripts/run_toolkit_dev_server.py &
TOOLKIT_PID=$!

# Trap signals for graceful shutdown
cleanup() {
  echo "==> Received shutdown signal. Terminating backend (PID: ${TOOLKIT_PID})..."
  kill -TERM "${TOOLKIT_PID}" 2>/dev/null || true
  wait "${TOOLKIT_PID}" 2>/dev/null || true
  exit 0
}
trap cleanup SIGTERM SIGINT

# 2. Wait for backend healthcheck to respond
echo "==> [2/3] Waiting for Toolkit backend to initialize..."
HEALTH_CHECK_PASSED=0
for i in $(seq 1 45); do
  # Check if backend process crashed
  if ! kill -0 "${TOOLKIT_PID}" 2>/dev/null; then
    echo " [ERROR] Toolkit backend process exited unexpectedly! Check logs above."
    exit 1
  fi

  # Probe health endpoint
  if python3 -c "
import urllib.request, sys
try:
    resp = urllib.request.urlopen('${TOOLKIT_URL}/', timeout=2)
    if resp.getcode() == 200:
        sys.exit(0)
except Exception:
    pass
sys.exit(1)
" 2>/dev/null; then
    echo "==> [OK] Toolkit backend is healthy and responding at ${TOOLKIT_URL}!"
    HEALTH_CHECK_PASSED=1
    break
  fi

  echo "    Waiting for toolkit initialization (attempt ${i}/45)..."
  sleep 1
done

if [ "${HEALTH_CHECK_PASSED}" -ne 1 ]; then
  echo " [!] Warning: Toolkit health check timed out. Proceeding to launch UI..."
fi

# 3. Start Demo Web Portal
# Note: Using 1 worker with 4 threads drastically conserves RAM (stays under 512 MB for free tiers)
echo "==> [3/3] Launching Web Portal on 0.0.0.0:${WEB_PORT}..."

if command -v gunicorn >/dev/null 2>&1; then
  echo "    Starting via Gunicorn (1 worker, 4 threads)..."
  gunicorn -w 1 --threads 4 -b "0.0.0.0:${WEB_PORT}" --timeout 180 "demo.app:app" &
  WEB_PID=$!
else
  echo "    Gunicorn not found. Starting via Python HTTP server..."
  python3 -c "
import os, sys
sys.path.insert(0, '.')
from demo.app import app
app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 7860)), debug=False)
" &
  WEB_PID=$!
fi

# Keep container alive and monitor child processes
wait -n "${TOOLKIT_PID}" "${WEB_PID}"
EXIT_STATUS=$?
echo "==> A server process exited with status ${EXIT_STATUS}. Cleaning up..."
cleanup
