# Deploying FinPilot to Cloud Run (Phase 12)

Nothing here has been created on GCP yet, and no billing has been turned on.
The backend and frontend run on Cloud Run with min instances 0. Neon (the
database) and Firebase (sign-in) stay as they are.

```bash
PROJECT_ID=my-project scripts/gcp_deploy.sh            # dry run: prints every command
PROJECT_ID=my-project scripts/gcp_deploy.sh --apply    # creates the services
PROJECT_ID=my-project scripts/gcp_teardown.sh --apply  # removes everything deploy made
```

Before `--apply`: run `gcloud auth login` (the saved sign-in on the dev
machine had expired on 2026-11-24), and put `DEPLOY_GEMINI_API_KEY` in `.env`.

What the deploy script does:

- **Checks first:** it stops before creating anything if a value it needs is
  missing from `.env`, or if `DEPLOY_GEMINI_API_KEY` is the same as the eval
  key `GEMINI_API_KEY`.
- **Billing:** it stops if the project has no billing; it never turns billing on.
- **Secrets:** six values from `.env` go into Secret Manager (the Neon app URL,
  the deployed app's own Gemini key `DEPLOY_GEMINI_API_KEY`, observability salt,
  Langfuse keys, Firebase key file), readable only by a dedicated service
  account. Values go to gcloud through a pipe, never a temporary file.
- **Re-runs are safe:** a resource that already exists is kept, as is a budget
  with the same name. A secret gets a new version only when its value changed,
  so re-runs stay within the free 6 versions. The service account's
  secret-access bindings are retried for up to a minute while a new account
  becomes visible to IAM.
- **The backend URL is wired automatically:** after deploying the backend, the
  script reads its real `run.app` URL and passes it to the frontend as
  `BACKEND_URL`.
- **Images:** built by Cloud Build, so no local Docker is needed. CI builds
  both images on every push (never pushed anywhere), so a broken Dockerfile or
  nginx template shows up before a deploy.
- **Image cleanup:** an Artifact Registry cleanup policy
  (`scripts/gcp_cleanup_policy.json`) keeps the latest 3 versions of each image
  and deletes older ones, so storage stays at about 3 images.
- **Budget alert:** a billing budget of `BUDGET_USD` (default $5) on this
  project emails the billing account's admins at 50%, 90% and 100%. **It only
  alerts: it never caps or stops spending.** The max-instances cap below is
  what bounds the bill. Creating a budget needs the Billing Account
  Administrator or Costs Manager role on the billing account; the teardown
  deletes it.
- **Services:** both services deploy with max instances 2, which caps the bill.

Two steps stay manual:

- In the **Firebase project `documind-29040`** (the one `FIREBASE_PROJECT_ID`
  names, not the Cloud Run project), add the frontend's `run.app` host under
  Authentication > Settings > Authorized domains. The script prints the exact
  host at the end. Sign-in fails on that host until then.
- Migrations: Neon is already migrated, and the owner URL never reaches Cloud Run.

## Monthly cost estimate

**What it includes:** the Google Cloud resources the deploy script creates:
Cloud Run (both services), Artifact Registry, Secret Manager and Cloud Build.

**What it excludes:**

- **Gemini calls:** the app's model calls, about 3 per question. They stay on
  the Gemini API free tier, whose daily limits cap usage; a paid key would bill
  per token on top of this estimate.
- **Neon:** the database stays on its current plan, billed by Neon, not Google.
- **Firebase:** sign-in stays on its current plan.
- Langfuse (traces) and network egress: Langfuse is billed separately, and the
  answers are a few KB each. Egress pricing wasn't checked.

The prices are the request-based billing rates for a Tier 1 region
(us-central1), looked up on 2026-11-24. **None is verified against the
official page.** The pages render client-side, so fetching them returned only
their titles. Every figure comes from search results quoting those pages.
Check each one on the page before you deploy.

| Item | Price | Free each month (per billing account) | Checked |
|---|---|---|---|
| Cloud Run CPU, request-based | $0.000024 per vCPU-second | 180,000 vCPU-seconds | unverified |
| Cloud Run memory, request-based | $0.0000025 per GiB-second | 360,000 GiB-seconds | unverified |
| Cloud Run requests | $0.40 per million | 2 million | unverified |
| Artifact Registry storage | about $0.10 per GiB-month | 0.5 GB | unverified |
| Secret Manager | $0.06 per active version | 6 versions, 10,000 accesses | unverified |
| Cloud Build | $0.006 per build-minute | 2,500 build-minutes (e2-standard-2) | unverified |

Links: [Cloud Run](https://cloud.google.com/run/pricing),
[Artifact Registry](https://cloud.google.com/artifact-registry/pricing),
[Secret Manager](https://cloud.google.com/secret-manager/pricing),
[Cloud Build](https://cloud.google.com/build/pricing),
[Free program](https://docs.cloud.google.com/free/docs/free-cloud-features).

**Sizing.** The backend gets 1 vCPU and 2 GiB, for the reranker plus headroom;
its memory hasn't been measured on Cloud Run. The frontend (nginx) gets 1 vCPU
and 256 MiB. An instance is billed while it handles a request. A question
takes about 20 s, mostly waiting on Gemini. The frontend proxies the answer
stream, so it's billed for the same 20 s. A cold start adds about 20 s to the
backend.

The usage is assumed: a 500-question month is a portfolio demo, and 5,000 is a
busy one. Both counts assume no two requests share an instance, which is the
worst case for billing.

| | 500 questions/month | 5,000 questions/month |
|---|---|---|
| Backend vCPU-s (answers + cold starts) | 10,000 + 2,000 | 100,000 + 10,000 |
| Frontend vCPU-s | 12,000 | 110,000 |
| Total vCPU-s | 24,000 (free) | 220,000: 40,000 over, **$0.96** |
| GiB-s (backend 2 GiB, frontend 0.25) | 27,000 (free) | 247,500 (free) |
| Requests | under 10,000 (free) | under 100,000 (free) |
| Artifact Registry: 3 kept versions of a ~1-2 GB backend image (not measured); layers they share are stored once, so 1.5-6 GB | ~$0.10-0.55 | ~$0.10-0.55 |
| Secret Manager (6 versions) | free | free |
| Cloud Build (~10-15 min per deploy) | free | free |
| **Total** | **about $0.10-0.55** | **about $1.05-1.50** |

Things that change the estimate:

- **Image storage:** the cleanup policy keeps 3 versions of each image, so
  storage stays near the line above however often you deploy. Without it, ten
  versions of a 1.5 GB image would come to about $1.50 a month.
- **Shared free tier:** it's per billing account, so other projects on the same
  account use it up too.
- **Gemini and Neon aren't counted:** both stay on their free tiers. At 5,000
  questions a month (about 3 model calls each), Gemini's free-tier daily
  limits, not Cloud Run, are the ceiling.
- **Max instances 2:** a flood of traffic queues and fails instead of
  scaling, so the bill stays bounded.
