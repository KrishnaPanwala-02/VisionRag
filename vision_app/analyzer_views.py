import os
import json
import tempfile
import hashlib
import threading
import queue
import time
from django.shortcuts import render
from django.http import JsonResponse, StreamingHttpResponse
from django.contrib.auth.decorators import login_required
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings


@login_required
def analyzer_view(request):
    """Render the Smart Component Analyzer page."""
    return render(request, 'vision_app/analyzer.html')


@csrf_exempt
@login_required
def analyzer_run(request):
    """
    Agentic pipeline — streams results section by section via SSE.
    Adds caching: if identical PDF+image were processed before, serve cached results.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    image_file = request.FILES.get('image')
    pdf_file   = request.FILES.get('pdf')

    if not image_file or not pdf_file:
        return JsonResponse({'error': 'Both image and PDF are required'}, status=400)

    # Helper to stream cached step content as tokens (small chunks)
    def stream_cached(component, description, result_map):
        # identify
        yield f"data: {json.dumps({'step':'identify','status':'running','msg':'Loading cached result...'})}\n\n"
        yield f"data: {json.dumps({'step':'identify','status':'done','component': component, 'description': description})}\n\n"
        # pdf step
        yield f"data: {json.dumps({'step':'pdf','status':'running','msg':'Using cached PDF extraction...'})}\n\n"
        yield f"data: {json.dumps({'step':'pdf','status':'done'})}\n\n"
        # for each agent step, stream tokens
        for sid, text in result_map.items():
            if sid not in ('specs','warnings','installation','compatibility'): continue
            yield f"data: {json.dumps({'step': sid, 'status': 'running', 'msg': 'Retrieving cached section...'})}\n\n"
            # split into modest tokens to emulate streaming
            chunk_size = 300
            for i in range(0, len(text), chunk_size):
                tok = text[i:i+chunk_size]
                yield f"data: {json.dumps({'step': sid, 'status':'token', 'token': tok})}\n\n"
            yield f"data: {json.dumps({'step': sid, 'status': 'done'})}\n\n"
        yield 'data: {"step": "done", "status": "done"}\n\n'

    def event_stream():
        # Immediate connected ping so client shows stream is open (debug)
        print("[ANALYZER] SSE connection opened")
        yield f"data: {json.dumps({'step':'debug','status':'info','msg':'sse_connected'})}\n\n"
        try:
            # compute hashes while saving temp files
            def save_and_hash(uploaded, suffix):
                h = hashlib.sha256()
                with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                    for chunk in uploaded.chunks():
                        tmp.write(chunk)
                        h.update(chunk)
                    tmp_path = tmp.name
                return tmp_path, h.hexdigest()

            img_path, img_hash = save_and_hash(image_file, os.path.splitext(image_file.name)[1] or '.jpg')
            pdf_path, pdf_hash = save_and_hash(pdf_file, '.pdf')

            # Debug: files saved
            print(f"[ANALYZER] saved files img={img_path} pdf={pdf_path} img_hash={img_hash} pdf_hash={pdf_hash}")
            yield f"data: {json.dumps({'step':'debug','status':'info','msg':'files_saved'})}\n\n"

            # Try cache lookup
            from .models import AnalyzerCache
            try:
                cache = AnalyzerCache.objects.filter(pdf_hash=pdf_hash, image_hash=img_hash).first()
            except Exception:
                cache = None

            if cache:
                # serve cached
                comp = cache.component or 'Hardware Component'
                desc = cache.description or ''
                result_map = cache.get_result()
                for out in stream_cached(comp, desc, result_map):
                    yield out
                try:
                    os.unlink(img_path)
                except Exception:
                    pass
                try:
                    os.unlink(pdf_path)
                except Exception:
                    pass
                return

            # No cache — proceed with normal pipeline
            from .image_utils import analyze_image
            from .rag_engine import _extract_text, _make_chunks, _keyword_search
            from .llm_router import stream_llm

            # Debug: about to run identify (vision)
            print(f"[ANALYZER] starting identify on {img_path}")
            yield f"data: {json.dumps({'step':'debug','status':'info','msg':'starting_identify'})}\n\n"

            # STEP 1: identify
            yield f"data: {json.dumps({'step': 'identify', 'status': 'running', 'msg': 'Analyzing hardware image with Vision...'})}\n\n"
            ollama_url = getattr(settings, 'OLLAMA_BASE_URL', 'http://localhost:11434')
            result = analyze_image(img_path, ollama_url)
            print(f"[ANALYZER] identify result: {result}")
            yield f"data: {json.dumps({'step':'debug','status':'info','msg':'identify_done'})}\n\n"
            component = result.get('label', 'Unknown Component')
            description = result.get('description', '')
            yield f"data: {json.dumps({'step': 'identify', 'status': 'done', 'component': component, 'description': description})}\n\n"

            # Debug: about to extract pdf text
            print(f"[ANALYZER] starting pdf extract on {pdf_path}")
            yield f"data: {json.dumps({'step':'debug','status':'info','msg':'starting_pdf_extract'})}\n\n"
            # STEP 2: extract pdf text
            yield f"data: {json.dumps({'step': 'pdf', 'status': 'running', 'msg': 'Extracting PDF content...'})}\n\n"
            pdf_text = _extract_text(pdf_path)
            print(f"[ANALYZER] pdf_text length: {len(pdf_text)}")
            yield f"data: {json.dumps({'step':'debug','status':'info','msg':'pdf_extracted','len': len(pdf_text)})}\n\n"
            if not pdf_text.strip():
                yield f"data: {json.dumps({'step': 'pdf', 'status': 'error', 'msg': 'Could not extract text from PDF'})}\n\n"
                yield 'data: {"step": "done"}\n\n'
                try:
                    os.unlink(img_path)
                except Exception:
                    pass
                try:
                    os.unlink(pdf_path)
                except Exception:
                    pass
                return
            chunks = _make_chunks(pdf_text, size=400, overlap=80)
            summary = f"Extracted {len(pdf_text):,} characters across {len(chunks)} sections"
            yield f"data: {json.dumps({'step': 'pdf', 'status': 'done', 'msg': summary})}\n\n"

            # helper to run an agent step and collect result text
            def stream_with_heartbeat(llm_fn, full_prompt, system, step_id, heartbeat_interval=8, max_wait=60):
                """Run llm_fn(full_prompt, system) in a background thread and yield tokens.
                If the llm blocks, emit heartbeat events every heartbeat_interval seconds so the UI doesn't stay 'Waiting...'.
                If no token/error is produced for max_wait seconds, abort the step with a timeout error.
                """
                q = queue.Queue()

                def target():
                    try:
                        for tok in llm_fn(full_prompt, system):
                            q.put(('token', tok))
                    except Exception as e:
                        q.put(('error', str(e)))
                    finally:
                        q.put(('done', None))

                t = threading.Thread(target=target, daemon=True)
                t.start()

                last_activity = time.time()

                while True:
                    try:
                        typ, payload = q.get(timeout=heartbeat_interval)
                        last_activity = time.time()
                    except queue.Empty:
                        # heartbeat
                        # if we've been waiting too long without any token, abort with timeout
                        if time.time() - last_activity > max_wait:
                            yield ('error', f'LLM step timed out after {max_wait} seconds')
                            break
                        yield ('heartbeat', {'step': step_id, 'msg': 'still processing'})
                        continue

                    if typ == 'token':
                        yield ('token', payload)
                    elif typ == 'error':
                        yield ('error', payload)
                        break
                    elif typ == 'done':
                        break

            def run_agent_step_collect(step_id, title, keywords, prompt):
                # signal running
                yield f"data: {json.dumps({'step': step_id, 'status': 'running', 'msg': f'Agent searching for {title}...'})}\n\n"
                # emit debug event and server log for start
                dbg_start = f'Starting LLM step {step_id} for {title}'
                print(f"[ANALYZER] {dbg_start}")
                yield f"data: {json.dumps({'step':'debug','status':'info','msg': dbg_start})}\n\n"
                if chunks:
                    _kw = _keyword_search(' '.join(keywords), chunks, n=6)
                    ctx = ' '.join(item['text'] for item in _kw) if _kw else ''
                else:
                    ctx = ''
                full_prompt = (
                    f"Component: {component}\n\nPDF TEXT:\n{ctx}\n\n{prompt}"
                )
                system = (
                    f"You are an expert hardware analyst examining a {component}. Extract information exactly as written in the provided PDF text. Be concise and structured. Never add information not present in the text."
                )
                collected = ''
                try:
                    # iterate tokens from helper; emit debug when LLM finishes or errors
                    for kind, token in stream_with_heartbeat(stream_llm, full_prompt, system, step_id):
                        if kind == 'token':
                            collected += token
                            yield f"data: {json.dumps({'step': step_id, 'status': 'token', 'token': token})}\n\n"
                        elif kind == 'heartbeat':
                            # heartbeat: keep UI alive
                            yield f"data: {json.dumps({'step': step_id, 'status': 'running', 'msg': token['msg'] if isinstance(token, dict) else 'still processing'})}\n\n"
                        elif kind == 'error':
                            err_msg = token
                            yield f"data: {json.dumps({'step': step_id, 'status': 'error', 'msg': err_msg})}\n\n"
                            print(f"[ANALYZER] LLM error in {step_id}: {err_msg}")
                            return None
                except Exception as e:
                    yield f"data: {json.dumps({'step': step_id, 'status': 'error', 'msg': str(e)})}\n\n"
                    print(f"[ANALYZER] Exception during LLM stream for {step_id}: {e}")
                    return None
                finally:
                    dbg_done = f'LLM step {step_id} finished'
                    print(f"[ANALYZER] {dbg_done}")
                    yield f"data: {json.dumps({'step':'debug','status':'info','msg': dbg_done})}\n\n"
                yield f"data: {json.dumps({'step': step_id, 'status': 'done'})}\n\n"
                return collected

            result_map = {}

            # Step 3: specs
            specs_prompt = (
                "Extract a technical specifications table for this component from the PDF text above.\n"
                "Format each spec on its own line as: Spec Name: Value\n"
            )
            gen = run_agent_step_collect('specs', 'Technical Specifications', ['voltage','watt','power','dimension','weight','speed','capacity'], specs_prompt)
            # generator: iterate
            specs_text = None
            for out in gen:
                yield out

            # run_agent_step_collect returns a value when exhausted; capture full text separately
            try:
                collected = ''
                from .llm_router import stream_llm as _stream_llm
                if chunks:
                    _kw = _keyword_search(' '.join(['voltage','watt','power','dimension','weight','speed','capacity']), chunks, n=6)
                    ctx = ' '.join(item['text'] for item in _kw) if _kw else ''
                else:
                    ctx = ''
                full_prompt = f"Component: {component}\n\nPDF TEXT:\n{ctx}\n\n{specs_prompt}"
                system = f"You are an expert hardware analyst examining a {component}. Extract information exactly as written in the provided PDF text. Be concise and structured. Never add information not present in the text."
                for kind, tok in stream_with_heartbeat(_stream_llm, full_prompt, system, 'specs'):
                    if kind == 'token':
                        collected += tok
                    elif kind == 'error':
                        raise Exception(tok)
                specs_text = collected
            except Exception:
                specs_text = ''
            result_map['specs'] = specs_text or ''

            # Step 4: warnings
            warnings_text = ''
            try:
                collected = ''
                if chunks:
                    _kw = _keyword_search(' '.join(['warning','caution','danger','do not']), chunks, n=6)
                    warn_ctx = ' '.join(item['text'] for item in _kw) if _kw else ''
                else:
                    warn_ctx = ''
                # signal running to client
                yield f"data: {json.dumps({'step': 'warnings', 'status': 'running', 'msg': 'Agent searching for Warnings & Cautions...'})}\n\n"
                for kind, tok in stream_with_heartbeat(_stream_llm, f"Component: {component}\n\nPDF TEXT:\n{warn_ctx}\n\nExtract ALL warnings, start each with ⚠️", system, 'warnings'):
                    if kind == 'token':
                        collected += tok
                        yield f"data: {json.dumps({'step': 'warnings', 'status': 'token', 'token': tok})}\n\n"
                    elif kind == 'heartbeat':
                        yield f"data: {json.dumps({'step': 'warnings', 'status': 'running', 'msg': tok['msg'] if isinstance(tok, dict) else 'still processing'})}\n\n"
                    elif kind == 'error':
                        raise Exception(tok)
                warnings_text = collected
            except Exception:
                warnings_text = ''
            result_map['warnings'] = warnings_text
            # signal done for warnings
            yield f"data: {json.dumps({'step': 'warnings', 'status': 'done'})}\n\n"

            # Step 5: installation
            installation_text = ''
            try:
                collected = ''
                if chunks:
                    _kw = _keyword_search(' '.join(['install','setup','mount']), chunks, n=6)
                    inst_ctx = ' '.join(item['text'] for item in _kw) if _kw else ''
                else:
                    inst_ctx = ''
                # signal running
                yield f"data: {json.dumps({'step': 'installation', 'status': 'running', 'msg': 'Agent extracting installation steps...'})}\n\n"
                for kind, tok in stream_with_heartbeat(_stream_llm, f"Component: {component}\n\nPDF TEXT:\n{inst_ctx}\n\nExtract installation steps as numbered list", system, 'installation'):
                    if kind == 'token':
                        collected += tok
                        yield f"data: {json.dumps({'step': 'installation', 'status': 'token', 'token': tok})}\n\n"
                    elif kind == 'heartbeat':
                        yield f"data: {json.dumps({'step': 'installation', 'status': 'running', 'msg': tok['msg'] if isinstance(tok, dict) else 'still processing'})}\n\n"
                    elif kind == 'error':
                        raise Exception(tok)
                installation_text = collected
            except Exception:
                installation_text = ''
            result_map['installation'] = installation_text
            yield f"data: {json.dumps({'step': 'installation', 'status': 'done'})}\n\n"

            # Step 6: compatibility
            compatibility_text = ''
            try:
                collected = ''
                if chunks:
                    _kw = _keyword_search(' '.join(['compatible','support','requires']), chunks, n=6)
                    comp_ctx = ' '.join(item['text'] for item in _kw) if _kw else ''
                else:
                    comp_ctx = ''
                # signal running
                yield f"data: {json.dumps({'step': 'compatibility', 'status': 'running', 'msg': 'Agent extracting compatibility info...'})}\n\n"
                for kind, tok in stream_with_heartbeat(_stream_llm, f"Component: {component}\n\nPDF TEXT:\n{comp_ctx}\n\nExtract compatibility info", system, 'compatibility'):
                    if kind == 'token':
                        collected += tok
                        yield f"data: {json.dumps({'step': 'compatibility', 'status': 'token', 'token': tok})}\n\n"
                    elif kind == 'heartbeat':
                        yield f"data: {json.dumps({'step': 'compatibility', 'status': 'running', 'msg': tok['msg'] if isinstance(tok, dict) else 'still processing'})}\n\n"
                    elif kind == 'error':
                        raise Exception(tok)
                compatibility_text = collected
            except Exception:
                compatibility_text = ''
            result_map['compatibility'] = compatibility_text
            yield f"data: {json.dumps({'step': 'compatibility', 'status': 'done'})}\n\n"

            # Save to cache
            try:
                cache_obj = AnalyzerCache.objects.create(
                    pdf_hash=pdf_hash, image_hash=img_hash,
                    component=component, description=description,
                    result_json=json.dumps(result_map)
                )
            except Exception:
                pass

            # Stream final done
            yield 'data: {"step": "done", "status": "done"}\n\n'

            try:
                os.unlink(img_path)
            except Exception:
                pass
            try:
                os.unlink(pdf_path)
            except Exception:
                pass

        except Exception as e:
            import traceback
            print(traceback.format_exc())
            err = json.dumps({'step': 'error', 'msg': str(e)})
            yield f"data: {err}\n\n"

    return StreamingHttpResponse(event_stream(), content_type='text/event-stream')
