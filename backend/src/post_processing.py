from neo4j import GraphDatabase
import logging
import time
from langchain_neo4j import Neo4jGraph
import os
from langchain_core.documents import Document
from langchain_text_splitters import TokenTextSplitter
from src.graph_query import get_graphDB_driver
from src.shared.common_fn import load_embedding_model,execute_graph_query,get_value_from_env
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import ChatPromptTemplate
from src.shared.constants import GRAPH_CLEANUP_PROMPT, ENTITY_DEDUP_CONFIRMATION_PROMPT
from src.llm import get_llm, get_graph_from_llm
from src.graphDB_dataAccess import graphDBdataAccess
import time 

# Constants for Full-Text Indexes
LABELS_QUERY = "CALL db.labels()"
FILTER_LABELS = ["Chunk","Document","__Community__"]
FULL_TEXT_QUERY = "CREATE FULLTEXT INDEX entities FOR (n{labels_str}) ON EACH [n.id, n.description];"
HYBRID_SEARCH_FULL_TEXT_QUERY = "CREATE FULLTEXT INDEX keyword FOR (n:Chunk) ON EACH [n.text]" 
COMMUNITY_INDEX_FULL_TEXT_QUERY = "CREATE FULLTEXT INDEX community_keyword FOR (n:`__Community__`) ON EACH [n.summary]" 

# Constants for Vector Indexes
CHUNK_VECTOR_INDEX_NAME = "vector"
ENTITY_VECTOR_INDEX_NAME = "entity_vector"
VECTOR_EMBEDDING_DEFAULT_DIMENSION = 384

CREATE_VECTOR_INDEX_QUERY = """
CREATE VECTOR INDEX {index_name} IF NOT EXISTS FOR (n:{node_label}) ON (n.{embedding_property})
OPTIONS {{
  indexConfig: {{
    `vector.dimensions`: {embedding_dimension},
    `vector.similarity_function`: 'cosine'
  }}
}}
"""

# Index Configurations
FULLTEXT_INDEXES = [
    {"type": "entities", "query": FULL_TEXT_QUERY},
    {"type": "hybrid", "query": HYBRID_SEARCH_FULL_TEXT_QUERY},
    {"type": "community", "query": COMMUNITY_INDEX_FULL_TEXT_QUERY}
]

VECTOR_INDEXES = [
    {"name": CHUNK_VECTOR_INDEX_NAME, "label": "Chunk", "property": "embedding"},
    {"name": ENTITY_VECTOR_INDEX_NAME, "label": "__Entity__", "property": "embedding"}
]

def create_vector_index(session, index_name, node_label, embedding_property, embedding_dimension):
    """Creates a vector index in the Neo4j database."""
    drop_query = f"DROP INDEX {index_name} IF EXISTS;"
    session.run(drop_query)
    
    query = CREATE_VECTOR_INDEX_QUERY.format(
        index_name=index_name,
        node_label=node_label,
        embedding_property=embedding_property,
        embedding_dimension=embedding_dimension
    )
    session.run(query)
    logging.info(f"Vector index '{index_name}' created successfully.")

def create_fulltext_index(session, index_type, query):
    """Creates a full-text index in the Neo4j database."""
    drop_query = f"DROP INDEX {index_type} IF EXISTS;"
    if index_type == 'hybrid':
        drop_query = "DROP INDEX keyword IF EXISTS;"
    elif index_type == 'community':
        drop_query = "DROP INDEX community_keyword IF EXISTS;"
    
    session.run(drop_query)

    if index_type == "entities":
        result = session.run(LABELS_QUERY)
        labels = [record["label"] for record in result if record["label"] not in FILTER_LABELS]
        if labels:
            labels_str = ":" + "|".join([f"`{label}`" for label in labels])
            query = query.format(labels_str=labels_str)
        else:
            logging.info("Full-text index for entities not created as no labels were found.")
            return
            
    session.run(query)
    logging.info(f"Full-text index for '{index_type}' created successfully.")


def create_vector_fulltext_indexes(credentials, embedding_provider, embedding_model):
    """Creates all configured full-text and vector indexes."""
    logging.info("Starting the process of creating full-text and vector indexes.")
    
    _, dimension = load_embedding_model(embedding_provider, embedding_model)
    if not dimension:
        dimension = VECTOR_EMBEDDING_DEFAULT_DIMENSION

    try:
        driver = get_graphDB_driver(credentials)
        driver.verify_connectivity()
        logging.info("Database connectivity verified.")

        with driver.session() as session:
            # Create Full-Text Indexes
            for index_config in FULLTEXT_INDEXES:
                try:
                    create_fulltext_index(session, index_config["type"], index_config["query"])
                except Exception as e:
                    logging.error(f"Failed to create full-text index for type '{index_config['type']}': {e}")

            # Create Vector Indexes
            for index_config in VECTOR_INDEXES:
                try:
                    create_vector_index(session, index_config["name"], index_config["label"], index_config["property"], dimension)
                except Exception as e:
                    logging.error(f"Failed to create vector index '{index_config['name']}': {e}")

    except Exception as e:
        logging.error(f"An error occurred during the index creation process: {e}", exc_info=True)
    finally:
        if 'driver' in locals() and driver:
            driver.close()
            logging.info("Driver closed successfully.")
    
    logging.info("Full-text and vector index creation process completed.")


