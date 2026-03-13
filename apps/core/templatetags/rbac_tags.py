"""
RBAC template tags for permission-aware template rendering.

Usage in templates:
    {% load rbac_tags %}

    {% has_permission "invoices.view" as can_view_invoices %}
    {% if can_view_invoices %}...{% endif %}

    {% has_role "ADMIN" as is_admin %}

    {% has_any_permission "invoices.view,reconciliation.view" as can_see %}

    {# Shorthand block tags #}
    {% if_can "reconciliation.run" %}
        <button>Run Reconciliation</button>
    {% end_if_can %}
"""
from django import template

register = template.Library()


@register.simple_tag(takes_context=True)
def has_permission(context, permission_code):
    """Check if current user has the given permission code.

    Usage: {% has_permission "invoices.view" as can_view %}
    """
    request = context.get("request")
    if not request:
        return False
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return False
    # Use pre-loaded context if available
    perms = context.get("user_permissions")
    if perms is not None:
        if context.get("is_admin"):
            return True
        return permission_code in perms
    # Fallback to user method
    if hasattr(user, "has_permission"):
        return user.has_permission(permission_code)
    return False


@register.simple_tag(takes_context=True)
def has_role(context, role_code):
    """Check if current user has the given role.

    Usage: {% has_role "ADMIN" as is_admin %}
    """
    request = context.get("request")
    if not request:
        return False
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return False
    role_codes = context.get("user_role_codes")
    if role_codes is not None:
        if "ADMIN" in role_codes:
            return True
        return role_code in role_codes
    if hasattr(user, "has_role"):
        return user.has_role(role_code)
    return getattr(user, "role", None) == role_code


@register.simple_tag(takes_context=True)
def has_any_permission(context, permission_codes_csv):
    """Check if user has any of comma-separated permission codes.

    Usage: {% has_any_permission "invoices.view,reconciliation.view" as can_see %}
    """
    codes = [c.strip() for c in permission_codes_csv.split(",") if c.strip()]
    request = context.get("request")
    if not request:
        return False
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return False
    perms = context.get("user_permissions")
    if perms is not None:
        if context.get("is_admin"):
            return True
        return bool(set(codes) & set(perms))
    if hasattr(user, "has_any_permission"):
        return user.has_any_permission(codes)
    return False


# ---------------------------------------------------------------------------
# Block-style permission tags
# ---------------------------------------------------------------------------

class IfCanNode(template.Node):
    """Renders child nodes only if user has the required permission."""

    def __init__(self, permission_code, nodelist_true, nodelist_false):
        self.permission_code = permission_code
        self.nodelist_true = nodelist_true
        self.nodelist_false = nodelist_false

    def render(self, context):
        request = context.get("request")
        allowed = False
        if request:
            user = getattr(request, "user", None)
            if user and user.is_authenticated:
                perms = context.get("user_permissions")
                if perms is not None:
                    allowed = context.get("is_admin") or (self.permission_code in perms)
                elif hasattr(user, "has_permission"):
                    allowed = user.has_permission(self.permission_code)

        if allowed:
            return self.nodelist_true.render(context)
        elif self.nodelist_false:
            return self.nodelist_false.render(context)
        return ""


@register.tag("if_can")
def do_if_can(parser, token):
    """Block tag: {% if_can "permission.code" %}...{% else_can %}...{% end_if_can %}"""
    bits = token.split_contents()
    if len(bits) != 2:
        raise template.TemplateSyntaxError(f"'{bits[0]}' tag requires exactly one argument")
    permission_code = bits[1].strip("\"'")
    nodelist_true = parser.parse(("else_can", "end_if_can"))
    token = parser.next_token()
    nodelist_false = None
    if token.contents == "else_can":
        nodelist_false = parser.parse(("end_if_can",))
        parser.delete_first_token()
    return IfCanNode(permission_code, nodelist_true, nodelist_false)
