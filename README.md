# Questionnaire-AI
React + FastAPI + PostgreSQL assessments using the OpenAI Responses API. Students sign in with Google, choose a subject and level, answer five questions, and review saved scores, explanations, feedback, and performance history.

## Assessment rules

- Subjects: SQL, Python, Statistics, Data Science, Machine Learning, Deep Learning, MLOps, AI Engineering.
- Levels: Easy, Medium, Hard, Production grade.
- Each test has four single-answer questions and one clearly labeled multiple-answer question, with four options each. The multiple-answer key has two or three correct options.
- One mark per question. Multiple-answer scoring requires the exact set of correct options: no partial credit, no negative marking. Tests are untimed; retakes create separate attempts.
- Production grade uses debugging, deployment, reliability, constraints, and operational tradeoffs adapted to the selected subject.
- The LLM generates questions, independently reviews suitability and answer keys, and generates feedback. Generation and review schemas explicitly require four single-answer questions and one multiple-answer question, including their answer counts. The backend calculates numerical marks against the reviewed key. Correct options and explanations are withheld until submission.

## Local setup (PowerShell)

Run from the repository root. Install Python 3.11+ and Node.js LTS (including npm).

```powershell
python -m venv .venv-local
.\.venv-local\Scripts\python.exe -m pip install -r requirements.txt
```

Create `.env` from `.env.example` if needed, and fill in real credentials:

```dotenv
LAYER_BASE_CONNECTION_STRING=postgresql://USER:PASSWORD@HOST:5432/DATABASE?sslmode=require
GOOGLE_CLIENT_ID=actual_client_id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=actual_client_secret
GOOGLE_REDIRECT_URI=http://localhost:8501
OPENAI_API_KEY=actual_openai_api_key
OPENAI_MODEL=gpt-4.1
```

These are placeholders. Keep the API key and client secret on the backend; never use a `VITE_` variable for them. Existing setups may store the OpenAI key under `LAYER_BASE`; the backend uses it when `OPENAI_API_KEY` is empty or absent. A nonempty `OPENAI_API_KEY` takes priority. `DATABASE_URL`, if set, overrides `LAYER_BASE_CONNECTION_STRING`. The configured model must support structured outputs through the Responses API.

Register `http://localhost:8501` as an authorized redirect URI on a Google OAuth **Web application** client. Use that exact address for sign-in. A fresh login is required after upgrading from the old profile-only login implementation.

Apply database migrations before starting the backend:

```powershell
.\.venv-local\Scripts\python.exe -m alembic upgrade head
.\.venv-local\Scripts\python.exe -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8001 --reload
```

In a separate terminal:

```powershell
cd frontend
$env:Path = "C:\Program Files\nodejs;" + $env:Path
& "C:\Program Files\nodejs\npm.cmd" ci
& "C:\Program Files\nodejs\npm.cmd" run dev
```

Open [the portal](http://localhost:8501) and [API documentation](http://127.0.0.1:8001/docs). Vite proxies `/api` to port 8001. For a separately hosted API, set `VITE_API_BASE_URL` in `frontend/.env.local` and explicitly configure its CORS origins.

## Storage and identity

`users` retains existing Google identities. `login_events` records successful sign-ins; `login_sessions` stores hashed, revocable tokens that expire after eight hours; `oauth_states` stores single-use sign-in state. Raw Google tokens are not persisted. The browser keeps the app session in session storage.

`assessment_attempts` links each student to a subject, level, status, model, prompt version, and timestamps. `questions`, `answers`, `results`, and `feedback` link back to the attempt. Draft answers save as selections change; History supports resume, discard, and result review. A student can read or update only their own assessments. Performance summaries aggregate all completed attempts by subject and level; the attempt list shows the latest 50.

Submission is serialized using PostgreSQL row locks. Repeating the same submission returns the existing score; different answers after submission are rejected. Scores are committed before feedback generation; if OpenAI fails, results remain available and the UI offers feedback retry. Generation requests use unique IDs, with up to three active attempts per student. Failed generation attempts stay visible in history. Generating attempts older than ten minutes are marked failed when a new attempt is created.

The initial migration creates missing tables and preserves existing users. It can adopt compatible assessment tables previously created by `create_all`; incompatible table definitions stop the migration without replacing tables. Schema changes should use subsequent Alembic revisions. Startup checks the assessment schema rather than changing it.

## Code locations

- `backend/app/services/examination.py`: subject scope, professional-level prompts, independent key review, OpenAI calls, feedback, scoring rule.
- `backend/app/models/examination.py`: linked database models.
- `backend/app/api/v1/endpoints/examinations.py`: assessment, draft, submission, history, and feedback endpoints.
- `backend/app/api/v1/endpoints/auth.py`: Google identity verification and database sessions.
- `frontend/src/App.jsx`: login, setup, questions, results, and performance UI.

## Verification

Offline integration tests use a temporary SQLite database and mocked OpenAI/Google responses:

```powershell
.\.venv-local\Scripts\python.exe -m unittest discover -s backend/tests -v
cd frontend
npm run build
npm run lint
```

An opt-in PostgreSQL test checks concurrent creation/submission and persistence in a disposable schema, without changing public student records. It uses mocked LLM responses and needs CREATE SCHEMA permission:

```powershell
.\.venv-local\Scripts\python.exe -m backend.tests.postgres_smoke
```

For a paid live audit of all 32 subject/level combinations, including generation,
draft saving, resume, submission, feedback, history, and database row counts:

```powershell
.\.venv-local\Scripts\python.exe -u -m backend.tests.live_matrix --concurrency 3
```

This uses real OpenAI calls and the configured PostgreSQL database in an isolated,
temporary schema. It preserves public student records and removes its own test
schema after verification. JSON, CSV, and Markdown reports are written to ignored
`artifacts/live-matrix/`. A passing run verifies one assessment per combination;
it does not guarantee that every future stochastic model response will pass review.
To rerun only unsuccessful cases from a saved report, use `--failed-from PATH`
and a different `--output` directory.

`frontend/tests/ui-smoke.cjs` is a fixture-based browser test requiring Playwright and Chrome. Run a Vite preview on port 8502 (or set `UI_BASE_URL`), then run the script with Node in an environment where `require('playwright')` resolves. Screenshots are written to ignored `artifacts/ui/`. This test does not call Google or OpenAI.

Live generation quality and provider configuration must also be checked with a real OpenAI API key. No offline test establishes the factual correctness of arbitrary generated questions.