def create_entity_embedding(graph:Neo4jGraph, embedding_provider, embedding_model):
    rows = fetch_entities_for_embedding(graph)
    for i in range(0, len(rows), 1000):
        update_embeddings(rows[i:i+1000],graph, embedding_provider, embedding_model)
            
def fetch_entities_for_embedding(graph):
    query = """
                MATCH (e)
                WHERE NOT (e:Chunk OR e:Document OR e:`__Community__`) AND e.embedding IS NULL AND e.id IS NOT NULL
                RETURN elementId(e) AS elementId, e.id + " " + coalesce(e.description, "") AS text
                """ 
    result = execute_graph_query(graph,query)        
    return [{"elementId": record["elementId"], "text": record["text"]} for record in result]

def update_embeddings(rows, graph, embedding_provider, embedding_model):
    embeddings, dimension = load_embedding_model(embedding_provider, embedding_model)
    logging.info(f"update embedding for entities")
    for row in rows:
        row['embedding'] = embeddings.embed_query(row['text'])                        
    query = """
      UNWIND $rows AS row
      MATCH (e) WHERE elementId(e) = row.elementId
      CALL db.create.setNodeVectorProperty(e, "embedding", row.embedding)
      """  
    return execute_graph_query(graph,query,params={'rows':rows})          

