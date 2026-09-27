"""Platform notices (PRD §16)."""

import itertools
import uuid
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from django_common_utils.models import ModelHistory, PlatformNoticeDismissalModel, PlatformNoticeModel
from django_common_utils.notices import dismiss, live_notices
from django_common_utils.notices.service import invalidate_cache
from django_common_utils.request_context import set_current_request

User = get_user_model()
TENANT = uuid.uuid4()
OTHER_TENANT = uuid.uuid4()


def tenant_from_header(request):
    return request.META.get("HTTP_X_TENANT")


def audiences_from_header(request):
    return [a for a in request.META.get("HTTP_X_AUDIENCES", "").split(",") if a]


def broken_resolver(request):
    raise RuntimeError("resolver down")


TENANCY = {"TENANT": {"RESOLVER": tenant_from_header}}
AUDIENCES = {"NOTICES": {"AUDIENCE_RESOLVER": audiences_from_header}}


class NoticeTestCase(TestCase):
    def setUp(self):
        invalidate_cache()
        self.addCleanup(invalidate_cache)
        self.factory = RequestFactory()

    def notice(self, title="Maintenance", **fields):
        return PlatformNoticeModel.objects.create(title=title, **fields)

    def request(self, user=None, tenant=None, audiences=()):
        headers = {}
        if tenant:
            headers["HTTP_X_TENANT"] = str(tenant)
        if audiences:
            headers["HTTP_X_AUDIENCES"] = ",".join(audiences)
        request = self.factory.get("/notices/", **headers)
        request.user = user
        return request

    def titles(self, request, **kwargs):
        return [notice.title for notice in live_notices(request, **kwargs)]


class WindowTests(NoticeTestCase):
    def test_only_notices_inside_their_window_are_live(self):
        now = timezone.now()
        self.notice("open")
        self.notice("started", starts_at=now - timedelta(hours=1))
        self.notice("ending", ends_at=now + timedelta(hours=1))
        self.notice("not yet", starts_at=now + timedelta(hours=1))
        self.notice("over", ends_at=now - timedelta(seconds=1))
        self.notice("off", is_active=False)
        self.notice("deleted", is_deleted=True)
        self.assertEqual(sorted(self.titles(self.request())), ["ending", "open", "started"])

    def test_a_cached_notice_starts_and_ends_on_time(self):
        now = timezone.now()
        self.notice("scheduled", starts_at=now + timedelta(hours=1), ends_at=now + timedelta(hours=2))
        self.assertEqual(self.titles(self.request()), [])  # the list is cached now
        with self.assertNumQueries(0):
            self.assertEqual(self.titles(self.request(), at=now + timedelta(minutes=90)), ["scheduled"])
            self.assertEqual(self.titles(self.request(), at=now + timedelta(hours=3)), [])

    def test_higher_priority_first(self):
        self.notice("low", priority=0)
        self.notice("high", priority=10)
        self.assertEqual(self.titles(self.request()), ["high", "low"])


class TenantTests(NoticeTestCase):
    def test_without_tenancy_only_notices_for_every_tenant_show(self):
        self.notice("everyone")
        self.notice("one tenant", tenant_id=TENANT)
        self.assertEqual(self.titles(self.request(tenant=TENANT)), ["everyone"])

    @override_settings(DJANGO_COMMON_UTILS=TENANCY)
    def test_a_tenant_sees_its_own_and_everyones(self):
        self.notice("everyone")
        self.notice("mine", tenant_id=TENANT)
        self.notice("theirs", tenant_id=OTHER_TENANT)
        self.assertEqual(sorted(self.titles(self.request(tenant=TENANT))), ["everyone", "mine"])
        self.assertEqual(self.titles(self.request()), ["everyone"])

    @override_settings(DJANGO_COMMON_UTILS=TENANCY)
    def test_a_notice_is_never_scoped_to_the_tenant_of_whoever_posted_it(self):
        set_current_request(self.request(tenant=TENANT))
        self.addCleanup(set_current_request, None)
        self.assertIsNone(self.notice().tenant_id)


