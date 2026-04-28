"""
VisionRAG Engine — Advanced RAG Pipeline
Implements three advanced RAG techniques:

1. HISTORY-AWARE QUERY REWRITING
   - Before retrieval, rewrites the current query using chat history
   - Resolves pronouns and references ("it", "that component", "the first one")
   - Example: history="what is a monitor?", query="what is its refresh rate?"
             → rewritten="what is a monitor's refresh rate?"
   - Uses Ollama to rewrite, falls back to original query if it fails

2. RRF (RECIPROCAL RANK FUSION)
   - Runs TWO independent searches: keyword BM25-style + semantic vector (if available)
   - Each search produces a ranked list with positions [1,2,3,4...]
   - RRF formula: score = Σ 1/(k + rank_i) where k=60 (standard constant)
   - Combines both ranked lists into one superior ranking
   - Better than either search alone because:
       • Keyword finds exact term matches
       • Vector finds semantic/conceptual matches
       • RRF merges without needing to normalize scores

3. CROSS-ENCODER RERANKING
   - After RRF produces top-N candidates, reranks them with a cross-encoder
   - Cross-encoder reads (query, chunk) together — much more accurate than bi-encoder
   - Uses sentence-transformers cross-encoder/ms-marco-MiniLM-L-6-v2
   - Falls back to RRF score if cross-encoder not installed
   - Reduces top-N=12 candidates to final top-k=5 for the prompt
"""

import os
import re
import json
import math
import requests
from pathlib import Path

# ─── SESSION STORES ──────────────────────────────────────────────────────────
_PDF_STORE    = {}   # {session_id: [{pdf_name, stem, pdf_path, text, chunks}]}
_VECTOR_STORE = {}   # {session_id: [{pdf_name, stem, vs}]}  — optional ChromaDB

# ─── CROSS-ENCODER CACHE ─────────────────────────────────────────────────────
_cross_encoder = None   # loaded once on first use
_ce_available  = None   # None=untested, True/False after first attempt


# ═══════════════════════════════════════════════════════════════════════════════
# REGISTRATION
# ═══════════════════════════════════════════════════════════════════════════════

def register_pdf(session_id: str, pdf_name: str, pdf_path: str):
    """Immediately extract text and pre-chunk. Called synchronously on upload."""
    text = _extract_text(pdf_path)
    chunks = _make_chunks(text, size=300, overlap=60)  # smaller chunks = shorter prompt = faster generation
    print(f"[RAG] Registered '{pdf_name}' — {len(text)} chars, {len(chunks)} chunks")
    if session_id not in _PDF_STORE:
        _PDF_STORE[session_id] = []
    stem = _clean_stem(pdf_name)
    _PDF_STORE[session_id] = [p for p in _PDF_STORE[session_id] if p['stem'] != stem]
    _PDF_STORE[session_id].append({
        'pdf_name': pdf_name,
        'stem': stem,
        'pdf_path': pdf_path,
        'text': text,
        'chunks': chunks,   # pre-chunked for fast retrieval
    })


def register_vector_store(session_id: str, pdf_name: str, vs):
    """Called by background thread when ChromaDB is ready."""
    if session_id not in _VECTOR_STORE:
        _VECTOR_STORE[session_id] = []
    stem = _clean_stem(pdf_name)
    _VECTOR_STORE[session_id] = [s for s in _VECTOR_STORE[session_id] if s['stem'] != stem]
    _VECTOR_STORE[session_id].append({'pdf_name': pdf_name, 'stem': stem, 'vs': vs})
    print(f"[RAG] Vector store ready: {pdf_name}")


