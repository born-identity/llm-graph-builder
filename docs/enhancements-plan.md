# Enhancement Plan: Web Crawling, URL Filtering & Graph Quality

## Overview

Seven related enhancements to improve the scope and quality of knowledge graph extraction from web sources:

1. **Sitemap-based subpage discovery** — crawl all subpages from a seed URL automatically
2. **Path-prefix URL filtering** — let users narrow extraction to specific site sections (e.g. products only)
3. **Entity deduplication pipeline** — merge near-duplicate entity nodes created from overlapping chunks
4. **Schema bootstrapping** — eliminate label proliferation by deriving the schema from a sample run
5. **Improved graph schema consolidation** — fix the constraint that prevents the LLM from creating better canonical names
6. **Page-type-aware extraction** — use different prompts and property sets for product pages vs. knowledge base vs. blog
7. **Structured HTML pre-processing** — extract tables and lists before chunking, rather than flattening them to plain text

---

## Enhancement 1: Sitemap-Based Subpage Discovery

### Problem
The current pipeline accepts one URL and creates one `Document` node. Users who want to extract a knowledge graph from an entire website (e.g. all product pages of `nfon.com/de/`) must submit URLs one by one.

### Proposed Behaviour
The user submits a single seed URL (e.g. `https://www.nfon.com/de/`). The backend discovers all subpages automatically and creates a `Document` node for each one. Each document then flows through the existing `/extract` pipeline unchanged.

### Discovery Strategy

**Step 1 — Sitemap candidates** (try in order):
```
{seed}/sitemap.xml
{root}/sitemap.xml
{root}/sitemap_index.xml
{seed}/sitemap_index.xml
```

**Step 2 — Sitemap index support**: if the sitemap is an index, fetch all child sitemaps and collect URLs from each.

**Step 3 — Fallback (link crawl)**: if no sitemap is found, fetch the seed page, extract all `<a href>` links within the same domain, and crawl up to `max_depth` levels.

### Example: nfon.com/de/
- Sitemap at `/de/sitemap.xml` → index pointing to 3 child sitemaps
- Total URLs: ~2,800 across all locales
- After locale filter (`/de/`): ~300–400 pages

### Backend Changes

**`backend/src/document_sources/web_pages.py`**
- Add `discover_subpage_urls(seed_url, max_pages=50) -> List[str]`
  - Try sitemap candidates
  - Parse sitemap index if needed (fetch child sitemaps)
  - Filter URLs to same locale/domain as seed
  - Apply `max_pages` cap
  - Fallback to link crawl if no sitemap found

**`backend/score.py` — `/url/scan` endpoint**
- Add `crawl_subpages: bool = False` and `max_pages: int = 50` parameters
- When `crawl_subpages=True`: call `discover_subpage_urls`, then call `create_source_node_graph_web_url` for each discovered URL
- Return list of all created file names plus a `discovered_count`

### Frontend Changes

**`frontend/src/components/WebSources/Web/WebInput.tsx`**
- Add "Crawl subpages" toggle checkbox
- Add "Max pages" number input (default 50, shown when toggle is on)
- Pass `crawl_subpages` and `max_pages` to `urlScanAPI()`

### Constraints
- Respect same-domain rule: only follow links/URLs within the same root domain
- Skip URLs already present as `Document` nodes in Neo4j (idempotent re-scan)
- Respect `robots.txt` for production use

---

## Enhancement 2: Path-Prefix URL Filtering

### Problem
Even after filtering to a single locale, a full sitemap crawl returns noise: blog posts, press releases, legal pages, partner pages, thank-you pages. For knowledge graph extraction focused on what a company *offers*, only product, feature, and integration pages are relevant.

### Proposed Behaviour

**Two-phase scan flow:**

1. **Discovery phase** (`/url/scan` with `crawl_subpages=True`): backend discovers all URLs and returns them grouped by top-level path segment with counts — but does **not** create Document nodes yet.

