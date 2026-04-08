"""
Export service -- CSV export of benchmark results.
"""
import csv
import io
import logging

logger = logging.getLogger(__name__)


class ExportService:
    """Generate CSV exports for benchmark requests."""

    @classmethod
    def export_request_csv(cls, benchmark_request) -> bytes:
        """
        Export all line items + summary for a BenchmarkRequest as CSV bytes.
        """
        output = io.StringIO()
        writer = csv.writer(output)

        # Header block
        writer.writerow(["Benchmark Report"])
        writer.writerow(["Request", benchmark_request.title])
        writer.writerow(["Project", benchmark_request.project_name or "-"])
        writer.writerow(["Geography", benchmark_request.geography])
        writer.writerow(["Scope Type", benchmark_request.get_scope_type_display() if hasattr(benchmark_request, 'get_scope_type_display') else benchmark_request.scope_type])
        writer.writerow([])

        # Summary block
        try:
            result = benchmark_request.result
            writer.writerow(["=== COMMERCIAL SUMMARY ==="])
            writer.writerow(["Total Quoted (AED)", float(result.total_quoted or 0)])
            writer.writerow(["Total Benchmark Mid (AED)", float(result.total_benchmark_mid or 0)])
            writer.writerow(["Overall Deviation %", f"{result.overall_deviation_pct:+.1f}%" if result.overall_deviation_pct is not None else "N/A"])
            writer.writerow(["Overall Status", result.overall_status])
            writer.writerow([])
            writer.writerow(["Lines Within Range", result.lines_within_range])
            writer.writerow(["Lines Moderate", result.lines_moderate])
            writer.writerow(["Lines High Variance", result.lines_high])
            writer.writerow(["Lines Needs Review", result.lines_needs_review])
            writer.writerow([])

            # Negotiation notes
            if result.negotiation_notes_json:
                writer.writerow(["=== NEGOTIATION NOTES ==="])
                for note in result.negotiation_notes_json:
                    writer.writerow([note])
                writer.writerow([])
        except Exception:
            pass

        # Line items
        writer.writerow([
            "Line#", "Description", "UOM", "Qty",
            "Quoted Unit Rate (AED)", "Line Amount (AED)",
            "Category",
            "Benchmark Min", "Benchmark Mid", "Benchmark Max",
            "Variance %", "Status", "Note",
        ])

        for quotation in benchmark_request.quotations.filter(is_active=True):
            for item in quotation.line_items.filter(is_active=True):
                writer.writerow([
                    item.line_number,
                    item.description,
                    item.uom or "",
                    float(item.quantity) if item.quantity is not None else "",
                    float(item.quoted_unit_rate) if item.quoted_unit_rate is not None else "",
                    float(item.line_amount) if item.line_amount is not None else "",
                    item.category,
                    float(item.benchmark_min) if item.benchmark_min is not None else "",
                    float(item.benchmark_mid) if item.benchmark_mid is not None else "",
                    float(item.benchmark_max) if item.benchmark_max is not None else "",
                    f"{item.variance_pct:+.1f}" if item.variance_pct is not None else "",
                    item.variance_status,
                    item.variance_note or "",
                ])

        return output.getvalue().encode("utf-8")
