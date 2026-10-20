"""OCR word representation, Tesseract TSV parsing, and visual line grouping."""

from dataclasses import dataclass
from statistics import median

TSV_WORD_LEVEL = 5


@dataclass(frozen=True)
class OcrWord:
    text: str
    left: int
    top: int
    width: int
    height: int
    conf: float
    page: int = 1

    @property
    def right(self) -> int:
        return self.left + self.width

    @property
    def bottom(self) -> int:
        return self.top + self.height

    @property
    def center_x(self) -> float:
        return self.left + self.width / 2

    @property
    def center_y(self) -> float:
        return self.top + self.height / 2


def parse_tesseract_tsv(tsv_text: str, page: int = 1) -> list[OcrWord]:
    rows = tsv_text.splitlines()
    if not rows:
        return []
    columns = rows[0].split("\t")
    words = []
    for row in rows[1:]:
        record = dict(zip(columns, row.split("\t")))
        text = record.get("text", "").strip()
        conf = float(record.get("conf", "-1"))
        # conf -1 marks page/block/paragraph/line rows and empty detections.
        if int(record["level"]) != TSV_WORD_LEVEL or not text or conf < 0:
            continue
        words.append(
            OcrWord(
                text=text,
                left=int(record["left"]),
                top=int(record["top"]),
                width=int(record["width"]),
                height=int(record["height"]),
                conf=conf,
                page=page,
            )
        )
    return words


def group_into_lines(words: list[OcrWord], tolerance_ratio: float = 0.5) -> list[list[OcrWord]]:
    """Cluster words into visual lines by vertical center, each line sorted left to right."""
    # Tesseract's own line_num splits table columns into separate blocks, so rows are rebuilt geometrically.
    lines: list[list[OcrWord]] = []
    for word in sorted(words, key=lambda w: w.center_y):
        if lines:
            current = lines[-1]
            current_center = sum(w.center_y for w in current) / len(current)
            current_height = median(w.height for w in current)
            if abs(word.center_y - current_center) <= tolerance_ratio * max(word.height, current_height):
                current.append(word)
                continue
        lines.append([word])
    return [sorted(line, key=lambda w: w.left) for line in lines]