2. **Selection phase** (new endpoint or parameter): user selects which path segments to include, then triggers Document node creation only for the filtered set.

### URL Structure Example (nfon.com/de/)

| Segment | Description | Include for products? |
|---|---|---|
| `produkte` | Product pages | ✅ |
| `integrations` | Integration pages | ✅ |
| `los-gehts` | Getting started / knowledge base | ✅ |
| `solutions` | Solutions pages | ✅ |
| `intelligent-assistant` | AI product | ✅ |
| `ai-transcription-summarisation` | AI feature | ✅ |
| `business-telefonie` | Core telephony product | ✅ |
| `preise` | Pricing | optional |
| `news` | Press releases | ❌ |
| `legal` / `rechtliches` | Legal | ❌ |
| `partner` | Partner pages | ❌ |
| `kundenstories` | Customer stories | ❌ |
| `thank-you*` | Form confirmation pages | ❌ |

### Backend Changes

**`/url/scan` response** (when `crawl_subpages=True`):
```json
{
  "status": "preview",
  "discovered_count": 312,
  "path_segments": [
    { "segment": "produkte", "count": 45, "example": "/de/produkte/cloudya/" },
    { "segment": "integrations", "count": 38 },
    { "segment": "news", "count": 89 },
    ...
  ]
}
```
No Document nodes created yet — this is a dry-run preview.

**`/url/scan` (confirm)**: add `include_paths: List[str]` parameter. When provided, create Document nodes only for URLs whose path starts with one of the included segments.

### Frontend Changes

**New "Path Filter" step** in the web URL input flow:
- After discovery scan returns, show a checkbox list of path segments with counts
- Pre-check segments that look like product/feature pages (heuristic: exclude `news`, `blog`, `legal`, `partner`, `thank-you`, `press`)
- "Select all" / "Deselect all" controls
- Show estimated page count as user toggles checkboxes
- "Start Extraction" button triggers the confirm call with selected segments

### Optional: LLM-Assisted Pre-Selection
For cases where path segments are not self-explanatory, add a "Smart filter" toggle that sends the URL list to a cheap model (e.g. `gpt-4o-mini`) to classify which URLs describe products, features, or integrations. Costs ~$0.001 for 3,000 URLs. The LLM result pre-populates the checkbox state, which the user can still override.

---

## Enhancement 3: Entity Deduplication Pipeline

### Problem
The `TokenTextSplitter` uses chunk overlap by design. Overlapping chunks are processed independently by the LLM, so the same real-world entity can be extracted multiple times with slightly different surface forms:

- `"Neo4j"` vs `"Neo4j Inc."` vs `"Neo4j Inc"`
- `"John Doe"` vs `"John"` vs `"J. Doe"`

The existing `MERGE`-based write only deduplicates on exact string match. The existing `graph_schema_consolidation` step only merges synonymous *label types* (e.g. `Person`/`Human`), not synonymous *instances*.

### Proposed Pipeline Step

Insert after `create_entity_embedding` in the post-processing pipeline:

```
post_processing (existing order):
  1. create_vector_fulltext_indexes    ✓
  2. create_entity_embedding           ✓  ← embeddings built here
  3. entity_deduplication              ← NEW: insert here
  4. graph_schema_consolidation        ✓
  5. create_communities                ✓
```

### Implementation: Two-Phase Approach

**Phase 1 — High-confidence exact merges (no LLM, zero false-positive risk)**

Normalize all entity `id` values:
- Lowercase
- Strip punctuation (`.`, `,`, `Inc`, `Ltd`, `GmbH`, etc.)
- Strip extra whitespace

Group entities whose normalized IDs match. Merge each group using APOC:
```cypher
MATCH (e:__Entity__)
WITH toLower(trim(e.id)) AS normId, collect(e) AS nodes
WHERE size(nodes) > 1
CALL apoc.refactor.mergeNodes(nodes, {properties: "combine", mergeRels: true})
YIELD node
RETURN count(node)
```

