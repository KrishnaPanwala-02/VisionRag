import numpy as np
import re
import os
import base64
import time
from PIL import Image, ImageEnhance

try:
    import cv2  # type: ignore
except Exception:
    cv2 = None


def resize_for_ollama(image_path: str, max_size: int = 800) -> str:
    img = Image.open(image_path).convert('RGB')
    w, h = img.size
    if max(w, h) > max_size:
        ratio = max_size / max(w, h)
        img = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)
    resized_path = image_path + '_resized.jpg'
    img.save(resized_path, 'JPEG', quality=85, optimize=True)
    return resized_path


def is_blurred(image_path: str, threshold: float = 100.0):
    """
    Returns (is_blurry: bool, blur_score: float).
    If OpenCV isn't installed, returns (False, 0.0) without crashing.
    """
    if cv2 is None:
        return False, 0.0
    img = cv2.imread(image_path)
    if img is None:
        pil_img = Image.open(image_path).convert('RGB')
        img = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape
    if max(h, w) > 600:
        scale = 600 / max(h, w)
        gray = cv2.resize(gray, (int(w * scale), int(h * scale)))
    variance = cv2.Laplacian(gray, cv2.CV_64F).var()
    return bool(variance < threshold), round(float(variance), 2)


def sharpen_image(image_path: str, output_path: str) -> str:
    if cv2 is None:
        # No-op fallback: save original to output path
        img = Image.open(image_path).convert('RGB')
        img.save(output_path, quality=90)
        return output_path
    img = Image.open(image_path).convert('RGB')
    img_np = np.array(img)
    img_bgr = cv2.cvtColor(img_np, cv2.COLOR_RGB2BGR)
    gaussian = cv2.GaussianBlur(img_bgr, (0, 0), 3)
    sharpened = cv2.addWeighted(img_bgr, 1.8, gaussian, -0.8, 0)
    sharpened_rgb = cv2.cvtColor(sharpened, cv2.COLOR_BGR2RGB)
    result_img = Image.fromarray(sharpened_rgb)
    enhancer = ImageEnhance.Sharpness(result_img)
    result_img = enhancer.enhance(2.0)
    result_img.save(output_path, quality=90)
    return output_path


def encode_image_base64(image_path: str) -> str:
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


# ─── SHARED PROMPT ────────────────────────────────────────────────────────────
_PROMPT = (
    "You are a computer hardware identification expert.\n"
    "Your job is to identify computer hardware components from images.\n\n"
    "Respond in exactly this two-line format:\n"
    "LABEL: <name of the hardware component, e.g. 'monitor', 'GPU', 'CPU', 'RAM', "
    "'motherboard', 'power supply', 'SSD', 'HDD', 'CPU cooler', 'PC case', 'keyboard', 'mouse'>\n"
    "DESCRIPTION: <2-3 sentences describing the hardware — its type, visible ports/features, "
    "form factor, and condition>\n\n"
    "Rules:\n"
    "- Focus ONLY on identifying computer hardware components\n"
    "- Use standard hardware category names (no brand names, no model numbers)\n"
    "- If the image does NOT contain computer hardware, write LABEL: not a hardware component\n"
    "- Be precise: distinguish between GPU and CPU, between SSD and HDD, "
    "between ATX and ITX motherboard, etc."
)


def _parse_response(raw: str) -> dict:
    """Parse LABEL:/DESCRIPTION: format from model response."""
    label = ""
    description = ""
    for line in raw.split('\n'):
        low = line.strip().lower()
        if low.startswith('label:'):
            label = line.strip()[6:].strip()
        elif low.startswith('description:'):
            description = line.strip()[12:].strip()

    # Fallback if model ignored the format
    if not label:
        lines = [l.strip() for l in raw.split('\n') if l.strip()]
        label = lines[0] if lines else "unknown object"
        description = " ".join(lines[1:]) if len(lines) > 1 else raw

    label = re.sub(r'\*+', '', label).strip()
    if not label or label.lower() in ('', 'unknown', 'none'):
        label = "unknown object"

    return {'label': label[:120], 'description': description[:600]}


# ─── PROVIDER 1: GEMINI ───────────────────────────────────────────────────────

# Try these Gemini models in order — each has its own free quota
GEMINI_MODELS = [
    "gemini-2.5-flash",      # your working model — try first
    "gemini-2.0-flash",
    "gemini-1.5-flash",
    "gemini-1.5-flash-8b",
]

