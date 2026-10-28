from __future__ import annotations

import re


_CITY_STATE_ZIP_RE = re.compile(
    r"^(?P<city>.+?),\s*(?P<region>[A-Za-z]{2})\s+(?P<postal>\d{5}(?:-\d{4})?)\s*$"
)
_STATE_ZIP_ONLY_RE = re.compile(
    r"^(?P<region>[A-Za-z]{2})\s+(?P<postal>\d{5}(?:-\d{4})?)\s*$"
)
_TRAILING_CITY_STATE_ZIP_RE = re.compile(
    r"^(?P<street>.+?),\s*(?P<city_line>[^,]+,\s*[A-Za-z]{2}\s+\d{5}(?:-\d{4})?)\s*$"
)
_ADDRESS_TYPE_PREFIX_RE = re.compile(
    r"^([A-Za-z][A-Za-z0-9 /._-]{0,19}):\s*"
)


def _clean(value: object) -> str:
    return str(value or "").strip()


def split_address_type_prefix(text: str) -> tuple[str, str]:
    """Split a leading 'Work:' / 'Other:' label from formatted address text."""
    value = str(text or "").replace("\r", "\n")
    if not value.strip():
        return "", ""
    first, *rest = value.split("\n", 1)
    match = _ADDRESS_TYPE_PREFIX_RE.match(first.strip())
    if not match:
        return "", _clean(value)
    remainder = first.strip()[match.end() :].strip()
    if rest:
        remainder = "\n".join(part for part in (remainder, rest[0]) if part)
    return match.group(1).strip(), remainder


def normalize_address_label(label: str) -> str:
    cleaned = _clean(label)
    if not cleaned or cleaned.lower() == "home":
        return ""
    return cleaned


def build_city_line(
    city: str = "",
    region: str = "",
    postal_code: str = "",
) -> str:
    city_text = _clean(city)
    region_text = _clean(region)
    postal = _clean(postal_code)
    if city_text and region_text and postal:
        return f"{city_text}, {region_text} {postal}"
    if city_text and region_text:
        return f"{city_text}, {region_text}"
    if city_text and postal:
        return f"{city_text} {postal}"
    return ", ".join(part for part in (city_text, region_text, postal) if part)


def build_street_block(
    street_address: str = "",
    extended_address: str = "",
) -> str:
    return ", ".join(
        part
        for part in (_clean(street_address), _clean(extended_address))
        if part
    )


def build_formatted_address(
    street_address: str = "",
    extended_address: str = "",
    city: str = "",
    region: str = "",
    postal_code: str = "",
) -> str:
    """Contact card and Google Contacts: one line each for street, street 2, city."""
    city_line = build_city_line(city, region, postal_code)
    return "\n".join(
        part
        for part in (
            _clean(street_address),
            _clean(extended_address),
            city_line,
        )
        if part
    )


def parse_formatted_address(value: str) -> dict[str, str]:
    text = _clean(str(value or "").replace("\r", "\n"))
    empty = {
        "street_address": "",
        "extended_address": "",
        "city": "",
        "region": "",
        "postal_code": "",
    }
    if not text:
        return empty

    lines = [line.strip() for line in text.split("\n") if line.strip()]
    if len(lines) == 1 and "," in lines[0]:
        parts = [part.strip() for part in lines[0].split(",") if part.strip()]
        if len(parts) >= 3:
            state_zip = _STATE_ZIP_ONLY_RE.match(parts[-1])
            if state_zip:
                street_parts = parts[:-2]
                return {
                    "street_address": street_parts[0],
                    "extended_address": ", ".join(street_parts[1:]) if len(street_parts) > 1 else "",
                    "city": parts[-2],
                    "region": state_zip.group("region").upper(),
                    "postal_code": state_zip.group("postal"),
                }
        if len(parts) == 2:
            state_zip = _CITY_STATE_ZIP_RE.match(f"{parts[0]}, {parts[1]}")
            if state_zip:
                return {
                    "street_address": "",
                    "extended_address": "",
                    "city": state_zip.group("city").strip(),
                    "region": state_zip.group("region").upper(),
                    "postal_code": state_zip.group("postal"),
                }
            trailing = _STATE_ZIP_ONLY_RE.match(parts[1])
            if trailing:
                return {
                    "street_address": parts[0],
                    "extended_address": "",
                    "city": "",
                    "region": trailing.group("region").upper(),
                    "postal_code": trailing.group("postal"),
                }

    if len(lines) >= 2:
        city_match = _CITY_STATE_ZIP_RE.match(lines[-1])
        if city_match:
            street_lines = lines[:-1]
            return {
                "street_address": street_lines[0] if street_lines else "",
                "extended_address": "\n".join(street_lines[1:]) if len(street_lines) > 1 else "",
                "city": city_match.group("city").strip(),
                "region": city_match.group("region").upper(),
                "postal_code": city_match.group("postal"),
            }

    if len(lines) == 1:
        city_match = _CITY_STATE_ZIP_RE.match(lines[0])
        if city_match:
            return {
                "street_address": "",
                "extended_address": "",
                "city": city_match.group("city").strip(),
                "region": city_match.group("region").upper(),
                "postal_code": city_match.group("postal"),
            }
        return {
            "street_address": lines[0],
            "extended_address": "",
            "city": "",
            "region": "",
            "postal_code": "",
        }

    return {
        "street_address": lines[0],
        "extended_address": "\n".join(lines[1:]),
        "city": "",
        "region": "",
        "postal_code": "",
    }


