from django.db import models
from django.contrib.auth.models import User
import json

class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    avatar_color = models.CharField(max_length=7, default='#7c6af7')
    bio = models.TextField(blank=True, default='')
    total_sessions = models.IntegerField(default=0)
    total_queries = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user.username}'s profile"

class ChatSession(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='sessions')
    session_id = models.CharField(max_length=100, unique=True)
    title = models.CharField(max_length=200, default='New Session')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    pdf_names = models.TextField(default='[]')  # JSON list
    image_labels = models.TextField(default='[]')  # JSON list of label strings (legacy)
    image_data   = models.TextField(default='[]')  # JSON list of {label, description, url}
    message_count = models.IntegerField(default=0)

    def get_pdfs(self):
        try: return json.loads(self.pdf_names)
        except: return []

    def get_images(self):
        try: return json.loads(self.image_labels)
        except: return []

    def get_image_data(self):
        try:
            data = json.loads(self.image_data)
            if data: return data
        except:
            pass
        return [{'label': l, 'description': '', 'url': ''} for l in self.get_images()]

    def __str__(self):
        return f"{self.user.username} — {self.title}"

    class Meta:
        ordering = ['-updated_at']

class ChatMessage(models.Model):
    ROLES = [('user', 'User'), ('assistant', 'Assistant')]
    session = models.ForeignKey(ChatSession, on_delete=models.CASCADE, related_name='messages')
    role = models.CharField(max_length=20, choices=ROLES)
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return f"[{self.role}] {self.content[:50]}"

# ── Caching models: store analyzer and visual-diff results keyed by input hashes
class AnalyzerCache(models.Model):
    pdf_hash = models.CharField(max_length=128, db_index=True)
    image_hash = models.CharField(max_length=128, db_index=True)
    component = models.CharField(max_length=200, blank=True)
    description = models.TextField(blank=True)
    result_json = models.TextField(blank=True)  # JSON: {step_id: text, ...}
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = (('pdf_hash', 'image_hash'),)

    def get_result(self):
        try:
            return json.loads(self.result_json) if self.result_json else {}
        except Exception:
            return {}

class VisualDiffCache(models.Model):
    img1_hash = models.CharField(max_length=128, db_index=True)
    img2_hash = models.CharField(max_length=128, db_index=True)
    result_json = models.TextField(blank=True)  # JSON with keys: component, summary, differences, condition, recommendation
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        # allow match regardless of order
        indexes = [models.Index(fields=['img1_hash', 'img2_hash'])]

    def get_result(self):
        try:
            return json.loads(self.result_json) if self.result_json else {}
        except Exception:
            return {}
