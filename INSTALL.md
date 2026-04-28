# VisionRAG — Installation Guide

## 1. System packages
```bash
sudo apt-get install -y poppler-utils tesseract-ocr
```

## 2. Python packages
```bash
pip install -r requirements.txt
```

## 3. HuggingFace embedding model (auto-downloads on first PDF upload)
The model `sentence-transformers/all-MiniLM-L6-v2` (~91MB) downloads automatically
the first time you upload a PDF. Requires internet access on first run.

## 4. Ollama models
```bash
ollama pull llama3.2          # for chat (text)
ollama pull llama3.2-vision   # for image analysis
ollama serve                  # keep running
```

## 5. Run
```bash
python manage.py migrate
python manage.py runserver
```

## How the RAG pipeline works (from your notebook)
1. PDF uploaded → `unstructured.partition_pdf()` extracts text/tables/images
2. `chunk_by_title()` creates smart semantic chunks (~21 chunks for a paper)
3. For chunks with tables/images → Ollama summarises them
4. `HuggingFaceEmbeddings` + `ChromaDB` creates vector store (persisted to disk)
5. On chat query → `db.as_retriever(k=4)` finds most relevant chunks
6. Ollama `llama3.2` answers strictly from retrieved context
7. If answer not in docs → "I don't know"

## Notes
- ChromaDB indexing runs in a background thread — PDF upload returns instantly
- The `⏳ indexing...` badge turns to `✓` after ~8 seconds
- Vector stores are cached in `media/chroma_db/<session_id>/`
