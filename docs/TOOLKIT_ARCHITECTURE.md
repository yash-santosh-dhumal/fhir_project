# Medical Data Toolkit Architecture

This document describes the source-code flow for the current repository, not
only the README description.

## End-to-End Flow

```text
User Document
|
v
REST API
|
v
Document Preprocessing
|
v
Document Classifier
|
v
Lab Report Extractor
|
v
Structured Lab Report Schema
|
v
Terminology Mapper
|
v
LOINC Query Engine
|
v
ABDM FHIR Generator
|
v
FHIR Document Bundle
```

## REST API Entry Point

The Flask application is defined in `src/rest_server.py`.

`POST /document_to_fhir` reads the raw request body with
`flask.request.get_data()`. It does not expect multipart/form-data. If the body
is empty, it returns HTTP 400 with `{"error": "Message body is empty."}`.

The endpoint validates or infers content type, then calls `_document_to_fhir`.
That function lazily initializes a `CompositeDocumentStandardizer` from the YAML
config file and returns `result.model_dump(mode="json")` as JSON.

## Input Validation and Supported Formats

Source file: `src/rest_server.py`

Responsible functions:

- `_validate_or_infer_content_type`
- `_pdf_page_count`
- `_is_image`
- `_get_image_mime_type`
- `_get_max_pdf_pages`

Supported MIME types are:

- `application/pdf`
- `image/png`
- `image/jpeg`

If a `Content-Type` header is present, the server verifies that the bytes
actually match. Without a header, it tries PDF first and image second. PDFs over
`max_pdf_pages` are rejected. The default is 40 pages, configured in
`src/config.yaml`.

## Server Configuration and LLM Clients

Source files:

- `src/rest_server.py`
- `src/config.yaml`
- `src/document_to_fhir/common/model_client.py`

Default `src/config.yaml` selects:

- classifier: `GeminiClient`, model `gemini-3.1-flash-lite-preview`
- extractor: `GeminiClient`, model `gemini-3-flash-preview`
- API key environment variable: `GEMINI_API_KEY`

`_create_llm_client` also supports `GemmaClient` and `LiteLLMClient`.
The commented Gemma example in `src/config.yaml` is a LiteLLM-based
configuration using `LITELLM_API_KEY` and an OpenAI-compatible API base.

`GeminiClient.supports_pdf` returns `True`; `GemmaClient.supports_pdf` returns
`False`; `LiteLLMClient` uses the configured `supports_pdf` parameter. In the
current pipeline, PDF input is rendered to images before classification and
extraction, so the classifier and extractor normally receive image parts.

## PDF Preprocessing

Source files:

- `src/document_to_fhir/core/orchestrator/composite_document_standardizer.py`
- `src/document_to_fhir/common/pdf_util.py`

Class and functions:

- `CompositeDocumentStandardizer.standardize`
- `convert_pdf_pages_to_png_images`
- `extract_pil_images_from_pdf`

PDF bytes are rendered into PNG page images at 300 DPI. Image uploads are passed
as a one-item image list. After preprocessing, the pipeline works with image
bytes and a working MIME type of `image/png` or `image/jpeg`.

## Document Classification

Source files:

- `src/document_to_fhir/core/classification/classifier.py`
- `src/document_to_fhir/core/classification/suggested_prompts/composite_document_classification.jinja2`
- `src/document_to_fhir/common/schema/standardized_composite_medical_document.py`
- `src/document_to_fhir/common/schema/document_types.py`

Classes:

- `MultiDocumentClassifier`
- `DocumentSegment`
- `CompositeDocument`

`MultiDocumentClassifier.classify` prepares image chunks, calls the configured
LLM with the classification prompt, and validates the response as a
`CompositeDocument`. Segments include document type, page range, reasoning, and
handwritten percentage.

`process_handwritten_medical_pages` changes medical segments to `HANDWRITTEN`
when handwritten content exceeds the threshold. Handwritten medical documents
are not supported by the toolkit.

## Supported Document Types

Source files:

- `src/rest_server.py`
- `src/config.yaml`
- `src/document_to_fhir/common/schema/document_types.py`

The code mapping currently includes `LABORATORY_REPORT` only. The config file
lists the same supported type. Unsupported segments may be passed through or
discarded depending on `document_standardization_policy`.

## Lab Report Extraction

Source files:

- `src/document_to_fhir/core/orchestrator/medical_document_standardizer.py`
- `src/document_to_fhir/core/extraction/medical_extractor.py`
- `src/document_to_fhir/core/extraction/extractors/lab_report_extractor.py`
- `src/document_to_fhir/core/extraction/suggested_prompts/lab_report.jinja2`

Classes:

- `MedicalDocumentStandardizer`
- `MedicalExtractor`
- `LabReportExtractor`

For a supported lab-report segment, `MedicalDocumentStandardizer.standardize`
first calls `extractor.extract(images, mime_type)`. The extractor sends the
segment images and lab prompt to the LLM and validates the structured response
against the configured Pydantic schema.

## Structured Lab Report Schema

Source files:

- `src/document_to_fhir/common/schema/medical_documents.py`
- `src/document_to_fhir/common/schema/resources.py`
- `src/document_to_fhir/common/schema/abdm/abdm_medical_documents.py`
- `src/document_to_fhir/common/schema/abdm/abdm_resources.py`

Classes:

- `LabReport`
- `AbdmLabReport`
- `Patient`
- `Organization`
- `Practitioner`
- `LabTest`
- `ReferenceRange`

The extracted lab report includes patient details, optional service provider,
optional practitioner, sample collection time, and a list of lab tests. `LabTest`
contains `core_analyte`, `name`, `result`, optional unit, specimen, method,
panel, reference ranges, and terminology fields used after mapping.

## Core Analyte Extraction

Source file: `src/document_to_fhir/common/schema/resources.py`

`LabTest.core_analyte` is part of the LLM extraction schema. The prompt asks the
model to extract the unique measured entity independent of specimen, method, or
timing. Runtime LOINC lookup begins with this field.

## Terminology Mapping and LOINC Querying

Source files:

- `src/document_to_fhir/core/orchestrator/medical_document_standardizer.py`
- `src/document_to_fhir/core/medical_coding/mapper/terminology_mapper.py`
- `src/document_to_fhir/core/medical_coding/mapper/terminology_mappers/loinc_terminology_mapper.py`
- `src/document_to_fhir/core/medical_coding/loinc/query.py`
- `src/document_to_fhir/core/medical_coding/loinc/axes_kb/core_analyte/index.py`
- `src/document_to_fhir/core/medical_coding/loinc/axes_kb/system/mapper.py`
- `src/document_to_fhir/core/medical_coding/loinc/axes_kb/scale_type/mapper.py`
- `src/document_to_fhir/core/medical_coding/loinc/axes_kb/property/mapper.py`

Classes and functions:

- `enrich_terminology_recursive`
- `LoincTerminologyMapper`
- `LoincQueryEngine`
- `AnalytesIndex`
- `SpecimenToSystemMapper`
- `ScaleMapper`
- `UnitToPropertyMapper`

`enrich_terminology_recursive` walks the extracted Pydantic object tree. When it
finds a `LabTest`, it invokes `LoincTerminologyMapper.map_inplace`.

`LoincQueryEngine.query` searches by `core_analyte`, then applies soft filters:
specimen-to-system, result-to-scale, and unit-to-property. If candidates remain,
the mapper writes the first result's `loinc_num` and `long_common_name` into the
`LabTest`. If no candidates are found, the fields remain `None`.

## ABDM FHIR Generation

Source files:

- `src/document_to_fhir/core/fhir/abdm/abdm_lab_report_fhir_generator.py`
- `src/document_to_fhir/core/fhir/abdm/abdm_fhir_resource_converter.py`
- `src/document_to_fhir/core/fhir/fhir_utils.py`
- `src/document_to_fhir/core/fhir/abdm/abdm_bundle_enricher.py`

Classes and functions:

- `AbdmLabReportFhirGenerator`
- `create_patient`
- `create_practitioner`
- `create_organization`
- `create_encounter`
- `create_lab_observation`
- `create_panel_observation`
- `create_diagnostic_report`
- `create_document_reference`

The generator creates a FHIR R4 document Bundle. Composition is first, followed
by Patient, optional Practitioner and Organization, Encounter, Observations, and
DiagnosticReport. Panelled lab tests produce member Observations plus a panel
Observation. Unpanelled tests produce direct Observations.

## ABDM Profiles Used

The generator and converter set these profile URLs:

- Bundle: `https://nrces.in/ndhm/fhir/r4/StructureDefinition/DocumentBundle`
- Composition: `https://nrces.in/ndhm/fhir/r4/StructureDefinition/DiagnosticReportRecord`
- Patient: `https://nrces.in/ndhm/fhir/r4/StructureDefinition/Patient`
- Practitioner: `https://nrces.in/ndhm/fhir/r4/StructureDefinition/Practitioner`
- Organization: `https://nrces.in/ndhm/fhir/r4/StructureDefinition/Organization`
- Encounter: `https://nrces.in/ndhm/fhir/r4/StructureDefinition/Encounter`
- Observation: `https://nrces.in/ndhm/fhir/r4/StructureDefinition/Observation`
- DiagnosticReport: `https://nrces.in/ndhm/fhir/r4/StructureDefinition/DiagnosticReportLab`
- DocumentReference, only when document attachment enrichment is enabled:
  `https://nrces.in/ndhm/fhir/r4/StructureDefinition/DocumentReference`

## Final API Response Structure

`POST /document_to_fhir` returns a
`StandardizedCompositeMedicalDocumentWithContext` JSON object, not a bare FHIR
Bundle.

Top-level fields:

- `standardized_medical_documents`: list of standardized segment objects
- `metadata`: optional pipeline metadata when `RETURN_METADATA` is true

Each standardized document may include:

- `document_type`
- `start_page`
- `end_page`
- `medical_document`
- `fhir_bundle`

The `fhir_bundle` field is the ABDM FHIR R4 document Bundle produced for that
segment.
