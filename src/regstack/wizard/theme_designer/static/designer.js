// regstack theme designer — live SPA.
//
// On boot, hits /api/state to fetch the variable schema, defaults,
// and any previously-saved values. Builds form controls per variable,
// wires them to update the preview pane in real time via CSS custom
// properties applied directly to the .d-preview element. Save POSTs
// the full {light, dark} payload.

(function () {
  "use strict";

  const TOKEN = document.body.dataset.wizardToken;

  // Form state — separate light + dark scopes.
  const state = {
    light: {},
    dark: {},
    activeScope: "light",
    schema: null,
    defaults: { light: {}, dark: {} },
    targetPath: "",
  };

  // ---- Boot -------------------------------------------------------------

  api("GET", "/api/state").then((data) => {
    if (!data) return;
    state.schema = data.schema;
    state.defaults = data.defaults;
    state.targetPath = data.target_dir + "/" + data.filename;
    document.getElementById("d-target-path").textContent = state.targetPath;

    // Seed form state: existing values first, fall back to defaults.
    state.light = { ...data.defaults.light, ...data.existing.light };
    state.dark = { ...data.defaults.dark, ...data.existing.dark };

    buildControls();
    refreshPreview();
  });

  // ---- Build controls ---------------------------------------------------

  function buildControls() {
    const colorRoot = document.getElementById("d-color-fields");
    const fontRoot = document.getElementById("d-font-fields");
    const radiusRoot = document.getElementById("d-radius-fields");
    colorRoot.innerHTML = "";
    fontRoot.innerHTML = "";
    radiusRoot.innerHTML = "";

    state.schema.color_vars.forEach((name) => {
      colorRoot.appendChild(buildColorRow(name));
    });
    state.schema.font_vars.forEach((name) => {
      fontRoot.appendChild(buildTextRow(name));
    });
    state.schema.radius_vars.forEach((name) => {
      radiusRoot.appendChild(buildTextRow(name));
    });
  }

  function currentValue(name) {
    return (state[state.activeScope] || {})[name] || "";
  }

  function setValue(name, value) {
    state[state.activeScope][name] = value;
    refreshPreview();
  }

  function buildColorRow(name) {
    const row = document.createElement("div");
    row.className = "d-row";
    row.dataset.var = name;

    const label = document.createElement("label");
    label.textContent = name;
    label.htmlFor = `d-input-${name}`;

    const wrap = document.createElement("div");
    wrap.style.display = "flex";
    wrap.style.gap = "0.4rem";

    const initial = currentValue(name);
    const isHex = /^#[0-9a-fA-F]{6}$/.test(initial);

    const colorInput = document.createElement("input");
    colorInput.type = "color";
    colorInput.value = isHex ? initial : "#000000";
    colorInput.disabled = !isHex;
    colorInput.title = isHex
      ? "Pick a colour"
      : "rgba() value — edit the text field";

    const textInput = document.createElement("input");
    textInput.type = "text";
    textInput.id = `d-input-${name}`;
    textInput.value = initial;
    textInput.spellcheck = false;

    colorInput.addEventListener("input", () => {
      textInput.value = colorInput.value;
      setValue(name, colorInput.value);
    });
    textInput.addEventListener("input", () => {
      const v = textInput.value;
      if (/^#[0-9a-fA-F]{6}$/.test(v)) {
        colorInput.value = v;
        colorInput.disabled = false;
      } else {
        colorInput.disabled = true;
      }
      setValue(name, v);
    });

    wrap.appendChild(colorInput);
    wrap.appendChild(textInput);
    row.appendChild(label);
    row.appendChild(wrap);
    return row;
  }

  function buildTextRow(name) {
    const row = document.createElement("div");
    row.className = "d-row";
    row.dataset.var = name;

    const label = document.createElement("label");
    label.textContent = name;
    label.htmlFor = `d-input-${name}`;

    const input = document.createElement("input");
    input.type = "text";
    input.id = `d-input-${name}`;
    input.value = currentValue(name);
    input.spellcheck = false;
    input.addEventListener("input", () => setValue(name, input.value));

    row.appendChild(label);
    row.appendChild(input);
    return row;
  }

  // ---- Preview ----------------------------------------------------------

  function refreshPreview() {
    const preview = document.getElementById("d-preview");
    preview.classList.toggle("is-dark", state.activeScope === "dark");
    const vars = state[state.activeScope] || {};
    Object.keys(vars).forEach((name) => {
      preview.style.setProperty(name, vars[name]);
    });
  }

  // ---- Tab switching ----------------------------------------------------

  document.querySelectorAll(".d-tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      document
        .querySelectorAll(".d-tab")
        .forEach((t) => t.classList.remove("is-active"));
      tab.classList.add("is-active");
      state.activeScope = tab.dataset.scope;
      // Re-bind input values to the new scope.
      buildControls();
      refreshPreview();
    });
  });

  // ---- Save / Reset / Copy ---------------------------------------------

  document.getElementById("d-save").addEventListener("click", async () => {
    setStatus("Saving…", "");
    const payload = { light: state.light, dark: state.dark };
    const response = await api("POST", "/api/save", payload);
    if (!response) return;
    if (response.ok) {
      setStatus(
        `Saved ${response.bytes_written} bytes · ${response.light_count} light · ${response.dark_count} dark`,
        "ok"
      );
      clearInputErrors();
    } else {
      setStatus("Validation failed — see field errors", "error");
      showInputErrors(response.errors || []);
    }
  });

  document.getElementById("d-reset").addEventListener("click", () => {
    state.light = { ...state.defaults.light };
    state.dark = { ...state.defaults.dark };
    buildControls();
    refreshPreview();
    setStatus("Reset to defaults — click Save to write.", "");
  });

  document.getElementById("d-copy").addEventListener("click", async () => {
    const css = generateCss(state.light, state.dark);
    try {
      await navigator.clipboard.writeText(css);
      setStatus("Copied CSS to clipboard.", "ok");
    } catch (e) {
      setStatus("Clipboard not available.", "error");
    }
  });

  function generateCss(light, dark) {
    const lines = ["/* regstack-theme.css */", "", ":root {"];
    Object.keys(light).forEach((k) => lines.push(`  ${k}: ${light[k]};`));
    lines.push("}");
    if (Object.keys(dark).length) {
      lines.push("", "@media (prefers-color-scheme: dark) {", "  :root {");
      Object.keys(dark).forEach((k) => lines.push(`    ${k}: ${dark[k]};`));
      lines.push("  }", "}");
    }
    return lines.join("\n") + "\n";
  }

  // ---- Errors / status --------------------------------------------------

  function setStatus(text, tone) {
    const el = document.getElementById("d-status");
    el.textContent = text;
    el.dataset.tone = tone || "";
  }

  function clearInputErrors() {
    document
      .querySelectorAll(".d-row input.has-error")
      .forEach((el) => el.classList.remove("has-error"));
  }

  function showInputErrors(errors) {
    clearInputErrors();
    errors.forEach((err) => {
      const row = document.querySelector(`.d-row[data-var="${err.field}"]`);
      if (!row) return;
      row.querySelectorAll("input").forEach((el) => el.classList.add("has-error"));
    });
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
      setStatus("Network error.", "error");
      return null;
    }
    let data = null;
    try {
      data = await response.json();
    } catch (e) {
      /* non-JSON */
    }
    if (response.status === 401) {
      setStatus("Session expired. Restart the designer.", "error");
      return null;
    }
    return data;
  }
})();
