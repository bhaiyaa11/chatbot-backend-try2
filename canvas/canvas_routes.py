"""
Canvas API routes.

Security model:

JWT
 ↓
get_current_user()
 ↓
verified user_id
 ↓
CanvasManager authorization
 ↓
Supabase
"""

from typing import Any, Dict, Optional, List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.auth import get_current_user
from canvas.canvas_manager import CanvasManager
from fastapi import Query


router = APIRouter(
    prefix="/canvas",
    tags=["Canvas"],
)

@router.get("/health")
def canvas_health():
    return {"status": "canvas api ok"}

canvas_manager = CanvasManager()

# FRONTEND_ORIGIN = "http://localhost:5173"
FRONTEND_ORIGIN = "https://chatbot-aim.vercel.app/"


# ================================================================
# Request models
# ================================================================

class CreateCanvasRequest(BaseModel):
    title: str = Field(default="Untitled Canvas", max_length=200)
    content: Optional[Dict[str, Any]] = None


class UpdateCanvasContentRequest(BaseModel):
    content: Dict[str, Any]


class UpdateCanvasTitleRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class UpdateVisibilityRequest(BaseModel):
    visibility: str = Field(pattern="^(restricted|anyone)$")


class CreateShareLinkRequest(BaseModel):
    permission: str = Field(default="viewer", pattern="^(viewer|commenter|editor)$")


class UpdateShareLinkRequest(BaseModel):
    permission: Optional[str] = Field(default=None, pattern="^(viewer|commenter|editor)$")
    link_access_enabled: Optional[bool] = None


class RegenerateShareLinkRequest(BaseModel):
    permission: Optional[str] = Field(default=None, pattern="^(viewer|commenter|editor)$")


class InviteMemberRequest(BaseModel):
    email: str
    permission: str = Field(default="viewer", pattern="^(viewer|commenter|editor)$")


class UpdateMemberRequest(BaseModel):
    permission: str = Field(pattern="^(viewer|commenter|editor)$")


class RespondAccessRequest(BaseModel):
    permission: Optional[str] = Field(default=None, pattern="^(viewer|commenter|editor)$")


class CreateCommentRequest(BaseModel):
    content: str = Field(min_length=1, max_length=4000)
    anchor_from: int
    anchor_to: int
    anchor_text: Optional[str] = None


class ResolveCommentRequest(BaseModel):
    resolved: bool = True


# ================================================================
# Create / List / Get / Update / Delete
# ================================================================

@router.post("")
def create_canvas(
    request: CreateCanvasRequest,
    user_id: str = Depends(get_current_user),
):
    try:
        canvas = canvas_manager.create_canvas(
            user_id=user_id,
            title=request.title,
            content=request.content,
        )
        return {"canvas": canvas}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to create canvas")


# @router.get("")
# def list_canvases(user_id: str = Depends(get_current_user)):
#     try:
#         canvases = canvas_manager.list_canvases(user_id=user_id)
#         return {"canvases": canvases}
#     except Exception:
#         raise HTTPException(status_code=500, detail="Failed to load canvases")

@router.get("")
def list_canvases(
    limit: int = Query(20, ge=1, le=50),
    offset: int = Query(0, ge=0),
    user_id: str = Depends(get_current_user),
):
    try:
        return canvas_manager.list_canvases(
            user_id=user_id,
            limit=limit,
            offset=offset,
        )
    except Exception:
        raise HTTPException(
            status_code=500,
            detail="Failed to load canvases",
        )

@router.get("/{canvas_id}")
def get_canvas(canvas_id: str, user_id: str = Depends(get_current_user)):
    try:
        canvas = canvas_manager.get_canvas(canvas_id=canvas_id, user_id=user_id)
        if not canvas:
            raise HTTPException(status_code=404, detail="Canvas not found")
        return {"canvas": canvas}
    except PermissionError:
        raise HTTPException(status_code=404, detail="Canvas not found")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to load canvas")


@router.get("/{canvas_id}/access-status")
def get_access_status(canvas_id: str, user_id: str = Depends(get_current_user)):
    """
    Lets the frontend show the right screen: the editor, or a
    "Request access" gate, without leaking canvas content.
    """
    try:
        status = canvas_manager.get_access_status(canvas_id=canvas_id, user_id=user_id)
        return status
    except LookupError:
        raise HTTPException(status_code=404, detail="Canvas not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to check access")


