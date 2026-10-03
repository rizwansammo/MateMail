"""Validated per-mailbox visual preferences; never fed into the mail engine."""
import re

from rest_framework import serializers

DEFAULT_FOLDER_COLOR = "#2563eb"
DEFAULT_LABEL_COLOR = "#9333ea"
HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


def validate_color(raw):
    """Only CSS-safe six-digit hexadecimal colors; normalize stored values."""
    if not isinstance(raw, str) or not HEX_COLOR.fullmatch(raw):
        raise serializers.ValidationError({
            "color": "Choose a valid six-digit hexadecimal color (for example #2563eb)."
        })
    return raw.lower()
