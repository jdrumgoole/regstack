// SES wizard SPA. Drives the nine-step flow defined in
// validators.py. Deliberately minimal — every step renders a fragment
// inline, validates server-side on Next, and stops on errors.
//
// Expand for production: nicer per-step layouts, inline domain-
// verification links, history-stack navigation. The OAuth wizard's
// wizard.js is the reference if/when the UX is polished further.

const TOKEN = document.body.dataset.token;
const NUM_STEPS = parseInt(document.body.dataset.numSteps, 10);

// Accumulated wizard state, posted in full to every validate / write
// call so the server can replay validation from scratch.
const state = {
  existing_ses: false,
  replace_existing: false,
  ses_region: "us-east-1",
  credential_source: "chain",
  ses_profile: "",
  ses_access_key_id: "",
  ses_secret_access_key: "",
  from_address: "",
  aws_in_sandbox: false,
  sandbox_attested: false,
  test_recipient: "",
  skip_test_send: false,
};

let stepIndex = 0;
let lastAws = null; // last AWS-state payload for review-step display

const root = document.getElementById("wizard-root");
const btnBack = document.getElementById("btn-back");
const btnNext = document.getElementById("btn-next");
const stepCurrent = document.getElementById("step-current");

btnBack.addEventListener("click", () => goTo(stepIndex - 1));
btnNext.addEventListener("click", advance);

(async function init() {
  const initial = await api("GET", "/api/state");
  if (initial) {
    state.existing_ses = !!initial.existing_ses;
    if (initial.from_address) state.from_address = initial.from_address;
  }
  render();
})();