def get_pdfs(session_id: str) -> list:
    """Returns PDFs for a session. Auto-reloads from disk if missing (e.g. after server restart)."""
    if session_id in _PDF_STORE and _PDF_STORE[session_id]:
        return _PDF_STORE[session_id]
    try:
        from django.conf import settings
        pdf_dir = os.path.join(settings.MEDIA_ROOT, 'pdfs', session_id)
        if os.path.isdir(pdf_dir):
            reloaded = []
            for fname in sorted(os.listdir(pdf_dir)):
                if fname.lower().endswith('.pdf'):
                    pdf_path = os.path.join(pdf_dir, fname)
                    text = _extract_text(pdf_path)
                    if text.strip():
                        chunks = _make_chunks(text, size=300, overlap=60)
                        reloaded.append({
                            'pdf_name': fname, 'stem': _clean_stem(fname),
                            'pdf_path': pdf_path, 'text': text, 'chunks': chunks,
                        })
                        print(f"[RAG] Auto-reloaded '{fname}' for session {session_id[:8]} — {len(chunks)} chunks")
            if reloaded:
                _PDF_STORE[session_id] = reloaded
                return reloaded
    except Exception as e:
        print(f"[RAG] Auto-reload failed: {e}")
    return _PDF_STORE.get(session_id, [])


def _clean_stem(filename: str) -> str:
    stem = Path(filename).stem
    return re.sub(r'[_\-\s]+', ' ', stem).strip().lower()


# ═══════════════════════════════════════════════════════════════════════════════
# TECHNIQUE 1 — HISTORY-AWARE QUERY REWRITING
# ═══════════════════════════════════════════════════════════════════════════════

# Pronouns that indicate the query refers to something from history
_PRONOUNS = {
    'it', 'its', 'they', 'their', 'them', 'this', 'that', 'these', 'those',
    'he', 'she', 'his', 'her', 'the same', 'such', 'what about', 'how about',
}

def rewrite_query(query: str, chat_history: list, ollama_url: str = None,
                   image_descriptions: list = None) -> str:
    """
    Fast Python-based query rewriting — no Ollama call, zero latency.

    Priority 1: Resolve pronouns using the identified hardware component (image label).
    Priority 2: Resolve pronouns using the last topic from conversation history.

    Example (image label):
      Image label: "SSD"
      Query: "define it"  →  "define it (context: SSD)"

    Example (history):
      History: "What is a monitor?" / "A monitor is an output device..."
      Query:   "What is its refresh rate?"  →  "What is its refresh rate? (context: monitor)"
    """
    q_lower = query.lower().strip()
    words   = set(re.findall(r'\b\w+\b', q_lower))
    has_pronoun = bool(words & _PRONOUNS)
    is_short    = len(query.split()) <= 4

    # ── Priority 1: use image label if query has pronoun/is short/vague ──
    if (has_pronoun or is_short) and image_descriptions:
        for img in image_descriptions:
            lbl = img.get('label', '').strip()
            if lbl and lbl.lower() not in ('unknown', '', 'analysis failed'):
                if lbl.lower() not in q_lower:
                    rewritten = f"{query} (context: {lbl})"
                    print(f"[REWRITE] '{query[:50]}' → '{rewritten[:70]}' [image]")
                    return rewritten

    if not chat_history or len(chat_history) < 2:
        return query

    q_lower = query.lower().strip()
    words   = set(re.findall(r'\b\w+\b', q_lower))

    # Check if query contains any pronoun/reference word
    has_pronoun = bool(words & _PRONOUNS)

    # Also catch very short queries like "summarise it", "explain more", "and?"
    is_short = len(query.split()) <= 4

    if not has_pronoun and not is_short:
        return query  # query is already standalone, no rewriting needed

    # Extract the main topic from the last user message in history
    last_user = next(
        (m['content'] for m in reversed(chat_history) if m['role'] == 'user'),
        None
    )
    # Extract key nouns from last user question (words > 4 chars, not stop words)
    _STOP = {'what', 'when', 'where', 'which', 'about', 'tell', 'explain',
              'describe', 'does', 'have', 'with', 'from', 'that', 'this',
              'give', 'more', 'also', 'please', 'could', 'would', 'should'}
    if last_user:
        topic_words = [w for w in re.findall(r'\b[a-z]{4,}\b', last_user.lower())
                       if w not in _STOP]
        topic = ' '.join(topic_words[:4]) if topic_words else ''
    else:
        topic = ''

    if topic and topic.lower() not in q_lower:
        rewritten = f"{query} (about: {topic})"
        print(f"[REWRITE] '{query[:50]}' → '{rewritten[:70]}' [fast]")
        return rewritten

    return query


# ═══════════════════════════════════════════════════════════════════════════════
# TECHNIQUE 2 — RRF (RECIPROCAL RANK FUSION)
# ═══════════════════════════════════════════════════════════════════════════════

