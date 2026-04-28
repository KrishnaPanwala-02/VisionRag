"""
Hardware Voice Bot — direct answers, no chain-of-thought shown to user.
"""
import json
from django.shortcuts import render
from django.http import JsonResponse, StreamingHttpResponse
from django.contrib.auth.decorators import login_required
from django.views.decorators.csrf import csrf_exempt


@login_required
def voice_bot_view(request):
    return render(request, 'vision_app/voice_bot.html')


@csrf_exempt
@login_required
def voice_bot_ask(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    try:
        data = json.loads(request.body)
    except Exception:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    question  = data.get('question', '').strip()
    history   = data.get('history', [])
    component = data.get('component', '')

    if not question:
        return JsonResponse({'error': 'No question provided'}, status=400)

    def event_stream():
        from .llm_router import stream_llm

        hist_ctx = ''
        if history:
            for msg in history[-6:]:
                role = 'Technician' if msg['role'] == 'user' else 'Assistant'
                hist_ctx += f"{role}: {msg['content']}\n"

        component_ctx = f"The technician is working with: {component}\n\n" if component else ''

        # Direct answer prompt — no step-by-step reasoning shown
        newline = chr(10)
        prompt = (
            f"{component_ctx}"
            f"{'Previous conversation:' + newline + hist_ctx + newline if hist_ctx else ''}"
            f"Question: {question}\n\n"
            "Give a direct, concise answer in 1-3 sentences. "
            "Do not show any reasoning steps or numbered lists. "
            "Just state the answer clearly and practically."
        )

        system = (
            "You are an expert hardware technician assistant. "
            "Answer questions about hardware directly and concisely in 1-3 sentences. "
            "Never show reasoning steps. Never use Step 1, Step 2 format. "
            "Just give the final practical answer immediately. "
            "If unrelated to hardware, say: I only help with hardware questions."
        )

        try:
            for token in stream_llm(prompt, system):
                yield 'data: ' + json.dumps({'token': token}) + '\n\n'
        except Exception as e:
            yield 'data: ' + json.dumps({'error': str(e)}) + '\n\n'

        yield 'data: ' + json.dumps({'done': True}) + '\n\n'

    return StreamingHttpResponse(event_stream(), content_type='text/event-stream')
