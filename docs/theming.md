# Theming the SSR pages

The bundled UI ships a structural stylesheet (`core.css`) and a default
theme stylesheet (`theme.css`). Hosts pick one of three integration
levels depending on how much they want to override.

## Level 1 — swap one stylesheet

Set `config.theme_css_url` to a URL where your host serves a custom
`theme.css`. regstack loads it **after** the bundled defaults so any
`--rs-*` variable you redefine wins.

```toml
# regstack.toml
theme_css_url = "/static/my-theme.css"
```

```python
# host
app.mount("/static", StaticFiles(directory="static"))
```

Your `static/my-theme.css`:

```css
:root {
  --rs-bg: #FAF7F2;
  --rs-accent: #5C0A2D;
  --rs-accent-fg: #FAF7F2;
  --rs-radius: 4px;
  --rs-font-display: "Playfair Display", Georgia, serif;
  --rs-font-body: "DM Sans", system-ui, sans-serif;
}
```

That's it — every regstack page picks up the new palette. See
`examples/minimal/branding/theme.css` for a working wine-themed
example.

## Variables

```{list-table}
:header-rows: 1
:widths: 25 30 45

* - Name
  - Default (light)
  - Notes

* - `--rs-bg`
  - `#ffffff`
  - Page background and input fill.
* - `--rs-bg-hover`
  - `#f3f4f6`
  - Hover state on neutral buttons.
* - `--rs-surface`
  - `#ffffff`
  - Card / header surface.
* - `--rs-fg`
  - `#111827`
  - Primary text.
* - `--rs-fg-muted`
  - `#4b5563`
  - Secondary text (labels, footer).
* - `--rs-border`
  - `#e5e7eb`
  - Card border, input border.
* - `--rs-accent`
  - `#2563eb`
  - Primary button background, links, focus ring.
* - `--rs-accent-fg`
  - `#ffffff`
  - Text on `--rs-accent`.
* - `--rs-accent-bg`
  - `rgba(37, 99, 235, 0.08)`
  - Subtle accent surface (success messages).
* - `--rs-danger`
  - `#b91c1c`
  - Destructive button + error tone.
* - `--rs-danger-fg`
  - `#ffffff`
  - Text on `--rs-danger`.
* - `--rs-danger-bg`
  - `rgba(185, 28, 28, 0.08)`
  - Subtle danger surface (error messages).
* - `--rs-radius`
  - `6px`
  - Corner radius across cards / inputs / buttons.
* - `--rs-shadow`
  - subtle two-stop
  - Card elevation.
* - `--rs-font-display`
  - system stack
  - Headings + brand wordmark.
* - `--rs-font-body`
  - system stack
  - Body copy, form fields, buttons.
```

The bundled `theme.css` also defines a `prefers-color-scheme: dark`
block redefining the same variables, so a host that overrides them
inside `:root` only is light-only by default. To support both schemes,
use the same `prefers-color-scheme` query in your file.

## Level 2 — replace specific templates

Drop a same-named file into a directory you register with
`add_template_dir()`. Jinja2's `ChoiceLoader` resolves host-first.

```python
regstack.add_template_dir(Path("/app/templates"))
```

Then:

```
/app/templates/
└── auth/
    └── login.html        ← overrides only the login page
```

Non-overridden templates still come from the bundled defaults. The
bundled `base.html` exposes named blocks you can override piecemeal:

- `{% block title %}` — page `<title>`.
- `{% block extra_head %}` — extra `<link>` or `<meta>` tags.
- `{% block brand %}` — header brand mark + logo.
- `{% block content %}` — main card body.
- `{% block footer %}` — small footer text.

```jinja
{# /app/templates/base.html — full override #}
{% extends "base.html" %}
{% block brand %}
<a class="rs-brand" href="{{ ui_prefix }}/me">
  <img src="/static/logo.svg" alt="" height="32">
  <span class="rs-brand-name">Acme Wine</span>
</a>
{% endblock %}
```

If your file extends `base.html`, Jinja's `ChoiceLoader` still resolves
`base.html` to the bundled one — extending host-first only works if
you provide a different name. To replace `base.html` itself, drop your
own file at the same path.

## Level 3 — swap the JavaScript

If your auth UX is fundamentally different (cookie-based session,
different storage strategy, additional steps), replace `regstack.js`
entirely. Mount your own static dir and edit the `<script>` tag in your
custom `base.html`. The bundled JS is ~250 lines; reading it is the
fastest route to understanding the contract.

The `<body data-rs-api>` and `<body data-rs-ui>` attributes are the
ABI between templates and JS — keep them in any custom `base.html` so
your script can find the API origin without hard-coding.

## Brand context

Every template renders with `app_name`, `brand_logo_url`, and
`brand_tagline` from config. For a no-template branding pass:

```toml
app_name = "Acme Wine Cellar"
brand_logo_url = "https://acme.example/logo.svg"
brand_tagline = "Beta"
```
