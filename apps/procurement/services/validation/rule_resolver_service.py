"""ValidationRuleResolverService — resolve applicable rule sets and rules."""
from __future__ import annotations

import logging
from typing import List

from django.db.models import QuerySet

from apps.procurement.models import ProcurementRequest, ValidationRule, ValidationRuleSet

logger = logging.getLogger(__name__)


class ValidationRuleResolverService:
    """Resolve applicable ValidationRuleSets and individual rules for a request."""

    @staticmethod
    def resolve_rule_sets(
        *,
        domain_code: str = "",
        schema_code: str = "",
        validation_type: str = "",
    ) -> QuerySet[ValidationRuleSet]:
        """Return active rule sets matching domain/schema/type, ordered by priority.

        Resolution order (most specific first):
        1. Exact domain + schema match
        2. Domain-only match (schema blank)
        3. Generic rules (domain and schema both blank)
        """
        qs = ValidationRuleSet.objects.filter(is_active=True)

        if validation_type:
            qs = qs.filter(validation_type=validation_type)

        # Build domain filter: match exact domain OR generic (blank)
        from django.db.models import Q

        domain_q = Q(domain_code="")
        if domain_code:
            domain_q |= Q(domain_code=domain_code)

        schema_q = Q(schema_code="")
        if schema_code:
            schema_q |= Q(schema_code=schema_code)

        qs = qs.filter(domain_q & schema_q)
        return qs.order_by("priority", "rule_set_code")

    @staticmethod
    def resolve_rules(
        *,
        domain_code: str = "",
        schema_code: str = "",
        validation_type: str = "",
    ) -> List[ValidationRule]:
        """Return all active rules from applicable rule sets."""
        rule_sets = ValidationRuleResolverService.resolve_rule_sets(
            domain_code=domain_code,
            schema_code=schema_code,
            validation_type=validation_type,
        )
        return list(
            ValidationRule.objects.filter(
                rule_set__in=rule_sets,
                is_active=True,
            )
            .select_related("rule_set")
            .order_by("rule_set__priority", "display_order")
        )

    @staticmethod
    def resolve_rules_for_request(
        request: ProcurementRequest,
        validation_type: str = "",
    ) -> List[ValidationRule]:
        """Convenience: resolve rules using request's domain/schema."""
        return ValidationRuleResolverService.resolve_rules(
            domain_code=request.domain_code,
            schema_code=request.schema_code,
            validation_type=validation_type,
        )
