"""Service for review routing rules management."""
from __future__ import annotations

from typing import Any

from django.db.models import QuerySet

from apps.extraction_core.models import ReviewRoutingRule
from apps.extraction_core.services.extraction_audit import ExtractionAuditService


CONDITION_TYPES = [
    ("low_confidence", "Low Confidence"),
    ("tax_issues", "Tax Issues"),
    ("vendor_mismatch", "Vendor Mismatch"),
    ("schema_missing", "Schema Missing"),
    ("jurisdiction_mismatch", "Jurisdiction Mismatch"),
    ("duplicate_suspicion", "Duplicate Suspicion"),
    ("unsupported_document_type", "Unsupported Document Type"),
]

TARGET_QUEUES = [
    ("EXCEPTION_OPS", "Exception Ops"),
    ("TAX_REVIEW", "Tax Review"),
    ("MASTER_DATA_REVIEW", "Vendor Ops / Master Data"),
    ("AP_REVIEW", "AP Review"),
    ("COMPLIANCE", "Compliance Review"),
]


class ReviewRoutingRulesService:
    """Stateless service for review routing rule management."""

    @classmethod
    def list_rules(cls, filters: dict | None = None) -> QuerySet:
        qs = ReviewRoutingRule.objects.all()
        if not filters:
            return qs
        if filters.get("condition_type"):
            qs = qs.filter(condition_type__iexact=filters["condition_type"])
        if filters.get("target_queue"):
            qs = qs.filter(target_queue__iexact=filters["target_queue"])
        if filters.get("is_active") is not None:
            qs = qs.filter(is_active=filters["is_active"])
        if filters.get("search"):
            qs = qs.filter(name__icontains=filters["search"])
        return qs

    @classmethod
    def get_rule(cls, pk: int) -> ReviewRoutingRule | None:
        return ReviewRoutingRule.objects.filter(pk=pk).first()

    @classmethod
    def create_rule(cls, data: dict, user) -> ReviewRoutingRule:
        rule = ReviewRoutingRule(
            name=data["name"],
            rule_code=data["rule_code"],
            condition_type=data["condition_type"],
            condition_config_json=data.get("condition_config_json", {}),
            target_queue=data["target_queue"],
            priority=data.get("priority", 100),
            description=data.get("description", ""),
            is_active=data.get("is_active", True),
            created_by=user,
            updated_by=user,
        )
        rule.save()
        ExtractionAuditService.log_settings_updated(
            entity_type="ReviewRoutingRule",
            entity_id=rule.pk,
            before={},
            after={"name": rule.name, "condition_type": rule.condition_type, "target_queue": rule.target_queue},
            user=user,
        )
        return rule

    @classmethod
    def update_rule(cls, pk: int, data: dict, user) -> ReviewRoutingRule | None:
        rule = cls.get_rule(pk)
        if not rule:
            return None
        before = {"name": rule.name, "condition_type": rule.condition_type, "target_queue": rule.target_queue, "priority": str(rule.priority), "is_active": str(rule.is_active)}
        editable = ["name", "condition_type", "condition_config_json", "target_queue", "priority", "description", "is_active"]
        for field in editable:
            if field in data:
                setattr(rule, field, data[field])
        rule.updated_by = user
        rule.save()
        after = {"name": rule.name, "condition_type": rule.condition_type, "target_queue": rule.target_queue, "priority": str(rule.priority), "is_active": str(rule.is_active)}
        ExtractionAuditService.log_settings_updated(
            entity_type="ReviewRoutingRule",
            entity_id=rule.pk,
            before=before,
            after=after,
            user=user,
        )
        return rule

    @classmethod
    def activate_rule(cls, pk: int, user) -> ReviewRoutingRule | None:
        rule = cls.get_rule(pk)
        if not rule:
            return None
        rule.is_active = True
        rule.updated_by = user
        rule.save()
        return rule

    @classmethod
    def deactivate_rule(cls, pk: int, user) -> ReviewRoutingRule | None:
        rule = cls.get_rule(pk)
        if not rule:
            return None
        rule.is_active = False
        rule.updated_by = user
        rule.save()
        return rule

    @classmethod
    def get_human_readable_explanation(cls, rule: ReviewRoutingRule) -> str:
        """Generate human-readable explanation of the rule."""
        condition_labels = dict(CONDITION_TYPES)
        queue_labels = dict(TARGET_QUEUES)
        condition = condition_labels.get(rule.condition_type, rule.condition_type)
        queue = queue_labels.get(rule.target_queue, rule.target_queue)
        config = rule.condition_config_json or {}
        threshold = config.get("threshold", "")
        explanation = f"When '{condition}' is detected"
        if threshold:
            explanation += f" (threshold: {threshold})"
        explanation += f", route to '{queue}' queue."
        if rule.priority:
            explanation += f" Priority: {rule.priority}."
        return explanation


class ReviewRoutingPreviewService:
    """Preview routing outcomes for sample inputs."""

    @classmethod
    def preview_route(cls, confidence: float, issues: list[str] | None = None) -> list[dict]:
        """Given sample confidence and issue types, return which rules would fire."""
        rules = ReviewRoutingRule.objects.filter(is_active=True).order_by("priority")
        results = []
        issues = issues or []
        for rule in rules:
            would_fire = False
            reason = ""
            config = rule.condition_config_json or {}
            threshold = config.get("threshold", 0.65)

            if rule.condition_type == "low_confidence" and confidence < float(threshold):
                would_fire = True
                reason = f"Confidence {confidence:.2f} < threshold {threshold}"
            elif rule.condition_type in issues:
                would_fire = True
                reason = f"Issue type '{rule.condition_type}' present"

            if would_fire:
                results.append({
                    "rule": rule,
                    "target_queue": rule.target_queue,
                    "reason": reason,
                    "priority": rule.priority,
                })
        return results
