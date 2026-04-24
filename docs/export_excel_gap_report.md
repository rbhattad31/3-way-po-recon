# Export Excel Gap Report (Phase 2)

Source schema: imports_formats/script.sql

Input baseline: docs/export_excel_column_mapping.csv (BLANK_TEMPLATE rows only)

## Summary
- Total BLANK_TEMPLATE columns reviewed: 200\n
- FILLABLE_FROM_CURRENT_ERP_TABLES: 164\n- FILLABLE_WITH_SCHEMA_EXTENSIONS: 34\n- REQUIRES_NEW_FIELD_OR_TABLE: 2\n
## By Sheet
- Item Body: FILLABLE_FROM_CURRENT_ERP_TABLES=48, FILLABLE_WITH_SCHEMA_EXTENSIONS=10\n- Header: FILLABLE_FROM_CURRENT_ERP_TABLES=25, FILLABLE_WITH_SCHEMA_EXTENSIONS=6, REQUIRES_NEW_FIELD_OR_TABLE=1\n- Summary: FILLABLE_FROM_CURRENT_ERP_TABLES=26, FILLABLE_WITH_SCHEMA_EXTENSIONS=6\n- Advance Set Off Body: FILLABLE_FROM_CURRENT_ERP_TABLES=8, REQUIRES_NEW_FIELD_OR_TABLE=1, FILLABLE_WITH_SCHEMA_EXTENSIONS=2\n- Voucher: FILLABLE_FROM_CURRENT_ERP_TABLES=5, FILLABLE_WITH_SCHEMA_EXTENSIONS=2\n- Adjustments Body: FILLABLE_FROM_CURRENT_ERP_TABLES=33, FILLABLE_WITH_SCHEMA_EXTENSIONS=8\n- Payments Body: FILLABLE_FROM_CURRENT_ERP_TABLES=8\n- Reference: FILLABLE_FROM_CURRENT_ERP_TABLES=1\n- Other Info: FILLABLE_FROM_CURRENT_ERP_TABLES=5\n- Transaction Details: FILLABLE_FROM_CURRENT_ERP_TABLES=5\n
## Notes
- FILLABLE_FROM_CURRENT_ERP_TABLES means a likely source exists in the currently integrated connector tables.\n- FILLABLE_WITH_SCHEMA_EXTENSIONS means source exists in script.sql but not in currently integrated table/query set.\n- REQUIRES_NEW_FIELD_OR_TABLE means no close match was found in script.sql and likely needs schema/process changes.\n