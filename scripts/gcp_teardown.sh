#!/usr/bin/env bash
# Remove everything scripts/gcp_deploy.sh created: both Cloud Run services,
# the images, the secrets, the service account and the budget alert. Neon and Firebase are
# untouched; the project and its billing link stay (delete those yourself).
#
#   PROJECT_ID=my-project scripts/gcp_teardown.sh            # dry run: prints every command
#   PROJECT_ID=my-project scripts/gcp_teardown.sh --apply    # runs them
set -uo pipefail  # no -e: carry on past what's already gone

PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID}"
REGION="${REGION:-us-central1}"
APPLY=false; [[ "${1:-}" == "--apply" ]] && APPLY=true
run() { if $APPLY; then "$@"; else printf '+ %q' "$1"; shift; printf ' %q' "$@"; echo; fi; }

for service in finpilot-frontend finpilot-backend; do
  run gcloud run services delete "$service" --region "$REGION" --project "$PROJECT_ID" --quiet
done
run gcloud artifacts repositories delete finpilot --location "$REGION" --project "$PROJECT_ID" --quiet
for name in database-url gemini-api-key observability-salt langfuse-public-key langfuse-secret-key firebase-key; do
  run gcloud secrets delete "finpilot-$name" --project "$PROJECT_ID" --quiet
done
run gcloud iam service-accounts delete "finpilot-backend@$PROJECT_ID.iam.gserviceaccount.com" \
  --project "$PROJECT_ID" --quiet
if $APPLY; then
  account="$(gcloud billing projects describe "$PROJECT_ID" --format='value(billingAccountName)')"
  for budget in $(gcloud billing budgets list --billing-account "${account#billingAccounts/}" \
      --filter "displayName=finpilot-$PROJECT_ID" --format 'value(name)'); do
    gcloud billing budgets delete "$budget" --quiet
  done
else
  echo "+ gcloud billing budgets delete <the budget named finpilot-$PROJECT_ID> --quiet"
fi
$APPLY || echo "(dry run: nothing was deleted; add --apply to run these)"
