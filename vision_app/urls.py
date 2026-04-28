from django.urls import path
from . import views
from .analyzer_views import analyzer_view, analyzer_run
from .visual_diff_views import visual_diff_view, visual_diff_run
from .voice_bot_views import voice_bot_view, voice_bot_ask
from .report_views import report_view, report_generate
from .health_score_views import health_score_view, health_score_run
from .analytics_views import analytics_view, analytics_data

urlpatterns = [
    # ── Auth ──────────────────────────────────────────────────────────────
    path('login/',    views.login_view,    name='login'),
    path('register/', views.register_view, name='register'),
    path('logout/',   views.logout_view,   name='logout'),

    # ── Forgot / Reset Password ───────────────────────────────────────────
    path('forgot-password/',                          views.forgot_password_view,  name='forgot_password'),
    path('forgot-username/',                           views.forgot_username_view,  name='forgot_username'),
    path('reset-password/<uidb64>/<token>/',           views.reset_password_view,   name='reset_password'),
    path('reset-password/done/',                       views.reset_password_done,   name='reset_password_done'),

    # ── Main pages ────────────────────────────────────────────────────────
    path('',          views.dashboard,     name='dashboard'),
    path('app/',      views.index,         name='index'),
    path('history/',  views.history_view,  name='history'),
    path('profile/',  views.profile_view,  name='profile'),
    path('session/<str:session_id>/',            views.session_detail,       name='session_detail'),
    path('session/<str:session_id>/delete/',     views.delete_session,       name='delete_session'),
    path('session/<str:session_id>/messages/',   views.get_session_messages, name='session_messages'),
    path('session/<str:session_id>/duplicate/',  views.duplicate_session,    name='duplicate_session'),
    path('session/<str:session_id>/clear/',      views.clear_session,        name='clear_session'),
    path('session/<str:session_id>/download/<str:filename>/', views.download_pdf, name='download_pdf'),

    # ── API ───────────────────────────────────────────────────────────────
    path('ping/',            views.ping,                  name='ping'),
    path('about/',           views.about_view,            name='about'),
    path('help/',            views.help_view,             name='help'),
    path('upload-pdf/',      views.upload_pdf,            name='upload_pdf'),
    path('upload-image/',    views.upload_image,          name='upload_image'),
    path('chat/',            views.chat_stream,           name='chat'),

    # ── Smart Analyzer ────────────────────────────────────────────────────
    path('analyzer/',            analyzer_view,               name='analyzer'),
    path('analyzer/run/',        analyzer_run,                name='analyzer_run'),

    # ── Visual Diff ───────────────────────────────────────────────────────
    path('visual-diff/',         visual_diff_view,            name='visual_diff'),
    path('visual-diff/run/',     visual_diff_run,             name='visual_diff_run'),

    # ── Voice Bot ─────────────────────────────────────────────────────────
    path('voice-bot/',           voice_bot_view,              name='voice_bot'),
    path('voice-bot/ask/',       voice_bot_ask,               name='voice_bot_ask'),

    # ── Auto Report Generator ─────────────────────────────────────────────
    path('report/',                              report_view,       name='report'),
    path('report/<str:session_id>/download/',    report_generate,   name='report_generate'),

    # ── Hardware Health Score ─────────────────────────────────────────────
    path('health-score/',      health_score_view,   name='health_score'),
    path('health-score/run/',  health_score_run,    name='health_score_run'),

    # ── Usage Analytics ───────────────────────────────────────────────────
    path('analytics/',         analytics_view,      name='analytics'),
    path('analytics/data/',    analytics_data,      name='analytics_data'),
]


