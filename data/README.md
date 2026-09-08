# Demo LOINC Placeholder Files

These CSV files are intentionally empty placeholders for local demo startup.
They do not contain LOINC records and they do not provide terminology mapping.

Use them only when you want the Medical Data Toolkit container to initialize and
demonstrate extraction/FHIR generation with limited terminology mapping.

Expected limitation:

```text
LOINC knowledge bases are not configured. Document extraction and FHIR demonstration may run with limited terminology mapping.
```

For real LOINC mapping, replace these files with knowledge bases generated from
the official LOINC Table by following `docs/LOINC_SETUP.md`.