@router.patch("/{canvas_id}/content")
def update_canvas_content(
    canvas_id: str,
    request: UpdateCanvasContentRequest,
    user_id: str = Depends(get_current_user),
):
    try:
        canvas = canvas_manager.update_canvas_content(
            canvas_id=canvas_id, user_id=user_id, content=request.content,
        )
        return {"canvas": canvas}
    except PermissionError:
        raise HTTPException(status_code=403, detail="You do not have permission to edit this canvas")
    except LookupError:
        raise HTTPException(status_code=404, detail="Canvas not found")
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to update canvas")


@router.patch("/{canvas_id}/title")
def update_canvas_title(
    canvas_id: str,
    request: UpdateCanvasTitleRequest,
    user_id: str = Depends(get_current_user),
):
    try:
        canvas = canvas_manager.update_canvas_title(
            canvas_id=canvas_id, user_id=user_id, title=request.title,
        )
        return {"canvas": canvas}
    except PermissionError:
        raise HTTPException(status_code=403, detail="You do not have permission to edit this canvas")
    except LookupError:
        raise HTTPException(status_code=404, detail="Canvas not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to update canvas")


@router.delete("/{canvas_id}")
def delete_canvas(canvas_id: str, user_id: str = Depends(get_current_user)):
    try:
        canvas_manager.delete_canvas(canvas_id=canvas_id, user_id=user_id)
        return {"status": "deleted"}
    except PermissionError:
        raise HTTPException(status_code=403, detail="Only the canvas owner can delete this canvas")
    except LookupError:
        raise HTTPException(status_code=404, detail="Canvas not found")
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to delete canvas")


# ================================================================
# Visibility
# ================================================================

@router.patch("/{canvas_id}/visibility")
def update_visibility(
    canvas_id: str,
    request: UpdateVisibilityRequest,
    user_id: str = Depends(get_current_user),
):
    try:
        canvas = canvas_manager.set_visibility(
            canvas_id=canvas_id, owner_id=user_id, visibility=request.visibility,
        )
        return {"canvas": canvas}
    except PermissionError:
        raise HTTPException(status_code=403, detail="Only the canvas owner can change visibility")
    except LookupError:
        raise HTTPException(status_code=404, detail="Canvas not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to update visibility")


# ================================================================
# Public link sharing ("anyone with the link")
# ================================================================

@router.post("/{canvas_id}/share")
def create_share_link(
    canvas_id: str,
    request: CreateShareLinkRequest,
    user_id: str = Depends(get_current_user),
):
    try:
        result = canvas_manager.create_share_link(
            canvas_id=canvas_id, user_id=user_id, permission=request.permission,
        )
        return {
            "share_url": f"{FRONTEND_ORIGIN}/shared/canvas/{result['token']}",
            "permission": result["permission"],
        }
    except PermissionError:
        raise HTTPException(status_code=403, detail="You do not have permission to share this canvas")
    except LookupError:
        raise HTTPException(status_code=404, detail="Canvas not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to create share link")


@router.get("/{canvas_id}/share")
def get_share_link_settings(canvas_id: str, user_id: str = Depends(get_current_user)):
    try:
        settings = canvas_manager.get_share_link_settings(canvas_id=canvas_id, user_id=user_id)
        return {"share": settings}
    except PermissionError:
        raise HTTPException(status_code=403, detail="Only the canvas owner can manage sharing")
    except LookupError:
        raise HTTPException(status_code=404, detail="Canvas not found")
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to load share settings")


@router.patch("/{canvas_id}/share")
def update_share_link_settings(
    canvas_id: str,
    request: UpdateShareLinkRequest,
    user_id: str = Depends(get_current_user),
):
    try:
        settings = canvas_manager.update_share_link_settings(
            canvas_id=canvas_id, user_id=user_id,
            permission=request.permission,
            link_access_enabled=request.link_access_enabled,
        )
        return {"share": settings}
    except PermissionError:
        raise HTTPException(status_code=403, detail="Only the canvas owner can manage sharing")
    except LookupError:
        raise HTTPException(status_code=404, detail="Canvas not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to update share settings")