def enrich_address_parts(
    *,
    street_address: str = "",
    extended_address: str = "",
    city: str = "",
    region: str = "",
    postal_code: str = "",
    formatted_address: str = "",
) -> dict[str, str]:
    street = _clean(street_address)
    extended = _clean(extended_address)
    city_text = _clean(city)
    region_text = _clean(region)
    postal = _clean(postal_code)
    formatted = _clean(str(formatted_address or "").replace("\r", "\n"))

    has_structured = any((street, extended, city_text, region_text, postal))
    if not has_structured and formatted:
        parsed = parse_formatted_address(formatted)
        street = parsed["street_address"]
        extended = parsed["extended_address"]
        city_text = parsed["city"]
        region_text = parsed["region"]
        postal = parsed["postal_code"]
        has_structured = any((street, extended, city_text, region_text, postal))

    if has_structured:
        rebuilt = build_formatted_address(street, extended, city_text, region_text, postal)
        if rebuilt:
            formatted = rebuilt

    return {
        "street_address": street,
        "extended_address": extended,
        "city": city_text,
        "region": region_text,
        "postal_code": postal,
        "formatted_address": formatted,
    }


def split_address_street_and_city(value: str) -> tuple[str, str]:
    """Return (street_block, city_line) for display wrapping."""
    text = _clean(str(value or "").replace("\r", "\n"))
    if not text:
        return "", ""

    lines = [line.strip() for line in text.split("\n") if line.strip()]
    if len(lines) >= 2 and _CITY_STATE_ZIP_RE.match(lines[-1]):
        return ", ".join(lines[:-1]), lines[-1]

    match = _TRAILING_CITY_STATE_ZIP_RE.match(" ".join(lines) if len(lines) > 1 else text)
    if match:
        return match.group("street").strip(), match.group("city_line").strip()

    enriched = enrich_address_parts(formatted_address=text)
    street_block = build_street_block(enriched["street_address"], enriched["extended_address"])
    city_line = build_city_line(enriched["city"], enriched["region"], enriched["postal_code"])
    if street_block or city_line:
        return street_block, city_line
    return text, ""


_UNIT_OR_APT_PART_RE = re.compile(
    r"^(?:#\S+|(?:apt|apartment|suite|ste|unit)\b.*)$",
    re.IGNORECASE,
)


def _is_unit_or_apt_part(part: str) -> bool:
    return bool(_UNIT_OR_APT_PART_RE.match(_clean(part)))


