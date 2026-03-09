"""Vendor API serializers."""
from rest_framework import serializers

from apps.vendors.models import Vendor, VendorAlias


class VendorAliasSerializer(serializers.ModelSerializer):
    class Meta:
        model = VendorAlias
        fields = ["id", "alias_name", "normalized_alias", "source", "created_at"]
        read_only_fields = ["id", "normalized_alias", "created_at"]


class VendorListSerializer(serializers.ModelSerializer):
    class Meta:
        model = Vendor
        fields = [
            "id", "code", "name", "country", "currency",
            "payment_terms", "is_active", "created_at",
        ]


class VendorDetailSerializer(serializers.ModelSerializer):
    aliases = VendorAliasSerializer(many=True, read_only=True)

    class Meta:
        model = Vendor
        fields = [
            "id", "code", "name", "normalized_name", "tax_id",
            "address", "country", "currency", "payment_terms",
            "contact_email", "is_active", "aliases", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "normalized_name", "created_at", "updated_at"]
