"""
Hardware Health Score Module
AI analyzes uploaded image + PDF and produces a 0-100 health score
with visual gauge, risk level, and detailed reasons.
"""
import os
import json
import tempfile
from django.shortcuts import render
from django.http import JsonResponse, StreamingHttpResponse
from django.contrib.auth.decorators import login_required
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings


@login_required
def health_score_view(request):
    return render(request, 'vision_app/health_score.html')


@csrf_exempt
@login_required
def health_score_run(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    image_file = request.FILES.get('image')
    pdf_file   = request.FILES.get('pdf')

    if not image_file:
        return JsonResponse({'error': 'Image is required'}, status=400)

    def event_stream():
        try:
            from .image_utils import analyze_image
            from .llm_router import stream_llm

            # ── Step 1: Identify component ─────────────────────────────────
            yield 'data: ' + json.dumps({'step': 'identify', 'status': 'running', 'msg': 'Identifying component from image...'}) + '\n\n'

            with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(image_file.name)[1] or '.jpg') as tmp:
                for chunk in image_file.chunks():
                    tmp.write(chunk)
                tmp_path = tmp.name

            ollama_url = getattr(settings, 'OLLAMA_BASE_URL', 'http://localhost:11434')
            result     = analyze_image(tmp_path, ollama_url)
            component  = result.get('label', 'Hardware Component')
            description = result.get('description', '')

            try: os.unlink(tmp_path)
            except: pass

            yield 'data: ' + json.dumps({'step': 'identify', 'status': 'done', 'component': component, 'description': description}) + '\n\n'

            # ── Step 2: Extract PDF context (optional) ─────────────────────
            pdf_context = ''
            if pdf_file:
                yield 'data: ' + json.dumps({'step': 'pdf', 'status': 'running', 'msg': 'Reading PDF for health indicators...'}) + '\n\n'
                from .rag_engine import _extract_text, _keyword_search, _make_chunks
                with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp_pdf:
                    for chunk in pdf_file.chunks():
                        tmp_pdf.write(chunk)
                    tmp_pdf_path = tmp_pdf.name
                pdf_text = _extract_text(tmp_pdf_path)
                try: os.unlink(tmp_pdf_path)
                except: pass
                if pdf_text.strip():
                    chunks   = _make_chunks(pdf_text, size=400, overlap=80)
                    kw       = ['warning', 'caution', 'temperature', 'voltage', 'operating', 'limit', 'maximum', 'minimum', 'life', 'failure', 'condition', 'specification']
                    results  = _keyword_search(' '.join(kw), chunks, n=5)
                    pdf_context = ' '.join(r['text'] for r in results)[:1500]
                yield 'data: ' + json.dumps({'step': 'pdf', 'status': 'done'}) + '\n\n'

            # ── Step 3: Generate health score ──────────────────────────────
            yield 'data: ' + json.dumps({'step': 'score', 'status': 'running', 'msg': 'AI is analyzing health...'}) + '\n\n'

            pdf_section = ''
            if pdf_context:
                pdf_section = 'RELEVANT SPECIFICATIONS FROM MANUAL:\n' + pdf_context + '\n\n'

            prompt = (
                'You are a hardware health assessment expert.\n'
                'Component identified: ' + component + '\n'
                'Visual description: ' + description + '\n\n'
                + pdf_section +
                'Based on the visual condition and any specification data provided, assess the health of this hardware component.\n\n'
                'Respond in this EXACT format (no deviations):\n\n'
                'SCORE: [number 0-100]\n\n'
                'RISK_LEVEL: [one of: Critical / Warning / Fair / Good / Excellent]\n\n'
                'SUMMARY:\n[2-3 sentence overall assessment]\n\n'
                'POSITIVE_INDICATORS:\n[bullet list starting with + of things that look good]\n\n'
                'RISK_INDICATORS:\n[bullet list starting with - of concerns or risks found]\n\n'
                'RECOMMENDATION:\n[specific action to take: replace, service, monitor, or keep using]\n\n'
                'Score guide: 90-100=Excellent, 75-89=Good, 50-74=Fair, 25-49=Warning, 0-24=Critical\n'
                'Be honest and realistic based on visible condition.'
            )

            system = (
                'You are a hardware health assessment AI. '
                'Evaluate hardware components objectively based on visual condition and specifications. '
                'Always respond in the exact format requested. '
                'Give realistic scores — do not default to 100 unless truly perfect condition.'
            )

            full_response = ''
            try:
                for token in stream_llm(prompt, system):
                    full_response += token
                    yield 'data: ' + json.dumps({'step': 'score', 'status': 'token', 'token': token}) + '\n\n'
            except Exception as e:
                yield 'data: ' + json.dumps({'step': 'score', 'status': 'error', 'msg': str(e)}) + '\n\n'
                yield 'data: {"step":"done"}\n\n'
                return

            # ── Parse the structured response ──────────────────────────────
            def extract(text, key, end_keys):
                try:
                    start = text.index(key + ':') + len(key) + 1
                    rest  = text[start:]
                    ends  = [rest.index(k + ':') for k in end_keys if k + ':' in rest and rest.index(k + ':') > 0]
                    return rest[:min(ends)].strip() if ends else rest.strip()
                except ValueError:
                    return ''

            all_keys = ['SCORE', 'RISK_LEVEL', 'SUMMARY', 'POSITIVE_INDICATORS', 'RISK_INDICATORS', 'RECOMMENDATION']

            score_raw = extract(full_response, 'SCORE', all_keys[1:])
            try:
                score = max(0, min(100, int(''.join(c for c in score_raw if c.isdigit())[:3])))
            except:
                score = 50

            risk        = extract(full_response, 'RISK_LEVEL', all_keys[2:])
            summary     = extract(full_response, 'SUMMARY', all_keys[3:])
            positives   = extract(full_response, 'POSITIVE_INDICATORS', all_keys[4:])
            risks       = extract(full_response, 'RISK_INDICATORS', all_keys[5:])
            recommend   = extract(full_response, 'RECOMMENDATION', [])

            # Clean risk level
            risk = risk.strip().split('\n')[0].strip()
            if not risk:
                if score >= 90:   risk = 'Excellent'
                elif score >= 75: risk = 'Good'
                elif score >= 50: risk = 'Fair'
                elif score >= 25: risk = 'Warning'
                else:             risk = 'Critical'

            yield 'data: ' + json.dumps({
                'step': 'done',
                'score': score,
                'risk': risk,
                'summary': summary,
                'positives': positives,
                'risks': risks,
                'recommendation': recommend,
                'component': component,
            }) + '\n\n'

        except Exception as e:
            import traceback
            print(traceback.format_exc())
            yield 'data: ' + json.dumps({'step': 'error', 'msg': str(e)}) + '\n\n'

    return StreamingHttpResponse(event_stream(), content_type='text/event-stream')
