const fileInput = document.getElementById("fileInput");
const browseButton = document.getElementById("browseButton");
const convertButton = document.getElementById("convertButton");
const dropZone = document.getElementById("dropZone");
const fileInfo = document.getElementById("fileInfo");
const errorBox = document.getElementById("errorBox");
const stageList = document.getElementById("stageList");
const duration = document.getElementById("duration");
const rawJson = document.getElementById("rawJson");
const copyButton = document.getElementById("copyButton");
const healthBadge = document.getElementById("healthBadge");

let selectedFile = null;
let lastRawJson = "{}";

browseButton.addEventListener("click", () => fileInput.click());
fileInput.addEventListener("change", () => setSelectedFile(fileInput.files[0]));
convertButton.addEventListener("click", convertSelectedFile);
copyButton.addEventListener("click", copyRawJson);

["dragenter", "dragover"].forEach((eventName) => {
  dropZone.addEventListener(eventName, (event) => {
    event.preventDefault();
    dropZone.classList.add("dragover");
  });
});

["dragleave", "drop"].forEach((eventName) => {
  dropZone.addEventListener(eventName, (event) => {
    event.preventDefault();
    dropZone.classList.remove("dragover");
  });
});

dropZone.addEventListener("drop", (event) => {
  setSelectedFile(event.dataTransfer.files[0]);
});

async function checkHealth() {
  try {
    const response = await fetch("/api/health");
    const data = await response.json();
    if (data.toolkit === "ok") {
      healthBadge.textContent = "Toolkit Connected";
      healthBadge.className = "status-badge online";
    } else {
      healthBadge.textContent = "Toolkit Offline";
      healthBadge.className = "status-badge offline";
    }
  } catch {
    healthBadge.textContent = "Toolkit Offline";
    healthBadge.className = "status-badge offline";
  }
}

function setSelectedFile(file) {
  selectedFile = file || null;
  convertButton.disabled = !selectedFile;
  hideError();
  if (!selectedFile) {
    fileInfo.textContent = "No file selected";
    return;
  }
  fileInfo.innerHTML = `<strong>${escapeHtml(selectedFile.name)}</strong><br>${formatBytes(selectedFile.size)}`;
}

async function convertSelectedFile() {
  if (!selectedFile) return;

  hideError();
  setProcessing(true);
  duration.textContent = "API processing duration: Processing...";

  const formData = new FormData();
  formData.append("file", selectedFile);

  try {
    const response = await fetch("/api/convert", {
      method: "POST",
      body: formData,
    });
    const data = await response.json();
    if (!response.ok) {
      showError(data.error || "Toolkit API error.");
      duration.textContent = data.duration_ms
        ? `API processing duration: ${data.duration_ms} ms`
        : "API processing duration: Not available";
      return;
    }
    renderSummary(data.summary);
    renderFhirVisualization(data.raw);
    lastRawJson = JSON.stringify(data.raw, null, 2);
    rawJson.textContent = lastRawJson;
    delete rawJson.dataset.highlighted;
    if (window.hljs) hljs.highlightElement(rawJson);
    copyButton.disabled = false;
    duration.textContent = `API processing duration: ${data.duration_ms} ms`;
  } catch {
    showError("The demo backend returned a response that could not be processed.");
  } finally {
    setProcessing(false);
    checkHealth();
  }
}

function renderSummary(summary) {
  const patient = summary.patient || {};
  document.getElementById("patientName").textContent = patient.name || "Not available";
  document.getElementById("patientGender").textContent = patient.gender || "Not available";
  document.getElementById("patientBirthDate").textContent = patient.birthDate || "Not available";

  const observations = summary.observations || [];
  const body = document.getElementById("observationsBody");
  body.innerHTML = "";
  if (!observations.length) {
    body.innerHTML = '<tr><td colspan="5">No observations found in the returned FHIR Bundle.</td></tr>';
  } else {
    observations.forEach((obs) => {
      const row = document.createElement("tr");
      const loinc = obs.loinc === "Not available"
        ? `<span>Not available</span><br><span class="loinc-missing">${escapeHtml(obs.loincMessage)}</span>`
        : escapeHtml(obs.loinc);
      row.innerHTML = `
        <td>${escapeHtml(obs.test)}</td>
        <td>${loinc}</td>
        <td>${escapeHtml(obs.result)}</td>
        <td>${escapeHtml(obs.unit)}</td>
        <td>${escapeHtml(obs.referenceRange)}</td>
      `;
      body.appendChild(row);
    });
  }

  renderBundle(summary.bundle || {});
}

