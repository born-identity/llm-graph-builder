# Changelog

All notable changes to this project will be documented here.

---

## [Unreleased] — enhancements/graph-quality

### Session 3 — 2026-03-31: Structured HTML Pre-Processing

#### Enhancement 7: Structured HTML Pre-Processing
**Files changed:** `backend/src/document_sources/web_pages.py`

- Replaced `WebBaseLoader` with a direct `requests` + `BeautifulSoup` fetch that extracts structured HTML elements before chunking.
- Three element types are converted to explicit natural-language sentences and prepended to page content:
  - **Section-headed lists** — `<ul>/<ol>` preceded by a `<h1>`–`<h6>` tag become `"Heading: item1, item2, item3."` sentences. Prevents chunk boundaries from separating a list from its heading context.
  - **Tables** — each data cell becomes `"RowHeader — ColumnHeader: CellValue."`. Preserves the row/column relationship that flattening destroys.
  - **Definition lists** — `<dt>/<dd>` pairs become `"Term: Definition."` sentences.
- Noise tags (`script`, `style`, `nav`, `footer`, etc.) are stripped before plain-text extraction.
- Page metadata (title, meta description, language) is preserved in Document metadata.

---

### Session 2 — 2026-03-30: Gemini API, Deduplication UI, Bug Fixes

#### Gemini API key support (replacing Vertex AI)
**Files changed:** `backend/requirements.txt`, `backend/src/llm.py`, `backend/src/QA_integration.py`, `backend/src/shared/common_fn.py`

- Replaced `langchain-google-vertexai` with `langchain-google-genai` in requirements.
- `get_llm()` now uses `ChatGoogleGenerativeAI` with an API key passed directly in the model config string (`LLM_MODEL_CONFIG_GEMINI_*="model-name,api_key"`), consistent with OpenAI/Anthropic format.
- `load_embedding_model()` now uses `GoogleGenerativeAIEmbeddings` instead of `VertexAIEmbeddings`. Model name is prefixed with `models/` as required by the Gemini API.
- `QA_integration.py` updated to reference `ChatGoogleGenerativeAI` for token counting.
- `google-cloud-storage` must be installed separately (was previously pulled in as a transitive dependency of vertexai).

#### Neo4j Enterprise write access fix
**Files changed:** `backend/src/graphDB_dataAccess.py`

- `check_account_access()` query now matches `graph = $database OR graph = '*'` — the admin role grants access via wildcard which was previously not matched, causing all Enterprise users to appear read-only.

#### De-Duplication Of Nodes tab — backend implementation
**Files changed:** `backend/src/graphDB_dataAccess.py`

- Added `get_duplicate_nodes_list(similarity_threshold=0.85)`: queries `entity_vector` index for near-duplicate entity pairs, returns them in the `dupNodes` shape expected by the frontend (primary node, similar nodes, connected documents, chunk connection count).
- Added `merge_duplicate_nodes(duplicate_nodes_list)`: merges user-selected pairs via APOC `mergeNodes`. Strips `embedding` from the duplicate node before merging to prevent dimension doubling (6144 bug).
- Both methods back the existing `/get_duplicate_nodes` and `/merge_duplicate_nodes` endpoints in `score.py` which previously had no implementation.

#### Embedding dimension bug fix
**Files changed:** `backend/src/graphDB_dataAccess.py`, `backend/src/post_processing.py`

- APOC `mergeNodes` with `properties: "combine"` was concatenating embedding arrays (3072 + 3072 = 6144), breaking the vector index. Fixed by stripping the duplicate node's embedding before merging in both the manual UI path and the automated `entity_deduplication` pipeline.

#### Post Processing Jobs — "Run Now" button
**Files changed:** `frontend/src/components/Popups/GraphEnhancementDialog/PostProcessingCheckList/index.tsx`

- Added a **Run Now** button to the Post Processing Jobs tab so users can trigger selected tasks on demand, without needing to re-run extraction.

#### Entity Deduplication added to Post Processing Jobs UI
**Files changed:** `frontend/src/utils/Constants.ts`

- Added `entity_deduplication` to `POST_PROCESSING_JOBS` so it appears as a checkbox in the Post Processing Jobs tab.

---

### Enhancement 5: Improved Graph Schema Consolidation

**Files changed:** `backend/src/shared/constants.py`, `backend/src/graphDB_dataAccess.py`, `backend/src/post_processing.py`

- `get_nodelabels_relationships()` now returns node counts alongside labels (3-tuple: `node_labels, relationship_types, node_counts`). The Cypher query fetches `value.count` per label.
- `graph_schema_consolidation()` passes `node_counts` in the prompt input so the LLM can use counts for tie-breaking.
- `GRAPH_CLEANUP_PROMPT` updated:
  - Sections 2–4: relaxed the hard constraint that canonical names must come verbatim from the input list. The LLM may now propose a normalised PascalCase form (e.g. `ProductCategory` from `Product category`).
  - New **Section 6 — Normalisation Rules**: instructs the LLM to merge labels that differ only by spaces/underscores/slashes/casing, treat `X` / `X category` / `X type` / `X/service` as strong merge candidates, and prefer the highest-count label as canonical.
  - New **Example 3**: demonstrates label-variant normalisation using node counts.

---

### Enhancement 3: Entity Deduplication Pipeline

**Files changed:** `backend/src/shared/constants.py`, `backend/src/post_processing.py`, `backend/score.py`

New post-processing step `entity_deduplication`, inserted in the pipeline after `materialize_entity_similarities` and before `graph_schema_consolidation`.

**Phase 1 — Exact normalization merges (no LLM)**
- Normalises each entity `id` (lowercase, strip `.`, `,`, `/`, `_`, `-`) and groups nodes by normalised id.
- Merges each group of >1 node using `apoc.refactor.mergeNodes` with `mergeRels: true`.
- Zero false-positive risk; only merges strings that are clearly the same.

**Phase 2 — Embedding similarity merges**
- Queries the existing `entity_vector` index for near-duplicate pairs.
- Pairs with cosine similarity ≥ 0.95 are auto-merged.
- Pairs between 0.85 and 0.95 are sent to the LLM (`ENTITY_DEDUP_CONFIRMATION_PROMPT`) for a yes/no confirmation before merging.
- All merges use `apoc.refactor.mergeNodes` with `mergeRels: true` to preserve all relationships.

Triggered by including `"entity_deduplication"` in the `tasks` form field of `/post_processing`. Gated by the `ENTITY_EMBEDDING` env flag (embeddings must exist for Phase 2 to run).

Added `ENTITY_DEDUP_CONFIRMATION_PROMPT` to `constants.py`.
