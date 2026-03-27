"""Template tags and filters for extraction control center."""
from django import template

register = template.Library()


@register.filter
def getattr_filter(obj, attr):
    """Get an attribute from an object. Usage: {{ obj|getattr:'field_name' }}"""
    if obj is None:
        return ""
    return getattr(obj, attr, "")


# Register with the shorter name for convenience
register.filter("getattr", getattr_filter)
