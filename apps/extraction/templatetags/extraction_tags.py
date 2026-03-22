"""Template tags for the extraction app."""
from django import template

register = template.Library()


@register.simple_tag
def get_approval(approval_map, invoice_id):
    """Look up an ExtractionApproval from the pre-loaded map by invoice_id."""
    if not approval_map or not invoice_id:
        return None
    return approval_map.get(invoice_id)
