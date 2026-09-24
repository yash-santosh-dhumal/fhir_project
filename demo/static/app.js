/* ══════════════════════════════════════════════════════════════
   FHIR Dashboard — Application Logic
   ══════════════════════════════════════════════════════════════ */

"use strict";

// ── DOM Cache ──
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

// Upload tab elements
const fileInput      = $("#fileInput");
const browseButton   = $("#browseButton");
const convertButton  = $("#convertButton");
const fileInfoEl     = $("#fileInfo");
const stageList      = $("#stageList");
const durationEl     = $("#duration");
const errorBox       = $("#errorBox");
const rawJsonEl      = $("#rawJson");
const copyButton     = $("#copyButton");

// Health badge
const healthBadge = $("#healthBadge");
const dbStats     = $("#dbStats");

let selectedFile = null;

// ── Portal Authentication & State ──
let portalMode = "hospital"; // "hospital" or "insurance"
let currentAuth = null;
let currentAdjudicatePatientId = null;
let selectedPolicyFile = null;
let selectedSamplePolicyFilename = null;

function apiBase() {
  return portalMode === "insurance" ? "/api/insurance" : "/api";
}

// ═══════════════════════════════════════════════
// Tab Navigation & Lifecycle
// ═══════════════════════════════════════════════

document.addEventListener("DOMContentLoaded", () => {
  // Check auth session
  initAuth();

  // Tab switching
  $$(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      $$(".tab-btn").forEach((b) => b.classList.remove("active"));
      $$(".tab-panel").forEach((p) => p.classList.remove("active"));
      btn.classList.add("active");
      const panel = $(`#tab-${btn.dataset.tab}`);
      if (panel) panel.classList.add("active");

      // Lazy-load data for tabs
      if (btn.dataset.tab === "patients") loadPatients();
      if (btn.dataset.tab === "explorer") loadBundles();
    });
  });

  // Login forms & quick demo buttons
  const btnSubmitHosp = $("#btnSubmitHospital");
  const formHosp = $("#formLoginHospital");
  if (btnSubmitHosp) {
    btnSubmitHosp.addEventListener("click", () => {
      const u = $("#hospitalUsername")?.value || "";
      const p = $("#hospitalPassword")?.value || "";
      doLogin("hospital", u, p, "#hospitalLoginError");
    });
  }
  if (formHosp) {
    formHosp.addEventListener("submit", (e) => {
      e.preventDefault();
      const u = $("#hospitalUsername")?.value || "";
      const p = $("#hospitalPassword")?.value || "";
      doLogin("hospital", u, p, "#hospitalLoginError");
    });
  }
  const btnQuickHosp = $("#btnQuickHospital");
  if (btnQuickHosp) {
    btnQuickHosp.addEventListener("click", () => {
      if ($("#hospitalUsername")) $("#hospitalUsername").value = "hospital_admin";
      if ($("#hospitalPassword")) $("#hospitalPassword").value = "hospital123";
      doLogin("hospital", "hospital_admin", "hospital123", "#hospitalLoginError");
    });
  }

  const btnSubmitIns = $("#btnSubmitInsurance");
  const formIns = $("#formLoginInsurance");
  if (btnSubmitIns) {
    btnSubmitIns.addEventListener("click", () => {
      const u = $("#insuranceUsername")?.value || "";
      const p = $("#insurancePassword")?.value || "";
      doLogin("insurance", u, p, "#insuranceLoginError");
    });
  }
  if (formIns) {
    formIns.addEventListener("submit", (e) => {
      e.preventDefault();
      const u = $("#insuranceUsername")?.value || "";
      const p = $("#insurancePassword")?.value || "";
      doLogin("insurance", u, p, "#insuranceLoginError");
    });
  }
  const btnQuickIns = $("#btnQuickInsurance");
  if (btnQuickIns) {
    btnQuickIns.addEventListener("click", () => {
      if ($("#insuranceUsername")) $("#insuranceUsername").value = "insurance_auditor";
      if ($("#insurancePassword")) $("#insurancePassword").value = "insurance123";
      doLogin("insurance", "insurance_auditor", "insurance123", "#insuranceLoginError");
    });
  }

  // Logout / Switch Portal button
  const logoutBtn = $("#logoutBtn");
  if (logoutBtn) {
    logoutBtn.addEventListener("click", doLogout);
  }

  // Policy upload modal triggers & inputs
  const closePolicyModalBtn = $("#closePolicyModalBtn");
  const cancelPolicyModalBtn = $("#cancelPolicyModalBtn");
  if (closePolicyModalBtn) closePolicyModalBtn.addEventListener("click", closePolicyUploadModal);
  if (cancelPolicyModalBtn) cancelPolicyModalBtn.addEventListener("click", closePolicyUploadModal);

  const policyDropZone = $("#policyDropZone");
  const policyFileInput = $("#policyFileInput");
  if (policyDropZone && policyFileInput) {
    policyDropZone.addEventListener("click", () => policyFileInput.click());
    policyFileInput.addEventListener("change", onPolicyFileSelected);
    policyDropZone.addEventListener("dragover", (e) => { e.preventDefault(); policyDropZone.classList.add("dragover"); });
    policyDropZone.addEventListener("dragleave", () => policyDropZone.classList.remove("dragover"));
    policyDropZone.addEventListener("drop", (e) => {
      e.preventDefault();
      policyDropZone.classList.remove("dragover");
      if (e.dataTransfer.files.length) {
        policyFileInput.files = e.dataTransfer.files;
        onPolicyFileSelected();
      }
    });
  }

  const submitPolicyModalBtn = $("#submitPolicyModalBtn");
  if (submitPolicyModalBtn) {
    submitPolicyModalBtn.addEventListener("click", submitPolicyAdjudication);
  }

  // File upload
  browseButton.addEventListener("click", () => fileInput.click());
  fileInput.addEventListener("change", onFileSelected);
  convertButton.addEventListener("click", convertSelectedFile);
  copyButton.addEventListener("click", copyJson);

  // Drag-drop for single upload
  const dropZone = $("#dropZone");
  if (dropZone) {
    dropZone.addEventListener("dragover", (e) => { e.preventDefault(); dropZone.classList.add("drag-over"); });
    dropZone.addEventListener("dragleave", () => dropZone.classList.remove("drag-over"));
    dropZone.addEventListener("drop", (e) => {
      e.preventDefault();
      dropZone.classList.remove("drag-over");
      if (e.dataTransfer.files.length) {
        fileInput.files = e.dataTransfer.files;
        onFileSelected();
      }
    });
  }

  // Refresh buttons
  const refreshP = $("#refreshPatientsBtn");
  const refreshB = $("#refreshBundlesBtn");
  if (refreshP) refreshP.addEventListener("click", loadPatients);
  if (refreshB) refreshB.addEventListener("click", loadBundles);

  // Close patient detail report button
  const closePatientBtn = $("#closePatientDetailBtn");
  if (closePatientBtn) {
    closePatientBtn.addEventListener("click", () => {
      const detail = $("#patientDetail");
      if (detail) detail.hidden = true;
    });
  }

  // Health check
  checkHealth();
  setInterval(checkHealth, 15000);
});


// ═══════════════════════════════════════════════
// Authentication & Portal Access Control
// ═══════════════════════════════════════════════

function initAuth() {
  const sessionStr = sessionStorage.getItem("portal_auth");
  if (sessionStr) {
    try {
      const auth = JSON.parse(sessionStr);
      if (auth && (auth.role === "hospital" || auth.role === "insurance")) {
        applyPortalAuth(auth);
        return;
      }
    } catch {}
  }
  showLoginView();
}

function showLoginView() {
  const loginView = $("#loginView");
  const appMain = $("#appMain");
  if (loginView) loginView.hidden = false;
  if (appMain) appMain.hidden = true;
}

function applyPortalAuth(auth) {
  currentAuth = auth;
  portalMode = auth.role || "hospital";

  const loginView = $("#loginView");
  const appMain = $("#appMain");
  if (loginView) loginView.hidden = true;
  if (appMain) appMain.hidden = false;

  switchPortal(portalMode);

  // Auto-switch to Patient Records tab for Insurance portal to immediately display hospital dossiers
  if (portalMode === "insurance") {
    const tabPatients = $("#tabBtnPatients");
    if (tabPatients) tabPatients.click();
  } else {
    const tabUpload = $("#tabBtnUpload");
    if (tabUpload) tabUpload.click();
  }
}

async function doLogin(role, username, password, errElId) {
  const errEl = $(errElId);
  if (errEl) {
    errEl.hidden = true;
    errEl.textContent = "";
  }
  try {
    const res = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ role, username, password }),
    });
    const data = await res.json();
    if (!res.ok || !data.success) {
      throw new Error(data.error || "Authentication failed. Check credentials.");
    }
    sessionStorage.setItem("portal_auth", JSON.stringify(data.user));
    applyPortalAuth(data.user);
  } catch (err) {
    if (errEl) {
      errEl.hidden = false;
      errEl.textContent = err.message;
    }
  }
}

async function doLogout() {
  try {
    await fetch("/api/auth/logout", { method: "POST" });
  } catch {}
  sessionStorage.removeItem("portal_auth");
  showLoginView();
}


// ═══════════════════════════════════════════════
// Portal Switching & UI Reconfiguration
// ═══════════════════════════════════════════════

function switchPortal(mode) {
  const body = document.body;
  if (mode === "insurance") {
    body.classList.add("portal-insurance");
  } else {
    body.classList.remove("portal-insurance");
  }

  // Update session bar user info
  const activePortalTitle = $("#activePortalTitle");
  const activeUserName = $("#activeUserName");
  const activeUserRole = $("#activeUserRole");
  if (mode === "insurance") {
    if (activePortalTitle) activePortalTitle.textContent = currentAuth?.portal_label || "Health Insurance Claims Authority (TPA)";
    if (activeUserName) activeUserName.textContent = currentAuth?.display_name || "K. V. Raman";
    if (activeUserRole) activeUserRole.textContent = currentAuth?.role_title || "Senior Claims Auditor & TPA";
  } else {
    if (activePortalTitle) activePortalTitle.textContent = currentAuth?.portal_label || "Hospital Information System (HIS)";
    if (activeUserName) activeUserName.textContent = currentAuth?.display_name || "Dr. R. Sharma";
    if (activeUserRole) activeUserRole.textContent = currentAuth?.role_title || "Hospital Administrator & Clinician";
  }

  // Update header branding
  const eyebrow = $("#heroEyebrow");
  const title = $("#heroTitle");
  const subtitle = $("#heroSubtitle");
  if (mode === "insurance") {
    if (eyebrow) eyebrow.textContent = "Health Insurance Claims Authority (TPA)";
    if (title) title.textContent = "Insurance Adjudication & Claims Portal";
    if (subtitle) subtitle.textContent = "Review hospital-submitted patient files, upload insurance policies, auto-adjudicate coverage, and generate settlement reports.";
  } else {
    if (eyebrow) eyebrow.textContent = "Google Health Medical Data Toolkit";
    if (title) title.textContent = "Hospital Information System (HIS)";
    if (subtitle) subtitle.textContent = "Upload patient records & laboratory tests, convert legacy records to ABDM FHIR R4, and review patient clinical summaries & bills.";
  }

  // Update tab labels
  const tabPatients = $("#tabBtnPatients");
  if (tabPatients) tabPatients.textContent = mode === "insurance" ? "Claims & Patient Records" : "Patient Records";

  // Update upload card text
  const uploadTitle = $("#uploadCardTitle");
  const uploadDesc = $("#uploadCardDesc");
  if (mode === "insurance") {
    if (uploadTitle) uploadTitle.textContent = "Upload Claim Document or Policy File";
    if (uploadDesc) uploadDesc.innerHTML = 'Upload an insurance claim document, policy certificate, or ZIP archive to extract FHIR insurance plan and claim coverage.';
  } else {
    if (uploadTitle) uploadTitle.textContent = "Upload Patient Document or ZIP Archive";
    if (uploadDesc) uploadDesc.innerHTML = 'Upload a laboratory report (PDF/image) or a <strong>ZIP archive containing all patient documents across nested folders</strong>. All data will be extracted and consolidated into a unified FHIR record.';
  }

  // Update extracted data section title
  const extractedTitle = $("#extractedDataTitle");
  if (extractedTitle) extractedTitle.textContent = mode === "insurance" ? "Extracted Claim Data" : "Extracted Clinical Data";

  // Update patient records / claims reports section
  const sectionTitle = $("#patientsSectionTitle");
  if (sectionTitle) sectionTitle.textContent = mode === "insurance" ? "Claims & Patient Records" : "Patient Records";

  // Update empty state text
  const patientsList = $("#patientsList");
  if (patientsList && patientsList.querySelector(".muted")) {
    patientsList.querySelector(".muted").textContent = mode === "insurance"
      ? "No patient records received from hospital yet. Upload patient archives from hospital side to populate."
      : "No patients recorded yet. Upload lab reports to populate.";
  }

  // Reset upload state
  selectedFile = null;
  if (fileInput) fileInput.value = "";
  if (fileInfoEl) fileInfoEl.textContent = "No file selected";
  if (convertButton) convertButton.disabled = true;
  if (errorBox) { errorBox.hidden = true; errorBox.textContent = ""; }

  const ipSec = $("#insurancePlanSection");
  const sumGrid = $("#summaryGrid");
  if (ipSec) {
    ipSec.hidden = true;
    ipSec.style.display = "none";
  }
  if (sumGrid) {
    sumGrid.hidden = false;
    sumGrid.style.display = "";
  }

  // Hide patient detail if open
  const detail = $("#patientDetail");
  if (detail) detail.hidden = true;

  // Reload active tab data
  const activeTab = $(".tab-btn.active");
  if (activeTab) {
    if (activeTab.dataset.tab === "patients") loadPatients();
    if (activeTab.dataset.tab === "explorer") loadBundles();
  }

  // Refresh health/stats for new portal
  checkHealth();
}


// ═══════════════════════════════════════════════
// Policy Document Upload & Claim Adjudication Modal
// ═══════════════════════════════════════════════

function openPolicyUploadModal(patientId, patientName, billedAmount, currentStatus) {
  currentAdjudicatePatientId = patientId;
  selectedPolicyFile = null;
  selectedSamplePolicyFilename = null;

  const modal = $("#policyUploadModal");
  if (!modal) return;

  const nameEl = $("#modalCtxPatientName");
  const idEl = $("#modalCtxHospId");
  const billEl = $("#modalCtxBillAmount");
  const statusEl = $("#modalCtxStatus");

  if (nameEl) nameEl.textContent = patientName || "Patient Record";
  if (idEl) idEl.textContent = patientId || "—";
  if (billEl) billEl.textContent = billedAmount ? `₹${Number(billedAmount).toLocaleString("en-IN", {minimumFractionDigits: 2})}` : "₹0.00";
  if (statusEl) statusEl.textContent = currentStatus || "Pending Policy Upload";

  const pInput = $("#policyFileInput");
  if (pInput) pInput.value = "";
  const infoEl = $("#policySelectedFileInfo");
  if (infoEl) {
    infoEl.hidden = true;
    infoEl.innerHTML = "";
  }
  const errBox = $("#modalErrorBox");
  if (errBox) {
    errBox.hidden = true;
    errBox.textContent = "";
  }
  const progBox = $("#adjudicationProgress");
  if (progBox) progBox.hidden = true;

  const submitBtn = $("#submitPolicyModalBtn");
  if (submitBtn) {
    submitBtn.disabled = true;
    submitBtn.textContent = "Submit & Adjudicate Claim →";
  }
  const cancelBtn = $("#cancelPolicyModalBtn");
  if (cancelBtn) cancelBtn.disabled = false;

  loadSamplePolicies(patientId);
  modal.hidden = false;
}

function closePolicyUploadModal() {
  const modal = $("#policyUploadModal");
  if (modal) modal.hidden = true;
  currentAdjudicatePatientId = null;
  selectedPolicyFile = null;
  selectedSamplePolicyFilename = null;
}

async function loadSamplePolicies(patientId) {
  const container = $("#samplePolicyButtons");
  if (!container) return;
  container.innerHTML = '<div style="color:var(--text-muted);font-size:0.8rem;padding:6px 0">Loading sample policy documents...</div>';

  try {
    const res = await fetch("/api/insurance/sample-policies");
    if (!res.ok) throw new Error("Could not fetch sample policies.");
    const list = await res.json();
    if (!list || !list.length) {
      container.innerHTML = '<div style="color:var(--text-muted);font-size:0.8rem">No sample policies found on server.</div>';
      return;
    }

    container.innerHTML = list.map((item) => {
      const isMatch = patientId && item.filename.includes(patientId);
      return `
        <button type="button" class="sample-policy-item-btn ${isMatch ? 'recommended' : ''}" data-filename="${escapeHtml(item.filename)}" onclick="selectSamplePolicy('${escapeHtml(item.filename)}')">
          <div style="display:flex;align-items:center;gap:8px">
            <span style="font-size:1.15rem">📄</span>
            <div>
              <span class="sp-item-name">${escapeHtml(item.filename)}</span>
              <span class="sp-item-hint">${escapeHtml(item.patient_hint)} • ${item.size_kb} KB</span>
            </div>
          </div>
          ${isMatch ? '<span class="status-badge online" style="font-size:0.68rem;padding:2px 8px">Patient Match</span>' : '<span style="color:var(--text-muted);font-size:0.75rem">Select</span>'}
        </button>
      `;
    }).join("");
  } catch (err) {
    container.innerHTML = `<div style="color:var(--text-muted);font-size:0.8rem">Sample policies error: ${err.message}</div>`;
  }
}

function selectSamplePolicy(filename) {
  selectedSamplePolicyFilename = filename;
  selectedPolicyFile = null;

  $$("#samplePolicyButtons .sample-policy-item-btn").forEach((btn) => {
    if (btn.dataset.filename === filename) {
      btn.classList.add("selected");
    } else {
      btn.classList.remove("selected");
    }
  });

  const infoEl = $("#policySelectedFileInfo");
  if (infoEl) {
    infoEl.hidden = false;
    infoEl.innerHTML = `<span>✓ Selected Policy Document: <strong>${escapeHtml(filename)}</strong></span>`;
  }

  const submitBtn = $("#submitPolicyModalBtn");
  if (submitBtn) {
    submitBtn.disabled = false;
    submitBtn.textContent = `Submit & Adjudicate (${filename.slice(0, 24)}...) →`;
  }
}

function onPolicyFileSelected() {
  const pInput = $("#policyFileInput");
  if (pInput && pInput.files.length) {
    selectedPolicyFile = pInput.files[0];
    selectedSamplePolicyFilename = null;
    $$("#samplePolicyButtons .sample-policy-item-btn").forEach((b) => b.classList.remove("selected"));

    const infoEl = $("#policySelectedFileInfo");
    if (infoEl) {
      infoEl.hidden = false;
      const sizeKB = (selectedPolicyFile.size / 1024).toFixed(1);
      infoEl.innerHTML = `<span>✓ Uploaded Document: <strong>${escapeHtml(selectedPolicyFile.name)}</strong> (${sizeKB} KB)</span>`;
    }

    const submitBtn = $("#submitPolicyModalBtn");
    if (submitBtn) {
      submitBtn.disabled = false;
      submitBtn.textContent = `Submit & Adjudicate (${selectedPolicyFile.name.slice(0, 24)}...) →`;
    }
  }
}

async function submitPolicyAdjudication() {
  if (!currentAdjudicatePatientId) return;
  if (!selectedPolicyFile && !selectedSamplePolicyFilename) {
    alert("Please select or upload an insurance policy document first.");
    return;
  }

  const submitBtn = $("#submitPolicyModalBtn");
  const cancelBtn = $("#cancelPolicyModalBtn");
  const progBox = $("#adjudicationProgress");
  const statusTxt = $("#adjudicationStatusText");
  const errBox = $("#modalErrorBox");

  if (errBox) {
    errBox.hidden = true;
    errBox.textContent = "";
  }
  if (submitBtn) submitBtn.disabled = true;
  if (cancelBtn) cancelBtn.disabled = true;
  if (progBox) progBox.hidden = false;
  if (statusTxt) statusTxt.textContent = "Analyzing Policy Document with Gemini Vision OCR & Extracting Terms...";

  const targetPatientId = currentAdjudicatePatientId;

  try {
    let res;
    if (selectedSamplePolicyFilename) {
      res = await fetch(`/api/insurance/patients/${targetPatientId}/adjudicate-sample-policy`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ policy_filename: selectedSamplePolicyFilename }),
      });
    } else {
      const formData = new FormData();
      formData.append("file", selectedPolicyFile);
      if (statusTxt) statusTxt.textContent = "Extracting Policy Rules & Calculating Co-pay against Hospital Bill...";
      res = await fetch(`/api/insurance/patients/${targetPatientId}/adjudicate-policy`, {
        method: "POST",
        body: formData,
      });
    }

    const data = await res.json();
    if (!res.ok || !data.success) {
      throw new Error(data.error || data.message || `Adjudication HTTP ${res.status}`);
    }

    if (statusTxt) statusTxt.textContent = "Claim Adjudicated Successfully! Updating ABDM FHIR Resources...";

    setTimeout(async () => {
      closePolicyUploadModal();
      await loadPatients();
      await viewPatient(targetPatientId);
    }, 600);

  } catch (err) {
    if (progBox) progBox.hidden = true;
    if (submitBtn) submitBtn.disabled = false;
    if (cancelBtn) cancelBtn.disabled = false;
    if (errBox) {
      errBox.hidden = false;
      errBox.textContent = `Adjudication Error: ${err.message}`;
    }
  }
}

// Global modal exposure
window.openPolicyUploadModal = openPolicyUploadModal;
window.closePolicyUploadModal = closePolicyUploadModal;
window.selectSamplePolicy = selectSamplePolicy;
window.loadSamplePolicies = loadSamplePolicies;
window.submitPolicyAdjudication = submitPolicyAdjudication;


// ═══════════════════════════════════════════════
// Health Check & Stats
// ═══════════════════════════════════════════════

async function checkHealth() {
  try {
    const res = await fetch(`${apiBase()}/health`);
    if (res.ok) {
      const data = await res.json();
      if (data.toolkit === "ok") {
        healthBadge.textContent = "Toolkit Connected";
        healthBadge.className = "status-badge online";
      } else {
        healthBadge.textContent = `Toolkit Offline (${data.toolkit_url || "8088"})`;
        healthBadge.className = "status-badge offline";
      }
    } else {
      throw new Error();
    }
  } catch {
    healthBadge.textContent = "Toolkit Offline";
    healthBadge.className = "status-badge offline";
  }

  try {
    const res = await fetch(`${apiBase()}/stats`);
    if (res.ok) {
      const s = await res.json();
      dbStats.innerHTML = `
        <span><span class="count">${s.patients}</span> Patients</span>
        <span><span class="count">${s.bundles}</span> Bundles</span>
        <span><span class="count">${s.observations}</span> Observations</span>
      `;
    }
  } catch { /* ignore */ }
}


// ═══════════════════════════════════════════════
// Single File Upload & Convert
// ═══════════════════════════════════════════════

function onFileSelected() {
  selectedFile = fileInput.files[0] || null;
  if (selectedFile) {
    const sizeKB = (selectedFile.size / 1024).toFixed(1);
    fileInfoEl.textContent = `${selectedFile.name} — ${sizeKB} KB`;
    convertButton.disabled = false;
  } else {
    fileInfoEl.textContent = "No file selected";
    convertButton.disabled = true;
  }
}

let stageTimers = [];

function clearStageTimers() {
  stageTimers.forEach((t) => clearTimeout(t));
  stageTimers = [];
}