class AudienceTests(NoticeTestCase):
    def test_without_a_resolver_only_notices_for_everyone_show(self):
        self.notice("everyone")
        self.notice("admins only", audience="admins")
        self.assertEqual(self.titles(self.request(audiences=["admins"])), ["everyone"])

    @override_settings(DJANGO_COMMON_UTILS=AUDIENCES)
    def test_the_resolver_decides_the_viewers_audiences(self):
        self.notice("everyone")
        self.notice("admins only", audience="admins")
        self.notice("customers only", audience="customers")
        self.assertEqual(
            sorted(self.titles(self.request(audiences=["admins"]))), ["admins only", "everyone"],
        )

    @override_settings(DJANGO_COMMON_UTILS={"NOTICES": {"AUDIENCE_RESOLVER": broken_resolver}})
    def test_a_failing_resolver_falls_back_to_notices_for_everyone(self):
        self.notice("everyone")
        self.notice("admins only", audience="admins")
        with self.assertLogs("django_common_utils.notices.service", "WARNING"):
            self.assertEqual(self.titles(self.request()), ["everyone"])

    def test_a_blank_audience_or_surface_means_every(self):
        notice = self.notice(audience="", surface="")
        self.assertIsNone(notice.audience)
        self.assertIsNone(notice.surface)
        self.assertEqual(self.titles(self.request()), ["Maintenance"])


class SurfaceTests(NoticeTestCase):
    def test_a_surface_sees_its_own_and_every_surfaces(self):
        self.notice("everywhere")
        self.notice("web", surface="web")
        self.notice("please update", surface="ios")
        self.assertEqual(sorted(self.titles(self.request(), surface="web")), ["everywhere", "web"])

    def test_a_client_that_names_no_surface_sees_no_other_surfaces_notice(self):
        self.notice("everywhere")
        self.notice("please update", surface="ios")
        self.assertEqual(self.titles(self.request()), ["everywhere"])


class QuerySetAgreesWithTheRowTests(NoticeTestCase):
    """The service answers from cached rows through the model's methods; the
    queryset answers from the table. They must never disagree."""

    def test_same_answer_for_every_combination(self):
        now = timezone.now()
        windows = [
            {}, {"starts_at": now + timedelta(hours=1)}, {"ends_at": now - timedelta(hours=1)},
            {"starts_at": now - timedelta(hours=1), "ends_at": now + timedelta(hours=1)},
        ]
        combinations = itertools.product(
            windows, [None, TENANT, OTHER_TENANT], [None, "admins"], [None, "web", "ios"], [True, False],
        )
        for window, tenant, audience, surface, active in combinations:
            self.notice(tenant_id=tenant, audience=audience, surface=surface, is_active=active, **window)

        rows = list(PlatformNoticeModel.objects.all())
        viewers = itertools.product([None, TENANT], [(), ("admins",)], [None, "web"])
        for tenant, audiences, surface in viewers:
            viewer = {"tenant_id": tenant, "audiences": audiences, "surface": surface}
            with self.subTest(**viewer):
                expected = {
                    row.pk for row in PlatformNoticeModel.objects.live(at=now).visible_to(**viewer)
                }
                self.assertEqual(
                    {row.pk for row in rows if row.is_live(now) and row.is_visible_to(**viewer)},
                    expected,
                )


class CacheTests(NoticeTestCase):
    def test_a_second_read_does_not_touch_the_database(self):
        self.notice()
        self.titles(self.request())
        with self.assertNumQueries(0):
            self.assertEqual(self.titles(self.request()), ["Maintenance"])

    def test_a_save_or_delete_drops_the_cache(self):
        notice = self.notice()
        self.titles(self.request())
        notice.title = "Maintenance moved"
        notice.save()
        self.assertEqual(self.titles(self.request()), ["Maintenance moved"])
        notice.delete()
        self.assertEqual(self.titles(self.request()), [])

    @override_settings(DJANGO_COMMON_UTILS={"NOTICES": {"CACHE_TTL_SECONDS": 0}})
    def test_ttl_zero_reads_the_database_every_time(self):
        self.notice()
        self.titles(self.request())
        with self.assertNumQueries(1):
            self.titles(self.request())


