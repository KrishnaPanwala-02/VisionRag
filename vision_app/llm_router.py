"""
VisionRAG LLM Router — Local Mode
===================================
Uses Ollama as the primary LLM (runs locally on your machine).
Cloud providers (Groq, Together, OpenRouter) are optional fallbacks
— leave their keys empty if you want Ollama only.

Setup Ollama:
  1. Install: https://ollama.com
  2. Pull model: ollama pull llama3.2
  3. It runs automatically — no API keys needed
"""

import os
import json
import time
import threading
import requests
from django.conf import settings

_lock     = threading.Lock()
_counters = {}

def _next_index(pool_name: str, pool_size: int) -> int:
    with _lock:
        idx = _counters.get(pool_name, 0)
        _counters[pool_name] = (idx + 1) % pool_size
        return idx

_rate_limited = {}

def _is_rate_limited(key: str) -> bool:
    ts = _rate_limited.get(key)
    if ts and (time.time() - ts) < 60:
        return True
    return False

def _mark_rate_limited(key: str):
    _rate_limited[key] = time.time()
    print(f"[LLM] Key ...{key[-6:]} rate limited — cooling off 60s")

class RateLimitError(Exception): pass
class ProviderError(Exception):  pass


def _build_pool():
    pool = []

    # ── OLLAMA FIRST (local, always available) ───────────────────────────────
    ollama_url = getattr(settings, 'OLLAMA_BASE_URL', 'http://localhost:11434')
    pool.append({'name': 'Ollama', 'type': 'ollama',
                 'url': ollama_url, 'model': 'llama3.2'})
    print(f"[LLM] Ollama: {ollama_url}")

    # ── GROQ (optional cloud fallback) ───────────────────────────────────────
    groq_raw = getattr(settings, 'GROQ_API_KEYS', '') or getattr(settings, 'GROQ_API_KEY', '')
    groq_keys = [k.strip() for k in groq_raw.split(',') if k.strip() and k.strip() not in ('your_groq_key_here', '')]
    for key in groq_keys:
        pool.append({'name': f'Groq({key[-4:]})', 'type': 'groq',
                     'key': key, 'model': 'llama-3.1-8b-instant'})
    if groq_keys:
        print(f"[LLM] Groq fallback: {len(groq_keys)} key(s)")

    # ── TOGETHER AI (optional cloud fallback) ────────────────────────────────
    together_raw = getattr(settings, 'TOGETHER_API_KEYS', '') or getattr(settings, 'TOGETHER_API_KEY', '')
    together_keys = [k.strip() for k in together_raw.split(',') if k.strip() and k.strip() not in ('your_together_key_here', '')]
    for key in together_keys:
        pool.append({'name': f'Together({key[-4:]})', 'type': 'together',
                     'key': key, 'model': 'meta-llama/Llama-3-8b-chat-hf'})
    if together_keys:
        print(f"[LLM] Together fallback: {len(together_keys)} key(s)")

    # ── OPENROUTER (optional cloud fallback) ─────────────────────────────────
    or_raw = getattr(settings, 'OPENROUTER_API_KEYS', '') or getattr(settings, 'OPENROUTER_API_KEY', '')
    or_keys = [k.strip() for k in or_raw.split(',') if k.strip() and k.strip() not in ('your_openrouter_key_here', '')]
    for key in or_keys:
        pool.append({'name': f'OpenRouter({key[-4:]})', 'type': 'openrouter',
                     'key': key, 'model': 'meta-llama/llama-3-8b-instruct:free'})
    if or_keys:
        print(f"[LLM] OpenRouter fallback: {len(or_keys)} key(s)")

    return pool


_pool = None
_pool_lock = threading.Lock()

def _get_pool():
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                _pool = _build_pool()
                print(f"[LLM] Provider pool: {[p['name'] for p in _pool]}")
    return _pool


def _call_ollama(prompt, system, url, model):
    r = requests.post(
        f"{url}/api/generate",
        json={'model': model, 'prompt': prompt, 'system': system,
              'stream': True,
              'options': {'temperature': 0.0, 'num_predict': 700,
                          'num_ctx': 4096, 'top_k': 10, 'repeat_penalty': 1.1}},
        stream=True, timeout=120
    )
    for line in r.iter_lines():
        if line:
            try:
                obj = json.loads(line)
                t = obj.get('response', '')
                if t: yield t
                if obj.get('done'): break
            except: continue


