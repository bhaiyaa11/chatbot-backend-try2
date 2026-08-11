# """
# Canvas API routes.

# Security model:

# JWT
#  ↓
# get_current_user()
#  ↓
# verified user_id
#  ↓
# CanvasManager authorization
#  ↓
# Supabase
# """

# import os
# from typing import Any, Dict, Optional, List

# from fastapi import APIRouter, Depends, HTTPException, Request
# from pydantic import BaseModel, Field
# from slowapi import Limiter
# from slowapi.util import get_remote_address
# import logging

# from api.auth import get_current_user
# from canvas.canvas_manager import CanvasManager

# logger = logging.getLogger(__name__)


# router = APIRouter(
#     prefix="/canvas",
#     tags=["Canvas"],
# )

# @router.get("/health")
# def canvas_health():
#     return {"status": "canvas api ok"}

# canvas_manager = CanvasManager()

# FRONTEND_ORIGIN = "http://localhost:5173"
# # FRONTEND_ORIGIN = "https://chatbot-aim.vercel.app/"

# # Rate limiting for the *unauthenticated* public-link endpoints below.
# # These have no JWT, so there's no per-user identity to hold
# # accountable — IP-based limiting is the baseline defense against
# # token-guessing, comment spam, and scraping. Authenticated endpoints
# # above this line are already accountable per-user via the JWT, so
# # they're not limited here (add limits there too if you want defense
# # in depth against a compromised/malicious authenticated account).
# #
# # Wire this into main.py:
# #   from canvas.canvas_routes import limiter
# #   app.state.limiter = limiter
# #   app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
# #   app.add_middleware(SlowAPIMiddleware)
# limiter = Limiter(key_func=get_remote_address)


# # ================================================================
# # Request models
# # ================================================================

# class CreateCanvasRequest(BaseModel):
#     title: str = Field(default="Untitled Canvas", max_length=200)
#     content: Optional[Dict[str, Any]] = None


# class UpdateCanvasContentRequest(BaseModel):
#     content: Dict[str, Any]


# class UpdateCanvasTitleRequest(BaseModel):
#     title: str = Field(min_length=1, max_length=200)


# class UpdateVisibilityRequest(BaseModel):
#     visibility: str = Field(pattern="^(restricted|anyone)$")


# class CreateShareLinkRequest(BaseModel):
#     permission: str = Field(default="viewer", pattern="^(viewer|commenter|editor)$")


# class UpdateShareLinkRequest(BaseModel):
#     permission: Optional[str] = Field(default=None, pattern="^(viewer|commenter|editor)$")
#     link_access_enabled: Optional[bool] = None


# class RegenerateShareLinkRequest(BaseModel):
#     permission: Optional[str] = Field(default=None, pattern="^(viewer|commenter|editor)$")


# class InviteMemberRequest(BaseModel):
#     email: str
#     permission: str = Field(default="viewer", pattern="^(viewer|commenter|editor)$")


# class UpdateMemberRequest(BaseModel):
#     permission: str = Field(pattern="^(viewer|commenter|editor)$")


# class RespondAccessRequest(BaseModel):
#     permission: Optional[str] = Field(default=None, pattern="^(viewer|commenter|editor)$")


# class CreateCommentRequest(BaseModel):
#     content: str = Field(min_length=1, max_length=4000)
#     anchor_from: int
#     anchor_to: int
#     anchor_text: Optional[str] = None


# class ResolveCommentRequest(BaseModel):
#     resolved: bool = True


# # ================================================================
# # Create / List / Get / Update / Delete
# # ================================================================

# @router.post("")
# def create_canvas(
#     request: CreateCanvasRequest,
#     user_id: str = Depends(get_current_user),
# ):
#     try:
#         canvas = canvas_manager.create_canvas(
#             user_id=user_id,
#             title=request.title,
#             content=request.content,
#         )
#         return {"canvas": canvas}
#     except ValueError as exc:
#         raise HTTPException(status_code=400, detail=str(exc))
#     except Exception:
#         logger.exception("Failed to create canvas")
#         raise HTTPException(status_code=500, detail="Failed to create canvas")


