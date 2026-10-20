"""Pick the layout profile whose anchors best match a page's OCR words."""

from converter.extraction.anchors import find_label
from converter.extraction.words import OcrWord, group_into_lines
from converter.profiles import LayoutProfile


def anchor_match_ratio(words: list[OcrWord], profile: LayoutProfile) -> float:
    lines = group_into_lines(words, profile.line_tolerance_ratio)
    labels = [spec.label for spec in profile.header_fields] + [spec.label for spec in profile.table_columns]
    found = sum(1 for label in labels if find_label(lines, label, profile.fuzzy_threshold))
    return found / len(labels) if labels else 0.0


def select_profile(words: list[OcrWord], profiles: list[LayoutProfile], min_ratio: float = 0.6) -> LayoutProfile | None:
    scored = [(anchor_match_ratio(words, profile), profile) for profile in profiles]
    best_ratio, best_profile = max(scored, key=lambda pair: pair[0], default=(0.0, None))
    return best_profile if best_ratio >= min_ratio else None
