# All_Testing

Root-level test suite organized app-wise.

Structure:
- One folder per application area
- Test modules live outside `apps/`
- Can be run with Django test runner or pytest explicitly by path

Example:
- `python manage.py test All_Testing.procurement --settings=config.test_settings`
