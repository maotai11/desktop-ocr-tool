"""Pure, bounded document-template rules; no file, clipboard or Qt side effects.

Page zero defines the shared layout. Later pages inherit each field unless that
field has an explicit page override. Coordinates are normalized to the displayed
page, so the same selection also works on pages with different raster sizes.
"""
from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from numbers import Integral, Real

MAX_FIELD_NAME_CHARS = 64
MAX_TEMPLATE_CHARS = 100_000
MAX_OUTPUT_CHARS = 10 * 1024 * 1024
_OPENING = '{｛'
_CLOSING = '}｝'


class TemplateError(ValueError):
    """A user-correctable field, region or template validation error."""


def validate_field_name(name: str) -> str:
    """Return the canonical name; spaces around a name are not significant."""
    if not isinstance(name, str):
        raise TemplateError('欄位名稱必須是文字。')
    if any(char in _OPENING + _CLOSING or ord(char) < 32 or
           0x7f <= ord(char) <= 0x9f or char in '\u2028\u2029'
           for char in name):
        raise TemplateError('欄位名稱不能含大括號、換行或控制字元。')
    name = name.strip()
    if not name:
        raise TemplateError('請輸入欄位名稱。')
    if len(name) > MAX_FIELD_NAME_CHARS:
        raise TemplateError(f'欄位名稱最多 {MAX_FIELD_NAME_CHARS} 個字元。')
    return name


