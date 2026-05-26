"""
services/report_service.py

PDF Chain-of-Custody Report Generator for DFECPIMS.

Generates a complete forensic chain-of-custody report for a case using
ReportLab's Platypus engine. The report is produced in memory as a
BytesIO buffer — no temp file is written to disk.

Report structure:
  Page 1 — Cover page
    System name and document classification banner
    Case ID, title, severity badge
    Investigator name, case status, date range
    Report generation timestamp and generating user

  Page 2+ — Case Metadata
    Full case description
    All key fields in a clean two-column info grid

  Next section — Evidence Inventory
    One row per evidence item:
      Evidence ID | Filename | Size | Type | Acquired By | Acquired At
      Write Blocker | Integrity Status | Last Verified At
      SHA-256 hash (full 64 chars, monospace, never truncated)
      Location description and notes (if present)

  Final section — Audit Trail
    Every audit log entry for this case in chronological order (oldest first)
    Columns: Timestamp | Actor | Action | Detail summary
    Rendered in a terminal-log aesthetic with alternating row shading

Typography decisions:
  Body text: Helvetica 9pt
  Monospace (hashes, IDs): Courier 7pt — fits full SHA-256 in a table cell
  Section headers: Helvetica-Bold 12pt
  Cover title: Helvetica-Bold 20pt

Hash display:
  SHA-256 hashes are always displayed in full (64 hex chars). A truncated
  hash in a forensic document is useless — it cannot be independently
  verified. The 7pt Courier font fits exactly 64 chars across a standard
  table cell width on A4.

Severity colour coding:
  LOW      → green
  MEDIUM   → orange
  HIGH     → red
  CRITICAL → dark red with white text

Integrity status colour coding:
  verified → green
  failed   → red
  pending  → grey
"""

import json
from datetime import datetime, timezone
from io import BytesIO
from typing import Optional

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.case import Case
from app.models.evidence import Evidence, IntegrityStatus
from app.schemas.audit import AuditLogEntry
from app.services.audit_query_service import AuditQueryService
from app.services.audit_service import AuditService
from app.services.case_service import CaseService
from app.services.evidence_service import EvidenceService


# ─── Colour palette ───────────────────────────────────────────────────────────

# Dark terminal background for headers — matches the frontend aesthetic
TERMINAL_BLACK   = colors.HexColor("#0d1117")
TERMINAL_GREEN   = colors.HexColor("#3fb950")
TERMINAL_RED     = colors.HexColor("#f85149")
TERMINAL_ORANGE  = colors.HexColor("#d29922")
TERMINAL_GREY    = colors.HexColor("#6e7681")
TERMINAL_BLUE    = colors.HexColor("#58a6ff")
DFECPIMS_ACCENT  = colors.HexColor("#1f6feb")

# Row shading for tables
ROW_LIGHT        = colors.HexColor("#f6f8fa")
ROW_DARK         = colors.white

# Severity colours
SEVERITY_COLORS = {
    "LOW":      (TERMINAL_GREEN,   colors.white),
    "MEDIUM":   (TERMINAL_ORANGE,  colors.white),
    "HIGH":     (TERMINAL_RED,     colors.white),
    "CRITICAL": (colors.HexColor("#8b0000"), colors.white),
}

# Integrity status colours
INTEGRITY_COLORS = {
    "verified": TERMINAL_GREEN,
    "failed":   TERMINAL_RED,
    "pending":  TERMINAL_GREY,
}


# ─── Style helpers ────────────────────────────────────────────────────────────