# @router.get("")
# def list_canvases(user_id: str = Depends(get_current_user)):
#     try:
#         canvases = canvas_manager.list_canvases(user_id=user_id)
#         return {"canvases": canvases}
#     except Exception:
#         logger.exception("Failed to load canvases")
#         raise HTTPException(status_code=500, detail="Failed to load canvases")


# @router.get("/{canvas_id}")
# def get_canvas(canvas_id: str, user_id: str = Depends(get_current_user)):
#     try:
#         canvas = canvas_manager.get_canvas(canvas_id=canvas_id, user_id=user_id)
#         if not canvas:
#             raise HTTPException(status_code=404, detail="Canvas not found")
#         return {"canvas": canvas}
#     except PermissionError:
#         raise HTTPException(status_code=404, detail="Canvas not found")
#     except HTTPException:
#         raise
#     except Exception:
#         logger.exception("Failed to load canvas")
#         raise HTTPException(status_code=500, detail="Failed to load canvas")


# @router.get("/{canvas_id}/access-status")
# def get_access_status(canvas_id: str, user_id: str = Depends(get_current_user)):
#     """
#     Lets the frontend show the right screen: the editor, or a
#     "Request access" gate, without leaking canvas content.
#     """
#     try:
#         status = canvas_manager.get_access_status(canvas_id=canvas_id, user_id=user_id)
#         return status
#     except LookupError:
#         raise HTTPException(status_code=404, detail="Canvas not found")
#     except ValueError as exc:
#         raise HTTPException(status_code=400, detail=str(exc))
#     except Exception:
#         logger.exception("Failed to check access")
#         raise HTTPException(status_code=500, detail="Failed to check access")


# @router.patch("/{canvas_id}/content")
# def update_canvas_content(
#     canvas_id: str,
#     request: UpdateCanvasContentRequest,
#     user_id: str = Depends(get_current_user),
# ):
#     try:
#         canvas = canvas_manager.update_canvas_content(
#             canvas_id=canvas_id, user_id=user_id, content=request.content,
#         )
#         return {"canvas": canvas}
#     except PermissionError:
#         raise HTTPException(status_code=403, detail="You do not have permission to edit this canvas")
#     except LookupError:
#         raise HTTPException(status_code=404, detail="Canvas not found")
#     except Exception:
#         logger.exception("Failed to update canvas")
#         raise HTTPException(status_code=500, detail="Failed to update canvas")


# @router.patch("/{canvas_id}/title")
# def update_canvas_title(
#     canvas_id: str,
#     request: UpdateCanvasTitleRequest,
#     user_id: str = Depends(get_current_user),
# ):
#     try:
#         canvas = canvas_manager.update_canvas_title(
#             canvas_id=canvas_id, user_id=user_id, title=request.title,
#         )
#         return {"canvas": canvas}
#     except PermissionError:
#         raise HTTPException(status_code=403, detail="You do not have permission to edit this canvas")
#     except LookupError:
#         raise HTTPException(status_code=404, detail="Canvas not found")
#     except ValueError as exc:
#         raise HTTPException(status_code=400, detail=str(exc))
#     except Exception:
#         logger.exception("Failed to update canvas")
#         raise HTTPException(status_code=500, detail="Failed to update canvas")


# @router.delete("/{canvas_id}")
# def delete_canvas(canvas_id: str, user_id: str = Depends(get_current_user)):
#     try:
#         canvas_manager.delete_canvas(canvas_id=canvas_id, user_id=user_id)
#         return {"status": "deleted"}
#     except PermissionError:
#         raise HTTPException(status_code=403, detail="Only the canvas owner can delete this canvas")
#     except LookupError:
#         raise HTTPException(status_code=404, detail="Canvas not found")
#     except Exception:
#         logger.exception("Failed to delete canvas")
#         raise HTTPException(status_code=500, detail="Failed to delete canvas")


# # ================================================================
# # Visibility
# # ================================================================

