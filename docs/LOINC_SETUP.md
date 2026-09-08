# LOINC Setup

Medical Data Toolkit separates LOINC support into two phases:

1. Knowledge Base Construction: offline CSV generation from the official LOINC
   dataset, enriched with an LLM.
2. Runtime Querying: loading those generated CSV files into mappers and
   `LoincQueryEngine` during toolkit startup.

The repository does not include the proprietary/restricted LOINC dataset. Do not
redistribute LOINC data or generated knowledge bases unless your LOINC license
allows it.

## Required Runtime Files

The default `src/config.yaml` expects these files inside the container:

```yaml
loinc_analaytes_index_csv_path: "/data/analyte_records_top_2000.csv"
loinc_system_kb_csv_path: "/data/loinc_axis_system_kb_top_2000.csv"
loinc_property_kb_csv_path: "/data/loinc_axis_property_kb_top_2000.csv"
```

If these files are missing, the current source code raises an error during
standardizer initialization. Full terminology mapping is not available until
these knowledge bases exist and are mounted into the container.

## Limited Demo Placeholder Files

This checkout includes header-only placeholder CSV files in `data/`:

```text
data/analyte_records_top_2000.csv
data/loinc_axis_system_kb_top_2000.csv
data/loinc_axis_property_kb_top_2000.csv
```

These files are only for local educational startup. They contain no LOINC
records and therefore cannot produce LOINC mappings. They avoid fake terminology
data while allowing the container to load the configured file paths.

Demo message to expect when the KBs are not configured:

```text
LOINC knowledge bases are not configured. Document extraction and FHIR demonstration may run with limited terminology mapping.
```

In the unmodified toolkit, missing KB files block startup/conversion instead of
silently continuing without mapping.

## Acquire the LOINC Table

Download the official LOINC Table CSV manually from:

```text
https://loinc.org/downloads/loinc-table/
```

You need the path to `LoincTable.csv`.

## Python Environment for Builders

PowerShell:

```powershell
cd "C:\FHIR toolkit"
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install -r src\document_to_fhir\core\medical_coding\loinc\requirements.txt
$env:API_KEY = "YOUR_MODEL_API_KEY"
```

Use an LLM client supported by `src/document_to_fhir/common/model_client.py`:

- `gemini`
- `gemma`
- `litellm`

The builders default `--api_key` to the `API_KEY` environment variable.

## Build Core Analyte KB

PowerShell:

```powershell
python -m src.document_to_fhir.core.medical_coding.loinc.axes_kb.core_analyte.builder_main `
  --loinc_csv_path="C:\path\to\LoincTable.csv" `
  --output_csv_folder="C:\FHIR toolkit\data" `
  --client_type="litellm" `
  --model_name="gemma-4-26b-a4b-it" `
  --max_rank=2000 `
  --workers=10
```

Expected output file:

```text
C:\FHIR toolkit\data\analyte_records_top_2000.csv
```

## Build System KB

PowerShell:

```powershell
python -m src.document_to_fhir.core.medical_coding.loinc.axes_kb.system.builder_main `
  --loinc_csv_path="C:\path\to\LoincTable.csv" `
  --output_csv_folder="C:\FHIR toolkit\data" `
  --client_type="litellm" `
  --model_name="gemma-4-26b-a4b-it" `
  --max_rank=2000 `
  --workers=10
```

Expected output file:

```text
C:\FHIR toolkit\data\loinc_axis_system_kb_top_2000.csv
```

## Build Property KB

PowerShell:

```powershell
python -m src.document_to_fhir.core.medical_coding.loinc.axes_kb.property.builder_main `
  --loinc_csv_path="C:\path\to\LoincTable.csv" `
  --output_csv_folder="C:\FHIR toolkit\data" `
  --client_type="litellm" `
  --model_name="gemma-4-26b-a4b-it" `
  --max_rank=2000 `
  --workers=10
```

Expected output file:

```text
C:\FHIR toolkit\data\loinc_axis_property_kb_top_2000.csv
```

## Runtime Querying

At runtime, `src/rest_server.py` loads:

- `AnalytesIndex.from_csv(loinc_analaytes_index_csv_path)`
- `SpecimenToSystemMapper.from_csv(loinc_system_kb_csv_path)`
- `UnitToPropertyMapper.from_csv(loinc_property_kb_csv_path)`
- `ScaleMapper()`

These are passed to `LoincQueryEngine`, then wrapped by
`LoincTerminologyMapper`.

## Docker Volume Mount

After building the CSV files, mount the folder to `/data` when running Docker:

```powershell
docker run --name medical-data-toolkit-container `
  -p 8080:8080 `
  -e GEMINI_API_KEY="$env:GEMINI_API_KEY" `
  -v "C:\FHIR toolkit\data:/data:ro" `
  medical-data-toolkit-image
```

The CSV filenames in the mounted folder must match the paths in
`src/config.yaml`, or you must build a custom config file and run the server with
that config.
