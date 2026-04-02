"""Vendor API serializers."""
from rest_framework import serializers

from apps.vendors.models import Vendor


class VendorAliasSerializer(serializers.Serializer):
    """Read-only alias serializer backed by VendorAliasMapping."""
    id = serializers.IntegerField(read_only=True)
    alias_name = serializers.CharField(source="alias_text")
    normalized_alias = serializers.CharField()
    source = serializers.CharField()
    confidence = serializers.FloatField()
    created_at = serializers.DateTimeField(read_only=True)


class VendorListSerializer(serializers.ModelSerializer):
    class Meta:
        model = Vendor
        fields = [
            "id", "code", "name", "country", "currency",
            "payment_terms", "is_active", "created_at",
        ]


class VendorDetailSerializer(serializers.ModelSerializer):
    aliases = VendorAliasSerializer(source="alias_mappings", many=True, read_only=True)

    class Meta:
        model = Vendor
        fields = [
            "id", "code", "name", "normalized_name", "tax_id",
            "address", "country", "currency", "payment_terms",
            "contact_email", "is_active", "aliases", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "normalized_name", "created_at", "updated_at"]
