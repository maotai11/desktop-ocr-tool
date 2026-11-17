"""Local, manual invoice comparison primitives."""
from .fields import (
    FIELD_KEYS,
    compare_fields,
    extract_invoice_fields,
    extract_row_fields,
    header_columns,
    normalize_value,
    valid_ubn,
)
from .rows import detect_active_row

__all__ = ['FIELD_KEYS', 'compare_fields', 'detect_active_row', 'extract_invoice_fields', 'extract_row_fields', 'header_columns', 'normalize_value', 'valid_ubn']
