"""
CaseAssignmentService — handles case assignments, routing, and escalation.
"""

import logging

from django.utils import timezone

from apps.cases.models import APCase, APCaseAssignment
from apps.core.enums import AssignmentStatus, AssignmentType, UserRole

logger = logging.getLogger(__name__)


class CaseAssignmentService:

    @staticmethod
    def assign_for_review(case: APCase, user=None, role=None, queue=None, priority_hours=48, tenant=None) -> APCaseAssignment:
        """Create a review assignment for a case."""
        assignment = APCaseAssignment.objects.create(
            case=case,
            assignment_type=AssignmentType.REVIEW,
            assigned_user=user,
            assigned_role=role or UserRole.REVIEWER,
            queue_name=queue or "default",
            due_at=timezone.now() + timezone.timedelta(hours=priority_hours),
            status=AssignmentStatus.ASSIGNED if user else AssignmentStatus.PENDING,
            tenant=tenant,
        )

        case.assigned_to = user
        case.assigned_role = role or UserRole.REVIEWER
        case.requires_human_review = True
        case.save(update_fields=["assigned_to", "assigned_role", "requires_human_review", "updated_at"])

        logger.info("Case %s assigned for review to %s", case.case_number, user or role)
        return assignment

    @staticmethod
    def escalate(case: APCase, reason: str, to_role: str = UserRole.FINANCE_MANAGER, tenant=None) -> APCaseAssignment:
        """Escalate a case to a higher role."""
        # Mark existing assignments as escalated
        case.assignments.filter(
            status__in=[AssignmentStatus.PENDING, AssignmentStatus.ASSIGNED, AssignmentStatus.IN_PROGRESS]
        ).update(status=AssignmentStatus.ESCALATED)

        assignment = APCaseAssignment.objects.create(
            case=case,
            assignment_type=AssignmentType.INVESTIGATION,
            assigned_role=to_role,
            queue_name="escalation",
            escalation_level=1,
            due_at=timezone.now() + timezone.timedelta(hours=24),
            status=AssignmentStatus.PENDING,
            tenant=tenant,
        )

        case.assigned_role = to_role
        case.save(update_fields=["assigned_role", "updated_at"])

        logger.info("Case %s escalated to %s: %s", case.case_number, to_role, reason)
        return assignment