def _keyword_search(query: str, chunks: list, n: int = 15) -> list:
    """
    BM25-inspired keyword search.
    Returns chunks ranked by term frequency + coverage.
    """
    qwords = set(re.findall(r'\b\w{3,}\b', query.lower()))
    if not qwords:
        return []

    scored = []
    for chunk in chunks:
        cwords = re.findall(r'\b\w{3,}\b', chunk.lower())
        cword_set = set(cwords)
        cword_count = len(cwords)

        # Coverage: how many query words appear in chunk
        coverage = len(qwords & cword_set) / len(qwords)

        # Frequency: sum of TF for each query word (BM25-like)
        tf_score = 0.0
        for w in qwords:
            tf = cwords.count(w)
            tf_score += tf / (tf + 1.5 + 0.75 * cword_count / 400)

        combined = coverage * 0.6 + (tf_score / max(len(qwords), 1)) * 0.4

        if coverage > 0:
            scored.append({'text': chunk, 'score': combined})

    scored.sort(key=lambda x: x['score'], reverse=True)
    return scored[:n]


def _semantic_search(query: str, pdf: dict, session_id: str, n: int = 15) -> list:
    """
    Semantic vector search using ChromaDB if available.
    Returns chunks ranked by cosine similarity.
    """
    vs_entry = next(
        (s for s in _VECTOR_STORE.get(session_id, []) if s['stem'] == pdf['stem']),
        None
    )
    if not vs_entry:
        return []

    try:
        results = vs_entry['vs'].similarity_search_with_score(query, k=n)
        # ChromaDB returns (doc, distance) — lower distance = more similar
        ranked = []
        for doc, dist in results:
            # Convert cosine distance to similarity score
            sim = 1.0 - dist
            ranked.append({'text': doc.page_content, 'score': sim})
        ranked.sort(key=lambda x: x['score'], reverse=True)
        return ranked
    except Exception as e:
        print(f"[SEMANTIC] Failed: {e}")
        return []


def _rrf_fusion(keyword_results: list, semantic_results: list, k: int = 60) -> list:
    """
    Reciprocal Rank Fusion.

    Formula: RRF_score(doc) = Σ 1 / (k + rank_i)
    k=60 is the standard constant that dampens high-rank advantages.

    A chunk appearing at rank 1 in keyword AND rank 2 in semantic
    scores higher than a chunk appearing at rank 1 in only one list.
    """
    scores = {}   # chunk_text[:100] → {'text': ..., 'rrf': ..., 'pdf_name': ...}

    # Add keyword ranks
    for rank, item in enumerate(keyword_results, start=1):
        key = item['text'][:100]
        if key not in scores:
            scores[key] = {'text': item['text'], 'rrf': 0.0,
                           'keyword_rank': rank, 'semantic_rank': None}
        scores[key]['rrf'] += 1.0 / (k + rank)
        scores[key]['keyword_rank'] = rank

    # Add semantic ranks
    for rank, item in enumerate(semantic_results, start=1):
        key = item['text'][:100]
        if key not in scores:
            scores[key] = {'text': item['text'], 'rrf': 0.0,
                           'keyword_rank': None, 'semantic_rank': rank}
        scores[key]['rrf'] += 1.0 / (k + rank)
        scores[key]['semantic_rank'] = rank

    fused = sorted(scores.values(), key=lambda x: x['rrf'], reverse=True)
    return fused


