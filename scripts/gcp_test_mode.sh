# Sourced by gcp_deploy.sh and gcp_teardown.sh. Test mode (the tests in
# backend/tests/unit/test_gcp_*.py set FINPILOT_FAKE_GCLOUD): refuse to run
# unless the gcloud on PATH is that fake, so a test can never reach the real gcloud.
if [[ -n "${FINPILOT_FAKE_GCLOUD:-}" ]]; then
  found="$(command -v gcloud || true)"
  if ! [[ -f "$found" && "$found" -ef "$FINPILOT_FAKE_GCLOUD" ]] || ! grep -q FINPILOT-FAKE-GCLOUD "$found"; then
    echo "Test mode: the gcloud on PATH is '${found:-none}', not the fake. Refusing to run." >&2; exit 2
  fi
fi
