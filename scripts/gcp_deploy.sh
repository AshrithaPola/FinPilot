#!/usr/bin/env bash
# Deploy FinPilot to Cloud Run: backend and frontend, min instances 0.
# Neon (the database) and Firebase (sign-in) stay as they are.
#
#   PROJECT_ID=my-project scripts/gcp_deploy.sh            # dry run: prints every command
#   PROJECT_ID=my-project scripts/gcp_deploy.sh --apply    # runs them
#
# Never enables billing: it stops if the project has none. Reads the repo-root
# .env for secrets (stored in Secret Manager, never in an image or a flag).
# The deployed app gets its own Gemini key, DEPLOY_GEMINI_API_KEY, never the
# eval key GEMINI_API_KEY (Gemini limits are per project: demo traffic mustn't
# use up the evals' quota). Safe to re-run: what already exists is kept, a
# secret gets a new version only when its value changed, and the service
# account's IAM bindings are retried while the new account propagates.
# Doesn't migrate: Neon is already migrated, and the owner URL never reaches
# Cloud Run. Teardown: scripts/gcp_teardown.sh.
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID}"
REGION="${REGION:-us-central1}"  # a Tier 1 region: the free tier's prices
APPLY=false; [[ "${1:-}" == "--apply" ]] && APPLY=true
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REPO="$REGION-docker.pkg.dev/$PROJECT_ID/finpilot"
TAG="$(git -C "$ROOT" rev-parse --short HEAD)"
SA="finpilot-backend@$PROJECT_ID.iam.gserviceaccount.com"

ENV_FILE="${ENV_FILE:-$ROOT/.env}"
BIND_WAIT_S="${BIND_WAIT_S:-10}"

# Test mode: only the fake gcloud, and secrets from a test .env, never the real ones.
source "$ROOT/scripts/gcp_test_mode.sh"
if [[ -n "${FINPILOT_FAKE_GCLOUD:-}" && "$ENV_FILE" -ef "$ROOT/.env" ]]; then
  echo "Test mode: set ENV_FILE to a test .env. Refusing to run." >&2; exit 2
fi

run() { if $APPLY; then "$@"; else printf '+ %q' "$1"; shift; printf ' %q' "$@"; echo; fi; }
env_value() { grep -E "^$1=" "$ENV_FILE" | tail -1 | cut -d= -f2- || true; }

# A create that may already exist from an earlier run: kept; any other failure stops the script.
create() {
  if ! $APPLY; then run "$@"; return; fi
  local out
  if ! out="$("$@" 2>&1)"; then
    if grep -qiE "already exists|ALREADY_EXISTS" <<<"$out"; then echo "exists, kept: ${*:2:4}"
    else echo "$out" >&2; exit 1; fi
  fi
}

# A new service account takes a while to be visible to IAM: retry its bindings.
bind() {
  if ! $APPLY; then run "$@"; return; fi
  local attempt
  for attempt in 1 2 3 4 5 6; do
    "$@" >/dev/null 2>&1 && return
    echo "IAM binding not accepted yet (attempt $attempt of 6); retrying in $BIND_WAIT_S s" >&2
    sleep "$BIND_WAIT_S"
  done
  "$@" >/dev/null  # the last try, with its error shown
}

# Every value the deploy needs, checked before anything is created.
missing=()
for key in NEON_APP_DATABASE_URL DEPLOY_GEMINI_API_KEY DEPLOY_GEMINI_PROJECT_ID OBSERVABILITY_SALT LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY \
           LANGFUSE_BASE_URL GOOGLE_APPLICATION_CREDENTIALS FIREBASE_PROJECT_ID VITE_FIREBASE_API_KEY \
           VITE_FIREBASE_AUTH_DOMAIN VITE_FIREBASE_PROJECT_ID VITE_FIREBASE_APP_ID; do
  [[ -n "$(env_value "$key")" ]] || missing+=("$key")
done
[[ -f "$(env_value GOOGLE_APPLICATION_CREDENTIALS)" ]] || missing+=("the Firebase key file GOOGLE_APPLICATION_CREDENTIALS names")
if [[ -n "$(env_value DEPLOY_GEMINI_API_KEY)" && "$(env_value DEPLOY_GEMINI_API_KEY)" == "$(env_value GEMINI_API_KEY)" ]]; then
  missing+=("a DEPLOY_GEMINI_API_KEY different from the eval key GEMINI_API_KEY")