def retrieve_with_rrf(query: str, session_id: str, n_candidates: int = 15) -> list:
    """
    For each PDF:
      1. Run keyword search → ranked list A
      2. Run semantic search → ranked list B (if ChromaDB available)
      3. Apply RRF to merge A+B into superior ranking
    Returns top candidates across all PDFs with pdf_name attached.
    """
    pdfs = get_pdfs(session_id)
    if not pdfs:
        return []

    all_candidates = []

    for pdf in pdfs:
        # Search 1: Keyword (always available)
        kw_results = _keyword_search(query, pdf['chunks'], n=n_candidates)

        # Search 2: Semantic (only if ChromaDB ready)
        sem_results = _semantic_search(query, pdf, session_id, n=n_candidates)

        if sem_results:
            # Both available — use RRF
            fused = _rrf_fusion(kw_results, sem_results)
            print(f"[RRF] {pdf['pdf_name']}: kw={len(kw_results)}, sem={len(sem_results)} → fused={len(fused)}")
            for item in fused[:n_candidates]:
                all_candidates.append({
                    'text': item['text'],
                    'pdf_name': pdf['pdf_name'],
                    'score': item['rrf'],
                    'method': 'rrf',
                    'kw_rank': item.get('keyword_rank'),
                    'sem_rank': item.get('semantic_rank'),
                })
        else:
            # Only keyword available
            print(f"[KW] {pdf['pdf_name']}: {len(kw_results)} keyword results")
            for item in kw_results[:n_candidates]:
                all_candidates.append({
                    'text': item['text'],
                    'pdf_name': pdf['pdf_name'],
                    'score': item['score'],
                    'method': 'keyword',
                })

    # Sort across all PDFs
    all_candidates.sort(key=lambda x: x['score'], reverse=True)
    return all_candidates[:n_candidates]


# ═══════════════════════════════════════════════════════════════════════════════
# TECHNIQUE 3 — CROSS-ENCODER RERANKING
# ═══════════════════════════════════════════════════════════════════════════════

def _load_cross_encoder():
    """Load cross-encoder model once. Returns model or None."""
    global _cross_encoder, _ce_available
    if _ce_available is not None:
        return _cross_encoder

    try:
        from sentence_transformers import CrossEncoder
        _cross_encoder = CrossEncoder('cross-encoder/ms-marco-MiniLM-L-6-v2')
        _ce_available = True
        print("[RERANK] Cross-encoder loaded: cross-encoder/ms-marco-MiniLM-L-6-v2")
    except ImportError:
        print("[RERANK] sentence-transformers not installed — skipping cross-encoder reranking")
        _ce_available = False
    except Exception as e:
        print(f"[RERANK] Cross-encoder load failed: {e}")
        _ce_available = False

    return _cross_encoder


def rerank_chunks(query: str, candidates: list, top_k: int = 5) -> list:
    """
    Cross-encoder reranking.

    Unlike bi-encoders (which encode query and chunk separately),
    the cross-encoder reads the (query, chunk) pair together,
    giving much more accurate relevance scores.

    Input:  top-15 candidates from RRF
    Output: top-5 reranked by cross-encoder score

    Falls back to RRF ordering if cross-encoder unavailable.
    """
    if not candidates:
        return []

    model = _load_cross_encoder()

    if not model:
        # No cross-encoder — just return top_k from RRF order
        print(f"[RERANK] Skipping (no model) — using top {top_k} from RRF")
        return candidates[:top_k]

    try:
        pairs = [(query, c['text']) for c in candidates]
        scores = model.predict(pairs)

        for i, candidate in enumerate(candidates):
            candidate['rerank_score'] = float(scores[i])

        reranked = sorted(candidates, key=lambda x: x['rerank_score'], reverse=True)
        top = reranked[:top_k]

        print(f"[RERANK] Cross-encoder: {len(candidates)} → {top_k} chunks")
        print(f"[RERANK] Top scores: {[round(c['rerank_score'],2) for c in top]}")
        return top

    except Exception as e:
        print(f"[RERANK] Failed: {e} — using RRF order")
        return candidates[:top_k]


# ═══════════════════════════════════════════════════════════════════════════════
# FULL PIPELINE: REWRITE → RRF → RERANK → ANSWER
# ═══════════════════════════════════════════════════════════════════════════════