@router.delete("/{canvas_id}/share")
def revoke_share_link(canvas_id: str, user_id: str = Depends(get_current_user)):
    try:
        canvas_manager.revoke_share_link(canvas_id=canvas_id, user_id=user_id)
        return {"status": "revoked"}
    except PermissionError:
        raise HTTPException(status_code=403, detail="Only the canvas owner can revoke sharing")
    except LookupError:
        raise HTTPException(status_code=404, detail="Canvas not found")
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to revoke share link")


@router.post("/{canvas_id}/share/regenerate")
def regenerate_share_link(
    canvas_id: str,
    request: RegenerateShareLinkRequest,
    user_id: str = Depends(get_current_user),
):
    try:
        result = canvas_manager.regenerate_share_link(
            canvas_id=canvas_id, user_id=user_id, permission=request.permission,
        )
        return {
            "share_url": f"{FRONTEND_ORIGIN}/shared/canvas/{result['token']}",
            "permission": result["permission"],
        }
    except PermissionError:
        raise HTTPException(status_code=403, detail="Only the canvas owner can regenerate sharing")
    except LookupError:
        raise HTTPException(status_code=404, detail="Canvas not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to regenerate share link")


# ================================================================
# Restricted access: requests
# ================================================================

@router.post("/{canvas_id}/access-requests")
def create_access_request(canvas_id: str, user_id: str = Depends(get_current_user)):
    try:
        row = canvas_manager.request_access(canvas_id=canvas_id, user_id=user_id)
        return {"request": row}
    except LookupError:
        raise HTTPException(status_code=404, detail="Canvas not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to request access")


@router.get("/{canvas_id}/access-requests")
def list_access_requests(canvas_id: str, user_id: str = Depends(get_current_user)):
    try:
        requests_ = canvas_manager.list_access_requests(canvas_id=canvas_id, owner_id=user_id)
        return {"requests": requests_}
    except PermissionError:
        raise HTTPException(status_code=403, detail="Only the canvas owner can view access requests")
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to load access requests")


@router.post("/{canvas_id}/access-requests/{request_id}/approve")
def approve_access_request(
    canvas_id: str,
    request_id: str,
    request: RespondAccessRequest,
    user_id: str = Depends(get_current_user),
):
    try:
        row = canvas_manager.respond_to_access_request(
            canvas_id=canvas_id, owner_id=user_id, request_id=request_id,
            approve=True, permission=request.permission,
        )
        return {"request": row}
    except PermissionError:
        raise HTTPException(status_code=403, detail="Only the canvas owner can approve requests")
    except LookupError:
        raise HTTPException(status_code=404, detail="Access request not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to approve access request")


@router.post("/{canvas_id}/access-requests/{request_id}/deny")
def deny_access_request(
    canvas_id: str,
    request_id: str,
    user_id: str = Depends(get_current_user),
):
    try:
        row = canvas_manager.respond_to_access_request(
            canvas_id=canvas_id, owner_id=user_id, request_id=request_id, approve=False,
        )
        return {"request": row}
    except PermissionError:
        raise HTTPException(status_code=403, detail="Only the canvas owner can deny requests")
    except LookupError:
        raise HTTPException(status_code=404, detail="Access request not found")
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to deny access request")


# ================================================================
# Restricted access: members
# ================================================================

@router.get("/{canvas_id}/members")
def list_members(canvas_id: str, user_id: str = Depends(get_current_user)):
    try:
        members = canvas_manager.list_members(canvas_id=canvas_id, owner_id=user_id)
        return {"members": members}
    except PermissionError:
        raise HTTPException(status_code=403, detail="Only the canvas owner can view members")
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to load members")


@router.post("/{canvas_id}/members")
def invite_member(
    canvas_id: str,
    request: InviteMemberRequest,
    user_id: str = Depends(get_current_user),
):
    try:
        member = canvas_manager.invite_member(
            canvas_id=canvas_id, owner_id=user_id,
            email=request.email, permission=request.permission,
        )
        return {"member": member}
    except PermissionError:
        raise HTTPException(status_code=403, detail="Only the canvas owner can invite people")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to invite member")