**Phase 2 — Embedding similarity merges (LLM confirmation for borderline cases)**

Using the existing `entity_vector` index, find candidate pairs above a cosine similarity threshold:
```cypher
MATCH (e:__Entity__)
WHERE e.embedding IS NOT NULL
CALL db.index.vector.queryNodes('entity_vector', 5, e.embedding)
YIELD node AS candidate, score
WHERE score > 0.95 AND elementId(e) < elementId(candidate)
  AND NOT (e)-[:SAME_AS]-(candidate)
RETURN e.id, candidate.id, score
```

For pairs between the high-confidence threshold (0.95) and a lower cutoff (0.85), send to LLM for confirmation:
```
Are these two nodes referring to the same real-world entity?
Node A: "Neo4j Inc." (type: Organization, description: "...")
Node B: "Neo4j" (type: Organization, description: "...")
Answer: yes / no
```

Confirmed pairs are merged via APOC `mergeNodes` with `mergeRels: true` (preserves all relationships from both nodes).

### Configuration Parameters

| Parameter | Default | Description |
|---|---|---|
| `dedup_similarity_threshold` | `0.95` | Auto-merge above this score |
| `dedup_llm_threshold` | `0.85` | Send to LLM for confirmation between this and auto-merge threshold |
| `dedup_normalization` | `true` | Enable Phase 1 exact-match normalization |
| `dedup_model` | `openai_gpt_4o_mini` | Model used for LLM confirmation calls |

### Backend Changes

**`backend/src/post_processing.py`**
- Add `entity_deduplication(graph, embedding_provider, embedding_model, config)` function
- Called from the existing `post_processing` orchestration in `score.py`

**`backend/src/shared/constants.py`**
- Add `ENTITY_DEDUP_CONFIRMATION_PROMPT`

**`backend/score.py` — `/post_processing` endpoint**
- Add `run_deduplication: bool = True` parameter

### Risk Mitigation
- Phase 1 (normalization) is safe: only merges entities that are clearly the same string
- Phase 2 uses a high default threshold (0.95) to avoid false positives
- LLM confirmation gate prevents merging semantically similar but distinct entities (e.g. `"Apple"` the company vs `"Apple"` the fruit, which would have very different descriptions)
- All merges are logged with the source node IDs for auditability

---

---

## Enhancement 4: Schema Bootstrapping

### Problem
Without a predefined schema, the LLM invents node labels freely. A single product concept produces multiple labels (`Product`, `Product category`, `Product/service`) because different pages and chunks use different language. The existing `graph_schema_consolidation` step can partially fix this after the fact, but the proliferation compounds with every additional document processed.

### Root Cause
`LLMGraphTransformer` receives an empty `allowed_nodes` list when no schema is configured. Every chunk independently invents whatever label fits the text. 21 chunks = up to 21 different labels for the same concept.

### Proposed Behaviour: Two-Phase Extraction

**Phase 1 — Schema discovery run**
Run extraction on a small sample (first N pages or a user-selected subset). Collect all generated node labels and relationship types. Run `graph_schema_consolidation` on the result. Present the consolidated schema to the user for review and editing.

**Phase 2 — Full extraction with schema locked**
Use the approved schema as `allowedNodes` and `allowedRelationship` for all remaining pages. The LLM is now constrained to a known vocabulary.

### UI Flow
After scanning URLs (Enhancement 2), add an optional "Discover schema first" toggle:
- Runs Phase 1 on the first 5–10 pages (configurable)
- Shows the user a schema editor: list of node types and relationship types with counts
- User can merge, rename, or delete types
- "Run full extraction with this schema" button locks it in

### Backend Changes
**`backend/score.py`** — `/extract` endpoint already accepts `allowedNodes` and `allowedRelationship`. No changes needed there.

**New endpoint `/schema/bootstrap`**:
- Accepts a list of URLs + model
- Runs extraction on the sample
- Returns consolidated schema suggestion
- Stores approved schema in Neo4j for reuse on subsequent runs against the same domain

