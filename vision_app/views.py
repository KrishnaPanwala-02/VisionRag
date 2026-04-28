import os, json, uuid, traceback, re, threading, shutil
from django.core.mail import send_mail
from django.contrib.auth.tokens import default_token_generator
from django.utils.http import urlsafe_base64_encode, urlsafe_base64_decode
from django.utils.encoding import force_bytes, force_str
from django.template.loader import render_to_string
from pathlib import Path
from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse, StreamingHttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.models import User
from django.contrib.auth.decorators import login_required
from django.conf import settings
from .models import UserProfile, ChatSession, ChatMessage

# ─── VALIDATION HELPERS ──────────────────────────────────────────────────────

VALID_EMAIL_DOMAINS = {
    'gmail.com','yahoo.com','hotmail.com','outlook.com','live.com','icloud.com',
    'protonmail.com','mail.com','aol.com','msn.com','ymail.com','googlemail.com',
    'me.com','mac.com','inbox.com','zoho.com','fastmail.com','tutanota.com',
    'rediffmail.com','yahoo.in','yahoo.co.uk','yahoo.co.in','hotmail.co.uk',
    'outlook.in','company.com','edu.in','ac.in','gov.in','nic.in',
}

BLOCKED_USERNAMES = {'admin','root','superuser','administrator','moderator','system','support','help','visionrag'}

def validate_email(email):
    """Returns (True, '') or (False, error_message)"""
    if not email:
        return False, 'Email address is required'
    email = email.strip().lower()
    pattern = r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$'
    if not re.match(pattern, email):
        return False, 'Enter a valid email address (e.g. name@gmail.com)'
    domain = email.split('@')[1].lower()
    # Check TLD is real (at least 2 chars, no numbers only)
    tld = domain.split('.')[-1]
    if len(tld) < 2 or tld.isdigit():
        return False, f'"{tld}" is not a valid email extension'
    # Warn on clearly fake domains
    if domain in ('test.com','example.com','fake.com','noemail.com','no@email.com'):
        return False, 'Please use a real email address'
    return True, ''

def validate_password(password):
    """Returns (True, '') or (False, error_message)"""
    if len(password) < 8:
        return False, 'Password must be at least 8 characters'
    if not re.search(r'[A-Z]', password):
        return False, 'Need at least one uppercase letter (A-Z)'
    if not re.search(r'[a-z]', password):
        return False, 'Need at least one lowercase letter (a-z)'
    if not re.search(r'[0-9]', password):
        return False, 'Need at least one number (0-9)'
    if not re.search(r'[^A-Za-z0-9]', password):
        return False, 'Need at least one special character (@#$!%*?&)'
    if password.lower() in ('password','12345678','password1','qwerty123','abc12345'):
        return False, 'Password is too common. Choose a stronger one'
    return True, ''

def validate_username(username):
    """Returns (True, '') or (False, error_message)"""
    if len(username) < 3:
        return False, 'Username must be at least 3 characters'
    if len(username) > 30:
        return False, 'Username must be 30 characters or fewer'
    if not re.match(r'^[a-zA-Z0-9_]+$', username):
        return False, 'Username can only contain letters, numbers, and underscores'
    if username.lower() in BLOCKED_USERNAMES:
        return False, f'"{username}" is a reserved username. Please choose another'
    if username.startswith('_') or username.endswith('_'):
        return False, 'Username cannot start or end with an underscore'
    return True, ''

# ─── AUTH ─────────────────────────────────────────────────────────────────────

def login_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
    error = None
    if request.method == 'POST':
        username = request.POST.get('username','').strip()
        password = request.POST.get('password','')
        if not username or not password:
            error = 'Please enter both username and password'
        else:
            user = authenticate(request, username=username, password=password)
            if user:
                if not user.is_active:
                    error = 'This account has been disabled'
                else:
                    login(request, user)
                    next_url = request.GET.get('next', 'dashboard')
                    return redirect(next_url)
            else:
                error = 'Incorrect username or password. Please try again'
    return render(request, 'vision_app/login.html', {'error': error})


