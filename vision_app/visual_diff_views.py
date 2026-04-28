"""
Visual Diff Module — uses same Gemini pattern as image_utils.py, falls back to Ollama.
"""
import os, json, tempfile, io, base64, hashlib
from django.shortcuts import render
from django.http import JsonResponse, StreamingHttpResponse
from django.contrib.auth.decorators import login_required
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings


@login_required
def visual_diff_view(request):
    return render(request, 'vision_app/visual_diff.html')


@csrf_exempt
@login_required
def visual_diff_run(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    img1 = request.FILES.get('image1')
    img2 = request.FILES.get('image2')
    if not img1 or not img2:
        return JsonResponse({'error': 'Both images are required'}, status=400)

    def save_and_hash(uploaded, suffix):
        h = hashlib.sha256()
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            for chunk in uploaded.chunks():
                tmp.write(chunk); h.update(chunk)
            tmp_path = tmp.name
        return tmp_path, h.hexdigest()

    def stream_cached(result):
        yield 'data: ' + json.dumps({'status': 'loading', 'msg': 'Loading cached result...'}) + '\n\n'
        yield 'data: ' + json.dumps({'status': 'done',
            'component': result.get('component'), 'summary': result.get('summary'),
            'differences': result.get('differences'), 'condition': result.get('condition'),
            'recommendation': result.get('recommendation')}) + '\n\n'

    def event_stream():
        try:
            path1, h1 = save_and_hash(img1, os.path.splitext(img1.name)[1] or '.jpg')
            path2, h2 = save_and_hash(img2, os.path.splitext(img2.name)[1] or '.jpg')

            # Lookup cache (either order)
            from django.db.models import Q
            from .models import VisualDiffCache
            cache = VisualDiffCache.objects.filter(
                Q(img1_hash=h1, img2_hash=h2) | Q(img1_hash=h2, img2_hash=h1)
            ).first()

            if cache:
                try:
                    for out in stream_cached(cache.get_result()):
                        yield out
                finally:
                    try:
                        os.unlink(path1)
                        os.unlink(path2)
                    except Exception:
                        pass
                return

            # No cache: proceed with analysis (existing flow)
            from PIL import Image
            yield 'data: ' + json.dumps({'status': 'loading', 'msg': 'Loading images...'}) + '\n\n'

            pil1, pil2 = Image.open(path1), Image.open(path2)
            yield 'data: ' + json.dumps({'status': 'loading', 'msg': 'Analyzing with Vision AI...'}) + '\n\n'

            PROMPT = (
                "You are a hardware inspection expert comparing two component images.\n"
                "Structure your response EXACTLY as:\n\n"
                "COMPONENT IDENTIFIED:\n[component name]\n\n"
                "SUMMARY:\n[one sentence summary]\n\n"
                "DIFFERENCES FOUND:\n[bullet list starting with • , or: • No visible differences detected]\n\n"
                "CONDITION ASSESSMENT:\nImage 1: [Good/Fair/Poor — reason]\nImage 2: [Good/Fair/Poor — reason]\n\n"
                "RECOMMENDATION:\n[what to do]"
            )

            result_text = None
            api_key = getattr(settings, 'GEMINI_API_KEY', '') or getattr(settings, 'GEMINI_API_KEYS', '')
            if api_key:
                try:
                    from google import genai
                    client = genai.Client(api_key=api_key)
                    for model in ['gemini-2.5-flash', 'gemini-2.0-flash', 'gemini-1.5-flash']:
                        try:
                            resp = client.models.generate_content(
                                model=model, contents=[PROMPT, pil1, pil2])
                            if resp.text:
                                result_text = resp.text
                                break
                        except Exception:
                            continue
                except ImportError:
                    pass

            if not result_text:
                # fallback to Ollama
                try:
                    import requests as req
                    def pil_b64(img):
                        buf = io.BytesIO(); img.save(buf, format='JPEG'); return base64.b64encode(buf.getvalue()).decode()
                    ollama_url = getattr(settings, 'OLLAMA_BASE_URL', 'http://localhost:11434')
                    b64_1, b64_2 = pil_b64(pil1), pil_b64(pil2)
                    for vm in ['llama3.2-vision', 'llava']:
                        try:
                            r = req.post(f"{ollama_url}/api/generate", json={
                                'model': vm, 'prompt': PROMPT,
                                'images': [b64_1, b64_2], 'stream': False,
                                'options': {'temperature': 0.1, 'num_predict': 900}
                            }, timeout=120)
                            if r.status_code == 200:
                                t = r.json().get('response', '')
                                if t:
                                    result_text = t; break
                        except Exception:
                            continue
                except Exception:
                    pass

            try: os.unlink(path1); os.unlink(path2)
            except: pass

            if not result_text:
                yield 'data: ' + json.dumps({'status': 'error',
                    'msg': 'No vision AI available. Run: pip install google-genai  OR  ollama pull llava'}) + '\n\n'
                return

            # Parse sections
            def section(text, heading):
                try:
                    start = text.index(heading + ':') + len(heading) + 1
                    rest = text[start:]
                    ends = [rest.index(h + ':') for h in ['COMPONENT IDENTIFIED','SUMMARY','DIFFERENCES FOUND','CONDITION ASSESSMENT','RECOMMENDATION'] if h + ':' in rest and rest.index(h + ':') > 0]
                    return rest[:min(ends)].strip() if ends else rest.strip()
                except ValueError:
                    return ''

            comp = section(result_text, 'COMPONENT IDENTIFIED')
            summ = section(result_text, 'SUMMARY')
            diff = section(result_text, 'DIFFERENCES FOUND') or result_text
            cond = section(result_text, 'CONDITION ASSESSMENT')
            rec  = section(result_text, 'RECOMMENDATION')

            # Save to cache
            try:
                from .models import VisualDiffCache
                VisualDiffCache.objects.create(
                    img1_hash=h1, img2_hash=h2,
                    result_json=json.dumps({'component': comp, 'summary': summ, 'differences': diff, 'condition': cond, 'recommendation': rec})
                )
            except Exception:
                pass

            yield 'data: ' + json.dumps({'status': 'done', 'component': comp or 'Hardware Component', 'summary': summ, 'differences': diff, 'condition': cond, 'recommendation': rec}) + '\n\n'

        except Exception as e:
            import traceback; print(traceback.format_exc())
            yield 'data: ' + json.dumps({'status': 'error', 'msg': str(e)}) + '\n\n'

    return StreamingHttpResponse(event_stream(), content_type='text/event-stream')
