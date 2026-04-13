"""Quick test: verify copilot page loads with rich payload data."""
import os
import sys
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
django.setup()

from django.test import RequestFactory, Client
from django.contrib.sessions.backends.db import SessionStore
from apps.accounts.models import User
from django.conf import settings

# Allow testserver
if "testserver" not in settings.ALLOWED_HOSTS and "*" not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append("testserver")

# Get or create test user
user = User.objects.filter(is_staff=True).first()
if not user:
    print("No staff user found")
    sys.exit(1)

print("Testing as user:", user.email)

client = Client()
client.force_login(user)

# Test hub page (no messages)
resp = client.get("/copilot/cases/")
print("Hub page status:", resp.status_code)
if resp.status_code == 200:
    content = resp.content.decode()
    if "sv-summary-card" in content:
        print("  WARN: sv-summary-card still in page")
    else:
        print("  OK: sv-summary-card removed")
    if "copilot-welcome" in content:
        print("  OK: Welcome state shown (no messages)")

# Find a session with messages
from apps.copilot.models import CopilotSession, CopilotMessage
session_with_msgs = (
    CopilotSession.objects
    .filter(user=user)
    .order_by("-last_message_at")
    .first()
)
if session_with_msgs:
    print("\nTesting session:", session_with_msgs.id)
    msg_count = CopilotMessage.objects.filter(session=session_with_msgs).count()
    print("  Messages:", msg_count)
    
    resp2 = client.get("/copilot/session/%s/" % session_with_msgs.id)
    print("  Session page status:", resp2.status_code)
    if resp2.status_code == 200:
        content2 = resp2.content.decode()
        payload_count = content2.count("rich-payload-data")
        print("  rich-payload-data tags:", payload_count)
        if "sv-summary-card" in content2:
            print("  WARN: sv-summary-card still in session page")
        else:
            print("  OK: sv-summary-card removed from session page")
        
        # Check a sample payload
        if payload_count > 0:
            import re
            match = re.search(
                r'<script type="application/json" class="rich-payload-data">(.*?)</script>',
                content2,
                re.DOTALL,
            )
            if match:
                import json
                try:
                    data = json.loads(match.group(1))
                    print("  Payload keys:", list(data.keys()))
                    if "summary" in data:
                        print("  Summary preview:", data["summary"][:80])
                    if "evidence" in data:
                        print("  Evidence items:", len(data["evidence"]))
                    print("  OK: Payload is valid JSON")
                except json.JSONDecodeError as e:
                    print("  ERROR: Invalid JSON in payload:", e)
else:
    print("\nNo sessions found for this user")

print("\nDone.")
