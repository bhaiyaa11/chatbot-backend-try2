# """
# Canvas database and authorization layer.

# IMPORTANT:
# This module uses the Supabase service-role client, so RLS is bypassed.

# Therefore every operation MUST explicitly enforce:
# - owner access
# - accepted membership
# - viewer/commenter/editor permissions

# Never trust user_id supplied by the client.
# The caller's user_id must come from the authenticated JWT.
# """

# from typing import Optional, Dict, Any, List
# from uuid import UUID
# from datetime import datetime, timezone
# import hashlib
# import secrets

# from db.client import supabase


# CANVAS_SELECT_FIELDS = """
#     id,
#     owner_id,
#     title,
#     content,
#     visibility,
#     link_access_enabled,
#     link_permission,
#     created_at,
#     updated_at
# """


# class CanvasManager:

#     # ============================================================
#     # Helpers
#     # ============================================================

#     @staticmethod
#     def _valid_uuid(value: str) -> bool:
#         try:
#             UUID(str(value))
#             return True
#         except (ValueError, TypeError, AttributeError):
#             return False

#     def _require_canvas_id(self, canvas_id: str) -> None:
#         if not self._valid_uuid(canvas_id):
#             raise ValueError("Invalid canvas ID")

#     @staticmethod
#     def _now() -> str:
#         return datetime.now(timezone.utc).isoformat()

#     @staticmethod
#     def _hash_share_token(token: str) -> str:
#         """Hash a raw share token before any database lookup/storage."""
#         return hashlib.sha256(token.encode("utf-8")).hexdigest()

#     def _notify(
#         self,
#         user_id: str,
#         canvas_id: Optional[str],
#         type_: str,
#         payload: Optional[Dict[str, Any]] = None,
#     ) -> None:
#         """
#         Best-effort notification insert. Never raises — a failed
#         notification should never break the operation that triggered it.
#         """
#         try:
#             supabase.table("canvas_notifications").insert({
#                 "user_id": user_id,
#                 "canvas_id": canvas_id,
#                 "type": type_,
#                 "payload": payload or {},
#             }).execute()
#         except Exception:
#             pass

#     # ============================================================
#     # Authorization
#     # ============================================================

#     def get_canvas_access(
#         self,
#         canvas_id: str,
#         user_id: str,
#     ) -> Optional[str]:
#         """
#         Return:
#             owner
#             viewer
#             commenter
#             editor
#             None

#         This is the central authorization decision. Covers four paths:
#         1. Owner
#         2. Accepted canvas_members row
#         3. canvas.visibility == 'anyone' (open to any authenticated user,
#            at whatever permission the link currently grants)
#         4. A pending canvas_invites row matching this user's verified
#            email — auto-redeemed into a canvas_members row on first
#            access, no owner approval needed (they pre-approved by
#            inviting the email in the first place).
#         """

#         self._require_canvas_id(canvas_id)

#         canvas_result = (
#             supabase
#             .table("canvases")
#             .select("id, owner_id, visibility, link_permission")
#             .eq("id", canvas_id)
#             .maybe_single()
#             .execute()
#         )

#         if not canvas_result.data:
#             return None

#         canvas = canvas_result.data

#         if canvas["owner_id"] == user_id:
#             return "owner"

#         member_result = (
#             supabase
#             .table("canvas_members")
#             .select("permission")
#             .eq("canvas_id", canvas_id)
#             .eq("user_id", user_id)
#             .eq("status", "accepted")
#             .maybe_single()
#             .execute()
#         )

#         if member_result.data:
#             return member_result.data["permission"]

#         if canvas.get("visibility") == "anyone":
#             return canvas.get("link_permission") or "viewer"

#         redeemed_permission = self._try_redeem_invite(canvas_id, user_id)
#         if redeemed_permission:
#             return redeemed_permission

#         return None

#     def _lookup_email_by_user_id(self, user_id: str) -> Optional[str]:
#         """
#         Best-effort reverse lookup (user_id -> verified email) via the
#         `profiles` mirror table. Never raises — every caller treats a
#         failure here as "couldn't determine email" and degrades
#         gracefully (falls back to the existing request-access flow)
#         rather than breaking the request.
#         """
#         try:
#             result = (
#                 supabase
#                 .table("profiles")
#                 .select("email")
#                 .eq("id", user_id)
#                 .maybe_single()
#                 .execute()
#             )
#             return (result.data or {}).get("email")
#         except Exception:
#             return None

#     def _lookup_user_id_by_email(self, email: str) -> Optional[str]:
#         """
#         Best-effort forward lookup (email -> user_id), used only as an
#         optimization in invite_member so an already-registered person
#         gets access immediately instead of waiting for their next
#         visit. Failure here is never fatal — invite_member falls back
#         to the deferred canvas_invites path either way.
#         """
#         try:
#             result = (
#                 supabase
#                 .table("profiles")
#                 .select("id")
#                 .eq("email", email)
#                 .maybe_single()
#                 .execute()
#             )
#             return (result.data or {}).get("id")
#         except Exception:
#             return None

#     def _try_redeem_invite(self, canvas_id: str, user_id: str) -> Optional[str]:
#         """
#         If this user's verified email matches a pending invite for
#         this canvas, grant access now and mark the invite consumed.
#         Every step degrades to "no redemption" on failure — this runs
#         on the hot path of every access check for non-members, so it
#         must never be the reason a request 500s.
#         """
#         email = self._lookup_email_by_user_id(user_id)
#         if not email:
#             return None

#         try:
#             invite = (
#                 supabase
#                 .table("canvas_invites")
#                 .select("id, permission")
#                 .eq("canvas_id", canvas_id)
#                 .eq("email", email.lower())
#                 .is_("redeemed_by", "null")
#                 .maybe_single()
#                 .execute()
#             )
#         except Exception:
#             return None

#         if not invite.data:
#             return None

#         permission = invite.data["permission"]

#         try:
#             supabase.table("canvas_members").insert({
#                 "canvas_id": canvas_id,
#                 "user_id": user_id,
#                 "permission": permission,
#                 "status": "accepted",
#             }).execute()

#             supabase.table("canvas_invites").update({
#                 "redeemed_by": user_id,
#                 "redeemed_at": self._now(),
#             }).eq("id", invite.data["id"]).execute()
#         except Exception:
#             return None

#         return permission

#     def require_view_access(self, canvas_id: str, user_id: str) -> str:
#         access = self.get_canvas_access(canvas_id, user_id)
#         if access is None:
#             raise PermissionError("Canvas access denied")
#         return access

#     def require_comment_access(self, canvas_id: str, user_id: str) -> str:
#         access = self.get_canvas_access(canvas_id, user_id)
#         if access not in {"owner", "editor", "commenter"}:
#             raise PermissionError("Comment access denied")
#         return access

#     def require_edit_access(self, canvas_id: str, user_id: str) -> str:
#         access = self.get_canvas_access(canvas_id, user_id)
#         if access not in {"owner", "editor"}:
#             raise PermissionError("Canvas edit access denied")
#         return access

#     def require_owner(self, canvas_id: str, user_id: str) -> None:
#         access = self.get_canvas_access(canvas_id, user_id)
#         if access != "owner":
#             raise PermissionError("Canvas owner access required")

#     # ============================================================
#     # Create
#     # ============================================================

#     def create_canvas(
#         self,
#         user_id: str,
#         title: str = "Untitled Canvas",
#         content: Optional[Dict[str, Any]] = None,
#     ) -> Dict[str, Any]:

#         if not self._valid_uuid(user_id):
#             raise ValueError("Invalid user ID")

#         title = (title or "Untitled Canvas").strip()

#         if len(title) > 200:
#             raise ValueError("Canvas title is too long")

#         row = {
#             # IMPORTANT: comes from the verified JWT, NOT frontend input.
#             "owner_id": user_id,
#             "title": title,
#             "content": content or {"type": "doc", "content": []},
#             "visibility": "restricted",
#         }

#         result = supabase.table("canvases").insert(row).execute()
#         return result.data[0] if isinstance(result.data, list) else result.data

#     # ============================================================
#     # Get one canvas
#     # ============================================================

#     def get_canvas(self, canvas_id: str, user_id: str) -> Optional[Dict[str, Any]]:
#         self.require_view_access(canvas_id, user_id)

#         result = (
#             supabase
#             .table("canvases")
#             .select(CANVAS_SELECT_FIELDS)
#             .eq("id", canvas_id)
#             .maybe_single()
#             .execute()
#         )

#         return result.data

#     # ============================================================
#     # Access status (for the "Request access" screen)
#     # ============================================================

#     def get_access_status(self, canvas_id: str, user_id: str) -> Dict[str, Any]:
#         self._require_canvas_id(canvas_id)

#         if not self._valid_uuid(user_id):
#             raise ValueError("Invalid user ID")

#         canvas_result = (
#             supabase
#             .table("canvases")
#             .select("id, title, owner_id, visibility")
#             .eq("id", canvas_id)
#             .maybe_single()
#             .execute()
#         )

#         if not canvas_result.data:
#             raise LookupError("Canvas not found")

#         canvas = canvas_result.data
#         access = self.get_canvas_access(canvas_id, user_id)

#         if access:
#             return {
#                 "has_access": True,
#                 "access_level": access,
#                 "title": canvas["title"],
#                 "request_status": None,
#             }

#         # Most recent request (if any) determines what the UI shows —
#         # a fresh "Request access" button, a pending state, or a
#         # "request again" state after a rejection.
#         request_result = (
#             supabase
#             .table("canvas_access_requests")
#             .select("status")
#             .eq("canvas_id", canvas_id)
#             .eq("requester_id", user_id)
#             .order("created_at", desc=True)
#             .limit(1)
#             .execute()
#         )

#         request_rows = request_result.data or []
#         request_status = request_rows[0]["status"] if request_rows else None

#         return {
#             "has_access": False,
#             "access_level": None,
#             "title": canvas["title"],
#             "request_status": request_status,  # None | "pending" | "rejected" | "approved"
#         }

#     # ============================================================
#     # List user's accessible canvases
#     # ============================================================

#     def list_canvases(self, user_id: str) -> list:
#         if not self._valid_uuid(user_id):
#             raise ValueError("Invalid user ID")

#         owned = (
#             supabase
#             .table("canvases")
#             .select(CANVAS_SELECT_FIELDS)
#             .eq("owner_id", user_id)
#             .order("updated_at", desc=True)
#             .execute()
#         )

#         memberships = (
#             supabase
#             .table("canvas_members")
#             .select("canvas_id, permission")
#             .eq("user_id", user_id)
#             .eq("status", "accepted")
#             .execute()
#         )

#         member_canvas_ids = [row["canvas_id"] for row in (memberships.data or [])]

#         shared = []
#         if member_canvas_ids:
#             shared_result = (
#                 supabase
#                 .table("canvases")
#                 .select(CANVAS_SELECT_FIELDS)
#                 .in_("id", member_canvas_ids)
#                 .order("updated_at", desc=True)
#                 .execute()
#             )
#             shared = shared_result.data or []

#         combined = {}
#         for canvas in owned.data or []:
#             combined[canvas["id"]] = canvas
#         for canvas in shared:
#             combined[canvas["id"]] = canvas

#         return sorted(
#             combined.values(),
#             key=lambda x: x["updated_at"],
#             reverse=True,
#         )

#     # ============================================================
#     # Update content / title
#     # ============================================================

#     def update_canvas_content(
#         self,
#         canvas_id: str,
#         user_id: str,
#         content: Dict[str, Any],
#     ) -> Dict[str, Any]:

#         self.require_edit_access(canvas_id, user_id)

#         result = (
#             supabase
#             .table("canvases")
#             .update({"content": content})
#             .eq("id", canvas_id)
#             .execute()
#         )

#         if not result.data:
#             raise LookupError("Canvas not found")

#         return result.data[0]

#     def update_canvas_title(
#         self,
#         canvas_id: str,
#         user_id: str,
#         title: str,
#     ) -> Dict[str, Any]:

#         self.require_edit_access(canvas_id, user_id)

#         title = title.strip()
#         if not title:
#             raise ValueError("Canvas title cannot be empty")
#         if len(title) > 200:
#             raise ValueError("Canvas title is too long")

#         result = (
#             supabase
#             .table("canvases")
#             .update({"title": title})
#             .eq("id", canvas_id)
#             .execute()
#         )

#         if not result.data:
#             raise LookupError("Canvas not found")

#         return result.data[0]

#     # ============================================================
#     # Delete
#     # ============================================================

#     def delete_canvas(self, canvas_id: str, user_id: str) -> None:
#         self.require_owner(canvas_id, user_id)

#         result = (
#             supabase
#             .table("canvases")
#             .delete()
#             .eq("id", canvas_id)
#             .execute()
#         )

#         if not result.data:
#             raise LookupError("Canvas not found")

#     # ============================================================
#     # Visibility (restricted <-> anyone)
#     # ============================================================

#     def set_visibility(
#         self,
#         canvas_id: str,
#         owner_id: str,
#         visibility: str,
#     ) -> Dict[str, Any]:

#         self.require_owner(canvas_id, owner_id)

#         if visibility not in {"restricted", "anyone"}:
#             raise ValueError("Invalid visibility")

#         update_data: Dict[str, Any] = {"visibility": visibility}

#         if visibility == "restricted":
#             # Fully revoke any public link when going restricted —
#             # the old token must never work again.
#             update_data["link_access_enabled"] = False
#             update_data["share_token_hash"] = None

#         updated = (
#             supabase
#             .table("canvases")
#             .update(update_data)
#             .eq("id", canvas_id)
#             .eq("owner_id", owner_id)
#             .execute()
#         )

#         if not updated.data:
#             raise LookupError("Canvas not found")

#         return updated.data[0]

#     # ============================================================
#     # Public link sharing ("anyone with the link")
#     # ============================================================

