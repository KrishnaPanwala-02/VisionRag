# VisionRAG — Deployment Guide

## Quick Start (Production)

### 1. Install dependencies
```bash
pip install django gunicorn PyPDF2 requests pillow
```

### 2. Set up database
```bash
python setup.py
```

### 3. Add API keys to settings.py
```python
# Get free keys at console.groq.com (6,000 req/day each)
# Add 5 keys = 30,000 req/day = handles ~80 concurrent users for FREE
GROQ_API_KEYS = "gsk_key1,gsk_key2,gsk_key3,gsk_key4,gsk_key5"
```

### 4. Start server
```bash
bash start.sh
```

---

## Capacity Planning (All Free)

| Keys | Requests/day | Req/hour | Concurrent users |
|------|-------------|----------|-----------------|
| 1 Groq key | 6,000 | 250 | ~15 |
| 3 Groq keys | 18,000 | 750 | ~45 |
| 5 Groq keys | 30,000 | 1,250 | ~80 |
| 5 Groq + 3 Together | 33,000+ | 1,375 | ~90 |
| 5 Groq + 5 OR + 3 Together | 35,000+ | 1,458 | ~100 |

---

## Getting Free API Keys

### Groq (recommended — fastest)
1. Go to console.groq.com
2. Sign up (free, no credit card)
3. API Keys → Create Key
4. Repeat with different email for more keys
5. Add to settings: `GROQ_API_KEYS = "key1,key2,key3"`

### OpenRouter (200 free req/day per key)
1. Go to openrouter.ai
2. Sign up free
3. Keys → Create Key
4. Add to settings: `OPENROUTER_API_KEYS = "sk-or-key1,sk-or-key2"`

### Together AI (~1000 req free per account)
1. Go to api.together.xyz
2. Sign up (get $1 free credit)
3. API Keys section
4. Add to settings: `TOGETHER_API_KEYS = "key1"`

---

## Free Cloud Deployment

### Railway (recommended)
```bash
# Install Railway CLI
npm install -g @railway/cli
railway login
railway init
railway up
# Set env vars in Railway dashboard:
# GROQ_API_KEYS=key1,key2,key3
```

### Render
- Connect GitHub repo
- Build: `pip install -r requirements.txt`
- Start: `gunicorn vision_rag.wsgi:application --config gunicorn.conf.py`
- Add env vars in dashboard

### Fly.io (free tier)
```bash
flyctl launch
flyctl secrets set GROQ_API_KEYS="key1,key2,key3"
flyctl deploy
```