function renderBundle(bundle) {
  const container = document.getElementById("bundleSummary");
  if (!bundle.found) {
    container.textContent = "No FHIR Bundle found in the toolkit response.";
  } else {
    const counts = bundle.resource_counts || {};
    const rows = [
      ["Bundle Type", bundle.type || "Not available"],
      ["Resources", bundle.resource_count || 0],
      ...Object.entries(counts),
    ];
    container.innerHTML = rows.map(([label, value]) => (
      `<div class="summary-row"><span>${escapeHtml(label)}</span><strong>${escapeHtml(String(value))}</strong></div>`
    )).join("");
  }

  const profiles = document.getElementById("profileList");
  const profileValues = bundle.profiles || [];
  profiles.innerHTML = profileValues.length
    ? profileValues.map((profile) => `<li>${escapeHtml(profile)}</li>`).join("")
    : "<li>Not available</li>";
}

function renderFhirVisualization(raw) {
  const vizSection = document.getElementById("fhirVizSection");
  const overview = document.getElementById("bundleOverview");
  const grid = document.getElementById("fhirResourceCards");

  if (!vizSection || !overview || !grid) return;

  if (!raw) {
    vizSection.hidden = true;
    return;
  }

  // Find the bundle
  let bundle = null;
  if (raw.resourceType === "Bundle") {
    bundle = raw;
  } else if (raw.standardized_medical_documents && raw.standardized_medical_documents.length > 0) {
    bundle = raw.standardized_medical_documents[0].fhir_bundle;
  }

  if (!bundle || !bundle.entry) {
    vizSection.hidden = true;
    return;
  }

  vizSection.hidden = false;
  overview.innerHTML = "";
  grid.innerHTML = "";

  // Render overview
  const totalResources = bundle.entry.length;
  overview.innerHTML = `
    <div class="bo-field">
      <span class="bo-label">Bundle ID</span>
      <span class="bo-value">${escapeHtml(bundle.id || "N/A")}</span>
    </div>
    <div class="bo-field">
      <span class="bo-label">Timestamp</span>
      <span class="bo-value">${escapeHtml(bundle.timestamp || "N/A")}</span>
    </div>
    <div class="bo-field">
      <span class="bo-label">Resources</span>
      <span class="bo-value">${totalResources}</span>
    </div>
  `;

  // Render resource cards
  bundle.entry.forEach((entry) => {
    const resource = entry.resource;
    if (!resource) return;

    const rt = resource.resourceType;
    const badgeClass = `fhir-badge fhir-badge-${rt.toLowerCase()}`;

    const card = document.createElement("div");
    card.className = "fhir-card";

    let fieldsHtml = "";
    
    // Add common fields
    if (resource.id) fieldsHtml += renderFhirField("id", resource.id);
    if (resource.status) fieldsHtml += renderFhirField("status", resource.status);
    
    // Render specific fields based on resource type
    if (rt === "Patient") {
      const name = resource.name?.[0];
      if (name) fieldsHtml += renderFhirField("name", name.text || `${name.given?.join(" ")} ${name.family}`);
      if (resource.gender) fieldsHtml += renderFhirField("gender", resource.gender);
      if (resource.birthDate) fieldsHtml += renderFhirField("birthDate", resource.birthDate);
      if (resource.identifier?.[0]) fieldsHtml += renderFhirField("identifier", resource.identifier[0].value);
    } 
    else if (rt === "Practitioner") {
      const name = resource.name?.[0];
      if (name) fieldsHtml += renderFhirField("name", name.text || `${name.prefix?.join(" ")} ${name.given?.join(" ")} ${name.family}`);
      if (resource.identifier?.[0]) fieldsHtml += renderFhirField("identifier", resource.identifier[0].value);
    }
    else if (rt === "Organization") {
      if (resource.name) fieldsHtml += renderFhirField("name", resource.name);
    }
    else if (rt === "Observation") {
      if (resource.code) fieldsHtml += renderFhirField("code", renderCodeableConcept(resource.code));
      if (resource.valueQuantity) {
        const vq = resource.valueQuantity;
        fieldsHtml += renderFhirField("value", `<div class="fhir-obs-result"><span class="fhir-obs-value">${vq.value}</span><span class="fhir-obs-unit">${escapeHtml(vq.unit || vq.code || "")}</span></div>`);
      }
      if (resource.referenceRange?.[0]) {
        const ref = resource.referenceRange[0];
        fieldsHtml += renderFhirField("refRange", `<span class="fhir-ref-range">${escapeHtml(ref.text || (ref.low?.value + " - " + ref.high?.value))}</span>`);
      }
    }
    else if (rt === "DiagnosticReport") {
      if (resource.code) fieldsHtml += renderFhirField("code", renderCodeableConcept(resource.code));
      if (resource.conclusion) fieldsHtml += renderFhirField("conclusion", resource.conclusion);
      if (resource.result) fieldsHtml += renderFhirField("result", resource.result.map(r => `<span class="fhir-ref">${escapeHtml(r.reference)}</span>`).join("<br>"));
    }
    else if (rt === "Composition") {
      if (resource.title) fieldsHtml += renderFhirField("title", resource.title);
      if (resource.type) fieldsHtml += renderFhirField("type", renderCodeableConcept(resource.type));
    }
    else if (rt === "Encounter") {
      if (resource.class) fieldsHtml += renderFhirField("class", `${resource.class.code} (${resource.class.display})`);
    }

    // Render references
    if (resource.subject?.reference) fieldsHtml += renderFhirField("subject", `<span class="fhir-ref">${escapeHtml(resource.subject.reference)}</span>`);
    if (resource.encounter?.reference) fieldsHtml += renderFhirField("encounter", `<span class="fhir-ref">${escapeHtml(resource.encounter.reference)}</span>`);

    let footerHtml = "";
    if (resource.meta?.profile) {
      footerHtml = `<div class="fhir-card-footer">
        ${resource.meta.profile.map(p => `<a href="${p}" target="_blank" class="fhir-profile-link">${escapeHtml(p)}</a>`).join("")}
      </div>`;
    }

    card.innerHTML = `
      <div class="fhir-card-header">
        <span class="${badgeClass}">${escapeHtml(rt)}</span>
        <span class="fhir-id">${escapeHtml(resource.id || "")}</span>
      </div>
      <div class="fhir-card-body">
        ${fieldsHtml}
      </div>
      ${footerHtml}
    `;
    grid.appendChild(card);
  });
}

