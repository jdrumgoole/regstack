// regstack OAuth setup wizard — SPA state machine.
//
// Reads the launch token + step count from <body>, persists per-input
// state to sessionStorage, hash-routes (#/step/N) so browser back /
// forward also work, and gates every Next click on the server's
// per-step validator. The actual write happens in step 11.

(function () {
  "use strict";

  const body = document.body;
  const TOKEN = body.dataset.wizardToken;
  const NUM_STEPS = parseInt(body.dataset.numSteps || "12", 10);
  const STORAGE_KEY = "regstack.oauth-wizard.state";

  const state = loadState();
  let currentStep = parseHash();

  // ---- Initial server-side state ----------------------------------------

  api("GET", "/api/state").then((s) => {
    state.serverState = s;
    if (state.base_url === undefined && s.base_url) state.base_url = s.base_url;
    if (state.existing_oauth === undefined) state.existing_oauth = s.existing_oauth;
    saveState();
    render();
  });

  // ---- Routing ----------------------------------------------------------

  window.addEventListener("hashchange", () => {
    currentStep = parseHash();
    render();
  });

  function parseHash() {
    const m = (location.hash || "").match(/^#\/step\/(\d+)/);
    if (!m) return 0;
    const n = parseInt(m[1], 10);
    if (!Number.isFinite(n) || n < 0 || n >= NUM_STEPS) return 0;
    return n;
  }

  function gotoStep(n) {
    if (n < 0 || n >= NUM_STEPS) return;
    location.hash = `#/step/${n}`;
  }

  // ---- Inputs <-> state -------------------------------------------------

  function bindInputs() {
    document.querySelectorAll("input[name]").forEach((el) => {
      const name = el.name;
      if (state[name] !== undefined) {
        if (el.type === "checkbox") el.checked = !!state[name];
        else el.value = state[name];
      } else if (el.type === "checkbox") {
        el.checked = false;
      }
      el.addEventListener("input", () => updateFromInput(el));
      el.addEventListener("change", () => updateFromInput(el));
    });
  }

  function updateFromInput(el) {
    if (el.type === "checkbox") state[el.name] = el.checked;
    else state[el.name] = el.value;
    saveState();
    if (el.name === "base_url") refreshRedirectPreview();
  }

  // ---- Render -----------------------------------------------------------

  function render() {
    document.querySelectorAll(".wiz-step").forEach((sec) => {
      sec.classList.toggle(
        "is-active",
        parseInt(sec.dataset.step, 10) === currentStep
      );
    });
    renderProgress();
    bindInputs();
    clearErrors();

    if (currentStep === 1) renderDetectExisting();
    if (currentStep === 2) refreshRedirectPreview();
    if (currentStep === 6) refreshRedirectCopy();
    if (currentStep === 10) renderReview();
    if (currentStep === 11) renderWriteTarget();

    document.getElementById("wiz-back").disabled = currentStep === 0;
    document.getElementById("wiz-next").hidden = currentStep === 11;
  }

  function renderProgress() {
    const root = document.getElementById("wiz-progress");
    root.innerHTML = "";
    for (let i = 0; i < NUM_STEPS; i++) {
      const cell = document.createElement("div");
      cell.className =
        "wiz-progress-cell" +
        (i < currentStep ? " is-done" : i === currentStep ? " is-current" : "");
      root.appendChild(cell);
    }
  }

  function renderDetectExisting() {
    const existing = !!state.existing_oauth;
    document
      .querySelector('[data-step="1"] [data-when="no-existing"]')
      .toggleAttribute("hidden", existing);
    document
      .querySelector('[data-step="1"] [data-when="existing"]')
      .toggleAttribute("hidden", !existing);
    if (state.serverState && state.serverState.config_file) {
      const el = document.getElementById("wiz-config-path");
      if (el) el.textContent = state.serverState.config_file;
    }
  }

  function refreshRedirectPreview() {
    const target = document.getElementById("wiz-redirect-preview");
    if (!target) return;
    target.textContent = computeRedirect();
  }

  function refreshRedirectCopy() {
    const target = document.getElementById("wiz-redirect-copy");
    if (!target) return;
    target.textContent = computeRedirect();
  }

  function computeRedirect() {
    const base = (state.base_url || "").trim();
    const prefix =
      (state.serverState && state.serverState.api_prefix) || "/api/auth";
    if (!base) return "—";
    return (
      base.replace(/\/+$/, "") +
      prefix.replace(/\/+$/, "") +
      "/oauth/google/callback"
    );
  }

  function renderReview() {
    document.getElementById("wiz-rev-redirect").textContent = computeRedirect();
    document.getElementById("wiz-rev-client-id").textContent =
      state.client_id || "—";
    const sec = state.client_secret || "";
    const masked = sec ? "•".repeat(Math.min(sec.length, 12)) : "—";
    const secEl = document.getElementById("wiz-rev-client-secret");
    secEl.textContent = masked;
    secEl.dataset.actual = sec;
    secEl.dataset.shown = "false";
    document.getElementById("wiz-rev-auto-link").textContent =
      state.auto_link_verified_emails ? "Yes" : "No";
    document.getElementById("wiz-rev-mfa").textContent =
      state.enforce_mfa_on_oauth_signin ? "Yes" : "No";
  }

  function renderWriteTarget() {
    const t = document.getElementById("wiz-write-target");
    if (t && state.serverState && state.serverState.target_dir) {
      t.textContent = state.serverState.target_dir + "/" +
        (state.serverState.config_file || "regstack.toml");
    }
  }

  // ---- Errors -----------------------------------------------------------

  function clearErrors() {
    document.querySelectorAll(".wiz-field-error").forEach((el) => el.remove());
    document
      .querySelectorAll(".wiz-field.has-error")
      .forEach((el) => el.classList.remove("has-error"));
    document.getElementById("wiz-errors").textContent = "";
  }

  function showErrors(errors) {
    let formMsg = "";
    errors.forEach((err) => {
      const input = document.querySelector(
        `.wiz-step.is-active [name="${err.field}"]`
      );
      if (input) {
        const field = input.closest(".wiz-field") || input.parentElement;
        if (field) {
          field.classList.add("has-error");
          const msg = document.createElement("div");
          msg.className = "wiz-field-error";
          msg.textContent = err.message;
          field.appendChild(msg);
        }
      } else {
        formMsg = formMsg ? formMsg + " " + err.message : err.message;
      }
    });
    if (formMsg) document.getElementById("wiz-errors").textContent = formMsg;
  }

  // ---- Actions ----------------------------------------------------------

  document.getElementById("wiz-next").addEventListener("click", async () => {
    const result = await api(
      "POST",
      `/api/step/${currentStep}/validate`,
      stateForServer()
    );
    if (result && result.ok) {
      gotoStep(currentStep + 1);
    } else if (result) {
      if (typeof result.jump_to === "number") {
        gotoStep(result.jump_to);
        // Render the destination, then surface the errors there.
        setTimeout(() => showErrors(result.errors || []), 0);
      } else {
        showErrors(result.errors || []);
      }
    }
  });

  document.getElementById("wiz-back").addEventListener("click", () => {
    gotoStep(currentStep - 1);
  });

  document.querySelectorAll("[data-goto]").forEach((btn) => {
    btn.addEventListener("click", () => gotoStep(parseInt(btn.dataset.goto, 10)));
  });

  document.querySelectorAll("[data-copy-target]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const target = document.querySelector(btn.dataset.copyTarget);
      if (!target) return;
      try {
        await navigator.clipboard.writeText(target.textContent || "");
        const orig = btn.textContent;
        btn.textContent = "Copied";
        setTimeout(() => (btn.textContent = orig), 1500);
      } catch (e) {
        // Clipboard API not available — leave the user to select manually.
      }
    });
  });

  document
    .getElementById("wiz-toggle-secret")
    .addEventListener("click", () => {
      const el = document.getElementById("wiz-rev-client-secret");
      const shown = el.dataset.shown === "true";
      if (shown) {
        el.textContent = "•".repeat(Math.min((el.dataset.actual || "").length, 12));
        el.dataset.shown = "false";
        document.getElementById("wiz-toggle-secret").textContent = "Show";
      } else {
        el.textContent = el.dataset.actual || "";
        el.dataset.shown = "true";
        document.getElementById("wiz-toggle-secret").textContent = "Hide";
      }
    });

  document.getElementById("wiz-write").addEventListener("click", async () => {
    const btn = document.getElementById("wiz-write");
    btn.disabled = true;
    const result = await api("POST", "/api/write", stateForServer());
    btn.disabled = false;
    if (!result) return;
    if (!result.ok) {
      if (typeof result.jump_to === "number") {
        gotoStep(result.jump_to);
        setTimeout(() => showErrors(result.errors || []), 0);
      } else {
        showErrors(result.errors || []);
      }
      return;
    }
    document.querySelector('[data-step="11"] [data-when="pre-write"]').hidden = true;
    const post = document.querySelector('[data-step="11"] [data-when="post-write"]');
    post.hidden = false;
    document.getElementById("wiz-write-summary").textContent =
      `${result.config_diff} in ${result.config_path}; ${result.secrets_diff} in ${result.secrets_path}.`;
  });

  document.getElementById("wiz-close").addEventListener("click", async () => {
    await api("POST", "/api/done", {});
    sessionStorage.removeItem(STORAGE_KEY);
    try {
      window.close();
    } catch (e) {
      /* webview may close on its own */
    }
  });

  // ---- Storage ----------------------------------------------------------

  function loadState() {
    try {
      const raw = sessionStorage.getItem(STORAGE_KEY);
      return raw ? JSON.parse(raw) : {};
    } catch (e) {
      return {};
    }
  }

  function saveState() {
    try {
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify(state));
    } catch (e) {
      /* quota — best-effort */
    }
  }

  function stateForServer() {
    const out = {};
    Object.keys(state).forEach((k) => {
      if (k === "serverState") return;
      out[k] = state[k];
    });
    return out;
  }

  // ---- HTTP -------------------------------------------------------------

  async function api(method, path, body) {
    const init = {
      method,
      headers: { "X-Wizard-Token": TOKEN },
    };
    if (body !== undefined) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(body);
    }
    let response;
    try {
      response = await fetch(path, init);
    } catch (e) {
      document.getElementById("wiz-errors").textContent =
        "Network error — is the wizard server still running?";
      return null;
    }
    let data = null;
    try {
      data = await response.json();
    } catch (e) {
      /* non-JSON */
    }
    if (response.status === 401) {
      document.getElementById("wiz-errors").textContent =
        "Wizard session expired. Restart `regstack oauth setup`.";
      return null;
    }
    return data;
  }
})();