#     def create_share_link(
#         self,
#         canvas_id: str,
#         user_id: str,
#         permission: str,
#     ) -> dict:
#         """
#         Create (or replace) a secure public share token for a canvas
#         and flip visibility to 'anyone'.

#         Only the canvas owner may create a share link.
#         The raw token is returned exactly once and is never stored.
#         """

#         allowed_permissions = {"viewer", "commenter", "editor"}

#         if permission not in allowed_permissions:
#             raise ValueError("Invalid share permission")

#         if not self._valid_uuid(user_id):
#             raise ValueError("Invalid user ID")

#         if not self._valid_uuid(canvas_id):
#             raise ValueError("Invalid canvas ID")

#         self.require_owner(canvas_id=canvas_id, user_id=user_id)

#         raw_token = secrets.token_urlsafe(32)
#         token_hash = self._hash_share_token(raw_token)

#         updated = (
#             supabase
#             .table("canvases")
#             .update({
#                 "link_access_enabled": True,
#                 "link_permission": permission,
#                 "share_token_hash": token_hash,
#                 "visibility": "anyone",
#             })
#             .eq("id", canvas_id)
#             .eq("owner_id", user_id)
#             .execute()
#         )

#         if not updated.data:
#             raise RuntimeError("Failed to create share link")

#         result = updated.data[0]

#         return {
#             "canvas_id": result["id"],
#             "permission": result["link_permission"],
#             "token": raw_token,
#         }

#     def get_share_link_settings(
#         self,
#         canvas_id: str,
#         user_id: str,
#     ) -> Dict[str, Any]:
#         """
#         Return share-link configuration for a canvas.
#         Only the canvas owner can access share-link management.
#         Never returns the raw token or share_token_hash.
#         """

#         self.require_owner(canvas_id=canvas_id, user_id=user_id)

#         result = (
#             supabase
#             .table("canvases")
#             .select(
#                 "id, visibility, link_access_enabled, link_permission, share_token_hash"
#             )
#             .eq("id", canvas_id)
#             .eq("owner_id", user_id)
#             .maybe_single()
#             .execute()
#         )

#         if not result.data:
#             raise LookupError("Canvas not found")

#         canvas = result.data

#         return {
#             "canvas_id": canvas["id"],
#             "visibility": canvas.get("visibility"),
#             "link_access_enabled": bool(canvas.get("link_access_enabled")),
#             "link_permission": canvas.get("link_permission"),
#             "has_active_link": bool(
#                 canvas.get("link_access_enabled") and canvas.get("share_token_hash")
#             ),
#         }

#     def update_share_link_settings(
#         self,
#         canvas_id: str,
#         user_id: str,
#         permission: Optional[str] = None,
#         link_access_enabled: Optional[bool] = None,
#     ) -> Dict[str, Any]:
#         """
#         Update share-link settings (permission and/or on/off).
#         Disabling destroys the stored token hash; re-enabling requires
#         a new token via regenerate_share_link().
#         """

#         self.require_owner(canvas_id=canvas_id, user_id=user_id)

#         allowed_permissions = {"viewer", "commenter", "editor"}

#         if permission is not None and permission not in allowed_permissions:
#             raise ValueError("Invalid share permission")

#         if permission is None and link_access_enabled is None:
#             raise ValueError("At least one share setting must be provided")

#         current = (
#             supabase
#             .table("canvases")
#             .select(
#                 "id, link_access_enabled, link_permission, share_token_hash"
#             )
#             .eq("id", canvas_id)
#             .eq("owner_id", user_id)
#             .maybe_single()
#             .execute()
#         )

#         if not current.data:
#             raise LookupError("Canvas not found")

#         canvas = current.data
#         update_data: Dict[str, Any] = {}

#         if permission is not None:
#             update_data["link_permission"] = permission

#         if link_access_enabled is False:
#             update_data["link_access_enabled"] = False
#             update_data["share_token_hash"] = None

#         elif link_access_enabled is True:
#             if not canvas.get("share_token_hash"):
#                 raise ValueError(
#                     "No active share link exists. "
#                     "Generate a new share link instead."
#                 )
#             update_data["link_access_enabled"] = True

#         updated = (
#             supabase
#             .table("canvases")
#             .update(update_data)
#             .eq("id", canvas_id)
#             .eq("owner_id", user_id)
#             .execute()
#         )

#         if not updated.data:
#             raise RuntimeError("Failed to update share-link settings")

#         result = updated.data[0]

#         return {
#             "canvas_id": result["id"],
#             "visibility": result.get("visibility"),
#             "link_access_enabled": bool(result.get("link_access_enabled")),
#             "link_permission": result.get("link_permission"),
#             "has_active_link": bool(
#                 result.get("link_access_enabled") and result.get("share_token_hash")
#             ),
#         }

#     def revoke_share_link(self, canvas_id: str, user_id: str) -> None:
#         self.require_owner(canvas_id=canvas_id, user_id=user_id)

#         result = (
#             supabase
#             .table("canvases")
#             .update({
#                 "link_access_enabled": False,
#                 "share_token_hash": None,
#             })
#             .eq("id", canvas_id)
#             .eq("owner_id", user_id)
#             .execute()
#         )

#         if not result.data:
#             raise LookupError("Canvas not found")

#     def regenerate_share_link(
#         self,
#         canvas_id: str,
#         user_id: str,
#         permission: Optional[str] = None,
#     ) -> Dict[str, Any]:
#         """
#         Generate a completely new public share token; the old token is
#         immediately invalidated. Raw token returned exactly once.
#         """

#         self.require_owner(canvas_id=canvas_id, user_id=user_id)

#         allowed_permissions = {"viewer", "commenter", "editor"}

#         if permission is not None and permission not in allowed_permissions:
#             raise ValueError("Invalid share permission")

#         raw_token = secrets.token_urlsafe(32)
#         token_hash = self._hash_share_token(raw_token)

#         update_data = {
#             "link_access_enabled": True,
#             "share_token_hash": token_hash,
#             "visibility": "anyone",
#         }

#         if permission is not None:
#             update_data["link_permission"] = permission

#         updated = (
#             supabase
#             .table("canvases")
#             .update(update_data)
#             .eq("id", canvas_id)
#             .eq("owner_id", user_id)
#             .execute()
#         )

#         if not updated.data:
#             raise RuntimeError("Failed to regenerate share link")

#         result = updated.data[0]

#         return {
#             "canvas_id": result["id"],
#             "permission": result["link_permission"],
#             "token": raw_token,
#         }

#     # ============================================================
#     # Public (token-based, unauthenticated) access — "anyone" mode
#     # ============================================================

#     def get_public_canvas(self, token: str) -> Optional[Dict[str, Any]]:
#         if not token or len(token) > 512:
#             return None

#         token_hash = self._hash_share_token(token.strip())

#         result = (
#             supabase
#             .table("canvases")
#             .select("id, title, content, link_permission")
#             .eq("share_token_hash", token_hash)
#             .eq("link_access_enabled", True)
#             .limit(1)
#             .execute()
#         )

#         if not result.data:
#             return None

#         canvas = result.data[0]

#         return {
#             "id": canvas["id"],
#             "title": canvas["title"],
#             "content": canvas["content"],
#             "permission": canvas["link_permission"],
#         }

#     def update_public_canvas_content(
#         self,
#         token: str,
#         content: Dict[str, Any],
#     ) -> Optional[Dict[str, Any]]:
#         if not token or len(token) > 512:
#             return None

#         token_hash = self._hash_share_token(token.strip())

#         lookup = (
#             supabase
#             .table("canvases")
#             .select("id, link_permission")
#             .eq("share_token_hash", token_hash)
#             .eq("link_access_enabled", True)
#             .limit(1)
#             .execute()
#         )

#         if not lookup.data:
#             return None

#         canvas = lookup.data[0]

#         if canvas.get("link_permission") != "editor":
#             raise PermissionError("Canvas is not editable")

#         canvas_id = canvas["id"]

#         result = (
#             supabase
#             .table("canvases")
#             .update({"content": content})
#             .eq("id", canvas_id)
#             .eq("share_token_hash", token_hash)
#             .eq("link_access_enabled", True)
#             .eq("link_permission", "editor")
#             .execute()
#         )

#         if not result.data:
#             return None

#         updated = result.data[0]

#         return {
#             "id": updated["id"],
#             "title": updated["title"],
#             "content": updated["content"],
#             "permission": updated["link_permission"],
#         }

#     # ============================================================
#     # Guest comments (public link, no account required)
#     # ============================================================

#     def list_public_comments(self, token: str) -> List[Dict[str, Any]]:
#         if not token or len(token) > 512:
#             return []

#         token_hash = self._hash_share_token(token.strip())

#         canvas_result = (
#             supabase
#             .table("canvases")
#             .select("id")
#             .eq("share_token_hash", token_hash)
#             .eq("link_access_enabled", True)
#             .limit(1)
#             .execute()
#         )

#         if not canvas_result.data:
#             return []

#         canvas_id = canvas_result.data[0]["id"]

#         result = (
#             supabase
#             .table("canvas_comments")
#             .select(
#                 "id, canvas_id, author_id, guest_name, content, anchor_from, "
#                 "anchor_to, anchor_text, resolved, created_at"
#             )
#             .eq("canvas_id", canvas_id)
#             .order("created_at", desc=False)
#             .execute()
#         )

#         return result.data or []

#     @staticmethod
#     def _validate_anchor(anchor_from: int, anchor_to: int) -> None:
#         if (
#             not isinstance(anchor_from, int)
#             or not isinstance(anchor_to, int)
#             or anchor_from < 0
#             or anchor_to <= anchor_from
#             or anchor_to - anchor_from > 20000  # sanity bound, not a real doc size limit
#         ):
#             raise ValueError("Invalid comment anchor")

#     def create_guest_comment(
#         self,
#         token: str,
#         guest_name: str,
#         content: str,
#         anchor_from: int,
#         anchor_to: int,
#         anchor_text: Optional[str] = None,
#     ) -> Optional[Dict[str, Any]]:
#         if not token or len(token) > 512:
#             return None

#         token_hash = self._hash_share_token(token.strip())

#         canvas_result = (
#             supabase
#             .table("canvases")
#             .select("id, link_permission")
#             .eq("share_token_hash", token_hash)
#             .eq("link_access_enabled", True)
#             .limit(1)
#             .execute()
#         )

#         if not canvas_result.data:
#             return None

#         canvas = canvas_result.data[0]

#         if canvas.get("link_permission") not in {"commenter", "editor"}:
#             raise PermissionError("This link does not allow comments")

#         guest_name = (guest_name or "Guest").strip()[:80] or "Guest"
#         content = (content or "").strip()

#         if not content:
#             raise ValueError("Comment cannot be empty")
#         if len(content) > 4000:
#             raise ValueError("Comment is too long")

#         self._validate_anchor(anchor_from, anchor_to)

#         row = {
#             "canvas_id": canvas["id"],
#             "author_id": None,
#             "guest_name": guest_name,
#             "content": content,
#             "anchor_from": anchor_from,
#             "anchor_to": anchor_to,
#             "anchor_text": (anchor_text or "")[:300],
#         }

#         result = supabase.table("canvas_comments").insert(row).execute()

#         if not result.data:
#             raise RuntimeError("Failed to create comment")

#         return result.data[0]

#     # ============================================================
#     # Restricted access: requests
#     # ============================================================

#     def request_access(self, canvas_id: str, user_id: str) -> Dict[str, Any]:
#         self._require_canvas_id(canvas_id)

#         if not self._valid_uuid(user_id):
#             raise ValueError("Invalid user ID")

#         canvas_result = (
#             supabase
#             .table("canvases")
#             .select("id, owner_id")
#             .eq("id", canvas_id)
#             .maybe_single()
#             .execute()
#         )

#         if not canvas_result.data:
#             raise LookupError("Canvas not found")

#         canvas = canvas_result.data

#         if canvas["owner_id"] == user_id:
#             raise ValueError("You already own this canvas")

#         if self.get_canvas_access(canvas_id, user_id):
#             raise ValueError("You already have access to this canvas")

#         existing_pending = (
#             supabase
#             .table("canvas_access_requests")
#             .select("id")
#             .eq("canvas_id", canvas_id)
#             .eq("requester_id", user_id)
#             .eq("status", "pending")
#             .maybe_single()
#             .execute()
#         )

#         if existing_pending.data:
#             raise ValueError("You already have a pending request for this canvas")

#         inserted = (
#             supabase
#             .table("canvas_access_requests")
#             .insert({
#                 "canvas_id": canvas_id,
#                 "requester_id": user_id,
#                 "requested_permission": "viewer",
#                 "status": "pending",
#             })
#             .execute()
#         )

#         row = inserted.data[0] if inserted.data else None

#         self._notify(
#             user_id=canvas["owner_id"],
#             canvas_id=canvas_id,
#             type_="access_request",
#             payload={"requester_id": user_id},
#         )

#         return row

#     def _attach_emails(self, rows: List[Dict[str, Any]], id_field: str) -> List[Dict[str, Any]]:
#         """
#         Best-effort: adds an "email" key to each row by looking up
#         the given id_field against `profiles`. If that table doesn't
#         exist or the lookup fails, rows are returned unchanged rather
#         than raising — this is display sugar, not load-bearing.
#         """
#         ids = list({row[id_field] for row in rows if row.get(id_field)})
#         if not ids:
#             return rows

#         try:
#             profiles = (
#                 supabase
#                 .table("profiles")
#                 .select("id, email")
#                 .in_("id", ids)
#                 .execute()
#             )
#             email_by_id = {p["id"]: p.get("email") for p in (profiles.data or [])}
#         except Exception:
#             email_by_id = {}

#         for row in rows:
#             row["email"] = email_by_id.get(row.get(id_field))

#         return rows

#     def list_access_requests(self, canvas_id: str, owner_id: str) -> List[Dict[str, Any]]:
#         self.require_owner(canvas_id, owner_id)