### Why This Is Better Than Post-hoc Consolidation
Consolidation fixes labels after nodes are created. Bootstrapping prevents the proliferation entirely, which means the entity deduplication step (Enhancement 3) also works better — fewer spurious near-duplicates to resolve.

---

## Enhancement 5: Improved Graph Schema Consolidation

### Problem
The current `GRAPH_CLEANUP_PROMPT` has a hard constraint: *"The name of each category must be chosen from the types in the input list. Do not create or infer new names for categories."*

This means if the graph has `Product`, `Product category`, and `Product/service`, the LLM must pick one of those three as the canonical name. It will likely pick `Product`, which is correct — but it might also keep `Product/service` separate because the `/` suggests a compound concept rather than a synonym.

More importantly, it cannot normalise formatting: `Product_Category`, `ProductCategory`, and `Product category` are the same thing but the current prompt won't catch all three.

### Proposed Changes to `GRAPH_CLEANUP_PROMPT`

1. **Relax the naming constraint**: allow the LLM to propose a normalised canonical name (e.g. `ProductCategory`) even if that exact string wasn't in the input, as long as it is clearly derived from an existing type.

2. **Add normalisation rules explicitly**:
   - Strip slashes, underscores, spaces → canonical PascalCase
   - Treat `X`, `X category`, `X type`, `X/service` as strong merge candidates

3. **Add a count-weighted hint**: pass node counts alongside labels. The LLM should prefer the label with the highest count as canonical (e.g. `Product (15)` beats `Product/service (3)`).

### Updated Prompt Addition
```
### 6. Normalisation Rules
- Labels that differ only by spaces, underscores, slashes, or casing refer to the same type.
  Merge them into a single PascalCase canonical name.
- Labels of the form "X", "X category", "X type", "X/service", "X entity" are strong
  merge candidates — group them under the most general form.
- When choosing a canonical name, prefer the label with the highest node count (provided
  in brackets). You may normalise its casing/formatting.
- You MAY propose a canonical name that is a normalised form of an existing label
  (e.g. "ProductCategory" from "Product category"), but you may NOT invent entirely new concepts.
```

### Backend Changes
**`backend/src/graphDB_dataAccess.py`** — `get_nodelabels_relationships()`: extend to return counts alongside labels.

**`backend/src/post_processing.py`** — `graph_schema_consolidation()`: pass counts in the prompt input dict.

---

## Enhancement 6: Page-Type-Aware Extraction

### Problem
Every page is sent to the LLM with the same prompt regardless of content type. A product feature page, a knowledge base definition, and a pricing table all need different extraction strategies:

| Page type | What to extract | Key relationships |
|---|---|---|
| Product page | Features, integrations, target audience | `HAS_FEATURE`, `INTEGRATES_WITH`, `TARGETS` |
| Knowledge base | Concept definitions, technical terms | `DEFINES`, `RELATED_TO`, `REQUIRES` |
| Pricing page | Plans, limits, included features | `INCLUDES`, `COSTS`, `UPGRADES_TO` |
| Blog / news | Topics, mentions | (low priority — likely excluded by Enhancement 2) |

Currently `additional_instructions` can be set per-run, but there is no mechanism to vary it per-page within a single run.

### Proposed Behaviour
Classify each URL's page type from its path segment (cheap, no LLM needed for most cases) and apply a type-specific `additional_instructions` string and `node_properties` set.

### Page Type Detection (rule-based)
```python
PAGE_TYPE_RULES = {
    "product":    ["produkte", "products", "solutions", "intelligent-assistant"],
    "kb":         ["los-gehts", "get-started", "lexikon", "knowledgebase-detail"],
    "pricing":    ["preise", "pricing"],
    "integration":["integrations"],
}
```

### Type-Specific Additional Instructions