# @router.patch("/{canvas_id}/visibility")
# def update_visibility(
#     canvas_id: str,
#     request: UpdateVisibilityRequest,
#     user_id: str = Depends(get_current_user),
# ):
#     try:
#         canvas = canvas_manager.set_visibility(
#             canvas_id=canvas_id, owner_id=user_id, visibility=request.visibility,
#         )
#         return {"canvas": canvas}
#     except PermissionError:
#         raise HTTPException(status_code=403, detail="Only the canvas owner can change visibility")
#     except LookupError:
#         raise HTTPException(status_code=404, detail="Canvas not found")
#     except ValueError as exc:
#         raise HTTPException(status_code=400, detail=str(exc))
#     except Exception:
#         logger.exception("Failed to update visibility")
#         raise HTTPException(status_code=500, detail="Failed to update visibility")


# # ================================================================
# # Public link sharing ("anyone with the link")
# # ================================================================

# @router.post("/{canvas_id}/share")
# def create_share_link(
#     canvas_id: str,
#     request: CreateShareLinkRequest,
#     user_id: str = Depends(get_current_user),
# ):
#     try:
#         result = canvas_manager.create_share_link(
#             canvas_id=canvas_id, user_id=user_id, permission=request.permission,
#         )
#         return {
#             "share_url": f"{FRONTEND_ORIGIN}/shared/canvas/{result['token']}",
#             "permission": result["permission"],
#         }
#     except PermissionError:
#         raise HTTPException(status_code=403, detail="You do not have permission to share this canvas")
#     except LookupError:
#         raise HTTPException(status_code=404, detail="Canvas not found")
#     except ValueError as exc:
#         raise HTTPException(status_code=400, detail=str(exc))
#     except Exception:
#         logger.exception("Failed to create share link")
#         raise HTTPException(status_code=500, detail="Failed to create share link")


# @router.get("/{canvas_id}/share")
# def get_share_link_settings(canvas_id: str, user_id: str = Depends(get_current_user)):
#     try:
#         settings = canvas_manager.get_share_link_settings(canvas_id=canvas_id, user_id=user_id)
#         return {"share": settings}
#     except PermissionError:
#         raise HTTPException(status_code=403, detail="Only the canvas owner can manage sharing")
#     except LookupError:
#         raise HTTPException(status_code=404, detail="Canvas not found")
#     except Exception:
#         logger.exception("Failed to load share settings")
#         raise HTTPException(status_code=500, detail="Failed to load share settings")


# @router.patch("/{canvas_id}/share")
# def update_share_link_settings(
#     canvas_id: str,
#     request: UpdateShareLinkRequest,
#     user_id: str = Depends(get_current_user),
# ):
#     try:
#         settings = canvas_manager.update_share_link_settings(
#             canvas_id=canvas_id, user_id=user_id,
#             permission=request.permission,
#             link_access_enabled=request.link_access_enabled,
#         )
#         return {"share": settings}
#     except PermissionError:
#         raise HTTPException(status_code=403, detail="Only the canvas owner can manage sharing")
#     except LookupError:
#         raise HTTPException(status_code=404, detail="Canvas not found")
#     except ValueError as exc:
#         raise HTTPException(status_code=400, detail=str(exc))
#     except Exception:
#         logger.exception("Failed to update share settings")
#         raise HTTPException(status_code=500, detail="Failed to update share settings")


# @router.delete("/{canvas_id}/share")
# def revoke_share_link(canvas_id: str, user_id: str = Depends(get_current_user)):
#     try:
#         canvas_manager.revoke_share_link(canvas_id=canvas_id, user_id=user_id)
#         return {"status": "revoked"}
#     except PermissionError:
#         raise HTTPException(status_code=403, detail="Only the canvas owner can revoke sharing")
#     except LookupError:
#         raise HTTPException(status_code=404, detail="Canvas not found")
#     except Exception:
#         logger.exception("Failed to revoke share link")
#         raise HTTPException(status_code=500, detail="Failed to revoke share link")