#         result = (
#             supabase
#             .table("canvas_access_requests")
#             .select("id, requester_id, requested_permission, status, created_at")
#             .eq("canvas_id", canvas_id)
#             .eq("status", "pending")
#             .order("created_at", desc=False)
#             .execute()
#         )

#         return self._attach_emails(result.data or [], "requester_id")

#     def respond_to_access_request(
#         self,
#         canvas_id: str,
#         owner_id: str,
#         request_id: str,
#         approve: bool,
#         permission: Optional[str] = None,
#     ) -> Dict[str, Any]:

#         self.require_owner(canvas_id, owner_id)

#         allowed_permissions = {"viewer", "commenter", "editor"}
#         if permission is not None and permission not in allowed_permissions:
#             raise ValueError("Invalid permission")

#         existing = (
#             supabase
#             .table("canvas_access_requests")
#             .select("id, requester_id, requested_permission, status")
#             .eq("id", request_id)
#             .eq("canvas_id", canvas_id)
#             .maybe_single()
#             .execute()
#         )

#         if not existing.data:
#             raise LookupError("Access request not found")

#         request_row = existing.data
#         requester_id = request_row["requester_id"]
#         granted_permission = permission or request_row["requested_permission"] or "viewer"

#         updated = (
#             supabase
#             .table("canvas_access_requests")
#             .update({"status": "approved" if approve else "rejected"})
#             .eq("id", request_id)
#             .execute()
#         )

#         if not updated.data:
#             raise RuntimeError("Failed to update access request")

#         if approve:
#             # Grant (or update) membership now that the owner approved.
#             existing_member = (
#                 supabase
#                 .table("canvas_members")
#                 .select("id")
#                 .eq("canvas_id", canvas_id)
#                 .eq("user_id", requester_id)
#                 .maybe_single()
#                 .execute()
#             )

#             if existing_member.data:
#                 supabase.table("canvas_members").update({
#                     "permission": granted_permission,
#                     "status": "accepted",
#                 }).eq("id", existing_member.data["id"]).execute()
#             else:
#                 supabase.table("canvas_members").insert({
#                     "canvas_id": canvas_id,
#                     "user_id": requester_id,
#                     "permission": granted_permission,
#                     "status": "accepted",
#                 }).execute()

#         self._notify(
#             user_id=requester_id,
#             canvas_id=canvas_id,
#             type_="access_approved" if approve else "access_denied",
#             payload={},
#         )

#         return updated.data[0]

#     # ============================================================
#     # Restricted access: members (direct invite + management)
#     # ============================================================

#     def invite_member(
#         self,
#         canvas_id: str,
#         owner_id: str,
#         email: str,
#         permission: str,
#     ) -> Dict[str, Any]:
#         """
#         Owner grants access to an email address — whether or not that
#         person has ever used the app. If they already have an account,
#         access is granted immediately. Otherwise this just records the
#         invite; it's auto-redeemed the moment that email verifies via
#         the passwordless OTP gate and opens the canvas link (see
#         _try_redeem_invite, called from get_canvas_access).

#         Every lookup here is best-effort — a failure never blocks the
#         invite itself from being recorded.
#         """

#         self.require_owner(canvas_id, owner_id)

#         allowed_permissions = {"viewer", "commenter", "editor"}
#         if permission not in allowed_permissions:
#             raise ValueError("Invalid permission")

#         email = (email or "").strip().lower()
#         if not email:
#             raise ValueError("Email is required")
#         if len(email) > 320 or "@" not in email:
#             raise ValueError("That doesn't look like a valid email")

#         existing_user_id = self._lookup_user_id_by_email(email)

#         if existing_user_id:
#             if existing_user_id == owner_id:
#                 raise ValueError("You already own this canvas")

#             existing_member = (
#                 supabase
#                 .table("canvas_members")
#                 .select("id")
#                 .eq("canvas_id", canvas_id)
#                 .eq("user_id", existing_user_id)
#                 .maybe_single()
#                 .execute()
#             )

#             if existing_member.data:
#                 supabase.table("canvas_members").update({
#                     "permission": permission,
#                     "status": "accepted",
#                 }).eq("id", existing_member.data["id"]).execute()
#             else:
#                 supabase.table("canvas_members").insert({
#                     "canvas_id": canvas_id,
#                     "user_id": existing_user_id,
#                     "permission": permission,
#                     "status": "accepted",
#                 }).execute()

#             self._notify(
#                 user_id=existing_user_id,
#                 canvas_id=canvas_id,
#                 type_="added_to_canvas",
#                 payload={},
#             )

#             return {
#                 "email": email,
#                 "permission": permission,
#                 "status": "accepted",
#                 "pending_signup": False,
#             }

#         # No account yet — record the invite. Upsert so re-inviting the
#         # same email just updates the permission rather than erroring.
#         supabase.table("canvas_invites").upsert(
#             {
#                 "canvas_id": canvas_id,
#                 "email": email,
#                 "permission": permission,
#                 "invited_by": owner_id,
#             },
#             on_conflict="canvas_id,email",
#         ).execute()

#         return {
#             "email": email,
#             "permission": permission,
#             "status": "pending_signup",
#             "pending_signup": True,
#         }

#     def list_pending_invites(self, canvas_id: str, owner_id: str) -> List[Dict[str, Any]]:
#         self.require_owner(canvas_id, owner_id)

#         result = (
#             supabase
#             .table("canvas_invites")
#             .select("id, email, permission, created_at")
#             .eq("canvas_id", canvas_id)
#             .is_("redeemed_by", "null")
#             .order("created_at", desc=False)
#             .execute()
#         )

#         return result.data or []

#     def revoke_invite(self, canvas_id: str, owner_id: str, invite_id: str) -> None:
#         self.require_owner(canvas_id, owner_id)

#         result = (
#             supabase
#             .table("canvas_invites")
#             .delete()
#             .eq("id", invite_id)
#             .eq("canvas_id", canvas_id)
#             .is_("redeemed_by", "null")
#             .execute()
#         )

#         if not result.data:
#             raise LookupError("Invite not found")

#     def list_members(self, canvas_id: str, owner_id: str) -> List[Dict[str, Any]]:
#         self.require_owner(canvas_id, owner_id)

#         result = (
#             supabase
#             .table("canvas_members")
#             .select("id, user_id, permission, status, created_at")
#             .eq("canvas_id", canvas_id)
#             .eq("status", "accepted")
#             .order("created_at", desc=False)
#             .execute()
#         )

#         return self._attach_emails(result.data or [], "user_id")

#     def update_member_permission(
#         self,
#         canvas_id: str,
#         owner_id: str,
#         member_id: str,
#         permission: str,
#     ) -> Dict[str, Any]:

#         self.require_owner(canvas_id, owner_id)

#         allowed_permissions = {"viewer", "commenter", "editor"}
#         if permission not in allowed_permissions:
#             raise ValueError("Invalid permission")

#         updated = (
#             supabase
#             .table("canvas_members")
#             .update({"permission": permission})
#             .eq("id", member_id)
#             .eq("canvas_id", canvas_id)
#             .execute()
#         )

#         if not updated.data:
#             raise LookupError("Member not found")

#         return updated.data[0]

#     def remove_member(self, canvas_id: str, owner_id: str, member_id: str) -> None:
#         self.require_owner(canvas_id, owner_id)

#         result = (
#             supabase
#             .table("canvas_members")
#             .delete()
#             .eq("id", member_id)
#             .eq("canvas_id", canvas_id)
#             .execute()
#         )

#         if not result.data:
#             raise LookupError("Member not found")

#     # ============================================================
#     # Comments
#     # ============================================================

#     def list_comments(self, canvas_id: str, user_id: str) -> List[Dict[str, Any]]:
#         self.require_view_access(canvas_id, user_id)

#         result = (
#             supabase
#             .table("canvas_comments")
#             .select(
#                 "id, canvas_id, author_id, guest_name, content, anchor_from, "
#                 "anchor_to, anchor_text, resolved, created_at"
#             )
#             .eq("canvas_id", canvas_id)
#             .order("created_at", desc=False)
#             .execute()
#         )

#         return result.data or []

#     def create_comment(
#         self,
#         canvas_id: str,
#         user_id: str,
#         content: str,
#         anchor_from: int,
#         anchor_to: int,
#         anchor_text: Optional[str] = None,
#     ) -> Dict[str, Any]:

#         self.require_comment_access(canvas_id, user_id)

#         content = (content or "").strip()
#         if not content:
#             raise ValueError("Comment cannot be empty")
#         if len(content) > 4000:
#             raise ValueError("Comment is too long")

#         self._validate_anchor(anchor_from, anchor_to)

#         row = {
#             "canvas_id": canvas_id,
#             "author_id": user_id,
#             "content": content,
#             "anchor_from": anchor_from,
#             "anchor_to": anchor_to,
#             "anchor_text": (anchor_text or "")[:300],
#         }

#         result = supabase.table("canvas_comments").insert(row).execute()

#         if not result.data:
#             raise RuntimeError("Failed to create comment")

#         return result.data[0]

#     def resolve_comment(
#         self,
#         canvas_id: str,
#         user_id: str,
#         comment_id: str,
#         resolved: bool = True,
#     ) -> Dict[str, Any]:

#         access = self.require_view_access(canvas_id, user_id)
#         if access not in {"owner", "editor", "commenter"}:
#             raise PermissionError("You cannot resolve comments")

#         updated = (
#             supabase
#             .table("canvas_comments")
#             .update({"resolved": resolved})
#             .eq("id", comment_id)
#             .eq("canvas_id", canvas_id)
#             .execute()
#         )

#         if not updated.data:
#             raise LookupError("Comment not found")

#         return updated.data[0]

#     def delete_comment(self, canvas_id: str, user_id: str, comment_id: str) -> None:
#         access = self.get_canvas_access(canvas_id, user_id)

#         result = (
#             supabase
#             .table("canvas_comments")
#             .select("id, author_id")
#             .eq("id", comment_id)
#             .eq("canvas_id", canvas_id)
#             .maybe_single()
#             .execute()
#         )

#         if not result.data:
#             raise LookupError("Comment not found")

#         if result.data["author_id"] != user_id and access != "owner":
#             raise PermissionError("You cannot delete this comment")

#         supabase.table("canvas_comments").delete().eq("id", comment_id).execute()

#     # ============================================================
#     # Notifications
#     # ============================================================

#     def list_notifications(
#         self,
#         user_id: str,
#         unread_only: bool = False,
#     ) -> List[Dict[str, Any]]:

#         if not self._valid_uuid(user_id):
#             raise ValueError("Invalid user ID")

#         query = (
#             supabase
#             .table("canvas_notifications")
#             .select("id, canvas_id, type, payload, read, created_at")
#             .eq("user_id", user_id)
#             .order("created_at", desc=True)
#             .limit(50)
#         )

#         if unread_only:
#             query = query.eq("read", False)

#         result = query.execute()
#         return result.data or []

#     def mark_notification_read(self, user_id: str, notification_id: str) -> Dict[str, Any]:
#         updated = (
#             supabase
#             .table("canvas_notifications")
#             .update({"read": True})
#             .eq("id", notification_id)
#             .eq("user_id", user_id)
#             .execute()
#         )

#         if not updated.data:
#             raise LookupError("Notification not found")

#         return updated.data[0]






















































# """
# Canvas database and authorization layer.

# IMPORTANT:
# This module uses the Supabase service-role client, so RLS is bypassed.

# Therefore every operation MUST explicitly enforce:
# - owner access
# - accepted membership
# - viewer/commenter/editor permissions

# Never trust user_id supplied by the client.
# The caller's user_id must come from the authenticated JWT.
# """

# from typing import Optional, Dict, Any, List
# from uuid import UUID
# from datetime import datetime, timezone
# import hashlib
# import secrets

# from db.client import supabase


# CANVAS_SELECT_FIELDS = """
#     id,
#     owner_id,
#     title,
#     content,
#     visibility,
#     link_access_enabled,
#     link_permission,
#     created_at,
#     updated_at
# """


# class CanvasManager:

#     # ============================================================
#     # Helpers
#     # ============================================================

#     @staticmethod
#     def _valid_uuid(value: str) -> bool:
#         try:
#             UUID(str(value))
#             return True
#         except (ValueError, TypeError, AttributeError):
#             return False

#     def _require_canvas_id(self, canvas_id: str) -> None:
#         if not self._valid_uuid(canvas_id):
#             raise ValueError("Invalid canvas ID")

#     @staticmethod
#     def _now() -> str:
#         return datetime.now(timezone.utc).isoformat()

#     @staticmethod
#     def _hash_share_token(token: str) -> str:
#         """Hash a raw share token before any database lookup/storage."""
#         return hashlib.sha256(token.encode("utf-8")).hexdigest()

#     def _notify(
#         self,
#         user_id: str,
#         canvas_id: Optional[str],
#         type_: str,
#         payload: Optional[Dict[str, Any]] = None,
#     ) -> None:
#         """
#         Best-effort notification insert. Never raises — a failed
#         notification should never break the operation that triggered it.
#         """
#         try:
#             supabase.table("canvas_notifications").insert({
#                 "user_id": user_id,
#                 "canvas_id": canvas_id,
#                 "type": type_,
#                 "payload": payload or {},
#             }).execute()
#         except Exception:
#             pass

#     # ============================================================
#     # Authorization
#     # ============================================================

#     def get_canvas_access(
#         self,
#         canvas_id: str,
#         user_id: str,
#     ) -> Optional[str]:
#         """
#         Return:
#             owner
#             viewer
#             commenter
#             editor
#             None

#         This is the central authorization decision. Covers four paths:
#         1. Owner
#         2. Accepted canvas_members row
#         3. canvas.visibility == 'anyone' (open to any authenticated user,
#            at whatever permission the link currently grants)
#         4. A pending canvas_invites row matching this user's verified
#            email — auto-redeemed into a canvas_members row on first
#            access, no owner approval needed (they pre-approved by
#            inviting the email in the first place).
#         """

