from apps.benchmarking.services.benchmark_service import BenchmarkEngine
from apps.accounts.models import User

user = User.objects.filter(is_active=True).order_by("id").first()
tenant = getattr(user, "company", None)
print("Running BenchmarkEngine.run() on pk=7 ...")
result = BenchmarkEngine.run(request_pk=7, user=user, tenant=tenant)
if result.get("success"):
    bm = result.get("benchmark_result")
    pk_val = getattr(bm, "pk", "N/A")
    variance = result.get("overall_variance_pct")
    print(f"SUCCESS  BenchmarkResult pk={pk_val}  variance={variance}%")
else:
    print(f"Error: {result.get('error')}")
print("Visit: http://127.0.0.1:8000/benchmarking/7/")
print("Agent runs: http://127.0.0.1:8000/agents/runs/")
