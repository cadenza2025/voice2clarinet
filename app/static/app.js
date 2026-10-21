"use strict";

const $ = (id) => document.getElementById(id);
let job = null;
let selected = null;
let view = { prefix: "", page: 1, pages: 0, revision: "" };
let polling = null;
let working = false;

async function api(path, options = {}) {
  const response = await fetch(path, options);
  if (!response.ok) {
    let message = `Request failed (${response.status}).`;
    try {
      const error = await response.json();
      message = typeof error.detail === "string" ? error.detail : message;
    } catch {
      message += " Check the server connection.";
    }
    const error = new Error(message);
    error.status = response.status;
    throw error;
  }
  return response.status === 204 ? null : response.json();
}

function notice(message = "", error = true) {
  $("notice").textContent = message;
  $("notice").className = `notice${error ? " error" : ""}`;
  $("notice").hidden = !message;
}

function stage(name) {
  for (const id of ["upload", "processing", "review", "failed"]) {
    $(id === "upload" ? "upload-stage" : `${id}-stage`).hidden = id !== name;
  }
  step(name === "upload" || name === "processing" || name === "failed" ? 1 : 2);
}

function step(index) {
  for (let n = 1; n <= 3; n++) {
    const element = $(`step-${n}`);
    element.className = n === index ? "active" : n < index ? "done" : "";
    if (n === index) element.setAttribute("aria-current", "step");
    else element.removeAttribute("aria-current");
  }
}

function fileUrl(name) {
  return `/api/jobs/${job.id}/files/${name}`;
}

function controls(busy) {
  working = busy;
  $("selection-fields").disabled = busy;
  $("delete-button").disabled = busy;
  $("save-xml").disabled = busy;
  $("correction-file").disabled = busy;
  $("score-busy").hidden = !busy;
  if (busy) {
    $("score-image").hidden = true;
    $("score-empty").hidden = true;
    $("output-panel").hidden = true;
  } else {
    $("score-image").hidden = !view.pages;
    $("score-empty").hidden = !!view.pages;
    $("output-panel").hidden = !job?.output || view.prefix !== "clarinet";
  }
  $("preview-button").disabled = busy || !selected;
  $("convert-button").disabled = busy || !selected || !$("confirm-part").checked;
  updatePageControls();
}

async function upload(file) {
  if (!file || working) return;
  notice();
  if (!file.name.toLowerCase().endsWith(".pdf") || file.size > 20 * 1024 * 1024) {
    notice("Choose a PDF no larger than 20 MB.");
    return;
  }
  if (job) {
    notice("Delete the current session before starting another score.");
    return;
  }
  working = true;
  stage("processing");
  $("processing-title").textContent = "Uploading your score…";
  $("processing-message").textContent = "Sending your PDF to your own recognition server.";
  try {
    job = await api("/api/jobs", {
      method: "POST", body: file,
      headers: { "Content-Type": "application/pdf", "X-File-Name": encodeURIComponent(file.name) },
    });
    sessionStorage.setItem("reed-voice-job", job.id);
    await poll();
  } catch (error) {
    stage("upload");
    notice(error.message);
  } finally {
    working = false;
    $("pdf-input").value = "";
  }
}

async function poll() {
  clearTimeout(polling);
  try {
    job = await api(`/api/jobs/${job.id}`);
    notice();
    if (job.status === "ready") {
      showReview();
    } else if (job.status === "failed") {
      stage("failed");
      $("failure-message").textContent = job.error;
      $("failure-log").hidden = !job.has_log;
      $("failure-log").href = fileUrl("recognition.log");
    } else {
      stage("processing");
      $("processing-title").textContent = job.status === "queued" ? "Your score is in the queue." : "Reading your score…";
      $("processing-message").textContent = job.status === "queued" ? "One PDF is processed at a time. Your score will start automatically." : "Audiveris is finding the staves, notes, and voices. Allow up to 10 minutes.";
      polling = setTimeout(poll, 2000);
    }
  } catch (error) {
    if (error.status === 404) {
      sessionStorage.removeItem("reed-voice-job");
      job = null;
      stage("upload");
    }
    notice(`${error.message} Reload this tab to reconnect.`);
  }
}