def _styles() -> dict:
    """
    Build and return the full set of paragraph styles used in the report.
    Using a dict so styles are referenced by name, not position.
    """
    base = getSampleStyleSheet()

    return {
        "cover_title": ParagraphStyle(
            "cover_title",
            fontName="Helvetica-Bold",
            fontSize=22,
            textColor=TERMINAL_BLACK,
            spaceAfter=6,
            leading=28,
        ),
        "cover_subtitle": ParagraphStyle(
            "cover_subtitle",
            fontName="Helvetica",
            fontSize=11,
            textColor=TERMINAL_GREY,
            spaceAfter=4,
        ),
        "cover_meta": ParagraphStyle(
            "cover_meta",
            fontName="Helvetica",
            fontSize=9,
            textColor=TERMINAL_BLACK,
            spaceAfter=3,
        ),
        "section_header": ParagraphStyle(
            "section_header",
            fontName="Helvetica-Bold",
            fontSize=13,
            textColor=TERMINAL_BLACK,
            spaceBefore=14,
            spaceAfter=6,
        ),
        "body": ParagraphStyle(
            "body",
            fontName="Helvetica",
            fontSize=9,
            textColor=TERMINAL_BLACK,
            spaceAfter=4,
            leading=13,
        ),
        "body_bold": ParagraphStyle(
            "body_bold",
            fontName="Helvetica-Bold",
            fontSize=9,
            textColor=TERMINAL_BLACK,
            spaceAfter=4,
        ),
        "mono": ParagraphStyle(
            "mono",
            fontName="Courier",
            fontSize=7,
            textColor=TERMINAL_BLACK,
            leading=10,
        ),
        "mono_green": ParagraphStyle(
            "mono_green",
            fontName="Courier-Bold",
            fontSize=7,
            textColor=TERMINAL_GREEN,
            leading=10,
        ),
        "mono_red": ParagraphStyle(
            "mono_red",
            fontName="Courier-Bold",
            fontSize=7,
            textColor=TERMINAL_RED,
            leading=10,
        ),
        "caption": ParagraphStyle(
            "caption",
            fontName="Helvetica",
            fontSize=7,
            textColor=TERMINAL_GREY,
            spaceAfter=2,
        ),
        "hash": ParagraphStyle(
            "hash",
            fontName="Courier",
            fontSize=7,
            textColor=colors.HexColor("#0550ae"),   # Blue for hash values
            leading=9,
            wordWrap="CJK",   # Force wrapping on any character boundary
        ),
        "classification": ParagraphStyle(
            "classification",
            fontName="Helvetica-Bold",
            fontSize=8,
            textColor=colors.white,
            alignment=1,  # Center
        ),
    }


def _hr(width_pct: float = 1.0) -> HRFlowable:
    """Return a horizontal rule flowable."""
    return HRFlowable(
        width=f"{int(width_pct * 100)}%",
        thickness=0.5,
        color=TERMINAL_GREY,
        spaceAfter=6,
        spaceBefore=6,
    )


def _fmt_bytes(n: int) -> str:
    """Human-readable file size: bytes → KB / MB / GB."""
    if n < 1024:
        return f"{n} B"
    if n < 1024 ** 2:
        return f"{n / 1024:.1f} KB"
    if n < 1024 ** 3:
        return f"{n / (1024 ** 2):.1f} MB"
    return f"{n / (1024 ** 3):.2f} GB"


def _fmt_ts(dt: Optional[datetime]) -> str:
    """Format a UTC-aware datetime as a clean ISO string, or '—' if None."""
    if dt is None:
        return "\u2014"
    return dt.strftime("%Y-%m-%d %H:%M:%S UTC")


def _detail_summary(detail: Optional[dict]) -> str:
    """
    Convert an audit log detail dict to a compact readable string for the
    audit trail table. Strips large values (like full hashes) for readability;
    the full detail is in the database for anyone who needs it.
    """
    if not detail:
        return ""

    # Fields we always omit from the table summary (too long or redundant)
    SKIP_KEYS = {"sha256_hash", "stored_hash", "computed_hash", "storage_path"}

    parts = []
    for k, v in detail.items():
        if k in SKIP_KEYS:
            continue
        if isinstance(v, str) and len(v) > 80:
            v = v[:77] + "..."
        if v is None:
            continue
        # Convert snake_case key to readable label
        label = k.replace("_", " ").title()
        parts.append(f"{label}: {v}")

    return "  |  ".join(parts) if parts else ""


# ─── Section builders ─────────────────────────────────────────────────────────