# @router.post("/{canvas_id}/share/regenerate")
# def regenerate_share_link(
#     canvas_id: str,
#     request: RegenerateShareLinkRequest,
#     user_id: str = Depends(get_current_user),
# ):
#     try:
#         result = canvas_manager.regenerate_share_link(
#             canvas_id=canvas_id, user_id=user_id, permission=request.permission,
#         )
#         return {
#             "share_url": f"{FRONTEND_ORIGIN}/shared/canvas/{result['token']}",
#             "permission": result["permission"],
#         }
#     except PermissionError:
#         raise HTTPException(status_code=403, detail="Only the canvas owner can regenerate sharing")
#     except LookupError:
#         raise HTTPException(status_code=404, detail="Canvas not found")
#     except ValueError as exc:
#         raise HTTPException(status_code=400, detail=str(exc))
#     except Exception:
#         logger.exception("Failed to regenerate share link")
#         raise HTTPException(status_code=500, detail="Failed to regenerate share link")


# # ================================================================
# # Restricted access: requests
# # ================================================================

# @router.post("/{canvas_id}/access-requests")
# def create_access_request(canvas_id: str, user_id: str = Depends(get_current_user)):
#     try:
#         row = canvas_manager.request_access(canvas_id=canvas_id, user_id=user_id)
#         return {"request": row}
#     except LookupError:
#         raise HTTPException(status_code=404, detail="Canvas not found")
#     except ValueError as exc:
#         raise HTTPException(status_code=400, detail=str(exc))
#     except Exception:
#         logger.exception("Failed to request access")
#         raise HTTPException(status_code=500, detail="Failed to request access")


# @router.get("/{canvas_id}/access-requests")
# def list_access_requests(canvas_id: str, user_id: str = Depends(get_current_user)):
#     try:
#         requests_ = canvas_manager.list_access_requests(canvas_id=canvas_id, owner_id=user_id)
#         return {"requests": requests_}
#     except PermissionError:
#         raise HTTPException(status_code=403, detail="Only the canvas owner can view access requests")
#     except Exception:
#         logger.exception("Failed to load access requests")
#         raise HTTPException(status_code=500, detail="Failed to load access requests")


# @router.post("/{canvas_id}/access-requests/{request_id}/approve")
# def approve_access_request(
#     canvas_id: str,
#     request_id: str,
#     request: RespondAccessRequest,
#     user_id: str = Depends(get_current_user),
# ):
#     try:
#         row = canvas_manager.respond_to_access_request(
#             canvas_id=canvas_id, owner_id=user_id, request_id=request_id,
#             approve=True, permission=request.permission,
#         )
#         return {"request": row}
#     except PermissionError:
#         raise HTTPException(status_code=403, detail="Only the canvas owner can approve requests")
#     except LookupError:
#         raise HTTPException(status_code=404, detail="Access request not found")
#     except ValueError as exc:
#         raise HTTPException(status_code=400, detail=str(exc))
#     except Exception:
#         logger.exception("Failed to approve access request")
#         raise HTTPException(status_code=500, detail="Failed to approve access request")


# @router.post("/{canvas_id}/access-requests/{request_id}/deny")
# def deny_access_request(
#     canvas_id: str,
#     request_id: str,
#     user_id: str = Depends(get_current_user),
# ):
#     try:
#         row = canvas_manager.respond_to_access_request(
#             canvas_id=canvas_id, owner_id=user_id, request_id=request_id, approve=False,
#         )
#         return {"request": row}
#     except PermissionError:
#         raise HTTPException(status_code=403, detail="Only the canvas owner can deny requests")
#     except LookupError:
#         raise HTTPException(status_code=404, detail="Access request not found")
#     except Exception:
#         logger.exception("Failed to deny access request")
#         raise HTTPException(status_code=500, detail="Failed to deny access request")


# # ================================================================
# # Restricted access: members
# # ================================================================

# @router.get("/{canvas_id}/members")
# def list_members(canvas_id: str, user_id: str = Depends(get_current_user)):
#     try:
#         members = canvas_manager.list_members(canvas_id=canvas_id, owner_id=user_id)
#         return {"members": members}
#     except PermissionError:
#         raise HTTPException(status_code=403, detail="Only the canvas owner can view members")
#     except Exception:
#         logger.exception("Failed to load members")
#         raise HTTPException(status_code=500, detail="Failed to load members")


