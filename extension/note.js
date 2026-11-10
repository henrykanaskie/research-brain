// research_brain note popover: runs in an extension iframe that annotate.js places next to a selection.
// No AI. Saves through background.js to the research_brain server on this computer.
"use strict";

const $ = (s) => document.querySelector(s);
const token = location.hash.slice(1);
const isMac = /Mac|iPhone|iPad/.test(navigator.platform);
let data = null, saving = false, ed = null;

function send(msg) {
  return new Promise((resolve) => chrome.runtime.sendMessage(msg, (r) => resolve(chrome.runtime.lastError ? null : r)));
}
const emit = (event, detail) => send({ type: "rb-note-event", token, event, detail });

function fit() {
  const h = Math.ceil(document.getElementById("app").getBoundingClientRect().height);
  // leave room for the Symbols palette when it is open
  const pal = document.querySelector(".le-pal:not([hidden])");
  emit("resize", { h: pal ? Math.max(h, 360) : h });
}

async function init() {
  const r = await send({ type: "rb-note-get", token });
  if (!r || !r.ok) { $("#quote").textContent = "This note can't be opened any more. Select the text again."; $("#save").disabled = true; fit(); return; }
  data = r.data;
  $("#quote").textContent = data.quote;
  $("#where").textContent = data.paper_title ? `Under ${data.paper_title}` : "";
  $("#where").title = data.paper_title || "";
  $("#save").innerHTML = `Save<kbd>${isMac ? "⌘↵" : "Ctrl+↵"}</kbd>`;
  $("#save").title = `Save (${isMac ? "⌘" : "Ctrl"}+Enter)`;
  const LE = window.LatexEditor;
  if (LE) {
    ed = LE.attach($("#note"), {
      preview: $("#prev"), mode: "markdown", toolbar: $("#tools"),
      render: (src) => (/\$|\\\(|\\\[|[*_`#]/.test(src) ? LE.render(src, { mode: "markdown" }) : ""),
    });
  }
  const grow = () => { const t = $("#note"); t.style.height = "auto"; t.style.height = Math.min(220, Math.max(72, t.scrollHeight + 2)) + "px"; };
  $("#note").addEventListener("input", () => { grow(); setTimeout(fit, 140); });
  new ResizeObserver(fit).observe(document.getElementById("app"));
  new MutationObserver(fit).observe(document.body, { subtree: true, attributes: true, attributeFilter: ["hidden"] });
  $("#note").focus();
  fit();
}

async function save() {
  if (saving || !data) return;
  saving = true;
  const btn = $("#save"); btn.disabled = true; btn.textContent = "Saving";
  $("#err").hidden = true;
  const body = { ...data, note: $("#note").value.trim() };
  delete body.paper_title;
  const r = await send({ type: "rb-api", path: "/api/clip", method: "POST", body });
  if (r && r.ok) { await emit("saved", r.data); return; }
  $("#err").textContent = r ? (r.offline ? r.error : `Couldn't save: ${r.error}`) : "The extension was reloaded. Refresh the page.";
  $("#err").hidden = false;
  saving = false; btn.disabled = false; btn.innerHTML = `Save<kbd>${isMac ? "⌘↵" : "Ctrl+↵"}</kbd>`;
  fit();
}

$("#save").addEventListener("click", save);
$("#cancel").addEventListener("click", () => emit("cancel"));
document.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); save(); }
  else if (e.key === "Escape" && !e.defaultPrevented && !document.querySelector(".le-pop:not([hidden]), .le-pal:not([hidden])")) { e.preventDefault(); emit("cancel"); }
});

init();
