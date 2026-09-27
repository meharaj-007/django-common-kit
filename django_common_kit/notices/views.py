"""The two endpoints a frontend needs (PRD §16). Mount them in the project::

    path("notices/", LiveNoticeListView.as_view(), name="notices"),
    path("notices/<uuid:notice_id>/dismiss/", NoticeDismissView.as_view(), name="notice_dismiss"),

``GET notices/?surface=web`` answers the live notices for the viewer, in the
envelope; ``POST notices/<id>/dismiss/`` hides one from that user everywhere.

Both take ``IsAuthenticatedActive`` and not the project's
``PERMISSION_CLASSES``: every signed-in user may read the notices meant for
them, and an RBAC class that asks which permission the view needs has no good
answer here. A project that shows notices before sign-in — a maintenance
banner on the login page — subclasses the list view with ``AllowAny``; an
anonymous viewer has no tenant and no audience unless the resolvers give it
one, so it sees only notices meant for everyone.
"""

from rest_framework.views import APIView

from django_common_kit.api.permissions import IsAuthenticatedActive
from django_common_kit.api.response import ApiResponse
from django_common_kit.notices.service import dismiss, find_visible, live_notices, notice_payload


class LiveNoticeListView(APIView):
    permission_classes = [IsAuthenticatedActive]

    def get(self, request):
        notices = live_notices(request, surface=request.query_params.get("surface") or None)
        return ApiResponse.success(
            data=[notice_payload(notice) for notice in notices],
            message="Notices retrieved successfully",
        )


class NoticeDismissView(APIView):
    permission_classes = [IsAuthenticatedActive]

    def post(self, request, notice_id):
        notice = find_visible(request, notice_id)
        if notice is None:
            return ApiResponse.not_found("Notice not found")
        try:
            dismiss(notice, request.user)
        except ValueError as exc:
            return ApiResponse.bad_request(str(exc))
        return ApiResponse.success(message="Notice dismissed")
