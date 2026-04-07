"""RoomWise Pre-Procurement Recommender Service.

Core logic for the recommendation engine:
- Parses room requirements
- Searches for matching products
- Retrieves vendor pricing
- Scores and ranks options
- Generates natural-language explanations
"""
import logging
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime

from django.db.models import QuerySet, Q

from apps.core.decorators import observed_service
from apps.procurement.models import (
    Product,
    VendorProduct,
    RecommendationLog,
    Room,
)

logger = logging.getLogger(__name__)


class RecommendationScoringEngine:
    """Encapsulates composite scoring logic for vendor-product pairs."""

    # Default weights for general usage
    DEFAULT_WEIGHTS = {
        "price": 0.20,
        "performance": 0.25,
        "delivery": 0.20,
        "vendor": 0.20,
        "fit": 0.15,
    }

    # Usage-type-specific weights
    USAGE_WEIGHTS = {
        "DATA_CENTER": {
            "price": 0.15,
            "performance": 0.35,
            "delivery": 0.15,
            "vendor": 0.25,
            "fit": 0.10,
        },
        "OFFICE": {
            "price": 0.30,
            "performance": 0.20,
            "delivery": 0.15,
            "vendor": 0.20,
            "fit": 0.15,
        },
        "WAREHOUSE": {
            "price": 0.25,
            "performance": 0.15,
            "delivery": 0.30,
            "vendor": 0.15,
            "fit": 0.15,
        },
        "RETAIL": {
            "price": 0.25,
            "performance": 0.20,
            "delivery": 0.20,
            "vendor": 0.20,
            "fit": 0.15,
        },
    }

    def __init__(self):
        self.all_prices = []
        self.all_lead_times = []

    def score_vendor_product(
        self,
        vendor_product: VendorProduct,
        room_attrs: Dict[str, Any],
        price_range: Tuple[Decimal, Decimal],
        lead_time_range: Tuple[int, int],
        weights: Optional[Dict[str, float]] = None,
    ) -> Dict[str, Any]:
        """
        Calculate composite score (0-100) for a vendor-product pair.

        Args:
            vendor_product: The VendorProduct to score
            room_attrs: Room attributes (cooling_load_kw, noise_limit_db, usage_type, etc.)
            price_range: (min_price, max_price) tuple for normalization
            lead_time_range: (min_days, max_days) tuple for normalization
            weights: Custom weight dict (defaults to usage type specific)

        Returns:
            Dict with score breakdown and metadata
        """
        if weights is None:
            usage_type = room_attrs.get("usage_type", "OFFICE")
            weights = self.USAGE_WEIGHTS.get(usage_type, self.DEFAULT_WEIGHTS)

        product = vendor_product.product
        vendor = vendor_product.vendor

        # --- Calculate component scores ---

        # Price score: lower is better, normalized to [0, 100]
        min_p, max_p = price_range
        if max_p > min_p:
            price_score = 100 - (
                (float(vendor_product.unit_price) - float(min_p))
                / (float(max_p) - float(min_p))
                * 100
            )
        else:
            price_score = 100.0
        price_score = max(0, min(100, price_score))

        # Performance score: capacity match * efficiency ratio
        design_load = float(room_attrs.get("design_cooling_load_kw", 20))
        capacity = float(product.capacity_kw)

        # Ideal capacity ratio is close to 1.0 (don't penalize reasonable oversizing)
        capacity_ratio = min(capacity / design_load, 1.3) if design_load > 0 else 1.0
        if capacity_ratio < 0.8:  # Undersized
            capacity_ratio = 0.5
        else:
            capacity_ratio = min(capacity_ratio, 1.0)  # Penalize oversizing

        # Efficiency ratio (normalize COP to typical 3.5)
        cop = float(product.cop_rating) if product.cop_rating else 3.5
        efficiency_ratio = min(cop / 3.5, 1.0)

        perf_score = capacity_ratio * efficiency_ratio * 100
        perf_score = max(0, min(100, perf_score))

        # Delivery score: shorter is better, but give grace period
        min_days, max_days = lead_time_range
        days_range = max_days - min_days if max_days > min_days else 1
        delivery_score = 100 - (
            (vendor_product.lead_time_days - min_days) / days_range * 100
        )
        delivery_score = max(0, min(100, delivery_score))

        # Vendor reliability score: on-time % * reliability rating
        on_time_pct = float(vendor.on_time_delivery_pct) / 100.0 if vendor.on_time_delivery_pct else 0.9
        reliability = float(vendor.reliability_score) / 5.0 if vendor.reliability_score else 0.8
        vendor_score = on_time_pct * reliability * 100

        # Fit score: usage type match + preference match
        fit_score = 0.0
        usage_type = room_attrs.get("usage_type")
        if usage_type in product.approved_use_cases:
            fit_score = 100.0

        # Bonus for preferred system type
        preferred_types = room_attrs.get("preferred_system_types", [])
        if product.system_type in preferred_types:
            fit_score = min(fit_score + 10, 100)

        # Bonus for noise match
        noise_limit = room_attrs.get("noise_limit_db")
        if noise_limit and product.sound_level_db_full_load <= noise_limit:
            fit_score = min(fit_score + 5, 100)

        # --- Composite score ---
        composite_score = (
            price_score * weights["price"]
            + perf_score * weights["performance"]
            + delivery_score * weights["delivery"]
            + vendor_score * weights["vendor"]
            + fit_score * weights["fit"]
        )

        # --- Risk tags ---
        risk_tags = []
        if vendor_product.lead_time_days > 21:
            risk_tags.append("long_lead_time")
        if vendor.reliability_score < 3.5:
            risk_tags.append("vendor_risk")
        if product.capacity_kw > design_load * 1.3:
            risk_tags.append("oversized")
        elif product.capacity_kw < design_load * 0.8:
            risk_tags.append("undersized")
        if vendor_product.stock_available is None or vendor_product.stock_available == 0:
            risk_tags.append("on_order")

        return {
            "price_score": price_score,
            "performance_score": perf_score,
            "delivery_score": delivery_score,
            "vendor_score": vendor_score,
            "fit_score": fit_score,
            "composite_score": composite_score,
            "risk_tags": risk_tags,
        }

    def generate_reason(
        self,
        vendor_product: VendorProduct,
        rank: int,
        composite_score: float,
        room_attrs: Dict[str, Any],
        scores: Dict[str, float],
    ) -> str:
        """Generate natural-language explanation for the recommendation."""
        product = vendor_product.product
        vendor = vendor_product.vendor
        design_load = float(room_attrs.get("design_cooling_load_kw", 20))

        if rank == 1 and composite_score > 85:
            reason = f"Best overall value. {product.manufacturer} {product.product_name} ({product.capacity_kw}kW) "
            reason += f"from {vendor.vendor_name}, ₹{vendor_product.unit_price:,.0f}, "
            reason += f"{vendor_product.lead_time_days}-day lead time, {product.sound_level_db_full_load}dB noise. "
            if vendor.reliability_score >= 4.5:
                reason += f"Strong vendor track record ({vendor.on_time_delivery_pct:.0f}% on-time). "
            reason += "Recommended."
        elif rank <= 3 and composite_score > 75:
            reason = f"Solid alternative. {product.manufacturer} {product.product_name} ({product.capacity_kw}kW) "
            reason += f"from {vendor.vendor_name}, ₹{vendor_product.unit_price:,.0f}. "
            if scores["price_score"] > 85:
                reason += "Budget-friendly option. "
            if scores["delivery_score"] > 85:
                reason += "Fast delivery. "
            if scores["fit_score"] > 80:
                reason += "Excellent fit for your requirements. "
        else:
            reason = f"Additional option. {product.manufacturer} {product.product_name} ({product.capacity_kw}kW). "

        # Add capacity note
        if product.capacity_kw > design_load * 1.2:
            reason += f"Capacity exceeds requirement by {((product.capacity_kw / design_load) - 1) * 100:.0f}% (room for growth). "
        elif product.capacity_kw < design_load * 0.9:
            reason += f"Capacity is {(1 - (product.capacity_kw / design_load)) * 100:.0f}% under requirement. "

        # Add lead time note
        if vendor_product.lead_time_days > 30:
            reason += f"Note: {vendor_product.lead_time_days}-day lead time may impact timeline. "

        return reason.strip()


