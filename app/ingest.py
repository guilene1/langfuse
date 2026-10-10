import json
from app import chunking, documents, embeddings, search_index, config
CARRIED_FIELDS = [
    "document_id", "title", "category", "region", "version", "status", "effective_date", ]
def load_manifest(documents_dir):
    manifest_path = documents_dir / "manifest.json"
    entries = json.loads(
        manifest_path.read_text(encoding="utf-8")
    )
    problems = []
    listed = set()
 
    for entry in entries:
        filename = entry.get("filename", "<missing filename>")
        listed.add(filename)
        missing = {
            "filename", "title", "document_id", "status",
        } - set(entry)
 
        if missing:
            problems.append(f"{filename}: manifest entry is missing {sorted(missing)}")
 
        if entry.get("status") not in ("current", "superseded"):
            problems.append(f"{filename}: status is {entry.get('status')!r}, expected current or superseded")
 
        if not (documents_dir / filename).exists():
            problems.append(f"{filename}: listed in the manifest but not in the folder")
 
    on_disk = {p.name for p in documents_dir.iterdir() if p.name != "manifest.json"}

    for orphan in sorted(on_disk - listed):
        problems.append(f"{orphan}: in the folder but not in manifest, so never searchable")
 
    if problems:
        raise SystemExit("manifest.json does not match documents:\n " + "\n ".join(problems))
 
    return entries

def build_chunks(documents_dir, entries):
    chunks = []; empty = []
    for entry in entries:
        path = documents_dir / entry["filename"]
        sections = documents.read_document(path)
        produced = 0
        for piece in chunking.chunk_sections(sections):
            chunk = {field: entry.get(field, "") for field in CARRIED_FIELDS}
            chunk["filename"] = entry["filename"]
            chunk["section"] = piece["section"]
            chunk["content"] = piece["content"]
            chunks.append(chunk); produced += 1
        if produced == 0: empty.append(entry["filename"])
    if empty: raise SystemExit("These documents produced no chunks:\n " + "\n ".join(empty))
    return chunks
 
def embedding_text(chunk):
    header = " - ".join(part for part in (chunk.get("title"), chunk.get("section")) if part)
    return f"{header}\n{chunk['content']}" if header else chunk["content"]
 
def ingest(documents_dir):
    entries = load_manifest(documents_dir)
    chunks = build_chunks(documents_dir, entries)
    vectors = embeddings.embed([embedding_text(c) for c in chunks])
    for chunk, vector in zip(chunks, vectors): chunk["embedding"] = vector
    search_index.create_index(); search_index.add_chunks(chunks)
    return {"documents": len(entries), "chunks": len(chunks)}
 
if __name__ == "__main__":
    summary = ingest(config.DOCUMENTS_DIR)
    print(f"Indexed {summary['chunks']} chunks from {summary['documents']} documents")