def register_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
    errors = {}
    form_data = {}
    if request.method == 'POST':
        username  = request.POST.get('username','').strip()
        email     = request.POST.get('email','').strip()
        password  = request.POST.get('password','')
        password2 = request.POST.get('password2','')
        form_data = {'username': username, 'email': email}

        # Validate username
        ok, msg = validate_username(username)
        if not ok:
            errors['username'] = msg

        # Validate email (required)
        if not email:
            errors['email'] = 'Email address is required'
        else:
            ok, msg = validate_email(email)
            if not ok:
                errors['email'] = msg

        # Validate password
        ok, msg = validate_password(password)
        if not ok:
            errors['password'] = msg
        elif password != password2:
            errors['password2'] = 'Passwords do not match'

        # Check username taken
        if 'username' not in errors and User.objects.filter(username__iexact=username).exists():
            errors['username'] = f'"{username}" is already taken. Try another'

        # Check email taken
        if 'email' not in errors and email and User.objects.filter(email__iexact=email).exists():
            errors['email'] = 'An account with this email already exists'

        if not errors:
            user = User.objects.create_user(username=username, email=email, password=password)
            UserProfile.objects.create(user=user)
            login(request, user)
            return redirect('dashboard')

    return render(request, 'vision_app/register.html', {'errors': errors, 'form_data': form_data})


def logout_view(request):
    logout(request)
    return redirect('login')


# ─── DASHBOARD ────────────────────────────────────────────────────────────────


# ─── FORGOT PASSWORD ─────────────────────────────────────────────────────────


def forgot_username_view(request):
    sent = False
    error = None
    if request.method == 'POST':
        email = request.POST.get('email', '').strip().lower()
        if not email:
            error = 'Please enter your email address.'
        else:
            try:
                user = User.objects.get(email__iexact=email)
                subject = 'Your VisionRAG Username'
                body = (
                    f"Hi there,\n\n"
                    f"You requested your VisionRAG username.\n\n"
                    f"Your username is: {user.username}\n\n"
                    f"You can sign in at: http://127.0.0.1:8000/login/\n\n"
                    f"If you also forgot your password, use the \'Forgot Password\' "
                    f"option on the login page.\n\n"
                    f"— VisionRAG"
                )
                try:
                    send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [user.email])
                except Exception as e:
                    print(f"[EMAIL] {e}")
                sent = True
            except User.DoesNotExist:
                sent = True  # Don't reveal if email exists
    return render(request, 'vision_app/forgot_username.html', {'sent': sent, 'error': error})

def forgot_password_view(request):
    sent = False
    error = None
    if request.method == 'POST':
        email = request.POST.get('email', '').strip().lower()
        if not email:
            error = 'Please enter your email address.'
        else:
            # Find user by email (case-insensitive)
            try:
                user = User.objects.get(email__iexact=email)
                # Generate token & uid
                uid   = urlsafe_base64_encode(force_bytes(user.pk))
                token = default_token_generator.make_token(user)
                reset_url = request.build_absolute_uri(
                    f'/reset-password/{uid}/{token}/'
                )
                # Build email body
                subject = 'Reset your VisionRAG password'
                body = (
                    f"Hi {user.username},\n\n"
                    f"Someone requested a password reset for your VisionRAG account.\n\n"
                    f"Click the link below to reset your password (valid for 1 hour):\n"
                    f"{reset_url}\n\n"
                    f"If you did not request this, you can safely ignore this email.\n\n"
                    f"— VisionRAG"
                )
                try:
                    send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [user.email])
                    sent = True
                except Exception as e:
                    # During dev with console backend, still mark as sent
                    print(f"[EMAIL] {e}")
                    sent = True
            except User.DoesNotExist:
                # Don't reveal whether email exists — always show success
                sent = True
    return render(request, 'vision_app/forgot_password.html', {'sent': sent, 'error': error})


def reset_password_view(request, uidb64, token):
    error = None
    user  = None
    try:
        uid  = force_str(urlsafe_base64_decode(uidb64))
        user = User.objects.get(pk=uid)
    except (TypeError, ValueError, OverflowError, User.DoesNotExist):
        user = None

    # Validate token
    valid = user is not None and default_token_generator.check_token(user, token)
    if not valid:
        return render(request, 'vision_app/reset_password.html', {
            'invalid': True, 'error': 'This reset link is invalid or has expired.'
        })

    if request.method == 'POST':
        pw1 = request.POST.get('password', '')
        pw2 = request.POST.get('password2', '')
        ok, msg = validate_password(pw1)
        if not ok:
            error = msg
        elif pw1 != pw2:
            error = 'Passwords do not match.'
        else:
            user.set_password(pw1)
            user.save()
            return redirect('reset_password_done')

    return render(request, 'vision_app/reset_password.html', {
        'invalid': False, 'error': error, 'uidb64': uidb64, 'token': token
    })


