## 1.5
- fix: "Logged in but usage data never arrived" on some networks
    - The usage widget only loads after the site fetches feature flags
    from sdk.split.io. When that is blocked (e.g. by AdGuard Home or
    Pi-hole), the error now says so and asks to allow split.io, instead
    of reporting a page structure change
- Block third-party analytics/survey scripts (Clarity, Medallia, Google
Tag Manager, AWS RUM) to reduce page load on low-powered hosts
- Longer waits for slow hosts: 90s for usage data (was 45s), 60s for the
login form (was 30s)
- Debug improvements
    - Request timeline for the usage widget and failed requests to
    socalgas.com, smartcmobile.com and split.io
    - Page text is saved before the screenshot, and screenshots time out
    after 10s instead of hanging for 30s
    - A login form that never appears is now dumped too

## 1.4
- Retry a failed sync after 5, 15 and 60 minutes instead of waiting for
the next scheduled run
- New `update_interval_hours` option (1-24, default 6)
- `mqtt_topic` is now configurable from the add-on options
- Debug screenshots and page text saved to /share/socalgas/ so they can
be retrieved (previously written inside the container)
- Docker image installs pinned versions from requirements.txt; removed
unused python-dotenv and requests
- fix: login detection
    - Any 4xx/5xx from the login endpoint (e.g. 429 rate limit) fails
    fast with its status code instead of a generic timeout
    - Only a real myaccount.socalgas.com app page counts as logged in;
    a redirect to /ui/error fails immediately
    - Login response body is only logged with debug on
- fix: cycle-boundary handling
    - A usagewidget 204 is only treated as a rollover if no real payload
    arrives within 5s, instead of skipping the run immediately
    - A zero-padded cost-to-date alongside a valid therms-to-date is
    published as a real 0 instead of skipping every run
    - Zero-padded placeholders with a decimal point or sign (e.g.
    "000000000.00") are now detected instead of published as 0.0
- fix: JSON served as text/plain or without a content-type is parsed again
- fix: exit code 2 when stuck on an interstitial is now based on whether
the page was actually left, not whether a click succeeded
- Wait timeouts measured in real time, so 15s/45s limits are accurate

## 1.3
- fix: login rejected with HTTP 403 by SoCalGas bot defense
    - Launch Chromium in new headless mode (channel="chromium") and strip
    "HeadlessChrome" from the user agent; the headless shell advertised
    itself via sec-ch-ua
- Dismiss post-login interstitials (e.g. 2FA prompt) via skip-style
buttons only; exit code 2 if stuck on one
- Treat cycle-boundary payloads (blank fields, zero-padded placeholders,
usagewidget 204) as "not ready" and skip publishing instead of sending 0.0
- Wait for MQTT CONNACK so broker auth failures are reported as such
- Mask email in debug config output

## 1.1.0
- fix: validate usage payload by schema, not URL/key heuristics
    - Replace URL substring matching + "UsageSoFar" key check with
    is_usage_payload(), which validates the actual CostToDate fields
    the parser depends on — closes a gap where capture and parse logic
    checked different things
    - Stop gating success on login_verified; usage_widget_data presence
    (already schema-validated) is sufficient proof of auth, and the
    old gate could false-fail if SoCalGas changes header behavior
    - Raise on non-numeric fields in build_payload instead of silently
    coercing to 0 (schema drift now fails loud, not silent)
    - Confirm MQTT publish actually delivered via is_published(), not
    just handshake completion
    - Stop logging partial AccessToken values in debug output
    - Add explicit timeout to page.goto()

## 1.0.18
- Fixed schema drift validation that could silently publish zero values
- Added MQTT publish delivery confirmation
- Removed unsafe AccessToken logging in debug mode

## 1.0.0
- Initial Commit