def _build_cover_page(
    case: Case,
    generated_by: str,
    generated_at: datetime,
    styles: dict,
    page_width: float,
) -> list:
    """
    Build the cover page flowables.

    Returns a list of Platypus flowables ending with a PageBreak.
    """
    story = []

    # Classification banner — visually prominent at the top
    banner_data = [["CHAIN OF CUSTODY REPORT — DFECPIMS — CONFIDENTIAL"]]
    banner = Table(
        banner_data,
        colWidths=[page_width - 40 * mm],
    )
    banner.setStyle(TableStyle([
        ("BACKGROUND",   (0, 0), (-1, -1), TERMINAL_BLACK),
        ("TEXTCOLOR",    (0, 0), (-1, -1), colors.white),
        ("FONTNAME",     (0, 0), (-1, -1), "Helvetica-Bold"),
        ("FONTSIZE",     (0, 0), (-1, -1), 9),
        ("ALIGN",        (0, 0), (-1, -1), "CENTER"),
        ("TOPPADDING",   (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 8),
    ]))
    story.append(banner)
    story.append(Spacer(1, 20 * mm))

    # Case ID in large monospace
    story.append(Paragraph(
        f'<font name="Courier-Bold" size="14" color="#1f6feb">{case.id}</font>',
        styles["body"],
    ))
    story.append(Spacer(1, 2 * mm))

    # Case title
    story.append(Paragraph(case.title, styles["cover_title"]))
    story.append(Spacer(1, 3 * mm))

    # Severity badge
    sev_bg, sev_fg = SEVERITY_COLORS.get(case.severity.value, (TERMINAL_GREY, colors.white))
    severity_data = [[f"  SEVERITY: {case.severity.value}  "]]
    severity_badge = Table(severity_data, colWidths=[60 * mm])
    severity_badge.setStyle(TableStyle([
        ("BACKGROUND",   (0, 0), (-1, -1), sev_bg),
        ("TEXTCOLOR",    (0, 0), (-1, -1), sev_fg),
        ("FONTNAME",     (0, 0), (-1, -1), "Helvetica-Bold"),
        ("FONTSIZE",     (0, 0), (-1, -1), 9),
        ("ALIGN",        (0, 0), (-1, -1), "LEFT"),
        ("TOPPADDING",   (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 5),
        ("LEFTPADDING",  (0, 0), (-1, -1), 8),
    ]))
    story.append(severity_badge)
    story.append(Spacer(1, 10 * mm))
    story.append(_hr())
    story.append(Spacer(1, 4 * mm))

    # Case metadata grid
    investigator_name = (
        case.investigator.name if case.investigator else "Unknown"
    )
    meta_rows = [
        ["Status",            case.status.value],
        ["Lead Investigator", investigator_name],
        ["Opened",            _fmt_ts(case.created_at)],
        ["Last Updated",      _fmt_ts(case.updated_at)],
    ]
    meta_table = Table(meta_rows, colWidths=[50 * mm, page_width - 40 * mm - 50 * mm])
    meta_table.setStyle(TableStyle([
        ("FONTNAME",     (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME",     (1, 0), (1, -1), "Helvetica"),
        ("FONTSIZE",     (0, 0), (-1, -1), 9),
        ("TEXTCOLOR",    (0, 0), (-1, -1), TERMINAL_BLACK),
        ("VALIGN",       (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING",   (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 4),
        ("LEFTPADDING",  (0, 0), (-1, -1), 0),
    ]))
    story.append(meta_table)
    story.append(Spacer(1, 10 * mm))
    story.append(_hr())
    story.append(Spacer(1, 4 * mm))

    # Report generation info
    story.append(Paragraph(
        f"Report generated: {_fmt_ts(generated_at)}",
        styles["cover_meta"],
    ))
    story.append(Paragraph(
        f"Generated by: {generated_by}",
        styles["cover_meta"],
    ))
    story.append(Paragraph(
        "This document constitutes an official chain-of-custody record produced by "
        "DFECPIMS. All timestamps are UTC. SHA-256 hashes were computed server-side "
        "at evidence ingestion time and may be independently verified against the "
        "stored evidence files.",
        styles["caption"],
    ))

    story.append(PageBreak())
    return story


def _build_case_metadata(case: Case, styles: dict) -> list:
    """Build the case metadata section flowables."""
    story = []
    story.append(Paragraph("Case Metadata", styles["section_header"]))
    story.append(_hr())

    if case.description:
        story.append(Paragraph("Description", styles["body_bold"]))
        story.append(Paragraph(case.description, styles["body"]))
        story.append(Spacer(1, 4 * mm))

    return story


def _build_evidence_section(
    evidence_items: list[Evidence],
    styles: dict,
    page_width: float,
) -> list:
    """
    Build the evidence inventory section.

    Each evidence item gets a self-contained block:
      - Header row with ID, filename, size, type
      - Full SHA-256 hash in monospace
      - Acquisition metadata
      - Notes and location if present
    """
    story = []
    story.append(Paragraph("Evidence Inventory", styles["section_header"]))
    story.append(_hr())

    if not evidence_items:
        story.append(Paragraph("No evidence items attached to this case.", styles["body"]))
        return story

    story.append(Paragraph(
        f"Total evidence items: {len(evidence_items)}",
        styles["caption"],
    ))
    story.append(Spacer(1, 3 * mm))

    usable_width = page_width - 40 * mm   # account for page margins

    for idx, ev in enumerate(evidence_items):
        # Integrity colour for status badge
        integrity_color = INTEGRITY_COLORS.get(ev.integrity_status.value, TERMINAL_GREY)

        # Evidence item header table
        header_data = [[
            Paragraph(f'<b>{ev.id}</b>', styles["body"]),
            Paragraph(ev.filename, styles["body"]),
            Paragraph(_fmt_bytes(ev.file_size_bytes), styles["body"]),
            Paragraph(ev.file_type or "unknown", styles["caption"]),
        ]]
        header_widths = [25 * mm, usable_width - 85 * mm, 25 * mm, 35 * mm]
        header_table = Table(header_data, colWidths=header_widths)
        header_table.setStyle(TableStyle([
            ("BACKGROUND",   (0, 0), (-1, -1), TERMINAL_BLACK if idx % 2 == 0 else colors.HexColor("#161b22")),
            ("TEXTCOLOR",    (0, 0), (-1, -1), colors.white),
            ("FONTNAME",     (0, 0), (-1, -1), "Helvetica"),
            ("FONTSIZE",     (0, 0), (-1, -1), 9),
            ("TOPPADDING",   (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING",(0, 0), (-1, -1), 6),
            ("LEFTPADDING",  (0, 0), (-1, -1), 8),
            ("VALIGN",       (0, 0), (-1, -1), "MIDDLE"),
        ]))
        story.append(header_table)

        # SHA-256 hash row — full 64 chars, never truncated
        hash_data = [[
            Paragraph("SHA-256", styles["caption"]),
            Paragraph(ev.sha256_hash, styles["hash"]),
        ]]
        hash_table = Table(hash_data, colWidths=[25 * mm, usable_width - 25 * mm])
        hash_table.setStyle(TableStyle([
            ("BACKGROUND",   (0, 0), (-1, -1), colors.HexColor("#f0f6ff")),
            ("TOPPADDING",   (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING",(0, 0), (-1, -1), 4),
            ("LEFTPADDING",  (0, 0), (-1, -1), 8),
            ("VALIGN",       (0, 0), (-1, -1), "MIDDLE"),
            ("LINEBELOW",    (0, 0), (-1, -1), 0.3, TERMINAL_GREY),
        ]))
        story.append(hash_table)

        # Acquisition metadata row
        acquired_by_name = (
            ev.acquired_by_user.name if ev.acquired_by_user else ev.acquired_by
        )
        integrity_label = ev.integrity_status.value.upper()
        verified_label = _fmt_ts(ev.last_verified_at)

        meta_data = [[
            Paragraph(f"<b>Acquired by:</b> {acquired_by_name}", styles["body"]),
            Paragraph(f"<b>Acquired at:</b> {_fmt_ts(ev.acquired_at)}", styles["body"]),
            Paragraph(
                f"<b>Write blocker:</b> {'YES' if ev.write_blocker_used else 'NO'}",
                styles["body"],
            ),
            Paragraph(
                f'<font color="{integrity_color.hexval() if hasattr(integrity_color, "hexval") else "#333333"}"><b>{integrity_label}</b></font>  '
                f"Last verified: {verified_label}",
                styles["body"],
            ),
        ]]
        meta_widths = [
            usable_width * 0.28,
            usable_width * 0.28,
            usable_width * 0.18,
            usable_width * 0.26,
        ]
        meta_table = Table(meta_data, colWidths=meta_widths)
        meta_table.setStyle(TableStyle([
            ("FONTSIZE",     (0, 0), (-1, -1), 8),
            ("TOPPADDING",   (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING",(0, 0), (-1, -1), 4),
            ("LEFTPADDING",  (0, 0), (-1, -1), 8),
            ("VALIGN",       (0, 0), (-1, -1), "TOP"),
            ("BACKGROUND",   (0, 0), (-1, -1), ROW_LIGHT),
            ("LINEBELOW",    (0, 0), (-1, -1), 0.3, TERMINAL_GREY),
        ]))
        story.append(meta_table)

        # Location and notes (conditional)
        if ev.location_description or ev.notes:
            notes_parts = []
            if ev.location_description:
                notes_parts.append(f"<b>Location:</b> {ev.location_description}")
            if ev.notes:
                notes_parts.append(f"<b>Notes:</b> {ev.notes}")

            notes_data = [[Paragraph("  ".join(notes_parts), styles["body"])]]
            notes_table = Table(notes_data, colWidths=[usable_width])
            notes_table.setStyle(TableStyle([
                ("BACKGROUND",   (0, 0), (-1, -1), colors.HexColor("#fffbea")),
                ("FONTSIZE",     (0, 0), (-1, -1), 8),
                ("TOPPADDING",   (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING",(0, 0), (-1, -1), 4),
                ("LEFTPADDING",  (0, 0), (-1, -1), 8),
                ("LINEBELOW",    (0, 0), (-1, -1), 0.5, TERMINAL_GREY),
            ]))
            story.append(notes_table)

        story.append(Spacer(1, 2 * mm))

    return story


def _build_audit_trail(
    audit_entries: list[AuditLogEntry],
    styles: dict,
    page_width: float,
) -> list:
    """
    Build the audit trail section.

    Renders every audit entry as a row in a terminal-log style table.
    Alternating row shading, monospace timestamps and action codes.
    """
    story = []
    story.append(PageBreak())
    story.append(Paragraph("Audit Trail", styles["section_header"]))
    story.append(_hr())
    story.append(Paragraph(
        f"Complete chronological record of all events — {len(audit_entries)} entries",
        styles["caption"],
    ))
    story.append(Spacer(1, 3 * mm))

    if not audit_entries:
        story.append(Paragraph("No audit entries found for this case.", styles["body"]))
        return story

    usable_width = page_width - 40 * mm

    # Column widths — timestamp narrow, action medium, detail fills the rest
    col_widths = [
        38 * mm,   # Timestamp
        28 * mm,   # Actor name
        35 * mm,   # Action code
        usable_width - 101 * mm,  # Detail summary
    ]

    # Header row
    table_data = [[
        Paragraph("Timestamp (UTC)", styles["body_bold"]),
        Paragraph("Actor", styles["body_bold"]),
        Paragraph("Action", styles["body_bold"]),
        Paragraph("Detail", styles["body_bold"]),
    ]]

    for idx, entry in enumerate(audit_entries):
        actor = entry.actor_name or (entry.actor_id[:8] + "..." if entry.actor_id else "system")
        detail_text = _detail_summary(entry.detail)

        # Colour-code HASH_FAILED and EVIDENCE_FILE_MISSING rows
        is_failure = entry.action in {"HASH_FAILED", "EVIDENCE_FILE_MISSING"}
        is_success = entry.action == "HASH_VERIFIED"

        row = [
            Paragraph(
                f'<font name="Courier" size="7">{_fmt_ts(entry.timestamp)}</font>',
                styles["mono"],
            ),
            Paragraph(actor, styles["body"]),
            Paragraph(
                f'<font name="Courier-Bold" size="7"'
                f' color="{"#f85149" if is_failure else "#3fb950" if is_success else "#58a6ff"}">'
                f'{entry.action}</font>',
                styles["mono"],
            ),
            Paragraph(detail_text, styles["caption"]),
        ]
        table_data.append(row)

    audit_table = Table(table_data, colWidths=col_widths, repeatRows=1)

    # Build row-by-row background colours
    style_commands = [
        # Header row styling
        ("BACKGROUND",    (0, 0), (-1, 0), TERMINAL_BLACK),
        ("TEXTCOLOR",     (0, 0), (-1, 0), colors.white),
        ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE",      (0, 0), (-1, 0), 8),
        ("TOPPADDING",    (0, 0), (-1, 0), 6),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
        ("LEFTPADDING",   (0, 0), (-1, -1), 6),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 4),
        # All rows
        ("FONTSIZE",      (0, 1), (-1, -1), 7),
        ("TOPPADDING",    (0, 1), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 3),
        ("VALIGN",        (0, 0), (-1, -1), "TOP"),
        ("GRID",          (0, 0), (-1, -1), 0.25, TERMINAL_GREY),
    ]

    # Alternating row shading + highlight failure rows
    for i, entry in enumerate(audit_entries):
        row_idx = i + 1  # +1 because row 0 is the header
        is_failure = entry.action in {"HASH_FAILED", "EVIDENCE_FILE_MISSING"}
        if is_failure:
            bg = colors.HexColor("#fff0f0")
        elif i % 2 == 0:
            bg = ROW_LIGHT
        else:
            bg = ROW_DARK
        style_commands.append(("BACKGROUND", (0, row_idx), (-1, row_idx), bg))

    audit_table.setStyle(TableStyle(style_commands))
    story.append(audit_table)

    # Footer
    story.append(Spacer(1, 6 * mm))
    story.append(_hr())
    story.append(Paragraph(
        "End of audit trail. This report was generated by DFECPIMS and reflects "
        "the complete event history stored in the append-only audit log at the "
        "time of report generation. The audit log is protected by database-level "
        "triggers that prevent modification or deletion of any entry.",
        styles["caption"],
    ))

    return story


# ─── Main report generation entry point ───────────────────────────────────────

class ReportService:
    """
    Generates PDF chain-of-custody reports for forensic cases.

    Usage:
        service = ReportService(db)
        pdf_bytes = await service.generate_case_report(
            case_id="CASE-2026-0001",
            generated_by_name="Supervisor Jane",
            generated_by_id="user-uuid",
        )
        # Stream pdf_bytes as application/pdf response
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.audit = AuditService(db)

    async def generate_case_report(
        self,
        case_id: str,
        generated_by_name: str,
        generated_by_id: str,
    ) -> BytesIO:
        """
        Generate a complete chain-of-custody PDF report for a case.

        Fetches all data (case, evidence, audit log) from the database,
        builds the ReportLab story, renders to a BytesIO buffer, and
        writes a REPORT_EXPORTED audit log entry.

        Args:
            case_id:            The CASE-YYYY-NNNN identifier.
            generated_by_name:  Display name of the generating user.
            generated_by_id:    UUID of the generating user (for audit log).

        Returns:
            BytesIO buffer containing the complete PDF. Caller streams it.

        Raises:
            ValueError: If the case does not exist.
        """
        generated_at = datetime.now(timezone.utc)

        # ── Fetch all data ────────────────────────────────────────────────────

        case_service = CaseService(self.db)
        case = await case_service.get_by_id(case_id)
        if case is None:
            raise ValueError(f"Case '{case_id}' not found.")

        ev_service = EvidenceService(self.db)
        evidence_items = await ev_service.list_by_case(case_id)

        audit_query = AuditQueryService(self.db)
        audit_entries = await audit_query.get_all_for_case(
            case_id=case_id,
            ascending=True,   # Chronological for chain-of-custody
        )

        # ── Build PDF ─────────────────────────────────────────────────────────

        buffer = BytesIO()

        page_width, page_height = A4
        doc = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            leftMargin=20 * mm,
            rightMargin=20 * mm,
            topMargin=20 * mm,
            bottomMargin=20 * mm,
            title=f"Chain of Custody Report — {case_id}",
            author="DFECPIMS",
            subject=case.title,
            creator="DFECPIMS v1.0",
        )

        styles = _styles()
        story: list = []

        # Section 1: Cover page
        story.extend(_build_cover_page(
            case=case,
            generated_by=generated_by_name,
            generated_at=generated_at,
            styles=styles,
            page_width=page_width,
        ))

        # Section 2: Case metadata
        story.extend(_build_case_metadata(case=case, styles=styles))

        # Section 3: Evidence inventory
        story.extend(_build_evidence_section(
            evidence_items=evidence_items,
            styles=styles,
            page_width=page_width,
        ))

        # Section 4: Full audit trail (starts on new page)
        story.extend(_build_audit_trail(
            audit_entries=audit_entries,
            styles=styles,
            page_width=page_width,
        ))

        # Render to buffer
        doc.build(story)

        # ── Audit log entry for the export event ──────────────────────────────

        await self.audit.log(
            action="REPORT_EXPORTED",
            actor_id=generated_by_id,
            case_id=case_id,
            detail={
                "generated_by": generated_by_name,
                "generated_at": generated_at.isoformat(),
                "evidence_count": len(evidence_items),
                "audit_entry_count": len(audit_entries),
            },
        )
        await self.db.commit()

        # Rewind so the caller can read from the start
        buffer.seek(0)
        return buffer
