# Changelog

All notable changes to this project will be documented here.

---

## [Unreleased] — enhancements/graph-quality

### Session 4 — 2026-04-01: Sitemap-Based Subpage Discovery & URL Path Grouping

#### Enhancement 1: Sitemap-Based Subpage Discovery
**Files changed:** `backend/src/document_sources/web_pages.py`, `backend/src/entities/source_extract_params.py`, `backend/src/main.py`, `frontend/src/types.ts`, `frontend/src/services/URLScan.ts`, `frontend/src/hooks/useSourceInput.tsx`, `frontend/src/components/WebSources/Web/WebInput.tsx`

- Added `discover_subpage_urls(seed_url, max_pages=50)` in `web_pages.py`:
  - Tries four sitemap candidates in order: `{seed}/sitemap.xml`, `{root}/sitemap.xml`, `{root}/sitemap_index.xml`, `{seed}/sitemap_index.xml`
  - Handles sitemap indexes by recursively fetching child sitemaps
  - Filters all discovered URLs to those starting with the seed prefix (same locale/path)
  - Falls back to crawling `<a href>` links on the seed page if no sitemap is found
  - Deduplicates and caps results at `max_pages`
- Added `url_path_segments(url, seed_url) -> (category, subcategory)` — extracts the first two path segments of a URL relative to the seed, used to annotate each discovered file
- Added `group_urls_by_path_segments(urls, seed_url)` — groups discovered URLs by `(category, subcategory)` pair with counts and an example URL per group; returned in the `/url/scan` response as `data.path_groups`
- `SourceScanExtractParams` extended with `crawl_subpages: bool = False` and `max_pages: int = 50`
- `create_source_node_graph_web_url` refactored: extracted `_create_web_source_node` helper; when `crawl_subpages=True` it discovers all subpages and creates a source node for each, logging per-URL failures without aborting the batch; returns a 4-tuple including `path_groups`
- Each file info dict in the response now includes `urlCategory` and `urlSubcategory` (path segments relative to seed; empty strings for single-URL scans)
- Frontend: "Crawl subpages" checkbox and "Max pages" number input added to the web URL input form; hidden by default, max pages field only appears when toggle is on
- Frontend: `urlCategory` and `urlSubcategory` propagated through `ScanProps` → `URLScan.ts` → `useSourceInput` → `CustomFile`
- Frontend: **Category** and **Subcategory** columns added to the file list table (between Source and Type); show `-` for non-crawled files

---

### Session 3 — 2026-03-31: Structured HTML Pre-Processing & Page-Type-Aware Extraction

#### Enhancement 6: Page-Type-Aware Extraction
**Files changed:** `backend/src/shared/constants.py`, `backend/src/document_sources/web_pages.py`, `backend/src/main.py`

- Classifies each web URL before extraction using a four-level cascade:
  1. **Schema.org JSON-LD** (`@type`) — site-agnostic, zero cost, extracted from the HTML already fetched in Enhancement 7
  2. **OpenGraph** (`og:type`) — widely supported fallback
  3. **URL path-segment rules** — site-specific keywords (e.g. `produkte`, `pricing`, `integrations`)
  4. **LLM fallback** — calls the extraction model with just the page title + meta description when the first three levels don't match
- Four built-in page types with tailored extraction instructions: `product`, `integration`, `kb`, `pricing`
- Type-specific instructions are appended to `additional_instructions` before the LLM extraction prompt; user-supplied instructions are preserved
- `PAGE_TYPE_RULES`, `PAGE_TYPE_INSTRUCTIONS`, `SCHEMA_ORG_TYPE_MAP`, `OG_TYPE_MAP`, and `PAGE_TYPE_LLM_PROMPT` all live in `constants.py` and are easily extended
- Added `test_page_type.py` — standalone test script (stdlib + requests + bs4 only for levels 1–3; supports `--llm <model>` for level 4)

---

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
