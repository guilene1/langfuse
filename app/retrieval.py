from app import config, embeddings, search_index, tracing
from app.tracing import observe

@observe(name="search-policies", as_type="retriever")
def search_policies(question):
    question_vector = embeddings.embed([question])[0]
    chunks = search_index.search(question_vector)
    if not chunks:
        return []
    # Results come back nearest first from both stores, but sort anyway rather than trusting it
    chunks.sort(key=lambda chunk: chunk["similarity"], reverse=True)
    best = chunks[0]["similarity"]
    # Recorded next to the excerpts in Langfuse, so a missing answer can be traced to the cut-off
    tracing.update_current(metadata={
        "candidates": len(chunks), "best_similarity": round(best, 3),
        "min_best_similarity": config.MIN_BEST_SIMILARITY,
        "max_gap_from_best": config.MAX_GAP_FROM_BEST,
    })
    if best < config.MIN_BEST_SIMILARITY:
        # The vectors found nothing convincing. Before giving up, try matching the words
        # themselves: an acronym the documents never spell out ("pto", "401k") is invisible
        # to an embedding model and obvious to keyword search.
        keyword_hits = search_index.keyword_search(question)
        kept = keyword_hits[: config.RESULTS_PER_SEARCH]
        tracing.update_current(metadata={"keyword_fallback": True, "kept": len(kept)})
        for hit in kept:
            hit.setdefault("similarity", config.MIN_BEST_SIMILARITY)
        return kept
    cutoff = max(config.MIN_BEST_SIMILARITY, best - config.MAX_GAP_FROM_BEST)
    kept = [c for c in chunks if c["similarity"] >= cutoff]
    tracing.update_current(metadata={"keyword_fallback": False, "cutoff": round(cutoff, 3), "kept": len(kept)})
    return kept
def cite(chunks):
    sources = []
    for number, chunk in enumerate(chunks, start=1):
        sources.append({
            "number": number, "title": chunk["title"], "section": chunk.get("section"),
            "filename": chunk.get("filename"), "version": chunk.get("version"),
            "effective_date": chunk.get("effective_date"), "region": chunk.get("region"),
        })
    return sources
 
def format_excerpts(chunks):
    if not chunks:
        return "NO POLICY EXCERPTS WERE FOUND FOR THIS QUESTION."
    blocks = []
    for number, chunk in enumerate(chunks, start=1):
        header = f"[{number}] {chunk['title']}"
        if chunk.get("section"): header += f" - {chunk['section']}"
        if chunk.get("version"):
            header += f" (version {chunk['version']}"
            if chunk.get("effective_date"): header += f", effective {chunk['effective_date']}"
            header += ")"
        if chunk.get("region") and chunk["region"] != "global":
            header += f" APPLIES TO: {chunk['region'].upper()} only"
        blocks.append(f"{header}\n{chunk['content']}")
    return "\n\n".join(blocks)
