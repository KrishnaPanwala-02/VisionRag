# VisionRag - AI System for Visual Understanding

A Django-based web app that:
- Accepts images via drag & drop, click, or paste (any format)
- Detects and auto-corrects image blur using OpenCV
- Identifies objects using **llama3.2-vision** via Ollama
- Lets users attach PDFs as a knowledge base
- Provides a **Multimodal RAG chatbot** that answers questions using image context + PDF documents

## Prerequisites

1. **Python 3.11+**
2. **Ollama** installed and running: https://ollama.com
3. Required Ollama models:
   ```bash
   ollama pull llama3.2-vision   # for image identification
   ollama pull llama3.2          # for RAG chat
   ```

## Setup

```bash
# 1. Clone / navigate to project directory
cd vision_rag

# 2. Create virtual environment
python -m venv venv
source venv/bin/activate          # Linux/Mac
# venv\Scripts\activate           # Windows

# 3. Install dependencies
pip install -r requirements.txt

# 4. Start Ollama (in a separate terminal)
ollama serve

# 5. Run Django server
python manage.py migrate
python manage.py runserver
```

Open **http://localhost:8000** in your browser.

## How it Works

### Image Pipeline
1. User uploads image (any format, any method)
2. **Blur Detection**: Laplacian variance method — if score < 100, image is blurred
3. **Auto-Sharpening**: Unsharp masking via OpenCV + PIL enhancement
4. **Object Identification**: llama3.2-vision generates the label
5. **Description Generation**: Detailed visual description stored for RAG context

### Multimodal RAG Chat
1. Image description + label = visual context
2. PDFs are parsed and chunked (with overlapping windows)
3. User question → keyword-based retrieval from PDF chunks
4. Combined context (image + relevant PDF passages) sent to llama3.2
5. Response returned with chat history awareness (last 6 messages)

## Project Structure