@router.patch("/{canvas_id}/members/{member_id}")
def update_member(
    canvas_id: str,
    member_id: str,
    request: UpdateMemberRequest,
    user_id: str = Depends(get_current_user),
):
    try:
        member = canvas_manager.update_member_permission(
            canvas_id=canvas_id, owner_id=user_id,
            member_id=member_id, permission=request.permission,
        )
        return {"member": member}
    except PermissionError:
        raise HTTPException(status_code=403, detail="Only the canvas owner can manage members")
    except LookupError:
        raise HTTPException(status_code=404, detail="Member not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to update member")


@router.delete("/{canvas_id}/members/{member_id}")
def remove_member(canvas_id: str, member_id: str, user_id: str = Depends(get_current_user)):
    try:
        canvas_manager.remove_member(canvas_id=canvas_id, owner_id=user_id, member_id=member_id)
        return {"status": "removed"}
    except PermissionError:
        raise HTTPException(status_code=403, detail="Only the canvas owner can remove members")
    except LookupError:
        raise HTTPException(status_code=404, detail="Member not found")
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to remove member")


# ================================================================
# Comments
# ================================================================

@router.get("/{canvas_id}/comments")
def list_comments(canvas_id: str, user_id: str = Depends(get_current_user)):
    try:
        comments = canvas_manager.list_comments(canvas_id=canvas_id, user_id=user_id)
        return {"comments": comments}
    except PermissionError:
        raise HTTPException(status_code=403, detail="You do not have access to this canvas")
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to load comments")


@router.post("/{canvas_id}/comments")
def create_comment(
    canvas_id: str,
    request: CreateCommentRequest,
    user_id: str = Depends(get_current_user),
):
    try:
        comment = canvas_manager.create_comment(
            canvas_id=canvas_id, user_id=user_id, content=request.content,
            anchor_from=request.anchor_from, anchor_to=request.anchor_to,
            anchor_text=request.anchor_text,
        )
        return {"comment": comment}
    except PermissionError:
        raise HTTPException(status_code=403, detail="You do not have comment access to this canvas")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to create comment")


@router.patch("/{canvas_id}/comments/{comment_id}/resolve")
def resolve_comment(
    canvas_id: str,
    comment_id: str,
    request: ResolveCommentRequest,
    user_id: str = Depends(get_current_user),
):
    try:
        comment = canvas_manager.resolve_comment(
            canvas_id=canvas_id, user_id=user_id,
            comment_id=comment_id, resolved=request.resolved,
        )
        return {"comment": comment}
    except PermissionError:
        raise HTTPException(status_code=403, detail="You cannot resolve comments on this canvas")
    except LookupError:
        raise HTTPException(status_code=404, detail="Comment not found")
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to update comment")


@router.delete("/{canvas_id}/comments/{comment_id}")
def delete_comment(canvas_id: str, comment_id: str, user_id: str = Depends(get_current_user)):
    try:
        canvas_manager.delete_comment(canvas_id=canvas_id, user_id=user_id, comment_id=comment_id)
        return {"status": "deleted"}
    except PermissionError:
        raise HTTPException(status_code=403, detail="You cannot delete this comment")
    except LookupError:
        raise HTTPException(status_code=404, detail="Comment not found")
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to delete comment")


# ================================================================
# Public (unauthenticated, token-based) routes
# ================================================================

public_router = APIRouter(
    prefix="/shared/canvas",
    tags=["Public Canvas"],
)

@public_router.get("/{token}")
def get_public_canvas(token: str):
    try:
        canvas = canvas_manager.get_public_canvas(token)
        if not canvas:
            raise HTTPException(status_code=404, detail="Canvas not found")
        return {"canvas": canvas}
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to load canvas")

@public_router.patch("/{token}/content")
def update_public_canvas_content(token: str, request: UpdateCanvasContentRequest):
    try:
        canvas = canvas_manager.update_public_canvas_content(token=token, content=request.content)
        if not canvas:
            raise HTTPException(status_code=404, detail="Canvas not found")
        return {"canvas": canvas}
    except PermissionError:
        raise HTTPException(status_code=403, detail="You do not have permission to edit this canvas")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to update canvas")