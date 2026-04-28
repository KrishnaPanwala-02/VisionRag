"""
Usage Analytics Dashboard
Shows charts: messages per day, component types, session activity,
most asked topics, busiest hours.
"""
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from django.shortcuts import render
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from django.utils import timezone
from django.conf import settings
from .models import ChatSession, ChatMessage


@login_required
def analytics_view(request):
    return render(request, 'vision_app/analytics.html')


@login_required
def analytics_data(request):
    """Returns all analytics data as JSON for the frontend charts."""
    user = request.user
    sessions = ChatSession.objects.filter(user=user).prefetch_related('messages').order_by('created_at')
    messages = ChatMessage.objects.filter(session__user=user).order_by('created_at')

    now = timezone.now()
    now_local = timezone.localtime(now)
    days_30_ago = now - timedelta(days=30)
    days_7_ago  = now - timedelta(days=7)

    # ── 1. Messages per day (last 30 days) ────────────────────────────────
    msgs_by_day = defaultdict(int)
    for msg in messages.filter(created_at__gte=days_30_ago):
        # convert to local time before grouping by day
        day = timezone.localtime(msg.created_at).strftime('%Y-%m-%d')
        msgs_by_day[day] += 1

    # Fill in missing days with 0
    msgs_per_day = []
    for i in range(30):
        day = (now_local - timedelta(days=29 - i)).strftime('%Y-%m-%d')
        msgs_per_day.append({'date': day, 'count': msgs_by_day.get(day, 0)})

    # ── 2. Sessions per day (last 30 days) ───────────────────────────────
    sess_by_day = defaultdict(int)
    for sess in sessions.filter(created_at__gte=days_30_ago):
        day = timezone.localtime(sess.created_at).strftime('%Y-%m-%d')
        sess_by_day[day] += 1

    sessions_per_day = []
    for i in range(30):
        day = (now_local - timedelta(days=29 - i)).strftime('%Y-%m-%d')
        sessions_per_day.append({'date': day, 'count': sess_by_day.get(day, 0)})

    # ── 3. Component types analyzed ───────────────────────────────────────
    component_counts = Counter()
    for sess in sessions:
        for label in sess.get_images():
            if label and label.lower() not in ('unknown', '', 'analysis failed'):
                # Normalize to title case
                component_counts[label.strip().title()] += 1

    top_components = [{'name': k, 'count': v}
                      for k, v in component_counts.most_common(8)]

    # ── 4. Activity by hour of day ────────────────────────────────────────
    hour_counts = defaultdict(int)
    for msg in messages.filter(role='user'):
        # use local time hour so chart reflects user's timezone
        hour = timezone.localtime(msg.created_at).hour
        hour_counts[hour] += 1

    hours_data = []
    for h in range(24):
        label = f"{h:02d}:00"
        hours_data.append({'hour': label, 'count': hour_counts.get(h, 0)})

    # ── 5. Most common question topics (word frequency) ───────────────────
    STOP = {'what', 'how', 'can', 'does', 'the', 'is', 'are', 'a', 'an',
            'in', 'of', 'to', 'and', 'or', 'for', 'this', 'that', 'it',
            'my', 'me', 'i', 'do', 'be', 'was', 'has', 'have', 'with',
            'about', 'tell', 'give', 'show', 'get', 'its', 'their', 'on'}
    word_counts = Counter()
    for msg in messages.filter(role='user'):
        words = re.findall(r'\b[a-zA-Z]{4,}\b', msg.content.lower())
        for w in words:
            if w not in STOP:
                word_counts[w] += 1

    top_topics = [{'word': k, 'count': v}
                  for k, v in word_counts.most_common(12)]

    # ── 6. Summary stats ──────────────────────────────────────────────────
    total_sessions  = sessions.count()
    total_messages  = messages.count()
    user_messages   = messages.filter(role='user').count()
    sessions_week   = sessions.filter(created_at__gte=days_7_ago).count()
    msgs_week       = messages.filter(created_at__gte=days_7_ago, role='user').count()
    total_pdfs      = sum(len(s.get_pdfs()) for s in sessions)
    total_images    = sum(len(s.get_images()) for s in sessions)

    # Avg messages per session
    avg_msgs = round(total_messages / total_sessions, 1) if total_sessions > 0 else 0

    # Most active day
    if msgs_by_day:
        best_day = max(msgs_by_day, key=msgs_by_day.get)
        best_day_count = msgs_by_day[best_day]
    else:
        best_day, best_day_count = '—', 0

    response = {
        'msgs_per_day':      msgs_per_day,
        'sessions_per_day':  sessions_per_day,
        'top_components':    top_components,
        'hours_data':        hours_data,
        'top_topics':        top_topics,
        'stats': {
            'total_sessions':  total_sessions,
            'total_messages':  total_messages,
            'user_messages':   user_messages,
            'sessions_week':   sessions_week,
            'msgs_week':       msgs_week,
            'total_pdfs':      total_pdfs,
            'total_images':    total_images,
            'avg_msgs':        avg_msgs,
            'best_day':        best_day,
            'best_day_count':  best_day_count,
        }
    }

    # Add lightweight debug info when DEBUG is enabled
    if getattr(settings, 'DEBUG', False):
        response['debug'] = {
            'now': now.isoformat(),
            'now_local': now_local.isoformat(),
            'total_messages': messages.count(),
            'msgs_by_day_keys': sorted(list(msgs_by_day.keys()))[:10],
        }

    return JsonResponse(response)