# @router.post("/{canvas_id}/members")
# def invite_member(
#     canvas_id: str,
#     request: InviteMemberRequest,
#     user_id: str = Depends(get_current_user),
# ):
#     try:
#         member = canvas_manager.invite_member(
#             canvas_id=canvas_id, owner_id=user_id,
#             email=request.email, permission=request.permission,
#         )
#         return {"member": member}
#     except PermissionError:
#         raise HTTPException(status_code=403, detail="Only the canvas owner can invite people")
#     except ValueError as exc:
#         raise HTTPException(status_code=400, detail=str(exc))
#     except Exception:
#         logger.exception("Failed to invite member")
#         raise HTTPException(status_code=500, detail="Failed to invite member")


# @router.patch("/{canvas_id}/members/{member_id}")
# def update_member(
#     canvas_id: str,
#     member_id: str,
#     request: UpdateMemberRequest,
#     user_id: str = Depends(get_current_user),
# ):
#     try:
#         member = canvas_manager.update_member_permission(
#             canvas_id=canvas_id, owner_id=user_id,
#             member_id=member_id, permission=request.permission,
#         )
#         return {"member": member}
#     except PermissionError:
#         raise HTTPException(status_code=403, detail="Only the canvas owner can manage members")
#     except LookupError:
#         raise HTTPException(status_code=404, detail="Member not found")
#     except ValueError as exc:
#         raise HTTPException(status_code=400, detail=str(exc))
#     except Exception:
#         logger.exception("Failed to update member")
#         raise HTTPException(status_code=500, detail="Failed to update member")


# @router.delete("/{canvas_id}/members/{member_id}")
# def remove_member(canvas_id: str, member_id: str, user_id: str = Depends(get_current_user)):
#     try:
#         canvas_manager.remove_member(canvas_id=canvas_id, owner_id=user_id, member_id=member_id)
#         return {"status": "removed"}
#     except PermissionError:
#         raise HTTPException(status_code=403, detail="Only the canvas owner can remove members")
#     except LookupError:
#         raise HTTPException(status_code=404, detail="Member not found")
#     except Exception:
#         logger.exception("Failed to remove member")
#         raise HTTPException(status_code=500, detail="Failed to remove member")


# @router.get("/{canvas_id}/invites")
# def list_pending_invites(canvas_id: str, user_id: str = Depends(get_current_user)):
#     try:
#         invites = canvas_manager.list_pending_invites(canvas_id=canvas_id, owner_id=user_id)
#         return {"invites": invites}
#     except PermissionError:
#         raise HTTPException(status_code=403, detail="Only the canvas owner can view invites")
#     except Exception:
#         logger.exception("Failed to load invites")
#         raise HTTPException(status_code=500, detail="Failed to load invites")


# @router.delete("/{canvas_id}/invites/{invite_id}")
# def revoke_invite(canvas_id: str, invite_id: str, user_id: str = Depends(get_current_user)):
#     try:
#         canvas_manager.revoke_invite(canvas_id=canvas_id, owner_id=user_id, invite_id=invite_id)
#         return {"status": "revoked"}
#     except PermissionError:
#         raise HTTPException(status_code=403, detail="Only the canvas owner can revoke invites")
#     except LookupError:
#         raise HTTPException(status_code=404, detail="Invite not found")
#     except Exception:
#         logger.exception("Failed to revoke invite")
#         raise HTTPException(status_code=500, detail="Failed to revoke invite")


# # ================================================================
# # Comments
# # ================================================================

# @router.get("/{canvas_id}/comments")
# def list_comments(canvas_id: str, user_id: str = Depends(get_current_user)):
#     try:
#         comments = canvas_manager.list_comments(canvas_id=canvas_id, user_id=user_id)
#         return {"comments": comments}
#     except PermissionError:
#         raise HTTPException(status_code=403, detail="You do not have access to this canvas")
#     except Exception:
#         logger.exception("Failed to load comments")
#         raise HTTPException(status_code=500, detail="Failed to load comments")


