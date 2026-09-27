from django.urls import path

from django_common_kit.notices.views import LiveNoticeListView, NoticeDismissView
from django_common_kit.shortlinks.views import short_link_redirect
from tests.views import BoomView, ThrottledView, ValidatedView

urlpatterns = [
    path("s/<slug:slug>/", short_link_redirect),
    path("validated/", ValidatedView.as_view()),
    path("boom/", BoomView.as_view()),
    path("throttled/", ThrottledView.as_view()),
    path("notices/", LiveNoticeListView.as_view()),
    path("notices/<uuid:notice_id>/dismiss/", NoticeDismissView.as_view()),
]