def stream_answer(query: str, image_descriptions: list, session_id: str,
                  chat_history: list = None, ollama_url: str = "http://localhost:11434"):
    """
    Advanced RAG pipeline:
    Step 1 — History-aware query rewriting
    Step 2 — RRF retrieval (keyword + semantic fusion)
    Step 3 — Cross-encoder reranking
    Step 4 — History-aware answer generation
    """
    pdfs = get_pdfs(session_id)
    has_images = bool(image_descriptions)
    has_pdfs   = bool(pdfs)

    print(f"\n{'='*60}")
    print(f"[PIPELINE] Query: {query[:70]}")
    print(f"[PIPELINE] PDFs: {[p['pdf_name'] for p in pdfs]}")
    print(f"[PIPELINE] Images: {[i.get('label','?') for i in image_descriptions]}")

    # ── Guard: no images ──
    if not has_images:
        yield "Please upload a hardware component image first, then ask questions."
        return

    # ── Guard: no PDFs ──
    if not has_pdfs:
        img_ctx = _img_context(image_descriptions)
        prompt = (f"{img_ctx}\n\nQUESTION: {query}\n\n"
                  "Answer ONLY from the image descriptions above.\n"
                  "If not answerable, say: I don't know — no PDFs uploaded.\n\nANSWER:")
        from .llm_router import stream_llm
        yield from stream_llm(prompt, "Answer only from provided image context.")
        return

    # ════════════════════════════════════════════════════════
    # STEP 1: HISTORY-AWARE QUERY REWRITING
    # ════════════════════════════════════════════════════════
    rewritten_query = rewrite_query(query, chat_history or [], ollama_url, image_descriptions)
    effective_query = rewritten_query  # used for retrieval

    # ════════════════════════════════════════════════════════
    # STEP 2: RRF RETRIEVAL
    # ════════════════════════════════════════════════════════
    candidates = retrieve_with_rrf(effective_query, session_id, n_candidates=10)
    print(f"[PIPELINE] RRF candidates: {len(candidates)}")

    if not candidates:
        yield "I don't know — no relevant content found in the uploaded documents."
        return

    # ════════════════════════════════════════════════════════
    # STEP 3: CROSS-ENCODER RERANKING
    # ════════════════════════════════════════════════════════
    final_chunks = rerank_chunks(effective_query, candidates, top_k=4)
    print(f"[PIPELINE] Final chunks: {len(final_chunks)}")

    # ════════════════════════════════════════════════════════
    # STEP 4: HISTORY-AWARE ANSWER GENERATION
    # ════════════════════════════════════════════════════════
    img_ctx  = _img_context(image_descriptions)
    used_pdfs = list(dict.fromkeys(c['pdf_name'] for c in final_chunks))

    doc_ctx = ""
    for chunk in final_chunks:
        # Trim each chunk to 250 words max to keep prompt concise and fast
        words = chunk['text'].split()
        trimmed = ' '.join(words[:250]) + ('...' if len(words) > 250 else '')
        doc_ctx += f"\n---\n{trimmed}\n"

    # Build history context for the answer (last 6 messages)
    hist_ctx = ""
    if chat_history:
        hist_ctx = "=== RECENT CONVERSATION ===\n"
        for msg in (chat_history or [])[-4:]:
            r = "User" if msg['role'] == 'user' else "Assistant"
            hist_ctx += f"{r}: {msg['content'][:120]}\n"
        hist_ctx += "\n"

    # Note if query was rewritten
    query_note = ""
    if rewritten_query != query:
        query_note = f"(Interpreted as: \"{rewritten_query}\")\n"

    prompt = f"""{img_ctx}
=== DOCUMENT CONTENT ==={doc_ctx}
{hist_ctx}=== QUESTION ===
{query}
{query_note}
=== HOW TO ANSWER ===
Read the DOCUMENT CONTENT above carefully and answer the QUESTION directly.
- Write your answer as natural flowing prose, like a knowledgeable person explaining it.
- Do NOT say "According to Section", "According to the document", "The document states", or any similar citation phrases.
- Do NOT mention file names, section numbers, or page numbers in your answer.
- Do NOT say "uploaded document" or "provided document".
- Just answer the question directly using the information you found.
- Use exact facts, numbers, and names from the documents.
- Do NOT use your own training knowledge — only what is in the document content above.
- If the answer is not in the document content, say only: "I don't know — this information is not in the uploaded documents."
- Give the complete answer, do not cut off mid-sentence.

ANSWER:"""

    system = ("You are a helpful assistant that answers questions from document content. "
              "Answer naturally and directly — never mention section numbers, file names, or citations. "
              "Never say 'according to the document' or 'the document states'. "
              "Just answer the question as if you know the information. "
              "If the answer is not in the provided content, say I don't know.")

    from .llm_router import stream_llm
    yield from stream_llm(prompt, system)


