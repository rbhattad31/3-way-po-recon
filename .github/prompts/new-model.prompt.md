---
mode: agent
description: "Add a new Django model with migrations, admin, serializers, API, and tenant wiring"
---

# Add a New Model

## Step 0 -- Read Existing Architecture First

Before writing any code, read these files to understand the model layer:

### Documentation
- `docs/current_system_review/06_Data_Model_and_Entity_Guide.md` -- all entity relationships, BaseModel, CompanyProfile tenant FK, field conventions
- `docs/current_system_review/02_Django_App_Landscape.md` -- app boundaries and which app owns which models
- `docs/MULTI_TENANT.md` -- tenant FK patterns, scoped querysets, platform admin bypass

### Source Files
- `apps/core/models.py` -- `BaseModel`, `TimestampMixin`, `AuditMixin`, `SoftDeleteMixin` (study the inheritance chain)
- `apps/core/enums.py` -- all business enums (study naming conventions: `XxxStatus`, `XxxType`)
- `apps/documents/models.py` -- canonical example of a tenant-scoped model with raw_* / normalized fields, FK relationships
- `apps/posting_core/models.py` -- example of reference data models (ERPVendorReference etc.) with import batch tracking
- `apps/accounts/rbac_models.py` -- example of RBAC-adjacent models (Role, Permission, UserRole)

### Comprehension Check
Before proceeding, confirm you understand:
1. `BaseModel` provides `pk (BigAutoField)`, `created_at`, `updated_at`, `is_active` (soft delete)
2. `TimestampMixin` is the lightweight alternative (no `is_active`) for log/join tables
3. Every business model needs `tenant = ForeignKey("accounts.CompanyProfile", on_delete=CASCADE, null=True, blank=True, db_index=True)`
4. Enums go in `apps/core/enums.py` using `TextChoices`, never inline on model fields
5. ERP connector enums are the one exception -- they go in `apps/erp_integration/enums.py`

---

## Inputs

Provide the following when invoking this prompt:
- **App name**: which `apps/<app>/` this model belongs to
- **Model name**: PascalCase class name
- **Fields**: field names, types, relationships, constraints
- **Purpose**: one-sentence description of what this model represents
- **Is it a log/join table?** (if yes, use `TimestampMixin` instead of `BaseModel`)

---

## Steps

### 1. Define Enums (if needed)

Add any new status/type enums to `apps/core/enums.py`:
- Use `TextChoices` with UPPER_SNAKE_CASE members
- Prefix with the model domain: e.g. `InvoicePostingStatus`, `ReconciliationMode`
- Keep enum values lowercase with underscores: `"pending"`, `"in_progress"`

### 2. Define the Model

In `apps/<app>/models.py`:
- Inherit from `BaseModel` (or `TimestampMixin` for log/join tables)
- Add `tenant = ForeignKey("accounts.CompanyProfile", ...)` for business entities
- Use `related_name` on all ForeignKey/OneToOneField definitions
- Add `db_index=True` on fields used in filtering/lookup
- Add `class Meta` with `ordering`, `verbose_name`, `verbose_name_plural`
- Add `__str__` returning a meaningful representation
- Reference enums via `choices=MyEnum.choices`, `default=MyEnum.VALUE`
- For JSON fields: use `models.JSONField(default=dict, blank=True)`

### 3. Create and Run Migration

```
python manage.py makemigrations <app>
python manage.py migrate
```

Verify the migration file was generated in `apps/<app>/migrations/`.

### 4. Register Admin

In `apps/<app>/admin.py`:
- Register with `@admin.register(MyModel)`
- Set `list_display` with the most useful columns
- Set `list_filter` for tenant, status, date fields
- Set `search_fields` for text lookup fields
- Set `readonly_fields = ("created_at", "updated_at")`

### 5. Create Serializer

In `apps/<app>/serializers.py`:
- Create `MyModelSerializer` extending `serializers.ModelSerializer`
- Exclude `tenant` from writable fields (set automatically by view)
- Create separate List/Detail serializers if the model has heavy nested data
- Use `read_only_fields` for computed/system-managed fields

### 6. Create ViewSet

In `apps/<app>/views.py`:
- Create `MyModelViewSet` extending `ModelViewSet` (or `ReadOnlyModelViewSet`)
- Apply `TenantQuerysetMixin` to scope querysets to `request.tenant`
- Set `permission_classes = [IsAuthenticated, HasPermissionCode("module.action")]`
- Set `filter_backends`, `filterset_fields`, `search_fields`, `ordering_fields`
- Override `perform_create()` to set `serializer.save(tenant=request.tenant)`

### 7. Register API Routes

In `apps/<app>/api_urls.py`:
- Register with `DefaultRouter` under a URL prefix matching the model name (plural, lowercase, hyphens)
- Verify it is included in `config/urls.py` under `api/v1/<app>/`

### 8. Add Permissions

If this model introduces a new permission module:
- Add `Permission` records to `apps/accounts/management/commands/seed_rbac.py` PERMISSIONS list
- Convention: `module.view`, `module.create`, `module.edit`, `module.delete`
- Map to roles in `ROLE_MATRIX`

### 9. Write Tests

Minimum test cases:
- Model creation with valid data
- `tenant` scoping: model created under tenant A is not visible to tenant B
- Soft delete: `is_active=False` excludes from default queries (if using `BaseModel`)
- Admin registration: verify model appears in admin
- API CRUD: create, read, list (with tenant filter), update, permission denial for unauthorized role

---

## Constraints

- ASCII only in all string literals, comments, docstrings
- Never hard-delete: use `is_active=False` for business entities
- Never put business logic in models -- use service classes
- Always add `tenant` FK for business entities (skip only for global config tables)
- Run `makemigrations` and `migrate` before declaring the step complete
