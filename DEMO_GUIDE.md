# Medical Data Toolkit Demo Guide

## 1. Requirements

- Git
- Docker Desktop for Windows
- Python 3.12 or compatible Python 3
- Model/API access for the configured LLM
- Official LOINC Table CSV if you want full LOINC terminology mapping

The default toolkit configuration uses Gemini and expects `GEMINI_API_KEY`.

The default toolkit configuration also expects generated LOINC knowledge-base
CSVs mounted at `/data` inside the container. See `docs/LOINC_SETUP.md`.

## 2. Clone Repository

PowerShell:

```powershell
git clone https://github.com/Google-Health/medical-data-toolkit.git "C:\FHIR toolkit"
cd "C:\FHIR toolkit"
```

## 3. Configure Environment

Copy the example file for reference:

```powershell
Copy-Item .env.example .env
```

Set required runtime variables in PowerShell:

```powershell
$env:GEMINI_API_KEY = "YOUR_GEMINI_API_KEY"
$env:TOOLKIT_URL = "http://localhost:8080"
$env:DEMO_PORT = "5000"
```

Important variables:

- `GEMINI_API_KEY`: used by default `src/config.yaml` for Gemini classifier and
  extractor clients.
- `API_KEY`: used by LOINC builder scripts if `--api_key` is not provided.
- `TOOLKIT_URL`: used by the demo Flask app to proxy to the toolkit.
- `TOOLKIT_TIMEOUT_SECONDS`: demo backend request timeout.
- `DEMO_PORT`: demo Flask port.
- `LITELLM_API_KEY`: used only if you switch `src/config.yaml` to a LiteLLM
  configuration.

## 4. Configure LOINC

For full LOINC mapping:

1. Download the official LOINC Table CSV from `https://loinc.org/downloads/loinc-table/`.
2. Build the core analyte, system, and property KB CSV files using the commands
   in `docs/LOINC_SETUP.md`.
3. Place or output them into `C:\FHIR toolkit\data`.
4. Mount that folder into the container as `/data`.

If LOINC KBs are unavailable, do not invent mappings. The demo will report:

```text
LOINC knowledge bases are not configured. Document extraction and FHIR demonstration may run with limited terminology mapping.
```

In the current unmodified toolkit source, the three configured KB CSV paths are
required at runtime. Missing files can prevent real conversion.

## 5. Build Toolkit Docker Image

PowerShell:

```powershell
cd "C:\FHIR toolkit"
docker build -t medical-data-toolkit-image .
```

The Docker build runs the repository unit tests.

## 6. Start Toolkit Container

With LOINC KB files:

```powershell
docker rm -f medical-data-toolkit-container 2>$null
docker run --name medical-data-toolkit-container `
  -p 8080:8080 `
  -e GEMINI_API_KEY="$env:GEMINI_API_KEY" `
  -v "C:\FHIR toolkit\data:/data:ro" `
  medical-data-toolkit-image
```

If you use a custom model/client configuration, edit `src/config.yaml` before
building or run the server with a custom config inside a custom image.

## 7. Verify Toolkit

The toolkit exposes a simple root health check:

```powershell
Invoke-WebRequest http://localhost:8080/ -UseBasicParsing
```

Expected body:

```text
Healthcheck OK
```

To test conversion after configuration:

```powershell
Invoke-WebRequest `
  -Method Post `
  -ContentType "application/pdf" `
  -InFile "C:\path\to\synthetic-lab-report.pdf" `
  -Uri "http://localhost:8080/document_to_fhir"
```

## 8. Start Demo

PowerShell:

```powershell
cd "C:\FHIR toolkit"
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r demo\requirements.txt
$env:TOOLKIT_URL = "http://localhost:8080"
python demo\app.py
```

## 8A. No-Docker Local Toolkit Option

Docker is still the recommended way to run the toolkit because the repository's
production entrypoint uses Linux nginx and gunicorn. For a Windows-only
educational demo, you can run the same Flask app directly with the helper
script in `scripts/run_toolkit_dev_server.py`.

Use Python 3.12 or 3.13. The pinned toolkit dependency `litellm==1.83.14` does
not install on Python 3.14.

PowerShell:

```powershell
cd "C:\FHIR toolkit"

py -3.12 -m venv .venv-toolkit
.\.venv-toolkit\Scripts\Activate.ps1

python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install colorama tzdata

$env:GEMINI_API_KEY = "YOUR_GEMINI_API_KEY"
$env:TOOLKIT_CONFIG_FILE = "src/config.yaml"
$env:TOOLKIT_DEV_PORT = "8080"

python scripts\run_toolkit_dev_server.py
```

In a second PowerShell window, start the demo UI:

```powershell
cd "C:\FHIR toolkit"

py -3.12 -m venv .venv-demo
.\.venv-demo\Scripts\Activate.ps1

python -m pip install --upgrade pip
python -m pip install -r demo\requirements.txt

$env:TOOLKIT_URL = "http://localhost:8080"
python demo\app.py
```

## 9. Open Demo

Open:

```text
http://127.0.0.1:5000
```

The page shows `Toolkit Connected` when `TOOLKIT_URL` responds to the root
health check.

## 10. Upload Synthetic Lab Report

Use a synthetic PDF, JPEG, or PNG only. See `demo/sample/README.md` for sample
fake content.

Click `Browse File`, choose the synthetic report, then click `Convert to FHIR`.
The browser uploads multipart form data to the demo backend. The demo backend
reads the raw bytes and posts them to the toolkit's existing
`/document_to_fhir` endpoint.

## 11. Understand Output

- Observation: a FHIR resource for an individual lab measurement.
- DiagnosticReport: the FHIR report resource that references the observations.
- Patient: the person described by the lab report.
- Composition: the document entry point. In a FHIR document Bundle, Composition
  is first.
- Document Bundle: the FHIR Bundle of type `document` containing the complete
  ABDM-compatible report.
- LOINC: a standard terminology for lab tests. The toolkit maps LOINC only when
  the runtime knowledge bases provide a candidate.

The raw FHIR JSON viewer displays the exact JSON returned by the toolkit.

## 12. Troubleshooting

Docker unavailable:

```powershell
docker version
```

Start Docker Desktop if the command fails.

Port 8080 already in use:

```powershell
Get-NetTCPConnection -LocalPort 8080 -ErrorAction SilentlyContinue
```

Stop the conflicting service or map a different host port and update
`TOOLKIT_URL`.

Model API key missing:

```powershell
$env:GEMINI_API_KEY = "YOUR_GEMINI_API_KEY"
```

Invalid model configuration:

- Check `src/config.yaml`.
- Supported server client types are `GeminiClient`, `GemmaClient`, and
  `LiteLLMClient`.
- The configured model must support image/document understanding for this
  workflow.

Toolkit connection refused:

- Confirm the container is running: `docker ps`
- Confirm health check: `Invoke-WebRequest http://localhost:8080/ -UseBasicParsing`
- Confirm `TOOLKIT_URL` is `http://localhost:8080`

LOINC KB files missing:

- Build the KB files using `docs/LOINC_SETUP.md`.
- Mount `C:\FHIR toolkit\data` to `/data`.
- Confirm expected filenames exist.

Unsupported document:

- The current code supports diagnostic/laboratory reports.
- Handwritten medical documents are not supported.
- Use a clear synthetic typed lab report.

PDF processing failure:

- Confirm the file is a valid PDF.
- Confirm the page count is no greater than `max_pdf_pages` in `src/config.yaml`.
- Try exporting the synthetic report again or uploading a PNG/JPEG version.