# ─── HELPERS ─────────────────────────────────────────────────────────────────

def _extract_text(pdf_path: str) -> str:
    try:
        import PyPDF2
        parts = []
        with open(pdf_path, 'rb') as f:
            reader = PyPDF2.PdfReader(f)
            for i, page in enumerate(reader.pages):
                t = page.extract_text() or ''
                if t.strip():
                    parts.append(f"[Page {i+1}]\n{t.strip()}")
        return '\n\n'.join(parts)
    except Exception as e:
        print(f"[RAG] Text extraction error: {e}")
        return ''


def extract_text_from_pdf(pdf_path: str) -> str:
    return _extract_text(pdf_path)


def _make_chunks(text: str, size: int = 400, overlap: int = 80) -> list:
    """Split text into overlapping word-based chunks."""
    words = text.split()
    chunks, i = [], 0
    while i < len(words):
        chunk = ' '.join(words[i:i + size])
        if chunk.strip():
            chunks.append(chunk)
        i += size - overlap
    return chunks


def _img_context(imgs: list) -> str:
    bad = {'analysis failed', 'missing package', 'unknown', '', 'not a hardware component'}
    valid = [img for img in imgs
             if img.get('label') and img['label'].lower() not in bad]
    if not valid:
        return ""
    ctx = "=== UPLOADED HARDWARE IMAGES ===\n"
    for i, img in enumerate(valid, 1):
        ctx += f"Image {i}: {img['label']}\n"
        if img.get('description'):
            ctx += f"  {img['description']}\n"
    return ctx + "\n"


def _ollama(prompt: str, system: str, ollama_url: str):
    try:
        r = requests.post(
            f"{ollama_url}/api/generate",
            json={
                "model": "llama3.2",
                "prompt": prompt,
                "system": system,
                "stream": True,
                "options": {
                    "temperature": 0.0,
                    "num_predict": 600,   # enough for full answers, stops wasted allocation
                    "num_ctx": 4096,      # tight context window = faster KV cache
                    "repeat_penalty": 1.1,
                    "top_k": 10,          # deterministic, fast greedy-like decoding
                    "top_p": 0.9,
                }
            },
            stream=True, timeout=120
        )
        for line in r.iter_lines():
            if line:
                try:
                    obj = json.loads(line)
                    t = obj.get("response", "")
                    if t:
                        yield t
                    if obj.get("done"):
                        break
                except Exception:
                    continue
    except requests.exceptions.ConnectionError:
        yield "⚠️ Ollama not running. Run: `ollama serve`"
    except Exception as e:
        yield f"⚠️ Error: {e}"


# ─── BACKGROUND CHROMADB (optional, improves RRF quality) ────────────────────

def run_ingestion_pipeline(pdf_path: str, session_id: str, media_root: str, ollama_url: str):
    """Optional background ChromaDB. Chat works WITHOUT this."""
    pdf_name = Path(pdf_path).name
    try:
        from langchain_huggingface import HuggingFaceEmbeddings
        from langchain_chroma import Chroma
        from langchain_core.documents import Document
    except ImportError:
        print("[INGEST] LangChain not installed — skipping (chat still works via keyword)")
        return
    try:
        persist_dir = os.path.join(media_root, "chroma_db", session_id, Path(pdf_path).stem)
        os.makedirs(persist_dir, exist_ok=True)
        emb = HuggingFaceEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2")
        if os.path.exists(os.path.join(persist_dir, "chroma.sqlite3")):
            vs = Chroma(persist_directory=persist_dir, embedding_function=emb)
        else:
            text = _extract_text(pdf_path)
            chunks = _make_chunks(text, size=400, overlap=80)
            docs = [Document(page_content=c, metadata={"source": pdf_name, "chunk": i})
                    for i, c in enumerate(chunks)]
            vs = Chroma.from_documents(docs, emb, persist_directory=persist_dir)
        register_vector_store(session_id, pdf_name, vs)
    except Exception as e:
        print(f"[INGEST] Error: {e}")


# ─── LEGACY COMPAT ───────────────────────────────────────────────────────────
def set_pdf_cache(session_id, filename, text): pass
def get_pdf_cache(session_id): return {}
