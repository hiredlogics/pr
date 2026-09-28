# Deploying

Two pieces, deployed separately:

| Piece | Where | Why |
|---|---|---|
| `frontend/` (Next.js) | Vercel | Static-ish, edge-cached, trivial to ship |
| `pcn_appeal/` (FastAPI) | Any container host | Stateful, 60MB native PDF dependency, 10–15s vision calls |

The split is already wired: the Next proxy at `app/api/[...path]/route.ts` forwards
`/api/*` to `process.env.PCN_API_URL`, so the browser only ever talks to one origin
and FastAPI needs no CORS configuration.

## Why the API is not on Vercel

Three things make a serverless function the wrong shape for this service.

**Case state.** A case is created, then uploaded to, then confirmed, then answered.
Function instances are ephemeral and not shared, so step 2 can land somewhere that
has never heard of step 1. The Postgres store fixes this, but then you are paying
for a database round trip to work around the platform rather than because the
design wanted one.

**Request body size.** A serverless function caps the body at a few megabytes. A
phone photo of a parking notice is routinely more. Blob uploads (below) solve this
for any host, but it is the reason the multipart route alone is not enough.

**Bundle size.** PyMuPDF is ~59MB, NumPy ~34MB, the OpenAI SDK ~24MB. That fits
inside a 250MB function, but with little room and a native extension to worry about.

None of these are problems for a long-lived container.

## 1. The API

Set these wherever you host it:

| Variable | Required | Notes |
|---|---|---|
| `OPENAI_API_KEY` | yes | Without it the service silently runs the demo reader |
| `DATABASE_URL` | yes in production | Postgres with pgvector. Without it, case state is per-process |
| `BLOB_ALLOWED_HOSTS` | yes if using blob uploads | Comma-separated hosts the API may fetch from |
| `LLM_PROVIDER` | no | Set to `openai` to make a bad key a hard startup failure instead of a demo fallback |
| `PORT` | no | Railway and Render inject it; the image honours it |

Initialise the database once, against a **database of its own** — the schema creates
a `cases` table and will collide with an unrelated one:

```bash
DATABASE_URL=postgres://... python -m pcn_appeal.store init
DATABASE_URL=postgres://... python -m pcn_appeal.store sync   # publishes a KB release
```

### Fly.io

```bash
fly launch --no-deploy --name pcn-appeal-api
fly secrets set OPENAI_API_KEY=sk-... DATABASE_URL=postgres://...
fly deploy
```

### Railway / Render

Point the service at the repository root; both detect the `Dockerfile`. Set the
variables above. No platform-specific file is needed.

### Check it

```bash
curl https://<your-api>/health
```

`provider` must read `openai` and `store` must read `postgres`. If `provider` says
`demo`, the key is not reaching the container.

## 2. The frontend

```bash
cd frontend
vercel link
vercel env add PCN_API_URL production     # https://<your-api>
vercel deploy --prod
```

### Blob uploads

Large files must not pass through the Vercel function. The browser uploads directly
to storage and sends the API only URLs, which it fetches via `POST /cases/{id}/blobs`.

Add a Blob store to the Vercel project (this sets `BLOB_READ_WRITE_TOKEN`), then set
`BLOB_ALLOWED_HOSTS` on the API to that store's hostname.

That URL is client-supplied, so the API treats it as hostile: https only, host
allowlist, and a download capped at `ingest.MAX_BYTES`. Leaving the allowlist empty
turns the endpoint into an SSRF hole — "fetch this URL for me" pointed at cloud
metadata — so narrow it to your own store rather than disabling it.

## Before real customers

Deployment does not settle any of these:

1. Legal sign-off on `legal/pofa.py` and every `code_versions.yaml` value.
2. `verified: true` is set on placeholder Code versions, which is what permits the
   letter to quote grace-period figures. Confirm the values or clear the flag.
3. No authentication anywhere. Every `/cases/{id}` route is readable by anyone who
   can guess an id, and ids are sequential in memory mode.
4. Letters carry no sender name or address, so they are not yet postable.
5. The admin KB routes return 501 by design; they need RBAC and dual control before
   they do anything.
6. DPIA for the health and disability data the equality route collects.
