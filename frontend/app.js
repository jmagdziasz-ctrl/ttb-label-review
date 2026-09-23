const STATUS_ICON = {
  match: "✓", match_minor: "✓", needs_review: "⚠",
  mismatch: "✗", missing: "✗",
};
const STATUS_LABEL = {
  match: "Match", match_minor: "Match (minor diff)", needs_review: "Needs Review",
  mismatch: "Mismatch", missing: "Not Found",
};
const OVERALL_LABEL = {
  pass: "PASS — all fields match",
  needs_review: "NEEDS REVIEW — human check recommended",
  fail: "FAIL — discrepancy found",
};
const OVERALL_ICON = { pass: "✅", needs_review: "⚠️", fail: "❌" };
const FIELD_LABELS = {
  brand_name: "Brand Name", class_type: "Class / Type", alcohol_content: "Alcohol Content",
  net_contents: "Net Contents", government_warning: "Government Warning", country_of_origin: "Country of Origin",
  bottler_name_address: "Bottler / Importer Name & Address",
};
const METHOD_LABEL = {
  ocr: "Read automatically from the photo",
  vision: "Read using AI image analysis",
};

// ---- Optional: reading with the user's own Anthropic API key ----
// Stored only in this browser tab, only for the current session (cleared
// automatically when the tab closes - sessionStorage rather than
// localStorage, so the key doesn't sit around indefinitely on a shared or
// public computer), never sent anywhere but this app's own backend, and
// only attached as a header on review requests.
const API_KEY_STORAGE_KEY = "ttb_anthropic_api_key";

function getStoredApiKey() {
  try {
    return sessionStorage.getItem(API_KEY_STORAGE_KEY) || "";
  } catch {
    return ""; // private-browsing / storage blocked - just fall back to the free reader
  }
}

function setStoredApiKey(key) {
  try {
    if (key) sessionStorage.setItem(API_KEY_STORAGE_KEY, key);
    else sessionStorage.removeItem(API_KEY_STORAGE_KEY);
  } catch {
    // storage unavailable - nothing to do, the key just won't persist
  }
}

function apiKeyHeaders() {
  const key = getStoredApiKey();
  return key ? { "X-Anthropic-Api-Key": key } : {};
}

// Whether *this server* already defaults to Vision (an ANTHROPIC_API_KEY is
// configured on the deployment itself, e.g. for a demo) - separate from
// whether the visitor has entered their own key. Without this, the status
// chip would wrongly say "Free reader" on a deployment that's actually
// already running AI reading for everyone by default.
let serverDefaultMethod = null;
fetch("/api/health")
  .then((res) => res.json())
  .then((data) => { serverDefaultMethod = data.default_extraction_method; refreshApiKeyStatus(); })
  .catch(() => {}); // health check failing isn't worth surfacing here - just keep the client-only view

function refreshApiKeyStatus() {
  const hasOwnKey = !!getStoredApiKey();
  const chip = document.getElementById("api-key-status-chip");
  const status = document.getElementById("api-key-status");
  if (hasOwnKey) {
    status.textContent = "Currently using: your API key (AI image reading).";
    chip.textContent = "Using your API key";
  } else if (serverDefaultMethod === "vision") {
    status.textContent = "Currently using: AI image reading (this deployment has it configured by default - no key needed from you).";
    chip.textContent = "AI reading (built in)";
  } else {
    status.textContent = "Currently using: the free built-in reader.";
    chip.textContent = "Free reader";
  }
  chip.classList.toggle("active", hasOwnKey || serverDefaultMethod === "vision");
}

const apiKeyToggle = document.getElementById("api-key-toggle");
const apiKeyPanel = document.getElementById("api-key-panel");
const apiKeyInput = document.getElementById("api-key-input");
apiKeyInput.value = getStoredApiKey();

apiKeyToggle.addEventListener("click", () => { apiKeyPanel.hidden = !apiKeyPanel.hidden; });

