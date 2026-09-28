# Parking charge appeal - customer frontend

Next.js (App Router, TypeScript) UI for the PCN appeal pipeline. Three screens,
driven entirely by the API response: upload the notice -> answer whatever facts
are missing -> read the letter.

## Running it

The FastAPI backend has to be up first, from the repository root:

```bash
source .venv/bin/activate
uvicorn pcn_appeal.api:app --port 8077
```

Then, in this directory:

```bash
npm install
npm run dev          # http://localhost:3000
```

## Environment

| Variable      | Default                 | Purpose                        |
| ------------- | ----------------------- | ------------------------------ |
| `PCN_API_URL` | `http://127.0.0.1:8077` | Base URL of the FastAPI backend |

Copy `.env.example` to `.env.local` to override it.

## It proxies FastAPI

The browser never calls FastAPI directly. Every request goes to a Next route
handler at `app/api/[...path]/route.ts`, which forwards it to `PCN_API_URL`
and streams the response back. FastAPI has no CORS configuration, and this
keeps the backend address server-side. Request bodies are forwarded as raw
bytes so multipart upload boundaries are preserved exactly.

So `POST /api/appeal/files` in the browser reaches `POST /appeal/files` on the
backend, and likewise for `/api/appeal/{case_id}`, `/api/health` and
`/api/cases/{case_id}/trace`.

## ESLint

Not installed. The scaffold's `eslint-config-next` tree could not be resolved
through this machine's npm mirror in workable time (50-100s per package), so it
was removed to keep `npm install` usable. Nothing in the app depends on it and
`next build` does not run it. To add it back:

```bash
npm i -D eslint eslint-config-next
```

`npm run typecheck` (`tsc --noEmit`) covers type safety in the meantime.

## Layout

```
app/
  layout.tsx              fonts + document shell
  page.tsx                the state machine: upload -> questions -> result
  globals.css             design tokens and all component styling
  api/[...path]/route.ts  proxy to FastAPI
components/
  ProgressRail.tsx        three-step progress indicator
  UploadStep.tsx          drag/drop, file picker, camera capture, narrative
  QuestionsStep.tsx       one round of questions, plus "skip the rest"
  ResultStep.tsx          letter + copy button, or the manual-review reasons
  Notices.tsx             flags, rejected files and what was read, in plain words
lib/
  api.ts                  fetch wrappers over the proxy
  flags.ts                flag codes -> plain language
  types.ts                the API response shapes
```

## Two things to know before changing it

- **Nothing about driver identity.** The journey never asks who was driving.
  The one related input is a status checkbox meaning the operator has already
  been formally told, sent as `driver_already_named_to_operator`.
- **The UI writes no appeal wording.** Letter text, grounds and blocking
  reasons are rendered from the API response only. If there is no `letter`,
  no letter is shown.