class RoomWiseRecommenderService:
    """Main recommendation orchestrator service."""

    @observed_service
    def run_recommendation(
        self,
        room_id: Optional[str] = None,
        requirement_text: str = "",
        user_id: Optional[str] = None,
        budget_max: Optional[Decimal] = None,
        preferred_lead_time_days: Optional[int] = None,
        exclude_vendors: Optional[List[str]] = None,
        preferred_system_types: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Main entry point for recommendation generation.

        Args:
            room_id: UUID of room to recommend for
            requirement_text: Free-text requirement description
            user_id: UUID of requesting user
            budget_max: Maximum budget constraint
            preferred_lead_time_days: Target delivery timeline
            exclude_vendors: List of vendor IDs to exclude
            preferred_system_types: List of preferred system types (VRF, SPLIT_AC, etc.)

        Returns:
            Dict with recommendation_log and results
        """
        exclude_vendors = exclude_vendors or []
        preferred_system_types = preferred_system_types or []

        # Step 1: Extract/parse room attributes
        room_attrs = self._extract_room_attributes(room_id, requirement_text)

        # Step 2: Create recommendation log entry
        from django.contrib.auth.models import User

        requesting_user = None
        if user_id:
            try:
                requesting_user = User.objects.get(id=user_id)
            except User.DoesNotExist:
                pass

        rec_log = RecommendationLog.objects.create(
            room_id=room_id,
            requirement_text=requirement_text,
            recommendation_input_json=room_attrs,
            requested_by=requesting_user,
            recommendation_method="DETERMINISTIC",
        )

        # Step 3: Find matching products
        candidates = self._find_candidate_products(
            room_attrs, exclude_vendors=exclude_vendors
        )

        if not candidates:
            logger.warning(f"No matching products for recommendation {rec_log.recommendation_id}")
            rec_log.num_options_generated = 0
            rec_log.save()
            return {
                "recommendation_id": str(rec_log.recommendation_id),
                "success": False,
                "message": "No matching HVAC products found with current filters.",
                "results": [],
            }

        # Step 4: Get vendor-product offerings
        vendor_products = self._get_vendor_offerings(
            candidate_ids=[p.id for p in candidates],
            budget_max=budget_max,
        )

        if not vendor_products:
            logger.warning(f"No vendor offerings for recommendation {rec_log.recommendation_id}")
            rec_log.num_options_generated = 0
            rec_log.save()
            return {
                "recommendation_id": str(rec_log.recommendation_id),
                "success": False,
                "message": "No vendors currently offering matching products.",
                "results": [],
            }

        # Step 5: Calculate price and lead-time ranges for normalization
        prices = [float(vp.unit_price) for vp in vendor_products]
        lead_times = [vp.lead_time_days for vp in vendor_products]
        price_range = (
            Decimal(min(prices)) if prices else Decimal(0),
            Decimal(max(prices)) if prices else Decimal(100000),
        )
        lead_time_range = (min(lead_times), max(lead_times)) if lead_times else (0, 30)

        # Step 6: Score each option
        scorer = RecommendationScoringEngine()
        scored_options = []

        for vp in vendor_products:
            scores = scorer.score_vendor_product(
                vp, room_attrs, price_range, lead_time_range
            )
            reason = scorer.generate_reason(vp, 0, scores["composite_score"], room_attrs, scores)

            scored_options.append(
                {
                    "vendor_product_id": str(vp.vendor_product_id),
                    "vendor_id": str(vp.vendor_id),
                    "vendor_name": vp.vendor.vendor_name,
                    "product_id": str(vp.product_id),
                    "product_sku": vp.product.sku,
                    "product_name": vp.product.product_name,
                    "capacity_kw": float(vp.product.capacity_kw),
                    "unit_price": float(vp.unit_price),
                    "currency": vp.currency,
                    "lead_time_days": vp.lead_time_days,
                    "noise_db": vp.product.sound_level_db_full_load,
                    "composite_score": float(scores["composite_score"]),
                    "price_score": float(scores["price_score"]),
                    "performance_score": float(scores["performance_score"]),
                    "delivery_score": float(scores["delivery_score"]),
                    "vendor_score": float(scores["vendor_score"]),
                    "fit_score": float(scores["fit_score"]),
                    "reason": reason,
                    "risk_tags": scores["risk_tags"],
                }
            )

        # Step 7: Sort by score and take top 5
        sorted_options = sorted(
            scored_options, key=lambda x: x["composite_score"], reverse=True
        )[:5]

        # Add rank
        for idx, opt in enumerate(sorted_options, start=1):
            opt["rank"] = idx

        # Step 8: Update recommendation log
        rec_log.recommended_products_json = sorted_options
        rec_log.num_options_generated = len(sorted_options)
        if sorted_options:
            rec_log.top_ranked_score = Decimal(str(sorted_options[0]["composite_score"]))
            # Link to top recommendation vendor-product
            try:
                rec_log.top_ranked_vendor_product_id = sorted_options[0]["vendor_product_id"]
            except (KeyError, IndexError):
                pass
        rec_log.save()

        return {
            "recommendation_id": str(rec_log.recommendation_id),
            "success": True,
            "num_options": len(sorted_options),
            "requirement_summary": requirement_text or f"Room {room_id}",
            "results": sorted_options,
        }

    def _extract_room_attributes(
        self, room_id: Optional[str], requirement_text: str
    ) -> Dict[str, Any]:
        """
        Extract room attributes from DB (if room_id provided) or parse free-text.

        Returns:
            Dict with normalized room attributes
        """
        attrs = {
            "area_sqm": 45,
            "design_cooling_load_kw": 20,
            "design_temp_c": 24,
            "temp_tolerance_c": 1,
            "noise_limit_db": 70,
            "usage_type": "OFFICE",
            "access_constraints": "",
            "preferred_system_types": [],
        }

        if room_id:
            try:
                room = Room.objects.get(room_id=room_id)
                attrs.update(
                    {
                        "area_sqm": float(room.area_sqm),
                        "design_cooling_load_kw": float(room.design_cooling_load_kw),
                        "design_temp_c": float(room.design_temp_c),
                        "temp_tolerance_c": float(room.temp_tolerance_c),
                        "noise_limit_db": room.noise_limit_db or 70,
                        "usage_type": room.usage_type,
                        "access_constraints": room.access_constraints,
                    }
                )
            except Room.DoesNotExist:
                logger.warning(f"Room {room_id} not found, using defaults")

        # Parse free-text requirements (simple regex matching)
        if requirement_text:
            import re

            # Area: "45 m²" or "45 sq"
            area_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:m²|m2|sq)", requirement_text, re.IGNORECASE)
            if area_match:
                attrs["area_sqm"] = float(area_match.group(1))

            # Cooling load: "20 kW" or "20kW"
            load_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:kW|kw|TR)", requirement_text, re.IGNORECASE)
            if load_match:
                value = float(load_match.group(1))
                # If TR (tons of refrigeration), convert to kW (1 TR ≈ 3.5 kW)
                if "TR" in requirement_text.upper():
                    value *= 3.5
                attrs["design_cooling_load_kw"] = value

            # Target temp: "24±1°C" or "24 +- 1C"
            temp_match = re.search(r"(\d+)\s*[±+-]+\s*(\d+)\s*°?C", requirement_text, re.IGNORECASE)
            if temp_match:
                attrs["design_temp_c"] = float(temp_match.group(1))
                attrs["temp_tolerance_c"] = float(temp_match.group(2))

            # Noise limit: "<70 dB" or "70dB"
            noise_match = re.search(r"[<≤]?\s*(\d+)\s*(?:dB|db)", requirement_text, re.IGNORECASE)
            if noise_match:
                attrs["noise_limit_db"] = int(noise_match.group(1))

            # Usage type keywords
            if any(kw in requirement_text.lower() for kw in ["data", "center", "server", "rack"]):
                attrs["usage_type"] = "DATA_CENTER"
            elif any(kw in requirement_text.lower() for kw in ["office", "floor"]):
                attrs["usage_type"] = "OFFICE"
            elif any(kw in requirement_text.lower() for kw in ["warehouse", "store"]):
                attrs["usage_type"] = "WAREHOUSE"

            # Preferred system types
            if "VRF" in requirement_text.upper():
                attrs["preferred_system_types"].append("VRF")
            if "SPLIT" in requirement_text.upper():
                attrs["preferred_system_types"].append("SPLIT_AC")
            if "FCU" in requirement_text.upper():
                attrs["preferred_system_types"].append("FCU")
            if "CHILLER" in requirement_text.upper():
                attrs["preferred_system_types"].append("CHILLER")

        return attrs

    def _find_candidate_products(
        self, room_attrs: Dict[str, Any], exclude_vendors: Optional[List[str]] = None
    ) -> QuerySet:
        """
        Find products matching room requirements.

        Filters by:
        - Capacity within 20-30% tolerance
        - Noise <= limit
        - Approved for usage type
        """
        exclude_vendors = exclude_vendors or []
        design_load = room_attrs.get("design_cooling_load_kw", 20)
        noise_limit = room_attrs.get("noise_limit_db", 70)
        usage_type = room_attrs.get("usage_type", "OFFICE")

        # Capacity tolerance: ±30% (0.7x to 1.3x)
        capacity_min = Decimal(str(design_load * 0.7))
        capacity_max = Decimal(str(design_load * 1.3))

        query = Product.objects.filter(
            is_active=True,
            capacity_kw__gte=capacity_min,
            capacity_kw__lte=capacity_max,
            sound_level_db_full_load__lte=noise_limit,
        )

        # Filter by approved use cases
        query = query.filter(approved_use_cases__contains=[usage_type])

        return query

    def _get_vendor_offerings(
        self,
        candidate_ids: List[int],
        budget_max: Optional[Decimal] = None,
        exclude_vendors: Optional[List[str]] = None,
    ) -> QuerySet:
        """Get vendor offerings for candidate products."""
        exclude_vendors = exclude_vendors or []

        query = VendorProduct.objects.filter(
            is_active=True,
            product_id__in=candidate_ids,
            vendor__is_active=True,
        ).select_related("vendor", "product")

        if budget_max:
            query = query.filter(unit_price__lte=budget_max)

        if exclude_vendors:
            query = query.exclude(vendor_id__in=exclude_vendors)

        return query
