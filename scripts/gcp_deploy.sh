#!/usr/bin/env bash
# Deploy FinPilot to Cloud Run: backend and frontend, min instances 0.
# Neon (the database) and Firebase (sign-in) stay as they are.
#
#   PROJECT_ID=my-project scripts/gcp_deploy.sh            # dry run: prints every command
#   PROJECT_ID=my-project scripts/gcp_deploy.sh --apply    # runs them
#
# Never enables billing: it stops if the project has none. Reads the repo-root
# .env for secrets (stored in Secret Manager, never in an image or a flag).
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

run() { if $APPLY; then "$@"; else printf '+ %q' "$1"; shift; printf ' %q' "$@"; echo; fi; }
env_value() { grep -E "^$1=" "$ROOT/.env" | tail -1 | cut -d= -f2-; }

if $APPLY; then
  [[ "$(gcloud billing projects describe "$PROJECT_ID" --format='value(billingEnabled)')" == "True" ]] || {
    echo "Billing isn't enabled on $PROJECT_ID. This script never turns it on: do that yourself first." >&2; exit 1; }
fi

run gcloud services enable run.googleapis.com artifactregistry.googleapis.com cloudbuild.googleapis.com \
  secretmanager.googleapis.com --project "$PROJECT_ID"
run gcloud artifacts repositories create finpilot --repository-format=docker --location "$REGION" --project "$PROJECT_ID"
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
run gcloud billing budgets create --billing-account "${BILLING_ACCOUNT#billingAccounts/}" \
  --display-name "finpilot-$PROJECT_ID" --budget-amount "${BUDGET_USD}USD" --filter-projects "projects/$PROJECT_ID" \
  --threshold-rule percent=0.5 --threshold-rule percent=0.9 --threshold-rule percent=1.0
run gcloud iam service-accounts create finpilot-backend --display-name "FinPilot backend" --project "$PROJECT_ID"

# Six secrets: the Secret Manager free tier covers six active versions.
secret() {  # secret NAME VALUE_OR_@FILE
  local name="finpilot-$1"
  if $APPLY; then
    gcloud secrets describe "$name" --project "$PROJECT_ID" >/dev/null 2>&1 \
      || gcloud secrets create "$name" --replication-policy automatic --project "$PROJECT_ID"
    if [[ "$2" == @* ]]; then gcloud secrets versions add "$name" --data-file "${2#@}" --project "$PROJECT_ID"
    else printf '%s' "$2" | gcloud secrets versions add "$name" --data-file - --project "$PROJECT_ID"; fi
    gcloud secrets add-iam-policy-binding "$name" --member "serviceAccount:$SA" \
      --role roles/secretmanager.secretAccessor --project "$PROJECT_ID" >/dev/null
  else
    echo "+ secret $name (value from .env, piped to gcloud secrets versions add; access for $SA)"
  fi
}
secret database-url "$(env_value NEON_APP_DATABASE_URL)"  # finpilot_app only, never the owner
secret gemini-api-key "$(env_value GEMINI_API_KEY)"
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

BACKEND_URL="https://finpilot-backend-<hash>.run.app"
$APPLY && BACKEND_URL="$(gcloud run services describe finpilot-backend --region "$REGION" --project "$PROJECT_ID" --format 'value(status.url)')"
run gcloud run deploy finpilot-frontend --image "$REPO/frontend:$TAG" --region "$REGION" --project "$PROJECT_ID" \
  --min-instances 0 --max-instances 2 --cpu 1 --memory 256Mi --timeout 300 --allow-unauthenticated \
  --set-env-vars "BACKEND_URL=$BACKEND_URL"

echo "Then, by hand: add the frontend's run.app host to Firebase Authentication > Settings > Authorized domains."
$APPLY || echo "(dry run: nothing was created; add --apply to run these)"
