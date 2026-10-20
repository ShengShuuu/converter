"""Layout profiles: declarative anchors/columns describing one printed form layout."""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

PROFILES_DIR = Path(__file__).parent

HeaderField = Literal["order_ref", "order_date", "customer", "salesperson", "payment_terms"]
LineField = Literal["product", "quantity", "unit_price", "taxes", "subtotal"]
TotalField = Literal["untaxed_total", "total"]


class ProfileModel(BaseModel):
    # A typo'd key would otherwise silently fall back to the default; inf would disable gap thresholds.
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class HeaderFieldSpec(ProfileModel):
    field: HeaderField
    label: str
    kind: Literal["text", "datetime"] = "text"
    formats: list[str] = Field(default_factory=list)
    pattern: str | None = None


class PositionalFieldSpec(ProfileModel):
    """An unlabeled field located relative to a labeled header field's line."""

    field: HeaderField
    position: Literal["first_line_of_block_above"]
    anchor_field: HeaderField
    # Vertical gap (in line heights) that ends the block, so a letterhead above it is not included.
    # block_max_gap_ratio: float = 1.0
    block_max_gap_ratio: float = Field(1.0, gt=0)
    # Fewer lines than this means a name/address block was not really found (stamp, fragment).
    min_block_lines: int = Field(2, ge=1)
    # A gap within this fraction of block_max_gap_ratio means the block boundary is a coin flip.
    block_borderline_margin: float = Field(0.25, ge=0)
    block_max_left_offset_ratio: float = Field(1.5, gt=0)


class ColumnSpec(ProfileModel):
    field: LineField
    label: str
    align: Literal["left", "right"]
    starts_row: bool = False
    required: bool = True


class TotalSpec(ProfileModel):
    field: TotalField
    label: str
    # False for rows a layout prints only sometimes (e.g. Untaxed Amount only when taxes apply).
    required: bool = True


class LayoutProfile(ProfileModel):
    name: str
    description: str = ""
    # rapidfuzz ratio scale; 0 would match any word to any label.
    fuzzy_threshold: float = Field(80.0, gt=0, le=100)
    line_tolerance_ratio: float = Field(0.5, gt=0)
    # Also the customer-block column split; <= 0 turns every word gap into a column gap.
    value_max_gap_ratio: float = Field(3.0, gt=0)
    column_pad_ratio: float = Field(1.0, ge=0)
    # Same 0..1 scale as OrderLine.confidence.
    low_confidence_threshold: float = Field(0.6, ge=0, le=1)
    header_fields: list[HeaderFieldSpec]
    positional_fields: list[PositionalFieldSpec] = Field(default_factory=list)
    table_columns: list[ColumnSpec]
    totals: list[TotalSpec] = Field(default_factory=list)
    pending_fields: dict[str, str] = Field(default_factory=dict)
    # Fields the review UI always asks the user to check against the scan, flagged or not.
    always_verify_fields: list[HeaderField | LineField | TotalField] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_positional_anchors(self) -> "LayoutProfile":
        labeled = {spec.field for spec in self.header_fields}
        positional: set[str] = set()
        for spec in self.positional_fields:
            if spec.anchor_field not in labeled:
                raise ValueError(f"{spec.field}: anchor_field {spec.anchor_field!r} is not a labeled header field")
            if spec.field in labeled:
                raise ValueError(f"{spec.field}: defined both as a labeled and a positional field")
            if spec.field in positional:
                raise ValueError(f"{spec.field}: defined more than once as a positional field")
            positional.add(spec.field)
        return self


def load_profile(name: str) -> LayoutProfile:
    return LayoutProfile.model_validate_json((PROFILES_DIR / f"{name}.json").read_text(encoding="utf-8"))
