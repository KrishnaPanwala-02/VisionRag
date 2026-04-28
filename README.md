# VisionRAG — AI System for Visual Understanding 

A Django-based web app that:
- Accepts images via drag & drop, click, or paste (any format)
- Detects and auto-corrects image blur using OpenCV
- Identifies objects using **llama3.2-vision** via Ollama
- Lets users attach PDFs as a knowledge base
- Provides a **Multimodal RAG chatbot** that answers questions using image context + PDF documents

## Prerequisites

1. **Python 3.11+**
2. **Ollama** installed and running: https://ollama.com
3. Required Ollama models:
   ```bash
   ollama pull llama3.2-vision   # for image identification
   ollama pull llama3.2          # for RAG chat
   ```

## Setup

```bash
# 1. Clone / navigate to project directory
cd vision_rag

# 2. Create virtual environment
python -m venv venv
source venv/bin/activate          # Linux/Mac
# venv\Scripts\activate           # Windows

# 3. Install dependencies
pip install -r requirements.txt

# 4. Start Ollama (in a separate terminal)
ollama serve

# 5. Run Django server
python manage.py migrate
python manage.py runserver
```

Open **http://localhost:8000** in your browser.

## How it Works

### Image Pipeline
1. User uploads image (any format, any method)
2. **Blur Detection**: Laplacian variance method — if score < 100, image is blurred
3. **Auto-Sharpening**: Unsharp masking via OpenCV + PIL enhancement
4. **Object Identification**: llama3.2-vision generates the label
5. **Description Generation**: Detailed visual description stored for RAG context

### Multimodal RAG Chat
1. Image description + label = visual context
2. PDFs are parsed and chunked (with overlapping windows)
3. User question → keyword-based retrieval from PDF chunks
4. Combined context (image + relevant PDF passages) sent to llama3.2
5. Response returned with chat history awareness (last 6 messages)

## Project Structure

```
vision_rag/
├── manage.py
├── requirements.txt
├── vision_rag/
│   ├── settings.py
│   ├── urls.py
│   └── wsgi.py
├── vision_app/
│   ├── views.py          # API endpoints
│   ├── image_utils.py    # Blur detection, sharpening, identification
│   ├── rag_engine.py     # PDF parsing, retrieval, RAG answering
│   ├── urls.py
│   └── templates/
│       └── vision_app/
│           └── index.html  # Full UI
└── media/
    ├── uploads/          # Stored images (per session)
    └── pdfs/             # Stored PDFs (per session)
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Main page |
| `/upload-image/` | POST | Upload & analyze image |
| `/upload-pdf/` | POST | Upload PDF for RAG |
| `/chat/` | POST | Send chat message |

## Configuration

Edit `vision_rag/settings.py`:
```python
OLLAMA_BASE_URL = "http://localhost:11434"  # Ollama server URL
VISION_MODEL = "llama3.2-vision"            # Model for image analysis
CHAT_MODEL = "llama3.2"                     # Model for RAG chat
```