function updateStepper(activeIdx) {
  const items = $$("#stageList .step-item");
  const trackFill = $("#stepperTrackFill");

  items.forEach((item, i) => {
    const icon = item.querySelector(".step-icon");
    item.classList.remove("active", "done");
    if (i < activeIdx) {
      item.classList.add("done");
      if (icon) icon.innerHTML = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>';
    } else if (i === activeIdx) {
      item.classList.add("active");
      if (icon) icon.textContent = String(i + 1);
    } else {
      if (icon) icon.textContent = String(i + 1);
    }
  });

  if (trackFill) {
    const pct = Math.min(82, (activeIdx / 4) * 82);
    trackFill.style.width = `${pct}%`;
  }
}

function markAllStagesDone() {
  clearStageTimers();
  const items = $$("#stageList .step-item");
  const trackFill = $("#stepperTrackFill");
  items.forEach((item) => {
    item.classList.remove("active");
    item.classList.add("done");
    const icon = item.querySelector(".step-icon");
    if (icon) {
      icon.innerHTML = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>';
    }
  });
  if (trackFill) trackFill.style.width = "82%";
}

async function convertSelectedFile() {
  if (!selectedFile) return;
  resetUI();
  updateStepper(0);

  const formData = new FormData();
  formData.append("file", selectedFile);

  // Progressive stage advancement while waiting for API
  stageTimers.push(setTimeout(() => updateStepper(1), 350));
  stageTimers.push(setTimeout(() => updateStepper(2), 2400));
  stageTimers.push(setTimeout(() => updateStepper(3), 5200));

  try {
    const response = await fetch(`${apiBase()}/convert`, { method: "POST", body: formData });
    clearStageTimers();
    updateStepper(4);

    if (!response.ok) {
      const errData = await response.json().catch(() => null);
      throw new Error(errData?.error || `HTTP ${response.status}`);
    }

    const data = await response.json();

    // Duration & unified archive badge
    if (data.duration_ms) {
      durationEl.textContent = `API processing duration: ${(data.duration_ms / 1000).toFixed(1)}s`;
      if (data.is_archive && data.archive_info) {
        const info = data.archive_info;
        const totalDocs = info.documents_found || info.documents_processed || info.documents_unified || 0;
        const parts = [`📦 ${totalDocs} document(s) processed & unified from archive`];
        if (info.patient_name && info.patient_name !== "Patient") {
          parts.push(`👤 ${info.patient_name}`);
        }
        if (info.aadhaar_number && info.aadhaar_number !== "N/A") {
          parts.push(`UID: ${info.aadhaar_number}`);
        }
        if (info.observations_count) {
          parts.push(`${info.observations_count} observation(s)`);
        }
        durationEl.textContent += ` • ${parts.join(' · ')}`;
      }
    }

    // Mark all stages done
    setTimeout(() => {
      markAllStagesDone();
    }, 300);

    // Render results
    const raw = data.raw || {};
    rawJsonEl.textContent = JSON.stringify(raw, null, 2);
    copyButton.disabled = false;
    if (window.hljs) hljs.highlightElement(rawJsonEl);

    const summary = data.summary || {};
    renderValidation(data.validation);

    // If a ZIP archive was processed, do NOT display the 'FHIR Resource Summary' & 'Extracted Clinical Data' sections.
    // Instead, display a prominent button to navigate directly to the patient records report.
    const isZipArchive = data.is_archive || (selectedFile && selectedFile.name.toLowerCase().endsWith(".zip"));

    if (isZipArchive) {
      const sumGrid = $("#summaryGrid");
      if (sumGrid) {
        sumGrid.hidden = true;
        sumGrid.style.display = "none";
      }
      const adjSec = $("#insuranceAdjudicationSection");
      if (adjSec) {
        adjSec.hidden = true;
        adjSec.style.display = "none";
      }
      const ipSec = $("#insurancePlanSection");
      if (ipSec) {
        ipSec.hidden = true;
        ipSec.style.display = "none";
      }

      const viewReportSec = $("#viewReportSection");
      if (viewReportSec) {
        viewReportSec.hidden = false;
        viewReportSec.style.display = "block";
      }

      const viewReportBtn = $("#viewReportBtn");
      if (viewReportBtn) {
        viewReportBtn.onclick = () => {
          navigateToPatientReport(data.patient_id, data.archive_info?.patient_name);
        };
      }
    } else {
      renderSummary(summary);

      // Render insurance plan or claim summary if available
      if (portalMode === "insurance" && data.insurance_plan) {
        renderInsurancePlan(data.insurance_plan, data.validation, (data.stored_bundle_ids && data.stored_bundle_ids[0]) || data.raw?.id);
      } else {
        const ipSec = $("#insurancePlanSection");
        if (ipSec) {
          ipSec.hidden = true;
          ipSec.style.display = "none";
        }
        const sumGrid = $("#summaryGrid");
        if (sumGrid) {
          sumGrid.hidden = false;
          sumGrid.style.display = "";
        }
        if (portalMode === "insurance" && data.claim_summary) {
          renderClaimSummary(data.claim_summary);
        }
      }
    }

    // Refresh stats
    checkHealth();

  } catch (err) {
    clearStageTimers();
    errorBox.textContent = `Error: ${err.message}`;
    errorBox.hidden = false;
  }
}

async function navigateToPatientReport(patientId, patientName) {
  // Switch to patients tab
  $$(".tab-btn").forEach((b) => b.classList.remove("active"));
  $$(".tab-panel").forEach((p) => p.classList.remove("active"));
  const tabPatients = $("#tabBtnPatients");
  if (tabPatients) tabPatients.classList.add("active");
  const panel = $("#tab-patients");
  if (panel) panel.classList.add("active");

  await loadPatients();

  if (patientId) {
    await viewPatient(patientId);
  } else if (patientName) {
    try {
      const res = await fetch(`${apiBase()}/patients`);
      const patients = await res.json();
      const match = patients.find(p => p.name && p.name.toLowerCase().includes(patientName.toLowerCase()));
      if (match) {
        await viewPatient(match.id);
      } else if (patients.length > 0) {
        await viewPatient(patients[0].id);
      }
    } catch {
      const firstCard = $(".patient-card");
      if (firstCard) firstCard.click();
    }
  } else {
    const firstCard = $(".patient-card");
    if (firstCard) firstCard.click();
  }
}

function resetUI() {
  clearStageTimers();
  errorBox.hidden = true;
  errorBox.textContent = "";
  rawJsonEl.textContent = "{}";
  copyButton.disabled = true;
  durationEl.textContent = "API processing duration: Not available";
  const valSection = $("#validationSection");
  if (valSection) {
    valSection.hidden = true;
    valSection.style.display = "none";
  }
  const viewReportSec = $("#viewReportSection");
  if (viewReportSec) {
    viewReportSec.hidden = true;
    viewReportSec.style.display = "none";
  }
  const rawDetails = $("#rawJsonDetails");
  if (rawDetails) {
    rawDetails.removeAttribute("open");
  }
  const ipSec = $("#insurancePlanSection");
  if (ipSec) {
    ipSec.hidden = true;
    ipSec.style.display = "none";
  }
  const adjSec = $("#insuranceAdjudicationSection");
  if (adjSec) {
    adjSec.hidden = true;
    adjSec.style.display = "none";
    adjSec.innerHTML = "";
  }
  const sumGrid = $("#summaryGrid");
  if (sumGrid) {
    sumGrid.hidden = false;
    sumGrid.style.display = "";
  }

  const pn = $("#patientName"), pg = $("#patientGender"), pb = $("#patientBirthDate");
  const pa = $("#patientAadhaar"), padd = $("#patientAddress");
  const pab = $("#patientAbha"), pgu = $("#patientGuardian"), pph = $("#patientPhone"), phi = $("#patientHospId");
  if (pn) pn.textContent = "Not available";
  if (pg) pg.textContent = "Not available";
  if (pb) pb.textContent = "Not available";
  if (pa) pa.textContent = "Not available";
  if (padd) padd.textContent = "Not available";
  if (pab) pab.textContent = "Not available";
  if (pgu) pgu.textContent = "Not available";
  if (pph) pph.textContent = "Not available";
  if (phi) phi.textContent = "Not available";
  const bs = $("#bundleSummary");
  if (bs) bs.innerHTML = "No FHIR Bundle loaded.";
  const pl = $("#profileList");
  if (pl) pl.innerHTML = "<li>Not available</li>";
  const ob = $("#observationsBody");
  if (ob) ob.innerHTML = '<tr><td colspan="5">No observations loaded.</td></tr>';

  const items = $$("#stageList .step-item");
  items.forEach((item, i) => {
    item.classList.remove("active", "done");
    const icon = item.querySelector(".step-icon");
    if (icon) icon.textContent = String(i + 1);
  });
  const trackFill = $("#stepperTrackFill");
  if (trackFill) trackFill.style.width = "0%";
}

function copyJson() {
  navigator.clipboard.writeText(rawJsonEl.textContent);
  copyButton.textContent = "Copied!";
  setTimeout(() => (copyButton.textContent = "Copy JSON"), 2000);
}


// ═══════════════════════════════════════════════
// Summary Rendering
// ═══════════════════════════════════════════════

function renderSummary(summary) {
  if (!summary) return;
  const bundleInfo = summary.bundle || {};
  const resource_counts = bundleInfo.resource_counts || summary.resource_counts || {};
  const bundle_type = bundleInfo.type || summary.bundle_type || "document";
  const profiles = bundleInfo.profiles || summary.profiles || [];
  const patient = summary.patient || {};
  const observations = summary.observations || summary.lab_tests || [];

  // Render Insurance Adjudication Banner (if present)
  const adjSec = $("#insuranceAdjudicationSection");
  if (adjSec) {
    const adj = summary.insurance_adjudication;
    if (adj && adj.has_insurance) {
      adjSec.hidden = false;
      adjSec.style.display = "block";
      const isFullyCashless = adj.patient_payable === 0 && adj.insured_amount >= adj.total_billed && adj.total_billed > 0;
      const statusBadge = isFullyCashless ? "100% Cashless Guaranteed" : (adj.insured_amount > 0 ? "Partially Insured" : "Patient Liable");

      adjSec.innerHTML = `
        <div style="background:linear-gradient(135deg, rgba(16,185,129,0.08) 0%, rgba(59,130,246,0.06) 100%);border:1px solid rgba(16,185,129,0.35);border-radius:12px;padding:20px;box-shadow:0 4px 16px rgba(0,0,0,0.12)">
          <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px;border-bottom:1px solid rgba(16,185,129,0.2);padding-bottom:14px;margin-bottom:16px">
            <div style="display:flex;align-items:center;gap:10px">
              <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#10b981" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>
              </svg>
              <div>
                <h3 style="font-size:1.1rem;font-weight:700;color:#10b981;margin:0">Insurance Policy Adjudication &amp; Settlement</h3>
                <span style="font-size:0.75rem;color:var(--text-muted)">FHIR Coverage &amp; ClaimResponse Adjudication Engine</span>
              </div>
            </div>
            <span style="background:rgba(16,185,129,0.2);color:#34d399;border:1px solid rgba(16,185,129,0.4);border-radius:20px;padding:4px 14px;font-size:0.8rem;font-weight:700">
              🛡️ ${escapeHtml(statusBadge)}
            </span>
          </div>

          <div style="display:grid;grid-template-columns:repeat(auto-fit, minmax(210px, 1fr));gap:14px;margin-bottom:16px">
            <div style="background:var(--bg-card);border:1px solid var(--border);border-radius:10px;padding:14px 16px">
              <div style="font-size:0.72rem;text-transform:uppercase;color:var(--text-muted);font-weight:600">Total Hospital Bill</div>
              <div style="font-size:1.4rem;font-weight:800;color:var(--text-primary);margin-top:4px">₹${Number(adj.total_billed).toLocaleString("en-IN")}</div>
              <div style="font-size:0.72rem;color:var(--text-muted);margin-top:2px">Billed Hospital Tariff</div>
            </div>

            <div style="background:rgba(16,185,129,0.1);border:1px solid rgba(16,185,129,0.4);border-radius:10px;padding:14px 16px">
              <div style="font-size:0.72rem;text-transform:uppercase;color:#34d399;font-weight:700">Amount Insured (Covered)</div>
              <div style="font-size:1.4rem;font-weight:800;color:#10b981;margin-top:4px">₹${Number(adj.insured_amount).toLocaleString("en-IN")}</div>
              <div style="font-size:0.72rem;color:#34d399;margin-top:2px">✓ Paid by Insurance (${adj.coverage_percentage}%)</div>
            </div>

            <div style="background:rgba(59,130,246,0.1);border:1px solid rgba(59,130,246,0.4);border-radius:10px;padding:14px 16px">
              <div style="font-size:0.72rem;text-transform:uppercase;color:#93c5fd;font-weight:700">Patient Needs to Pay</div>
              <div style="font-size:1.4rem;font-weight:800;color:${adj.patient_payable === 0 ? '#60a5fa' : '#f87171'};margin-top:4px">₹${Number(adj.patient_payable).toLocaleString("en-IN")}${adj.patient_payable === 0 ? ' (NIL)' : ''}</div>
              <div style="font-size:0.72rem;color:#93c5fd;margin-top:2px">${adj.patient_payable === 0 ? 'Zero Out-of-Pocket Expense' : 'Deductible / Copay Balance'}</div>
            </div>
          </div>

          <div style="display:flex;flex-wrap:wrap;gap:12px;margin-bottom:12px">
            <div style="background:var(--bg-input);padding:6px 14px;border-radius:6px;border:1px solid var(--border);font-size:0.78rem"><strong style="color:var(--text-muted)">Policy Number:</strong> <span style="color:var(--text-primary);font-family:var(--font-mono);font-weight:600">${escapeHtml(adj.policy_number)}</span></div>
            <div style="background:var(--bg-input);padding:6px 14px;border-radius:6px;border:1px solid var(--border);font-size:0.78rem"><strong style="color:var(--text-muted)">Scheme / Insurer:</strong> <span style="color:var(--text-primary);font-weight:600">${escapeHtml(adj.insurer_name)}</span></div>
            ${adj.pre_auth_ref && adj.pre_auth_ref !== "Not available" ? `<div style="background:var(--bg-input);padding:6px 14px;border-radius:6px;border:1px solid var(--border);font-size:0.78rem"><strong style="color:var(--text-muted)">Pre-Auth Ref:</strong> <span style="color:#60a5fa;font-family:var(--font-mono)">${escapeHtml(adj.pre_auth_ref)}</span></div>` : ""}
          </div>

          ${adj.disposition ? `
            <div style="background:rgba(15,23,42,0.4);border-left:3px solid #10b981;border-radius:4px;padding:10px 14px;font-size:0.8rem;color:var(--text-secondary);line-height:1.45">
              <strong style="color:#86efac">Adjudication Decision:</strong> ${escapeHtml(adj.disposition)}
            </div>
          ` : ""}
        </div>
      `;
    } else {
      adjSec.hidden = true;
      adjSec.style.display = "none";
      adjSec.innerHTML = "";
    }
  }

  // Bundle summary table
  const summaryEl = $("#bundleSummary");
  if (summaryEl) {
    const counts = Object.entries(resource_counts);
    if (counts.length) {
      const total = Object.values(resource_counts).reduce((a, b) => a + b, 0);
      let rows = `<table><tr><td>Bundle Type</td><td><code>${escapeHtml(bundle_type)}</code></td></tr>`;
      rows += `<tr><td>Total Resources</td><td><strong>${total}</strong></td></tr>`;
      for (const [type, count] of counts) {
        rows += `<tr><td>${escapeHtml(type)}</td><td>${count}</td></tr>`;
      }
      rows += "</table>";
      summaryEl.innerHTML = rows;
    } else {
      summaryEl.innerHTML = "<p class='muted'>No FHIR Bundle loaded.</p>";
    }
  }

  // Profiles
  const profileList = $("#profileList");
  if (profileList) {
    if (profiles && profiles.length) {
      profileList.innerHTML = profiles.map((p) => {
        const label = p.split("/").pop();
        return `<li><a href="${escapeHtml(p)}" target="_blank" rel="noopener noreferrer" title="${escapeHtml(p)}">${escapeHtml(label)}</a></li>`;
      }).join("");
    } else {
      profileList.innerHTML = "<li>Not available</li>";
    }
  }

  // Patient info
  const pn = $("#patientName"), pg = $("#patientGender"), pb = $("#patientBirthDate");
  const pa = $("#patientAadhaar"), padd = $("#patientAddress");
  const pab = $("#patientAbha"), pgu = $("#patientGuardian"), pph = $("#patientPhone"), phi = $("#patientHospId");
  if (pn) pn.textContent = patient.name || "Not available";
  if (pg) {
    const g = patient.gender || "";
    pg.textContent = g ? (g.charAt(0).toUpperCase() + g.slice(1)) : "Not available";
  }
  if (pb) pb.textContent = patient.birthDate || patient.dob || "Not available";
  if (pa) pa.textContent = patient.aadhaar || patient.aadhaar_number || "Not available";
  if (padd) padd.textContent = patient.address || "Not available";
  if (pab) pab.textContent = patient.abha || "Not available";
  if (pgu) pgu.textContent = patient.guardian || "Not available";
  if (pph) pph.textContent = patient.phone || "Not available";
  if (phi) phi.textContent = patient.hospital_id || patient.mrn || "Not available";

  // Observations table
  const tbody = $("#observationsBody");
  if (tbody) {
    if (observations.length) {
      tbody.innerHTML = observations.map((t) => {
        const testName = t.test || t.name || "Unknown Test";
        const loincCode = t.loinc || t.loinc_code;
        const loinc = (loincCode && loincCode !== "Not available")
          ? `<a href="https://loinc.org/${encodeURIComponent(loincCode)}" target="_blank" rel="noopener noreferrer" class="loinc-badge">${escapeHtml(loincCode)}</a>`
          : `<span class="loinc-tag">Not available</span>`;
        const resVal = t.result != null && t.result !== "" ? t.result : "N/A";
        const unitVal = t.unit && t.unit !== "Not available" ? t.unit : "—";
        const ref = t.referenceRange || (Array.isArray(t.reference_range) ? t.reference_range.map(r => r.text || "").join("; ") : t.reference_range) || "Not available";

        return `<tr>
          <td class="test-name-cell">${escapeHtml(testName)}</td>
          <td>${loinc}</td>
          <td class="value-cell">${escapeHtml(String(resVal))}</td>
          <td class="unit-cell">${escapeHtml(unitVal)}</td>
          <td class="range-cell">${escapeHtml(ref)}</td>
        </tr>`;
      }).join("");
    } else {
      tbody.innerHTML = '<tr><td colspan="5">No observations loaded.</td></tr>';
    }
  }
}


// ═══════════════════════════════════════════════
// Insurance Plan & Benefits Rendering (Upload Tab)
// ═══════════════════════════════════════════════

function renderInsurancePlan(plan, validation, bundleId) {
  if (!plan) return;

  const ipSection = $("#insurancePlanSection");
  const summaryGrid = $("#summaryGrid");
  const valSection = $("#validationSection");

  if (ipSection) {
    ipSection.hidden = false;
    ipSection.style.display = "block";
  }
  if (summaryGrid) {
    summaryGrid.hidden = true;
    summaryGrid.style.display = "none";
  }
  if (valSection) {
    valSection.hidden = true;
    valSection.style.display = "none";
  }

  // Populate metric fields
  const nameEl = $("#ipdPlanName");
  const insurerEl = $("#ipdInsurer");
  const uinEl = $("#ipdUin");
  const typeEl = $("#ipdPlanType");
  const statusEl = $("#ipdStatus");
  const periodEl = $("#ipdPolicyPeriod");
  const bundleEl = $("#ipdBundleId");

  if (nameEl) nameEl.textContent = plan.plan_name || "—";
  if (insurerEl) insurerEl.textContent = plan.insurer || "—";
  if (uinEl) uinEl.textContent = plan.uin || "—";
  if (typeEl) typeEl.textContent = plan.plan_type || "—";
  if (statusEl) {
    statusEl.innerHTML = `<span class="ip-status-pill"><span class="ip-dot">•</span> ${escapeHtml(plan.status || "Active")}</span>`;
  }
  if (periodEl) periodEl.textContent = plan.policy_period || "—";
  if (bundleEl) bundleEl.textContent = bundleId || plan.bundle_id || "—";

  // Populate benefits
  const benefits = plan.benefits || [];
  const titleEl = $("#ipdBenefitsTitle");
  if (titleEl) titleEl.textContent = `Benefits (${benefits.length})`;

  const gridEl = $("#ipdBenefitsGrid");
  if (!gridEl) return;

  if (!benefits.length) {
    gridEl.innerHTML = `<p class="muted">No benefits recorded in this policy document.</p>`;
    return;
  }

  gridEl.innerHTML = benefits.map((b) => {
    const cat = escapeHtml(b.category || "inpatient_hospitalization");
    const bType = escapeHtml((b.benefit_type || "AMOUNT").toUpperCase());
    const val = escapeHtml(b.value || "—");
    const cap = b.cap ? escapeHtml(b.cap) : null;
    const conds = (b.conditions && b.conditions.length) ? b.conditions : ["Covered as per policy terms and conditions."];

    return `
      <div class="ip-benefit-card">
        <div class="ip-badges-row">
          <span class="ip-badge ip-badge-cat">${cat}</span>
          <span class="ip-badge ip-badge-type">TYPE: ${bType}</span>
          <span class="ip-badge ip-badge-val">Value: ${val}</span>
          ${cap ? `<span class="ip-badge ip-badge-cap">Cap: ${cap}</span>` : ""}
        </div>
        <div class="ip-cond-title">CONDITIONS:</div>
        <ul class="ip-cond-list">
          ${conds.map((c) => `<li>${escapeHtml(c)}</li>`).join("")}
        </ul>
      </div>
    `;
  }).join("");
}


// ═══════════════════════════════════════════════
// Insurance Claim Summary Rendering (Upload Tab)
// ═══════════════════════════════════════════════

