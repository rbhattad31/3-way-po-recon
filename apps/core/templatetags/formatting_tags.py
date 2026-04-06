"""Template filters for formatting values in UI views."""
from django import template

register = template.Library()


@register.filter
def to_percent(value):
    """Convert score values to 0-100 integer percentage.

    Supports both scales:
    - 0.0..1.0 (confidence style)
    - 0..100 (percentage style)
    """
    try:
        numeric_value = float(value)
    except (TypeError, ValueError):
        return 0

    if numeric_value <= 1.0:
        numeric_value *= 100.0

    if numeric_value < 0:
        numeric_value = 0.0
    if numeric_value > 100:
        numeric_value = 100.0

    return int(round(numeric_value))