def entity_deduplication(graph, llm_model=None):
    """
    Two-phase entity deduplication pipeline.

    Phase 1 — Exact normalization merges (no LLM):
      Normalises entity id values (lowercase, strip punctuation) and merges
      nodes whose normalised ids match exactly using APOC mergeNodes.

    Phase 2 — Embedding similarity merges:
      Finds candidate pairs via the entity_vector index.
      Pairs above AUTO_MERGE_THRESHOLD are merged automatically.
      Pairs between LLM_CONFIRM_THRESHOLD and AUTO_MERGE_THRESHOLD are sent
      to the LLM for confirmation before merging.
    """
    AUTO_MERGE_THRESHOLD = 0.98
    LLM_CONFIRM_THRESHOLD = 0.92

    # Phase 1: normalize and merge exact string variants
    phase1_query = """
        MATCH (e:__Entity__)
        WHERE e.id IS NOT NULL
        WITH e, toLower(trim(
            replace(replace(replace(replace(replace(
                e.id, '.', ''), ',', ''), '/', ' '), '_', ' '), '-', ' ')
        )) AS normId
        WITH normId, collect(e) AS nodes
        WHERE size(nodes) > 1
        CALL apoc.refactor.mergeNodes(nodes, {properties: "discard", mergeRels: true})
        YIELD node
        RETURN count(node) AS merged
    """
    try:
        result = execute_graph_query(graph, phase1_query)
        phase1_count = result[0]["merged"] if result else 0
        logging.info(f"Entity deduplication Phase 1: merged {phase1_count} duplicate groups")
    except Exception as e:
        logging.error(f"Entity deduplication Phase 1 failed: {e}")
        phase1_count = 0

    # Phase 2: embedding similarity candidates
    candidate_query = """
        MATCH (e:__Entity__)
        WHERE e.embedding IS NOT NULL
        CALL db.index.vector.queryNodes('entity_vector', 6, e.embedding)
        YIELD node AS candidate, score
        WHERE score >= $llm_threshold
          AND score < 1.0
          AND elementId(e) < elementId(candidate)
          AND e.id <> candidate.id
        RETURN elementId(e) AS elem_a, e.id AS id_a, coalesce(e.description, '') AS desc_a,
               elementId(candidate) AS elem_b, candidate.id AS id_b, coalesce(candidate.description, '') AS desc_b,
               score
    """
    merge_query = """
        MATCH (e:__Entity__) WHERE elementId(e) = $elem_a
        MATCH (c:__Entity__) WHERE elementId(c) = $elem_b
        REMOVE c.embedding
        WITH e, c
        CALL apoc.refactor.mergeNodes([e, c], {properties: "discard", mergeRels: true})
        YIELD node RETURN node
    """

    try:
        candidates = execute_graph_query(graph, candidate_query, params={"llm_threshold": LLM_CONFIRM_THRESHOLD})
    except Exception as e:
        logging.error(f"Entity deduplication Phase 2 candidate query failed: {e}")
        return None

    auto_pairs = [c for c in candidates if c["score"] >= AUTO_MERGE_THRESHOLD]
    llm_pairs  = [c for c in candidates if LLM_CONFIRM_THRESHOLD <= c["score"] < AUTO_MERGE_THRESHOLD]

    # Auto-merge high-confidence pairs
    auto_merged = 0
    for pair in auto_pairs:
        try:
            execute_graph_query(graph, merge_query, params={"elem_a": pair["elem_a"], "elem_b": pair["elem_b"]})
            auto_merged += 1
            logging.info(f"Auto-merged: '{pair['id_a']}' + '{pair['id_b']}' (score={pair['score']:.3f})")
        except Exception as e:
            logging.warning(f"Failed to auto-merge '{pair['id_a']}' + '{pair['id_b']}': {e}")
    logging.info(f"Entity deduplication Phase 2: auto-merged {auto_merged} pairs")

    # LLM-confirmed merges for borderline pairs
    if llm_pairs:
        if llm_model is None:
            llm_model = get_value_from_env("GRAPH_CLEANUP_MODEL", "openai_gpt_4o_mini")
        llm, _, _ = get_llm(llm_model)
        llm_merged = 0
        for pair in llm_pairs:
            try:
                response = llm.invoke(ENTITY_DEDUP_CONFIRMATION_PROMPT.format(
                    id_a=pair["id_a"], desc_a=pair["desc_a"],
                    id_b=pair["id_b"], desc_b=pair["desc_b"],
                ))
                if response.content.strip().lower().startswith("yes"):
                    execute_graph_query(graph, merge_query, params={"elem_a": pair["elem_a"], "elem_b": pair["elem_b"]})
                    llm_merged += 1
                    logging.info(f"LLM-confirmed merge: '{pair['id_a']}' + '{pair['id_b']}' (score={pair['score']:.3f})")
            except Exception as e:
                logging.warning(f"LLM confirmation failed for '{pair['id_a']}' + '{pair['id_b']}': {e}")
        logging.info(f"Entity deduplication Phase 2: LLM-confirmed {llm_merged} additional merges")

    # Clean up self-loop relationships created when merged entities had direct relationships
    self_loop_cleanup_query = """
        MATCH (n:__Entity__)-[r]->(n)
        DELETE r
        RETURN count(*) AS deleted
    """
    try:
        result = execute_graph_query(graph, self_loop_cleanup_query)
        deleted = result[0]["deleted"] if result else 0
        logging.info(f"Entity deduplication: removed {deleted} self-loop relationships")
    except Exception as e:
        logging.error(f"Entity deduplication self-loop cleanup failed: {e}")

    return None


def graph_schema_consolidation(graph):
    graphDb_data_Access = graphDBdataAccess(graph)
    node_labels, relation_labels, node_counts = graphDb_data_Access.get_nodelabels_relationships()
    parser = JsonOutputParser()
    prompt = ChatPromptTemplate(
        messages=[("system", GRAPH_CLEANUP_PROMPT), ("human", "{input}")],
        partial_variables={"format_instructions": parser.get_format_instructions()}
    )
    graph_cleanup_model = get_value_from_env("GRAPH_CLEANUP_MODEL", 'openai_gpt_5_mini')
    llm, _, _ = get_llm(graph_cleanup_model)
    chain = prompt | llm | parser

    nodes_relations_input = {'nodes': node_labels, 'relationships': relation_labels, 'node_counts': node_counts}
    mappings = chain.invoke({'input': nodes_relations_input})
    node_mapping = {old: new for new, old_list in mappings['nodes'].items() for old in old_list if new != old}
    relation_mapping = {old: new for new, old_list in mappings['relationships'].items() for old in old_list if new != old}

    logging.info(f"Node Labels: Total = {len(node_labels)}, Reduced to = {len(set(node_mapping.values()))} (from {len(node_mapping)})")
    logging.info(f"Relationship Types: Total = {len(relation_labels)}, Reduced to = {len(set(relation_mapping.values()))} (from {len(relation_mapping)})")

    if node_mapping:
        for old_label, new_label in node_mapping.items():
            query = f"""
                    MATCH (n:`{old_label}`)
                    SET n:`{new_label}`
                    REMOVE n:`{old_label}`
                    """
            execute_graph_query(graph,query)

    for old_label, new_label in relation_mapping.items():
        query = f"""
                MATCH (n)-[r:`{old_label}`]->(m)
                CREATE (n)-[r2:`{new_label}`]->(m)
                DELETE r
                """
        execute_graph_query(graph,query)

    return None