```
vision_rag/
├── manage.py
├── requirements.txt
├── vision_rag/
│   ├── settings.py
│   ├── urls.py
│   └── wsgi.py
├── vision_app/
│   ├── views.py          # API endpoints
│   ├── image_utils.py    # Blur detection, sharpening, identification
│   ├── rag_engine.py     # PDF parsing, retrieval, RAG answering
│   ├── urls.py
│   └── templates/
│       └── vision_app/
│           └── index.html  # Full UI
└── media/
    ├── uploads/          # Stored images (per session)
    └── pdfs/             # Stored PDFs (per session)
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Main page |
| `/upload-image/` | POST | Upload & analyze image |
| `/upload-pdf/` | POST | Upload PDF for RAG |
| `/chat/` | POST | Send chat message |

## Configuration

Edit `vision_rag/settings.py`:
```python
OLLAMA_BASE_URL = "http://localhost:11434"  # Ollama server URL
VISION_MODEL = "llama3.2-vision"            # Model for image analysis
CHAT_MODEL = "llama3.2"                     # Model for RAG chat
```

## Deep Architecture and Components

This section describes the project's main subsystems, how they interact, and which files implement them.

- Frontend/UI
  - Single-page-like Django templates in `vision_app/templates/vision_app/` provide image upload, PDF upload, chat UI and visual-diff views.
  - Client-side logic handles drag/drop, paste, and upload. Uploaded files are sent to Django endpoints implemented in `vision_app/views.py` and specialized views files.

- Backend (Django)
  - `vision_rag/` contains project settings and WSGI entrypoint.
  - `vision_app/` contains the application logic: views, URL routing, templates, and utilities.

- Image processing pipeline (quality, enhancement, detection)
  - Core code: `vision_app/image_utils.py`
  - Steps:
    1. Blur detection (Laplacian variance). If below threshold, image considered blurred.
    2. Auto-sharpening (unsharp mask + PIL enhancements) to try to recover details.
    3. Optional resizing/normalization and pre-processing for the vision LLM.
    4. Vision model call (Ollama / Gemini / other configured provider) to obtain labels and structured descriptions.
  - Output: a detailed visual description and metadata stored for RAG context and downstream modules.

## Health Score

Purpose
- Provide a single metric describing the "health" or quality of an uploaded dataset/session or an individual image. Useful for monitoring, filtering low-quality inputs, and alerting.

How it's computed (high level)
- The health score is composed of weighted sub-metrics:
  - Image Quality (IQ): blur score, resolution, noise estimate.
  - Processing Success (PS): whether OCR/extraction succeeded, whether vision LLM returned usable labels.
  - Coverage (CV): fraction of important documents (PDFs) parsed and indexed relative to expected count.
  - Response Confidence (RC): average confidence from LLM responses or retrieval ranking scores.
  - Freshness (FR): how recently the data was processed (penalize stale results).

Example (normalized 0-100):
- Health = round(0.35*IQ + 0.25*PS + 0.20*CV + 0.15*RC + 0.05*FR)

Files & endpoints
- `vision_app/health_score_views.py` — endpoint(s) to compute and return health for a session or image.
- `vision_app/models.py` — stores health-related metadata for sessions and images.
- Use cases: dashboard health widget, automatic requeue for low-health items.

Tuning
- Thresholds and weights are configurable. Start with conservative thresholds and adjust using real data.

## Visual Diff

Purpose
- Compare two images (e.g., before/after, expected vs received) and highlight differences to detect changes, regressions, or tampering.

Algorithms used
- Perceptual hashing (pHash) for fast similarity checks.
- Structural Similarity Index (SSIM) for dense structural comparison and a heatmap of differences.
- Keypoint matching (ORB/SIFT-like) for geometric changes and misalignment tolerance.
- Optional pixel-by-pixel diff after registration for strict comparisons.

Implementation
- `vision_app/visual_diff_views.py` — request handling and orchestration.
- Visualization: generated difference images and JSON summaries returned to the UI. The UI overlays heatmaps and bounding boxes over original images.

Common parameters
- Similarity threshold (for pHash/SSIM)
- Minimum changed area to ignore small noise
- Registration enabled/disabled (align images before diff)

## Smart Analyzer (RAG — Retrieval Augmented Generation)

Overview
- The Smart Analyzer combines visual context (descriptions from the image pipeline) with uploaded PDFs (or other text data) to produce grounded, evidence-based answers.

Components
- PDF ingestion: parse PDFs, extract text, clean and split into overlapping chunks (chunk size and overlap configurable).
- Embeddings & vector store: compute embeddings for chunks and store/retrieve via a vector DB (project stores local Chroma DB under `media/chroma_db/`).
- Retriever: given a user query + image description, retrieve top-k relevant chunks.
- Prompt assembly: combine image description, retrieved text snippets, and system instructions into a single prompt for the LLM.
- LLM call: send the assembled prompt to the configured chat model (Ollama or remote LLM) and return the response.

Files & flows
- `vision_app/rag_engine.py` — ingestion, chunking, embedding, retrieval, and response generation.
- `vision_app/llm_router.py` — selects which LLM provider to call and handles fallback logic across multiple API keys/providers.
- `vision_app/views.py` / `vision_app/analyzer_views.py` — frontend endpoints for analyzer actions.

Key parameters
- chunk_size and chunk_overlap — control chunk granularity and retrieval effectiveness.
- embedding model — choose available embedding provider; local/offline vs remote.
- top_k retrieval — number of chunks returned to the LLM (5-10 common).

Performance and costs
- Embeddings and retrieval are the most costly operations; cache embeddings and reuse vector DB between sessions.
- For large doc sets, periodically reindex and prune low-utility chunks.

## Agentic / Orchestrator AI

What it means here
- "Agentic" refers to higher-level agents that orchestrate multiple tools and submodules to accomplish multi-step tasks (e.g., analyze an image, search PDFs, run a follow-up verification, then compose a report).

Patterns
- Controller agent: receives a user intent and executes a task plan (sequence of calls to RAG, visual diff, external APIs).
- Tools: the agent uses well-defined tool interfaces — e.g., `analyze_image()`, `search_documents()`, `visual_diff()`.
- Safety & limits: agents should run in a sandboxed manner, cap number of steps and timeouts, and log actions.

Files
- `vision_app/llm_router.py` and `vision_app/rag_engine.py` implement pieces of the orchestrator; `vision_app/analyzer_views.py` exposes higher-level operations.

## Security, Secrets & Environment

Important environment variables (see `.env.example`):
- DJANGO_SECRET_KEY (or DJANGO_SECRET_KEY) — Django secret key
- GEMINI_API_KEY, GROQ_API_KEYS, TOGETHER_API_KEYS, OPENROUTER_API_KEYS — LLM provider keys
- EMAIL_HOST_USER / EMAIL_HOST_PASSWORD — email for notifications
- OLLAMA_BASE_URL — local Ollama endpoint

Recommendations
- Never commit real secrets. Use `.env` locally and GitHub repository secrets for CI.
- For production, rotate keys and generate a new strong SECRET_KEY.

## Deployment & Production Notes

- Gunicorn: `gunicorn.conf.py` provided; use `gunicorn vision_rag.wsgi:application -c gunicorn.conf.py` for production.
- Start script: `start.sh` contains convenience start steps.
- Reverse proxy: front the app with Nginx to serve static files and handle SSL.
- Database: switch from SQLite to Postgres for production and configure connection via env var.
- Storage: use S3-compatible storage for `MEDIA_ROOT` in production.
- Scale vector DB: use managed vector DB or networked Chroma for larger datasets.

## CI / GitHub Actions

- Do not store secrets in workflows. Use repository secrets and the Actions UI.
- Typical flow: run linters and tests, build docs, optionally run integration checks against a test Ollama endpoint.

## Testing & Evaluation

- Unit tests: add tests around `image_utils`, `rag_engine`, and `llm_router`.
- End-to-end tests: run server + Ollama in a CI job and validate core flows (ingest PDF + upload image + chat response).
- Metrics to monitor: average health score, query latency, retrieval accuracy (human-evaluated), and LLM cost per query.

## Contributing

- Fork, create feature branch, open PR. Keep changes small and add tests.
- Add changelog entries and update `README.md` when adding features.

## Troubleshooting

- "No GEMINI/LLM key set": ensure environment vars exist or Ollama is running for local models.
- Slow retrieval: increase overlap or reduce chunk size, or precompute embeddings.
- Visual diff false positives: tune SSIM and minimum changed area.

---

If you want, I can now update the `README.md` file in the repo with these deep sections (commit and push). Should I apply the changes? Reply yes / no.
