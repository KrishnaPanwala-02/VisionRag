"""
Run this ONCE after extracting the zip:
  python setup.py

It will:
1. Run all Django migrations (creates the SQLite DB with all tables)
2. Optionally create a superuser
"""
import os, sys
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'vision_rag.settings')

import django
django.setup()

from django.core.management import call_command

print("Running migrations...")
call_command('migrate', '--run-syncdb')
print("\n✅ Database ready!")
print("\nCreate your first account at: http://localhost:8000/register/")
print("Then start the server:  python manage.py runserver")