def _call_groq(prompt, system, model, key):
    r = requests.post(
        'https://api.groq.com/openai/v1/chat/completions',
        headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'},
        json={'model': model,
              'messages': [{'role': 'system', 'content': system},
                           {'role': 'user',   'content': prompt}],
              'stream': True, 'temperature': 0.0, 'max_tokens': 700},
        stream=True, timeout=30
    )
    if r.status_code == 429: raise RateLimitError()
    if r.status_code != 200: raise ProviderError(f'HTTP {r.status_code}')
    for line in r.iter_lines():
        if line:
            line = line.decode() if isinstance(line, bytes) else line
            if line.startswith('data: '):
                d = line[6:].strip()
                if d == '[DONE]': break
                try:
                    tok = json.loads(d)['choices'][0]['delta'].get('content','')
                    if tok: yield tok
                except: continue


def _call_together(prompt, system, model, key):
    r = requests.post(
        'https://api.together.xyz/v1/chat/completions',
        headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'},
        json={'model': model,
              'messages': [{'role': 'system', 'content': system},
                           {'role': 'user',   'content': prompt}],
              'stream': True, 'temperature': 0, 'max_tokens': 700},
        stream=True, timeout=30
    )
    if r.status_code == 429: raise RateLimitError()
    if r.status_code != 200: raise ProviderError(f'HTTP {r.status_code}')
    for line in r.iter_lines():
        if line:
            line = line.decode() if isinstance(line, bytes) else line
            if line.startswith('data: '):
                d = line[6:].strip()
                if d == '[DONE]': break
                try:
                    tok = json.loads(d)['choices'][0]['delta'].get('content','')
                    if tok: yield tok
                except: continue


def _call_openrouter(prompt, system, model, key):
    r = requests.post(
        'https://openrouter.ai/api/v1/chat/completions',
        headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json',
                 'HTTP-Referer': 'https://visionrag.app', 'X-Title': 'VisionRAG'},
        json={'model': model,
              'messages': [{'role': 'system', 'content': system},
                           {'role': 'user',   'content': prompt}],
              'stream': True, 'temperature': 0, 'max_tokens': 700},
        stream=True, timeout=30
    )
    if r.status_code == 429: raise RateLimitError()
    if r.status_code != 200: raise ProviderError(f'HTTP {r.status_code}')
    for line in r.iter_lines():
        if line:
            line = line.decode() if isinstance(line, bytes) else line
            if line.startswith('data: '):
                d = line[6:].strip()
                if d == '[DONE]': break
                try:
                    tok = json.loads(d)['choices'][0]['delta'].get('content','')
                    if tok: yield tok
                except: continue


def stream_llm(prompt: str, system: str):
    """
    Try Ollama first (local). Fall back to cloud providers if Ollama is down.
    """
    pool = _get_pool()
    ollama = next((p for p in pool if p['type'] == 'ollama'), None)
    cloud  = [p for p in pool if p['type'] != 'ollama']

    # ── Try Ollama first ─────────────────────────────────────────────────────
    if ollama:
        try:
            print(f"[LLM] → Ollama ({ollama['model']})")
            gen = _call_ollama(prompt, system, ollama['url'], ollama['model'])
            first = next(gen, None)
            if first is not None:
                def _chain(f, g):
                    yield f
                    yield from g
                return _chain(first, gen)
            print("[LLM] Ollama returned empty — trying cloud fallback")
        except requests.exceptions.ConnectionError:
            print("[LLM] Ollama not running — trying cloud fallback")
        except Exception as e:
            print(f"[LLM] Ollama error: {e} — trying cloud fallback")

    # ── Cloud fallback ───────────────────────────────────────────────────────
    if cloud:
        start = _next_index('cloud', len(cloud))
        order = cloud[start:] + cloud[:start]
        for p in order:
            key = p.get('key', '')
            if _is_rate_limited(key):
                continue
            try:
                print(f"[LLM] → {p['name']} (cloud fallback)")
                if p['type'] == 'groq':
                    gen = _call_groq(prompt, system, p['model'], key)
                elif p['type'] == 'together':
                    gen = _call_together(prompt, system, p['model'], key)
                elif p['type'] == 'openrouter':
                    gen = _call_openrouter(prompt, system, p['model'], key)
                first = next(gen, None)
                if first is None:
                    raise ProviderError('Empty response')
                def _chain(f, g):
                    yield f
                    yield from g
                return _chain(first, gen)
            except RateLimitError:
                _mark_rate_limited(key)
                continue
            except requests.exceptions.ConnectionError:
                print(f"[LLM] {p['name']} unreachable")
                continue
            except Exception as e:
                print(f"[LLM] {p['name']} failed: {e}")
                continue

    # ── Nothing works ────────────────────────────────────────────────────────
    def _fail():
        yield "⚠️ Ollama is not running. Start it with: ollama serve — then make sure llama3.2 is installed: ollama pull llama3.2"
    return _fail()


def get_provider_status() -> list:
    pool = _get_pool()
    result = []
    for p in pool:
        key = p.get('key', '')
        result.append({
            'name': p['name'],
            'type': p['type'],
            'rate_limited': _is_rate_limited(key),
            'model': p.get('model', ''),
        })
    return result