# @router.post("/{canvas_id}/comments")
# def create_comment(
#     canvas_id: str,
#     request: CreateCommentRequest,
#     user_id: str = Depends(get_current_user),
# ):
#     try:
#         comment = canvas_manager.create_comment(
#             canvas_id=canvas_id, user_id=user_id, content=request.content,
#             anchor_from=request.anchor_from, anchor_to=request.anchor_to,
#             anchor_text=request.anchor_text,
#         )
#         return {"comment": comment}
#     except PermissionError:
#         raise HTTPException(status_code=403, detail="You do not have comment access to this canvas")
#     except ValueError as exc:
#         raise HTTPException(status_code=400, detail=str(exc))
#     except Exception:
#         logger.exception("Failed to create comment")
#         raise HTTPException(status_code=500, detail="Failed to create comment")


# @router.patch("/{canvas_id}/comments/{comment_id}/resolve")
# def resolve_comment(
#     canvas_id: str,
#     comment_id: str,
#     request: ResolveCommentRequest,
#     user_id: str = Depends(get_current_user),
# ):
#     try:
#         comment = canvas_manager.resolve_comment(
#             canvas_id=canvas_id, user_id=user_id,
#             comment_id=comment_id, resolved=request.resolved,
#         )
#         return {"comment": comment}
#     except PermissionError:
#         raise HTTPException(status_code=403, detail="You cannot resolve comments on this canvas")
#     except LookupError:
#         raise HTTPException(status_code=404, detail="Comment not found")
#     except Exception:
#         logger.exception("Failed to update comment")
#         raise HTTPException(status_code=500, detail="Failed to update comment")


# @router.delete("/{canvas_id}/comments/{comment_id}")
# def delete_comment(canvas_id: str, comment_id: str, user_id: str = Depends(get_current_user)):
#     try:
#         canvas_manager.delete_comment(canvas_id=canvas_id, user_id=user_id, comment_id=comment_id)
#         return {"status": "deleted"}
#     except PermissionError:
#         raise HTTPException(status_code=403, detail="You cannot delete this comment")
#     except LookupError:
#         raise HTTPException(status_code=404, detail="Comment not found")
#     except Exception:
#         logger.exception("Failed to delete comment")
#         raise HTTPException(status_code=500, detail="Failed to delete comment")


# # ================================================================
# # Public (unauthenticated, token-based) routes
# # ================================================================

# class CreateGuestCommentRequest(BaseModel):
#     guest_name: str = Field(default="Guest", max_length=80)
#     content: str = Field(min_length=1, max_length=4000)
#     anchor_from: int
#     anchor_to: int
#     anchor_text: Optional[str] = None


# public_router = APIRouter(
#     prefix="/shared/canvas",
#     tags=["Public Canvas"],
# )

# @public_router.get("/{token}")
# @limiter.limit("60/minute")
# def get_public_canvas(request: Request, token: str):
#     try:
#         canvas = canvas_manager.get_public_canvas(token)
#         if not canvas:
#             raise HTTPException(status_code=404, detail="Canvas not found")
#         return {"canvas": canvas}
#     except HTTPException:
#         raise
#     except Exception:
#         logger.exception("Failed to load canvas")
#         raise HTTPException(status_code=500, detail="Failed to load canvas")

# @public_router.patch("/{token}/content")
# @limiter.limit("60/minute")
# def update_public_canvas_content(request: Request, token: str, request_body: UpdateCanvasContentRequest):
#     try:
#         canvas = canvas_manager.update_public_canvas_content(token=token, content=request_body.content)
#         if not canvas:
#             raise HTTPException(status_code=404, detail="Canvas not found")
#         return {"canvas": canvas}
#     except PermissionError:
#         raise HTTPException(status_code=403, detail="You do not have permission to edit this canvas")
#     except HTTPException:
#         raise
#     except Exception:
#         logger.exception("Failed to update canvas")
#         raise HTTPException(status_code=500, detail="Failed to update canvas")


# @public_router.get("/{token}/comments")
# @limiter.limit("60/minute")
# def list_public_comments(request: Request, token: str):
#     try:
#         comments = canvas_manager.list_public_comments(token)
#         return {"comments": comments}
#     except Exception:
#         logger.exception("Failed to load comments")
#         raise HTTPException(status_code=500, detail="Failed to load comments")