function renderFhirField(label, valueHtml) {
  return `
    <div class="fhir-field">
      <div class="fhir-label">${escapeHtml(label)}</div>
      <div class="fhir-value">${valueHtml}</div>
    </div>
  `;
}

function renderCodeableConcept(cc) {
  if (!cc) return "";
  let html = "";
  if (cc.text) {
    html += `<div>${escapeHtml(cc.text)}</div>`;
  }
  if (cc.coding && cc.coding.length > 0) {
    cc.coding.forEach(c => {
      html += `
        <div class="fhir-coding">
          <span class="fhir-coding-system">${escapeHtml(c.system || "")}</span>
          <span><span class="fhir-coding-code">${escapeHtml(c.code || "")}</span> <span class="fhir-coding-display">${escapeHtml(c.display || "")}</span></span>
        </div>
      `;
    });
  }
  return html;
}

function setProcessing(isProcessing) {
  convertButton.disabled = isProcessing || !selectedFile;
  stageList.classList.toggle("processing", isProcessing);
}

function showError(message) {
  errorBox.textContent = message;
  errorBox.hidden = false;
}

function hideError() {
  errorBox.hidden = true;
  errorBox.textContent = "";
}

async function copyRawJson() {
  await navigator.clipboard.writeText(lastRawJson);
  copyButton.textContent = "Copied";
  setTimeout(() => {
    copyButton.textContent = "Copy JSON";
  }, 1200);
}

function formatBytes(bytes) {
  if (bytes < 1024) return `${bytes} bytes`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

checkHealth();