def reset_password_done(request):
    return render(request, 'vision_app/reset_password_done.html')


def _get_or_create_profile(user):
    profile, _ = UserProfile.objects.get_or_create(user=user)
    return profile


@login_required
def dashboard(request):
    sessions   = ChatSession.objects.filter(user=request.user).order_by('-updated_at')[:20]
    profile    = _get_or_create_profile(request.user)
    total_msgs = ChatMessage.objects.filter(session__user=request.user).count()
    return render(request, 'vision_app/dashboard.html', {
        'sessions': sessions,
        'profile': profile,
        'total_msgs': total_msgs,
    })


# ─── PROFILE ──────────────────────────────────────────────────────────────────

@login_required
def profile_view(request):
    profile = _get_or_create_profile(request.user)
    errors  = {}
    success = None

    if request.method == 'POST':
        bio      = request.POST.get('bio', '').strip()
        color    = request.POST.get('avatar_color', '#7c6af7').strip()
        fname    = request.POST.get('first_name', '').strip()
        lname    = request.POST.get('last_name', '').strip()
        email    = request.POST.get('email', '').strip()
        new_pw   = request.POST.get('new_password', '').strip()
        conf_pw  = request.POST.get('confirm_password', '').strip()

        # Validate email (required)
        if not email:
            errors['email'] = 'Email address is required'
        else:
            ok, msg = validate_email(email)
            if not ok:
                errors['email'] = msg
            elif email != request.user.email:
                if User.objects.filter(email__iexact=email).exclude(pk=request.user.pk).exists():
                    errors['email'] = 'This email is already used by another account'

        # Validate password change if provided
        if new_pw:
            ok, msg = validate_password(new_pw)
            if not ok:
                errors['new_password'] = msg
            elif new_pw != conf_pw:
                errors['confirm_password'] = 'Passwords do not match'

        # Validate name fields
        if fname and not re.match(r'^[a-zA-Z\s\-\.\']{1,50}$', fname):
            errors['first_name'] = 'First name contains invalid characters'
        if lname and not re.match(r'^[a-zA-Z\s\-\.\']{1,50}$', lname):
            errors['last_name'] = 'Last name contains invalid characters'

        if not errors:
            profile.bio = bio
            # Validate avatar color format
            if re.match(r'^#[0-9a-fA-F]{6}$', color):
                profile.avatar_color = color
            profile.save()
            request.user.first_name = fname
            request.user.last_name  = lname
            request.user.email      = email
            request.user.save()
            # Apply password change if provided
            if new_pw:
                request.user.set_password(new_pw)
                request.user.save()
                update_session_auth_hash(request, request.user)  # keep user logged in
            success = 'Profile updated successfully!'

    sessions = ChatSession.objects.filter(user=request.user)
    total_q  = ChatMessage.objects.filter(session__user=request.user, role='user').count()
    return render(request, 'vision_app/profile.html', {
        'profile': profile,
        'total_sessions': sessions.count(),
        'total_queries': total_q,
        'recent_sessions': sessions[:5],
        'errors': errors,
        'success': success,
    })


# ─── CHAT APP ─────────────────────────────────────────────────────────────────

@login_required
def index(request):
    session_id = request.GET.get('session') or str(uuid.uuid4())
    db_session = None
    try:
        db_session = ChatSession.objects.get(session_id=session_id, user=request.user)
    except ChatSession.DoesNotExist:
        pass
    return render(request, 'vision_app/index.html', {
        'session_id': session_id,
        'db_session': db_session,
    })


@login_required
def history_view(request):
    sessions = ChatSession.objects.filter(user=request.user).order_by('-updated_at')
    return render(request, 'vision_app/history.html', {'sessions': sessions})


@login_required
def session_detail(request, session_id):
    session  = get_object_or_404(ChatSession, session_id=session_id, user=request.user)
    messages = session.messages.all()
    return render(request, 'vision_app/session_detail.html', {
        'session': session, 'messages': messages,
    })