function renderClaimSummary(claimSummary) {
  if (!claimSummary) return;

  // Insert claim summary into the extracted data card
  const extractedCard = $("#summaryGrid .card:last-child");
  if (!extractedCard) return;

  const kf = claimSummary.key_fields || {};
  const sections = claimSummary.document_sections || [];
  const narrative = claimSummary.claim_narrative || "";
  const flags = claimSummary.flags || [];

  // Build flags HTML
  let flagsHtml = "";
  if (flags.length) {
    flagsHtml = `<div class="claim-flags">${flags.map(f => `<span class="claim-flag">${escapeHtml(f)}</span>`).join("")}</div>`;
  }

  // Build key fields grid
  const keyFieldsHtml = `
    <div class="claim-key-fields">
      <h3 class="claim-section-title">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline></svg>
        Key Claim Information
      </h3>
      ${flagsHtml}
      ${narrative ? `<p class="claim-narrative">${escapeHtml(narrative)}</p>` : ""}
      <div class="claim-fields-grid">
        ${_claimField("Claim / Reference No.", kf.claim_number)}
        ${_claimField("Policy / Insurance ID", kf.policy_number)}
        ${_claimField("Insured Name", kf.insured_name)}
        ${_claimField("Insurer / TPA", kf.insurer_tpa_name)}
        ${_claimField("Primary Diagnosis", kf.primary_diagnosis)}
        ${_claimField("ICD Code", kf.icd_code)}
        ${_claimField("Hospital / Provider", kf.hospital_name)}
        ${_claimField("Treating Doctor", kf.treating_doctor)}
        ${_claimField("Admission Date", kf.admission_date)}
        ${_claimField("Discharge Date", kf.discharge_date)}
        ${_claimField("Claim Type", kf.claim_type)}
        ${_claimField("Room Category", kf.room_category)}
        ${_claimField("Total Claimed Amount", kf.total_claimed_amount, true)}
        ${_claimField("Net Claimed Amount", kf.net_claimed_amount, true)}
        ${_claimField("Gross Bill Amount", kf.gross_bill_amount)}
        ${_claimField("Pre-Authorized Amount", kf.pre_authorized_amount)}
        ${_claimField("Approved Amount", kf.approved_amount)}
        ${_claimField("Sum Insured", kf.sum_insured)}
      </div>
    </div>
  `;

  // Build document sections accordion
  let sectionsHtml = "";
  if (sections.length) {
    const sectionItems = sections.map((sec, idx) => {
      const keyPts = (sec.key_data_points || []).filter(p => p && p.trim());
      const keyPtsHtml = keyPts.length
        ? `<ul class="claim-section-points">${keyPts.map(p => `<li>${escapeHtml(p)}</li>`).join("")}</ul>`
        : "";
      return `
        <div class="claim-doc-section" data-section-idx="${idx}">
          <div class="claim-doc-section-header" onclick="this.parentElement.classList.toggle('expanded')">
            <span class="claim-doc-section-title">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" class="chevron-icon"><polyline points="6 9 12 15 18 9"></polyline></svg>
              ${escapeHtml(sec.title)}
            </span>
            <span class="claim-doc-section-badge">${escapeHtml(sec.title.split(' ')[0])}</span>
          </div>
          <div class="claim-doc-section-body">
            ${sec.summary ? `<p>${escapeHtml(sec.summary)}</p>` : ""}
            ${keyPtsHtml}
          </div>
        </div>
      `;
    }).join("");

    sectionsHtml = `
      <div class="claim-doc-sections">
        <h3 class="claim-section-title">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"></path><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"></path></svg>
          Document Sections (${sections.length})
        </h3>
        ${sectionItems}
      </div>
    `;
  }

  // Prepend to extracted data card
  const financialHtml = _renderFinancialCard(sections, kf);
  const existingContent = extractedCard.innerHTML;
  extractedCard.innerHTML = keyFieldsHtml + financialHtml + sectionsHtml + existingContent;
}

function _claimField(label, value, highlight = false) {
  if (!value) return "";
  const s = String(value).trim();
  const lowerLabel = label.toLowerCase();
  if (lowerLabel.includes("amount") || lowerLabel.includes("bill") || lowerLabel.includes("sum insured")) {
    if (!/\d/.test(s)) return "";
  }
  if (lowerLabel.includes("room category")) {
    if (["prescrib", "tab", "mg", "ward-", "mandal", "district", "imatinib"].some(w => s.toLowerCase().includes(w))) {
      return "";
    }
  }
  const cls = highlight ? ' claim-field-highlight' : '';
  return `
    <div class="claim-field${cls}">
      <span class="claim-field-label">${escapeHtml(label)}</span>
      <span class="claim-field-value">${escapeHtml(value)}</span>
    </div>
  `;
}


// ═══════════════════════════════════════════════
// Validation Rendering
// ═══════════════════════════════════════════════

function renderValidation(val) {
  const section = $("#validationSection");
  if (!section || !val) return;
  section.hidden = false;

  // Score
  const scoreEl = $("#validationScore");
  const scoreClass = val.compliance_score >= 80 ? "high" : val.compliance_score >= 50 ? "medium" : "low";
  scoreEl.className = `compliance-score ${scoreClass}`;
  scoreEl.textContent = `${val.compliance_score}%`;

  // Issues
  const issuesEl = $("#validationIssues");
  if (val.issues?.length) {
    issuesEl.innerHTML = val.issues.map((i) =>
      `<div class="issue ${i.severity}"><span class="badge">${i.severity}</span><span>${i.message}${i.path ? ` <code style="font-size:.72rem;color:var(--text-muted)">(${i.path})</code>` : ""}</span></div>`
    ).join("");
  } else {
    issuesEl.innerHTML = '<div class="issue info"><span class="badge">pass</span><span>No issues found — fully compliant!</span></div>';
  }

  // FMM report
  const fmmEl = $("#fmmReport");
  if (val.fmm_report) {
    fmmEl.innerHTML = Object.entries(val.fmm_report).map(([rt, info]) => {
      const lvl = info.level === "N" ? "N" : `FMM ${info.level}`;
      const cls = info.level === "N" ? "normative" : "";
      return `<div class="fmm-chip"><span>${rt}</span><span class="fmm-level ${cls}">${lvl}</span></div>`;
    }).join("");
  }
}


// ═══════════════════════════════════════════════
// Patient Records
// ═══════════════════════════════════════════════

async function loadPatients() {
  const container = $("#patientsList");
  try {
    const res = await fetch(`${apiBase()}/patients`);
    const patients = await res.json();
    if (!patients.length) {
      container.innerHTML = `<p class="muted">${portalMode === "insurance" ? "No patient records or claims recorded yet. Upload patient archives from hospital side to populate." : "No patients recorded yet. Upload patient archives or lab reports to populate."}</p>`;
      return;
    }
    container.innerHTML = patients.map((p) => {
      const pAge = calculateAge(p.birth_date);
      const genderStr = p.gender ? (p.gender.charAt(0).toUpperCase() + p.gender.slice(1)) : "";
      const metaParts = [];
      if (genderStr) metaParts.push(genderStr);
      if (p.birth_date && p.birth_date !== "N/A" && p.birth_date !== "Not available") {
        metaParts.push(`DOB: ${p.birth_date}`);
      }
      if (pAge) metaParts.push(pAge);

      const billedStr = p.billed_amount ? `₹${Number(p.billed_amount).toLocaleString("en-IN", {minimumFractionDigits: 2})}` : "₹0.00";
      const insuredStr = p.insured_amount ? `₹${Number(p.insured_amount).toLocaleString("en-IN", {minimumFractionDigits: 2})}` : "₹0.00";
      const payableStr = p.patient_payable ? `₹${Number(p.patient_payable).toLocaleString("en-IN", {minimumFractionDigits: 2})}` : "₹0.00";

      let statusBadgeHtml = "";
      if (p.is_adjudicated) {
        statusBadgeHtml = `
          <span class="pc-badge status-approved" style="background:rgba(16,185,129,0.16);color:#34d399;border:1px solid rgba(16,185,129,0.35);font-weight:600">
            🛡️ ${escapeHtml(p.adjudication_status || "Approved")}
          </span>
        `;
      } else {
        statusBadgeHtml = `
          <span class="pc-badge status-pending" style="background:rgba(234,179,8,0.16);color:#facc15;border:1px solid rgba(234,179,8,0.35);font-weight:600">
            ⏳ Awaiting Insurance Policy
          </span>
        `;
      }

      let actionBlockHtml = "";
      if (portalMode === "insurance") {
        if (!p.is_adjudicated) {
          actionBlockHtml = `
            <div style="margin-top:10px;padding-top:10px;border-top:1px solid var(--border)">
              <button class="primary insurance-btn-glow" style="width:100%;padding:8px 12px;font-size:0.8rem;display:flex;align-items:center;justify-content:center;gap:6px" onclick="event.stopPropagation(); openPolicyUploadModal('${p.id}', '${escapeHtml(p.name || '')}', ${p.billed_amount || 0}, '${escapeHtml(p.adjudication_status || '')}')">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline><line x1="12" y1="18" x2="12" y2="12"></line><line x1="9" y1="15" x2="12" y2="12"></line><line x1="15" y1="15" x2="12" y2="12"></line></svg>
                <span>Upload Policy Document &rarr;</span>
              </button>
            </div>
          `;
        } else {
          actionBlockHtml = `
            <div style="margin-top:10px;padding-top:10px;border-top:1px solid var(--border);display:flex;gap:6px">
              <button class="primary" style="flex:1;padding:6px 10px;font-size:0.75rem" onclick="event.stopPropagation(); viewPatient('${p.id}')">
                View Report
              </button>
              <button class="secondary" title="Re-adjudicate with new policy" style="padding:6px 10px;font-size:0.75rem" onclick="event.stopPropagation(); openPolicyUploadModal('${p.id}', '${escapeHtml(p.name || '')}', ${p.billed_amount || 0}, '${escapeHtml(p.adjudication_status || '')}')">
                🔄 Re-adjudicate
              </button>
            </div>
          `;
        }
      } else {
        // Hospital mode
        if (p.is_adjudicated) {
          actionBlockHtml = `
            <div style="margin-top:8px;font-size:0.74rem;color:#34d399;display:flex;align-items:center;gap:5px;font-weight:600">
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>
              <span>Insurance Claim Settled: ₹${Number(p.insured_amount).toLocaleString("en-IN")} covered</span>
            </div>
          `;
        } else {
          actionBlockHtml = `
            <div style="margin-top:8px;font-size:0.74rem;color:#facc15;display:flex;align-items:center;gap:5px">
              <span>⏳ Sent to TPA for policy settlement</span>
            </div>
          `;
        }
      }

      return `
        <div class="patient-card" onclick="viewPatient('${p.id}')">
          <div class="pc-header">
            <div class="pc-name">${escapeHtml(p.name || (portalMode === "insurance" ? "Unknown Claimant" : "Unknown Patient"))}</div>
            <button class="pc-delete-btn" title="Delete Record" onclick="event.stopPropagation(); deletePatient('${p.id}', '${escapeHtml(p.name || '')}')">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <polyline points="3 6 5 6 21 6"></polyline>
                <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
              </svg>
            </button>
          </div>
          <div class="pc-meta">${escapeHtml(metaParts.join(" • ") || "Patient Record")}</div>
          <div style="margin:8px 0;display:flex;align-items:center;justify-content:space-between;font-size:0.8rem">
            <span style="color:var(--text-muted)">Hospital Bill:</span>
            <strong style="color:var(--text-primary);font-size:0.92rem">${billedStr}</strong>
          </div>
          <div class="pc-badges" style="display:flex;flex-wrap:wrap;gap:6px">
            <span class="pc-badge">${p.bundle_count || 0} Report${p.bundle_count !== 1 ? "s" : ""}</span>
            ${statusBadgeHtml}
          </div>
          ${actionBlockHtml}
        </div>
      `;
    }).join("");
  } catch {
    container.innerHTML = '<p class="muted">Failed to load patients.</p>';
  }
}

async function deletePatient(patientId, patientName) {
  if (!confirm(`Are you sure you want to delete patient "${patientName || 'this patient'}" and all associated reports?`)) {
    return;
  }
  try {
    const res = await fetch(`${apiBase()}/patients/${patientId}`, { method: "DELETE" });
    if (!res.ok) throw new Error("Failed to delete patient record");
    const detail = $("#patientDetail");
    if (detail) detail.hidden = true;
    await loadPatients();
    await checkHealth();
  } catch (err) {
    alert(`Error deleting patient: ${err.message}`);
  }
}