function api(method, path, body) {
  const init = { method, headers: { "X-Wizard-Token": TOKEN } };
  if (body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  return fetch(path, init).then((r) => r.json()).catch(() => null);
}

function goTo(n) {
  if (n < 0 || n >= NUM_STEPS) return;
  stepIndex = n;
  stepCurrent.textContent = String(n + 1);
  btnBack.disabled = n === 0;
  btnNext.textContent = n === NUM_STEPS - 1 ? "Write" : n === NUM_STEPS - 2 ? "Confirm" : "Next";
  render();
}

async function advance() {
  // Snapshot form into state.
  syncForm();
  // Validate the current step server-side.
  const result = await api("POST", `/api/step/${stepIndex}/validate`, state);
  if (!result) {
    showRootError("Validation request failed; check the server log.");
    return;
  }
  if (result.aws) lastAws = result.aws;
  if (result.aws && "in_sandbox" in result.aws) {
    state.aws_in_sandbox = !!result.aws.in_sandbox;
  }
  if (!result.ok) {
    renderErrors(result.errors || [], result.warnings || []);
    return;
  }
  if (stepIndex === NUM_STEPS - 1) {
    await doWrite();
    return;
  }
  goTo(stepIndex + 1);
}

async function doWrite() {
  const result = await api("POST", "/api/write", state);
  if (!result || !result.ok) {
    showRootError("Write failed: " + JSON.stringify(result?.errors || result));
    return;
  }
  root.innerHTML = `
    <h2>Done</h2>
    <p>Configuration written successfully.</p>
    <pre class="diff-pre">${escapeHTML(result.config_diff)}\n${escapeHTML(result.secrets_diff)}</pre>
    <p><code>${escapeHTML(result.config_path)}</code></p>
    <p><code>${escapeHTML(result.secrets_path)}</code></p>
    <p>You can close this window. Run <code>regstack doctor --send-test-email you@example.com</code> to confirm end-to-end.</p>
  `;
  btnBack.disabled = true;
  btnNext.disabled = true;
  await api("POST", "/api/done", {});
}

function syncForm() {
  document.querySelectorAll("[data-bind]").forEach((el) => {
    const key = el.dataset.bind;
    if (el.type === "checkbox") {
      state[key] = el.checked;
    } else if (el.type === "radio") {
      if (el.checked) state[key] = el.value;
    } else {
      state[key] = el.value;
    }
  });
}

function render() {
  root.innerHTML = STEP_HTML[stepIndex](state, lastAws);
}

function renderErrors(errors, warnings) {
  errors.forEach((e) => {
    const field = root.querySelector(`[data-error-for="${e.field}"]`);
    if (field) {
      field.textContent = e.message;
      field.className = "error";
    } else {
      showRootError(`${e.field}: ${e.message}`);
    }
  });
  warnings.forEach((w) => {
    const field = root.querySelector(`[data-warning-for="${w.field}"]`);
    if (field) {
      field.textContent = w.message;
      field.className = "warning";
    }
  });
}

function showRootError(msg) {
  const existing = root.querySelector(".error.root");
  if (existing) existing.remove();
  const div = document.createElement("p");
  div.className = "error root";
  div.textContent = msg;
  root.appendChild(div);
}

function escapeHTML(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// Step renderers. Each returns the inner HTML for the step. The
// renderer reads from `s` (state snapshot) and `aws` (last AWS probe
// payload). Form elements use `data-bind="<state-key>"` so the shared
// syncForm() reads them back into state on Next click.

const STEP_HTML = [
  // 0 — welcome
  () => `
    <h2>Welcome</h2>
    <p>This wizard configures the SES email backend.</p>
    <p>We'll validate against AWS as we go. Have your AWS region, credential source (profile / explicit keys / instance role), and a verified sender domain ready.</p>
  `,
  // 1 — detect existing
  (s) => s.existing_ses ? `
    <h2>Existing SES configuration detected</h2>
    <p>This <code>regstack.toml</code> already has <code>[email].backend = "ses"</code>.</p>
    <label><input type="checkbox" data-bind="replace_existing" ${s.replace_existing ? "checked" : ""}> Replace it with the new values from this wizard.</label>
    <p data-error-for="replace_existing"></p>
  ` : `
    <h2>No existing SES configuration</h2>
    <p>The wizard will add a fresh <code>[email]</code> table.</p>
  `,
  // 2 — region
  (s) => `
    <h2>AWS region</h2>
    <p>SES is regional. Pick the region you've verified your sender domain in.</p>
    <label for="ses_region">Region</label>
    <input id="ses_region" type="text" data-bind="ses_region" value="${escapeHTML(s.ses_region)}" placeholder="eu-west-1">
    <p data-error-for="ses_region"></p>
  `,
  // 3 — credentials
  (s, aws) => `
    <h2>Credentials</h2>
    <p>How should regstack authenticate to AWS?</p>
    <label><input type="radio" name="credential_source" data-bind="credential_source" value="profile" ${s.credential_source === "profile" ? "checked" : ""}> Named AWS profile (laptop dev)</label>
    <label><input type="radio" name="credential_source" data-bind="credential_source" value="explicit" ${s.credential_source === "explicit" ? "checked" : ""}> Explicit access key + secret (containerised prod, secrets manager)</label>
    <label><input type="radio" name="credential_source" data-bind="credential_source" value="chain" ${s.credential_source === "chain" ? "checked" : ""}> Boto3 default credential chain (IAM instance role)</label>
    <p data-error-for="credential_source"></p>
    <label for="ses_profile">Profile name (profile mode)</label>
    <input id="ses_profile" type="text" data-bind="ses_profile" value="${escapeHTML(s.ses_profile)}">
    <p data-error-for="ses_profile"></p>
    <label for="ses_access_key_id">Access key ID (explicit mode)</label>
    <input id="ses_access_key_id" type="text" data-bind="ses_access_key_id" value="${escapeHTML(s.ses_access_key_id)}">
    <p data-error-for="ses_access_key_id"></p>
    <label for="ses_secret_access_key">Secret access key (explicit mode)</label>
    <input id="ses_secret_access_key" type="password" data-bind="ses_secret_access_key" value="${escapeHTML(s.ses_secret_access_key)}">
    <p data-error-for="ses_secret_access_key"></p>
    ${awsBlock(aws, "credential_ok", (a) => `<p><strong>Resolved:</strong> ${escapeHTML(a.arn || "")}</p>`)}
  `,
  // 4 — sender
  (s, aws) => `
    <h2>Sender address</h2>
    <p>SES requires the sender's address (or its domain) to be verified in this region.</p>
    <label for="from_address">From address</label>
    <input id="from_address" type="email" data-bind="from_address" value="${escapeHTML(s.from_address)}" placeholder="noreply@app.example.com">
    <p data-error-for="from_address"></p>
    ${awsBlock(aws, "verified", (a) => `
      <p><strong>Address:</strong> ${escapeHTML(a.address_status)} &middot; <strong>Domain:</strong> ${escapeHTML(a.domain_status)}</p>
      ${a.verified ? "" : `<p>Verify in the <a href="https://console.aws.amazon.com/ses/home#verified-senders-domain:" target="_blank">SES console</a> and retry.</p>`}
    `)}
  `,
  // 5 — sandbox
  (s, aws) => `
    <h2>SES sandbox check</h2>
    ${awsBlock(aws, "in_sandbox", (a) => a.in_sandbox ? `
      <p><strong>This AWS account is in the SES sandbox.</strong> SES will reject email to any address that isn't separately verified. Graduate out of the sandbox in the AWS console for production use.</p>
      <label><input type="checkbox" data-bind="sandbox_attested" ${s.sandbox_attested ? "checked" : ""}> I understand. This is for development or every recipient is independently verified.</label>
      <p data-error-for="sandbox_attested"></p>
    ` : `<p>Not in sandbox. Production sends are allowed.</p>`)}
    ${!aws ? `<p>(Sandbox state will be checked when you click Next.)</p>` : ""}
    <p data-warning-for="_form"></p>
  `,
  // 6 — test send
  (s, aws) => `
    <h2>Send a test email</h2>
    <label><input type="checkbox" data-bind="skip_test_send" ${s.skip_test_send ? "checked" : ""}> Skip the test send.</label>
    <label for="test_recipient">Test recipient (defaults to From address)</label>
    <input id="test_recipient" type="email" data-bind="test_recipient" value="${escapeHTML(s.test_recipient || s.from_address)}">
    <p data-error-for="test_recipient"></p>
    ${awsBlock(aws, "send_ok", (a) => a.send_ok ? `<p>Test send accepted by SES. MessageId: <code>${escapeHTML(a.message_id || "")}</code></p>` : "")}
  `,
  // 7 — review
  (s) => `
    <h2>Review</h2>
    <pre class="diff-pre">
backend         = "ses"
ses_region      = "${escapeHTML(s.ses_region)}"
from_address    = "${escapeHTML(s.from_address)}"
credential mode = "${escapeHTML(s.credential_source)}"
${s.credential_source === "profile" ? `ses_profile     = "${escapeHTML(s.ses_profile)}"` : ""}
${s.credential_source === "explicit" ? `ses_access_key_id = "${escapeHTML(s.ses_access_key_id)}"\n(secret goes to regstack.secrets.env)` : ""}
${s.credential_source === "chain" ? `(no credential keys written — boto3 default chain at runtime)` : ""}
    </pre>
    <p>Clicking Confirm replays validation server-side. Clicking Write at the next step persists the changes.</p>
  `,
  // 8 — write
  () => `
    <h2>Write configuration</h2>
    <p>Click Write to merge into <code>regstack.toml</code> + <code>regstack.secrets.env</code>.</p>
  `,
];

function awsBlock(aws, presenceKey, renderer) {
  if (!aws || !(presenceKey in aws)) return "";
  const ok = aws[presenceKey] === true || aws[presenceKey] === "verified" || aws[presenceKey] === false; // surface either state
  const cls = aws.error ? "error" : "ok";
  return `<div class="aws-state ${cls}">${aws.error ? `<p><strong>AWS:</strong> ${escapeHTML(aws.error)}</p>` : renderer(aws)}</div>`;
}