def _analyze_with_gemini(pil_image: Image.Image, api_key: str) -> dict:
    """Try each Gemini model in order until one works."""
    from google import genai

    client = genai.Client(api_key=api_key)

    for model in GEMINI_MODELS:
        try:
            print(f"[IMAGE] Trying Gemini model: {model}")
            response = client.models.generate_content(
                model=model,
                contents=[_PROMPT, pil_image]
            )
            raw = (response.text or "").strip()
            if raw:
                result = _parse_response(raw)
                print(f"[IMAGE] Gemini {model} succeeded: {result['label']}")
                return result
        except Exception as e:
            err = str(e)
            if '429' in err or 'RESOURCE_EXHAUSTED' in err or 'quota' in err.lower():
                print(f"[IMAGE] Gemini {model} quota exhausted — trying next model")
                continue
            elif 'not found' in err.lower() or '404' in err:
                print(f"[IMAGE] Gemini {model} not available — trying next model")
                continue
            else:
                print(f"[IMAGE] Gemini {model} error: {e}")
                continue

    raise Exception("All Gemini models exhausted")


# ─── PROVIDER 2: OLLAMA VISION ────────────────────────────────────────────────

def _analyze_with_ollama(image_path: str, ollama_url: str) -> dict:
    """Use Ollama llava/llama3.2-vision as fallback."""
    import requests

    # Try these vision models in order
    vision_models = ["llama3.2-vision", "llava", "llava:13b", "bakllava"]

    # Resize and encode image
    resized = resize_for_ollama(image_path, max_size=512)
    img_b64 = encode_image_base64(resized)
    try:
        os.remove(resized)
    except Exception:
        pass

    for model in vision_models:
        try:
            print(f"[IMAGE] Trying Ollama model: {model}")
            r = requests.post(
                f"{ollama_url}/api/generate",
                json={
                    "model": model,
                    "prompt": _PROMPT,
                    "images": [img_b64],
                    "stream": False,
                    "options": {"temperature": 0.0, "num_predict": 200}
                },
                timeout=60
            )
            if r.status_code == 404:
                print(f"[IMAGE] Ollama model {model} not found — trying next")
                continue
            r.raise_for_status()
            raw = r.json().get("response", "").strip()
            if raw:
                result = _parse_response(raw)
                print(f"[IMAGE] Ollama {model} succeeded: {result['label']}")
                return result
        except requests.exceptions.ConnectionError:
            raise Exception("Ollama not running. Run: ollama serve")
        except Exception as e:
            print(f"[IMAGE] Ollama {model} error: {e}")
            continue

    raise Exception("No Ollama vision model available. Run: ollama pull llama3.2-vision")


# ─── MAIN ENTRY POINT ─────────────────────────────────────────────────────────

def analyze_image(image_path: str, ollama_url: str = "http://localhost:11434") -> dict:
    """
    Analyze a hardware component image.

    Provider priority:
      1. Gemini API  — tries gemini-2.0-flash → 1.5-flash → 1.5-flash-8b → 1.0-pro-vision
      2. Ollama      — tries llama3.2-vision → llava → bakllava (local, no quota)

    Automatically falls back when quota is exceeded on any model/provider.
    """
    from django.conf import settings

    # Resize image once for all providers
    pil_image = Image.open(image_path).convert('RGB')
    w, h = pil_image.size
    if max(w, h) > 800:
        ratio = 800 / max(w, h)
        pil_image = pil_image.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)

    # ── Try Gemini first — rotate across all configured keys ──
    # Support both GEMINI_API_KEYS (comma-separated) and GEMINI_API_KEY (single)
    keys_raw = (
        getattr(settings, 'GEMINI_API_KEYS', '') or
        getattr(settings, 'GEMINI_API_KEY', '') or
        ''
    )
    gemini_keys = [k.strip() for k in str(keys_raw).split(',') if k.strip() and k.strip() not in ('', 'None')]
    if gemini_keys:
        try:
            from google import genai  # noqa — check import available
            last_err = None
            for key in gemini_keys:
                try:
                    return _analyze_with_gemini(pil_image, key)
                except Exception as e:
                    if 'exhausted' in str(e).lower() or 'quota' in str(e).lower():
                        print(f"[IMAGE] Gemini key ...{key[-6:]} exhausted — trying next key")
                        last_err = e
                        continue
                    raise
            print(f"[IMAGE] All Gemini keys exhausted — falling back to Ollama")
        except ImportError:
            print("[IMAGE] google-genai not installed — skipping Gemini")
        except Exception as e:
            print(f"[IMAGE] Gemini failed: {e} — falling back to Ollama")
    else:
        print("[IMAGE] No GEMINI_API_KEY set — skipping Gemini")

    # ── Fall back to Ollama ──
    try:
        return _analyze_with_ollama(image_path, ollama_url)
    except Exception as e:
        print(f"[IMAGE] Ollama also failed: {e}")
        return {
            'label': 'analysis failed',
            'description': (
                f"All image analysis providers failed. "
                f"Gemini: quota exhausted on all models. "
                f"Ollama: {e}. "
                f"To fix: add a new GEMINI_API_KEY in settings.py, "
                f"or run 'ollama pull llama3.2-vision' for local analysis."
            )
        }