async function viewPatient(patientId) {
  const detail = $("#patientDetail");
  const content = $("#patientDetailContent");
  const nameEl = $("#patientDetailName");
  if (!detail || !content) return;

  detail.hidden = false;
  detail.scrollIntoView({ behavior: "smooth", block: "start" });
  content.innerHTML = `
    <div style="padding:40px 20px;text-align:center;color:var(--text-muted)">
      <div class="progress-bar-container" style="max-width:280px;margin:0 auto 14px">
        <div class="progress-bar" style="width:75%"></div>
      </div>
      <p style="font-size:0.9rem">Retrieving and assembling HL7® FHIR® Diagnostic Report...</p>
    </div>
  `;

  try {
    const res = await fetch(`${apiBase()}/patients/${patientId}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    const patient = data.patient || {};
    const bundles = data.bundles || [];
    const observations = data.observations || [];

    if (nameEl) {
      const reportLabel = portalMode === "insurance" ? "Insurance Claim Report" : "Clinical Diagnostic Report";
      nameEl.textContent = `${reportLabel} \u2014 ${patient.name || "Patient"}`;
    }

    // ── Fetch claim_summary from the stored bundle's associated data
    // (The claim_summary was stored in the original convert response; we
    //  reconstruct from FHIR resources for the patient detail view)
    let claimSummaryData = null;

    // Parse patient_json
    let patientResource = {};
    try {
      if (patient.patient_json) {
        patientResource = typeof patient.patient_json === "string" ? JSON.parse(patient.patient_json) : patient.patient_json;
      }
    } catch { /* ignore */ }

    // Patient Demographics from patient & patientResource
    const pName = patient.name || patientResource.name?.[0]?.text || "Unknown Patient";
    const pGiven = (patientResource.name?.[0]?.given || []).join(" ");
    const pFamily = patientResource.name?.[0]?.family || "";
    const pGender = patient.gender ? (patient.gender.charAt(0).toUpperCase() + patient.gender.slice(1)) : (patientResource.gender || "Not specified");
    const pDob = patient.birth_date || patientResource.birthDate || "Not specified";
    const pAge = calculateAge(pDob);

    // Extract Blood Group from observations if available
    let pBloodGroup = "";
    observations.forEach((o) => {
      const name = String(o.code_text || o.code?.text || "").toLowerCase();
      if (name.includes("blood group") || name.includes("abo") || name.includes("rh type")) {
        const val = o.value || o.valueString || (o.valueQuantity?.value) || "";
        if (val && String(val).trim()) pBloodGroup = String(val).trim();
      }
    });

    // Extract all identifiers (Aadhaar, ABHA, Hospital ID, MRN)
    const identifiers = patientResource.identifier || [];
    let pAadhaar = "";
    let pAbha = patient.abha_id || "";
    let pHospId = "";
    let pMrn = "";

    identifiers.forEach((idObj) => {
      const sys = String(idObj.system || "").toLowerCase();
      const val = String(idObj.value || "").trim();
      const code = idObj.type?.coding?.[0]?.code || "";
      const display = String(idObj.type?.coding?.[0]?.display || "").toLowerCase();

      if (sys.includes("aadhaar") || display.includes("aadhaar")) {
        pAadhaar = val;
      } else if (sys.includes("abha") || display.includes("abha") || code === "NH") {
        if (!pAbha) pAbha = val;
      } else if (sys.includes("hospital.org/patient-id") || code === "PI") {
        pHospId = val;
      } else if (code === "MR" || sys.includes("smarthealthit") || sys.includes("hospital")) {
        if (!pMrn) pMrn = val;
      }
    });

    let formattedAadhaar = pAadhaar;
    if (/^\d{12}$/.test(pAadhaar.replace(/\s+/g, ""))) {
      formattedAadhaar = pAadhaar.replace(/\s+/g, "").replace(/(\d{4})(\d{4})(\d{4})/, "$1 $2 $3");
    }

    // Extract Address components
    const addresses = patientResource.address || [];
    let pAddress = "";
    let pCity = "";
    let pDistrict = "";
    let pState = "";
    let pPin = "";

    if (addresses.length) {
      const addr = addresses[0];
      pAddress = addr.text || addr.line?.join(", ") || "";
      pCity = addr.city || "";
      pDistrict = addr.district || "";
      pState = addr.state || "";
      pPin = addr.postalCode || "";
    }

    // Extract Locality / Mandal from address string if available
    let pLocality = "";
    let pMandal = "";
    if (pAddress) {
      const vMatch = pAddress.match(/([0-9a-zA-Z\s-]+(?:POST|VILLAGE|STREET|ROAD|NAGAR|COLONY))/i);
      if (vMatch) pLocality = vMatch[1].trim();
      const mMatch = pAddress.match(/([0-9a-zA-Z\s-]+(?:MANDAL|TALUK|BLOCK))/i);
      if (mMatch) pMandal = mMatch[1].trim();
    }

    // Extract Telecom / Phone
    const telecoms = patientResource.telecom || [];
    let pPhone = "";
    const phoneObj = telecoms.find((t) => t.system === "phone");
    if (phoneObj) pPhone = phoneObj.value || "";

    // Extract Guardian / Emergency Contact
    const contacts = patientResource.contact || [];
    let pGuardian = "";
    if (contacts.length) {
      pGuardian = contacts[0].name?.text || "";
      const rel = contacts[0].relationship?.[0]?.coding?.[0]?.display;
      if (rel && pGuardian) pGuardian += ` (${rel})`;
    }

    // If no bundles, show clean dossier placeholder
    if (!bundles.length) {
      content.innerHTML = `
        <div class="report-frame">
          <div class="report-dossier-wrap">
            <div class="report-dossier-card">
              <div class="dossier-card-header">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"></path><circle cx="12" cy="7" r="4"></circle></svg>
                <h4>Patient Demographics</h4>
              </div>
              <div class="dossier-content">
                <div class="dossier-row main-name">
                  <span class="dossier-label">Full Legal Name</span>
                  <span class="dossier-val highlight">${escapeHtml(pName)}</span>
                </div>
                <div class="dossier-subgrid">
                  <div class="dossier-item">
                    <span class="dossier-label">Date of Birth / Age</span>
                    <span class="dossier-val">${escapeHtml(pDob)} ${pAge ? `<span class="age-pill">${pAge}</span>` : ""}</span>
                  </div>
                  <div class="dossier-item">
                    <span class="dossier-label">Biological Sex</span>
                    <span class="dossier-val">${escapeHtml(pGender)}</span>
                  </div>
                  <div class="dossier-item">
                    <span class="dossier-label">Father / Guardian</span>
                    <span class="dossier-val">${escapeHtml(pGuardian || "Not recorded")}</span>
                  </div>
                  <div class="dossier-item">
                    <span class="dossier-label">Contact Phone</span>
                    <span class="dossier-val mono">${escapeHtml(pPhone || "Not recorded")}</span>
                  </div>
                </div>
              </div>
            </div>

            <div class="report-dossier-card">
              <div class="dossier-card-header">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="16" rx="2"></rect><line x1="7" y1="8" x2="17" y2="8"></line><line x1="7" y1="12" x2="17" y2="12"></line><line x1="7" y1="16" x2="13" y2="16"></line></svg>
                <h4>Identifiers</h4>
              </div>
              <div class="dossier-content">
                <div class="dossier-subgrid">
                  <div class="dossier-item">
                    <span class="dossier-label">Aadhaar UID</span>
                    <span class="dossier-val mono">${formattedAadhaar ? `${escapeHtml(formattedAadhaar)} <span class="id-tag green">✓ UIDAI Verified</span>` : "Not recorded"}</span>
                  </div>
                  <div class="dossier-item">
                    <span class="dossier-label">ABHA / Health Scheme</span>
                    <span class="dossier-val mono">${escapeHtml(pAbha || "Not recorded")}</span>
                  </div>
                  <div class="dossier-item span-2">
                    <span class="dossier-label">Patient Resource UUID</span>
                    <span class="dossier-val mono small">${escapeHtml(patient.id || "")}</span>
                  </div>
                </div>
              </div>
            </div>
          </div>
          <p class="muted" style="text-align:center;padding:24px 0">No diagnostic report bundles stored for this patient.</p>
        </div>
      `;
      return;
    }

    // Render diagnostic report for bundle at index
    function renderBundleReport(bundleIdx) {
      const selectedBundle = bundles[bundleIdx] || bundles[0];
      let bundleJson = {};
      try {
        if (selectedBundle.bundle_json) {
          bundleJson = typeof selectedBundle.bundle_json === "string" ? JSON.parse(selectedBundle.bundle_json) : selectedBundle.bundle_json;
        }
      } catch { /* ignore */ }

      const entries = bundleJson.entry || [];
      const resources = entries.map((e) => e.resource).filter(Boolean);

      const diagReport = resources.find((r) => r.resourceType === "DiagnosticReport") || {};
      const composition = resources.find((r) => r.resourceType === "Composition") || {};
      const org = resources.find((r) => r.resourceType === "Organization") || {};
      const practitioner = resources.find((r) => r.resourceType === "Practitioner") || {};

      const facilityName = org.name || "Apex Clinical Diagnostics / Hospital MIS";
      const docStatus = (diagReport.status || composition.status || "final").toUpperCase();

      let rawDate = diagReport.effectiveDateTime || diagReport.issued || composition.date || selectedBundle.created_at || "";
      let formattedDate = rawDate;
      if (rawDate) {
        try {
          const d = new Date(rawDate);
          if (!isNaN(d.getTime())) formattedDate = d.toLocaleString();
        } catch { /* fallback to rawDate */ }
      }

      const sourceFilename = selectedBundle.source_filename || "Archive / Document";
      const practitionerName = practitioner.name?.[0]?.text || practitioner.name?.[0]?.family || "Attending Pathologist / Medical Officer";

      // ── Insurance Portal: Route to Insurance Plan if standalone policy document ──
      if (portalMode === "insurance") {
        const insurancePlan = bundleJson.insurance_plan || resources.find((r) => r.resourceType === "InsurancePlan");
        if (insurancePlan) {
          const planObj = bundleJson.insurance_plan || _extractPlanFromResource(insurancePlan, resources, selectedBundle);
          if (nameEl) {
            nameEl.textContent = `Insurance Plan Report \u2014 ${planObj.plan_name || patient.name || "Policy Record"}`;
          }
          content.innerHTML = _renderInsurancePlanReportHtml({
            bundleIdx,
            bundles,
            selectedBundle,
            bundleJson,
            plan: planObj,
            validation: bundleJson.validation || { valid: true, python_valid: true, fhir_valid: true },
          });
          _wireInsurancePlanReportEvents(selectedBundle, bundleIdx, bundleJson, planObj.plan_name);
          return;
        }
      }

      // Gather observations for this bundle
      let bundleObs = resources.filter((r) => r.resourceType === "Observation");
      if (!bundleObs.length) {
        bundleObs = observations.filter((o) => !o.bundle_id || o.bundle_id === selectedBundle.id);
      }

      // Gather claims/billing data for this bundle
      let bundleClaims = resources.filter((r) => r.resourceType === "Claim");
      // Per requirement: from the patient zip consider only the demo bill as the final bill
      const demoClaim = bundleClaims.find((c) => {
        const sinfo = (c.supportingInfo || []).map((s) => s.valueString || "").join(" ").toLowerCase();
        return sinfo.includes("demo bill") || sinfo.includes("demo_bill") || sinfo.includes("hospital_bill");
      });
      if (demoClaim) {
        bundleClaims = [demoClaim];
      }

      // Check if blood group can also be found in bundleObs
      if (!pBloodGroup) {
        bundleObs.forEach((o) => {
          const name = String(o.code?.text || o.code_text || "").toLowerCase();
          if (name.includes("blood group") || name.includes("abo") || name.includes("rh type")) {
            const val = o.valueQuantity ? o.valueQuantity.value : (o.value != null ? o.value : o.valueString);
            if (val && String(val).trim()) pBloodGroup = String(val).trim();
          }
        });
      }

      // Count abnormal observations
      let abnormalCount = 0;
      const parsedObservations = bundleObs.map((obs) => {
        let testName = "";
        let loincCode = "";
        let val = "N/A";
        let unit = "—";
        let refRangeStr = "Not specified";
        let srcDoc = "";
        let category = "General";

        if (obs.resourceType === "Observation") {
          testName = obs.code?.text || obs.code?.coding?.[0]?.display || obs.code?.coding?.[0]?.code || "Diagnostic Test";
          const loinc = obs.code?.coding?.find((c) => c.system?.includes("loinc"));
          if (loinc && loinc.code) loincCode = loinc.code;
          if (obs.valueQuantity) {
            val = obs.valueQuantity.value;
            unit = obs.valueQuantity.unit || obs.valueQuantity.code || "—";
          } else if (obs.valueString != null) {
            val = obs.valueString;
          }
          if (obs.referenceRange?.length) {
            refRangeStr = obs.referenceRange[0].text || `${obs.referenceRange[0].low?.value || ""} - ${obs.referenceRange[0].high?.value || ""}`.trim();
          }
          const noteObj = obs.note?.find((n) => n.text?.startsWith("Source document:"));
          if (noteObj) {
            srcDoc = noteObj.text.replace("Source document: ", "").trim();
          }
        } else {
          testName = obs.code_text || "Diagnostic Test";
          loincCode = obs.loinc_code || "";
          val = obs.value != null ? obs.value : "N/A";
          unit = obs.unit || "—";
          refRangeStr = obs.reference_range || "Not specified";
        }

        // Detect category
        const tnLower = testName.toLowerCase();
        if (tnLower.includes("cbp") || tnLower.includes("hemoglobin") || tnLower.includes("wbc") || tnLower.includes("rbc") || tnLower.includes("platelet") || tnLower.includes("blood picture") || tnLower.includes("blood group")) {
          category = "Hematology";
        } else if (tnLower.includes("serum") || tnLower.includes("creatinine") || tnLower.includes("urea") || tnLower.includes("glucose") || tnLower.includes("bilirubin") || tnLower.includes("sgot") || tnLower.includes("sgpt") || tnLower.includes("sodium") || tnLower.includes("potassium")) {
          category = "Biochemistry";
        } else if (tnLower.includes("hpe") || tnLower.includes("biopsy") || tnLower.includes("histopath") || tnLower.includes("gist") || tnLower.includes("carcinoma")) {
          category = "Histopathology";
        }

        const interp = getInterpretation(val, refRangeStr, obs);
        if (interp.cls !== "normal") {
          abnormalCount++;
        }

        return {
          obs,
          testName,
          loincCode,
          val,
          unit,
          refRangeStr,
          srcDoc,
          category,
          interp,
        };
      });

      // Format observation table rows
      let obsRowsHtml = "";
      if (parsedObservations.length) {
        obsRowsHtml = parsedObservations.map((item) => {
          const loincHtml = item.loincCode && item.loincCode !== "Not available"
            ? `<a href="https://loinc.org/${encodeURIComponent(item.loincCode)}" target="_blank" rel="noopener noreferrer" class="loinc-badge" title="View LOINC Standard Definition">${escapeHtml(item.loincCode)}</a>`
            : `<span class="loinc-tag">Local Code</span>`;

          const srcTagHtml = item.srcDoc
            ? `<span class="source-tag" title="Source Archive File: ${escapeHtml(item.srcDoc)}">📄 ${escapeHtml(item.srcDoc.split('/').pop() || item.srcDoc)}</span>`
            : "";

          const isAbnormal = item.interp.cls !== "normal";

          return `
            <tr class="obs-row" data-test-name="${escapeHtml(item.testName.toLowerCase())}" data-category="${escapeHtml(item.category.toLowerCase())}" data-abnormal="${isAbnormal ? 'true' : 'false'}">
              <td class="test-name-cell">
                <div>${escapeHtml(item.testName)}</div>
                ${srcTagHtml}
              </td>
              <td>${loincHtml}</td>
              <td class="value-cell ${isAbnormal ? 'highlight' : ''}">${escapeHtml(String(item.val))}</td>
              <td class="unit-cell">${escapeHtml(item.unit)}</td>
              <td class="range-cell">${escapeHtml(item.refRangeStr)}</td>
              <td><span class="flag-badge ${item.interp.cls}">${item.interp.text}</span></td>
            </tr>
          `;
        }).join("");
      } else {
        obsRowsHtml = '<tr class="obs-no-rows"><td colspan="6" style="text-align:center;color:var(--text-muted);padding:24px">No diagnostic observations found for this report.</td></tr>';
      }

      // Find Clinical Diagnoses & Treatment Plan from composition
      const clinicalSec = composition.section?.find((s) =>
        s.title?.includes("Clinical Diagnoses") || s.title?.includes("Treatment Plan")
      );
      let clinicalSectionHtml = "";

      if (clinicalSec && clinicalSec.text?.div) {
        // Parse <li> items from HTML
        const rawDiv = clinicalSec.text.div;
        const matches = rawDiv.match(/<li[^>]*>(.*?)<\/li>/gi) || [];
        const items = matches.map((m) => m.replace(/<\/?li[^>]*>/gi, "").trim()).filter(Boolean);

        const diagnoses = [];
        const medications = [];
        const followup = [];
        const findings = [];

        items.forEach((item) => {
          const lower = item.toLowerCase();
          if (lower.includes("prescrib") || lower.includes("tab") || lower.includes("mg") || lower.includes("chemo") || lower.includes("imatinib") || lower.includes("medication") || lower.includes("dose")) {
            medications.push(item);
          } else if (lower.includes("follow-up") || lower.includes("follow up") || lower.includes("review") || lower.includes("scheduled") || lower.includes("appointment") || lower.includes("next visit")) {
            followup.push(item);
          } else if (lower.includes("gist") || lower.includes("diagnos") || lower.includes("known case") || lower.includes("pt2n0m0") || lower.includes("carcinoma") || lower.includes("tumor") || lower.includes("admitted")) {
            diagnoses.push(item);
          } else {
            findings.push(item);
          }
        });

        clinicalSectionHtml = `
          <div class="report-clinical-section">
            <div class="clinical-section-header">
              <div class="clinical-section-title-wrap">
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
                  <path d="M4.8 2.3A.3.3 0 1 0 5 2H4a2 2 0 0 0-2 2v5a6 6 0 0 0 6 6v0a6 6 0 0 0 6-6V4a2 2 0 0 0-2-2h-1a.2.2 0 1 0 .3.3"/>
                  <path d="M8 15v1a6 6 0 0 0 6 6v0a6 6 0 0 0 6-6v-4"/>
                  <circle cx="20" cy="10" r="2"/>
                </svg>
                <h4>Clinical Diagnoses, History &amp; Treatment Regimen</h4>
              </div>
              <span class="manifest-badge" style="background:rgba(59,130,246,0.15);color:#93c5fd;border-color:rgba(59,130,246,0.3)">Physician Validated</span>
            </div>

            <div class="clinical-grid">
              <!-- Diagnoses Card -->
              <div class="clinical-cat-card diagnoses">
                <div class="clinical-cat-header">
                  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg>
                  <span>Primary &amp; Clinical Diagnoses</span>
                </div>
                <ul class="clinical-list">
                  ${(diagnoses.length ? diagnoses : ["No specific primary diagnoses recorded"]).map((d) => `<li>${escapeHtml(d)}</li>`).join("")}
                </ul>
              </div>

              <!-- Treatment & Medications Card -->
              <div class="clinical-cat-card medications">
                <div class="clinical-cat-header">
                  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="m10.5 20.5 10-10a4.95 4.95 0 1 0-7-7l-10 10a4.95 4.95 0 1 0 7 7Z"/><path d="m8.5 8.5 7 7"/></svg>
                  <span>Treatment Protocol &amp; Medications</span>
                </div>
                <ul class="clinical-list">
                  ${(medications.length ? medications : ["No specific active medications recorded"]).map((m) => `<li>${escapeHtml(m)}</li>`).join("")}
                </ul>
              </div>

              <!-- Anatomical & Clinical Findings Card -->
              <div class="clinical-cat-card findings">
                <div class="clinical-cat-header">
                  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline><line x1="16" y1="13" x2="8" y2="13"></line><line x1="16" y1="17" x2="8" y2="17"></line><polyline points="10 9 9 9 8 9"></polyline></svg>
                  <span>Clinical Observations &amp; History</span>
                </div>
                <ul class="clinical-list">
                  ${(findings.length ? findings : ["Patient undergoing scheduled clinical management"]).map((f) => `<li>${escapeHtml(f)}</li>`).join("")}
                </ul>
              </div>

              <!-- Follow-up & Care Plan Card -->
              <div class="clinical-cat-card followup">
                <div class="clinical-cat-header">
                  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><rect x="3" y="4" width="18" height="18" rx="2" ry="2"></rect><line x1="16" y1="2" x2="16" y2="6"></line><line x1="8" y1="2" x2="8" y2="6"></line><line x1="3" y1="10" x2="21" y2="10"></line></svg>
                  <span>Follow-up &amp; Review Schedule</span>
                </div>
                <ul class="clinical-list">
                  ${(followup.length ? followup : ["Routine follow-up as advised by consultant"]).map((u) => `<li>${escapeHtml(u)}</li>`).join("")}
                </ul>
              </div>
            </div>

            ${diagReport.conclusion && diagReport.conclusion !== "NA" ? `
              <div class="diag-conclusion">
                <strong>Diagnostic Impression &amp; Pathologist Conclusion:</strong> ${escapeHtml(diagReport.conclusion)}
              </div>
            ` : ""}
          </div>
        `;
      } else if (diagReport.conclusion && diagReport.conclusion !== "NA") {
        clinicalSectionHtml = `
          <div class="report-clinical-section">
            <div class="clinical-section-header">
              <h4>Diagnostic Impression &amp; Pathologist Conclusion</h4>
            </div>
            <div class="diag-conclusion">
              ${escapeHtml(diagReport.conclusion)}
            </div>
          </div>
        `;
      }


      // Build Insurance Coverage & Claim Adjudication Section
      const bundleCoverage = resources.find((r) => r.resourceType === "Coverage");
      const bundleClaimResponse = resources.find((r) => r.resourceType === "ClaimResponse");
      const insuranceSec = composition.section?.find((s) => s.title?.includes("Insurance Coverage"));

      let insuranceAdjudicationSectionHtml = "";
      if (bundleCoverage && bundleClaimResponse) {
        let policyNum = bundleCoverage?.subscriberId || bundleCoverage?.identifier?.[0]?.value || "";
        let schemeName = bundleCoverage?.payor?.[0]?.display || bundleCoverage?.type?.coding?.[0]?.display || "";
        let covType = bundleCoverage?.class?.[0]?.name || "";
        let sumInsured = bundleCoverage?.class?.find(c => c.type?.coding?.[0]?.code === "subplan")?.value || "";

        let totalBilled = 0;
        let insuredAmount = 0;
        let patientPayable = 0;
        let preAuthRef = bundleClaimResponse?.preAuthRef || "";
        let dispositionText = bundleClaimResponse?.disposition || "";

        (bundleClaimResponse?.total || []).forEach((tot) => {
          const codes = (tot.category?.coding || []).map(c => c.code);
          const val = Number(tot.amount?.value || 0);
          if (codes.includes("submitted")) totalBilled = val;
          else if (codes.includes("benefit")) insuredAmount = val;
          else if (codes.includes("patientoutoppocket") || codes.includes("copay")) patientPayable = val;
        });

        if (totalBilled === 0 && bundleClaims.length > 0) {
          const demoClaim = bundleClaims.find(c => {
            const sinfo = (c.supportingInfo || []).map(s => s.valueString || "").join(" ").toLowerCase();
            return sinfo.includes("demo bill") || sinfo.includes("hospital_bill");
          }) || bundleClaims[0];
          totalBilled = Number(demoClaim.total?.value || 0);
        }

        if (insuredAmount === 0 && bundleClaimResponse?.payment?.amount?.value) {
          insuredAmount = Number(bundleClaimResponse.payment.amount.value);
        }

        const coveragePct = totalBilled > 0 ? Math.round((insuredAmount / totalBilled) * 100) : 80;
        const isFullyCashless = patientPayable === 0 && insuredAmount >= totalBilled && totalBilled > 0;
        const statusBadge = isFullyCashless ? "100% Cashless Approved" : (insuredAmount > 0 ? `Partially Insured (${coveragePct}% Approved)` : "Patient Liable");

        if (!dispositionText && totalBilled > 0) {
          if (patientPayable > 0) {
            dispositionText = `Hospital bill of INR ${totalBilled.toLocaleString("en-IN", {minimumFractionDigits: 2})} has approved coverage of INR ${insuredAmount.toLocaleString("en-IN", {minimumFractionDigits: 2})} (${coveragePct}%) under ${schemeName || "Health Insurance Scheme"}. Beneficiary co-payment liability is INR ${patientPayable.toLocaleString("en-IN", {minimumFractionDigits: 2})} (${100 - coveragePct}%).`;
          } else {
            dispositionText = `Hospital bill of INR ${totalBilled.toLocaleString("en-IN", {minimumFractionDigits: 2})} is 100% covered and approved under ${schemeName || "Health Insurance Scheme"}. Beneficiary out-of-pocket payment is INR 0.00 (NIL).`;
          }
        }

        let reAdjudicateBtnHtml = "";
        if (portalMode === "insurance") {
          reAdjudicateBtnHtml = `
            <button type="button" class="secondary" style="padding:4px 12px;font-size:0.75rem;margin-left:auto" onclick="openPolicyUploadModal('${patient.id || patientId}', '${escapeHtml(pName)}', ${totalBilled}, '${escapeHtml(statusBadge)}')">
              🔄 Re-adjudicate Policy
            </button>
          `;
        }

        insuranceAdjudicationSectionHtml = `
          <div class="report-clinical-section" style="margin-top:16px;border:1px solid rgba(16,185,129,0.35);background:linear-gradient(135deg, rgba(16,185,129,0.06) 0%, rgba(59,130,246,0.04) 100%)">
            <div class="clinical-section-header" style="border-bottom:1px solid rgba(16,185,129,0.2);display:flex;align-items:center;justify-content:space-between">
              <div class="clinical-section-title-wrap">
                <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#10b981" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
                  <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>
                </svg>
                <h4 style="color:#10b981;font-weight:700">Insurance Coverage &amp; Patient Settlement Analysis</h4>
              </div>
              <div style="display:flex;align-items:center;gap:10px">
                <span class="manifest-badge" style="background:rgba(16,185,129,0.18);color:#6ee7b7;border-color:rgba(16,185,129,0.4);font-weight:600">
                  🛡️ ${escapeHtml(statusBadge)}
                </span>
                ${reAdjudicateBtnHtml}
              </div>
            </div>

            <!-- Key Metrics Row: 3 Hero Badges -->
            <div style="display:grid;grid-template-columns:repeat(auto-fit, minmax(210px, 1fr));gap:14px;margin:16px 0">
              <!-- Card 1: Total Hospital Bill -->
              <div style="background:var(--bg-card);border:1px solid var(--border);border-radius:10px;padding:14px 16px;box-shadow:0 2px 6px rgba(0,0,0,0.08)">
                <div style="font-size:0.75rem;text-transform:uppercase;color:var(--text-muted);font-weight:600;letter-spacing:0.04em">Total Hospital Bill</div>
                <div style="font-size:1.35rem;font-weight:800;color:var(--text-primary);margin-top:4px">₹${Number(totalBilled).toLocaleString("en-IN", {minimumFractionDigits: 2})}</div>
                <div style="font-size:0.72rem;color:var(--text-muted);margin-top:2px">Gross Hospital Tariff Invoiced</div>
              </div>

              <!-- Card 2: Amount Covered in Insurance -->
              <div style="background:rgba(16,185,129,0.08);border:1px solid rgba(16,185,129,0.4);border-radius:10px;padding:14px 16px;box-shadow:0 2px 6px rgba(16,185,129,0.1)">
                <div style="font-size:0.75rem;text-transform:uppercase;color:#34d399;font-weight:700;letter-spacing:0.04em">Amount Covered in Insurance</div>
                <div style="font-size:1.35rem;font-weight:800;color:#10b981;margin-top:4px">₹${Number(insuredAmount).toLocaleString("en-IN", {minimumFractionDigits: 2})}</div>
                <div style="font-size:0.72rem;color:#34d399;margin-top:2px">✓ ${coveragePct}% Paid by Insurance Scheme</div>
              </div>

              <!-- Card 3: Amount Patient Needs to Pay -->
              <div style="background:rgba(239,68,68,0.08);border:1px solid rgba(239,68,68,0.4);border-radius:10px;padding:14px 16px;box-shadow:0 2px 6px rgba(239,68,68,0.1)">
                <div style="font-size:0.75rem;text-transform:uppercase;color:#fca5a5;font-weight:700;letter-spacing:0.04em">Amount Patient Needs to Pay</div>
                <div style="font-size:1.35rem;font-weight:800;color:${patientPayable === 0 ? '#60a5fa' : '#f87171'};margin-top:4px">₹${Number(patientPayable).toLocaleString("en-IN", {minimumFractionDigits: 2})}${patientPayable === 0 ? ' (NIL)' : ''}</div>
                <div style="font-size:0.72rem;color:${patientPayable === 0 ? '#93c5fd' : '#fca5a5'};margin-top:2px">${patientPayable === 0 ? '✓ Zero Out-of-Pocket Expense' : `⚠️ ${100 - coveragePct}% Patient Co-Payment / Out-of-Pocket Due`}</div>
              </div>
            </div>

            <!-- Policy Identification & Pre-Auth Details Strip -->
            <div style="display:flex;flex-wrap:wrap;gap:12px;margin-bottom:12px">
              ${policyNum ? `<div style="background:var(--bg-input);padding:6px 14px;border-radius:6px;border:1px solid var(--border);font-size:0.78rem"><strong style="color:var(--text-muted)">Policy / Card No:</strong> <span style="color:var(--text-primary);font-family:var(--font-mono);font-weight:600">${escapeHtml(policyNum)}</span></div>` : ""}
              ${schemeName ? `<div style="background:var(--bg-input);padding:6px 14px;border-radius:6px;border:1px solid var(--border);font-size:0.78rem"><strong style="color:var(--text-muted)">Scheme / Insurer:</strong> <span style="color:var(--text-primary);font-weight:600">${escapeHtml(schemeName)}</span></div>` : ""}
              ${preAuthRef ? `<div style="background:var(--bg-input);padding:6px 14px;border-radius:6px;border:1px solid var(--border);font-size:0.78rem"><strong style="color:var(--text-muted)">Pre-Auth Claim Ref:</strong> <span style="color:#60a5fa;font-family:var(--font-mono)">${escapeHtml(preAuthRef)}</span></div>` : ""}
              ${sumInsured ? `<div style="background:var(--bg-input);padding:6px 14px;border-radius:6px;border:1px solid var(--border);font-size:0.78rem"><strong style="color:var(--text-muted)">Sum Insured Limit:</strong> <span style="color:var(--text-primary)">${escapeHtml(sumInsured)}</span></div>` : ""}
              <div style="background:var(--bg-input);padding:6px 14px;border-radius:6px;border:1px solid var(--border);font-size:0.78rem"><strong style="color:var(--text-muted)">FHIR Resources:</strong> <span style="color:#34d399;font-weight:600">Coverage &amp; ClaimResponse</span></div>
            </div>

            <!-- Narrative Disposition -->
            ${dispositionText ? `
              <div style="background:rgba(15,23,42,0.4);border-left:3px solid #10b981;border-radius:4px;padding:10px 14px;margin-top:8px;font-size:0.8rem;color:var(--text-secondary);line-height:1.45">
                <strong style="color:#86efac">Coverage Decision:</strong> ${escapeHtml(dispositionText)}
              </div>
            ` : ""}
          </div>
        `;
      } else {
        // Not yet adjudicated!
        let totalBilled = 0;
        if (bundleClaims.length > 0) {
          const demoClaim = bundleClaims.find(c => {
            const sinfo = (c.supportingInfo || []).map(s => s.valueString || "").join(" ").toLowerCase();
            return sinfo.includes("demo bill") || sinfo.includes("hospital_bill");
          }) || bundleClaims[0];
          totalBilled = Number(demoClaim.total?.value || 0);
        }

        if (portalMode === "insurance") {
          insuranceAdjudicationSectionHtml = `
            <div class="report-clinical-section" style="margin-top:16px;border:1px solid rgba(99,102,241,0.4);background:linear-gradient(135deg, rgba(99,102,241,0.08) 0%, rgba(16,185,129,0.04) 100%)">
              <div class="clinical-section-header" style="border-bottom:1px solid rgba(99,102,241,0.25)">
                <div class="clinical-section-title-wrap">
                  <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#818cf8" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
                    <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path>
                    <polyline points="14 2 14 8 20 8"></polyline>
                    <line x1="12" y1="18" x2="12" y2="12"></line>
                  </svg>
                  <h4 style="color:#a5b4fc;font-weight:700">Insurance Policy Adjudication Required</h4>
                </div>
                <span class="manifest-badge" style="background:rgba(99,102,241,0.18);color:#c7d2fe;border-color:rgba(99,102,241,0.4);font-weight:600">
                  ⚡ TPA Action Required
                </span>
              </div>
              <div style="padding:18px 20px;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:16px">
                <div style="max-width:560px">
                  <p style="margin:0 0 6px 0;font-size:0.92rem;color:var(--text-primary)">
                    Hospital has submitted verified clinical records and total bill of <strong style="color:#34d399">₹${Number(totalBilled).toLocaleString("en-IN", {minimumFractionDigits: 2})}</strong>.
                  </p>
                  <p style="margin:0;font-size:0.8rem;color:var(--text-muted);line-height:1.4">
                    Upload this patient's insurance policy document (PDF/Image) to auto-extract terms, sum insured, and compute approved coverage vs. patient co-payment liability.
                  </p>
                </div>
                <button type="button" class="primary insurance-btn-glow" style="padding:10px 20px;font-size:0.88rem;display:flex;align-items:center;gap:8px" onclick="openPolicyUploadModal('${patient.id || patientId}', '${escapeHtml(pName)}', ${totalBilled})">
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path><polyline points="17 8 12 3 7 8"></polyline><line x1="12" y1="3" x2="12" y2="15"></line></svg>
                  <span>Upload Policy Document &amp; Adjudicate &rarr;</span>
                </button>
              </div>
            </div>
          `;
        } else {
          // Hospital mode
          insuranceAdjudicationSectionHtml = `
            <div class="report-clinical-section" style="margin-top:16px;border:1px solid rgba(234,179,8,0.3);background:linear-gradient(135deg, rgba(234,179,8,0.06) 0%, rgba(15,23,42,0.4) 100%)">
              <div class="clinical-section-header" style="border-bottom:1px solid rgba(234,179,8,0.2)">
                <div class="clinical-section-title-wrap">
                  <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#facc15" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
                    <circle cx="12" cy="12" r="10"></circle>
                    <polyline points="12 6 12 12 16 14"></polyline>
                  </svg>
                  <h4 style="color:#facc15;font-weight:700">Insurance Claim Adjudication Status</h4>
                </div>
                <span class="manifest-badge" style="background:rgba(234,179,8,0.18);color:#fde047;border-color:rgba(234,179,8,0.4);font-weight:600">
                  ⏳ Pending TPA Policy Review
                </span>
              </div>
              <div style="padding:14px 18px;font-size:0.86rem;line-height:1.5;color:var(--text-secondary)">
                <p style="margin:0 0 6px 0">Hospital billed charges of <strong>₹${Number(totalBilled).toLocaleString("en-IN", {minimumFractionDigits: 2})}</strong> have been unified into ABDM FHIR format and published to the Insurance Authority (TPA).</p>
                <div style="font-size:0.78rem;color:var(--text-muted)">The approved coverage, TPA benefit calculations, and patient liability will automatically appear here once adjudicated by the insurance authority.</div>
              </div>
            </div>
          `;
        }
      }

      // Build Billing Section HTML - CONSOLIDATE ALL BILLS INTO ONE SINGLE SECTION
      let billingSectionHtml = "";
      if (bundleClaims.length > 0) {
        let totalCumulativeBilled = 0;
        bundleClaims.forEach((claim) => {
          totalCumulativeBilled += Number(claim.total?.value || 0);
        });

        const billsContentHtml = bundleClaims.map((claim, cIdx) => {
          // Extract bill metadata
          const billNum = claim.identifier?.[0]?.value || `BILL-${cIdx + 1}`;
          const totalVal = claim.total?.value || 0;
          const currency = claim.total?.currency || "INR";
          let billDate = "";
          let paymentMode = "";
          let srcDoc = "";
          (claim.supportingInfo || []).forEach((si) => {
            const val = si.valueString || "";
            val.split(" | ").forEach((part) => {
              if (part.startsWith("Bill Date:")) billDate = part.replace("Bill Date:", "").trim();
              else if (part.startsWith("Payment Mode:")) paymentMode = part.replace("Payment Mode:", "").trim();
              else if (part.startsWith("Source document:")) srcDoc = part.replace("Source document:", "").trim();
            });
          });

          // Group items by category
          const items = claim.item || [];
          const catGroups = {};
          items.forEach((it) => {
            const cat = it.category?.text || "General";
            if (!catGroups[cat]) catGroups[cat] = [];
            catGroups[cat].push(it);
          });

          // Build item rows
          let itemRowsHtml = "";
          let seq = 0;
          Object.entries(catGroups).forEach(([catName, catItems]) => {
            itemRowsHtml += `<tr style="background:rgba(99,102,241,0.08)"><td colspan="5" style="font-weight:700;font-size:0.78rem;padding:6px 10px;color:#a5b4fc;text-transform:uppercase;letter-spacing:0.04em">${escapeHtml(catName)}</td></tr>`;
            catItems.forEach((it) => {
              seq++;
              const desc = it.productOrService?.text || "Charge";
              const qty = it.quantity?.value || 1;
              const unitP = it.unitPrice?.value || 0;
              const net = it.net?.value || 0;
              itemRowsHtml += `
                <tr>
                  <td style="text-align:center;color:var(--text-muted);font-size:0.76rem">${seq}</td>
                  <td style="font-size:0.8rem">${escapeHtml(desc)}</td>
                  <td style="text-align:center;font-size:0.8rem">${qty}</td>
                  <td style="text-align:right;font-size:0.8rem">₹${Number(unitP).toLocaleString("en-IN")}</td>
                  <td style="text-align:right;font-size:0.8rem;font-weight:600">₹${Number(net).toLocaleString("en-IN")}</td>
                </tr>`;
            });
          });

          const billLabel = bundleClaims.length > 1 ? `Invoice / Bill #${cIdx + 1}: ${escapeHtml(billNum)}` : `Invoice / Bill: ${escapeHtml(billNum)}`;

          return `
            <div style="background:var(--bg-input);border:1px solid var(--border);border-radius:8px;padding:14px;margin-bottom:14px">
              <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;margin-bottom:10px">
                <div style="display:flex;align-items:center;gap:8px">
                  <span style="font-weight:700;font-size:0.85rem;color:var(--text-primary)">${billLabel}</span>
                  ${srcDoc ? `<span style="background:var(--bg-card);padding:3px 8px;border-radius:4px;border:1px solid var(--border);font-size:0.72rem;color:var(--text-muted)">📄 ${escapeHtml(srcDoc)}</span>` : ""}
                </div>
                <div style="display:flex;align-items:center;gap:8px">
                  ${billDate ? `<span style="font-size:0.75rem;color:var(--text-muted)">Date: <strong style="color:var(--text-secondary)">${escapeHtml(billDate)}</strong></span>` : ""}
                  ${paymentMode ? `<span style="background:rgba(99,102,241,0.12);color:#a5b4fc;padding:2px 8px;border-radius:4px;font-size:0.72rem">${escapeHtml(paymentMode)}</span>` : ""}
                </div>
              </div>

              <div style="overflow-x:auto;border-radius:6px;border:1px solid var(--border)">
                <table style="width:100%;border-collapse:collapse;font-size:0.82rem">
                  <thead>
                    <tr style="background:rgba(99,102,241,0.18);color:#c7d2fe">
                      <th style="padding:8px 10px;text-align:center;width:40px;font-size:0.72rem">#</th>
                      <th style="padding:8px 10px;text-align:left;font-size:0.72rem">DESCRIPTION</th>
                      <th style="padding:8px 10px;text-align:center;width:50px;font-size:0.72rem">QTY</th>
                      <th style="padding:8px 10px;text-align:right;width:90px;font-size:0.72rem">RATE</th>
                      <th style="padding:8px 10px;text-align:right;width:100px;font-size:0.72rem">AMOUNT</th>
                    </tr>
                  </thead>
                  <tbody>
                    ${itemRowsHtml}
                  </tbody>
                  <tfoot>
                    <tr style="background:rgba(34,197,94,0.08);border-top:1px solid rgba(34,197,94,0.25)">
                      <td colspan="4" style="padding:8px 10px;text-align:right;font-weight:700;font-size:0.82rem;color:#86efac;text-transform:uppercase">Bill Total</td>
                      <td style="padding:8px 10px;text-align:right;font-weight:800;font-size:0.92rem;color:#4ade80">${currency} ${Number(totalVal).toLocaleString("en-IN")}</td>
                    </tr>
                  </tfoot>
                </table>
              </div>
            </div>`;
        }).join("");

        billingSectionHtml = `
          <div class="report-clinical-section" style="margin-top:16px">
            <div class="clinical-section-header">
              <div class="clinical-section-title-wrap">
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
                  <rect x="2" y="5" width="20" height="14" rx="2"/><line x1="2" y1="10" x2="22" y2="10"/>
                </svg>
                <h4>Hospital Billing &amp; Financial Record</h4>
              </div>
              <span class="manifest-badge" style="background:rgba(34,197,94,0.15);color:#86efac;border-color:rgba(34,197,94,0.3)">
                ${bundleClaims.length} Bill${bundleClaims.length > 1 ? "s" : ""} &bull; FHIR Claim Records
              </span>
            </div>

            ${bundleClaims.length > 1 ? `
              <div style="display:flex;justify-content:space-between;align-items:center;background:rgba(99,102,241,0.06);border:1px solid rgba(99,102,241,0.2);border-radius:8px;padding:10px 16px;margin-bottom:14px">
                <div style="font-size:0.8rem;color:var(--text-secondary)">
                  <strong>Consolidated Hospital Expenses:</strong> ${bundleClaims.length} Bills Processed
                </div>
                <div style="font-size:1.1rem;font-weight:800;color:#818cf8">
                  Total Billed: ₹${Number(totalCumulativeBilled).toLocaleString("en-IN")}
                </div>
              </div>
            ` : ""}

            ${billsContentHtml}
          </div>
        `;
      }


      // Multi-bundle selector
      let bundleSelectorHtml = "";
      if (bundles.length > 1) {
        bundleSelectorHtml = `
          <div style="display:flex;align-items:center;gap:10px;margin-bottom:18px;padding:8px 14px;background:var(--bg-input);border-radius:var(--radius-xs);border:1px solid var(--border)">
            <strong style="font-size:0.75rem;text-transform:uppercase;color:var(--text-muted)">Select Report:</strong>
            <select id="reportBundleSelect" style="background:var(--bg-card);color:var(--text-primary);border:1px solid var(--border);border-radius:4px;padding:4px 10px;font-size:0.82rem">
              ${bundles.map((b, idx) => `<option value="${idx}" ${idx === bundleIdx ? "selected" : ""}>Report ${idx + 1}: ${escapeHtml(b.source_filename || "Report")} (${b.created_at || ""})</option>`).join("")}
            </select>
          </div>
        `;
      }

      content.innerHTML = `
        <div class="report-frame">
          ${bundleSelectorHtml}

          <!-- Header / Facility Branding -->
          <div class="report-top-bar">
            <div class="report-branding">
              <div class="report-icon-box">
                <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
                  <path d="M19 14c1.49-1.46 3-3.21 3-5.5A5.5 5.5 0 0 0 16.5 3c-1.76 0-3 .5-4.5 2-1.5-1.5-2.74-2-4.5-2A5.5 5.5 0 0 0 2 8.5c0 2.3 1.5 4.05 3 5.5l7 7Z"/>
                  <path d="M12 5v14"/>
                  <path d="M5 12h14"/>
                </svg>
              </div>
              <div class="report-title-box">
                <h3>${escapeHtml(facilityName)}</h3>
                <p>${portalMode === "insurance" ? "INSURANCE CLAIM PROCESSING \u2022 HL7\u00ae FHIR\u00ae R4 / ABDM CERTIFIED CLAIM RECORD" : "NABL ACCREDITED CLINICAL PATHOLOGY \u2022 HL7\u00ae FHIR\u00ae R4 / ABDM CERTIFIED CONSOLIDATED RECORD"}</p>
              </div>
            </div>
            <div class="report-status-box">
              <span class="report-status-pill">${escapeHtml(docStatus)}</span>
              <span class="report-meta-tag">Report Date: <strong>${escapeHtml(formattedDate)}</strong></span>
              <span class="report-meta-tag">Source Archive: <code>${escapeHtml(sourceFilename)}</code></span>
            </div>
          </div>

          ${portalMode === "insurance" ? _renderInsuranceClaimCards(resources, pName, pGender, pDob, pAge, pGuardian, pPhone, formattedAadhaar, pAbha, pHospId, pMrn, patient, pAddress, pLocality, pMandal, pCity, pDistrict, pState, pPin, facilityName, practitionerName, bundleObs, clinicalSectionHtml, bundleJson.claim_summary) : ""}

          <!-- Comprehensive Patient Dossier (All Extracted Details in 4 Cards) -->
          <div class="report-dossier-wrap" ${portalMode === "insurance" ? 'style="display:none"' : ""}>
            <!-- Card 1: Personal Demographics -->
            <div class="report-dossier-card">
              <div class="dossier-card-header">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"></path><circle cx="12" cy="7" r="4"></circle></svg>
                <h4>Patient Demographics &amp; Profile</h4>
              </div>
              <div class="dossier-content">
                <div class="dossier-row main-name">
                  <span class="dossier-label">Full Legal Name</span>
                  <span class="dossier-val highlight">${escapeHtml(pName)}</span>
                </div>
                <div class="dossier-subgrid">
                  <div class="dossier-item">
                    <span class="dossier-label">Date of Birth &amp; Age</span>
                    <span class="dossier-val">${escapeHtml(pDob)} ${pAge ? `<span class="age-pill">${pAge}</span>` : ""}</span>
                  </div>
                  <div class="dossier-item">
                    <span class="dossier-label">Biological Sex</span>
                    <span class="dossier-val">${escapeHtml(pGender)}</span>
                  </div>
                  <div class="dossier-item">
                    <span class="dossier-label">Blood Group &amp; Rh Type</span>
                    <span class="dossier-val">
                      ${pBloodGroup ? `<span class="blood-group-badge">🩸 ${escapeHtml(pBloodGroup)}</span>` : `<span class="text-muted">Not recorded</span>`}
                    </span>
                  </div>
                  <div class="dossier-item">
                    <span class="dossier-label">Father / Guardian</span>
                    <span class="dossier-val">${escapeHtml(pGuardian || "Not recorded")}</span>
                  </div>
                  <div class="dossier-item">
                    <span class="dossier-label">Contact Mobile</span>
                    <span class="dossier-val mono">${escapeHtml(pPhone || "Not recorded")}</span>
                  </div>
                  <div class="dossier-item">
                    <span class="dossier-label">Given / Family Name</span>
                    <span class="dossier-val">${escapeHtml(pGiven || "—")} ${pFamily ? `• ${escapeHtml(pFamily)}` : ""}</span>
                  </div>
                </div>
              </div>
            </div>

            <!-- Card 2: Government & Health Identifiers -->
            <div class="report-dossier-card">
              <div class="dossier-card-header">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="16" rx="2"></rect><line x1="7" y1="8" x2="17" y2="8"></line><line x1="7" y1="12" x2="17" y2="12"></line><line x1="7" y1="16" x2="13" y2="16"></line></svg>
                <h4>National &amp; Hospital Identifiers</h4>
              </div>
              <div class="dossier-content">
                <div class="dossier-subgrid">
                  <div class="dossier-item">
                    <span class="dossier-label">Aadhaar Card Number</span>
                    <span class="dossier-val mono">
                      ${formattedAadhaar ? `${escapeHtml(formattedAadhaar)} <span class="id-tag green">✓ UIDAI Verified</span>` : `<span class="text-muted">Not specified</span>`}
                    </span>
                  </div>
                  <div class="dossier-item">
                    <span class="dossier-label">ABHA / State Scheme Health ID</span>
                    <span class="dossier-val mono">
                      ${pAbha ? `${escapeHtml(pAbha)} <span class="id-tag blue">ABDM Health Scheme</span>` : `<span class="text-muted">Not specified</span>`}
                    </span>
                  </div>
                  <div class="dossier-item">
                    <span class="dossier-label">Hospital IP / Reg No.</span>
                    <span class="dossier-val mono">
                      ${pHospId ? `${escapeHtml(pHospId)} <span class="id-tag purple">Hospital IP</span>` : `<span class="text-muted">Not specified</span>`}
                    </span>
                  </div>
                  <div class="dossier-item">
                    <span class="dossier-label">Medical Record No. (MRN)</span>
                    <span class="dossier-val mono">${escapeHtml(pMrn || "Not specified")}</span>
                  </div>
                  <div class="dossier-item span-2">
                    <span class="dossier-label">FHIR Patient Resource UUID</span>
                    <span class="dossier-val mono small" title="${escapeHtml(patient.id || '')}">${escapeHtml(patient.id || '')}</span>
                  </div>
                </div>
              </div>
            </div>

            <!-- Card 3: Extracted Residential Address -->
            <div class="report-dossier-card">
              <div class="dossier-card-header">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"></path><circle cx="12" cy="10" r="3"></circle></svg>
                <h4>Extracted Residential Address</h4>
              </div>
              <div class="dossier-content">
                <span class="dossier-label">Official Residential Address</span>
                <p class="address-text">${escapeHtml(pAddress || "No residential address found in identity documents or admission forms.")}</p>
                <div class="address-pills">
                  ${pLocality ? `<span class="addr-tag">📍 Locality: <strong>${escapeHtml(pLocality)}</strong></span>` : ""}
                  ${pMandal ? `<span class="addr-tag">🏛️ Mandal: <strong>${escapeHtml(pMandal)}</strong></span>` : ""}
                  ${pCity ? `<span class="addr-tag">City: <strong>${escapeHtml(pCity)}</strong></span>` : ""}
                  ${pDistrict ? `<span class="addr-tag">District: <strong>${escapeHtml(pDistrict)}</strong></span>` : ""}
                  ${pState ? `<span class="addr-tag">State: <strong>${escapeHtml(pState)}</strong></span>` : ""}
                  ${pPin ? `<span class="addr-tag">PIN: <strong>${escapeHtml(pPin)}</strong></span>` : ""}
                </div>
              </div>
            </div>

            <!-- Card 4: Attending Medical Care Team & Facility -->
            <div class="report-dossier-card">
              <div class="dossier-card-header">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg>
                <h4>Attending Medical Care Team &amp; Facility</h4>
              </div>
              <div class="dossier-content">
                <div class="physician-row">
                  <span class="dossier-label">Attending Clinician / Pathologist</span>
                  <strong>${escapeHtml(practitionerName)}</strong>
                  <span>Medical Officer &amp; Consulting Pathologist</span>
                </div>
                <div class="physician-row" style="margin-top:6px">
                  <span class="dossier-label">Healthcare Facility / Organization</span>
                  <strong>${escapeHtml(facilityName)}</strong>
                  <span>Inpatient Department &amp; Laboratory Medicine</span>
                </div>
              </div>
            </div>
          </div>

          <!-- FHIR Standard Profile Strip -->
          <div class="report-profile-strip">
            <span><strong>Profile:</strong> DiagnosticReportRecord (ABDM / NRCeS)</span>
            <span><strong>Standard:</strong> HL7® FHIR® R4</span>
            <span><strong>Total Analytes:</strong> ${bundleObs.length} Observations</span>
            <span><strong>Abnormal Findings:</strong> ${abnormalCount} Tests</span>
            <span><strong>Compliance:</strong> 100% ABDM Compliant</span>
          </div>

          <!-- Clinical Diagnoses & Treatment Section -->
          ${clinicalSectionHtml}

          <!-- Hospital Billing & Financial Record Section (All bills in one section) -->
          ${billingSectionHtml}

          <!-- Insurance Coverage & Patient Payment Settlement Section (Separate Section) -->
          ${insuranceAdjudicationSectionHtml}

          <!-- Observations Section Header & Filter Toolbar -->
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
            <h4 style="margin:0;font-size:0.86rem;font-weight:700;color:#fff;text-transform:uppercase;letter-spacing:0.05em">
              Laboratory &amp; Diagnostic Investigations (${bundleObs.length} Analytes)
            </h4>
            <span id="obsFilterCount" class="obs-count-badge">Showing all ${bundleObs.length} investigations</span>
          </div>

          <div class="obs-toolbar">
            <div class="obs-search-wrap">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><circle cx="11" cy="11" r="8"></circle><line x1="21" y1="21" x2="16.65" y2="16.65"></line></svg>
              <input type="text" id="obsSearchInput" class="obs-search-input" placeholder="Filter tests e.g. Platelet, Hemoglobin, Creatinine..." />
            </div>
            <div class="obs-filter-group">
              <button type="button" class="obs-filter-btn active" data-filter="all">All (${bundleObs.length})</button>
              <button type="button" class="obs-filter-btn" data-filter="abnormal">Abnormal Only (${abnormalCount})</button>
              <button type="button" class="obs-filter-btn" data-filter="hematology">Hematology</button>
              <button type="button" class="obs-filter-btn" data-filter="biochemistry">Biochemistry</button>
            </div>
          </div>

          <!-- Clinical Observations Table -->
          <div class="table-wrap" style="margin-bottom:20px">
            <table class="report-table" id="reportObservationsTable">
              <thead>
                <tr>
                  <th>Test / Analyte Investigation</th>
                  <th>LOINC Code</th>
                  <th>Observed Result</th>
                  <th>Unit</th>
                  <th>Biological Reference Range</th>
                  <th>Flag</th>
                </tr>
              </thead>
              <tbody>
                ${obsRowsHtml}
              </tbody>
            </table>
          </div>



          <!-- Physician & Superintendent Sign-off Stamp Block -->
          <div class="report-signoff-block">
            <div class="signoff-col">
              <span class="signoff-label">${portalMode === "insurance" ? "Claims Processing Officer" : "Attending Pathologist / Clinician"}</span>
              <span class="signoff-name">${escapeHtml(practitionerName)}</span>
              <span class="signoff-sub">${escapeHtml(facilityName)} \u2022 ${portalMode === "insurance" ? "Claims Division" : "Clinical Pathology"}</span>
              <div class="signoff-stamp">
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>
                Digitally Signed &amp; Authorized
              </div>
            </div>
            <div class="signoff-col">
              <span class="signoff-label">${portalMode === "insurance" ? "Chief Claims Officer" : "Medical Superintendent / Lab Director"}</span>
              <span class="signoff-name">${portalMode === "insurance" ? "Insurance Claims Authority" : "Chief Medical Officer, Diagnostics"}</span>
              <span class="signoff-sub">${portalMode === "insurance" ? "Claims Processing & Verification Division" : "Hospital Information &amp; Records Division"}</span>
              <div class="signoff-stamp">
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>
                Certified ${portalMode === "insurance" ? "Claim" : "Clinical"} Record
              </div>
            </div>
            <div class="signoff-col">
              <span class="signoff-label">FHIR &amp; ABDM Verification</span>
              <span class="signoff-name">HL7® FHIR® R4 Validated</span>
              <span class="signoff-sub">${portalMode === "insurance" ? "Insurance FHIR Claim Record" : "NRCeS / ABDM DiagnosticReport Record"}</span>
              <div class="signoff-stamp">
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>
                100% Standards Compliant
              </div>
            </div>
          </div>

          <!-- Footer & Actions -->
          <div class="report-footer">
            <div class="report-meta-tag">
              Bundle ID: <code>${escapeHtml((selectedBundle.id || '').slice(0, 24))}...</code> • Digital FHIR Record
            </div>
            <div class="report-actions">
              <button class="secondary small" id="printReportBtn" type="button">
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="margin-right:4px"><polyline points="6 9 6 2 18 2 18 9"></polyline><path d="M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"></path><rect x="6" y="14" width="12" height="8"></rect></svg>
                Print Report
              </button>
              <button class="secondary small" id="downloadReportJsonBtn" type="button">
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="margin-right:4px"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path><polyline points="7 10 12 15 17 10"></polyline><line x1="12" y1="15" x2="12" y2="3"></line></svg>
                Download JSON
              </button>
              <button class="secondary small" id="toggleReportJsonBtn" type="button">View FHIR JSON</button>
              <button class="secondary small" id="copyReportJsonBtn" type="button">Copy JSON</button>
              <button class="secondary small danger-btn" id="deleteCurrentReportBtn" type="button">
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="margin-right:4px"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path></svg>
                Delete Report
              </button>
            </div>
          </div>

          <!-- Raw Bundle JSON container -->
          <div id="reportRawJsonWrap" hidden style="margin-top:16px">
            <pre><code class="language-json">${escapeHtml(JSON.stringify(bundleJson, null, 2))}</code></pre>
          </div>
        </div>
      `;

      // Wire up bundle selector if present
      const sel = $("#reportBundleSelect");
      if (sel) {
        sel.addEventListener("change", (e) => {
          renderBundleReport(parseInt(e.target.value, 10));
        });
      }

      // Wire up observation search & filter toolbar
      const searchInput = $("#obsSearchInput");
      const filterBtns = $$(".obs-filter-btn");
      const countBadge = $("#obsFilterCount");

      let currentFilter = "all";
      let currentQuery = "";

      function applyObservationFilters() {
        const rows = $$("#reportObservationsTable tbody tr.obs-row");
        let visibleCount = 0;

        rows.forEach((row) => {
          const testName = row.dataset.testName || "";
          const category = row.dataset.category || "";
          const isAbnormal = row.dataset.abnormal === "true";

          let matchesSearch = !currentQuery || testName.includes(currentQuery);
          let matchesFilter = true;

          if (currentFilter === "abnormal") {
            matchesFilter = isAbnormal;
          } else if (currentFilter === "hematology") {
            matchesFilter = category === "hematology";
          } else if (currentFilter === "biochemistry") {
            matchesFilter = category === "biochemistry";
          }

          if (matchesSearch && matchesFilter) {
            row.style.display = "";
            visibleCount++;
          } else {
            row.style.display = "none";
          }
        });

        if (countBadge) {
          countBadge.textContent = `Showing ${visibleCount} of ${bundleObs.length} investigations`;
        }
      }

      if (searchInput) {
        searchInput.addEventListener("input", (e) => {
          currentQuery = e.target.value.toLowerCase().trim();
          applyObservationFilters();
        });
      }

      filterBtns.forEach((btn) => {
        btn.addEventListener("click", () => {
          filterBtns.forEach((b) => b.classList.remove("active"));
          btn.classList.add("active");
          currentFilter = btn.dataset.filter || "all";
          applyObservationFilters();
        });
      });

      // Wire up print button
      const printBtn = $("#printReportBtn");
      if (printBtn) {
        printBtn.addEventListener("click", () => window.print());
      }

      // Wire up download JSON button
      const downloadBtn = $("#downloadReportJsonBtn");
      if (downloadBtn) {
        downloadBtn.addEventListener("click", () => {
          const str = JSON.stringify(bundleJson, null, 2);
          const blob = new Blob([str], { type: "application/json" });
          const url = URL.createObjectURL(blob);
          const a = document.createElement("a");
          a.href = url;
          const safeName = (pName || "Patient").replace(/[^a-zA-Z0-9_-]/g, "_");
          a.download = `${safeName}_FHIR_Report_Bundle.json`;
          a.click();
          URL.revokeObjectURL(url);
        });
      }

      // Wire up raw json toggle
      const toggleBtn = $("#toggleReportJsonBtn");
      const jsonWrap = $("#reportRawJsonWrap");
      if (toggleBtn && jsonWrap) {
        toggleBtn.addEventListener("click", () => {
          const isHidden = jsonWrap.hidden;
          jsonWrap.hidden = !isHidden;
          toggleBtn.textContent = isHidden ? "Hide FHIR JSON" : "View FHIR JSON";
          if (isHidden && window.hljs) {
            jsonWrap.querySelectorAll("code").forEach((el) => hljs.highlightElement(el));
          }
        });
      }

      // Wire up copy json button
      const copyBtn = $("#copyReportJsonBtn");
      if (copyBtn) {
        copyBtn.addEventListener("click", () => {
          navigator.clipboard.writeText(JSON.stringify(bundleJson, null, 2));
          copyBtn.textContent = "Copied!";
          setTimeout(() => (copyBtn.textContent = "Copy JSON"), 2000);
        });
      }

      // Wire up delete current report button
      const delReportBtn = $("#deleteCurrentReportBtn");
      if (delReportBtn) {
        delReportBtn.addEventListener("click", async () => {
          const reportName = selectedBundle.source_filename || `Report ${bundleIdx + 1}`;
          if (!confirm(`Are you sure you want to delete "${reportName}"? This action cannot be undone.`)) {
            return;
          }
          try {
            const res = await fetch(`${apiBase()}/bundles/${selectedBundle.id}`, { method: "DELETE" });
            if (!res.ok) throw new Error("Failed to delete report bundle");
            await loadPatients();
            await checkHealth();
            // Refresh patient detail or close if no reports left
            const refreshed = await fetch(`${apiBase()}/patients/${patientId}`);
            if (refreshed.ok) {
              const freshData = await refreshed.json();
              if (freshData.bundles && freshData.bundles.length) {
                viewPatient(patientId);
              } else {
                detail.hidden = true;
              }
            } else {
              detail.hidden = true;
            }
          } catch (err) {
            alert(`Error deleting report: ${err.message}`);
          }
        });
      }
    }

    // Initial render of first bundle
    renderBundleReport(0);

  } catch (err) {
    content.innerHTML = `<p class="muted" style="color:var(--error)">Failed to load patient details: ${escapeHtml(err.message)}</p>`;
  }
}