@login_required
def duplicate_session(request, session_id):
    """Create a new chat session by duplicating an existing one, including messages and media."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    orig = get_object_or_404(ChatSession, session_id=session_id, user=request.user)

    new_sid = str(uuid.uuid4())
    new_session = ChatSession.objects.create(
        user=request.user,
        session_id=new_sid,
        title=f"{orig.title} (copy)",
        pdf_names=orig.pdf_names,
        image_labels=orig.image_labels,
        image_data=orig.image_data,
        message_count=orig.message_count,
    )

    # Duplicate messages
    for m in orig.messages.all():
        ChatMessage.objects.create(session=new_session, role=m.role, content=m.content)

    # Copy media folders (PDFs + uploads) if they exist
    for sub in ('pdfs', 'uploads'):
        src = os.path.join(settings.MEDIA_ROOT, sub, session_id)
        dst = os.path.join(settings.MEDIA_ROOT, sub, new_sid)
        if os.path.isdir(src) and not os.path.isdir(dst):
            try:
                shutil.copytree(src, dst)
            except Exception as e:
                print(f"[DUPLICATE] Failed to copy {sub} for {session_id}: {e}")

    return JsonResponse({'success': True, 'session_id': new_sid, 'url': f'/app/?session={new_sid}'})


@login_required
def clear_session(request, session_id):
    """Clear all messages in a chat session but keep PDFs and images."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    sess = get_object_or_404(ChatSession, session_id=session_id, user=request.user)
    deleted = sess.messages.count()
    sess.messages.all().delete()
    sess.message_count = 0
    # Reset title to default if desired
    sess.title = f'Session {sess.session_id[:8]}'
    sess.save()
    return JsonResponse({'success': True, 'deleted': deleted})


@csrf_exempt
@login_required
def delete_session(request, session_id):
    if request.method == 'POST':
        ChatSession.objects.filter(session_id=session_id, user=request.user).delete()
        return JsonResponse({'success': True})
    return JsonResponse({'error': 'POST required'}, status=405)


@login_required
def get_session_messages(request, session_id):
    try:
        sess = ChatSession.objects.get(session_id=session_id, user=request.user)
        msgs = [{'role': m.role, 'content': m.content, 'time': m.created_at.isoformat()}
                for m in sess.messages.all()]
        return JsonResponse({'messages': msgs, 'title': sess.title,
                             'pdfs': sess.get_pdfs(), 'images': sess.get_images(),
                             'image_data': sess.get_image_data()})
    except ChatSession.DoesNotExist:
        return JsonResponse({'messages': [], 'title': '', 'pdfs': [], 'images': [], 'image_data': []})


# ─── API ──────────────────────────────────────────────────────────────────────

def ping(request):
    return JsonResponse({'status': 'ok'})


def about_view(request):
    return render(request, 'vision_app/about.html')


def help_view(request):
    faqs = [
        ("Why does VisionRAG say 'Please upload a hardware image first'?",
         "VisionRAG requires at least one image to provide hardware context for answers. Upload an image of your component using the image upload button in the chat, then ask your question."),
        ("Can I use VisionRAG without a PDF?",
         "Yes! If no PDF is uploaded, VisionRAG answers purely from the image description. For detailed technical specs, uploading the component's datasheet PDF gives much better results."),
        ("Why is my answer sometimes slow?",
         "Response speed depends on the active LLM provider. Groq is fastest (~500 tokens/second). If Groq is rate-limited, the system falls back to Together AI, then OpenRouter, then local Ollama. Check the Provider Status page to see which is active."),
        ("My PDF uploaded but the answers don't seem to use it?",
         "ChromaDB vector indexing happens in the background after upload. For the first 30-60 seconds after uploading a large PDF, answers fall back to keyword-only search, which is still good but less precise. Wait a moment and retry."),
        ("Are my uploaded files and chats private?",
         "Yes. Files are stored per-session under your account and are not shared with other users. Sessions are tied to your login. Deleting a session removes its chat history from the database."),
        ("Can I upload multiple PDFs in one session?",
         "Absolutely. You can upload as many PDFs as needed. Retrieval runs across all of them simultaneously, and each result is tagged with its source filename."),
        ("What hardware components can VisionRAG identify?",
         "GPU, CPU, RAM, motherboard (ATX/ITX), SSD, HDD, power supply, CPU cooler, PC case, monitor, keyboard, mouse, and more. The Gemini Vision model distinguishes between similar components (e.g. SSD vs HDD, ATX vs ITX)."),
        ("How do I add my own Groq / Together / OpenRouter API key?",
         "Open settings.py and set GROQ_API_KEYS, TOGETHER_API_KEYS, or OPENROUTER_API_KEYS to your key(s). Separate multiple keys with commas. More keys = higher daily request capacity."),
    ]
    return render(request, 'vision_app/help.html', {'faqs': faqs})