**Product pages:**
```
Focus on extracting: the product name, its features (as Feature nodes),
integrations with other tools (INTEGRATES_WITH relationships),
and the target industry or audience (TARGETS relationship).
Relationship types should prefer: HAS_FEATURE, INTEGRATES_WITH, TARGETS, PART_OF, REPLACES.
```

**Knowledge base pages:**
```
Focus on extracting technical concepts and their definitions.
Each concept should have a concise description property.
Relationship types should prefer: DEFINES, IS_A, RELATED_TO, REQUIRES, ENABLES.
```

**Pricing pages:**
```
Extract pricing tiers as Plan nodes. Each plan's included features should be
represented as INCLUDES relationships to Feature nodes.
Capture limits (users, storage, calls) as properties on the Plan node, not as separate nodes.
```

### Backend Changes
**`backend/src/document_sources/web_pages.py`** — add `classify_page_type(url) -> str`

**`backend/src/main.py`** — `processing_source()`: detect page type from URL, look up type-specific `additional_instructions`, pass to `get_graph_from_llm()`

**`backend/src/shared/constants.py`** — add `PAGE_TYPE_INSTRUCTIONS` dict

---

## Enhancement 7: Structured HTML Pre-Processing

### Problem
`WebBaseLoader` uses `BeautifulSoup` internally but discards HTML structure — it returns a single plain text string. This loses:

- **Feature comparison tables**: `<table>` rows become unstructured text, losing the column headers that define what each cell means
- **Bullet-point feature lists**: `<ul><li>` items lose their association with the section heading that labels them
- **Specification blocks**: `<dl><dt><dd>` definitions (term + description pairs) become running text

For a product knowledge graph, these are exactly the highest-signal parts of a page.

### Example
A page with this HTML:
```html
<h2>Cloudya Features</h2>
<ul>
  <li>HD Voice Calls</li>
  <li>Call Recording</li>
  <li>Mobile App (iOS & Android)</li>
</ul>
```
Becomes this text after `WebBaseLoader`:
```
Cloudya Features HD Voice Calls Call Recording Mobile App (iOS & Android)
```
The LLM may or may not correctly infer that these are features of Cloudya.

### Proposed Approach

Add a pre-processing step in `get_documents_from_web_page()` that uses `BeautifulSoup` (already available via `unstructured`) to extract structured elements *before* chunking:

1. **Section-headed lists**: associate each `<ul>/<ol>` with the nearest preceding heading. Reformat as: `"Cloudya has the following features: HD Voice Calls, Call Recording, Mobile App."`

2. **Tables**: convert each `<table>` to a set of natural-language triples: `"[Row header] [Column header]: [Cell value]"` — one sentence per cell.

3. **Definition lists**: convert `<dt>/<dd>` pairs to `"[term]: [definition]"` sentences.

These pre-processed sentences are prepended to the chunk content before the LLM call, giving the LLM explicitly structured context rather than relying on it to reconstruct structure from flattened text.

### Backend Changes
**`backend/src/document_sources/web_pages.py`** — add `extract_structured_elements(html: str) -> str` using BeautifulSoup. Called before returning documents from `get_documents_from_web_page()`.

No new dependencies needed — `beautifulsoup4` is already installed via `unstructured`.

---

## Implementation Order

1. **Enhancement 5** (consolidation prompt fix) — one-line prompt change, immediate quality win for existing users, zero risk
2. **Enhancement 3** (entity deduplication) — self-contained post-processing step, high value for existing users
3. **Enhancement 7** (structured HTML) — backend only, improves input quality for all web extractions
4. **Enhancement 6** (page-type-aware extraction) — backend only, complements Enhancement 7
5. **Enhancement 1** (sitemap crawl) — backend only initially, testable via existing URL input
6. **Enhancement 4** (schema bootstrapping) — depends on Enhancement 1 for meaningful test coverage; requires new UI step
7. **Enhancement 2** (path filtering) — depends on Enhancement 1, requires new frontend step
