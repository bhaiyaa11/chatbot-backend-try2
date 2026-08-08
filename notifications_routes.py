"""
Notifications API routes.

Covers step 4/6 of the Canvas flow: the writer gets notified when
someone requests access, and when they approve/deny it the requester
gets notified back.

Mount this in main.py alongside the canvas routers:

    from notifications_routes import router as notifications_router
    app.include_router(notifications_router)
"""

from fastapi import APIRouter, Depends, HTTPException, Query

from api.auth import get_current_user
from canvas.canvas_manager import CanvasManager


router = APIRouter(
    prefix="/notifications",
    tags=["Notifications"],
)

canvas_manager = CanvasManager()


@router.get("")
def list_notifications(
    unread_only: bool = Query(False),
    user_id: str = Depends(get_current_user),
):
    try:
        notifications = canvas_manager.list_notifications(
            user_id=user_id,
            unread_only=unread_only,
        )
        return {"notifications": notifications}
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to load notifications")


@router.post("/{notification_id}/read")
def mark_notification_read(
    notification_id: str,
    user_id: str = Depends(get_current_user),
):
    try:
        notification = canvas_manager.mark_notification_read(
            user_id=user_id,
            notification_id=notification_id,
        )
        return {"notification": notification}
    except LookupError:
        raise HTTPException(status_code=404, detail="Notification not found")
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to update notification")