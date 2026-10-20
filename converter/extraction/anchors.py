"""Fuzzy label matching and label-anchored value extraction for header fields."""

from dataclasses import dataclass, replace
from statistics import median

from rapidfuzz import fuzz

from converter.extraction.words import OcrWord


@dataclass(frozen=True)
class LabelMatch:
    line_index: int
    start: int
    end: int


def normalize_token(text: str) -> str:
    return text.strip().rstrip(":").upper()


def tokens_match(word_text: str, label_token: str, threshold: float) -> bool:
    return fuzz.ratio(normalize_token(word_text), normalize_token(label_token)) >= threshold


def find_label_in_line(line: list[OcrWord], label: str, threshold: float, start_at: int = 0) -> tuple[int, int] | None:
    label_tokens = label.split()
    for start in range(start_at, len(line) - len(label_tokens) + 1):
        if all(tokens_match(line[start + i].text, token, threshold) for i, token in enumerate(label_tokens)):
            return start, start + len(label_tokens)
    return None


def find_label(lines: list[list[OcrWord]], label: str, threshold: float, first_line: int = 0) -> LabelMatch | None:
    for line_index in range(first_line, len(lines)):
        span = find_label_in_line(lines[line_index], label, threshold)
        if span:
            return LabelMatch(line_index, *span)
    return None


# Replaced by extract._find_totals_row: first-word-only matching truncated tables and missed stamped totals rows.
# def find_line_start_label(lines: list[list[OcrWord]], label: str, threshold: float, first_line: int = 0) -> LabelMatch | None:
#     """Like find_label, but the label must be the first word on its line (totals rows)."""
#     for line_index in range(first_line, len(lines)):
#         line = lines[line_index]
#         if not line:
#             continue
#         span = find_label_in_line(line[: len(label.split())], label, threshold)
#         if span:
#             return LabelMatch(line_index, *span)
#     return None


# Replaced by find_block_above: whole visual rows merged other columns' text into the block.
# def block_above(lines: list[list[OcrWord]], anchor_line_index: int, max_gap_ratio: float) -> list[list[OcrWord]]:
#     """Lines directly above the anchor line that form one block, top to bottom; empty if none."""
#     if anchor_line_index == 0:
#         return []
#     block = [lines[anchor_line_index - 1]]
#     for line in reversed(lines[: anchor_line_index - 1]):
#         lower = block[0]
#         gap = min(word.top for word in lower) - max(word.bottom for word in line)
#         if gap > max_gap_ratio * median(word.height for word in lower):
#             break
#         block.insert(0, line)
#     return block


@dataclass(frozen=True)
class TextBlock:
    lines: list[list[OcrWord]]
    # Vertical gap between consecutive block lines, in line heights of the lower line.
    gap_ratios: list[float]
    # Gap to the nearest same-column line above that ended the block; None if nothing is above.
    stop_gap_ratio: float | None
    has_rival_block: bool = False


def is_column_gap(previous: OcrWord, word: OcrWord, max_gap_ratio: float) -> bool:
    return word.left - previous.right > max_gap_ratio * max(word.height, previous.height)


def split_at_column_gaps(line: list[OcrWord], max_gap_ratio: float) -> list[list[OcrWord]]:
    segments = [[line[0]]]
    for word in line[1:]:
        if is_column_gap(segments[-1][-1], word, max_gap_ratio):
            segments.append([word])
        else:
            segments[-1].append(word)
    return segments


def _overlaps_horizontally(upper: list[OcrWord], lower: list[OcrWord]) -> bool:
    return min(w.left for w in upper) < max(w.right for w in lower) and min(w.left for w in lower) < max(
        w.right for w in upper
    )


def _vertical_gap_ratio(upper: list[OcrWord], lower: list[OcrWord]) -> float:
    gap = min(word.top for word in lower) - max(word.bottom for word in upper)
    return gap / median(word.height for word in lower)