def _wrap_address_words(text: str, *, fits) -> list[str]:
    value = _clean(text)
    if not value:
        return []
    if fits(value):
        return [value]
    words = value.split()
    if len(words) <= 1:
        return [value]
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        if fits(candidate):
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _wrap_address_text_to_width(
    text: str,
    *,
    fits,
) -> list[str]:
    """Wrap a long address segment, preferring commas, then spaces."""
    value = _clean(text)
    if not value:
        return []
    if fits(value):
        return [value]

    parts = [part.strip() for part in value.split(",") if part.strip()]
    if len(parts) > 1:
        lines: list[str] = []
        current = parts[0]
        for part in parts[1:]:
            candidate = f"{current}, {part}"
            # Keep short unit/apt tokens with the street line when possible.
            if fits(candidate) or _is_unit_or_apt_part(part):
                current = candidate
            else:
                # Keep the comma on the finished line so the break is obvious.
                lines.append(f"{current},")
                current = part
        if current:
            lines.append(current)
        wrapped: list[str] = []
        for line in lines:
            if fits(line):
                wrapped.append(line)
            else:
                # Word-wrap only — do not re-enter comma logic (sticky units).
                wrapped.extend(_wrap_address_words(line.rstrip(",").strip(), fits=fits) or [line])
        return wrapped

    return _wrap_address_words(value, fits=fits)


def _with_city_line(street_lines: list[str], city_line: str, fits) -> list[str]:
    if not city_line:
        return street_lines
    if not street_lines:
        return _wrap_address_text_to_width(city_line, fits=fits)
    combined = f"{street_lines[-1]}, {city_line}"
    if fits(combined):
        return [*street_lines[:-1], combined]
    return [*street_lines, city_line]


def address_display_lines(
    value: str = "",
    *,
    street_address: str = "",
    extended_address: str = "",
    city: str = "",
    region: str = "",
    postal_code: str = "",
    label: str = "",
    max_width: float | None = None,
    text_width=None,
) -> list[str]:
    """Print lines for address book / field list.

    Street address 1 and street address 2 stay on separate lines when both
    are filled. With only one street line, prefer street + city on one line;
    if that is too wide, put city/state/ZIP on the next line. Long street
    blocks wrap at commas so lines stay inside the available address width.
    Non-Home labels such as Work: are included in the one-line fit check.
    """
    def _fits(line: str) -> bool:
        if max_width is None or text_width is None:
            return True
        try:
            return float(text_width(line)) <= float(max_width)
        except Exception:
            return True

    parsed_label, unlabeled_value = split_address_type_prefix(value)
    address_label = normalize_address_label(label) or normalize_address_label(parsed_label)
    prefix = f"{address_label}: " if address_label else ""

    def _with_prefix(line: str) -> str:
        if not prefix or not line:
            return line
        if line.lower().startswith(prefix.lower()):
            return line
        return f"{prefix}{line}"

    def _fits_first(line: str) -> bool:
        return _fits(_with_prefix(line))

    def _apply_prefix(lines: list[str]) -> list[str]:
        if not prefix or not lines:
            return lines
        return [_with_prefix(lines[0]), *lines[1:]]

    enriched = enrich_address_parts(
        street_address=street_address,
        extended_address=extended_address,
        city=city,
        region=region,
        postal_code=postal_code,
        formatted_address=unlabeled_value,
    )
    street1 = _clean(enriched["street_address"])
    street1_label, street1_rest = split_address_type_prefix(street1)
    if street1_label and (
        not address_label or street1_label.lower() == address_label.lower()
    ):
        street1 = street1_rest
    street2 = _clean(str(enriched["extended_address"] or "").replace("\r", "\n"))
    street2_parts = [part.strip() for part in street2.split("\n") if part.strip()]
    street_segments = [part for part in [street1, *street2_parts] if part]
    city_line = build_city_line(enriched["city"], enriched["region"], enriched["postal_code"])

    if not street_segments and not city_line:
        return []
    if not street_segments:
        return _apply_prefix(_wrap_address_text_to_width(city_line, fits=_fits_first if prefix else _fits))

    if street1 and street2_parts:
        first = _wrap_address_text_to_width(street1, fits=_fits_first if prefix else _fits) or [street1]
        rest: list[str] = []
        for segment in street2_parts:
            rest.extend(_wrap_address_text_to_width(segment, fits=_fits) or [segment])
        return _apply_prefix(_with_city_line([*first, *rest], city_line, _fits))

    street_block = ", ".join(street_segments)
    if not city_line:
        return _apply_prefix(_wrap_address_text_to_width(street_block, fits=_fits_first if prefix else _fits))

    one_line = f"{street_block}, {city_line}"
    if _fits_first(one_line):
        return [_with_prefix(one_line)]
    if _fits_first(street_block):
        return [_with_prefix(street_block), city_line]
    return _apply_prefix(_with_city_line(_wrap_address_text_to_width(street_block, fits=_fits), city_line, _fits))