#         self._require_canvas_id(canvas_id)

#         canvas_result = (
#             supabase
#             .table("canvases")
#             .select("id, owner_id, visibility, link_permission")
#             .eq("id", canvas_id)
#             .maybe_single()
#             .execute()
#         )

#         if not canvas_result.data:
#             return None

#         canvas = canvas_result.data

#         if canvas["owner_id"] == user_id:
#             return "owner"

#         member_result = (
#             supabase
#             .table("canvas_members")
#             .select("permission")
#             .eq("canvas_id", canvas_id)
#             .eq("user_id", user_id)
#             .eq("status", "accepted")
#             .maybe_single()
#             .execute()
#         )

#         if member_result.data:
#             return member_result.data["permission"]

#         if canvas.get("visibility") == "anyone":
#             return canvas.get("link_permission") or "viewer"

#         redeemed_permission = self._try_redeem_invite(canvas_id, user_id)
#         if redeemed_permission:
#             return redeemed_permission

#         return None

#     def _lookup_email_by_user_id(self, user_id: str) -> Optional[str]:
#         """
#         Best-effort reverse lookup (user_id -> verified email) via the
#         `profiles` mirror table. Never raises — every caller treats a
#         failure here as "couldn't determine email" and degrades
#         gracefully (falls back to the existing request-access flow)
#         rather than breaking the request.
#         """
#         try:
#             result = (
#                 supabase
#                 .table("profiles")
#                 .select("email")
#                 .eq("id", user_id)
#                 .maybe_single()
#                 .execute()
#             )
#             return (result.data or {}).get("email")
#         except Exception:
#             return None

#     def _lookup_user_id_by_email(self, email: str) -> Optional[str]:
#         """
#         Best-effort forward lookup (email -> user_id), used only as an
#         optimization in invite_member so an already-registered person
#         gets access immediately instead of waiting for their next
#         visit. Failure here is never fatal — invite_member falls back
#         to the deferred canvas_invites path either way.
#         """
#         try:
#             result = (
#                 supabase
#                 .table("profiles")
#                 .select("id")
#                 .eq("email", email)
#                 .maybe_single()
#                 .execute()
#             )
#             return (result.data or {}).get("id")
#         except Exception:
#             return None

#     def _try_redeem_invite(self, canvas_id: str, user_id: str) -> Optional[str]:
#         """
#         If this user's verified email matches a pending invite for
#         this canvas, grant access now and mark the invite consumed.
#         Every step degrades to "no redemption" on failure — this runs
#         on the hot path of every access check for non-members, so it
#         must never be the reason a request 500s.
#         """
#         email = self._lookup_email_by_user_id(user_id)
#         if not email:
#             return None

#         try:
#             invite = (
#                 supabase
#                 .table("canvas_invites")
#                 .select("id, permission")
#                 .eq("canvas_id", canvas_id)
#                 .eq("email", email.lower())
#                 .is_("redeemed_by", "null")
#                 .maybe_single()
#                 .execute()
#             )
#         except Exception:
#             return None

#         if not invite.data:
#             return None

#         permission = invite.data["permission"]

#         try:
#             supabase.table("canvas_members").insert({
#                 "canvas_id": canvas_id,
#                 "user_id": user_id,
#                 "permission": permission,
#                 "status": "accepted",
#             }).execute()

#             supabase.table("canvas_invites").update({
#                 "redeemed_by": user_id,
#                 "redeemed_at": self._now(),
#             }).eq("id", invite.data["id"]).execute()
#         except Exception:
#             return None

#         return permission

#     def require_view_access(self, canvas_id: str, user_id: str) -> str:
#         access = self.get_canvas_access(canvas_id, user_id)
#         if access is None:
#             raise PermissionError("Canvas access denied")
#         return access

#     def require_comment_access(self, canvas_id: str, user_id: str) -> str:
#         access = self.get_canvas_access(canvas_id, user_id)
#         if access not in {"owner", "editor", "commenter"}:
#             raise PermissionError("Comment access denied")
#         return access

#     def require_edit_access(self, canvas_id: str, user_id: str) -> str:
#         access = self.get_canvas_access(canvas_id, user_id)
#         if access not in {"owner", "editor"}:
#             raise PermissionError("Canvas edit access denied")
#         return access

#     def require_owner(self, canvas_id: str, user_id: str) -> None:
#         access = self.get_canvas_access(canvas_id, user_id)
#         if access != "owner":
#             raise PermissionError("Canvas owner access required")

#     # ============================================================
#     # Create
#     # ============================================================

#     def create_canvas(
#         self,
#         user_id: str,
#         title: str = "Untitled Canvas",
#         content: Optional[Dict[str, Any]] = None,
#     ) -> Dict[str, Any]:

#         if not self._valid_uuid(user_id):
#             raise ValueError("Invalid user ID")

#         title = (title or "Untitled Canvas").strip()

#         if len(title) > 200:
#             raise ValueError("Canvas title is too long")

#         row = {
#             # IMPORTANT: comes from the verified JWT, NOT frontend input.
#             "owner_id": user_id,
#             "title": title,
#             "content": content or {"type": "doc", "content": []},
#             "visibility": "restricted",
#         }

#         result = supabase.table("canvases").insert(row).execute()
#         return result.data[0] if isinstance(result.data, list) else result.data

#     # ============================================================
#     # Get one canvas
#     # ============================================================

#     def get_canvas(self, canvas_id: str, user_id: str) -> Optional[Dict[str, Any]]:
#         self.require_view_access(canvas_id, user_id)

#         result = (
#             supabase
#             .table("canvases")
#             .select(CANVAS_SELECT_FIELDS)
#             .eq("id", canvas_id)
#             .maybe_single()
#             .execute()
#         )

#         return result.data

#     # ============================================================
#     # Access status (for the "Request access" screen)
#     # ============================================================

#     def get_access_status(self, canvas_id: str, user_id: str) -> Dict[str, Any]:
#         self._require_canvas_id(canvas_id)

#         if not self._valid_uuid(user_id):
#             raise ValueError("Invalid user ID")

#         canvas_result = (
#             supabase
#             .table("canvases")
#             .select("id, title, owner_id, visibility")
#             .eq("id", canvas_id)
#             .maybe_single()
#             .execute()
#         )

#         if not canvas_result.data:
#             raise LookupError("Canvas not found")

#         canvas = canvas_result.data
#         access = self.get_canvas_access(canvas_id, user_id)

#         if access:
#             return {
#                 "has_access": True,
#                 "access_level": access,
#                 "title": canvas["title"],
#                 "request_status": None,
#             }

#         # Most recent request (if any) determines what the UI shows —
#         # a fresh "Request access" button, a pending state, or a
#         # "request again" state after a rejection.
#         request_result = (
#             supabase
#             .table("canvas_access_requests")
#             .select("status")
#             .eq("canvas_id", canvas_id)
#             .eq("requester_id", user_id)
#             .order("created_at", desc=True)
#             .limit(1)
#             .execute()
#         )

#         request_rows = request_result.data or []
#         request_status = request_rows[0]["status"] if request_rows else None

#         return {
#             "has_access": False,
#             "access_level": None,
#             "title": canvas["title"],
#             "request_status": request_status,  # None | "pending" | "rejected" | "approved"
#         }

#     # ============================================================
#     # List user's accessible canvases
#     # ============================================================

#     def list_canvases(self, user_id: str) -> list:
#         if not self._valid_uuid(user_id):
#             raise ValueError("Invalid user ID")

#         owned = (
#             supabase
#             .table("canvases")
#             .select(CANVAS_SELECT_FIELDS)
#             .eq("owner_id", user_id)
#             .order("updated_at", desc=True)
#             .execute()
#         )

#         memberships = (
#             supabase
#             .table("canvas_members")
#             .select("canvas_id, permission")
#             .eq("user_id", user_id)
#             .eq("status", "accepted")
#             .execute()
#         )

#         member_canvas_ids = [row["canvas_id"] for row in (memberships.data or [])]

#         shared = []
#         if member_canvas_ids:
#             shared_result = (
#                 supabase
#                 .table("canvases")
#                 .select(CANVAS_SELECT_FIELDS)
#                 .in_("id", member_canvas_ids)
#                 .order("updated_at", desc=True)
#                 .execute()
#             )
#             shared = shared_result.data or []

#         combined = {}
#         for canvas in owned.data or []:
#             combined[canvas["id"]] = canvas
#         for canvas in shared:
#             combined[canvas["id"]] = canvas

#         return sorted(
#             combined.values(),
#             key=lambda x: x["updated_at"],
#             reverse=True,
#         )

#     # ============================================================
#     # Update content / title
#     # ============================================================

#     def update_canvas_content(
#         self,
#         canvas_id: str,
#         user_id: str,
#         content: Dict[str, Any],
#     ) -> Dict[str, Any]:

#         self.require_edit_access(canvas_id, user_id)

#         result = (
#             supabase
#             .table("canvases")
#             .update({"content": content})
#             .eq("id", canvas_id)
#             .execute()
#         )

#         if not result.data:
#             raise LookupError("Canvas not found")

#         return result.data[0]

#     def update_canvas_title(
#         self,
#         canvas_id: str,
#         user_id: str,
#         title: str,
#     ) -> Dict[str, Any]:

#         self.require_edit_access(canvas_id, user_id)

#         title = title.strip()
#         if not title:
#             raise ValueError("Canvas title cannot be empty")
#         if len(title) > 200:
#             raise ValueError("Canvas title is too long")

#         result = (
#             supabase
#             .table("canvases")
#             .update({"title": title})
#             .eq("id", canvas_id)
#             .execute()
#         )

#         if not result.data:
#             raise LookupError("Canvas not found")

#         return result.data[0]

#     # ============================================================
#     # Delete
#     # ============================================================

#     def delete_canvas(self, canvas_id: str, user_id: str) -> None:
#         self.require_owner(canvas_id, user_id)

#         result = (
#             supabase
#             .table("canvases")
#             .delete()
#             .eq("id", canvas_id)
#             .execute()
#         )

#         if not result.data:
#             raise LookupError("Canvas not found")

#     # ============================================================
#     # Visibility (restricted <-> anyone)
#     # ============================================================

#     def set_visibility(
#         self,
#         canvas_id: str,
#         owner_id: str,
#         visibility: str,
#     ) -> Dict[str, Any]:

#         self.require_owner(canvas_id, owner_id)

#         if visibility not in {"restricted", "anyone"}:
#             raise ValueError("Invalid visibility")

#         update_data: Dict[str, Any] = {"visibility": visibility}

#         if visibility == "restricted":
#             # Fully revoke any public link when going restricted —
#             # the old token must never work again.
#             update_data["link_access_enabled"] = False
#             update_data["share_token_hash"] = None

#         updated = (
#             supabase
#             .table("canvases")
#             .update(update_data)
#             .eq("id", canvas_id)
#             .eq("owner_id", owner_id)
#             .execute()
#         )

#         if not updated.data:
#             raise LookupError("Canvas not found")

#         return updated.data[0]

#     # ============================================================
#     # Public link sharing ("anyone with the link")
#     # ============================================================

#     def create_share_link(
#         self,
#         canvas_id: str,
#         user_id: str,
#         permission: str,
#     ) -> dict:
#         """
#         Create (or replace) a secure public share token for a canvas
#         and flip visibility to 'anyone'.

#         Only the canvas owner may create a share link.
#         The raw token is returned exactly once and is never stored.
#         """

#         allowed_permissions = {"viewer", "commenter", "editor"}

#         if permission not in allowed_permissions:
#             raise ValueError("Invalid share permission")

#         if not self._valid_uuid(user_id):
#             raise ValueError("Invalid user ID")

#         if not self._valid_uuid(canvas_id):
#             raise ValueError("Invalid canvas ID")

#         self.require_owner(canvas_id=canvas_id, user_id=user_id)

#         raw_token = secrets.token_urlsafe(32)
#         token_hash = self._hash_share_token(raw_token)

#         updated = (
#             supabase
#             .table("canvases")
#             .update({
#                 "link_access_enabled": True,
#                 "link_permission": permission,
#                 "share_token_hash": token_hash,
#                 "visibility": "anyone",
#             })
#             .eq("id", canvas_id)
#             .eq("owner_id", user_id)
#             .execute()
#         )

#         if not updated.data:
#             raise RuntimeError("Failed to create share link")

#         result = updated.data[0]

#         return {
#             "canvas_id": result["id"],
#             "permission": result["link_permission"],
#             "token": raw_token,
#         }

#     def get_share_link_settings(
#         self,
#         canvas_id: str,
#         user_id: str,
#     ) -> Dict[str, Any]:
#         """
#         Return share-link configuration for a canvas.
#         Only the canvas owner can access share-link management.
#         Never returns the raw token or share_token_hash.
#         """

#         self.require_owner(canvas_id=canvas_id, user_id=user_id)

#         result = (
#             supabase
#             .table("canvases")
#             .select(
#                 "id, visibility, link_access_enabled, link_permission, share_token_hash"
#             )
#             .eq("id", canvas_id)
#             .eq("owner_id", user_id)
#             .maybe_single()
#             .execute()
#         )

#         if not result.data:
#             raise LookupError("Canvas not found")

#         canvas = result.data

#         return {
#             "canvas_id": canvas["id"],
#             "visibility": canvas.get("visibility"),
#             "link_access_enabled": bool(canvas.get("link_access_enabled")),
#             "link_permission": canvas.get("link_permission"),
#             "has_active_link": bool(
#                 canvas.get("link_access_enabled") and canvas.get("share_token_hash")
#             ),
#         }

#     def update_share_link_settings(
#         self,
#         canvas_id: str,
#         user_id: str,
#         permission: Optional[str] = None,
#         link_access_enabled: Optional[bool] = None,
#     ) -> Dict[str, Any]:
#         """
#         Update share-link settings (permission and/or on/off).
#         Disabling destroys the stored token hash; re-enabling requires
#         a new token via regenerate_share_link().
#         """