fi
KEY_PROJECT="$(env_value DEPLOY_GEMINI_PROJECT_ID)"
if [[ -n "$KEY_PROJECT" && "$KEY_PROJECT" == "$PROJECT_ID" ]]; then
  missing+=("a DEPLOY_GEMINI_PROJECT_ID other than PROJECT_ID (the deploy project has billing on)")
fi
if (( ${#missing[@]} )); then
  printf '.env lacks: %s\n' "${missing[@]}" >&2
  $APPLY && exit 1
  echo "(dry run continues; --apply would stop here)" >&2
fi

if $APPLY; then
  [[ "$(gcloud billing projects describe "$PROJECT_ID" --format='value(billingEnabled)')" == "True" ]] || {
    echo "Billing is not enabled on $PROJECT_ID. This script never turns it on: do that yourself first." >&2; exit 1; }
  # A Gemini key takes on its project's billing status: with billing on, demo
  # traffic is billed per token instead of hitting the free tier's limits.
  if key_billing="$(gcloud billing projects describe "$KEY_PROJECT" --format='value(billingEnabled)' 2>&1)" \
     && [[ "$key_billing" == True || "$key_billing" == False ]]; then
    [[ "$key_billing" == False ]] || {
      echo "Billing is on for $KEY_PROJECT, the deploy key's project: the key would be billed per token. Use a key from a project with billing off." >&2; exit 1; }
  elif [[ "${DEPLOY_KEY_PROJECT_UNBILLED:-}" == yes ]]; then
    echo "Could not read billing for $KEY_PROJECT ($key_billing); continuing because DEPLOY_KEY_PROJECT_UNBILLED=yes." >&2
  else
    echo "Could not read billing for $KEY_PROJECT, the deploy key's project: $key_billing" >&2
    echo "Refusing: without that check the key may be billed per token. If you know billing is off there, set DEPLOY_KEY_PROJECT_UNBILLED=yes." >&2
    exit 1
  fi
fi

run gcloud services enable run.googleapis.com artifactregistry.googleapis.com cloudbuild.googleapis.com \
  secretmanager.googleapis.com --project "$PROJECT_ID"
create gcloud artifacts repositories create finpilot --repository-format=docker --location "$REGION" --project "$PROJECT_ID"
# Keep the latest 3 versions of each image, delete older ones (a Keep rule wins
# over a Delete rule; --no-dry-run, or the policy only logs what it would delete).
run gcloud artifacts repositories set-cleanup-policies finpilot --location "$REGION" --project "$PROJECT_ID" \
  --policy "$ROOT/scripts/gcp_cleanup_policy.json" --no-dry-run

# A budget alert: emails the billing account's admins at 50%, 90% and 100% of
# BUDGET_USD. It only alerts; it never caps or stops spending.
BUDGET_USD="${BUDGET_USD:-5}"
BILLING_ACCOUNT="<billing-account-id>"
$APPLY && BILLING_ACCOUNT="$(gcloud billing projects describe "$PROJECT_ID" --format='value(billingAccountName)')"
run gcloud services enable billingbudgets.googleapis.com --project "$PROJECT_ID"
# Budgets have no unique name, so a create never says "already exists": look for ours first.
if $APPLY && [[ -n "$(gcloud billing budgets list --billing-account "${BILLING_ACCOUNT#billingAccounts/}" \
      --filter "displayName=finpilot-$PROJECT_ID" --format 'value(name)')" ]]; then
  echo "exists, kept: budget finpilot-$PROJECT_ID"
else
  run gcloud billing budgets create --billing-account "${BILLING_ACCOUNT#billingAccounts/}" \
    --display-name "finpilot-$PROJECT_ID" --budget-amount "${BUDGET_USD}USD" --filter-projects "projects/$PROJECT_ID" \
    --threshold-rule percent=0.5 --threshold-rule percent=0.9 --threshold-rule percent=1.0
fi
create gcloud iam service-accounts create finpilot-backend --display-name "FinPilot backend" --project "$PROJECT_ID"

# Six secrets: the Secret Manager free tier covers six active versions, so a
# re-run adds a version only when the value changed.
secret() {  # secret NAME VALUE_OR_@FILE
  local name="finpilot-$1"
  if ! $APPLY; then
    echo "+ secret $name (value from .env; a new version only if it changed; access for $SA, retried)"
    return
  fi
  create gcloud secrets create "$name" --replication-policy automatic --project "$PROJECT_ID"
  # The value goes through a pipe, never a temp file; a file secret is read where it is.
  value() { if [[ "$2" == @* ]]; then cat "${2#@}"; else printf '%s' "$2"; fi; }
  if cmp -s <(value "$@") <(gcloud secrets versions access latest --secret "$name" --project "$PROJECT_ID" 2>/dev/null); then
    echo "unchanged, kept: $name"
  else
    value "$@" | gcloud secrets versions add "$name" --data-file - --project "$PROJECT_ID" >/dev/null
    echo "new version: $name"
  fi
  bind gcloud secrets add-iam-policy-binding "$name" --member "serviceAccount:$SA" \
    --role roles/secretmanager.secretAccessor --project "$PROJECT_ID"
}
secret database-url "$(env_value NEON_APP_DATABASE_URL)"  # finpilot_app only, never the owner
secret gemini-api-key "$(env_value DEPLOY_GEMINI_API_KEY)"  # the deployed app's own key, not the eval key
secret observability-salt "$(env_value OBSERVABILITY_SALT)"
secret langfuse-public-key "$(env_value LANGFUSE_PUBLIC_KEY)"
secret langfuse-secret-key "$(env_value LANGFUSE_SECRET_KEY)"
secret firebase-key "@$(env_value GOOGLE_APPLICATION_CREDENTIALS)"

# Images, built by Cloud Build (no local Docker needed).
run gcloud builds submit "$ROOT/backend" --tag "$REPO/backend:$TAG" --project "$PROJECT_ID"
run gcloud builds submit "$ROOT/frontend" --config "$ROOT/frontend/cloudbuild.yaml" --project "$PROJECT_ID" \
  --substitutions "_IMAGE=$REPO/frontend:$TAG,_FB_API_KEY=$(env_value VITE_FIREBASE_API_KEY),_FB_AUTH_DOMAIN=$(env_value VITE_FIREBASE_AUTH_DOMAIN),_FB_PROJECT_ID=$(env_value VITE_FIREBASE_PROJECT_ID),_FB_APP_ID=$(env_value VITE_FIREBASE_APP_ID)"

# Max instances 2 caps the bill if the URL gets hammered. The app checks
# Firebase tokens itself, so the services are public at the Cloud Run level.
run gcloud run deploy finpilot-backend --image "$REPO/backend:$TAG" --region "$REGION" --project "$PROJECT_ID" \
  --service-account "$SA" --min-instances 0 --max-instances 2 --cpu 1 --memory 2Gi \
  --concurrency 10 --timeout 300 --allow-unauthenticated \
  --set-env-vars "LLM_PROVIDER=gemini,FIREBASE_PROJECT_ID=$(env_value FIREBASE_PROJECT_ID),LANGFUSE_BASE_URL=$(env_value LANGFUSE_BASE_URL),GOOGLE_APPLICATION_CREDENTIALS=/secrets/firebase/key.json" \
  --set-secrets "DATABASE_URL=finpilot-database-url:latest,GEMINI_API_KEY=finpilot-gemini-api-key:latest,OBSERVABILITY_SALT=finpilot-observability-salt:latest,LANGFUSE_PUBLIC_KEY=finpilot-langfuse-public-key:latest,LANGFUSE_SECRET_KEY=finpilot-langfuse-secret-key:latest,/secrets/firebase/key.json=finpilot-firebase-key:latest"

# The backend's real URL, read back from the deploy above: no manual edit.
BACKEND_URL="https://finpilot-backend-<hash>.run.app"
if $APPLY; then
  BACKEND_URL="$(gcloud run services describe finpilot-backend --region "$REGION" --project "$PROJECT_ID" --format 'value(status.url)')"
  [[ "$BACKEND_URL" == https://* ]] || { echo "Could not read the backend URL (got '$BACKEND_URL')." >&2; exit 1; }
  echo "backend: $BACKEND_URL"
fi
run gcloud run deploy finpilot-frontend --image "$REPO/frontend:$TAG" --region "$REGION" --project "$PROJECT_ID" \
  --min-instances 0 --max-instances 2 --cpu 1 --memory 256Mi --timeout 300 --allow-unauthenticated \
  --set-env-vars "BACKEND_URL=$BACKEND_URL"

FRONTEND_URL="https://finpilot-frontend-<hash>.run.app"
$APPLY && FRONTEND_URL="$(gcloud run services describe finpilot-frontend --region "$REGION" --project "$PROJECT_ID" --format 'value(status.url)')"
echo "Then, by hand: in the Firebase project $(env_value FIREBASE_PROJECT_ID), add ${FRONTEND_URL#https://} to"
echo "Authentication > Settings > Authorized domains (sign-in fails on that host until then)."
$APPLY || echo "(dry run: nothing was created; add --apply to run these)"