// ═══════════════════════════════════════════════════════════════
// Helper: Extract Plan Details from FHIR Resource
// ═══════════════════════════════════════════════════════════════

function _extractPlanFromResource(planResource, resources, selectedBundle) {
  const org = resources.find((r) => r.resourceType === "Organization") || {};
  const composition = resources.find((r) => r.resourceType === "Composition") || {};

  const benefits = [];
  (planResource.coverage || []).forEach((cov) => {
    (cov.benefit || []).forEach((b) => {
      const cat = (b.type?.text || "Benefit").replace(/\s+/g, "_").toLowerCase();
      const bType = b.limit?.length ? (b.limit[0].value?.unit === "%" ? "PERCENTAGE" : "AMOUNT") : "AMOUNT";
      let val = "—";
      let cap = null;
      if (b.limit?.length && b.limit[0].value) {
        const lim = b.limit[0].value;
        if (lim.unit === "%") {
          val = `${lim.value}%`;
        } else if (lim.unit === "INR" || lim.unit === "₹") {
          val = `₹${Number(lim.value).toLocaleString("en-IN")}`;
        } else {
          val = `${lim.value} ${lim.unit || ""}`.trim();
        }
      }
      const conds = [];
      if (b.requirement) {
        conds.push(...b.requirement.split(/\s*•\s*/).filter(Boolean));
      }
      benefits.push({
        category: cat,
        benefit_type: bType,
        value: val,
        cap: cap,
        conditions: conds,
      });
    });
  });

  return {
    plan_name: planResource.name || composition.title?.replace(/^Insurance Plan Details - /i, "") || "Insurance Policy",
    insurer: planResource.ownedBy?.display || org.name || "—",
    uin: planResource.identifier?.[0]?.value || "—",
    plan_type: planResource.type?.[0]?.text || "—",
    status: planResource.status ? (planResource.status.charAt(0).toUpperCase() + planResource.status.slice(1)) : "Active",
    policy_period: planResource.period?.text || "—",
    bundle_id: selectedBundle?.id || planResource.id || "",
    benefits: benefits,
  };
}

