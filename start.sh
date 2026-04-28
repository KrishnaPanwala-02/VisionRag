#!/bin/bash
# VisionRAG Production Start Script
# Usage: bash start.sh

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  VisionRAG — Production Server"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Run migrations if needed
python manage.py migrate --run-syncdb 2>/dev/null || python setup.py

WORKERS=$(python3 -c "import multiprocessing; print((multiprocessing.cpu_count()*2)+1)")
echo "  Workers:  $WORKERS ($(python3 -c 'import multiprocessing; print(multiprocessing.cpu_count())') CPU cores × 2 + 1)"
echo "  Threads:  4 per worker"
echo "  Capacity: ~$(($WORKERS * 4 * 10)) concurrent requests"
echo "  URL:      http://0.0.0.0:8000"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Check if Gunicorn installed
if command -v gunicorn &> /dev/null; then
    gunicorn vision_rag.wsgi:application \
        --config gunicorn.conf.py \
        --preload
else
    echo "⚠ Gunicorn not found. Install: pip install gunicorn"
    echo "  Falling back to Django dev server (not for production)"
    python manage.py runserver 0.0.0.0:8000
fi