function showReview() {
  stage("review");
  try { $("filename").textContent = decodeURIComponent(job.name); }
  catch { $("filename").textContent = job.name; }
  $("source-link").href = fileUrl("source.pdf");
  $("log-link").hidden = !job.has_log;
  $("log-link").href = fileUrl("recognition.log");
  $("movement-select").replaceChildren();
  for (const movement of job.movements) {
    const option = document.createElement("option");
    option.value = movement.index;
    option.textContent = `${movement.index + 1}. ${movement.title}`;
    $("movement-select").append(option);
  }
  $("movement-select").value = job.output?.movement ?? 0;
  showParts();
  if (job.output) showOutput(job.output);
}

function showParts() {
  selected = null;
  $("confirm-part").checked = false;
  $("parts").replaceChildren();
  const movement = job.movements.find((m) => m.index === Number($("movement-select").value));
  $("recognized-link").href = fileUrl(`movement-${movement.index}.musicxml`);
  for (const part of movement.parts) {
    const label = document.createElement("label");
    label.className = "part-option";
    const input = document.createElement("input");
    input.type = "radio"; input.name = "part"; input.value = part.id;
    const title = document.createElement("span");
    title.className = "part-name"; title.textContent = part.name;
    const meta = document.createElement("span");
    meta.className = "part-meta";
    meta.textContent = `${part.measures} measures · ${part.notes} notes · ${part.staves} ${part.staves === 1 ? "staff" : "staves"}`;
    if (part.likely_voice) {
      const badge = document.createElement("span");
      badge.className = "vocal-badge"; badge.textContent = "Possible vocal part";
      meta.append(badge);
    }
    label.append(input, title, meta);
    $("parts").append(label);
    input.addEventListener("change", () => {
      selected = part.id;
      $("confirm-part").checked = false;
      resetView();
      controls(false);
    });
  }
  resetView();
  controls(false);
}

function resetView() {
  view = { prefix: "", page: 1, pages: 0, revision: "" };
  $("score-image").hidden = true;
  $("score-empty").hidden = false;
  $("output-panel").hidden = true;
  $("preview-label").textContent = "PART PREVIEW";
  $("workspace-title").textContent = "Which part sings?";
  step(2);
  updatePageControls();
}

function updatePageControls() {
  $("page-label").textContent = view.pages ? `${view.page} / ${view.pages}` : "—";
  $("previous-page").disabled = working || view.page <= 1;
  $("next-page").disabled = working || view.page >= view.pages;
}

function showPage() {
  $("score-image").src = `${fileUrl(`${view.prefix}-${view.page}.svg`)}?v=${view.revision}`;
  $("score-image").alt = `${view.prefix === "clarinet" ? "Written B-flat clarinet" : "Recognized source part"}, page ${view.page} of ${view.pages}`;
  $("score-image").hidden = false;
  $("score-empty").hidden = true;
  updatePageControls();
}

function showOutput(output) {
  job.output = output;
  view = { prefix: "clarinet", page: 1, pages: output.pages, revision: output.revision };
  $("preview-label").textContent = "B♭ CLARINET · WRITTEN PITCH";
  $("workspace-title").textContent = "Your melody, reimagined.";
  $("pdf-download").href = `${fileUrl("clarinet.pdf")}?v=${output.revision}`;
  $("xml-download").href = `${fileUrl("clarinet.musicxml")}?v=${output.revision}`;
  $("warnings").replaceChildren();
  for (const message of output.warnings) {
    const li = document.createElement("li");
    li.textContent = message;
    $("warnings").append(li);
  }
  $("output-panel").hidden = false;
  step(3);
  showPage();
  loadEditor();
}

