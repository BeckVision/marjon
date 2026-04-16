"""Template helpers for the U-001 operations cockpit."""

from django import template

register = template.Library()


@register.filter
def get_item(mapping, key):
    """Return a dictionary value by key for simple table rendering."""
    if not isinstance(mapping, dict):
        return None
    return mapping.get(key)


@register.filter
def human_label(value):
    """Render underscored keys as human-readable labels."""
    if value is None:
        return ''
    return str(value).replace('_', ' ').strip().title()