#         self.require_owner(canvas_id=canvas_id, user_id=user_id)

#         allowed_permissions = {"viewer", "commenter", "editor"}

#         if permission is not None and permission not in allowed_permissions:
#             raise ValueError("Invalid share permission")

#         if permission is None and link_access_enabled is None:
#             raise ValueError("At least one share setting must be provided")

#         current = (
#             supabase
#             .table("canvases")
#             .select(
#                 "id, link_access_enabled, link_permission, share_token_hash"
#             )
#             .eq("id", canvas_id)
#             .eq("owner_id", user_id)
#             .maybe_single()
#             .execute()
#         )

#         if not current.data:
#             raise LookupError("Canvas not found")

#         canvas = current.data
#         update_data: Dict[str, Any] = {}

#         if permission is not None:
#             update_data["link_permission"] = permission

#         if link_access_enabled is False:
#             update_data["link_access_enabled"] = False
#             update_data["share_token_hash"] = None

#         elif link_access_enabled is True:
#             if not canvas.get("share_token_hash"):
#                 raise ValueError(
#                     "No active share link exists. "
#                     "Generate a new share link instead."
#                 )
#             update_data["link_access_enabled"] = True

#         updated = (
#             supabase
#             .table("canvases")
#             .update(update_data)
#             .eq("id", canvas_id)
#             .eq("owner_id", user_id)
#             .execute()
#         )

#         if not updated.data:
#             raise RuntimeError("Failed to update share-link settings")

#         result = updated.data[0]

#         return {
#             "canvas_id": result["id"],
#             "visibility": result.get("visibility"),
#             "link_access_enabled": bool(result.get("link_access_enabled")),
#             "link_permission": result.get("link_permission"),
#             "has_active_link": bool(
#                 result.get("link_access_enabled") and result.get("share_token_hash")
#             ),
#         }

#     def revoke_share_link(self, canvas_id: str, user_id: str) -> None:
#         self.require_owner(canvas_id=canvas_id, user_id=user_id)

#         result = (
#             supabase
#             .table("canvases")
#             .update({
#                 "link_access_enabled": False,
#                 "share_token_hash": None,
#             })
#             .eq("id", canvas_id)
#             .eq("owner_id", user_id)
#             .execute()
#         )

#         if not result.data:
#             raise LookupError("Canvas not found")

#     def regenerate_share_link(
#         self,
#         canvas_id: str,
#         user_id: str,
#         permission: Optional[str] = None,
#     ) -> Dict[str, Any]:
#         """
#         Generate a completely new public share token; the old token is
#         immediately invalidated. Raw token returned exactly once.
#         """

#         self.require_owner(canvas_id=canvas_id, user_id=user_id)

#         allowed_permissions = {"viewer", "commenter", "editor"}

#         if permission is not None and permission not in allowed_permissions:
#             raise ValueError("Invalid share permission")

#         raw_token = secrets.token_urlsafe(32)
#         token_hash = self._hash_share_token(raw_token)

#         update_data = {
#             "link_access_enabled": True,
#             "share_token_hash": token_hash,
#             "visibility": "anyone",
#         }

#         if permission is not None:
#             update_data["link_permission"] = permission

#         updated = (
#             supabase
#             .table("canvases")
#             .update(update_data)
#             .eq("id", canvas_id)
#             .eq("owner_id", user_id)
#             .execute()
#         )

#         if not updated.data:
#             raise RuntimeError("Failed to regenerate share link")

#         result = updated.data[0]

#         return {
#             "canvas_id": result["id"],
#             "permission": result["link_permission"],
#             "token": raw_token,
#         }

#     # ============================================================
#     # Public (token-based, unauthenticated) access — "anyone" mode
#     # ============================================================

#     def get_public_canvas(self, token: str) -> Optional[Dict[str, Any]]:
#         if not token or len(token) > 512:
#             return None

#         token_hash = self._hash_share_token(token.strip())

#         result = (
#             supabase
#             .table("canvases")
#             .select("id, title, content, link_permission")
#             .eq("share_token_hash", token_hash)
#             .eq("link_access_enabled", True)
#             .limit(1)
#             .execute()
#         )

#         if not result.data:
#             return None

#         canvas = result.data[0]

#         return {
#             "id": canvas["id"],
#             "title": canvas["title"],
#             "content": canvas["content"],
#             "permission": canvas["link_permission"],
#         }

#     def update_public_canvas_content(
#         self,
#         token: str,
#         content: Dict[str, Any],
#     ) -> Optional[Dict[str, Any]]:
#         if not token or len(token) > 512:
#             return None

#         token_hash = self._hash_share_token(token.strip())

#         lookup = (
#             supabase
#             .table("canvases")
#             .select("id, link_permission")
#             .eq("share_token_hash", token_hash)
#             .eq("link_access_enabled", True)
#             .limit(1)
#             .execute()
#         )

#         if not lookup.data:
#             return None

#         canvas = lookup.data[0]

#         if canvas.get("link_permission") != "editor":
#             raise PermissionError("Canvas is not editable")

#         canvas_id = canvas["id"]

#         result = (
#             supabase
#             .table("canvases")
#             .update({"content": content})
#             .eq("id", canvas_id)
#             .eq("share_token_hash", token_hash)
#             .eq("link_access_enabled", True)
#             .eq("link_permission", "editor")
#             .execute()
#         )

#         if not result.data:
#             return None

#         updated = result.data[0]

#         return {
#             "id": updated["id"],
#             "title": updated["title"],
#             "content": updated["content"],
#             "permission": updated["link_permission"],
#         }

#     # ============================================================
#     # Guest comments (public link, no account required)
#     # ============================================================

#     def list_public_comments(self, token: str) -> List[Dict[str, Any]]:
#         if not token or len(token) > 512:
#             return []

#         token_hash = self._hash_share_token(token.strip())

#         canvas_result = (
#             supabase
#             .table("canvases")
#             .select("id")
#             .eq("share_token_hash", token_hash)
#             .eq("link_access_enabled", True)
#             .limit(1)
#             .execute()
#         )

#         if not canvas_result.data:
#             return []

#         canvas_id = canvas_result.data[0]["id"]

#         result = (
#             supabase
#             .table("canvas_comments")
#             .select(
#                 "id, canvas_id, author_id, guest_name, content, anchor_from, "
#                 "anchor_to, anchor_text, resolved, created_at"
#             )
#             .eq("canvas_id", canvas_id)
#             .order("created_at", desc=False)
#             .execute()
#         )

#         return result.data or []

#     @staticmethod
#     def _validate_anchor(anchor_from: int, anchor_to: int) -> None:
#         if (
#             not isinstance(anchor_from, int)
#             or not isinstance(anchor_to, int)
#             or anchor_from < 0
#             or anchor_to <= anchor_from
#             or anchor_to - anchor_from > 20000  # sanity bound, not a real doc size limit
#         ):
#             raise ValueError("Invalid comment anchor")

#     def create_guest_comment(
#         self,
#         token: str,
#         guest_name: str,
#         content: str,
#         anchor_from: int,
#         anchor_to: int,
#         anchor_text: Optional[str] = None,
#     ) -> Optional[Dict[str, Any]]:
#         if not token or len(token) > 512:
#             return None

#         token_hash = self._hash_share_token(token.strip())

#         canvas_result = (
#             supabase
#             .table("canvases")
#             .select("id, link_permission")
#             .eq("share_token_hash", token_hash)
#             .eq("link_access_enabled", True)
#             .limit(1)
#             .execute()
#         )

#         if not canvas_result.data:
#             return None

#         canvas = canvas_result.data[0]

#         if canvas.get("link_permission") not in {"commenter", "editor"}:
#             raise PermissionError("This link does not allow comments")

#         guest_name = (guest_name or "Guest").strip()[:80] or "Guest"
#         content = (content or "").strip()

#         if not content:
#             raise ValueError("Comment cannot be empty")
#         if len(content) > 4000:
#             raise ValueError("Comment is too long")

#         self._validate_anchor(anchor_from, anchor_to)

#         row = {
#             "canvas_id": canvas["id"],
#             "author_id": None,
#             "guest_name": guest_name,
#             "content": content,
#             "anchor_from": anchor_from,
#             "anchor_to": anchor_to,
#             "anchor_text": (anchor_text or "")[:300],
#         }

#         result = supabase.table("canvas_comments").insert(row).execute()

#         if not result.data:
#             raise RuntimeError("Failed to create comment")

#         return result.data[0]

#     # ============================================================
#     # Restricted access: requests
#     # ============================================================

#     def request_access(self, canvas_id: str, user_id: str) -> Dict[str, Any]:
#         self._require_canvas_id(canvas_id)

#         if not self._valid_uuid(user_id):
#             raise ValueError("Invalid user ID")

#         canvas_result = (
#             supabase
#             .table("canvases")
#             .select("id, owner_id")
#             .eq("id", canvas_id)
#             .maybe_single()
#             .execute()
#         )

#         if not canvas_result.data:
#             raise LookupError("Canvas not found")

#         canvas = canvas_result.data

#         if canvas["owner_id"] == user_id:
#             raise ValueError("You already own this canvas")

#         if self.get_canvas_access(canvas_id, user_id):
#             raise ValueError("You already have access to this canvas")

#         existing_pending = (
#             supabase
#             .table("canvas_access_requests")
#             .select("id")
#             .eq("canvas_id", canvas_id)
#             .eq("requester_id", user_id)
#             .eq("status", "pending")
#             .maybe_single()
#             .execute()
#         )

#         if existing_pending.data:
#             raise ValueError("You already have a pending request for this canvas")

#         inserted = (
#             supabase
#             .table("canvas_access_requests")
#             .insert({
#                 "canvas_id": canvas_id,
#                 "requester_id": user_id,
#                 "requested_permission": "viewer",
#                 "status": "pending",
#             })
#             .execute()
#         )

#         row = inserted.data[0] if inserted.data else None

#         self._notify(
#             user_id=canvas["owner_id"],
#             canvas_id=canvas_id,
#             type_="access_request",
#             payload={"requester_id": user_id},
#         )

#         return row

#     def _attach_emails(self, rows: List[Dict[str, Any]], id_field: str) -> List[Dict[str, Any]]:
#         """
#         Best-effort: adds an "email" key to each row by looking up
#         the given id_field against `profiles`. If that table doesn't
#         exist or the lookup fails, rows are returned unchanged rather
#         than raising — this is display sugar, not load-bearing.
#         """
#         ids = list({row[id_field] for row in rows if row.get(id_field)})
#         if not ids:
#             return rows

#         try:
#             profiles = (
#                 supabase
#                 .table("profiles")
#                 .select("id, email")
#                 .in_("id", ids)
#                 .execute()
#             )
#             email_by_id = {p["id"]: p.get("email") for p in (profiles.data or [])}
#         except Exception:
#             email_by_id = {}

#         for row in rows:
#             row["email"] = email_by_id.get(row.get(id_field))

#         return rows

#     def list_access_requests(self, canvas_id: str, owner_id: str) -> List[Dict[str, Any]]:
#         self.require_owner(canvas_id, owner_id)

#         result = (
#             supabase
#             .table("canvas_access_requests")
#             .select("id, requester_id, requested_permission, status, created_at")
#             .eq("canvas_id", canvas_id)
#             .eq("status", "pending")
#             .order("created_at", desc=False)
#             .execute()
#         )

#         return self._attach_emails(result.data or [], "requester_id")

#     def respond_to_access_request(
#         self,
#         canvas_id: str,
#         owner_id: str,
#         request_id: str,
#         approve: bool,
#         permission: Optional[str] = None,
#     ) -> Dict[str, Any]:

#         self.require_owner(canvas_id, owner_id)

#         allowed_permissions = {"viewer", "commenter", "editor"}
#         if permission is not None and permission not in allowed_permissions:
#             raise ValueError("Invalid permission")

#         existing = (
#             supabase
#             .table("canvas_access_requests")
#             .select("id, requester_id, requested_permission, status")
#             .eq("id", request_id)
#             .eq("canvas_id", canvas_id)
#             .maybe_single()
#             .execute()
#         )

#         if not existing.data:
#             raise LookupError("Access request not found")

#         request_row = existing.data
#         requester_id = request_row["requester_id"]
#         granted_permission = permission or request_row["requested_permission"] or "viewer"

#         updated = (
#             supabase
#             .table("canvas_access_requests")
#             .update({"status": "approved" if approve else "rejected"})
#             .eq("id", request_id)
#             .execute()
#         )

#         if not updated.data:
#             raise RuntimeError("Failed to update access request")

#         if approve:
#             # Grant (or update) membership now that the owner approved.
#             existing_member = (
#                 supabase
#                 .table("canvas_members")
#                 .select("id")
#                 .eq("canvas_id", canvas_id)
#                 .eq("user_id", requester_id)
#                 .maybe_single()
#                 .execute()
#             )

#             if existing_member.data:
#                 supabase.table("canvas_members").update({
#                     "permission": granted_permission,
#                     "status": "accepted",
#                 }).eq("id", existing_member.data["id"]).execute()
#             else:
#                 supabase.table("canvas_members").insert({
#                     "canvas_id": canvas_id,
#                     "user_id": requester_id,
#                     "permission": granted_permission,
#                     "status": "accepted",
#                 }).execute()

#         self._notify(
#             user_id=requester_id,
#             canvas_id=canvas_id,
#             type_="access_approved" if approve else "access_denied",
#             payload={},
#         )

#         return updated.data[0]

#     # ============================================================
#     # Restricted access: members (direct invite + management)
#     # ============================================================

