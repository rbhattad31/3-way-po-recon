import os, django
os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings"
django.setup()

from apps.agents.models import AgentRun, AgentStep, AgentMessage
from apps.tools.models import ToolCall

runs = AgentRun.objects.filter(agent_type="SUPERVISOR").order_by("-created_at")[:2]
r = runs[0]
print(f"Latest supervisor run: id={r.id} status={r.status} created={r.created_at}")
print(f"Reasoning: {(r.summarized_reasoning or '')[:500]}")
print()

# Check tool calls
tcs = ToolCall.objects.filter(agent_run=r).order_by("created_at")
print(f"Tool calls ({tcs.count()}):")
for t in tcs[:30]:
    inp = (str(t.input_payload or ""))[:200]
    out = (str(t.output_payload or ""))[:300]
    err = (str(t.error_message or ""))[:200]
    print(f"  tool={t.tool_name} status={t.status} duration={t.duration_ms}ms")
    if inp:
        print(f"    input: {inp}")
    if err and err != "None":
        print(f"    ERROR: {err}")
    elif out:
        print(f"    output: {out}")
    print()