# @public_router.post("/{token}/comments")
# @limiter.limit("10/minute")
# def create_public_comment(request: Request, token: str, request_body: CreateGuestCommentRequest):
#     try:
#         comment = canvas_manager.create_guest_comment(
#             token=token,
#             guest_name=request_body.guest_name,
#             content=request_body.content,
#             anchor_from=request_body.anchor_from,
#             anchor_to=request_body.anchor_to,
#             anchor_text=request_body.anchor_text,
#         )
#         if not comment:
#             raise HTTPException(status_code=404, detail="Canvas not found")
#         return {"comment": comment}
#     except PermissionError:
#         raise HTTPException(status_code=403, detail="This link does not allow comments")
#     except ValueError as exc:
#         raise HTTPException(status_code=400, detail=str(exc))
#     except HTTPException:
#         raise
#     except Exception:
#         logger.exception("Failed to create comment")
#         raise HTTPException(status_code=500, detail="Failed to create comment")











































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

import os
from typing import Any, Dict, Optional, List

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from slowapi import Limiter
from slowapi.util import get_remote_address
import logging

from api.auth import get_current_user
from canvas.canvas_manager import CanvasManager

logger = logging.getLogger(__name__)


router = APIRouter(
    prefix="/canvas",
    tags=["Canvas"],
)

# Bump this string every time this file (or canvas_manager.py) changes.
# GET /canvas/health returns it — the single source of truth for
# "is the code I'm looking at actually what's running on the server
# right now." Check this BEFORE debugging any canvas behavior.
CANVAS_API_VERSION = "2026-08-09-restricted-access-fixes-2"

@router.get("/health")
def canvas_health():
    return {
        "status": "canvas api ok",
        "version": CANVAS_API_VERSION,
        "has_guest_comments": True,
        "has_canvas_invites": True,
        "has_rate_limiting": True,
    }

canvas_manager = CanvasManager()

# FRONTEND_ORIGIN = "http://localhost:5173"
FRONTEND_ORIGIN = "https://chatbot-aim.vercel.app/"

# Rate limiting for the *unauthenticated* public-link endpoints below.
# These have no JWT, so there's no per-user identity to hold
# accountable — IP-based limiting is the baseline defense against
# token-guessing, comment spam, and scraping. Authenticated endpoints
# above this line are already accountable per-user via the JWT, so
# they're not limited here (add limits there too if you want defense
# in depth against a compromised/malicious authenticated account).
#
# Wire this into main.py:
#   from canvas.canvas_routes import limiter
#   app.state.limiter = limiter
#   app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
#   app.add_middleware(SlowAPIMiddleware)
limiter = Limiter(key_func=get_remote_address)


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
        logger.exception("Failed to create canvas")
        raise HTTPException(status_code=500, detail="Failed to create canvas")


@router.get("")
def list_canvases(user_id: str = Depends(get_current_user)):
    try:
        canvases = canvas_manager.list_canvases(user_id=user_id)
        return {"canvases": canvases}
    except Exception:
        logger.exception("Failed to load canvases")
        raise HTTPException(status_code=500, detail="Failed to load canvases")


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
        logger.exception("Failed to load canvas")
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
        logger.exception("Failed to check access")
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
        logger.exception("Failed to update canvas")
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
        logger.exception("Failed to update canvas")
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
        logger.exception("Failed to delete canvas")
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
        logger.exception("Failed to update visibility")
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
        logger.exception("Failed to create share link")
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
        logger.exception("Failed to load share settings")
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
        logger.exception("Failed to update share settings")
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
        logger.exception("Failed to revoke share link")
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
        logger.exception("Failed to regenerate share link")
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
        logger.exception("Failed to request access")
        raise HTTPException(status_code=500, detail="Failed to request access")


@router.get("/{canvas_id}/access-requests")
def list_access_requests(canvas_id: str, user_id: str = Depends(get_current_user)):
    try:
        requests_ = canvas_manager.list_access_requests(canvas_id=canvas_id, owner_id=user_id)
        return {"requests": requests_}
    except PermissionError:
        raise HTTPException(status_code=403, detail="Only the canvas owner can view access requests")
    except Exception:
        logger.exception("Failed to load access requests")
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
        logger.exception("Failed to approve access request")
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
        logger.exception("Failed to deny access request")
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
        logger.exception("Failed to load members")
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
        logger.exception("Failed to invite member")
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
        logger.exception("Failed to update member")
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
        logger.exception("Failed to remove member")
        raise HTTPException(status_code=500, detail="Failed to remove member")


