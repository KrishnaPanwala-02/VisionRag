"""
Gunicorn config for VisionRAG production deployment.
Handles concurrent requests properly unlike Django dev server.
"""
import multiprocessing

# Workers = (2 × CPU cores) + 1  — standard formula
workers     = (multiprocessing.cpu_count() * 2) + 1
worker_class = 'gthread'   # thread-based: better for I/O-heavy (streaming LLM responses)
threads     = 4             # threads per worker — handles concurrent streaming responses
bind        = '0.0.0.0:8000'
timeout     = 120           # long timeout for LLM streaming
keepalive   = 5
max_requests = 1000         # recycle workers to prevent memory leaks
max_requests_jitter = 100
accesslog   = '-'           # stdout
errorlog    = '-'
loglevel    = 'info'