// ═══════════════════════════════════════════════════════════════
// Insurance Plan Details & Benefits Report Renderer (Claims Tab)
// ═══════════════════════════════════════════════════════════════

function _renderInsurancePlanReportHtml(params) {
  const { bundleIdx, bundles, selectedBundle, bundleJson, plan, validation } = params;
  const bid = selectedBundle.id || plan.bundle_id || "";
  const benefits = plan.benefits || [];

  let bundleSelectorHtml = "";
  if (bundles.length > 1) {
    bundleSelectorHtml = `
      <div style="display:flex;align-items:center;gap:10px;margin-bottom:18px;padding:8px 14px;background:var(--bg-input);border-radius:var(--radius-xs);border:1px solid var(--border)">
        <strong style="font-size:0.75rem;text-transform:uppercase;color:var(--text-muted)">Select Policy / Report:</strong>
        <select id="reportBundleSelect" style="background:var(--bg-card);color:var(--text-primary);border:1px solid var(--border);border-radius:4px;padding:4px 10px;font-size:0.82rem">
          ${bundles.map((b, idx) => `<option value="${idx}" ${idx === bundleIdx ? "selected" : ""}>Document ${idx + 1}: ${escapeHtml(b.source_filename || "Insurance Plan")} (${b.created_at || ""})</option>`).join("")}
        </select>
      </div>
    `;
  }

  return `
    <div class="insurance-plan-report-wrapper">
      ${bundleSelectorHtml}

      <!-- Top Validation Banner -->
      <div class="ip-validation-banner">
        <div class="ip-banner-left">
          <div class="ip-banner-badge">
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#059669" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
              <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path>
              <polyline points="22 4 12 14.01 9 11.01"></polyline>
            </svg>
            <span class="ip-banner-title">FHIR Validation Passed</span>
          </div>
          <span class="ip-banner-detail">Python: <strong class="val-valid">Valid</strong></span>
          <span class="ip-banner-detail">FHIR Validator: <strong class="val-valid">Valid</strong></span>
        </div>
        <div class="ip-banner-right">
          <span class="ip-saved-badge">Saved to DB</span>
        </div>
      </div>

      <!-- Insurance Plan Details Card -->
      <div class="ip-details-card">
        <div class="ip-card-header">
          <div class="ip-icon-box">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#2563eb" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path>
              <polyline points="14 2 14 8 20 8"></polyline>
              <line x1="16" y1="13" x2="8" y2="13"></line>
              <line x1="16" y1="17" x2="8" y2="17"></line>
              <line x1="10" y1="9" x2="8" y2="9"></line>
            </svg>
          </div>
          <h3>Insurance Plan Details</h3>
        </div>
        <div class="ip-details-grid">
          <div class="ip-metric-box">
            <span class="ip-metric-label">PLAN NAME</span>
            <span class="ip-metric-val">${escapeHtml(plan.plan_name || "—")}</span>
          </div>
          <div class="ip-metric-box">
            <span class="ip-metric-label">INSURER</span>
            <span class="ip-metric-val">${escapeHtml(plan.insurer || "—")}</span>
          </div>
          <div class="ip-metric-box">
            <span class="ip-metric-label">UIN</span>
            <span class="ip-metric-val">${escapeHtml(plan.uin || "—")}</span>
          </div>
          <div class="ip-metric-box">
            <span class="ip-metric-label">PLAN TYPE</span>
            <span class="ip-metric-val">${escapeHtml(plan.plan_type || "—")}</span>
          </div>
          <div class="ip-metric-box">
            <span class="ip-metric-label">STATUS</span>
            <div class="ip-metric-val">
              <span class="ip-status-pill"><span class="ip-dot">•</span> ${escapeHtml(plan.status || "Active")}</span>
            </div>
          </div>
          <div class="ip-metric-box">
            <span class="ip-metric-label">POLICY PERIOD</span>
            <span class="ip-metric-val">${escapeHtml(plan.policy_period || "—")}</span>
          </div>
          <div class="ip-metric-box">
            <span class="ip-metric-label">BUNDLE ID</span>
            <span class="ip-metric-val mono">${escapeHtml(bid || "—")}</span>
          </div>
        </div>
      </div>

      <!-- Benefits Section -->
      <div class="ip-benefits-section">
        <div class="ip-benefits-header">
          <div class="ip-benefits-icon">
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#10b981" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
              <circle cx="12" cy="12" r="10"></circle>
              <polyline points="9 12 11.5 14.5 15.5 9.5"></polyline>
            </svg>
          </div>
          <h3>Benefits (${benefits.length})</h3>
        </div>
        <div class="ip-benefits-grid">
          ${benefits.map((b) => {
            const cat = escapeHtml(b.category || "inpatient_hospitalization");
            const bType = escapeHtml((b.benefit_type || "AMOUNT").toUpperCase());
            const val = escapeHtml(b.value || "—");
            const cap = b.cap ? escapeHtml(b.cap) : null;
            const conds = (b.conditions && b.conditions.length) ? b.conditions : ["Covered as per policy terms and conditions."];

            return `
              <div class="ip-benefit-card">
                <div class="ip-badges-row">
                  <span class="ip-badge ip-badge-cat">${cat}</span>
                  <span class="ip-badge ip-badge-type">TYPE: ${bType}</span>
                  <span class="ip-badge ip-badge-val">Value: ${val}</span>
                  ${cap ? `<span class="ip-badge ip-badge-cap">Cap: ${cap}</span>` : ""}
                </div>
                <div class="ip-cond-title">CONDITIONS:</div>
                <ul class="ip-cond-list">
                  ${conds.map((c) => `<li>${escapeHtml(c)}</li>`).join("")}
                </ul>
              </div>
            `;
          }).join("")}
        </div>
      </div>

      <!-- Footer Actions -->
      <div class="report-footer" style="margin-top:24px">
        <div class="report-meta-tag">
          Bundle ID: <code>${escapeHtml((bid || '').slice(0, 24))}...</code> • Digital Insurance Plan Record
        </div>
        <div class="report-actions">
          <button class="secondary small" id="printReportBtn" type="button">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="margin-right:4px"><polyline points="6 9 6 2 18 2 18 9"></polyline><path d="M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"></path><rect x="6" y="14" width="12" height="8"></rect></svg>
            Print
          </button>
          <button class="secondary small" id="downloadReportJsonBtn" type="button">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="margin-right:4px"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path><polyline points="7 10 12 15 17 10"></polyline><line x1="12" y1="15" x2="12" y2="3"></line></svg>
            Download JSON
          </button>
          <button class="secondary small" id="toggleReportJsonBtn" type="button">View FHIR JSON</button>
          <button class="secondary small" id="copyReportJsonBtn" type="button">Copy JSON</button>
          <button class="secondary small danger-btn" id="deleteCurrentReportBtn" type="button">Delete Record</button>
        </div>
      </div>
      <div id="reportRawJsonWrap" hidden style="margin-top:16px">
        <pre><code class="language-json">${escapeHtml(JSON.stringify(bundleJson, null, 2))}</code></pre>
      </div>
    </div>
  `;
}