def provider_status_view(request):
    from .llm_router import get_provider_status
    # The JS on the page fetches this same URL to get JSON
    if request.headers.get('Accept', '').startswith('application/json') or request.GET.get('json'):
        return JsonResponse({'providers': get_provider_status()})
    return render(request, 'vision_app/provider_status.html')


@csrf_exempt
@login_required
def upload_pdf(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    try:
        if 'pdf' not in request.FILES:
            return JsonResponse({'error': 'No pdf file in request'}, status=400)
        pdf_file   = request.FILES['pdf']
        session_id = request.POST.get('session_id','').strip() or str(uuid.uuid4())
        pdf_dir    = os.path.join(settings.MEDIA_ROOT, 'pdfs', session_id)
        os.makedirs(pdf_dir, exist_ok=True)
        safe_name = re.sub(r'[^a-zA-Z0-9._-]', '_', pdf_file.name)
        if not safe_name.lower().endswith('.pdf'):
            safe_name += '.pdf'
        pdf_path = os.path.join(pdf_dir, safe_name)
        with open(pdf_path, 'wb') as f:
            for chunk in pdf_file.chunks():
                f.write(chunk)
        from .rag_engine import register_pdf, _clean_stem, get_pdfs
        register_pdf(session_id, safe_name, pdf_path)
        pdfs       = get_pdfs(session_id)
        char_count = next((len(p['text']) for p in pdfs if p['stem'] == _clean_stem(safe_name)), os.path.getsize(pdf_path))
        if request.user.is_authenticated:
            db_sess, _ = ChatSession.objects.get_or_create(
                session_id=session_id,
                defaults={'user': request.user, 'title': f'Session {session_id[:8]}'}
            )
            pdf_list = db_sess.get_pdfs()
            if safe_name not in pdf_list:
                pdf_list.append(safe_name)
            db_sess.pdf_names = json.dumps(pdf_list)
            db_sess.save()
        ollama_url = getattr(settings, 'OLLAMA_BASE_URL', 'http://localhost:11434')
        def _bg():
            try:
                from .rag_engine import run_ingestion_pipeline
                run_ingestion_pipeline(pdf_path, session_id, settings.MEDIA_ROOT, ollama_url)
            except Exception as e:
                print(f"[BG] {e}")
        threading.Thread(target=_bg, daemon=True).start()
        return JsonResponse({'success': True, 'session_id': session_id,
                             'filename': pdf_file.name, 'char_count': char_count,
                             'pdf_url': f"/media/pdfs/{session_id}/{safe_name}"})
    except Exception as e:
        print(traceback.format_exc())
        return JsonResponse({'error': str(e)}, status=500)


@csrf_exempt
@login_required
def upload_image(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    try:
        if 'image' not in request.FILES:
            return JsonResponse({'error': 'No image provided'}, status=400)
        image_file = request.FILES['image']
        session_id = request.POST.get('session_id','').strip() or str(uuid.uuid4())
        from .image_utils import is_blurred, sharpen_image, analyze_image
        session_dir = os.path.join(settings.MEDIA_ROOT, 'uploads', session_id)
        os.makedirs(session_dir, exist_ok=True)
        ext = Path(image_file.name).suffix.lower()
        if ext not in ['.jpg','.jpeg','.png','.webp','.gif','.bmp','.tiff','.tif']:
            ext = '.jpg'
        img_id    = str(uuid.uuid4())[:8]
        orig_path = os.path.join(session_dir, f'img_{img_id}{ext}')
        with open(orig_path, 'wb') as f:
            for chunk in image_file.chunks():
                f.write(chunk)
        try:    blurred, blur_score = is_blurred(orig_path)
        except: blurred, blur_score = False, 0.0
        proc_path     = orig_path
        was_sharpened = False
        if blurred:
            try:
                sp = os.path.join(session_dir, f'img_{img_id}_sharp{ext}')
                proc_path = sharpen_image(orig_path, sp)
                was_sharpened = True
            except: pass
        ollama_url = getattr(settings, 'OLLAMA_BASE_URL', 'http://localhost:11434')
        result = analyze_image(proc_path, ollama_url)
        rel    = os.path.relpath(proc_path, settings.MEDIA_ROOT).replace('\\','/')
        label  = str(result.get('label','unknown'))
        if request.user.is_authenticated and session_id:
            try:
                db_sess = ChatSession.objects.get(session_id=session_id, user=request.user)
                # Update legacy label list
                imgs = db_sess.get_images()
                if label not in imgs:
                    imgs.append(label)
                db_sess.image_labels = json.dumps(imgs)
                # Update rich image data list (label + description + url)
                img_data_list = db_sess.get_image_data()
                img_url = f'/media/{rel}'
                # Replace or append entry for this image
                img_data_list.append({
                    'label': label,
                    'description': str(result.get('description', '')),
                    'url': img_url
                })
                db_sess.image_data = json.dumps(img_data_list)
                db_sess.save()
            except ChatSession.DoesNotExist:
                pass
        return JsonResponse({'session_id': session_id, 'image_id': img_id,
                             'label': label, 'description': str(result.get('description','')),
                             'was_blurred': bool(blurred), 'blur_score': round(float(blur_score),2),
                             'was_sharpened': bool(was_sharpened), 'processed_url': f'/media/{rel}'})
    except Exception as e:
        print(traceback.format_exc())
        return JsonResponse({'error': str(e)}, status=500)


@csrf_exempt
@login_required
def chat_stream(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    try:    data = json.loads(request.body)
    except: return JsonResponse({'error': 'Invalid JSON'}, status=400)
    query        = data.get('query','').strip()
    session_id   = data.get('session_id','')
    image_descs  = data.get('image_descriptions',[])
    chat_history = data.get('history',[])
    if not query:
        return JsonResponse({'error': 'Empty query'}, status=400)
    from .rag_engine import stream_answer
    ollama_url = getattr(settings, 'OLLAMA_BASE_URL', 'http://localhost:11434')
    def _save_msg(role, content):
        if request.user.is_authenticated and session_id:
            try:
                db_sess, _ = ChatSession.objects.get_or_create(
                    session_id=session_id,
                    defaults={'user': request.user, 'title': f'Session {session_id[:8]}'}
                )
                ChatMessage.objects.create(session=db_sess, role=role, content=content)
                db_sess.message_count = db_sess.messages.count()
                if role == 'user' and db_sess.message_count == 1:
                    db_sess.title = content[:60] + ('...' if len(content) > 60 else '')
                db_sess.save()
            except Exception:
                pass
    _save_msg('user', query)
    def event_stream():
        full = []
        try:
            for token in stream_answer(query=query, image_descriptions=image_descs,
                                       session_id=session_id, chat_history=chat_history,
                                       ollama_url=ollama_url):
                full.append(token)
                yield f"data: {json.dumps({'token': token})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'token': f'⚠️ {e}'})}\n\n"
        yield "data: [DONE]\n\n"
        _save_msg('assistant', ''.join(full))
    resp = StreamingHttpResponse(event_stream(), content_type='text/event-stream')
    resp['Cache-Control'] = 'no-cache'
    resp['X-Accel-Buffering'] = 'no'
    return resp


@login_required
def download_pdf(request, session_id, filename):
    """Serve a session PDF as a download."""
    import mimetypes
    from django.http import FileResponse, Http404
    # Security: session must belong to this user
    get_object_or_404(ChatSession, session_id=session_id, user=request.user)
    safe_name = re.sub(r'[^a-zA-Z0-9._-]', '_', filename)
    if not safe_name.lower().endswith('.pdf'):
        raise Http404
    pdf_path = os.path.join(settings.MEDIA_ROOT, 'pdfs', session_id, safe_name)
    if not os.path.exists(pdf_path):
        raise Http404
    response = FileResponse(open(pdf_path, 'rb'), content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{safe_name}"'
    return response