async function loadEditor() {
  try {
    const response = await fetch(fileUrl("clarinet.musicxml"));
    if (!response.ok) throw new Error("Could not load the MusicXML editor.");
    $("xml-editor").value = await response.text();
  } catch (error) { notice(error.message); }
}

async function runAction(action) {
  if (working || !selected) return;
  notice();
  controls(true);
  try {
    const result = await api(`/api/jobs/${job.id}/${action}`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ movement: Number($("movement-select").value), part_id: selected }),
    });
    if (action === "convert") showOutput(result);
    else {
      view = { prefix: "preview", page: 1, pages: result.pages, revision: Date.now() };
      $("preview-label").textContent = "RECOGNIZED SOURCE · CHECK AGAINST YOUR PDF";
      showPage();
    }
  } catch (error) { notice(error.message); }
  finally { controls(false); }
}

async function correct(data) {
  if (working || !job) return;
  notice();
  controls(true);
  try {
    const output = await api(`/api/jobs/${job.id}/correction`, {
      method: "PUT", headers: { "Content-Type": "application/xml" }, body: data,
    });
    showOutput(output);
    notice("Corrected score saved. Written pitches were kept exactly as supplied.", false);
  } catch (error) { notice(error.message); }
  finally {
    controls(false);
    $("correction-file").value = "";
  }
}

async function removeSession() {
  if (working) return false;
  try {
    if (job) await api(`/api/jobs/${job.id}`, { method: "DELETE" });
  } catch (error) {
    if (error.status !== 404) { notice(error.message); return false; }
  }
  clearTimeout(polling);
  sessionStorage.removeItem("reed-voice-job");
  job = null; selected = null;
  view = { prefix: "", page: 1, pages: 0, revision: "" };
  notice(); stage("upload");
  return true;
}

$("pdf-input").addEventListener("change", (event) => upload(event.target.files[0]));
$("drop-zone").addEventListener("dragover", (event) => { event.preventDefault(); $("drop-zone").classList.add("dragging"); });
$("drop-zone").addEventListener("dragleave", () => $("drop-zone").classList.remove("dragging"));
$("drop-zone").addEventListener("drop", (event) => {
  event.preventDefault(); $("drop-zone").classList.remove("dragging");
  if (event.dataTransfer.files.length !== 1) { notice("Drop one PDF at a time."); return; }
  upload(event.dataTransfer.files[0]);
});
$("demo-button").addEventListener("click", async () => {
  try {
    const response = await fetch("/demo/orchard-song.pdf");
    if (!response.ok) throw new Error("The sample PDF could not be loaded.");
    await upload(new File([await response.blob()], "Orchard song - original sample.pdf", { type: "application/pdf" }));
  } catch (error) { notice(error.message); }
});
$("movement-select").addEventListener("change", showParts);
$("confirm-part").addEventListener("change", () => controls(false));
$("preview-button").addEventListener("click", () => runAction("preview"));
$("convert-button").addEventListener("click", () => runAction("convert"));
$("previous-page").addEventListener("click", () => { if (view.page > 1) { view.page--; showPage(); } });
$("next-page").addEventListener("click", () => { if (view.page < view.pages) { view.page++; showPage(); } });
$("correction-file").addEventListener("change", async (event) => {
  const file = event.target.files[0];
  if (!file) return;
  if (file.size > 8 * 1024 * 1024) { notice("MusicXML must be no larger than 8 MB."); return; }
  await correct(await file.text());
});
$("save-xml").addEventListener("click", () => correct($("xml-editor").value));
$("delete-button").addEventListener("click", removeSession);
$("retry-button").addEventListener("click", removeSession);
$("score-image").addEventListener("error", () => notice("The engraved page could not be loaded. Preview or convert the part again."));

async function init() {
  try {
    const health = await api("/api/health");
    $("engine-notice").hidden = health.omr_available;
  } catch (error) { notice(`The processing server is unavailable. ${error.message}`); }
  const saved = sessionStorage.getItem("reed-voice-job");
  if (saved) { job = { id: saved }; await poll(); }
}
init();
