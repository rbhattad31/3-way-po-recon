#!/usr/bin/env python
"""
run_e2e.py -- Master E2E Test Runner
=====================================
Run all e2e_tests/, capture results, and generate COMPLIANCE_REPORT.md.

Usage:
    python run_e2e.py                      # run all tests
    python run_e2e.py --module test_03     # single module
    python run_e2e.py --quickly            # skip slow import tests
    python run_e2e.py --no-report          # skip report generation

Output:
    e2e_report.json                        # raw pytest JSON
    COMPLIANCE_REPORT.md                   # formatted pass/fail report
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent

MODULES = [
    ("01 -- Health & Auth",            "test_01_health_and_auth.py"),
    ("02 -- Dashboard",                "test_02_dashboard.py"),
    ("03 -- LLM Agents",               "test_03_agents_llm.py"),
    ("04 -- System Agents (Determin)", "test_04_agents_system.py"),
    ("05 -- Extraction Pipeline",      "test_05_extraction.py"),
    ("06 -- Reconciliation",           "test_06_reconciliation.py"),
    ("07 -- Posting + ERP",            "test_07_posting_erp.py"),
    ("08 -- Procurement",              "test_08_procurement.py"),
    ("09 -- Email Integration",        "test_09_email_integration.py"),
    ("10 -- Cases & Reviews",          "test_10_cases_reviews.py"),
    ("11 -- RBAC & Audit",             "test_11_rbac_audit.py"),
    ("12 -- Eval & Learning",          "test_12_eval_learning.py"),
    ("13 -- Vendors & Reports",        "test_13_vendors_reports.py"),
]

STATUS_ICONS = {
    "passed":  "PASS",
    "failed":  "FAIL",
    "skipped": "SKIP",
    "error":   "ERR ",
}


def run_pytest(test_path: str, json_out: str) -> int:
    """Run pytest on given path; return exit code."""
    cmd = [
        sys.executable, "-m", "pytest",
        test_path,
        "-v",
        "--tb=short",
        f"--json-report",
        f"--json-report-file={json_out}",
        "--no-header",
        "-q",
    ]
    result = subprocess.run(cmd, cwd=str(ROOT.parent))
    return result.returncode


def load_json(path: str) -> dict:
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def collect_results(report: dict) -> tuple[int, int, int, int, list]:
    passed = failed = skipped = errs = 0
    failures = []
    for test in report.get("tests", []):
        outcome = test.get("outcome", "error")
        if outcome == "passed":
            passed += 1
        elif outcome == "failed":
            failed += 1
            failures.append({
                "id": test.get("nodeid", "?"),
                "message": test.get("call", {}).get("longrepr", "")[:250],
            })
        elif outcome == "skipped":
            skipped += 1
        else:
            errs += 1
    return passed, failed, skipped, errs, failures


def generate_report(all_results: list, report_path: str, duration: float):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    total_passed = sum(r["passed"] for r in all_results)
    total_failed = sum(r["failed"] for r in all_results)
    total_skipped = sum(r["skipped"] for r in all_results)
    total = total_passed + total_failed + total_skipped

    lines = [
        "# 3-Way PO Reconciliation -- E2E Compliance Report",
        "",
        f"**Generated**: {now}",
        f"**Duration**:  {duration:.1f}s",
        "",
        "---",
        "",
        "## Summary",
        "",
        f"| Metric | Count |",
        f"|--------|-------|",
        f"| Total tests | {total} |",
        f"| PASSED | {total_passed} |",
        f"| FAILED | **{total_failed}** |",
        f"| SKIPPED | {total_skipped} |",
        f"| Pass Rate | {(total_passed/total*100):.1f}% |" if total else "| Pass Rate | N/A |",
        "",
        "---",
        "",
        "## Module Breakdown",
        "",
        "| Module | PASS | FAIL | SKIP | Status |",
        "|--------|------|------|------|--------|",
    ]

    for r in all_results:
        icon = "OK" if r["failed"] == 0 else "BROKEN"
        lines.append(
            f"| {r['name']} | {r['passed']} | **{r['failed']}** | {r['skipped']} | {icon} |"
        )

    # Failure details
    all_failures = []
    for r in all_results:
        for f in r.get("failures", []):
            all_failures.append((r["name"], f))

    if all_failures:
        lines.extend([
            "",
            "---",
            "",
            "## Failure Details",
            "",
        ])
        for mod_name, f in all_failures:
            lines.append(f"### [{mod_name}] `{f['id']}`")
            if f["message"]:
                lines.append("```")
                lines.append(f["message"].strip())
                lines.append("```")
            lines.append("")

    # Known issues
    lines.extend([
        "",
        "---",
        "",
        "## Known Issues & Not-Yet-Implemented",
        "",
        "| Area | Status | Notes |",
        "|------|--------|-------|",
        "| Real ERP submission | NOT IMPLEMENTED | PostingActionService.submit_posting() is Phase 1 mock |",
        "| Auto ERP re-import (Celery Beat) | NOT IMPLEMENTED | No periodic task configured |",
        "| Feedback learning (alias propagation) | NOT IMPLEMENTED | LearningEngine proposes; auto-apply pending |",
        "| Docker / deployment | NOT AVAILABLE | No Dockerfile in repo |",
        "| CI/CD pipeline | NOT CONFIGURED | No GitHub Actions |",
        "| Email notifications | NOT IMPLEMENTED | No notification system for review assignments |",
        "| Multi-page invoice OCR | PARTIAL | Single-page tested only |",
        "| LLM-assisted item mapping | NOT IMPLEMENTED | PostingMappingEngine uses deterministic only |",
        "| Report CSV/Excel export | PARTIAL | Case console CSV only; full export not built |",
        "",
        "---",
        "",
        "## Seed Commands Required Before Full Green",
        "",
        "```bash",
        "python manage.py seed_all",
        "python manage.py seed_email_data",
        "python manage.py seed_agent_contracts",
        "```",
        "",
    ])

    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"\nCompliance report written to: {report_path}")


def main():
    parser = argparse.ArgumentParser(description="3-Way PO E2E Test Runner")
    parser.add_argument("--module", help="Run a single module by name prefix")
    parser.add_argument("--no-report", action="store_true", help="Skip report generation")
    args = parser.parse_args()

    e2e_dir = ROOT / "e2e_tests"
    json_out = str(ROOT / "e2e_report.json")
    report_out = str(ROOT / "COMPLIANCE_REPORT.md")

    start = datetime.now()
    all_results = []

    if args.module:
        modules_to_run = [(name, f) for name, f in MODULES if args.module in f]
    else:
        modules_to_run = MODULES

    for name, filename in modules_to_run:
        test_path = str(e2e_dir / filename)
        if not Path(test_path).exists():
            print(f"[MISSING] {filename}")
            all_results.append({
                "name": name, "passed": 0, "failed": 0,
                "skipped": 1, "failures": [],
            })
            continue

        print(f"\n{'='*60}")
        print(f"  Running: {name}")
        print(f"{'='*60}")

        rc = run_pytest(test_path, json_out)
        report_data = load_json(json_out)
        passed, failed, skipped, _, failures = collect_results(report_data)

        all_results.append({
            "name": name,
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
            "failures": failures,
        })
        print(f"  => PASS={passed} FAIL={failed} SKIP={skipped}")

    duration = (datetime.now() - start).total_seconds()

    total_failed = sum(r["failed"] for r in all_results)
    print("\n" + "=" * 60)
    print(f"  TOTAL FAILURES: {total_failed}")
    print("=" * 60)

    if not args.no_report:
        generate_report(all_results, report_out, duration)

    sys.exit(1 if total_failed else 0)


if __name__ == "__main__":
    main()