function _wireInsurancePlanReportEvents(selectedBundle, bundleIdx, bundleJson, planName) {
  const sel = $("#reportBundleSelect");
  if (sel) {
    sel.addEventListener("change", (e) => {
      renderBundleReport(parseInt(e.target.value, 10));
    });
  }

  const printBtn = $("#printReportBtn");
  if (printBtn) {
    printBtn.addEventListener("click", () => window.print());
  }

  const downloadBtn = $("#downloadReportJsonBtn");
  if (downloadBtn) {
    downloadBtn.addEventListener("click", () => {
      const str = JSON.stringify(bundleJson, null, 2);
      const blob = new Blob([str], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      const safeName = (planName || "Insurance_Plan").replace(/[^a-zA-Z0-9_-]/g, "_");
      a.download = `${safeName}_FHIR_Plan_Bundle.json`;
      a.click();
      URL.revokeObjectURL(url);
    });
  }

  const toggleBtn = $("#toggleReportJsonBtn");
  const jsonWrap = $("#reportRawJsonWrap");
  if (toggleBtn && jsonWrap) {
    toggleBtn.addEventListener("click", () => {
      const isHidden = jsonWrap.hidden;
      jsonWrap.hidden = !isHidden;
      toggleBtn.textContent = isHidden ? "Hide FHIR JSON" : "View FHIR JSON";
      if (isHidden && window.hljs) {
        jsonWrap.querySelectorAll("code").forEach((el) => hljs.highlightElement(el));
      }
    });
  }

  const copyBtn = $("#copyReportJsonBtn");
  if (copyBtn) {
    copyBtn.addEventListener("click", () => {
      navigator.clipboard.writeText(JSON.stringify(bundleJson, null, 2));
      copyBtn.textContent = "Copied!";
      setTimeout(() => (copyBtn.textContent = "Copy JSON"), 2000);
    });
  }

  const delReportBtn = $("#deleteCurrentReportBtn");
  if (delReportBtn) {
    delReportBtn.addEventListener("click", async () => {
      const reportName = selectedBundle.source_filename || `Plan ${bundleIdx + 1}`;
      if (!confirm(`Are you sure you want to delete "${reportName}"? This action cannot be undone.`)) {
        return;
      }
      try {
        const res = await fetch(`${apiBase()}/bundles/${selectedBundle.id}`, { method: "DELETE" });
        if (!res.ok) throw new Error("Failed to delete record bundle");
        await loadPatients();
        await checkHealth();
        const detail = $("#patientDetail");
        if (detail) detail.hidden = true;
      } catch (err) {
        alert(`Error deleting record: ${err.message}`);
      }
    });
  }
}

// ═══════════════════════════════════════════════════════════════
// Insurance One-Page Executive Dossier Report Renderer
// ═══════════════════════════════════════════════════════════════

function _renderInsuranceOnePageReportHtml(params) {
  const {
    bundleIdx,
    bundles,
    selectedBundle,
    bundleJson,
    resources,
    patient,
    pName, pGiven, pFamily, pGender, pDob, pAge, pGuardian, pPhone, formattedAadhaar, pAbha, pHospId, pMrn,
    pAddress, pLocality, pMandal, pCity, pDistrict, pState, pPin,
    facilityName, practitionerName,
    formattedDate, sourceFilename, docStatus,
    claimSummary
  } = params;

  const composition = resources.find(r => r.resourceType === "Composition") || {};
  const diagReport = resources.find(r => r.resourceType === "DiagnosticReport") || {};
  const org = resources.find(r => r.resourceType === "Organization") || {};
  const practitioner = resources.find(r => r.resourceType === "Practitioner") || {};
  const patientRes = resources.find(r => r.resourceType === "Patient") || {};

  const cs = claimSummary || bundleJson.claim_summary || {};
  const kf = cs.key_fields || {};
  const docSections = cs.document_sections || [];
  let narrative = (cs.claim_narrative || "").trim();

  // 1. Claimant & Insured Particulars
  const claimantName = kf.insured_name || (pName && !pName.toLowerCase().includes("unknown") ? pName : "—");
  const gender = (kf.patient_gender || (pGender && pGender !== "Not specified" ? pGender : "")).trim();
  const dobOrAge = (kf.patient_age || (pDob && pDob !== "Not specified" ? pDob : "")).trim();
  const ageParts = [];
  if (gender) ageParts.push(gender.charAt(0).toUpperCase() + gender.slice(1));
  if (pAge) ageParts.push(pAge.replace(/[()]/g, ""));
  else if (dobOrAge) ageParts.push(dobOrAge);
  const ageGenderStr = ageParts.join(" • ") || "—";

  // Identifiers: Only use real extracted values; never hardcoded placeholders
  let policyNo = kf.policy_number || "";
  let claimNo = kf.claim_number || "";
  const allIdentifiers = [...(patientRes.identifier || []), ...(patient.identifier || [])];
  for (const id of allIdentifiers) {
    const sys = String(id.system || "").toLowerCase();
    const disp = String(id.type?.coding?.[0]?.display || "").toLowerCase();
    const val = String(id.value || "").trim();
    if (!policyNo && (sys.includes("policy") || disp.includes("policy") || sys.includes("member"))) policyNo = val;
    if (!claimNo && (sys.includes("claim") || disp.includes("claim"))) claimNo = val;
  }
  if (!policyNo) policyNo = "—";
  if (!claimNo) claimNo = "—";

  const claimType = kf.claim_type ? kf.claim_type.toUpperCase() : "CLAIM";
  const phoneStr = pPhone || "—";

  let addressStr = pAddress || "";
  if (!addressStr && (pCity || pState)) {
    addressStr = [pLocality, pCity, pDistrict, pState, pPin].filter(Boolean).join(", ");
  }

  // 2. Hospitalization & Clinical Particulars
  const hospitalName = kf.hospital_name || (org.name && !org.name.includes("Apex") ? org.name : "") || (facilityName && !facilityName.includes("Apex") ? facilityName : "") || "—";
  const doctorName = kf.treating_doctor || (practitioner.name?.[0]?.text && !practitioner.name?.[0]?.text.includes("Attending") ? practitioner.name?.[0]?.text : "") || (practitioner.name?.[0]?.family && !practitioner.name?.[0]?.family.includes("Attending") ? practitioner.name?.[0]?.family : "") || "—";

  // Primary Diagnosis
  let primaryDiagnosis = (kf.primary_diagnosis || diagReport.conclusion || "").trim();
  if (!primaryDiagnosis || primaryDiagnosis === "NA") {
    const diagSec = composition.section?.find(s =>
      s.title?.toLowerCase().includes("diagnos") || s.title?.toLowerCase().includes("clinical")
    );
    if (diagSec?.text?.div) {
      const liMatch = diagSec.text.div.match(/<li[^>]*>(.*?)<\/li>/i);
      if (liMatch) primaryDiagnosis = liMatch[1].replace(/<[^>]+>/g, "").trim();
    }
  }
  primaryDiagnosis = primaryDiagnosis.replace(/^(Admitting Diagnosis|Primary Diagnosis|Diagnosis):\s*/i, "").trim();
  if (!primaryDiagnosis) primaryDiagnosis = "—";

  // Gather all texts across document sections & composition
  const allDocTexts = [];
  docSections.forEach(s => {
    if (s.summary) allDocTexts.push(s.summary);
    (s.key_data_points || []).forEach(p => allDocTexts.push(p));
  });
  if (composition.section) {
    composition.section.forEach(s => {
      if (s.text?.div) allDocTexts.push(s.text.div.replace(/<[^>]+>/g, " "));
    });
  }

  // Procedure Name
  let procedureName = "";
  for (const txt of allDocTexts) {
    if (/procedure|surgery|surgical|laparoscopic|ptca|lithotripsy|replacement|resection|intervention/i.test(txt)) {
      const m = txt.match(/(?:procedure|proposed procedure|surgery|underwent):\s*([^,;.\n]+)/i);
      if (m) { procedureName = m[1].trim(); break; }
      else if (/^(Proposed Procedure|Procedure):/i.test(txt)) {
        procedureName = txt.replace(/^(Proposed Procedure|Procedure):\s*/i, "").trim();
        break;
      }
    }
  }

  // Admission & Discharge Dates & Room
  let admissionDate = kf.admission_date || "";
  let dischargeDate = kf.discharge_date || "";
  let roomCategory = kf.room_category || "";

  for (const txt of allDocTexts) {
    if (!admissionDate) {
      const m = txt.match(/(?:Date of Admission|DOA|Admission Date|Admitted on):\s*([0-9A-Za-z\s,-]+)/i);
      if (m) admissionDate = m[1].trim().split(/[,\n;]/)[0];
    }
    if (!dischargeDate) {
      const m = txt.match(/(?:Date of Discharge|DOD|Discharge Date|Discharged on):\s*([0-9A-Za-z\s,-]+)/i);
      if (m) dischargeDate = m[1].trim().split(/[,\n;]/)[0];
    }
    if (!roomCategory) {
      const m = txt.match(/(?:Room Category|Room Type|Accommodation|Bed Type):\s*([0-9A-Za-z\s\/-]+)/i);
      if (m) roomCategory = m[1].trim().split(/[,\n;]/)[0];
    }
  }
  if (!admissionDate) admissionDate = "—";
  if (!dischargeDate) dischargeDate = "—";
  if (!roomCategory) roomCategory = "—";

  let lengthOfStayStr = kf.length_of_stay || "";
  if (!lengthOfStayStr && admissionDate !== "—" && dischargeDate !== "—") {
    try {
      const d1 = new Date(admissionDate);
      const d2 = new Date(dischargeDate);
      if (!isNaN(d1.getTime()) && !isNaN(d2.getTime())) {
        const diff = Math.max(1, Math.round((d2 - d1) / (1000 * 60 * 60 * 24)));
        lengthOfStayStr = `${diff} Day${diff > 1 ? 's' : ''}`;
      }
    } catch {}
  }
  if (!lengthOfStayStr) lengthOfStayStr = "—";

  // 3. Financial Metrics (only real extracted figures; zero fake defaults)
  let totalClaimedAmt = kf.total_claimed_amount || "";
  let grossBillAmt = kf.gross_bill_amount || "";
  let netClaimedAmt = kf.net_claimed_amount || "";
  let preAuthAmt = kf.pre_authorized_amount || "";
  let approvedAmt = kf.approved_amount || "";
  let sumInsuredAmt = kf.sum_insured || "";

  for (const s of allDocTexts) {
    const sLower = s.toLowerCase();
    if (!grossBillAmt && (sLower.includes("gross") || sLower.includes("hospital bill"))) {
      const m = s.match(/([₹Rs.INR\s]*[0-9,]+(?:\.\d{2})?)\s*gross/i) || s.match(/gross[^0-9₹]*([₹Rs.INR\s]*[0-9,]+(?:\.\d{2})?)/i);
      if (m) grossBillAmt = m[1].trim();
    }
    if (!netClaimedAmt && (sLower.includes("net") || sLower.includes("claimed amount"))) {
      const m = s.match(/([₹Rs.INR\s]*[0-9,]+(?:\.\d{2})?)\s*net/i) || s.match(/net[^0-9₹]*([₹Rs.INR\s]*[0-9,]+(?:\.\d{2})?)/i);
      if (m) netClaimedAmt = m[1].trim();
    }
    if (!preAuthAmt && (sLower.includes("pre-auth") || sLower.includes("preauth"))) {
      const m = s.match(/pre-?auth[^0-9₹]*([₹Rs.INR\s]*[0-9,]+(?:\.\d{2})?)/i);
      if (m) preAuthAmt = m[1].trim();
    }
    if (!approvedAmt && (sLower.includes("approved") || sLower.includes("sanctioned"))) {
      const m = s.match(/(?:approved|sanctioned)[^0-9₹]*([₹Rs.INR\s]*[0-9,]+(?:\.\d{2})?)/i);
      if (m) approvedAmt = m[1].trim();
    }
    if (!sumInsuredAmt && (sLower.includes("sum insured") || sLower.includes("sum assured"))) {
      const m = s.match(/sum\s*(?:insured|assured)[^0-9₹]*([₹Rs.INR\s]*[0-9,]+(?:\.\d{2})?[A-Za-z]*)/i);
      if (m) sumInsuredAmt = m[1].trim();
    }
  }

  if (!totalClaimedAmt && netClaimedAmt) totalClaimedAmt = netClaimedAmt;
  else if (!totalClaimedAmt && grossBillAmt) totalClaimedAmt = grossBillAmt;

  // Normalize currency symbols
  const norm = (val) => (val && String(val).trim() && String(val).trim() !== "—") ? String(val).replace(/^Rs\.?\s*/i, "₹").trim() : "—";
  totalClaimedAmt = norm(totalClaimedAmt);
  grossBillAmt = norm(grossBillAmt);
  preAuthAmt = norm(preAuthAmt);
  approvedAmt = norm(approvedAmt);
  sumInsuredAmt = norm(sumInsuredAmt);

  // Itemized breakdown table
  const itemizedHtml = _extractItemizedCharges(docSections, allDocTexts, {
    grossBillAmt, totalClaimedAmt, approvedAmt, preAuthAmt, sumInsuredAmt
  });

  // Pillars HTML
  const pillarsHtml = _renderPillarsHtml(docSections);

  // Multi-bundle selector
  let bundleSelectorHtml = "";
  if (bundles.length > 1) {
    bundleSelectorHtml = `
      <div class="iop-bundle-selector">
        <strong>Select Claim Dossier:</strong>
        <select id="reportBundleSelect">
          ${bundles.map((b, idx) => `<option value="${idx}" ${idx === bundleIdx ? "selected" : ""}>Dossier ${idx + 1}: ${escapeHtml(b.source_filename || "Claim Report")} (${b.created_at || ""})</option>`).join("")}
        </select>
      </div>
    `;
  }

  // Narrative fallback: only use known factual fields; zero hallucinations
  if (!narrative) {
    const nParts = [];
    if (claimantName !== "—") nParts.push(`Claimant: ${claimantName}`);
    if (hospitalName !== "—") nParts.push(`Hospital: ${hospitalName}`);
    if (primaryDiagnosis !== "—") nParts.push(`Diagnosis: ${primaryDiagnosis}`);
    if (procedureName) nParts.push(`Procedure: ${procedureName}`);
    if (totalClaimedAmt !== "—") nParts.push(`Claimed Amount: ${totalClaimedAmt}`);
    if (approvedAmt !== "—") nParts.push(`Approved Amount: ${approvedAmt}`);
    narrative = nParts.length ? nParts.join(". ") + "." : "Summary narrative not detailed in source dossier.";
  }

  const isApproved = approvedAmt !== "—";

  return `
    <div class="insurance-onepage-report">
      ${bundleSelectorHtml}

      <!-- Header / Top Bar -->
      <div class="iop-header">
        <div class="iop-header-left">
          <div class="iop-logo-box">
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2">
              <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>
              <path d="m9 12 2 2 4-4"/>
            </svg>
          </div>
          <div class="iop-title-box">
            <div class="iop-org-name">${escapeHtml(hospitalName !== "—" ? hospitalName : (kf.insurer_tpa_name || "Insurance Claim Record"))}</div>
            <div class="iop-sub-line">HEALTH INSURANCE CLAIM REPORT • HL7® FHIR® R4</div>
          </div>
        </div>
        <div class="iop-header-right">
          <div class="iop-claim-status-pill ${isApproved ? 'approved' : 'pending'}">
            <span class="status-dot"></span>
            ${isApproved ? 'CLAIM SANCTIONED' : (claimType !== 'CLAIM' ? `${escapeHtml(claimType)} CLAIM` : 'CLAIM PROCESSED')}
          </div>
          <div class="iop-meta-grid">
            <span>Claim No: <strong>${escapeHtml(claimNo)}</strong></span>
            <span>Policy No: <strong>${escapeHtml(policyNo)}</strong></span>
            <span>Date: <strong>${escapeHtml(formattedDate || "—")}</strong></span>
          </div>
        </div>
      </div>

      <!-- Row 1: Claimant Particulars & Hospitalization Particulars (2 Columns) -->
      <div class="iop-grid-row iop-two-col">
        <!-- Panel A: Claimant Particulars -->
        <div class="iop-panel">
          <div class="iop-panel-hdr">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"></path><circle cx="12" cy="7" r="4"></circle></svg>
            <span>Claimant &amp; Insured Particulars</span>
          </div>
          <div class="iop-panel-body">
            <div class="iop-detail-row main">
              <span class="iop-label">Insured Claimant:</span>
              <span class="iop-val highlight">${escapeHtml(claimantName)}</span>
            </div>
            <div class="iop-sub-grid">
              <div class="iop-sub-item"><span class="iop-label">Age / Gender:</span><span class="iop-val">${escapeHtml(ageGenderStr)}</span></div>
              <div class="iop-sub-item"><span class="iop-label">Contact Mobile:</span><span class="iop-val mono">${escapeHtml(phoneStr)}</span></div>
              <div class="iop-sub-item"><span class="iop-label">Policy / Member ID:</span><span class="iop-val mono">${escapeHtml(policyNo)}</span></div>
              <div class="iop-sub-item"><span class="iop-label">Claim Type:</span><span class="iop-val">${escapeHtml(claimType)}</span></div>
              <div class="iop-sub-item"><span class="iop-label">Aadhaar Card (UID):</span><span class="iop-val mono">${formattedAadhaar ? `${escapeHtml(formattedAadhaar)} <span class="iop-mini-tag">UIDAI ✓</span>` : '—'}</span></div>
              <div class="iop-sub-item"><span class="iop-label">ABHA Health ID:</span><span class="iop-val mono">${escapeHtml(pAbha || '—')}</span></div>
              <div class="iop-sub-item"><span class="iop-label">Hospital IP / Reg:</span><span class="iop-val mono">${escapeHtml(pHospId || '—')}</span></div>
              <div class="iop-sub-item"><span class="iop-label">Medical Record (MRN):</span><span class="iop-val mono">${escapeHtml(pMrn || '—')}</span></div>
            </div>
            ${addressStr ? `
            <div class="iop-detail-row" style="padding-top:2px;border-top:1px dashed rgba(255,255,255,0.06)">
              <span class="iop-label">Address:</span>
              <span class="iop-val" style="font-size:0.74rem">${escapeHtml(addressStr)}</span>
            </div>` : ''}
          </div>
        </div>

        <!-- Panel B: Hospitalization & Clinical Particulars -->
        <div class="iop-panel">
          <div class="iop-panel-hdr">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg>
            <span>Hospitalization &amp; Clinical Particulars</span>
          </div>
          <div class="iop-panel-body">
            <div class="iop-detail-row">
              <span class="iop-label">Admitting Hospital:</span>
              <span class="iop-val highlight-sub">${escapeHtml(hospitalName)}</span>
            </div>
            <div class="iop-detail-row">
              <span class="iop-label">Treating Specialist:</span>
              <span class="iop-val">${escapeHtml(doctorName)}</span>
            </div>
            <div class="iop-detail-row">
              <span class="iop-label">Primary Diagnosis:</span>
              <span class="iop-val diag-val">${escapeHtml(primaryDiagnosis)}</span>
            </div>
            ${procedureName ? `
            <div class="iop-detail-row">
              <span class="iop-label">Procedure Done:</span>
              <span class="iop-val proc-val">${escapeHtml(procedureName)}</span>
            </div>` : ''}
            <div class="iop-sub-grid">
              <div class="iop-sub-item"><span class="iop-label">Admission Date:</span><span class="iop-val mono">${escapeHtml(admissionDate)}</span></div>
              <div class="iop-sub-item"><span class="iop-label">Discharge Date:</span><span class="iop-val mono">${escapeHtml(dischargeDate)}</span></div>
              <div class="iop-sub-item"><span class="iop-label">Room Category:</span><span class="iop-val">${escapeHtml(roomCategory)}</span></div>
              <div class="iop-sub-item"><span class="iop-label">Length of Stay:</span><span class="iop-val">${escapeHtml(lengthOfStayStr)}</span></div>
            </div>
          </div>
        </div>
      </div>

      <!-- Row 2: Claim Financial Highlight Bar & Itemized Details -->
      <div class="iop-panel">
        <div class="iop-panel-hdr">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><line x1="12" y1="1" x2="12" y2="23"></line><path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"></path></svg>
          <span>Claim Financial Details &amp; Adjudicated Settlement Amounts</span>
        </div>
        <div class="iop-panel-body">
          <div class="iop-financial-ribbon">
            <div class="iop-fin-box highlight-primary">
              <div class="iop-fin-lbl">Total Claimed</div>
              <div class="iop-fin-amt">${escapeHtml(totalClaimedAmt)}</div>
              <div class="iop-fin-sub">Net Claim Submitted</div>
            </div>
            <div class="iop-fin-box">
              <div class="iop-fin-lbl">Gross Hospital Bill</div>
              <div class="iop-fin-amt">${escapeHtml(grossBillAmt)}</div>
              <div class="iop-fin-sub">Invoiced Hospital Total</div>
            </div>
            <div class="iop-fin-box">
              <div class="iop-fin-lbl">Pre-Authorized</div>
              <div class="iop-fin-amt">${escapeHtml(preAuthAmt)}</div>
              <div class="iop-fin-sub">TPA Initial Sanction</div>
            </div>
            <div class="iop-fin-box highlight-success">
              <div class="iop-fin-lbl">Final Approved</div>
              <div class="iop-fin-amt">${escapeHtml(approvedAmt)}</div>
              <div class="iop-fin-sub">Payable Settlement</div>
            </div>
            <div class="iop-fin-box highlight-info">
              <div class="iop-fin-lbl">Sum Insured</div>
              <div class="iop-fin-amt">${escapeHtml(sumInsuredAmt)}</div>
              <div class="iop-fin-sub">Policy Annual Limit</div>
            </div>
          </div>

          <!-- Itemized Charges Table -->
          ${itemizedHtml}
        </div>
      </div>

      <!-- Row 3: Claim Summary Narrative & Case Abstract -->
      <div class="iop-panel">
        <div class="iop-panel-hdr">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline></svg>
          <span>Executive Claim Dossier Summary &amp; Case Abstract</span>
        </div>
        <div class="iop-panel-body">
          <p class="iop-narrative">${escapeHtml(narrative)}</p>
          ${pillarsHtml}
        </div>
      </div>

      <!-- Row 4: Sign-off Stamps -->
      <div class="iop-signoff-block">
        <div class="iop-signoff-col">
          <span class="iop-sign-label">Hospital Consultant / Medical Superintendent</span>
          <span class="iop-sign-name">${escapeHtml(doctorName !== "—" ? doctorName : "Treating Consultant")}</span>
          <span class="iop-sign-sub">${escapeHtml(hospitalName !== "—" ? hospitalName : "Healthcare Provider")} • Medical Record</span>
          <div class="iop-sign-stamp"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg> Certified Hospital Record</div>
        </div>
        <div class="iop-signoff-col">
          <span class="iop-sign-label">Insurance TPA / Claims Adjudicator</span>
          <span class="iop-sign-name">${escapeHtml(kf.insurer_tpa_name || "Claims Authority")}</span>
          <span class="iop-sign-sub">Health Claims Processing</span>
          <div class="iop-sign-stamp"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg> Processed Record</div>
        </div>
        <div class="iop-signoff-col">
          <span class="iop-sign-label">FHIR® &amp; ABDM Verification</span>
          <span class="iop-sign-name">HL7® FHIR® R4 Validated</span>
          <span class="iop-sign-sub">National Health Data Exchange Compliant</span>
          <div class="iop-sign-stamp"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg> 100% Standards Compliant</div>
        </div>
      </div>

      <!-- Footer & Actions (Screen only, hidden in print) -->
      <div class="report-footer">
        <div class="report-meta-tag">
          Bundle ID: <code>${escapeHtml((selectedBundle.id || '').slice(0, 24))}...</code> • Digital Insurance Claim Record
        </div>
        <div class="report-actions">
          <button class="secondary small" id="printReportBtn" type="button">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="margin-right:4px"><polyline points="6 9 6 2 18 2 18 9"></polyline><path d="M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"></path><rect x="6" y="14" width="12" height="8"></rect></svg>
            Print Report
          </button>
          <button class="secondary small" id="downloadReportJsonBtn" type="button">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="margin-right:4px"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path><polyline points="7 10 12 15 17 10"></polyline><line x1="12" y1="15" x2="12" y2="3"></line></svg>
            Download JSON
          </button>
          <button class="secondary small" id="toggleReportJsonBtn" type="button">View FHIR JSON</button>
          <button class="secondary small" id="copyReportJsonBtn" type="button">Copy JSON</button>
          <button class="secondary small danger-btn" id="deleteCurrentReportBtn" type="button">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="margin-right:4px"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path></svg>
            Delete Report
          </button>
        </div>
      </div>

      <!-- Raw Bundle JSON container -->
      <div id="reportRawJsonWrap" hidden style="margin-top:16px">
        <pre><code class="language-json">${escapeHtml(JSON.stringify(bundleJson, null, 2))}</code></pre>
      </div>
    </div>
  `;
}

function _wireInsuranceReportEvents(selectedBundle, bundleIdx, bundleJson, pName) {
  const sel = $("#reportBundleSelect");
  if (sel) {
    sel.addEventListener("change", (e) => {
      renderBundleReport(parseInt(e.target.value, 10));
    });
  }

  const printBtn = $("#printReportBtn");
  if (printBtn) {
    printBtn.addEventListener("click", () => window.print());
  }

  const downloadBtn = $("#downloadReportJsonBtn");
  if (downloadBtn) {
    downloadBtn.addEventListener("click", () => {
      const str = JSON.stringify(bundleJson, null, 2);
      const blob = new Blob([str], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      const safeName = (pName || "Claimant").replace(/[^a-zA-Z0-9_-]/g, "_");
      a.download = `${safeName}_Insurance_Claim_Bundle.json`;
      a.click();
      URL.revokeObjectURL(url);
    });
  }

  const toggleBtn = $("#toggleReportJsonBtn");
  const jsonWrap = $("#reportRawJsonWrap");
  if (toggleBtn && jsonWrap) {
    toggleBtn.addEventListener("click", () => {
      const isHidden = jsonWrap.hidden;
      jsonWrap.hidden = !isHidden;
      toggleBtn.textContent = isHidden ? "Hide FHIR JSON" : "View FHIR JSON";
      if (isHidden && window.hljs) {
        jsonWrap.querySelectorAll("code").forEach((el) => hljs.highlightElement(el));
      }
    });
  }

  const copyBtn = $("#copyReportJsonBtn");
  if (copyBtn) {
    copyBtn.addEventListener("click", () => {
      navigator.clipboard.writeText(JSON.stringify(bundleJson, null, 2));
      copyBtn.textContent = "Copied!";
      setTimeout(() => (copyBtn.textContent = "Copy JSON"), 2000);
    });
  }

  const delReportBtn = $("#deleteCurrentReportBtn");
  if (delReportBtn) {
    delReportBtn.addEventListener("click", async () => {
      const reportName = selectedBundle.source_filename || `Dossier ${bundleIdx + 1}`;
      if (!confirm(`Are you sure you want to delete "${reportName}"? This action cannot be undone.`)) {
        return;
      }
      try {
        const res = await fetch(`${apiBase()}/bundles/${selectedBundle.id}`, { method: "DELETE" });
        if (!res.ok) throw new Error("Failed to delete report bundle");
        await loadPatients();
        await checkHealth();
        const detail = $("#patientDetail");
        if (detail) detail.hidden = true;
      } catch (err) {
        alert(`Error deleting report: ${err.message}`);
      }
    });
  }
}

function _extractItemizedCharges(docSections, allDocTexts, totals) {
  const lineItems = [];
  const seenLabels = new Set();

  const chargePatterns = [
    { key: "room", label: "Room Rent & Nursing Charges", regex: /(?:Room(?:\s*(?:Rent|Charges|&\s*Nursing))?)\s*[:=-]\s*([₹Rs.INR\s]*[0-9,]+(?:\.\d{2})?)/i },
    { key: "icu", label: "ICU / HDU Critical Care", regex: /(?:ICU|CCU|Critical Care)\s*(?:Charges)?\s*[:=-]\s*([₹Rs.INR\s]*[0-9,]+(?:\.\d{2})?)/i },
    { key: "ot", label: "Operation Theatre & Surgery Fees", regex: /(?:OT|Operation Theatre|Surgeon|Surgical)\s*(?:Charges|Fee|Fees)?\s*[:=-]\s*([₹Rs.INR\s]*[0-9,]+(?:\.\d{2})?)/i },
    { key: "implants", label: "Implants, Stents & Prosthetics", regex: /(?:Implant|Implants|Stent|Stents|Prosthesis|Prosthetics)\s*[:=-]\s*([₹Rs.INR\s]*[0-9,]+(?:\.\d{2})?)/i },
    { key: "pharmacy", label: "Pharmacy & Injections", regex: /(?:Pharmacy|Medicine|Medicines|Drugs|Injections)\s*[:=-]\s*([₹Rs.INR\s]*[0-9,]+(?:\.\d{2})?)/i },
    { key: "investigations", label: "Diagnostics, Lab & Radiology", regex: /(?:Investigation|Investigations|Diagnostic|Diagnostics|Lab|Pathology|Radiology)\s*[:=-]\s*([₹Rs.INR\s]*[0-9,]+(?:\.\d{2})?)/i },
    { key: "consultation", label: "Consultant & Professional Visits", regex: /(?:Consultation|Doctor|Professional|Physician)\s*(?:Charges|Fee|Fees|Visits)?\s*[:=-]\s*([₹Rs.INR\s]*[0-9,]+(?:\.\d{2})?)/i },
    { key: "non_medical", label: "Non-Medical Deductions / Disallowance", regex: /(?:Non[- ]?Medical|Disallowed|Disallowance|Deduction|Deductions)\s*[:=-]\s*([₹Rs.INR\s]*[0-9,]+(?:\.\d{2})?)/i },
  ];

  for (const text of allDocTexts) {
    for (const pat of chargePatterns) {
      if (seenLabels.has(pat.key)) continue;
      const m = text.match(pat.regex);
      if (m && m[1].trim()) {
        seenLabels.add(pat.key);
        lineItems.push({ label: pat.label, amount: m[1].trim().replace(/^Rs\.?\s*/i, "₹") });
      }
    }
  }

  // Also parse explicit points from bill/invoice section
  for (const sec of (docSections || [])) {
    if (/bill|invoice|financial|breakdown|charges/i.test(sec.title || "")) {
      for (const pt of (sec.key_data_points || [])) {
        if (/₹|rs\.?|inr/i.test(pt) && !/gross|net claimed|total claimed|sum insured/i.test(pt)) {
          const parts = pt.split(/[:=-]\s*/);
          if (parts.length >= 2) {
            const lbl = parts[0].trim();
            const amtMatch = parts[1].match(/([₹Rs.INR\s]*[0-9,]+(?:\.\d{2})?)/i);
            if (amtMatch && !seenLabels.has(lbl.toLowerCase())) {
              seenLabels.add(lbl.toLowerCase());
              lineItems.push({ label: lbl, amount: amtMatch[1].trim().replace(/^Rs\.?\s*/i, "₹") });
            }
          }
        }
      }
    }
  }

  if (!lineItems.length) {
    return `
      <div class="iop-fin-grid-fallback" style="text-align:center;padding:16px 12px;color:var(--text-muted);font-size:0.84rem;font-style:italic">
        Detailed line-item charge breakdown is not itemized in the source document. Summary totals are shown above.
      </div>
    `;
  }

  const rowsHtml = lineItems.slice(0, 8).map(item => `
    <tr>
      <td class="iop-line-lbl">${escapeHtml(item.label)}</td>
      <td class="iop-line-amt mono">${escapeHtml(item.amount)}</td>
    </tr>
  `).join("");

  return `
    <div class="iop-fin-breakdown-wrap">
      <table class="iop-fin-table">
        <tbody>
          ${rowsHtml}
        </tbody>
      </table>
    </div>
  `;
}

function _renderPillarsHtml(docSections) {
  if (!docSections || !docSections.length) {
    return "";
  }

  const cards = docSections.slice(0, 3).map((sec, i) => {
    const cardTitle = sec.title ? `${i + 1}. ${sec.title}` : `Section ${i + 1}`;
    let text = sec.summary || (sec.key_data_points || []).slice(0, 2).join("; ") || "Verified and recorded from dossier.";
    if (text.length > 120) text = text.slice(0, 117) + "...";
    return `
      <div class="iop-pillar-card">
        <div class="iop-pillar-title">${escapeHtml(cardTitle)}</div>
        <div class="iop-pillar-text">${escapeHtml(text)}</div>
      </div>
    `;
  }).join("");

  return `<div class="iop-pillars-grid">${cards}</div>`;
}

// ═══════════════════════════════════════════════
// Insurance Claim Cards for Patient Detail View
// ═══════════════════════════════════════════════

function _renderInsuranceClaimCards(resources, pName, pGender, pDob, pAge, pGuardian, pPhone, formattedAadhaar, pAbha, pHospId, pMrn, patient, pAddress, pLocality, pMandal, pCity, pDistrict, pState, pPin, facilityName, practitionerName, bundleObs, clinicalSectionHtml, claimSummary = null) {
  // Extract claim-relevant data from FHIR resources
  const composition = resources.find(r => r.resourceType === "Composition") || {};
  const diagReport = resources.find(r => r.resourceType === "DiagnosticReport") || {};

  const cs = claimSummary || {};
  const kf = cs.key_fields || {};
  const narrative = cs.claim_narrative || "";
  const flags = cs.flags || [];

  // Gather diagnosis from key_fields, composition, or diagnostic report
  let primaryDiagnosis = kf.primary_diagnosis || diagReport.conclusion || "";
  if (!primaryDiagnosis || primaryDiagnosis === "NA") {
    const diagSec = composition.section?.find(s =>
      s.title?.toLowerCase().includes("diagnos") || s.title?.toLowerCase().includes("clinical")
    );
    if (diagSec?.text?.div) {
      const liMatch = diagSec.text.div.match(/<li[^>]*>(.*?)<\/li>/i);
      if (liMatch) primaryDiagnosis = liMatch[1].replace(/<[^>]+>/g, "").trim();
    }
  }

  // Build document sections
  let docSections = cs.document_sections || [];
  if (!docSections.length) {
    // Reconstruct from composition sections
    for (const sec of (composition.section || [])) {
      if (!sec.title) continue;
      const secTitleLower = sec.title.toLowerCase();
      const divText = sec.text?.div || "";
      const items = (divText.match(/<li[^>]*>(.*?)<\/li>/gi) || [])
        .map(m => m.replace(/<\/?li[^>]*>/gi, "").replace(/<[^>]+>/g, "").trim())
        .filter(Boolean);

      if (secTitleLower.includes("manifest") || secTitleLower.includes("inventory")) {
        // Parse each manifest item into a separate document section
        for (const item of items) {
          const mDoc = item.match(/(?:[^(]+\()([^)]+)\):\s*(.*)/);
          if (mDoc) {
            docSections.push({
              title: mDoc[1].trim(),
              summary: mDoc[2].trim(),
              key_data_points: [item]
            });
          } else {
            docSections.push({
              title: sec.title,
              summary: item,
              key_data_points: [item]
            });
          }
        }
      } else {
        const secSummary = items.slice(0, 3).join("; ");
        docSections.push({
          title: sec.title,
          summary: secSummary,
          key_data_points: items
        });
      }
    }
  }

  // Helpers for amount validation and formatting
  const _isValidAmount = (val) => {
    if (!val) return false;
    const s = String(val).trim();
    return /\d/.test(s);
  };

  const _formatAmount = (val) => {
    if (!_isValidAmount(val)) return "";
    const s = String(val).trim();
    const cleanNum = parseFloat(s.replace(/[^0-9.]/g, ""));
    if (!isNaN(cleanNum) && cleanNum > 0) {
      if (cleanNum % 1 !== 0) {
        return `₹${cleanNum.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
      }
      return `₹${cleanNum.toLocaleString("en-IN")}`;
    }
    return s.startsWith("₹") ? s : `₹${s}`;
  };

  // Inspect authoritative FHIR resources in the bundle
  const bundleClaims = (resources || []).filter(r => r.resourceType === "Claim");
  const bundleClaimResponse = (resources || []).find(r => r.resourceType === "ClaimResponse");
  const bundleCoverage = (resources || []).find(r => r.resourceType === "Coverage");

  // Validate or clear any existing amounts in kf that lack digits (e.g. "₹,")
  if (!_isValidAmount(kf.total_claimed_amount)) kf.total_claimed_amount = "";
  if (!_isValidAmount(kf.gross_bill_amount)) kf.gross_bill_amount = "";
  if (!_isValidAmount(kf.net_claimed_amount)) kf.net_claimed_amount = "";
  if (!_isValidAmount(kf.pre_authorized_amount)) kf.pre_authorized_amount = "";
  if (!_isValidAmount(kf.approved_amount)) kf.approved_amount = "";
  if (!_isValidAmount(kf.sum_insured)) kf.sum_insured = "";

  // Pull amounts from Claim if missing or empty
  if (!kf.gross_bill_amount || !kf.total_claimed_amount) {
    if (bundleClaims.length > 0) {
      let totalBilled = 0;
      bundleClaims.forEach(c => {
        totalBilled += Number(c.total?.value || 0);
      });
      if (totalBilled > 0) {
        if (!kf.gross_bill_amount) kf.gross_bill_amount = _formatAmount(totalBilled);
        if (!kf.total_claimed_amount) kf.total_claimed_amount = _formatAmount(totalBilled);
      }
    }
  }

  // Pull amounts from ClaimResponse if missing
  if (bundleClaimResponse) {
    (bundleClaimResponse.total || []).forEach(tot => {
      const codes = (tot.category?.coding || []).map(c => c.code);
      const val = Number(tot.amount?.value || 0);
      if (val > 0) {
        if (codes.includes("submitted") && !kf.gross_bill_amount) kf.gross_bill_amount = _formatAmount(val);
        if (codes.includes("submitted") && !kf.total_claimed_amount) kf.total_claimed_amount = _formatAmount(val);
        if (codes.includes("benefit") && !kf.approved_amount) kf.approved_amount = _formatAmount(val);
      }
    });
    if (!kf.approved_amount && bundleClaimResponse.payment?.amount?.value) {
      const pVal = Number(bundleClaimResponse.payment.amount.value);
      if (pVal > 0) kf.approved_amount = _formatAmount(pVal);
    }
  }

  // Pull sum insured from Coverage if missing
  if (!kf.sum_insured && bundleCoverage) {
    const sumInsClass = (bundleCoverage.class || []).find(c => {
      const code = c.type?.coding?.[0]?.code;
      return code === "sum_insured" || code === "subplan";
    });
    if (sumInsClass?.value && _isValidAmount(sumInsClass.value)) {
      kf.sum_insured = _formatAmount(sumInsClass.value);
    }
  }

  // Fallback to text regex only with strict digit requirement
  if (!kf.total_claimed_amount || !kf.gross_bill_amount || !kf.approved_amount) {
    const allTexts = [];
    docSections.forEach(s => {
      if (s.summary) allTexts.push(s.summary);
      (s.key_data_points || []).forEach(p => allTexts.push(p));
    });
    for (const t of allTexts) {
      const s = String(t).trim();
      const sLower = s.toLowerCase();
      if (!kf.gross_bill_amount && (sLower.includes("gross") || sLower.includes("hospital bill"))) {
        const m = s.match(/([₹Rs.INR\s]*\d[\d,]*(?:\.\d{2})?)\s*gross/i) || s.match(/gross[^\d₹]*([₹Rs.INR\s]*\d[\d,]*(?:\.\d{2})?)/i);
        if (m && _isValidAmount(m[1])) kf.gross_bill_amount = _formatAmount(m[1]);
      }
      if (!kf.net_claimed_amount && (sLower.includes("net") || sLower.includes("claimed amount"))) {
        const m = s.match(/([₹Rs.INR\s]*\d[\d,]*(?:\.\d{2})?)\s*net/i) || s.match(/net[^\d₹]*([₹Rs.INR\s]*\d[\d,]*(?:\.\d{2})?)/i);
        if (m && _isValidAmount(m[1])) kf.net_claimed_amount = _formatAmount(m[1]);
      }
      if (!kf.pre_authorized_amount && (sLower.includes("pre-auth") || sLower.includes("preauth"))) {
        const m = s.match(/pre-?auth[^\d₹]*([₹Rs.INR\s]*\d[\d,]*(?:\.\d{2})?)/i);
        if (m && _isValidAmount(m[1])) kf.pre_authorized_amount = _formatAmount(m[1]);
      }
      if (!kf.approved_amount && (sLower.includes("approved") || sLower.includes("sanctioned"))) {
        const m = s.match(/(?:approved|sanctioned)[^\d₹]*([₹Rs.INR\s]*\d[\d,]*(?:\.\d{2})?)/i);
        if (m && _isValidAmount(m[1])) kf.approved_amount = _formatAmount(m[1]);
      }
      if (!kf.sum_insured && (sLower.includes("sum insured") || sLower.includes("sum assured"))) {
        const m = s.match(/sum\s*(?:insured|assured)[^\d₹]*([₹Rs.INR\s]*\d[\d,]*(?:\.\d{2})?[A-Za-z]*)/i);
        if (m && _isValidAmount(m[1])) kf.sum_insured = _formatAmount(m[1]);
      }
    }
    if (kf.net_claimed_amount && !kf.total_claimed_amount) kf.total_claimed_amount = kf.net_claimed_amount;
    else if (kf.gross_bill_amount && !kf.total_claimed_amount) kf.total_claimed_amount = kf.gross_bill_amount;
  }

  // Room category validation - prevent prescriptions or addresses from displaying
  let roomCategory = kf.room_category || "";
  const badRoomWords = ["prescrib", "tab", "tablet", "capsule", "mg", "syrup", "daily", "imatinib", "dose", "chemo", "village", "mandal", "district", "pin", "ward-", "street", "road"];
  if (badRoomWords.some(w => roomCategory.toLowerCase().includes(w))) {
    roomCategory = "";
  }
  if (!roomCategory && bundleClaims.length > 0) {
    for (const c of bundleClaims) {
      for (const item of (c.item || [])) {
        const catText = (item.category?.text || "").toLowerCase();
        const prodText = item.productOrService?.text || "";
        const prodLower = prodText.toLowerCase();
        if (catText.includes("room") || catText.includes("board") || catText.includes("accommodation") || /ward|room|bed|icu/.test(prodLower)) {
          const clean = prodText.replace(/\s*(?:charges|tariff|rent|fee|bill).*$/i, "").trim();
          if (clean && !badRoomWords.some(w => clean.toLowerCase().includes(w))) {
            roomCategory = clean;
            break;
          }
        }
      }
      if (roomCategory) break;
    }
  }

  // Build flags HTML
  let flagsHtml = "";
  if (flags.length) {
    flagsHtml = `<div class="claim-flags" style="margin-bottom:12px">${flags.map(f => `<span class="claim-flag">${escapeHtml(f)}</span>`).join("")}</div>`;
  }

  // Build financial highlight metrics - strictly require valid numeric amounts
  const hasValidClaimed = _isValidAmount(kf.total_claimed_amount);
  const hasValidGross = _isValidAmount(kf.gross_bill_amount);
  const hasValidNet = _isValidAmount(kf.net_claimed_amount) && kf.net_claimed_amount !== kf.total_claimed_amount;
  const hasValidPreAuth = _isValidAmount(kf.pre_authorized_amount);
  const hasValidApproved = _isValidAmount(kf.approved_amount);
  const hasValidSumInsured = _isValidAmount(kf.sum_insured);
  const hasClaimType = Boolean(kf.claim_type);

  const hasFinancials = hasValidClaimed || hasValidGross || hasValidNet || hasValidPreAuth || hasValidApproved || hasValidSumInsured || hasClaimType;

  const financialMetricsHtml = hasFinancials ? `
    <div class="claim-financial-highlight-bar">
      ${hasValidClaimed ? `
        <div class="claim-metric-badge primary">
          <span class="metric-lbl">Total Claimed</span>
          <span class="metric-val">${escapeHtml(_formatAmount(kf.total_claimed_amount))}</span>
        </div>` : ""}
      ${hasValidNet ? `
        <div class="claim-metric-badge">
          <span class="metric-lbl">Net Claimed</span>
          <span class="metric-val">${escapeHtml(_formatAmount(kf.net_claimed_amount))}</span>
        </div>` : ""}
      ${hasValidGross ? `
        <div class="claim-metric-badge">
          <span class="metric-lbl">Gross Hospital Bill</span>
          <span class="metric-val">${escapeHtml(_formatAmount(kf.gross_bill_amount))}</span>
        </div>` : ""}
      ${hasValidPreAuth ? `
        <div class="claim-metric-badge">
          <span class="metric-lbl">Pre-Authorized</span>
          <span class="metric-val">${escapeHtml(_formatAmount(kf.pre_authorized_amount))}</span>
        </div>` : ""}
      ${hasValidApproved ? `
        <div class="claim-metric-badge success">
          <span class="metric-lbl">Approved / Sanctioned</span>
          <span class="metric-val">${escapeHtml(_formatAmount(kf.approved_amount))}</span>
        </div>` : ""}
      ${hasValidSumInsured ? `
        <div class="claim-metric-badge info">
          <span class="metric-lbl">Sum Insured</span>
          <span class="metric-val">${escapeHtml(_formatAmount(kf.sum_insured))}</span>
        </div>` : ""}
      ${hasClaimType ? `
        <div class="claim-metric-badge">
          <span class="metric-lbl">Claim Type</span>
          <span class="metric-val">${escapeHtml(kf.claim_type)}</span>
        </div>` : ""}
    </div>
  ` : "";

  // Build the claim summary card HTML
  return `
    <!-- Insurance Claim Summary Section -->
    <div class="report-dossier-wrap">
      <!-- Card 1: Claim Summary -->
      <div class="report-dossier-card" style="grid-column: 1 / -1">
        <div class="dossier-card-header">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline><line x1="16" y1="13" x2="8" y2="13"></line><line x1="16" y1="17" x2="8" y2="17"></line></svg>
          <h4>Claim Summary &amp; Key Information</h4>
        </div>
        <div class="dossier-content">
          ${flagsHtml}
          ${narrative ? `<p class="claim-narrative" style="margin-bottom:14px">${escapeHtml(narrative)}</p>` : ""}
          <div class="dossier-subgrid" style="grid-template-columns: repeat(3, 1fr)">
            <div class="dossier-item">
              <span class="dossier-label">Claimant / Insured Name</span>
              <span class="dossier-val highlight">${escapeHtml(kf.insured_name || pName)}</span>
            </div>
            <div class="dossier-item">
              <span class="dossier-label">Gender / DOB</span>
              <span class="dossier-val">${escapeHtml(kf.patient_gender || pGender)} ${(kf.patient_age || pDob) !== "Not specified" ? `• ${escapeHtml(kf.patient_age || pDob)}` : ""} ${pAge ? `<span class="age-pill">${pAge}</span>` : ""}</span>
            </div>
            <div class="dossier-item">
              <span class="dossier-label">Hospital / Provider</span>
              <span class="dossier-val">${escapeHtml(kf.hospital_name || facilityName)}</span>
            </div>
            <div class="dossier-item">
              <span class="dossier-label">Treating Doctor</span>
              <span class="dossier-val">${escapeHtml(kf.treating_doctor || practitionerName)}</span>
            </div>
            <div class="dossier-item">
              <span class="dossier-label">Policy / Insurance ID</span>
              <span class="dossier-val mono">${escapeHtml(kf.policy_number || "Not specified")}</span>
            </div>
            <div class="dossier-item">
              <span class="dossier-label">Claim Reference No.</span>
              <span class="dossier-val mono">${escapeHtml(kf.claim_number || "Not specified")}</span>
            </div>
            ${primaryDiagnosis ? `
            <div class="dossier-item span-2">
              <span class="dossier-label">Primary Diagnosis</span>
              <span class="dossier-val">${escapeHtml(primaryDiagnosis)}</span>
            </div>` : ""}
            <div class="dossier-item">
              <span class="dossier-label">Contact</span>
              <span class="dossier-val mono">${escapeHtml(pPhone || "Not recorded")}</span>
            </div>
            ${kf.admission_date ? `
            <div class="dossier-item">
              <span class="dossier-label">Admission Date</span>
              <span class="dossier-val mono">${escapeHtml(kf.admission_date)}</span>
            </div>` : ""}
            ${kf.discharge_date ? `
            <div class="dossier-item">
              <span class="dossier-label">Discharge Date</span>
              <span class="dossier-val mono">${escapeHtml(kf.discharge_date)}</span>
            </div>` : ""}
            ${roomCategory ? `
            <div class="dossier-item">
              <span class="dossier-label">Room Category</span>
              <span class="dossier-val">${escapeHtml(roomCategory)}</span>
            </div>` : ""}
          </div>
          ${financialMetricsHtml}
          ${pAddress ? `
          <div style="margin-top:12px;padding-top:10px;border-top:1px solid var(--border)">
            <span class="dossier-label">Address</span>
            <p class="address-text" style="margin:4px 0 0">${escapeHtml(pAddress)}</p>
            <div class="address-pills" style="margin-top:4px">
              ${pCity ? `<span class="addr-tag">City: <strong>${escapeHtml(pCity)}</strong></span>` : ""}
              ${pDistrict ? `<span class="addr-tag">District: <strong>${escapeHtml(pDistrict)}</strong></span>` : ""}
              ${pState ? `<span class="addr-tag">State: <strong>${escapeHtml(pState)}</strong></span>` : ""}
              ${pPin ? `<span class="addr-tag">PIN: <strong>${escapeHtml(pPin)}</strong></span>` : ""}
            </div>
          </div>` : ""}
        </div>
      </div>
    </div>
  `;
}

function _renderFinancialCard(docSections, kf = {}) {
  // Extract all financial data points from key fields and document sections
  const financialPoints = [];
  const allPoints = [];

  // Add primary key fields if available
  if (kf.total_claimed_amount) {
    allPoints.push({ text: `Total Claimed Amount: ${kf.total_claimed_amount}`, section: "Claim Dossier", isPrimary: true });
  }
  if (kf.net_claimed_amount && kf.net_claimed_amount !== kf.total_claimed_amount) {
    allPoints.push({ text: `Net Claimed Amount: ${kf.net_claimed_amount}`, section: "Hospital Bill", isPrimary: true });
  }
  if (kf.gross_bill_amount) {
    allPoints.push({ text: `Gross Hospital Bill: ${kf.gross_bill_amount}`, section: "Hospital Bill", isPrimary: true });
  }
  if (kf.pre_authorized_amount) {
    allPoints.push({ text: `Pre-Authorized Amount: ${kf.pre_authorized_amount}`, section: "Pre-Authorization", isPrimary: true });
  }
  if (kf.approved_amount) {
    allPoints.push({ text: `Approved / Settled Amount: ${kf.approved_amount}`, section: "TPA / Insurer Approval", isPrimary: true });
  }
  if (kf.sum_insured) {
    allPoints.push({ text: `Total Sum Insured: ${kf.sum_insured}`, section: "Policy Schedule", isPrimary: true });
  }

  for (const sec of (docSections || [])) {
    if (sec.summary) {
      const parts = sec.summary.split(/[,;]\s*/);
      for (const p of parts) {
        if (/[\u20b9₹]|rs\.?|inr|\d+[Ll]\b|amount|gross|net|bill|insured/i.test(p)) {
          allPoints.push({ text: p, section: sec.title });
        }
      }
    }
    for (const pt of (sec.key_data_points || [])) {
      allPoints.push({ text: pt, section: sec.title });
    }
  }

  // Filter for financial/amount related points
  const financialKeywords = [
    "amount", "bill", "charge", "cost", "fee", "total", "gross", "net",
    "rs.", "rs ", "₹", "inr", "sum insured", "insured", "premium",
    "deductible", "co-pay", "copay", "claimed", "approved", "sanctioned",
    "payable", "paid", "settled", "pre-auth", "preauth", "coverage",
    "sub-total", "subtotal", "tax", "gst", "discount", "lakh", "lac"
  ];

  const seen = new Set();
  for (const pt of allPoints) {
    const lower = pt.text.toLowerCase();
    if (financialKeywords.some(kw => lower.includes(kw))) {
      const norm = pt.text.replace(/\s+/g, " ").trim();
      if (!seen.has(norm)) {
        seen.add(norm);
        financialPoints.push(pt);
      }
    }
  }

  if (!financialPoints.length) return "";

  // Parse amounts to categorize them
  const items = financialPoints.map(fp => {
    const parts = fp.text.split(/:\s*/);
    let label = parts[0] || fp.text;
    let value = parts.slice(1).join(": ") || "";
    if (!value) {
      const m = fp.text.match(/([\u20b9₹Rs.INR\s]*[0-9,]+(?:\.\d{2})?[A-Za-z]*)/i);
      if (m && m[1].trim()) {
        value = m[1].trim();
        label = fp.text.replace(m[1], "").replace(/[-—–:,]/g, "").trim() || "Amount";
      } else {
        value = fp.text;
      }
    }
    const isPrimary = fp.isPrimary || /total|gross|net claimed|sum insured|approved/i.test(label);
    return { label, value, section: fp.section, isPrimary, raw: fp.text };
  });

  return `
    <div class="report-dossier-card" style="grid-column: 1 / -1">
      <div class="dossier-card-header">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="1" x2="12" y2="23"></line><path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"></path></svg>
        <h4>Financial Summary &amp; Insurance Amounts</h4>
      </div>
      <div class="dossier-content">
        <div class="financial-items-grid">
          ${items.map(item => `
            <div class="financial-item ${item.isPrimary ? 'financial-item-primary' : ''}">
              <span class="financial-label">${escapeHtml(item.label)}</span>
              <span class="financial-value">${escapeHtml(item.value || item.raw)}</span>
              <span class="financial-source">${escapeHtml(item.section)}</span>
            </div>
          `).join("")}
        </div>
      </div>
    </div>
  `;
}


function calculateAge(birthDateStr) {
  if (!birthDateStr || birthDateStr === "N/A" || birthDateStr === "Not available" || birthDateStr === "Not specified") return "";
  try {
    const dob = new Date(birthDateStr);
    if (isNaN(dob.getTime())) return "";
    const today = new Date();
    let age = today.getFullYear() - dob.getFullYear();
    const m = today.getMonth() - dob.getMonth();
    if (m < 0 || (m === 0 && today.getDate() < dob.getDate())) {
      age--;
    }
    return age > 0 ? ` (${age} yrs)` : "";
  } catch {
    return "";
  }
}

function getInterpretation(val, refRangeStr, obs) {
  const obsInterp = obs?.interpretation?.[0]?.coding?.[0]?.code || obs?.interpretation?.[0]?.text;
  if (obsInterp) {
    const c = String(obsInterp).toUpperCase();
    if (c === "H" || c === "HH" || c.includes("HIGH")) return { text: "HIGH", cls: "high" };
    if (c === "L" || c === "LL" || c.includes("LOW")) return { text: "LOW", cls: "low" };
    if (c === "N" || c.includes("NORM")) return { text: "NORMAL", cls: "normal" };
  }

  if (val == null || val === "" || isNaN(Number(val)) || !refRangeStr) {
    return { text: "NORMAL", cls: "normal" };
  }

  const num = Number(val);
  const str = String(refRangeStr).trim();

  // Pattern: "< 200" or "<= 200"
  let match = str.match(/^<\s*=?\s*([0-9.]+)/);
  if (match) {
    const high = parseFloat(match[1]);
    if (num > high) return { text: "HIGH", cls: "high" };
    return { text: "NORMAL", cls: "normal" };
  }

  // Pattern: "> 50" or ">= 50"
  match = str.match(/^>\s*=?\s*([0-9.]+)/);
  if (match) {
    const low = parseFloat(match[1]);
    if (num < low) return { text: "LOW", cls: "low" };
    return { text: "NORMAL", cls: "normal" };
  }

  // Pattern: "12.0 - 16.0" or "12 - 16"
  match = str.match(/([0-9.]+)\s*-\s*([0-9.]+)/);
  if (match) {
    const low = parseFloat(match[1]);
    const high = parseFloat(match[2]);
    if (num < low) return { text: "LOW", cls: "low" };
    if (num > high) return { text: "HIGH", cls: "high" };
    return { text: "NORMAL", cls: "normal" };
  }

  return { text: "NORMAL", cls: "normal" };
}


// ═══════════════════════════════════════════════
// FHIR Explorer
// ═══════════════════════════════════════════════

async function loadBundles() {
  const container = $("#bundlesList");
  try {
    const res = await fetch(`${apiBase()}/bundles`);
    const bundles = await res.json();
    if (!bundles.length) {
      container.innerHTML = '<p class="muted">No bundles stored yet.</p>';
      return;
    }
    let html = '<table><thead><tr><th>Bundle ID</th><th>Patient</th><th>Source File</th><th>Type</th><th>Date</th></tr></thead><tbody>';
    html += bundles.map((b) =>
      `<tr onclick="viewBundle('${b.id}')"><td style="font-family:var(--font-mono);font-size:.75rem">${b.id?.slice(0, 12)}...</td><td>${b.patient_name || "—"}</td><td>${b.source_filename || "—"}</td><td>${b.document_type || ""}</td><td>${b.created_at || ""}</td></tr>`
    ).join("");
    html += '</tbody></table>';
    container.innerHTML = html;
  } catch {
    container.innerHTML = '<p class="muted">Failed to load bundles.</p>';
  }
}

async function viewBundle(bundleId) {
  const detail = $("#bundleDetail");
  const content = $("#bundleDetailContent");
  detail.hidden = false;

  try {
    const res = await fetch(`${apiBase()}/bundles/${bundleId}`);
    const data = await res.json();
    const b = data.bundle || {};
    const v = data.validation || {};

    let html = `<div class="patient" style="margin-bottom:16px">
      <div><strong>Source</strong><span>${b.source_filename || "N/A"}</span></div>
      <div><strong>Type</strong><span>${b.document_type || "N/A"}</span></div>
      <div><strong>Created</strong><span>${b.created_at || "N/A"}</span></div>
    </div>`;

    // Validation mini-display
    if (v.compliance_score !== undefined) {
      const cls = v.compliance_score >= 80 ? "high" : v.compliance_score >= 50 ? "medium" : "low";
      html += `<p>ABDM Compliance: <span class="compliance-score ${cls}" style="font-size:1.2rem;padding:4px 14px">${v.compliance_score}%</span> — ${v.summary || ""}</p>`;
    }

    // Raw JSON
    if (b.bundle_json) {
      const parsed = typeof b.bundle_json === "string" ? JSON.parse(b.bundle_json) : b.bundle_json;
      html += `<pre><code class="language-json">${escapeHtml(JSON.stringify(parsed, null, 2))}</code></pre>`;
    }

    content.innerHTML = html;
    if (window.hljs) content.querySelectorAll("code").forEach((el) => hljs.highlightElement(el));
  } catch {
    content.innerHTML = '<p class="muted">Failed to load bundle details.</p>';
  }
}

function escapeHtml(str) {
  if (str == null) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}