#     def invite_member(
#         self,
#         canvas_id: str,
#         owner_id: str,
#         email: str,
#         permission: str,
#     ) -> Dict[str, Any]:
#         """
#         Owner grants access to an email address — whether or not that
#         person has ever used the app. If they already have an account,
#         access is granted immediately. Otherwise this just records the
#         invite; it's auto-redeemed the moment that email verifies via
#         the passwordless OTP gate and opens the canvas link (see
#         _try_redeem_invite, called from get_canvas_access).

#         Every lookup here is best-effort — a failure never blocks the
#         invite itself from being recorded.
#         """

#         self.require_owner(canvas_id, owner_id)

#         allowed_permissions = {"viewer", "commenter", "editor"}
#         if permission not in allowed_permissions:
#             raise ValueError("Invalid permission")

#         email = (email or "").strip().lower()
#         if not email:
#             raise ValueError("Email is required")
#         if len(email) > 320 or "@" not in email:
#             raise ValueError("That doesn't look like a valid email")

#         existing_user_id = self._lookup_user_id_by_email(email)

#         if existing_user_id:
#             if existing_user_id == owner_id:
#                 raise ValueError("You already own this canvas")

#             existing_member = (
#                 supabase
#                 .table("canvas_members")
#                 .select("id")
#                 .eq("canvas_id", canvas_id)
#                 .eq("user_id", existing_user_id)
#                 .maybe_single()
#                 .execute()
#             )

#             if existing_member.data:
#                 supabase.table("canvas_members").update({
#                     "permission": permission,
#                     "status": "accepted",
#                 }).eq("id", existing_member.data["id"]).execute()
#             else:
#                 supabase.table("canvas_members").insert({
#                     "canvas_id": canvas_id,
#                     "user_id": existing_user_id,
#                     "permission": permission,
#                     "status": "accepted",
#                 }).execute()

#             self._notify(
#                 user_id=existing_user_id,
#                 canvas_id=canvas_id,
#                 type_="added_to_canvas",
#                 payload={},
#             )

#             return {
#                 "email": email,
#                 "permission": permission,
#                 "status": "accepted",
#                 "pending_signup": False,
#             }

#         # No account yet — record the invite. Upsert so re-inviting the
#         # same email just updates the permission rather than erroring.
#         try:
#             supabase.table("canvas_invites").upsert(
#                 {
#                     "canvas_id": canvas_id,
#                     "email": email,
#                     "permission": permission,
#                     "invited_by": owner_id,
#                 },
#                 on_conflict="canvas_id,email",
#             ).execute()
#         except Exception as exc:
#             raise ValueError(
#                 "Couldn't save that invite. This usually means the "
#                 "`canvas_invites` table hasn't been set up yet — run "
#                 "migrations/004_canvas_invites.sql against this "
#                 "environment's database."
#             ) from exc

#         return {
#             "email": email,
#             "permission": permission,
#             "status": "pending_signup",
#             "pending_signup": True,
#         }

#     def list_pending_invites(self, canvas_id: str, owner_id: str) -> List[Dict[str, Any]]:
#         self.require_owner(canvas_id, owner_id)

#         try:
#             result = (
#                 supabase
#                 .table("canvas_invites")
#                 .select("id, email, permission, created_at")
#                 .eq("canvas_id", canvas_id)
#                 .is_("redeemed_by", "null")
#                 .order("created_at", desc=False)
#                 .execute()
#             )
#             return result.data or []
#         except Exception:
#             # Table missing (migration not run yet) shouldn't take down
#             # the whole Share panel — members/requests still load fine.
#             return []

#     def revoke_invite(self, canvas_id: str, owner_id: str, invite_id: str) -> None:
#         self.require_owner(canvas_id, owner_id)

#         try:
#             result = (
#                 supabase
#                 .table("canvas_invites")
#                 .delete()
#                 .eq("id", invite_id)
#                 .eq("canvas_id", canvas_id)
#                 .is_("redeemed_by", "null")
#                 .execute()
#             )
#         except Exception as exc:
#             raise ValueError(
#                 "Couldn't revoke that invite — the `canvas_invites` table "
#                 "may not be set up yet (migrations/004_canvas_invites.sql)."
#             ) from exc

#         if not result.data:
#             raise LookupError("Invite not found")

#     def list_members(self, canvas_id: str, owner_id: str) -> List[Dict[str, Any]]:
#         self.require_owner(canvas_id, owner_id)

#         result = (
#             supabase
#             .table("canvas_members")
#             .select("id, user_id, permission, status, created_at")
#             .eq("canvas_id", canvas_id)
#             .eq("status", "accepted")
#             .order("created_at", desc=False)
#             .execute()
#         )

#         return self._attach_emails(result.data or [], "user_id")

#     def update_member_permission(
#         self,
#         canvas_id: str,
#         owner_id: str,
#         member_id: str,
#         permission: str,
#     ) -> Dict[str, Any]:

#         self.require_owner(canvas_id, owner_id)

#         allowed_permissions = {"viewer", "commenter", "editor"}
#         if permission not in allowed_permissions:
#             raise ValueError("Invalid permission")

#         updated = (
#             supabase
#             .table("canvas_members")
#             .update({"permission": permission})
#             .eq("id", member_id)
#             .eq("canvas_id", canvas_id)
#             .execute()
#         )

#         if not updated.data:
#             raise LookupError("Member not found")

#         return updated.data[0]

#     def remove_member(self, canvas_id: str, owner_id: str, member_id: str) -> None:
#         self.require_owner(canvas_id, owner_id)

#         result = (
#             supabase
#             .table("canvas_members")
#             .delete()
#             .eq("id", member_id)
#             .eq("canvas_id", canvas_id)
#             .execute()
#         )

#         if not result.data:
#             raise LookupError("Member not found")

#     # ============================================================
#     # Comments
#     # ============================================================

#     def list_comments(self, canvas_id: str, user_id: str) -> List[Dict[str, Any]]:
#         self.require_view_access(canvas_id, user_id)

#         result = (
#             supabase
#             .table("canvas_comments")
#             .select(
#                 "id, canvas_id, author_id, guest_name, content, anchor_from, "
#                 "anchor_to, anchor_text, resolved, created_at"
#             )
#             .eq("canvas_id", canvas_id)
#             .order("created_at", desc=False)
#             .execute()
#         )

#         return result.data or []

#     def create_comment(
#         self,
#         canvas_id: str,
#         user_id: str,
#         content: str,
#         anchor_from: int,
#         anchor_to: int,
#         anchor_text: Optional[str] = None,
#     ) -> Dict[str, Any]:

#         self.require_comment_access(canvas_id, user_id)

#         content = (content or "").strip()
#         if not content:
#             raise ValueError("Comment cannot be empty")
#         if len(content) > 4000:
#             raise ValueError("Comment is too long")

#         self._validate_anchor(anchor_from, anchor_to)

#         row = {
#             "canvas_id": canvas_id,
#             "author_id": user_id,
#             "content": content,
#             "anchor_from": anchor_from,
#             "anchor_to": anchor_to,
#             "anchor_text": (anchor_text or "")[:300],
#         }

#         result = supabase.table("canvas_comments").insert(row).execute()

#         if not result.data:
#             raise RuntimeError("Failed to create comment")

#         return result.data[0]

#     def resolve_comment(
#         self,
#         canvas_id: str,
#         user_id: str,
#         comment_id: str,
#         resolved: bool = True,
#     ) -> Dict[str, Any]:

#         access = self.require_view_access(canvas_id, user_id)
#         if access not in {"owner", "editor", "commenter"}:
#             raise PermissionError("You cannot resolve comments")

#         updated = (
#             supabase
#             .table("canvas_comments")
#             .update({"resolved": resolved})
#             .eq("id", comment_id)
#             .eq("canvas_id", canvas_id)
#             .execute()
#         )

#         if not updated.data:
#             raise LookupError("Comment not found")

#         return updated.data[0]

#     def delete_comment(self, canvas_id: str, user_id: str, comment_id: str) -> None:
#         access = self.get_canvas_access(canvas_id, user_id)

#         result = (
#             supabase
#             .table("canvas_comments")
#             .select("id, author_id")
#             .eq("id", comment_id)
#             .eq("canvas_id", canvas_id)
#             .maybe_single()
#             .execute()
#         )

#         if not result.data:
#             raise LookupError("Comment not found")

#         if result.data["author_id"] != user_id and access != "owner":
#             raise PermissionError("You cannot delete this comment")

#         supabase.table("canvas_comments").delete().eq("id", comment_id).execute()

#     # ============================================================
#     # Notifications
#     # ============================================================

#     def list_notifications(
#         self,
#         user_id: str,
#         unread_only: bool = False,
#     ) -> List[Dict[str, Any]]:

#         if not self._valid_uuid(user_id):
#             raise ValueError("Invalid user ID")

#         query = (
#             supabase
#             .table("canvas_notifications")
#             .select("id, canvas_id, type, payload, read, created_at")
#             .eq("user_id", user_id)
#             .order("created_at", desc=True)
#             .limit(50)
#         )

#         if unread_only:
#             query = query.eq("read", False)

#         result = query.execute()
#         return result.data or []

#     def mark_notification_read(self, user_id: str, notification_id: str) -> Dict[str, Any]:
#         updated = (
#             supabase
#             .table("canvas_notifications")
#             .update({"read": True})
#             .eq("id", notification_id)
#             .eq("user_id", user_id)
#             .execute()
#         )

#         if not updated.data:
#             raise LookupError("Notification not found")

#         return updated.data[0]

















"""
Canvas database and authorization layer.

IMPORTANT:
This module uses the Supabase service-role client, so RLS is bypassed.

Therefore every operation MUST explicitly enforce:
- owner access
- accepted membership
- viewer/commenter/editor permissions

Never trust user_id supplied by the client.
The caller's user_id must come from the authenticated JWT.
"""

from typing import Optional, Dict, Any, List
from uuid import UUID
from datetime import datetime, timezone
import hashlib
import secrets

from db.client import supabase


CANVAS_SELECT_FIELDS = """
    id,
    owner_id,
    title,
    content,
    visibility,
    link_access_enabled,
    link_permission,
    created_at,
    updated_at
"""