def _grow_block_upward(
    seed: list[OcrWord], lines_above: list[list[OcrWord]], max_gap_ratio: float, column_gap_ratio: float
) -> TextBlock:
    block = [seed]
    gap_ratios: list[float] = []
    for line in reversed(lines_above):
        same_column = [
            segment
            for segment in split_at_column_gaps(line, column_gap_ratio)
            if _overlaps_horizontally(segment, block[0])
        ]
        if not same_column:
            continue
        upper = [word for segment in same_column for word in segment]
        gap_ratio = _vertical_gap_ratio(upper, block[0])
        if gap_ratio > max_gap_ratio:
            return TextBlock(block, gap_ratios, gap_ratio)
        block.insert(0, upper)
        gap_ratios.insert(0, gap_ratio)
    return TextBlock(block, gap_ratios, None)


def find_block_above(
    lines: list[list[OcrWord]],
    anchor_line_index: int,
    max_gap_ratio: float,
    column_gap_ratio: float,
    min_rival_lines: int,
) -> TextBlock | None:
    """The tallest single-column block of lines directly above the anchor line, top to bottom."""
    if anchor_line_index == 0:
        return None
    seeds = split_at_column_gaps(lines[anchor_line_index - 1], column_gap_ratio)
    candidates = [_grow_block_upward(seed, lines[: anchor_line_index - 1], max_gap_ratio, column_gap_ratio) for seed in seeds]
    # tallest = max(candidates, key=lambda block: len(block.lines))
    # tied = sum(len(block.lines) == len(tallest.lines) for block in candidates) > 1
    # return replace(tallest, has_rival_block=tied)
    tallest_index = max(range(len(candidates)), key=lambda index: len(candidates[index].lines))
    # Replaced by _has_rival_block: only rivals ending on the row above the anchor were compared, missing top-aligned ones.
    # has_rival = any(
    #     len(block.lines) >= min_rival_lines for index, block in enumerate(candidates) if index != tallest_index
    # )
    tallest = candidates[tallest_index]
    has_rival = _has_rival_block(lines, anchor_line_index, tallest, max_gap_ratio, column_gap_ratio, min_rival_lines)
    return replace(tallest, has_rival_block=has_rival)


def _has_rival_block(
    lines: list[list[OcrWord]],
    anchor_line_index: int,
    winner: TextBlock,
    max_gap_ratio: float,
    column_gap_ratio: float,
    min_rival_lines: int,
) -> bool:
    """Whether non-winner text ending anywhere in the winner's vertical span forms a block of min_rival_lines."""
    # Height alone cannot tell the customer from a second address block (e.g. a separate shipping address).
    winner_word_ids = {id(word) for line in winner.lines for word in line}
    remaining = [[word for word in line if id(word) not in winner_word_ids] for line in lines[:anchor_line_index]]
    winner_top_index = next(
        index for index, line in enumerate(lines) if any(id(word) in winner_word_ids for word in line)
    )
    for row_index in range(winner_top_index, anchor_line_index):
        if not remaining[row_index]:
            continue
        rows_above = [line for line in remaining[:row_index] if line]
        for seed in split_at_column_gaps(remaining[row_index], column_gap_ratio):
            if len(_grow_block_upward(seed, rows_above, max_gap_ratio, column_gap_ratio).lines) >= min_rival_lines:
                return True
    return False


def value_words_after(line: list[OcrWord], label_end: int, stop_at: int, max_gap_ratio: float) -> list[OcrWord]:
    """Words right of a label, stopping at the next label or a column-sized horizontal gap."""
    collected: list[OcrWord] = []
    previous = line[label_end - 1]
    for word in line[label_end:stop_at]:
        if word.text == ":" and not collected:
            continue
        # if word.left - previous.right > max_gap_ratio * max(word.height, previous.height):
        if is_column_gap(previous, word, max_gap_ratio):
            break
        collected.append(word)
        previous = word
    return collected
