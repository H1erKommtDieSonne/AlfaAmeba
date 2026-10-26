import uuid

from django.urls import reverse
from django.utils import timezone

from core.testing import CreditTestCase, make_user
from node_integration.models import NodeFeedback, NodeJob
from organizations.models import Organization, OrganizationMembership

from .models import Application, ApplicationVersion


class DocumentFeedbackContextTests(CreditTestCase):
    def setUp(self):
        self.application = self._submit_application()
        self.version1 = self.application.versions.get(version_number=1)
        self.feedback_v1 = self._feedback(self.version1, [{"code": "old", "message": "v1"}])
        self.version2 = self._version(self.application, 2)
        self.feedback_v2 = self._feedback(self.version2, [{"code": "newer", "message": "v2"}])
        self.version3 = self._version(self.application, 3)
        self.feedback_v3 = self._feedback(self.version3, [{"code": "current", "message": "v3"}])
        Application.objects.filter(pk=self.application.pk).update(current_version=3)
        self.application.refresh_from_db()

    def _submit_application(self):
        self.client.force_login(self.business_user)
        response = self.client.post(reverse("business-cabinet"), self.valid_payload())
        self.assertEqual(response.status_code, 302, response.content.decode())
        return Application.objects.get()

    def _version(self, application, number):
        version = ApplicationVersion.objects.create(
            application=application,
            version_number=number,
            product=self.product,
            amount="1500000.00",
            term_months=12,
            purpose="Закупка сезонной партии товара на три месяца",
            submitted_at=timezone.now(),
            snapshot={},
        )
        NodeJob.objects.create(
            version=version,
            external_job_id=uuid.uuid4().hex,
            status=NodeJob.Status.COMPLETED,
            result={"marker": f"raw-{number}"},
        )
        return version

    def _feedback(self, version, issues):
        feedback = NodeFeedback.objects.create(
            feedback_id=uuid.uuid4(),
            job=NodeJob.objects.get(version=version),
            issues=issues,
            payload_sha256="a" * 64,
        )
        return feedback

    def _business_detail(self, user):
        self.client.force_login(user)
        return self.client.get(
            reverse("business-application-detail", args=[self.application.pk])
        )

    def _bank_detail(self, user):
        self.client.force_login(user)
        return self.client.get(
            reverse("bank-application-detail", args=[self.application.pk])
        )

    def test_current_version_and_history_are_separated(self):
        response = self._business_detail(self.business_user)
        self.assertEqual(response.status_code, 200)
        current = response.context["document_feedback_current"]
        history = response.context["document_feedback_history"]

        self.assertEqual([group["feedback_id"] for group in current], [str(self.feedback_v3.pk)])
        self.assertEqual(
            [group["feedback_id"] for group in history],
            [str(self.feedback_v2.pk), str(self.feedback_v1.pk)],
        )
        self.assertEqual(current[0]["version_number"], 3)
        self.assertEqual(history[0]["version_number"], 2)
        self.assertEqual(history[1]["version_number"], 1)

    def test_context_issues_expose_only_safe_display_fields(self):
        self.feedback_v3.issues = [
            {
                "code": "missing_field",
                "message": "m",
                "field": "f",
                "document_id": str(uuid.uuid4()),
                "filename": "statement.pdf",
                "suggestion": "s",
            }
        ]
        self.feedback_v3.save(update_fields=("issues",))

        response = self._bank_detail(self.bank_user)
        current = response.context["document_feedback_current"]

        self.assertEqual(
            set(current[0]["issues"][0]),
            {"code", "message", "field", "document_id", "filename", "suggestion"},
        )
        self.assertNotIn("raw-3", str(response.context["document_feedback_current"]))

    def test_bank_sees_same_context_contract(self):
        response = self._bank_detail(self.bank_user)
        self.assertEqual(response.status_code, 200)
        self.assertIn("document_feedback_current", response.context)
        self.assertIn("document_feedback_history", response.context)
        self.assertEqual(
            [group["feedback_id"] for group in response.context["document_feedback_current"]],
            [str(self.feedback_v3.pk)],
        )

    def test_feedback_is_scoped_to_its_application(self):
        other = Application.objects.create(
            organization=self.organization,
            organization_name=self.organization.name,
            inn=self.organization.inn,
            created_by=self.business_user,
            status=Application.Status.SUBMITTED,
            submitted_at=timezone.now(),
        )
        other_version = self._version(other, 1)
        other_feedback = self._feedback(
            other_version, [{"code": "other", "message": "не видно"}]
        )

        response = self._business_detail(self.business_user)
        groups = (
            response.context["document_feedback_current"]
            + response.context["document_feedback_history"]
        )
        ids = {group["feedback_id"] for group in groups}
        self.assertNotIn(str(other_feedback.pk), ids)

    def test_access_is_limited_by_role_and_organization(self):
        unrelated = make_user("unrelated@example.test")
        self.assertEqual(self._business_detail(unrelated).status_code, 403)
        self.assertEqual(self._bank_detail(self.bank_admin).status_code, 403)

        self.client.logout()
        anonymous = self.client.get(
            reverse("business-application-detail", args=[self.application.pk])
        )
        self.assertEqual(anonymous.status_code, 302)
        self.assertIn(reverse("login"), anonymous.url)