async def bootstrap_schema(pages: list[Document], model: str, chunks_to_combine: int = 1) -> dict:
    """
    Run a lightweight schema discovery pass over a set of documents without writing to Neo4j.

    Chunks the documents, runs LLM extraction, collects all node labels and
    relationship types, then applies the schema-consolidation LLM prompt to
    normalise them.

    Args:
        pages:            Documents to sample (typically from a handful of web pages).
        model:            LLM model name to use for extraction.
        chunks_to_combine: Number of chunks to combine before sending to the LLM.

    Returns:
        dict with keys:
          - ``nodes``         — sorted list of canonical node label strings
          - ``relationships`` — list of ``{"source", "type", "target"}`` dicts
          - ``raw_nodes``     — raw (pre-consolidation) node labels for debugging
          - ``raw_relationships`` — raw relationship types for debugging
    """
    # 1. Chunk the pages in memory (no Neo4j writes)
    splitter = TokenTextSplitter(chunk_size=512, chunk_overlap=50)
    chunks = splitter.split_documents(pages)
    logging.info(f"bootstrap_schema: {len(pages)} pages → {len(chunks)} chunks")

    if not chunks:
        return {"nodes": [], "relationships": [], "raw_nodes": [], "raw_relationships": []}

    # Build the chunkId_chunkDoc_list format expected by get_graph_from_llm
    chunkId_chunkDoc_list = [
        {"chunk_id": f"bootstrap_{i}", "chunk_doc": chunk}
        for i, chunk in enumerate(chunks)
    ]

    # 2. Run LLM extraction (no Neo4j writes)
    graph_documents, _ = await get_graph_from_llm(
        model, chunkId_chunkDoc_list, allowedNodes=None, allowedRelationship=None,
        chunks_to_combine=chunks_to_combine
    )

    # 3. Collect raw labels and relationship types
    raw_node_labels: set[str] = set()
    raw_rel_types: set[str] = set()
    raw_triples: list[tuple[str, str, str]] = []

    for gd in graph_documents:
        for node in gd.nodes:
            if node.type:
                raw_node_labels.add(node.type)
        for rel in gd.relationships:
            raw_rel_types.add(rel.type)
            if rel.source and rel.target:
                raw_triples.append((rel.source.type, rel.type, rel.target.type))

    logging.info(f"bootstrap_schema: found {len(raw_node_labels)} node labels, {len(raw_rel_types)} relationship types")

    if not raw_node_labels:
        return {"nodes": [], "relationships": [], "raw_nodes": [], "raw_relationships": []}

    # 4. Run schema consolidation to normalise labels
    node_label_list = sorted(raw_node_labels)
    rel_type_list = sorted(raw_rel_types)

    parser = JsonOutputParser()
    prompt = ChatPromptTemplate(
        messages=[("system", GRAPH_CLEANUP_PROMPT), ("human", "{input}")],
        partial_variables={"format_instructions": parser.get_format_instructions()}
    )
    cleanup_model = get_value_from_env("GRAPH_CLEANUP_MODEL", 'openai_gpt_4o_mini')
    llm, _, _ = get_llm(cleanup_model)
    chain = prompt | llm | parser

    # Pass node_counts as 1 each (we don't have real counts without Neo4j)
    node_counts = {label: 1 for label in node_label_list}
    nodes_relations_input = {
        'nodes': node_label_list,
        'relationships': rel_type_list,
        'node_counts': node_counts,
    }
    mappings = chain.invoke({'input': nodes_relations_input})

    # Build reverse map: old label → canonical label
    node_mapping = {old: new for new, old_list in mappings.get('nodes', {}).items() for old in old_list}
    rel_mapping = {old: new for new, old_list in mappings.get('relationships', {}).items() for old in old_list}

    canonical_nodes = sorted(set(node_mapping.get(lbl, lbl) for lbl in node_label_list))

    # Deduplicate relationships using canonical names
    seen_triples: set[tuple[str, str, str]] = set()
    canonical_relationships = []
    for src, rel_type, tgt in raw_triples:
        c_src = node_mapping.get(src, src)
        c_rel = rel_mapping.get(rel_type, rel_type)
        c_tgt = node_mapping.get(tgt, tgt)
        triple = (c_src, c_rel, c_tgt)
        if triple not in seen_triples:
            seen_triples.add(triple)
            canonical_relationships.append({"source": c_src, "type": c_rel, "target": c_tgt})

    return {
        "nodes": canonical_nodes,
        "relationships": canonical_relationships,
        "raw_nodes": node_label_list,
        "raw_relationships": rel_type_list,
    }