@router.get("/{canvas_id}/invites")
def list_pending_invites(canvas_id: str, user_id: str = Depends(get_current_user)):
    try:
        invites = canvas_manager.list_pending_invites(canvas_id=canvas_id, owner_id=user_id)
        return {"invites": invites}
    except PermissionError:
        raise HTTPException(status_code=403, detail="Only the canvas owner can view invites")
    except Exception:
        logger.exception("Failed to load invites")
        raise HTTPException(status_code=500, detail="Failed to load invites")


@router.delete("/{canvas_id}/invites/{invite_id}")
def revoke_invite(canvas_id: str, invite_id: str, user_id: str = Depends(get_current_user)):
    try:
        canvas_manager.revoke_invite(canvas_id=canvas_id, owner_id=user_id, invite_id=invite_id)
        return {"status": "revoked"}
    except PermissionError:
        raise HTTPException(status_code=403, detail="Only the canvas owner can revoke invites")
    except LookupError:
        raise HTTPException(status_code=404, detail="Invite not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception:
        logger.exception("Failed to revoke invite")
        raise HTTPException(status_code=500, detail="Failed to revoke invite")


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
        logger.exception("Failed to load comments")
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
        logger.exception("Failed to create comment")
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
        logger.exception("Failed to update comment")
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
        logger.exception("Failed to delete comment")
        raise HTTPException(status_code=500, detail="Failed to delete comment")


# ================================================================
# Public (unauthenticated, token-based) routes
# ================================================================

class CreateGuestCommentRequest(BaseModel):
    guest_name: str = Field(default="Guest", max_length=80)
    content: str = Field(min_length=1, max_length=4000)
    anchor_from: int
    anchor_to: int
    anchor_text: Optional[str] = None


public_router = APIRouter(
    prefix="/shared/canvas",
    tags=["Public Canvas"],
)

@public_router.get("/{token}")
@limiter.limit("60/minute")
def get_public_canvas(request: Request, token: str):
    try:
        canvas = canvas_manager.get_public_canvas(token)
        if not canvas:
            raise HTTPException(status_code=404, detail="Canvas not found")
        return {"canvas": canvas}
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to load canvas")
        raise HTTPException(status_code=500, detail="Failed to load canvas")

@public_router.patch("/{token}/content")
@limiter.limit("60/minute")
def update_public_canvas_content(request: Request, token: str, request_body: UpdateCanvasContentRequest):
    try:
        canvas = canvas_manager.update_public_canvas_content(token=token, content=request_body.content)
        if not canvas:
            raise HTTPException(status_code=404, detail="Canvas not found")
        return {"canvas": canvas}
    except PermissionError:
        raise HTTPException(status_code=403, detail="You do not have permission to edit this canvas")
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to update canvas")
        raise HTTPException(status_code=500, detail="Failed to update canvas")


@public_router.get("/{token}/comments")
@limiter.limit("60/minute")
def list_public_comments(request: Request, token: str):
    try:
        comments = canvas_manager.list_public_comments(token)
        return {"comments": comments}
    except Exception:
        logger.exception("Failed to load comments")
        raise HTTPException(status_code=500, detail="Failed to load comments")


@public_router.post("/{token}/comments")
@limiter.limit("10/minute")
def create_public_comment(request: Request, token: str, request_body: CreateGuestCommentRequest):
    try:
        comment = canvas_manager.create_guest_comment(
            token=token,
            guest_name=request_body.guest_name,
            content=request_body.content,
            anchor_from=request_body.anchor_from,
            anchor_to=request_body.anchor_to,
            anchor_text=request_body.anchor_text,
        )
        if not comment:
            raise HTTPException(status_code=404, detail="Canvas not found")
        return {"comment": comment}
    except PermissionError:
        raise HTTPException(status_code=403, detail="This link does not allow comments")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to create comment")
        raise HTTPException(status_code=500, detail="Failed to create comment")