def _number(value, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise TemplateError(f'{label}必須是有效數字。')
    value = float(value)
    if not math.isfinite(value):
        raise TemplateError(f'{label}必須是有效數字。')
    return value


def _dimensions(width: int, height: int) -> tuple[int, int]:
    if (isinstance(width, bool) or isinstance(height, bool) or
            not isinstance(width, Integral) or not isinstance(height, Integral) or
            width <= 0 or height <= 0):
        raise TemplateError('頁面寬高必須是正整數。')
    return int(width), int(height)


def _page_index(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, Integral) or value < 0:
        raise TemplateError('頁碼必須是從零開始的非負整數。')
    return int(value)


@dataclass(frozen=True)
class Region:
    """Nonempty normalized rectangle, fully inside the unrotated page image."""
    x: float
    y: float
    width: float
    height: float

    def __post_init__(self):
        values = [_number(value, '框選座標')
                  for value in (self.x, self.y, self.width, self.height)]
        x, y, width, height = values
        if (not 0 <= x < 1 or not 0 <= y < 1 or width <= 0 or height <= 0 or
                x + width > 1 + 1e-12 or y + height > 1 + 1e-12):
            raise TemplateError('請在頁面內框選一個有效範圍。')
        # Permit only floating-point rounding at the bottom/right boundary.
        width, height = min(width, 1 - x), min(height, 1 - y)
        for key, value in zip(('x', 'y', 'width', 'height'), (x, y, width, height)):
            object.__setattr__(self, key, value)

    @classmethod
    def from_pixels(cls, left, top, right, bottom, page_width: int, page_height: int) -> Region:
        """Normalize a drag in either direction, clipping only at the page edge."""
        width, height = _dimensions(page_width, page_height)
        x1, y1, x2, y2 = [_number(value, '框選座標') for value in (left, top, right, bottom)]
        x1, x2 = sorted((max(0.0, min(width, x1)), max(0.0, min(width, x2))))
        y1, y2 = sorted((max(0.0, min(height, y1)), max(0.0, min(height, y2))))
        return cls(x1 / width, y1 / height, (x2 - x1) / width, (y2 - y1) / height)

    def pixel_bounds(self, page_width: int, page_height: int) -> tuple[int, int, int, int]:
        """Return exclusive right/bottom crop bounds covering at least one pixel."""
        width, height = _dimensions(page_width, page_height)

        def snap(value):
            integer = round(value)
            return integer if abs(value - integer) < 1e-9 else value

        left = min(width - 1, max(0, math.floor(snap(self.x * width))))
        top = min(height - 1, max(0, math.floor(snap(self.y * height))))
        right = min(width, max(left + 1, math.ceil(snap((self.x + self.width) * width))))
        bottom = min(height, max(top + 1, math.ceil(snap((self.y + self.height) * height))))
        return left, top, right, bottom


@dataclass(frozen=True)
class Field:
    name: str
    base_region: Region | None = None

    def __post_init__(self):
        object.__setattr__(self, 'name', validate_field_name(self.name))
        if self.base_region is not None and not isinstance(self.base_region, Region):
            raise TemplateError('欄位位置必須是有效框選範圍。')


class TemplateLayout:
    """Ordered named fields and optional later-page region replacements.

    ``fields`` and ``overrides`` expose immutable values/copied mappings. Use
    ``copy()`` when submitting work so later UI changes cannot move a running job.
    """
    def __init__(self, fields: Iterable[Field] = (),
                 overrides: Mapping[int, Mapping[str, Region | None]] | None = None):
        self._fields: dict[str, Field] = {}
        self._overrides: dict[int, dict[str, Region | None]] = {}
        for field in fields:
            self.add_field(field)
        if overrides is not None:
            if not isinstance(overrides, Mapping):
                raise TemplateError('頁面欄位設定格式錯誤。')
            for page, regions in overrides.items():
                page = _page_index(page)
                if not isinstance(regions, Mapping):
                    raise TemplateError('頁面欄位設定格式錯誤。')
                for name, region in regions.items():
                    self.set_region(name, region, page)

    @property
    def fields(self) -> tuple[Field, ...]:
        return tuple(self._fields.values())

    @property
    def field_names(self) -> tuple[str, ...]:
        return tuple(self._fields)

    @property
    def overrides(self) -> dict[int, dict[str, Region | None]]:
        return {page: dict(regions) for page, regions in self._overrides.items()}

    def _require_field(self, name: str) -> Field:
        name = validate_field_name(name)
        try:
            return self._fields[name]
        except KeyError:
            raise TemplateError(f'找不到欄位「{name}」。') from None

    def add_field(self, field: Field | str, region: Region | None = None) -> Field:
        if isinstance(field, str):
            field = Field(field, region)
        elif not isinstance(field, Field) or region is not None:
            raise TemplateError('請使用有效的欄位名稱與框選範圍。')
        if field.name in self._fields:
            raise TemplateError(f'欄位名稱「{field.name}」已存在，請使用不同名稱。')
        self._fields[field.name] = field
        return field

    def remove_field(self, name: str) -> None:
        field = self._require_field(name)
        del self._fields[field.name]
        for page, regions in list(self._overrides.items()):
            regions.pop(field.name, None)
            if not regions:
                self._overrides.pop(page, None)

    def set_region(self, name: str, region: Region | None, page_index: int | None = None) -> None:
        """Set a position, or explicitly clear it; clear_override restores inheritance."""
        field = self._require_field(name)
        page = 0 if page_index is None else _page_index(page_index)
        if region is not None and not isinstance(region, Region):
            raise TemplateError('欄位位置必須是有效框選範圍。')
        if page == 0:
            self._fields[field.name] = Field(field.name, region)
        else:
            self._overrides.setdefault(page, {})[field.name] = region

    def region_for(self, name: str, page_index: int) -> Region | None:
        field = self._require_field(name)
        page = _page_index(page_index)
        return self._overrides.get(page, {}).get(field.name, field.base_region)

    def effective_regions(self, page_index: int) -> dict[str, Region | None]:
        page = _page_index(page_index)
        return {name: self.region_for(name, page) for name in self._fields}

    def has_override(self, name: str, page_index: int) -> bool:
        field = self._require_field(name)
        return field.name in self._overrides.get(_page_index(page_index), {})

    def clear_override(self, name: str, page_index: int) -> None:
        field = self._require_field(name)
        page = _page_index(page_index)
        regions = self._overrides.get(page)
        if regions is not None:
            regions.pop(field.name, None)
            if not regions:
                self._overrides.pop(page, None)

    def clear_page_overrides(self, page_index: int) -> None:
        self._overrides.pop(_page_index(page_index), None)

    def rename_field(self, old: str, new: str, template: str | None = None) -> str | None:
        field = self._require_field(old)
        new = validate_field_name(new)
        if new != field.name and new in self._fields:
            raise TemplateError(f'欄位名稱「{new}」已存在，請使用不同名稱。')
        updated = rename_placeholder(template, field.name, new) if template is not None else None
        if new == field.name:
            return updated
        self._fields = {new if name == field.name else name:
                        Field(new, value.base_region) if name == field.name else value
                        for name, value in self._fields.items()}
        for regions in self._overrides.values():
            if field.name in regions:
                regions[new] = regions.pop(field.name)
        return updated

    def copy(self) -> TemplateLayout:
        return TemplateLayout(self.fields, self.overrides)


@dataclass(frozen=True)
class TemplateToken:
    """Literal text (``name is None``), or an exact source placeholder span."""
    text: str
    name: str | None
    start: int
    end: int


def parse_template(template: str) -> tuple[TemplateToken, ...]:
    """Parse curly-brace placeholders; double identical braces escape literals.

    Each opening/closing brace may independently be ASCII or fullwidth. Literal
    text, including whitespace, is preserved; only escaped doubled braces are
    reduced in rendered output. There is no expression or attribute evaluation.
    """
    if not isinstance(template, str):
        raise TemplateError('模板必須是文字。')
    if len(template) > MAX_TEMPLATE_CHARS:
        raise TemplateError(f'模板過長，最多 {MAX_TEMPLATE_CHARS:,} 個字元。')
    tokens = []
    literal = []
    literal_start = 0
    index = 0
    while index < len(template):
        char = template[index]
        if char in _OPENING + _CLOSING and template[index:index + 2] == char * 2:
            literal.append(char)
            index += 2
        elif char in _OPENING:
            if literal:
                tokens.append(TemplateToken(''.join(literal), None, literal_start, index))
                literal = []
            closing = index + 1
            while closing < len(template) and template[closing] not in _CLOSING:
                if template[closing] in _OPENING:
                    raise TemplateError('模板的大括號不能重疊，請使用 {欄位名稱}。')
                closing += 1
            if closing == len(template):
                raise TemplateError('模板有未關閉的大括號，請使用 {欄位名稱}。')
            name = validate_field_name(template[index + 1:closing])
            tokens.append(TemplateToken(template[index:closing + 1], name, index, closing + 1))
            index = closing + 1
            literal_start = index
        elif char in _CLOSING:
            raise TemplateError('模板有多餘的結束括號；要顯示括號請連續輸入兩個。')
        else:
            literal.append(char)
            index += 1
    if literal:
        tokens.append(TemplateToken(''.join(literal), None, literal_start, len(template)))
    return tuple(tokens)


def placeholder_names(template: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(token.name for token in parse_template(template) if token.name is not None))


def validate_template(template: str, field_names: Iterable[str]) -> tuple[str, ...]:
    names = placeholder_names(template)
    allowed = {validate_field_name(name) for name in field_names}
    missing = [name for name in names if name not in allowed]
    if missing:
        raise TemplateError('模板使用了尚未建立的欄位：' + '、'.join(missing))
    return names


def rename_placeholder(template: str, old: str, new: str) -> str:
    """Rename exact fields, preserving brace width and surrounding whitespace."""
    old, new = validate_field_name(old), validate_field_name(new)
    edits = []
    for token in parse_template(template):
        if token.name != old:
            continue
        inner = template[token.start + 1:token.end - 1]
        leading = len(inner) - len(inner.lstrip())
        trailing = len(inner) - len(inner.rstrip())
        replacement = inner[:leading] + new + (inner[len(inner) - trailing:] if trailing else '')
        edits.append((token.start + 1, token.end - 1, replacement))
    for start, end, replacement in reversed(edits):
        template = template[:start] + replacement + template[end:]
    return template


def _limit(value: int | None) -> int | None:
    if value is not None and (isinstance(value, bool) or not isinstance(value, Integral) or value <= 0):
        raise TemplateError('輸出字數上限必須是正整數。')
    return int(value) if value is not None else None


def _render(tokens: tuple[TemplateToken, ...], values: Mapping[str, str], limit: int | None) -> str:
    if not isinstance(values, Mapping):
        raise TemplateError('辨識結果格式錯誤。')
    chunks = []
    length = 0
    for token in tokens:
        if token.name is None:
            text = token.text
        else:
            if token.name not in values:
                raise TemplateError(f'缺少欄位「{token.name}」的辨識結果。')
            text = values[token.name]
            if not isinstance(text, str):
                raise TemplateError(f'欄位「{token.name}」的辨識結果必須是文字。')
        length += len(text)
        if limit is not None and length > limit:
            raise TemplateError('輸出內容超過字數上限，請縮短模板或減少處理頁數。')
        chunks.append(text)
    return ''.join(chunks)


def render_template(template: str, values: Mapping[str, str], *,
                    max_output_chars: int | None = MAX_OUTPUT_CHARS) -> str:
    return _render(parse_template(template), values, _limit(max_output_chars))


def build_output(template: str, page_values: Iterable[Mapping[str, str]], separator: str = '\n\n', *,
                 max_output_chars: int | None = MAX_OUTPUT_CHARS) -> str:
    """Resolve each logical page once; never reparse inserted OCR content."""
    tokens = parse_template(template)
    limit = _limit(max_output_chars)
    if not isinstance(separator, str):
        raise TemplateError('頁面分隔必須是文字。')
    pages = []
    length = 0
    for index, values in enumerate(page_values):
        try:
            text = _render(tokens, values, limit)
        except TemplateError as exc:
            raise TemplateError(f'第 {index + 1} 頁：{exc}') from None
        length += len(text) + (len(separator) if pages else 0)
        if limit is not None and length > limit:
            raise TemplateError('輸出內容超過字數上限，請縮短模板或減少處理頁數。')
        pages.append(text)
    return separator.join(pages)
