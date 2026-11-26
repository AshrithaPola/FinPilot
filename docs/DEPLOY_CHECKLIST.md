# Deploy checklist: what to do yourself, in order

`scripts/gcp_deploy.sh` does the deploying. These are the steps it can't do for you, or refuses to
do: accounts, billing, keys and one console setting. Do them in this order. Run the commands in Git
Bash from the repo root, signed in as your personal Google account (polaashritha@gmail.com), so the
live app depends on nothing tied to your university account (the Neon move is under "Later").

Nothing here prints a secret. Values go into the repo-root `.env` (gitignored), never into the repo.

## 1. Sign in to gcloud as your personal account

```bash
gcloud auth login                              # pick polaashritha@gmail.com in the browser
gcloud config set account polaashritha@gmail.com
gcloud auth list                               # the active account should be the Gmail one
```

The saved sign-in on this machine had expired (2026-11-24).

## 2. Create the GCP project, turn billing on and plan the budget

```bash
PROJECT_ID=finpilot-<something-unique>         # lowercase, digits, hyphens; globally unique
gcloud projects create "$PROJECT_ID" --name "FinPilot"
gcloud config set project "$PROJECT_ID"
gcloud billing accounts list                   # your billing account's ID (create one in the console if none)
gcloud billing projects link "$PROJECT_ID" --billing-account <BILLING_ACCOUNT_ID>
gcloud billing projects describe "$PROJECT_ID" --format='value(billingEnabled)'   # expect: True
```

The deploy script refuses to run on a project without billing, and never turns billing on itself.

**Budget.** The script creates a budget alert (default **$5**, emails at 50%, 90% and 100%). Set
another amount with `BUDGET_USD=<n>` when you run it. Creating a budget needs the **Billing Account
Administrator** (or Costs Manager) role on the billing account, which the account's creator has. A
budget only alerts and never stops spending; max instances 2 is what bounds the bill.

## 3. Create the deploy Gemini key in a project with billing off

A Gemini key takes on its project's billing status. A key from a billed project is charged per
token instead of hitting the free tier's limits, so the deployed app's key must come from a
different project with billing **off**:

```bash
KEY_PROJECT=finpilot-gemini-<something-unique>
gcloud projects create "$KEY_PROJECT" --name "FinPilot Gemini"            # don't link billing
gcloud billing projects describe "$KEY_PROJECT" --format='value(billingEnabled)'   # expect: False
```

Then, in [Google AI Studio](https://aistudio.google.com/apikey) signed in as the Gmail account,
create an API key **in that project**, and add both values to `.env`:

```
DEPLOY_GEMINI_API_KEY=<the new key>
DEPLOY_GEMINI_PROJECT_ID=<KEY_PROJECT>
```

The script checks that the key differs from the eval key `GEMINI_API_KEY`, that `KEY_PROJECT`
differs from `PROJECT_ID`, and that billing is off for `KEY_PROJECT`. It refuses otherwise.

## 4. Check every value the script needs is in `.env`

`--apply` stops before creating anything if any of these is empty:

| Key | What it is |
|---|---|
| `NEON_APP_DATABASE_URL` | `finpilot_app` on the pooled host, written by `scripts/neon.py setup` (never the owner URL) |
| `DEPLOY_GEMINI_API_KEY`, `DEPLOY_GEMINI_PROJECT_ID` | Step 3 |
| `OBSERVABILITY_SALT` | Hashes user ids in traces; any long random string |
| `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_BASE_URL` | Your Langfuse project |
| `GOOGLE_APPLICATION_CREDENTIALS` | Path to the Firebase service-account key file, which must exist (outside the repo) |
| `FIREBASE_PROJECT_ID` | The Firebase project (`documind-29040`) |
| `VITE_FIREBASE_API_KEY`, `VITE_FIREBASE_AUTH_DOMAIN`, `VITE_FIREBASE_PROJECT_ID`, `VITE_FIREBASE_APP_ID` | The Firebase web config |

The dry run lists anything missing without creating anything:

```bash
PROJECT_ID=<PROJECT_ID> scripts/gcp_deploy.sh
```

**Accounts:** Firebase (`documind-29040`), Langfuse (project "finpilot") and GitHub
(AshrithaPola/FinPilot) are already on your personal account. Only the Neon project isn't yet
(see "Later").

## 5. Deploy

When you've decided to deploy:

```bash
PROJECT_ID=<PROJECT_ID> scripts/gcp_deploy.sh --apply
```

It enables the APIs, creates the Artifact Registry repo with a keep-3 cleanup policy, the budget,
the service account and six secrets. It builds both images with Cloud Build, deploys both Cloud
Run services, and prints the frontend's `run.app` host.

## 6. Authorize the frontend's host in Firebase

In the Firebase console for **`documind-29040`** (not the Cloud Run project): Authentication >
Settings > Authorized domains > Add domain, and paste the host the script printed. Sign-in fails on
that host until then.

## 7. Check the live app

- Sign in as a demo customer and ask "Why was I charged $15 on April 6?". Expect an answer citing the
  fee, the transaction and the fee schedule.
- Ask for another customer's balance. Expect a refusal.
- Watch the budget emails for the first days.

To remove everything the deploy created: `PROJECT_ID=<PROJECT_ID> scripts/gcp_teardown.sh --apply`.

## Later

- Move the Neon project from my UMD account to polaashritha@gmail.com.
