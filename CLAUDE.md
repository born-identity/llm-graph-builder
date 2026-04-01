# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

LLM Knowledge Graph Builder transforms unstructured data (PDFs, documents, YouTube videos, web pages, etc.) into structured Knowledge Graphs using LLMs, stored in Neo4j. It's a full-stack app with a Python FastAPI backend and React/TypeScript frontend.

## Development Commands

### Backend (Python 3.12+)
```bash
cd backend
python3.12 -m venv venv
source venv/bin/activate
pip install -r requirements.txt -c constraints.txt

# Development server
uvicorn score:app --reload

# Production (used in Docker)
gunicorn score:app --workers 8 --threads 8 --worker-class uvicorn.workers.UvicornWorker
```

### Frontend (Node 20+, yarn)
```bash
cd frontend
yarn install
yarn run dev        # Development server
yarn run build      # Production build
yarn run lint       # ESLint
yarn run format     # Prettier
```

### Docker (full stack)
```bash
docker-compose up --build
# Backend: http://localhost:8000 (FastAPI docs at /docs)
# Frontend: http://localhost:8080
```

### Environment Setup
Copy `example.env` to `.env` in both `backend/` and `frontend/`. Backend needs Neo4j credentials and LLM API keys. Frontend needs `VITE_BACKEND_API_URL` and feature flags.

## Architecture

### Request Flow
```
React Frontend → FastAPI Backend → Neo4j Database
                      ↓
               LLM Providers (OpenAI, Gemini, Anthropic, etc.)
               Document Sources (S3, GCS, YouTube, Wikipedia, Web)
```

### Backend (`backend/`)
- **`score.py`** — FastAPI entry point with 25+ endpoints. Key endpoints: `/extract`, `/url/scan`, `/post_processing`, `/chat_bot`, `/sources_list`, `/schema`
- **`src/main.py`** — Core extraction logic: document loading from all sources, chunking, and Neo4j writes
- **`src/graphDB_dataAccess.py`** — All Neo4j Cypher operations
- **`src/llm.py`** — Unified LLM provider interface (OpenAI, Gemini, Anthropic, Bedrock, Fireworks, Groq, Ollama, Diffbot)
- **`src/document_sources/`** — One file per source type: `local_file.py`, `s3_bucket.py`, `gcs_bucket.py`, `youtube.py`, `wikipedia.py`, `web_pages.py`
- **`src/QA_integration.py`** — RAG pipeline for chatbot (vector, graph, hybrid, entity search modes)
- **`src/post_processing.py`** — Post-extraction pipeline: vector embeddings, full-text indexes, entity embeddings, schema consolidation
- **`src/communities.py`** — Graph community detection
- **`src/shared/constants.py`** — Cypher query templates and system-wide constants
- **`src/shared/common_fn.py`** — Shared utility functions

### Frontend (`frontend/src/`)
- **`App.tsx`** — Router: `/home` (main), `/readonly`, `/chat-only`
- **`Home.tsx`** — Main application component
- **`context/`** — React context for global state: `UserCredentials.tsx` (Neo4j creds), `UsersFiles.tsx` (file state), `UserMessages.tsx` (chat), `Alert.tsx` (toasts)
- **`components/`** — Feature-organized: `DataSources/` (file upload, source selection), `ChatBot/`, `Graph/` (Neo4j Bloom), `Login/`, `Popups/` (schema, settings modals)
- **`hooks/`** — Custom hooks for SSE streaming, speech input, source selection
- **`utils/`** — Helper functions, constants, API utilities
- **`API/`** — Axios client with Neo4j credential injection interceptors

### Key Data Flow
1. **Upload**: User selects source → `/url/scan` creates source node in Neo4j
2. **Extraction**: "Generate Graph" → `/extract` → LLM processes document chunks → entities/relationships written to Neo4j
3. **Post-processing**: `/post_processing` → embeddings, indexes, community detection, entity consolidation
4. **Chat**: User question → `/chat_bot` → RAG retrieves graph context → LLM answers
5. **Visualization**: Neo4j Bloom iframe embedded in frontend

### Supported LLMs
OpenAI, Gemini, Azure OpenAI, Anthropic, Fireworks, Groq, Amazon Bedrock, Ollama, Diffbot, Deepseek

### Cloud Deployment
Google Cloud Run for backend/frontend, Cloud Build CI/CD (`cloudbuild.yaml`), GCS for file storage, GCP Cloud Logging.
