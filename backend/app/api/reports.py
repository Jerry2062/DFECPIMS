"""
api/reports.py

PDF Chain-of-Custody Report route handler for DFECPIMS.

Endpoint:
  GET /cases/{case_id}/report  — download PDF (supervisor only)

Returns a StreamingResponse with content-type application/pdf.
The filename in Content-Disposition uses the case ID so the downloaded
file is self-describing: DFECPIMS-CASE-2026-0001-ChainOfCustody.pdf

This endpoint is supervisor-only because chain-of-custody reports are
formal forensic documents — not something investigators should be able
to produce unilaterally. A supervisor generating the report also writes
a REPORT_EXPORTED audit log entry, which is itself forensically significant.

The PDF is rendered entirely in memory (BytesIO buffer) — no temp files.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import require_supervisor
from app.core.security import TokenPayload
from app.services.report_service import ReportService


router = APIRouter(tags=["Reports"])


@router.get(
    "/cases/{case_id}/report",
    summary="Download chain-of-custody PDF report (supervisor only)",
    response_description="PDF file download",
    status_code=status.HTTP_200_OK,
)
async def download_case_report(
    case_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: TokenPayload = Depends(require_supervisor),
) -> StreamingResponse:
    """
    Generate and download a PDF chain-of-custody report for a case.

    The report includes:
      - Cover page with case metadata and severity badge
      - Case description and key fields
      - Full evidence inventory with SHA-256 hashes (never truncated)
      - Complete audit trail in chronological order

    Generating the report writes a REPORT_EXPORTED audit log entry.
    The PDF is rendered in memory — no server-side temp files.

    Supervisor-only. The download triggers automatically in the browser.
    """
    service = ReportService(db)

    try:
        pdf_buffer = await service.generate_case_report(
            case_id=case_id,
            generated_by_name=current_user.name,
            generated_by_id=current_user.user_id,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        )
    except Exception as e:
        # ReportLab failures are rare but catch them explicitly rather than
        # returning a 500 with an opaque traceback to the client
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Report generation failed: {str(e)}",
        )

    filename = f"DFECPIMS-{case_id}-ChainOfCustody.pdf"

    return StreamingResponse(
        content=pdf_buffer,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            # Prevent the PDF from being cached — chain-of-custody
            # documents should always reflect the current DB state
            "Cache-Control": "no-store, no-cache, must-revalidate",
        },
    )
