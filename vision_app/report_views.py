"""
Auto Report Generator Module
Takes a chat session and generates a professional PDF inspection report.
Uses ReportLab for PDF generation (pure Python, no external service).
"""
import os
import json
import io
from datetime import datetime
from django.shortcuts import render, get_object_or_404
from django.http import HttpResponse, JsonResponse
from django.contrib.auth.decorators import login_required
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
from .models import ChatSession


@login_required
def report_view(request):
    """Show list of sessions to generate reports from."""
    sessions = ChatSession.objects.filter(user=request.user).order_by('-updated_at')[:30]
    return render(request, 'vision_app/report.html', {'sessions': sessions})


@login_required
def report_generate(request, session_id):
    """Generate and download a PDF report for a session."""
    session = get_object_or_404(ChatSession, session_id=session_id, user=request.user)
    messages = list(session.messages.all().order_by('created_at'))
    images   = session.get_image_data()
    pdfs     = session.get_pdfs()

    # Component name from first image label
    component = images[0].get('label', 'Hardware Component') if images else 'Hardware Component'

    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.lib import colors
        from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer,
                                         Table, TableStyle, HRFlowable, Image as RLImage)
        from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT

        buf = io.BytesIO()
        doc = SimpleDocTemplate(
            buf, pagesize=A4,
            leftMargin=20*mm, rightMargin=20*mm,
            topMargin=20*mm, bottomMargin=20*mm
        )

        styles = getSampleStyleSheet()
        W = A4[0] - 40*mm  # usable width

        # ── Custom Styles ──────────────────────────────────────────────────
        title_style = ParagraphStyle('Title', parent=styles['Normal'],
            fontSize=22, fontName='Helvetica-Bold', textColor=colors.HexColor('#1a1d2e'),
            spaceAfter=4)
        sub_style = ParagraphStyle('Sub', parent=styles['Normal'],
            fontSize=10, textColor=colors.HexColor('#888'), spaceAfter=2)
        section_style = ParagraphStyle('Section', parent=styles['Normal'],
            fontSize=13, fontName='Helvetica-Bold', textColor=colors.HexColor('#4285f4'),
            spaceBefore=14, spaceAfter=6)
        body_style = ParagraphStyle('Body', parent=styles['Normal'],
            fontSize=10, leading=16, textColor=colors.HexColor('#2d2d2d'), spaceAfter=4)
        user_style = ParagraphStyle('User', parent=styles['Normal'],
            fontSize=10, leading=15, textColor=colors.HexColor('#4285f4'),
            fontName='Helvetica-Bold', spaceAfter=2)
        bot_style = ParagraphStyle('Bot', parent=styles['Normal'],
            fontSize=10, leading=15, textColor=colors.HexColor('#2d2d2d'),
            spaceAfter=8, leftIndent=10)

        story = []
        now = datetime.now().strftime('%B %d, %Y at %H:%M')

        # ── Header (two-column: text + optional image) ──────────────────────
        # Prepare optional image for header (fixed size)
        rl_img = None
        if images:
            img_data = images[0]
            img_url  = img_data.get('url', '')
            if img_url:
                img_path = os.path.join(settings.MEDIA_ROOT, img_url.lstrip('/media/'))
                if os.path.exists(img_path):
                    try:
                        rl_img = RLImage(img_path, width=60*mm, height=60*mm)
                        rl_img.hAlign = 'RIGHT'
                    except Exception:
                        rl_img = None

        # Build left column HTML so we can control spacing and colors in one Paragraph
        left_html = (
            f"<font name='Helvetica-Bold' size='22' color='#1a1d2e'>🔮 VisionRAG</font><br/><br/>"
            f"<font name='Helvetica-Bold' size='18' color='#1a1d2e'>Hardware Inspection Report</font><br/>"
            f"<font name='Helvetica-Bold' size='13' color='#34a853'>Component: {component}</font><br/>"
            f"<font size='10' color='#888'>Generated: {now}  ·  Session: {session_id[:16]}...</font>"
        )

        header_table = Table([[Paragraph(left_html, styles['Normal']), rl_img or '']], colWidths=[W - 65*mm, 60*mm])
        header_table.setStyle(TableStyle([
            ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
            ('LEFTPADDING',  (0,0), (-1,-1), 0),
            ('RIGHTPADDING', (0,0), (-1,-1), 0),
            ('TOPPADDING',   (0,0), (-1,-1), 0),
            ('BOTTOMPADDING',(0,0), (-1,-1), 0),
        ]))
        story.append(header_table)
        story.append(Spacer(1, 4*mm))
        story.append(HRFlowable(width=W, thickness=2, color=colors.HexColor('#4285f4'),
                                 spaceAfter=12))

        # ── Session Info Table ─────────────────────────────────────────────
        story.append(Paragraph('Session Overview', section_style))
        info_data = [
            ['Field', 'Value'],
            ['Component', component],
            ['Session ID', session_id[:24] + '...'],
            ['Created', session.created_at.strftime('%Y-%m-%d %H:%M')],
            ['Total Messages', str(len(messages))],
            ['PDFs Used', ', '.join(pdfs) if pdfs else 'None'],
        ]
        info_table = Table(info_data, colWidths=[45*mm, W - 45*mm])
        info_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#4285f4')),
            ('TEXTCOLOR',  (0, 0), (-1, 0), colors.white),
            ('FONTNAME',   (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE',   (0, 0), (-1, -1), 9),
            ('BACKGROUND', (0, 1), (0, -1), colors.HexColor('#f8f9ff')),
            ('FONTNAME',   (0, 1), (0, -1), 'Helvetica-Bold'),
            ('TEXTCOLOR',  (0, 1), (0, -1), colors.HexColor('#1a1d2e')),
            ('GRID',       (0, 0), (-1, -1), 0.5, colors.HexColor('#e0e0e0')),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8f9ff')]),
            ('LEFTPADDING',  (0, 0), (-1, -1), 8),
            ('RIGHTPADDING', (0, 0), (-1, -1), 8),
            ('TOPPADDING',   (0, 0), (-1, -1), 6),
            ('BOTTOMPADDING',(0, 0), (-1, -1), 6),
            ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
            ('ALIGN', (0,0), (0,-1), 'LEFT'),
            ('ALIGN', (1,0), (1,-1), 'LEFT'),
        ]))
        story.append(info_table)
        story.append(Spacer(1, 6*mm))

        # ── Chat Transcript ────────────────────────────────────────────────
        story.append(HRFlowable(width=W, thickness=1, color=colors.HexColor('#e0e0e0'), spaceAfter=6))
        story.append(Paragraph('Chat Transcript', section_style))

        for msg in messages:
            if msg.role == 'user':
                story.append(Paragraph(f'Q: {msg.content}', user_style))
            else:
                # Clean up long bot responses
                content = msg.content[:1200] + ('...' if len(msg.content) > 1200 else '')
                story.append(Paragraph(f'A: {content}', bot_style))

        # ── Footer ─────────────────────────────────────────────────────────
        story.append(Spacer(1, 8*mm))
        story.append(HRFlowable(width=W, thickness=1, color=colors.HexColor('#e0e0e0'), spaceAfter=4))
        story.append(Paragraph(
            f'Generated by VisionRAG  ·  {now}  ·  Confidential',
            ParagraphStyle('Footer', parent=styles['Normal'],
                fontSize=8, textColor=colors.HexColor('#aaa'), alignment=TA_CENTER)
        ))

        doc.build(story)
        pdf_bytes = buf.getvalue()

        filename = f"VisionRAG_Report_{component.replace(' ','_')}_{datetime.now().strftime('%Y%m%d')}.pdf"
        response = HttpResponse(pdf_bytes, content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response

    except ImportError:
        return JsonResponse({'error': 'ReportLab not installed. Run: pip install reportlab'}, status=500)
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return JsonResponse({'error': str(e)}, status=500)
