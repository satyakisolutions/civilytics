#!/bin/bash
# Runs vLLM's own OpenAI-compatible server (the base image's original
# entrypoint, "vllm serve") alongside vlm-worker and tts-worker as sibling
# background processes in one container. "$@" is whatever compose's
# `command:` block passes (model/quantization/context flags) -- forwarded
# unchanged to vllm serve, so it starts exactly as it did as a standalone
# container.
set -e
cd /app

vllm serve "$@" &
python3 -m services.vlm.main &
python3 -m services.tts.main &

# Exit (and let Docker restart the whole container) the moment ANY of the
# three dies -- a half-dead container silently missing its worker process
# is worse than a clean restart of all three together.
wait -n
exit_code=$?
echo "start_vlm_combined: one of vllm/vlm-worker/tts-worker exited (code $exit_code) -- stopping container"
exit "$exit_code"