class CanvasManager:

    # ============================================================
    # Helpers
    # ============================================================

    @staticmethod
    def _valid_uuid(value: str) -> bool:
        try:
            UUID(str(value))
            return True
        except (ValueError, TypeError, AttributeError):
            return False

    def _require_canvas_id(self, canvas_id: str) -> None:
        if not self._valid_uuid(canvas_id):
            raise ValueError("Invalid canvas ID")

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _hash_share_token(token: str) -> str:
        """Hash a raw share token before any database lookup/storage."""
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def _notify(
        self,
        user_id: str,
        canvas_id: Optional[str],
        type_: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Best-effort notification insert. Never raises — a failed
        notification should never break the operation that triggered it.
        """
        try:
            supabase.table("canvas_notifications").insert({
                "user_id": user_id,
                "canvas_id": canvas_id,
                "type": type_,
                "payload": payload or {},
            }).execute()
        except Exception:
            pass

    # ============================================================
    # Authorization
    # ============================================================

    def get_canvas_access(
        self,
        canvas_id: str,
        user_id: str,
    ) -> Optional[str]:
        """
        Return:
            owner
            viewer
            commenter
            editor
            None

        This is the central authorization decision. Covers three paths:
        1. Owner
        2. Accepted canvas_members row
        3. canvas.visibility == 'anyone' (open to any authenticated user,
           at whatever permission the link currently grants)
        """

        self._require_canvas_id(canvas_id)

        canvas_result = (
            supabase
            .table("canvases")
            .select("id, owner_id, visibility, link_permission")
            .eq("id", canvas_id)
            .maybe_single()
            .execute()
        )

        if not canvas_result.data:
            return None

        canvas = canvas_result.data

        if canvas["owner_id"] == user_id:
            return "owner"

        member_result = (
            supabase
            .table("canvas_members")
            .select("permission")
            .eq("canvas_id", canvas_id)
            .eq("user_id", user_id)
            .eq("status", "accepted")
            .maybe_single()
            .execute()
        )

        if member_result.data:
            return member_result.data["permission"]

        if canvas.get("visibility") == "anyone":
            return canvas.get("link_permission") or "viewer"

        return None

    def require_view_access(self, canvas_id: str, user_id: str) -> str:
        access = self.get_canvas_access(canvas_id, user_id)
        if access is None:
            raise PermissionError("Canvas access denied")
        return access

    def require_comment_access(self, canvas_id: str, user_id: str) -> str:
        access = self.get_canvas_access(canvas_id, user_id)
        if access not in {"owner", "editor", "commenter"}:
            raise PermissionError("Comment access denied")
        return access

    def require_edit_access(self, canvas_id: str, user_id: str) -> str:
        access = self.get_canvas_access(canvas_id, user_id)
        if access not in {"owner", "editor"}:
            raise PermissionError("Canvas edit access denied")
        return access

    def require_owner(self, canvas_id: str, user_id: str) -> None:
        access = self.get_canvas_access(canvas_id, user_id)
        if access != "owner":
            raise PermissionError("Canvas owner access required")

    # ============================================================
    # Create
    # ============================================================

    def create_canvas(
        self,
        user_id: str,
        title: str = "Untitled Canvas",
        content: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:

        if not self._valid_uuid(user_id):
            raise ValueError("Invalid user ID")

        title = (title or "Untitled Canvas").strip()

        if len(title) > 200:
            raise ValueError("Canvas title is too long")

        row = {
            # IMPORTANT: comes from the verified JWT, NOT frontend input.
            "owner_id": user_id,
            "title": title,
            "content": content or {"type": "doc", "content": []},
            "visibility": "restricted",
        }

        result = supabase.table("canvases").insert(row).execute()
        return result.data[0] if isinstance(result.data, list) else result.data

    # ============================================================
    # Get one canvas
    # ============================================================

    def get_canvas(self, canvas_id: str, user_id: str) -> Optional[Dict[str, Any]]:
        self.require_view_access(canvas_id, user_id)

        result = (
            supabase
            .table("canvases")
            .select(CANVAS_SELECT_FIELDS)
            .eq("id", canvas_id)
            .maybe_single()
            .execute()
        )

        return result.data

    # ============================================================
    # Access status (for the "Request access" screen)
    # ============================================================

    def get_access_status(self, canvas_id: str, user_id: str) -> Dict[str, Any]:
        self._require_canvas_id(canvas_id)

        if not self._valid_uuid(user_id):
            raise ValueError("Invalid user ID")

        canvas_result = (
            supabase
            .table("canvases")
            .select("id, title, owner_id, visibility")
            .eq("id", canvas_id)
            .maybe_single()
            .execute()
        )

        if not canvas_result.data:
            raise LookupError("Canvas not found")

        canvas = canvas_result.data
        access = self.get_canvas_access(canvas_id, user_id)

        if access:
            return {
                "has_access": True,
                "access_level": access,
                "title": canvas["title"],
                "request_status": None,
            }

        # Most recent request (if any) determines what the UI shows —
        # a fresh "Request access" button, a pending state, or a
        # "request again" state after a rejection.
        request_result = (
            supabase
            .table("canvas_access_requests")
            .select("status")
            .eq("canvas_id", canvas_id)
            .eq("requester_id", user_id)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )

        request_rows = request_result.data or []
        request_status = request_rows[0]["status"] if request_rows else None

        return {
            "has_access": False,
            "access_level": None,
            "title": canvas["title"],
            "request_status": request_status,  # None | "pending" | "rejected" | "approved"
        }

    # ============================================================
    # List user's accessible canvases
    # ============================================================

    def list_canvases(self, user_id: str) -> list:
        if not self._valid_uuid(user_id):
            raise ValueError("Invalid user ID")

        owned = (
            supabase
            .table("canvases")
            .select(CANVAS_SELECT_FIELDS)
            .eq("owner_id", user_id)
            .order("updated_at", desc=True)
            .execute()
        )

        memberships = (
            supabase
            .table("canvas_members")
            .select("canvas_id, permission")
            .eq("user_id", user_id)
            .eq("status", "accepted")
            .execute()
        )

        member_canvas_ids = [row["canvas_id"] for row in (memberships.data or [])]

        shared = []
        if member_canvas_ids:
            shared_result = (
                supabase
                .table("canvases")
                .select(CANVAS_SELECT_FIELDS)
                .in_("id", member_canvas_ids)
                .order("updated_at", desc=True)
                .execute()
            )
            shared = shared_result.data or []

        combined = {}
        for canvas in owned.data or []:
            combined[canvas["id"]] = canvas
        for canvas in shared:
            combined[canvas["id"]] = canvas

        return sorted(
            combined.values(),
            key=lambda x: x["updated_at"],
            reverse=True,
        )

    # ============================================================
    # Update content / title
    # ============================================================

    def update_canvas_content(
        self,
        canvas_id: str,
        user_id: str,
        content: Dict[str, Any],
    ) -> Dict[str, Any]:

        self.require_edit_access(canvas_id, user_id)

        result = (
            supabase
            .table("canvases")
            .update({"content": content})
            .eq("id", canvas_id)
            .execute()
        )

        if not result.data:
            raise LookupError("Canvas not found")

        return result.data[0]

    def update_canvas_title(
        self,
        canvas_id: str,
        user_id: str,
        title: str,
    ) -> Dict[str, Any]:

        self.require_edit_access(canvas_id, user_id)

        title = title.strip()
        if not title:
            raise ValueError("Canvas title cannot be empty")
        if len(title) > 200:
            raise ValueError("Canvas title is too long")

        result = (
            supabase
            .table("canvases")
            .update({"title": title})
            .eq("id", canvas_id)
            .execute()
        )

        if not result.data:
            raise LookupError("Canvas not found")

        return result.data[0]

    # ============================================================
    # Delete
    # ============================================================

    def delete_canvas(self, canvas_id: str, user_id: str) -> None:
        self.require_owner(canvas_id, user_id)

        result = (
            supabase
            .table("canvases")
            .delete()
            .eq("id", canvas_id)
            .execute()
        )

        if not result.data:
            raise LookupError("Canvas not found")

    # ============================================================
    # Visibility (restricted <-> anyone)
    # ============================================================

    def set_visibility(
        self,
        canvas_id: str,
        owner_id: str,
        visibility: str,
    ) -> Dict[str, Any]:

        self.require_owner(canvas_id, owner_id)

        if visibility not in {"restricted", "anyone"}:
            raise ValueError("Invalid visibility")

        update_data: Dict[str, Any] = {"visibility": visibility}

        if visibility == "restricted":
            # Fully revoke any public link when going restricted —
            # the old token must never work again.
            update_data["link_access_enabled"] = False
            update_data["share_token_hash"] = None

        updated = (
            supabase
            .table("canvases")
            .update(update_data)
            .eq("id", canvas_id)
            .eq("owner_id", owner_id)
            .execute()
        )

        if not updated.data:
            raise LookupError("Canvas not found")

        return updated.data[0]

    # ============================================================
    # Public link sharing ("anyone with the link")
    # ============================================================

    def create_share_link(
        self,
        canvas_id: str,
        user_id: str,
        permission: str,
    ) -> dict:
        """
        Create (or replace) a secure public share token for a canvas
        and flip visibility to 'anyone'.

        Only the canvas owner may create a share link.
        The raw token is returned exactly once and is never stored.
        """

        allowed_permissions = {"viewer", "commenter", "editor"}

        if permission not in allowed_permissions:
            raise ValueError("Invalid share permission")

        if not self._valid_uuid(user_id):
            raise ValueError("Invalid user ID")

        if not self._valid_uuid(canvas_id):
            raise ValueError("Invalid canvas ID")

        self.require_owner(canvas_id=canvas_id, user_id=user_id)

        raw_token = secrets.token_urlsafe(32)
        token_hash = self._hash_share_token(raw_token)

        updated = (
            supabase
            .table("canvases")
            .update({
                "link_access_enabled": True,
                "link_permission": permission,
                "share_token_hash": token_hash,
                "visibility": "anyone",
            })
            .eq("id", canvas_id)
            .eq("owner_id", user_id)
            .execute()
        )

        if not updated.data:
            raise RuntimeError("Failed to create share link")

        result = updated.data[0]

        return {
            "canvas_id": result["id"],
            "permission": result["link_permission"],
            "token": raw_token,
        }

    def get_share_link_settings(
        self,
        canvas_id: str,
        user_id: str,
    ) -> Dict[str, Any]:
        """
        Return share-link configuration for a canvas.
        Only the canvas owner can access share-link management.
        Never returns the raw token or share_token_hash.
        """

        self.require_owner(canvas_id=canvas_id, user_id=user_id)

        result = (
            supabase
            .table("canvases")
            .select(
                "id, visibility, link_access_enabled, link_permission, share_token_hash"
            )
            .eq("id", canvas_id)
            .eq("owner_id", user_id)
            .maybe_single()
            .execute()
        )

        if not result.data:
            raise LookupError("Canvas not found")

        canvas = result.data

        return {
            "canvas_id": canvas["id"],
            "visibility": canvas.get("visibility"),
            "link_access_enabled": bool(canvas.get("link_access_enabled")),
            "link_permission": canvas.get("link_permission"),
            "has_active_link": bool(
                canvas.get("link_access_enabled") and canvas.get("share_token_hash")
            ),
        }

    def update_share_link_settings(
        self,
        canvas_id: str,
        user_id: str,
        permission: Optional[str] = None,
        link_access_enabled: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """
        Update share-link settings (permission and/or on/off).
        Disabling destroys the stored token hash; re-enabling requires
        a new token via regenerate_share_link().
        """

        self.require_owner(canvas_id=canvas_id, user_id=user_id)

        allowed_permissions = {"viewer", "commenter", "editor"}

        if permission is not None and permission not in allowed_permissions:
            raise ValueError("Invalid share permission")

        if permission is None and link_access_enabled is None:
            raise ValueError("At least one share setting must be provided")

        current = (
            supabase
            .table("canvases")
            .select(
                "id, link_access_enabled, link_permission, share_token_hash"
            )
            .eq("id", canvas_id)
            .eq("owner_id", user_id)
            .maybe_single()
            .execute()
        )

        if not current.data:
            raise LookupError("Canvas not found")

        canvas = current.data
        update_data: Dict[str, Any] = {}

        if permission is not None:
            update_data["link_permission"] = permission

        if link_access_enabled is False:
            update_data["link_access_enabled"] = False
            update_data["share_token_hash"] = None

        elif link_access_enabled is True:
            if not canvas.get("share_token_hash"):
                raise ValueError(
                    "No active share link exists. "
                    "Generate a new share link instead."
                )
            update_data["link_access_enabled"] = True

        updated = (
            supabase
            .table("canvases")
            .update(update_data)
            .eq("id", canvas_id)
            .eq("owner_id", user_id)
            .execute()
        )

        if not updated.data:
            raise RuntimeError("Failed to update share-link settings")

        result = updated.data[0]

        return {
            "canvas_id": result["id"],
            "visibility": result.get("visibility"),
            "link_access_enabled": bool(result.get("link_access_enabled")),
            "link_permission": result.get("link_permission"),
            "has_active_link": bool(
                result.get("link_access_enabled") and result.get("share_token_hash")
            ),
        }

    def revoke_share_link(self, canvas_id: str, user_id: str) -> None:
        self.require_owner(canvas_id=canvas_id, user_id=user_id)

        result = (
            supabase
            .table("canvases")
            .update({
                "link_access_enabled": False,
                "share_token_hash": None,
            })
            .eq("id", canvas_id)
            .eq("owner_id", user_id)
            .execute()
        )

        if not result.data:
            raise LookupError("Canvas not found")

    def regenerate_share_link(
        self,
        canvas_id: str,
        user_id: str,
        permission: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Generate a completely new public share token; the old token is
        immediately invalidated. Raw token returned exactly once.
        """

        self.require_owner(canvas_id=canvas_id, user_id=user_id)

        allowed_permissions = {"viewer", "commenter", "editor"}

        if permission is not None and permission not in allowed_permissions:
            raise ValueError("Invalid share permission")

        raw_token = secrets.token_urlsafe(32)
        token_hash = self._hash_share_token(raw_token)

        update_data = {
            "link_access_enabled": True,
            "share_token_hash": token_hash,
            "visibility": "anyone",
        }

        if permission is not None:
            update_data["link_permission"] = permission

        updated = (
            supabase
            .table("canvases")
            .update(update_data)
            .eq("id", canvas_id)
            .eq("owner_id", user_id)
            .execute()
        )

        if not updated.data:
            raise RuntimeError("Failed to regenerate share link")

        result = updated.data[0]

        return {
            "canvas_id": result["id"],
            "permission": result["link_permission"],
            "token": raw_token,
        }

    # ============================================================
    # Public (token-based, unauthenticated) access — "anyone" mode
    # ============================================================

    def get_public_canvas(self, token: str) -> Optional[Dict[str, Any]]:
        if not token or len(token) > 512:
            return None

        token_hash = self._hash_share_token(token.strip())

        result = (
            supabase
            .table("canvases")
            .select("id, title, content, link_permission")
            .eq("share_token_hash", token_hash)
            .eq("link_access_enabled", True)
            .limit(1)
            .execute()
        )

        if not result.data:
            return None

        canvas = result.data[0]

        return {
            "id": canvas["id"],
            "title": canvas["title"],
            "content": canvas["content"],
            "permission": canvas["link_permission"],
        }

    def update_public_canvas_content(
        self,
        token: str,
        content: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        if not token or len(token) > 512:
            return None

        token_hash = self._hash_share_token(token.strip())

        lookup = (
            supabase
            .table("canvases")
            .select("id, link_permission")
            .eq("share_token_hash", token_hash)
            .eq("link_access_enabled", True)
            .limit(1)
            .execute()
        )

        if not lookup.data:
            return None

        canvas = lookup.data[0]

        if canvas.get("link_permission") != "editor":
            raise PermissionError("Canvas is not editable")

        canvas_id = canvas["id"]

        result = (
            supabase
            .table("canvases")
            .update({"content": content})
            .eq("id", canvas_id)
            .eq("share_token_hash", token_hash)
            .eq("link_access_enabled", True)
            .eq("link_permission", "editor")
            .execute()
        )

        if not result.data:
            return None

        updated = result.data[0]

        return {
            "id": updated["id"],
            "title": updated["title"],
            "content": updated["content"],
            "permission": updated["link_permission"],
        }

    # ============================================================
    # Guest comments (public link, no account required)
    # ============================================================

    def list_public_comments(self, token: str) -> List[Dict[str, Any]]:
        if not token or len(token) > 512:
            return []

        token_hash = self._hash_share_token(token.strip())

        canvas_result = (
            supabase
            .table("canvases")
            .select("id")
            .eq("share_token_hash", token_hash)
            .eq("link_access_enabled", True)
            .limit(1)
            .execute()
        )

        if not canvas_result.data:
            return []

        canvas_id = canvas_result.data[0]["id"]

        result = (
            supabase
            .table("canvas_comments")
            .select(
                "id, canvas_id, author_id, guest_name, content, anchor_from, "
                "anchor_to, anchor_text, resolved, created_at"
            )
            .eq("canvas_id", canvas_id)
            .order("created_at", desc=False)
            .execute()
        )

        return result.data or []

    @staticmethod
    def _validate_anchor(anchor_from: int, anchor_to: int) -> None:
        if (
            not isinstance(anchor_from, int)
            or not isinstance(anchor_to, int)
            or anchor_from < 0
            or anchor_to <= anchor_from
            or anchor_to - anchor_from > 20000  # sanity bound, not a real doc size limit
        ):
            raise ValueError("Invalid comment anchor")

    def create_guest_comment(
        self,
        token: str,
        guest_name: str,
        content: str,
        anchor_from: int,
        anchor_to: int,
        anchor_text: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        if not token or len(token) > 512:
            return None

        token_hash = self._hash_share_token(token.strip())

        canvas_result = (
            supabase
            .table("canvases")
            .select("id, link_permission")
            .eq("share_token_hash", token_hash)
            .eq("link_access_enabled", True)
            .limit(1)
            .execute()
        )

        if not canvas_result.data:
            return None

        canvas = canvas_result.data[0]

        if canvas.get("link_permission") not in {"commenter", "editor"}:
            raise PermissionError("This link does not allow comments")

        guest_name = (guest_name or "Guest").strip()[:80] or "Guest"
        content = (content or "").strip()

        if not content:
            raise ValueError("Comment cannot be empty")
        if len(content) > 4000:
            raise ValueError("Comment is too long")

        self._validate_anchor(anchor_from, anchor_to)

        row = {
            "canvas_id": canvas["id"],
            "author_id": None,
            "guest_name": guest_name,
            "content": content,
            "anchor_from": anchor_from,
            "anchor_to": anchor_to,
            "anchor_text": (anchor_text or "")[:300],
        }

        result = supabase.table("canvas_comments").insert(row).execute()

        if not result.data:
            raise RuntimeError("Failed to create comment")

        return result.data[0]

    # ============================================================
    # Restricted access: requests
    # ============================================================

    def request_access(self, canvas_id: str, user_id: str) -> Dict[str, Any]:
        self._require_canvas_id(canvas_id)

        if not self._valid_uuid(user_id):
            raise ValueError("Invalid user ID")

        canvas_result = (
            supabase
            .table("canvases")
            .select("id, owner_id")
            .eq("id", canvas_id)
            .maybe_single()
            .execute()
        )

        if not canvas_result.data:
            raise LookupError("Canvas not found")

        canvas = canvas_result.data

        if canvas["owner_id"] == user_id:
            raise ValueError("You already own this canvas")

        if self.get_canvas_access(canvas_id, user_id):
            raise ValueError("You already have access to this canvas")

        existing_pending = (
            supabase
            .table("canvas_access_requests")
            .select("id")
            .eq("canvas_id", canvas_id)
            .eq("requester_id", user_id)
            .eq("status", "pending")
            .maybe_single()
            .execute()
        )

        if existing_pending.data:
            raise ValueError("You already have a pending request for this canvas")

        inserted = (
            supabase
            .table("canvas_access_requests")
            .insert({
                "canvas_id": canvas_id,
                "requester_id": user_id,
                "requested_permission": "viewer",
                "status": "pending",
            })
            .execute()
        )

        row = inserted.data[0] if inserted.data else None

        self._notify(
            user_id=canvas["owner_id"],
            canvas_id=canvas_id,
            type_="access_request",
            payload={"requester_id": user_id},
        )

        return row

    def _attach_emails(self, rows: List[Dict[str, Any]], id_field: str) -> List[Dict[str, Any]]:
        """
        Best-effort: adds an "email" key to each row by looking up
        the given id_field against `profiles`. If that table doesn't
        exist or the lookup fails, rows are returned unchanged rather
        than raising — this is display sugar, not load-bearing.
        """
        ids = list({row[id_field] for row in rows if row.get(id_field)})
        if not ids:
            return rows

        try:
            profiles = (
                supabase
                .table("profiles")
                .select("id, email")
                .in_("id", ids)
                .execute()
            )
            email_by_id = {p["id"]: p.get("email") for p in (profiles.data or [])}
        except Exception:
            email_by_id = {}

        for row in rows:
            row["email"] = email_by_id.get(row.get(id_field))

        return rows

    def list_access_requests(self, canvas_id: str, owner_id: str) -> List[Dict[str, Any]]:
        self.require_owner(canvas_id, owner_id)

        result = (
            supabase
            .table("canvas_access_requests")
            .select("id, requester_id, requested_permission, status, created_at")
            .eq("canvas_id", canvas_id)
            .eq("status", "pending")
            .order("created_at", desc=False)
            .execute()
        )

        return self._attach_emails(result.data or [], "requester_id")

    def respond_to_access_request(
        self,
        canvas_id: str,
        owner_id: str,
        request_id: str,
        approve: bool,
        permission: Optional[str] = None,
    ) -> Dict[str, Any]:

        self.require_owner(canvas_id, owner_id)

        allowed_permissions = {"viewer", "commenter", "editor"}
        if permission is not None and permission not in allowed_permissions:
            raise ValueError("Invalid permission")

        existing = (
            supabase
            .table("canvas_access_requests")
            .select("id, requester_id, requested_permission, status")
            .eq("id", request_id)
            .eq("canvas_id", canvas_id)
            .maybe_single()
            .execute()
        )

        if not existing.data:
            raise LookupError("Access request not found")

        request_row = existing.data
        requester_id = request_row["requester_id"]
        granted_permission = permission or request_row["requested_permission"] or "viewer"

        updated = (
            supabase
            .table("canvas_access_requests")
            .update({"status": "approved" if approve else "rejected"})
            .eq("id", request_id)
            .execute()
        )

        if not updated.data:
            raise RuntimeError("Failed to update access request")

        if approve:
            # Grant (or update) membership now that the owner approved.
            existing_member = (
                supabase
                .table("canvas_members")
                .select("id")
                .eq("canvas_id", canvas_id)
                .eq("user_id", requester_id)
                .maybe_single()
                .execute()
            )

            if existing_member.data:
                supabase.table("canvas_members").update({
                    "permission": granted_permission,
                    "status": "accepted",
                }).eq("id", existing_member.data["id"]).execute()
            else:
                supabase.table("canvas_members").insert({
                    "canvas_id": canvas_id,
                    "user_id": requester_id,
                    "permission": granted_permission,
                    "status": "accepted",
                }).execute()

        self._notify(
            user_id=requester_id,
            canvas_id=canvas_id,
            type_="access_approved" if approve else "access_denied",
            payload={},
        )

        return updated.data[0]

    # ============================================================
    # Restricted access: members (direct invite + management)
    # ============================================================


    def invite_member(
        self,
        canvas_id: str,
        owner_id: str,
        email: str,
        permission: str,
    ) -> Dict[str, Any]:
        """
        Create a pending canvas invitation for an email address.

        The invited person does NOT need to have an account yet.

        The invitation remains pending until the recipient authenticates
        with the invited email address and accepts the invitation.
        """

        self.require_owner(canvas_id, owner_id)

        allowed_permissions = {"viewer", "commenter", "editor"}

        if permission not in allowed_permissions:
            raise ValueError("Invalid permission")

        email = (email or "").strip().lower()

        if not email:
            raise ValueError("Email is required")

        # Basic email validation.
        if "@" not in email or "." not in email.rsplit("@", 1)[-1]:
            raise ValueError("Please enter a valid email address")

        # Owner cannot invite themselves.
        owner_profile = (
            supabase
            .table("profiles")
            .select("email")
            .eq("id", owner_id)
            .maybe_single()
            .execute()
        )

        if (
            owner_profile.data
            and owner_profile.data.get("email")
            and owner_profile.data["email"].strip().lower() == email
        ):
            raise ValueError("You already own this canvas")

        # ------------------------------------------------------------
        # Generate a random invitation token.
        #
        # Raw token:
        #   sent to the client in the invitation URL
        #
        # Hash:
        #   stored in the database
        #
        # We NEVER store the raw invitation token.
        # ------------------------------------------------------------

        raw_token = secrets.token_urlsafe(32)
        token_hash = self._hash_share_token(raw_token)

        # ------------------------------------------------------------
        # Check whether this email already has an invitation
        # for this canvas.
        # ------------------------------------------------------------



        existing = (
            supabase
            .table("canvas_invitations")
            .select("id, status")
            .eq("canvas_id", canvas_id)
            .eq("email", email)
            .limit(1)
            .execute()
        )

        existing_row = existing.data[0] if existing and existing.data else None

        if existing_row:
            # Re-inviting the same email updates the existing invitation.
            updated = (
                supabase
                .table("canvas_invitations")
                .update({
                    "permission": permission,
                    "status": "pending",
                    "invited_by": owner_id,
                    "token_hash": token_hash,
                    "accepted_at": None,
                })
                .eq("id", existing_row["id"])
                .execute()
            )

            if not updated.data:
                raise RuntimeError("Failed to update canvas invitation")

            invitation = updated.data[0]

        else:
            # New invitation.
            created = (
                supabase
                .table("canvas_invitations")
                .insert({
                    "canvas_id": canvas_id,
                    "email": email,
                    "permission": permission,
                    "status": "pending",
                    "invited_by": owner_id,
                    "token_hash": token_hash,
                })
                .execute()
            )

            if not created.data:
                raise RuntimeError("Failed to create canvas invitation")

            invitation = created.data[0]

       

        # ------------------------------------------------------------
        # IMPORTANT:
        #
        # We do NOT create canvas_members here.
        #
        # canvas_members will be created only after the client
        # authenticates and the email matches this invitation.
        # ------------------------------------------------------------

        return {
            "id": invitation["id"],
            "canvas_id": canvas_id,
            "email": email,
            "permission": invitation["permission"],
            "status": invitation["status"],
            "token": raw_token,
        }

    def list_members(self, canvas_id: str, owner_id: str) -> List[Dict[str, Any]]:
        self.require_owner(canvas_id, owner_id)

        result = (
            supabase
            .table("canvas_members")
            .select("id, user_id, permission, status, created_at")
            .eq("canvas_id", canvas_id)
            .eq("status", "accepted")
            .order("created_at", desc=False)
            .execute()
        )

        return self._attach_emails(result.data or [], "user_id")

    def update_member_permission(
        self,
        canvas_id: str,
        owner_id: str,
        member_id: str,
        permission: str,
    ) -> Dict[str, Any]:

        self.require_owner(canvas_id, owner_id)

        allowed_permissions = {"viewer", "commenter", "editor"}
        if permission not in allowed_permissions:
            raise ValueError("Invalid permission")

        updated = (
            supabase
            .table("canvas_members")
            .update({"permission": permission})
            .eq("id", member_id)
            .eq("canvas_id", canvas_id)
            .execute()
        )

        if not updated.data:
            raise LookupError("Member not found")

        return updated.data[0]

    def remove_member(self, canvas_id: str, owner_id: str, member_id: str) -> None:
        self.require_owner(canvas_id, owner_id)

        result = (
            supabase
            .table("canvas_members")
            .delete()
            .eq("id", member_id)
            .eq("canvas_id", canvas_id)
            .execute()
        )

        if not result.data:
            raise LookupError("Member not found")

    # ============================================================
    # Comments
    # ============================================================

    def list_comments(self, canvas_id: str, user_id: str) -> List[Dict[str, Any]]:
        self.require_view_access(canvas_id, user_id)

        result = (
            supabase
            .table("canvas_comments")
            .select(
                "id, canvas_id, author_id, guest_name, content, anchor_from, "
                "anchor_to, anchor_text, resolved, created_at"
            )
            .eq("canvas_id", canvas_id)
            .order("created_at", desc=False)
            .execute()
        )

        return result.data or []

    def create_comment(
        self,
        canvas_id: str,
        user_id: str,
        content: str,
        anchor_from: int,
        anchor_to: int,
        anchor_text: Optional[str] = None,
    ) -> Dict[str, Any]:

        self.require_comment_access(canvas_id, user_id)

        content = (content or "").strip()
        if not content:
            raise ValueError("Comment cannot be empty")
        if len(content) > 4000:
            raise ValueError("Comment is too long")

        self._validate_anchor(anchor_from, anchor_to)

        row = {
            "canvas_id": canvas_id,
            "author_id": user_id,
            "content": content,
            "anchor_from": anchor_from,
            "anchor_to": anchor_to,
            "anchor_text": (anchor_text or "")[:300],
        }

        result = supabase.table("canvas_comments").insert(row).execute()

        if not result.data:
            raise RuntimeError("Failed to create comment")

        return result.data[0]

    def resolve_comment(
        self,
        canvas_id: str,
        user_id: str,
        comment_id: str,
        resolved: bool = True,
    ) -> Dict[str, Any]:

        access = self.require_view_access(canvas_id, user_id)
        if access not in {"owner", "editor", "commenter"}:
            raise PermissionError("You cannot resolve comments")

        updated = (
            supabase
            .table("canvas_comments")
            .update({"resolved": resolved})
            .eq("id", comment_id)
            .eq("canvas_id", canvas_id)
            .execute()
        )

        if not updated.data:
            raise LookupError("Comment not found")

        return updated.data[0]

    def delete_comment(self, canvas_id: str, user_id: str, comment_id: str) -> None:
        access = self.get_canvas_access(canvas_id, user_id)

        result = (
            supabase
            .table("canvas_comments")
            .select("id, author_id")
            .eq("id", comment_id)
            .eq("canvas_id", canvas_id)
            .maybe_single()
            .execute()
        )

        if not result.data:
            raise LookupError("Comment not found")

        if result.data["author_id"] != user_id and access != "owner":
            raise PermissionError("You cannot delete this comment")

        supabase.table("canvas_comments").delete().eq("id", comment_id).execute()

    # ============================================================
    # Notifications
    # ============================================================

    def list_notifications(
        self,
        user_id: str,
        unread_only: bool = False,
    ) -> List[Dict[str, Any]]:

        if not self._valid_uuid(user_id):
            raise ValueError("Invalid user ID")

        query = (
            supabase
            .table("canvas_notifications")
            .select("id, canvas_id, type, payload, read, created_at")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .limit(50)
        )

        if unread_only:
            query = query.eq("read", False)

        result = query.execute()
        return result.data or []

    def mark_notification_read(self, user_id: str, notification_id: str) -> Dict[str, Any]:
        updated = (
            supabase
            .table("canvas_notifications")
            .update({"read": True})
            .eq("id", notification_id)
            .eq("user_id", user_id)
            .execute()
        )

        if not updated.data:
            raise LookupError("Notification not found")

        return updated.data[0]