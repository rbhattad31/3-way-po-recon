# Reconciliation Tests

## Running Tests

```bash
# All reconciliation tests
pytest apps/reconciliation/tests/ -v

# Single module
pytest apps/reconciliation/tests/test_tolerance_engine.py -v

# With coverage
pytest apps/reconciliation/tests/ --cov=apps/reconciliation --cov-report=html

# Fast (skip DB tests)
pytest apps/reconciliation/tests/ -v -m "not django_db"
```

## Test Files

| File | Module | Notes |
|---|---|---|
| `test_tolerance_engine.py` | `ToleranceEngine` | Pure unit, no DB |
| `test_classification_service.py` | `ClassificationService` | Pure unit, no DB |
| `test_grn_match_service.py` | `GRNMatchService` | Pure unit, no DB |
| `test_header_match_service.py` | `HeaderMatchService` | Mocked, no DB |
| `test_mode_resolver.py` | `ReconciliationModeResolver` | Mix of DB + mocked |
| `factories.py` | — | Factory-boy fixture builders |
| `conftest.py` | — | Shared pytest fixtures |

## DB Handling

SQLite in-memory is automatically used in tests via root `conftest.py`. No MySQL setup needed.
