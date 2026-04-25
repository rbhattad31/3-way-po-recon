"""Template tags for the extraction app."""
from django import template

register = template.Library()


@register.simple_tag
def get_approval(approval_map, invoice_id):
    """Look up an ExtractionApproval from the pre-loaded map by invoice_id."""
    if not approval_map or not invoice_id:
        return None
    return approval_map.get(invoice_id)


@register.simple_tag
def get_case(case_map, invoice_id):
    """Look up an APCase from the pre-loaded map by invoice_id."""
    if not case_map or not invoice_id:
        return None
    return case_map.get(invoice_id)


@register.simple_tag
def get_value(data_map, key):
    """Look up a value from a pre-loaded map by key."""
    if not data_map or key is None:
        return None
    return data_map.get(key)


@register.filter
def pretty_enum(value):
    """Render enum-like values such as TWO_WAY as Two Way."""
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    return text.replace("_", " ").title()
