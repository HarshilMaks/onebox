"""Read-only view of what the background worker did with the caller's own incoming mail."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from server.pagination import InvalidPageToken
from server.schemas import PublicErrorResponse, TriageDetail, TriagePage, TriageState
from server.services.setup_google import get_current_user_info
from server.services.triage_results import TriageResultNotFound, get_triage_result, list_triage_results

router = APIRouter(prefix="/mail", tags=["mail"])


@router.get("/triage", response_model=TriagePage)
async def list_triage_endpoint(
    state: TriageState = Query(..., description="Analysis outcome to list; required."),
    limit: int = Query(20, ge=1, le=100, description="Max number of results per page"),
    page_token: str | None = Query(default=None, min_length=1, max_length=512, description="Pagination token"),
    user_info: dict = Depends(get_current_user_info),
):
    """List what the background worker did with your incoming mail, newest first, as short previews.

    Only the caller's own connected mailbox is ever returned. Summaries are written by a model
    from untrusted email content, so a sender can influence them: render them as plain text only.
    Results are kept for the configured retention period (14 days by default), so this is a
    recent-activity view, not an archive.
    """
    try:
        return await list_triage_results(
            user_info["user_id"],
            state=state.value,
            limit=limit,
            page_token=page_token,
        )
    except InvalidPageToken:
        raise HTTPException(
            status_code=422,
            detail=PublicErrorResponse(error="validation_error", detail="Invalid page token.").model_dump(),
        ) from None


@router.get("/triage/{triage_id}", response_model=TriageDetail)
async def get_triage_endpoint(
    triage_id: UUID,
    user_info: dict = Depends(get_current_user_info),
):
    """Return one of your results with the full summary (still untrusted: render as plain text)."""
    try:
        return await get_triage_result(triage_id, user_info["user_id"])
    except TriageResultNotFound:
        raise HTTPException(status_code=404, detail="Triage result not found") from None