document.getElementById("api-key-save").addEventListener("click", () => {
  const key = apiKeyInput.value.trim();
  if (!key) { showToast("Enter a key first, or use Remove Key to go back to this app's default reader."); return; }
  setStoredApiKey(key);
  refreshApiKeyStatus();
  // A cheap, non-blocking sanity check: every real Anthropic key starts
  // with this prefix, so a mismatch almost always means something else got
  // pasted in (wrong field, browser autofill, a truncated copy) - still
  // saved either way in case the format ever changes, just flagged.
  if (!key.startsWith("sk-ant-")) {
    showToast("Saved for this session, but that doesn't look like a typical Anthropic key (usually starts with \"sk-ant-\") — double-check what you pasted.");
  } else {
    showToast("API key saved for this session — reviews will use it until you close the browser or click Remove Key.");
  }
});

document.getElementById("api-key-remove").addEventListener("click", () => {
  apiKeyInput.value = "";
  setStoredApiKey("");
  refreshApiKeyStatus();
  showToast(serverDefaultMethod === "vision" ? "API key removed — back to this deployment's default AI reading." : "API key removed — back to the free reader.");
});

refreshApiKeyStatus();

function formatSeconds(ms) {
  return `${(ms / 1000).toFixed(1)}s`;
}

function resultMetaLine(data) {
  const method = METHOD_LABEL[data.extraction_method] || "Read automatically from the photo";
  const confidence = data.extraction_confidence != null
    ? ` · about ${(data.extraction_confidence * 100).toFixed(0)}% sure we read it correctly`
    : "";
  return `${method}${confidence} · took ${formatSeconds(data.processing_time_ms)}`;
}

function resultActionsHtml() {
  return `
    <div class="result-actions">
      <button type="button" class="secondary-btn" data-action="print">Print</button>
      <button type="button" class="secondary-btn" data-action="save-pdf">Save as PDF</button>
    </div>`;
}

function printWithDetailsExpanded(container) {
  // Batch rows are collapsed by default so the table stays scannable, but a
  // printed/saved copy should show everything — nothing usefully "expandable"
  // on paper. Expand for the print pass, then restore on-screen state after
  // (via `afterprint`, which fires once the print dialog actually closes,
  // rather than a fixed timeout that could fire too early or leave things
  // expanded too long).
  const collapsedRows = Array.from(container.querySelectorAll(".detail-row[hidden]"));
  collapsedRows.forEach((row) => { row.hidden = false; });
  const restore = () => collapsedRows.forEach((row) => { row.hidden = true; });
  window.addEventListener("afterprint", restore, { once: true });
  window.print();
  // Safety net: some browsers (or a cancelled/blocked dialog) may never
  // fire `afterprint` — don't leave the table stuck expanded indefinitely.
  setTimeout(restore, 5000);
}

function wireResultActions(container, filenameSlug) {
  const printBtn = container.querySelector('[data-action="print"]');
  const pdfBtn = container.querySelector('[data-action="save-pdf"]');
  if (printBtn) printBtn.addEventListener("click", () => printWithDetailsExpanded(container));
  if (pdfBtn) pdfBtn.addEventListener("click", () => {
    // Chrome/Edge suggest the document title as the default filename when
    // "Save as PDF" is picked as the print destination, so we swap it in
    // briefly rather than adding a PDF-generation library for this.
    const originalTitle = document.title;
    document.title = `${filenameSlug}-${new Date().toISOString().slice(0, 10)}`;
    printWithDetailsExpanded(container);
    setTimeout(() => { document.title = originalTitle; }, 1000);
  });
}

function showToast(message) {
  const toast = document.getElementById("toast");
  toast.textContent = message;
  toast.hidden = false;
  clearTimeout(showToast._t);
  showToast._t = setTimeout(() => (toast.hidden = true), 6000);
}

// ---- Tabs ----
document.querySelectorAll(".tab").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((b) => { b.classList.remove("active"); b.setAttribute("aria-selected", "false"); });
    btn.classList.add("active");
    btn.setAttribute("aria-selected", "true");
    document.querySelectorAll(".panel").forEach((p) => p.classList.remove("active"));
    document.getElementById(`panel-${btn.dataset.tab}`).classList.add("active");
  });
});

// ---- Single review: image preview + drag/drop ----
const dropzone = document.getElementById("dropzone");
const imageInput = document.getElementById("image-input");
const dropzoneText = document.getElementById("dropzone-text");
const imagePreview = document.getElementById("image-preview");

