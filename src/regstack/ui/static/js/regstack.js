// regstack — small client for the SSR pages. Reads its endpoints from
// data attributes on <body> so a single static file works for every host.

(function () {
  "use strict";

  const STORAGE_KEY = "regstack.access_token";
  const MFA_PENDING_KEY = "regstack.mfa_pending";
  const body = document.body;
  const apiPrefix = body.dataset.rsApi || "/api/auth";
  const uiPrefix = body.dataset.rsUi || "/account";
  const page = body.dataset.rsPage;

  const messageEl = document.querySelector("[data-rs-message]");

  function getToken() {
    return window.localStorage.getItem(STORAGE_KEY);
  }

  function setToken(token) {
    if (token) {
      window.localStorage.setItem(STORAGE_KEY, token);
    } else {
      window.localStorage.removeItem(STORAGE_KEY);
    }
  }

  function showMessage(text, tone) {
    if (!messageEl) return;
    messageEl.textContent = text;
    messageEl.hidden = false;
    if (tone) {
      messageEl.dataset.rsTone = tone;
    } else {
      delete messageEl.dataset.rsTone;
    }
  }

  function clearMessage() {
    if (!messageEl) return;
    messageEl.hidden = true;
    messageEl.textContent = "";
    delete messageEl.dataset.rsTone;
  }

  function tokenFromQuery() {
    const params = new URLSearchParams(window.location.search);
    return params.get("token");
  }

  async function api(method, path, body, withAuth = false) {
    const headers = { "content-type": "application/json" };
    if (withAuth) {
      const token = getToken();
      if (!token) {
        window.location.href = uiPrefix + "/login";
        throw new Error("not authenticated");
      }
      headers["authorization"] = "Bearer " + token;
    }
    const res = await fetch(apiPrefix + path, {
      method: method,
      headers: headers,
      body: body == null ? undefined : JSON.stringify(body),
    });
    let payload = null;
    try {
      payload = await res.json();
    } catch (_) {
      payload = null;
    }
    return { ok: res.ok, status: res.status, body: payload };
  }

  function formData(form) {
    const out = {};
    for (const [key, value] of new FormData(form).entries()) {
      out[key] = value;
    }
    return out;
  }

  function detailFromError(payload, fallback) {
    if (!payload) return fallback;
    if (typeof payload.detail === "string") return payload.detail;
    if (Array.isArray(payload.detail) && payload.detail.length) {
      return payload.detail.map((e) => e.msg || e).join("; ");
    }
    return fallback;
  }

  function on(formName, handler) {
    const form = document.querySelector(`[data-rs-form="${formName}"]`);
    if (!form) return;
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      clearMessage();
      const submitButton = form.querySelector("button[type='submit']");
      if (submitButton) submitButton.disabled = true;
      try {
        await handler(formData(form), form);
      } catch (err) {
        showMessage(err.message || String(err), "error");
      } finally {
        if (submitButton) submitButton.disabled = false;
      }
    });
  }

  // --- Page wiring -----------------------------------------------------

  async function wireLogin() {
    if (getToken()) {
      window.location.href = uiPrefix + "/me";
      return;
    }
    on("login", async (data) => {
      const res = await api("POST", "/login", data);
      if (!res.ok) {
        throw new Error(detailFromError(res.body, "Login failed."));
      }
      if (res.body && res.body.status === "mfa_required") {
        window.sessionStorage.setItem(MFA_PENDING_KEY, res.body.mfa_pending_token);
        window.location.href = uiPrefix + "/mfa-confirm";
        return;
      }
      setToken(res.body.access_token);
      window.location.href = uiPrefix + "/me";
    });
  }

  async function wireMfaConfirm() {
    const pending = window.sessionStorage.getItem(MFA_PENDING_KEY);
    if (!pending) {
      window.location.href = uiPrefix + "/login";
      return;
    }
    on("mfa-confirm", async (data) => {
      const res = await api("POST", "/login/mfa-confirm", {
        mfa_pending_token: pending,
        code: data.code,
      });
      if (!res.ok) {
        throw new Error(detailFromError(res.body, "Code rejected."));
      }
      window.sessionStorage.removeItem(MFA_PENDING_KEY);
      setToken(res.body.access_token);
      window.location.href = uiPrefix + "/me";
    });
  }

  async function wireRegister() {
    if (getToken()) {
      window.location.href = uiPrefix + "/me";
      return;
    }
    on("register", async (data) => {
      const res = await api("POST", "/register", data);
      if (!res.ok) {
        throw new Error(detailFromError(res.body, "Registration failed."));
      }
      if (res.body && res.body.status === "pending_verification") {
        showMessage(
          "Account created — please check " + (data.email || "your email") + " for a verification link.",
          "success"
        );
      } else {
        showMessage("Account created. You can now sign in.", "success");
      }
    });
  }

  async function wireForgot() {
    on("forgot", async (data) => {
      const res = await api("POST", "/forgot-password", data);
      if (!res.ok) {
        throw new Error(detailFromError(res.body, "Request failed."));
      }
      showMessage(
        "If an account exists for that email, a reset link has been sent.",
        "success"
      );
    });
  }

  async function wireReset() {
    const tokenInput = document.querySelector("[data-rs-token]");
    const queryToken = tokenFromQuery();
    if (tokenInput && queryToken) tokenInput.value = queryToken;
    on("reset", async (data) => {
      if (!data.token) {
        throw new Error("Missing reset token. Use the link from your email.");
      }
      const res = await api("POST", "/reset-password", {
        token: data.token,
        new_password: data.new_password,
      });
      if (!res.ok) {
        throw new Error(detailFromError(res.body, "Reset failed."));
      }
      showMessage("Password reset. Redirecting to sign in…", "success");
      setToken(null);
      setTimeout(() => {
        window.location.href = uiPrefix + "/login";
      }, 1200);
    });
  }

  async function wireVerify() {
    const status = document.querySelector("[data-rs-status]");
    const token = tokenFromQuery();
    if (!token) {
      if (status) status.textContent = "Missing verification token.";
      showMessage("No token in URL.", "error");
      return;
    }
    const res = await api("POST", "/verify", { token: token });
    if (!res.ok) {
      const msg = detailFromError(res.body, "Verification failed.");
      if (status) status.textContent = msg;
      showMessage(msg, "error");
      return;
    }
    if (status) status.textContent = "Email confirmed — you can sign in.";
    showMessage("Email confirmed.", "success");
  }

  async function wireConfirmEmailChange() {
    const status = document.querySelector("[data-rs-status]");
    const token = tokenFromQuery();
    if (!token) {
      if (status) status.textContent = "Missing token.";
      showMessage("No token in URL.", "error");
      return;
    }
    const res = await api("POST", "/confirm-email-change", { token: token });
    if (!res.ok) {
      const msg = detailFromError(res.body, "Confirmation failed.");
      if (status) status.textContent = msg;
      showMessage(msg, "error");
      return;
    }
    setToken(null); // bulk revoke fired — old session is dead
    if (status) status.textContent = "Email updated — please sign in again.";
    showMessage("Email updated. Sign in with your new address.", "success");
  }

  async function wireMe() {
    if (!getToken()) {
      window.location.href = uiPrefix + "/login";
      return;
    }
    const res = await api("GET", "/me", null, true);
    if (!res.ok) {
      setToken(null);
      window.location.href = uiPrefix + "/login";
      return;
    }
    fillAccount(res.body);

    on("update-profile", async (data) => {
      const r = await api("PATCH", "/me", { full_name: data.full_name || null }, true);
      if (!r.ok) throw new Error(detailFromError(r.body, "Update failed."));
      fillAccount(r.body);
      showMessage("Profile updated.", "success");
    });

    on("change-password", async (data) => {
      const r = await api("POST", "/change-password", data, true);
      if (!r.ok) throw new Error(detailFromError(r.body, "Change failed."));
      setToken(null);
      showMessage("Password changed. Redirecting to sign in…", "success");
      setTimeout(() => {
        window.location.href = uiPrefix + "/login";
      }, 1200);
    });

    on("change-email", async (data) => {
      const r = await api("POST", "/change-email", data, true);
      if (!r.ok) throw new Error(detailFromError(r.body, "Change failed."));
      showMessage(
        "Confirmation sent to " + data.new_email + ". Click the link to finish.",
        "success"
      );
    });

    on("delete-account", async (data) => {
      if (!window.confirm("This permanently deletes your account. Continue?")) return;
      const r = await api("DELETE", "/account", data, true);
      if (!r.ok) throw new Error(detailFromError(r.body, "Delete failed."));
      setToken(null);
      window.location.href = uiPrefix + "/login";
    });

    wireMfaSection(res.body);

    const logoutBtn = document.querySelector("[data-rs-action='logout']");
    if (logoutBtn) {
      logoutBtn.addEventListener("click", async () => {
        try {
          await api("POST", "/logout", null, true);
        } catch (_) {
          // ignore — we're signing out client-side anyway
        }
        setToken(null);
        window.location.href = uiPrefix + "/login";
      });
    }
  }

  function wireMfaSection(user) {
    const section = document.querySelector("[data-rs-mfa-section]");
    if (!section) return;

    const status = section.querySelector("[data-rs-mfa-status]");
    const enableBlock = section.querySelector("[data-rs-mfa-enable]");
    const disableBlock = section.querySelector("[data-rs-mfa-disable]");
    const confirmForm = section.querySelector(
      "[data-rs-form='phone-setup-confirm']"
    );

    if (user.is_mfa_enabled) {
      if (status) {
        status.textContent =
          "Enabled — sign-in codes go to " + (user.phone_number || "your phone") + ".";
      }
      enableBlock.hidden = true;
      disableBlock.hidden = false;
    } else {
      if (status) status.textContent = "Disabled. Add a phone number to enable.";
      enableBlock.hidden = false;
      disableBlock.hidden = true;
    }

    let setupPendingToken = null;
    on("phone-setup-start", async (data) => {
      const res = await api("POST", "/phone/start", data, true);
      if (!res.ok) throw new Error(detailFromError(res.body, "Send failed."));
      setupPendingToken = res.body.pending_token;
      confirmForm.hidden = false;
      showMessage("Code sent — enter it below to enable 2FA.", "success");
    });

    on("phone-setup-confirm", async (data) => {
      if (!setupPendingToken) {
        throw new Error("Request a code first.");
      }
      const res = await api(
        "POST",
        "/phone/confirm",
        { pending_token: setupPendingToken, code: data.code },
        false
      );
      if (!res.ok) throw new Error(detailFromError(res.body, "Confirm failed."));
      setupPendingToken = null;
      showMessage("SMS 2FA enabled — sign in again to confirm.", "success");
      setTimeout(() => window.location.reload(), 1200);
    });

    on("mfa-disable", async (data) => {
      const res = await fetch(apiPrefix + "/phone", {
        method: "DELETE",
        headers: {
          "content-type": "application/json",
          authorization: "Bearer " + getToken(),
        },
        body: JSON.stringify(data),
      });
      let payload = null;
      try {
        payload = await res.json();
      } catch (_) {
        payload = null;
      }
      if (!res.ok) {
        throw new Error(detailFromError(payload, "Disable failed."));
      }
      showMessage("SMS 2FA disabled.", "success");
      setTimeout(() => window.location.reload(), 1200);
    });
  }

  function fillAccount(user) {
    if (!user) return;
    document.querySelectorAll("[data-rs-field]").forEach((el) => {
      const key = el.dataset.rsField;
      let value = user[key];
      if (key === "is_verified") value = value ? "yes" : "no";
      if (key === "created_at" && value) value = new Date(value).toLocaleString();
      if (value === null || value === undefined || value === "") value = "—";
      el.textContent = String(value);
    });
    const fnInput = document.querySelector(
      "[data-rs-form='update-profile'] input[name='full_name']"
    );
    if (fnInput) fnInput.value = user.full_name || "";
  }

  // Dispatch by page name set on <html>/<body>.
  switch (page) {
    case "login": wireLogin(); break;
    case "register": wireRegister(); break;
    case "forgot": wireForgot(); break;
    case "reset": wireReset(); break;
    case "verify": wireVerify(); break;
    case "confirm-email-change": wireConfirmEmailChange(); break;
    case "mfa-confirm": wireMfaConfirm(); break;
    case "me": wireMe(); break;
    default: break;
  }
})();