class DismissalTests(NoticeTestCase):
    def setUp(self):
        super().setUp()
        self.ana = User.objects.create_user(username="ana", password="x")
        self.ben = User.objects.create_user(username="ben", password="x")

    def test_a_dismissed_notice_is_hidden_from_that_user_only(self):
        notice = self.notice()
        dismiss(notice, self.ana)
        self.assertEqual(self.titles(self.request(user=self.ana)), [])
        self.assertEqual(self.titles(self.request(user=self.ben)), ["Maintenance"])

    def test_dismissing_twice_is_one_row(self):
        notice = self.notice()
        dismiss(notice, self.ana)
        dismiss(notice, self.ana)
        self.assertEqual(PlatformNoticeDismissalModel.objects.count(), 1)

    def test_a_notice_that_is_not_dismissible_cannot_be_dismissed(self):
        with self.assertRaises(ValueError):
            dismiss(self.notice(is_dismissible=False), self.ana)

    @override_settings(DJANGO_COMMON_UTILS=TENANCY)
    def test_a_dismissal_belongs_to_its_notices_tenant(self):
        set_current_request(self.request(tenant=OTHER_TENANT))
        self.addCleanup(set_current_request, None)
        self.assertEqual(dismiss(self.notice(tenant_id=TENANT), self.ana).tenant_id, TENANT)
        self.assertIsNone(dismiss(self.notice("everyone"), self.ana).tenant_id)

    def test_notices_keep_history_and_dismissals_do_not(self):
        notice = self.notice()
        dismiss(notice, self.ana)
        recorded = set(ModelHistory.objects.values_list("content_type", flat=True))
        self.assertIn(ContentType.objects.get_for_model(PlatformNoticeModel).pk, recorded)
        self.assertNotIn(ContentType.objects.get_for_model(PlatformNoticeDismissalModel).pk, recorded)


@override_settings(ROOT_URLCONF="tests.urls")
class EndpointTests(NoticeTestCase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(username="ana", password="x")
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_the_list_answers_in_the_envelope(self):
        notice = self.notice(severity="warning", surface="web", audience=None, action_url="https://status.example")
        body = self.client.get("/notices/", {"surface": "web"}).json()
        self.assertEqual(body["status"], "success")
        [row] = body["data"]
        self.assertEqual(row["id"], str(notice.pk))
        self.assertEqual(row["severity"], "warning")
        self.assertEqual(row["action_url"], "https://status.example")
        self.assertNotIn("audience", row)
        self.assertNotIn("tenant_id", row)

    def test_an_empty_list_is_still_data(self):
        self.assertEqual(self.client.get("/notices/").json()["data"], [])

    def test_dismiss(self):
        notice = self.notice(surface="ios")
        response = self.client.post(f"/notices/{notice.pk}/dismiss/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get("/notices/", {"surface": "ios"}).json()["data"], [])

    def test_dismissing_a_notice_that_cannot_be_dismissed_is_a_400(self):
        notice = self.notice(is_dismissible=False)
        self.assertEqual(self.client.post(f"/notices/{notice.pk}/dismiss/").status_code, 400)

    def test_a_notice_the_viewer_cannot_see_is_a_404(self):
        hidden = self.notice(audience="admins")
        self.assertEqual(self.client.post(f"/notices/{hidden.pk}/dismiss/").status_code, 404)
        self.assertEqual(self.client.post(f"/notices/{uuid.uuid4()}/dismiss/").status_code, 404)
        self.assertFalse(PlatformNoticeDismissalModel.objects.exists())

    def test_anonymous_is_refused(self):
        notice = self.notice()
        anonymous = APIClient()
        self.assertIn(anonymous.get("/notices/").status_code, (401, 403))
        self.assertIn(anonymous.post(f"/notices/{notice.pk}/dismiss/").status_code, (401, 403))
