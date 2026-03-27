# Changelog

All notable changes to this project will be documented here.

---

## [Unreleased] — enhancements/graph-quality

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