function handleFileSelected(file) {
  if (!file) return;
  const reader = new FileReader();
  reader.onload = (e) => {
    imagePreview.src = e.target.result;
    imagePreview.hidden = false;
    dropzoneText.textContent = file.name;
  };
  reader.readAsDataURL(file);
}
imageInput.addEventListener("change", () => handleFileSelected(imageInput.files[0]));
["dragenter", "dragover"].forEach((evt) =>
  dropzone.addEventListener(evt, (e) => { e.preventDefault(); dropzone.classList.add("drag-over"); })
);
["dragleave", "drop"].forEach((evt) =>
  dropzone.addEventListener(evt, (e) => { e.preventDefault(); dropzone.classList.remove("drag-over"); })
);
dropzone.addEventListener("drop", (e) => {
  const file = e.dataTransfer.files[0];
  if (file) { imageInput.files = e.dataTransfer.files; handleFileSelected(file); }
});

// ---- Single review submit ----
const singleForm = document.getElementById("single-form");
const singleResult = document.getElementById("single-result");
const singleSubmit = document.getElementById("single-submit");

document.getElementById("single-clear").addEventListener("click", () => {
  singleForm.reset();
  imagePreview.hidden = true;
  imagePreview.removeAttribute("src");
  dropzoneText.textContent = "Click to choose a photo, or drag one here";
  singleResult.hidden = true;
  singleResult.innerHTML = "";
});

singleForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  if (!imageInput.files[0]) { showToast("Please choose a label image first."); return; }

  const formData = new FormData(singleForm);
  formData.set("image", imageInput.files[0]);

  singleSubmit.disabled = true;
  singleSubmit.innerHTML = '<span class="spinner"></span>Reviewing…';
  singleResult.hidden = true;

  const started = performance.now();
  try {
    const res = await fetch("/api/review", { method: "POST", body: formData, headers: apiKeyHeaders() });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Request failed");
    renderSingleResult(data, performance.now() - started);
  } catch (err) {
    showToast(`Error: ${err.message}`);
  } finally {
    singleSubmit.disabled = false;
    singleSubmit.textContent = "Review Label";
  }
});

function fieldRowHtml(f) {
  const label = FIELD_LABELS[f.field] || f.field;
  return `
    <tr>
      <td>${label}</td>
      <td class="value-diff">${escapeHtml(f.submitted_value ?? "—")}</td>
      <td class="value-diff">${escapeHtml(f.extracted_value ?? "—")}</td>
      <td>
        <span class="status-pill ${f.status}">${STATUS_ICON[f.status]} ${STATUS_LABEL[f.status]}</span>
        ${f.note ? `<div class="note">${escapeHtml(f.note)}</div>` : ""}
      </td>
    </tr>`;
}

function renderSingleResult(data, clientMs) {
  const warningsHtml = data.warnings && data.warnings.length
    ? `<div class="warnings-box"><strong>Heads up:</strong><ul>${data.warnings.map((w) => `<li>${escapeHtml(w)}</li>`).join("")}</ul></div>`
    : "";

  singleResult.innerHTML = `
    ${resultActionsHtml()}
    <div class="overall-banner ${data.overall_status}">
      <span class="icon">${OVERALL_ICON[data.overall_status]}</span>
      <div>
        ${OVERALL_LABEL[data.overall_status]}
        <span class="meta-line">${resultMetaLine(data)}</span>
      </div>
    </div>
    ${warningsHtml}
    <table class="field-table">
      <thead><tr><th>Field</th><th>Application Says</th><th>Label Shows</th><th>Result</th></tr></thead>
      <tbody>${data.fields.map(fieldRowHtml).join("")}</tbody>
    </table>
  `;
  singleResult.hidden = false;
  wireResultActions(singleResult, "ttb-label-review");
  singleResult.scrollIntoView({ behavior: "smooth", block: "start" });
}

// ---- Batch review (manifest CSV) ----
const batchForm = document.getElementById("batch-form");
const batchResult = document.getElementById("batch-result");
const batchSubmit = document.getElementById("batch-submit");

document.getElementById("batch-clear").addEventListener("click", () => {
  batchForm.reset();
  batchResult.hidden = true;
  batchResult.innerHTML = "";
});

batchForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const manifestFile = document.getElementById("manifest-input").files[0];
  const imageFiles = document.getElementById("images-input").files;
  if (!manifestFile || !imageFiles.length) { showToast("Please select a manifest CSV and at least one image."); return; }

  const formData = new FormData();
  formData.append("manifest", manifestFile);
  Array.from(imageFiles).forEach((f) => formData.append("images", f));

  batchSubmit.disabled = true;
  batchSubmit.innerHTML = '<span class="spinner"></span>Reviewing batch…';
  batchResult.hidden = true;

  const started = performance.now();
  try {
    const res = await fetch("/api/review/batch", { method: "POST", body: formData, headers: apiKeyHeaders() });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Request failed");
    renderBatchResult(data, performance.now() - started);
  } catch (err) {
    showToast(`Error: ${err.message}`);
  } finally {
    batchSubmit.disabled = false;
    batchSubmit.textContent = "Review Batch";
  }
});

function renderBatchResult(data, clientMs, container = batchResult) {
  const s = data.summary;
  const summaryHtml = `
    <div class="summary-row">
      <div class="summary-chip total">${s.total} total</div>
      <div class="summary-chip pass">${s.pass} pass</div>
      <div class="summary-chip needs_review">${s.needs_review} needs review</div>
      <div class="summary-chip fail">${s.fail} fail</div>
      ${s.error ? `<div class="summary-chip error">${s.error} errored</div>` : ""}
      <div class="summary-chip total">${(clientMs / 1000).toFixed(1)}s total</div>
    </div>`;

  const unmatchedHtml = data.unmatched_uploaded_images.length
    ? `<div class="warnings-box"><strong>Uploaded but not in manifest:</strong> ${data.unmatched_uploaded_images.map(escapeHtml).join(", ")}</div>`
    : "";

  const rowsHtml = data.results.map((r, i) => {
    const detailId = `${container.id}-detail-${i}`;
    if (r.error) {
      return `<tr class="error-row"><td>${escapeHtml(r.filename)}</td><td colspan="4">${escapeHtml(r.error)}</td></tr>`;
    }
    const fieldSummary = r.fields.map((f) => `${FIELD_LABELS[f.field] || f.field}: ${STATUS_ICON[f.status]}`).join("  ");
    return `
      <tr class="expandable" data-detail="${detailId}">
        <td>${escapeHtml(r.filename)}</td>
        <td><span class="status-pill ${r.overall_status === "pass" ? "match" : r.overall_status}">${OVERALL_ICON[r.overall_status]} ${r.overall_status.replace("_", " ")}</span></td>
        <td>${escapeHtml(fieldSummary)}</td>
        <td>${formatSeconds(r.processing_time_ms)}</td>
        <td>▾ details</td>
      </tr>
      <tr id="${detailId}" class="detail-row" hidden>
        <td colspan="5">
          <table class="field-table">
            <thead><tr><th>Field</th><th>Application Says</th><th>Label Shows</th><th>Result</th></tr></thead>
            <tbody>${r.fields.map(fieldRowHtml).join("")}</tbody>
          </table>
          ${r.warnings && r.warnings.length ? `<div class="warnings-box"><ul>${r.warnings.map((w) => `<li>${escapeHtml(w)}</li>`).join("")}</ul></div>` : ""}
        </td>
      </tr>`;
  }).join("");

  container.innerHTML = `
    ${resultActionsHtml()}
    ${summaryHtml}
    ${unmatchedHtml}
    <table class="batch-table">
      <thead><tr><th>Filename</th><th>Overall</th><th>Field Summary</th><th>Time</th><th></th></tr></thead>
      <tbody>${rowsHtml}</tbody>
    </table>`;
  container.hidden = false;

  container.querySelectorAll("tr.expandable").forEach((row) => {
    row.addEventListener("click", () => {
      const detail = document.getElementById(row.dataset.detail);
      detail.hidden = !detail.hidden;
    });
  });
  wireResultActions(container, "ttb-batch-review");
  container.scrollIntoView({ behavior: "smooth", block: "start" });
}

function escapeHtml(str) {
  return String(str).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