class DocumentFeedbackRenderingTests(CreditTestCase):
    def setUp(self):
        self.client.force_login(self.business_user)
        response = self.client.post(reverse("business-cabinet"), self.valid_payload())
        self.assertEqual(response.status_code, 302, response.content.decode())
        self.application = Application.objects.get()
        self.version = self.application.versions.get(version_number=1)
        self.job = NodeJob.objects.get(version=self.version)

    def _feedback(self, version, issues):
        return NodeFeedback.objects.create(
            feedback_id=uuid.uuid4(),
            job=NodeJob.objects.get(version=version),
            issues=issues,
            payload_sha256="a" * 64,
        )

    def _extra_version(self, number):
        version = ApplicationVersion.objects.create(
            application=self.application,
            version_number=number,
            product=self.product,
            amount="1500000.00",
            term_months=12,
            purpose="Закупка сезонной партии товара на три месяца",
            submitted_at=timezone.now(),
            snapshot={},
        )
        NodeJob.objects.create(
            version=version,
            external_job_id=uuid.uuid4().hex,
            status=NodeJob.Status.COMPLETED,
        )
        return version

    def _business_detail(self, user):
        self.client.force_login(user)
        return self.client.get(
            reverse("business-application-detail", args=[self.application.pk])
        )

    def _bank_detail(self, user):
        self.client.force_login(user)
        return self.client.get(
            reverse("bank-application-detail", args=[self.application.pk])
        )

    def test_client_and_bank_render_issue_and_suggestion(self):
        self._feedback(
            self.version,
            [
                {
                    "code": "missing_field",
                    "message": "Не найден номер счёта",
                    "suggestion": "Загрузите полную выписку",
                }
            ],
        )
        for get_detail, user in (
            (self._business_detail, self.business_user),
            (self._bank_detail, self.bank_user),
        ):
            with self.subTest(user=user.email):
                response = get_detail(user)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "Замечания к документам")
                self.assertContains(response, "Не найден номер счёта")
                self.assertContains(response, "Загрузите полную выписку")
                self.assertContains(response, "не решение банка")

    def test_malicious_message_is_escaped(self):
        self._feedback(
            self.version,
            [
                {
                    "code": "missing_field",
                    "message": "<script>alert(1)</script>",
                    "suggestion": "<img src=x onerror=alert(2)>",
                }
            ],
        )
        response = self._business_detail(self.business_user)
        self.assertContains(response, "&lt;script&gt;alert(1)&lt;/script&gt;")
        self.assertNotContains(response, "<script>alert(1)</script>")
        self.assertContains(response, "&lt;img src=x onerror=alert(2)&gt;")
        self.assertNotContains(response, "<img src=x onerror=alert(2)>")

    def test_current_and_history_are_rendered_separately(self):
        self._feedback(self.version, [{"code": "current", "message": "current-marker"}])
        version2 = self._extra_version(2)
        self._feedback(version2, [{"code": "history", "message": "history-marker"}])

        response = self._bank_detail(self.bank_user)
        self.assertContains(response, "current-marker")
        self.assertContains(response, "Замечания к предыдущим версиям")
        self.assertContains(response, "history-marker")
        current = response.context["document_feedback_current"]
        history = response.context["document_feedback_history"]
        self.assertEqual([group["version_number"] for group in current], [1])
        self.assertEqual([group["version_number"] for group in history], [2])

    def test_foreign_org_member_cannot_render_feedback(self):
        self._feedback(self.version, [{"code": "c", "message": "secret-marker"}])
        other_org = Organization.objects.create(
            name="ООО Юг",
            inn="7707083894",
            verification_status=Organization.VerificationStatus.VERIFIED,
        )
        foreign = make_user("foreign@example.test")
        OrganizationMembership.objects.create(
            organization=other_org,
            user=foreign,
            role=OrganizationMembership.Role.OWNER,
        )
        response = self._business_detail(foreign)
        self.assertEqual(response.status_code, 403)
        self.assertNotIn("document_feedback_current", response.context or {})

    def test_cta_visible_only_when_client_can_edit(self):
        self._feedback(
            self.version,
            [{"code": "missing_field", "message": "m", "suggestion": "s"}],
        )
        response = self._business_detail(self.business_user)
        self.assertNotContains(response, "Исправить документы")

        Application.objects.filter(pk=self.application.pk).update(
            status=Application.Status.NEEDS_REVISION
        )
        response = self._business_detail(self.business_user)
        self.assertContains(response, "Исправить документы")
        self.assertContains(response, '#revision-form')

    def test_no_feedback_hides_panel(self):
        response = self._business_detail(self.business_user)
        self.assertNotContains(response, "Замечания к документам")
        self.assertNotContains(response, "По текущей версии замечаний нет.")
