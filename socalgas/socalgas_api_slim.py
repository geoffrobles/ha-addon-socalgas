import json
import os
import re
import sys
import time
from datetime import datetime

import paho.mqtt.client as mqtt
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

# Home Assistant add-on config
try:
    with open("/data/options.json") as f:
        config = json.load(f)
except FileNotFoundError:
    print("Using local test config")
    try:
        with open("options.json") as f:
            config = json.load(f)
    except FileNotFoundError:
        print("Error: Configuration file not found.")
        sys.exit(1)

SOCALGAS_EMAIL = config.get("email")
SOCALGAS_PASSWORD = config.get("password")
MQTT_HOST = config.get("mqtt_host")
MQTT_PORT = int(config.get("mqtt_port", 1883))
MQTT_USER = config.get("mqtt_user", "")
MQTT_PASSWORD = config.get("mqtt_password", "")
# `or` so an optional-but-blank schema value falls back to the default.
MQTT_TOPIC = config.get("mqtt_topic") or "home/socalgas/total"

LOGIN_URL = "https://myaccount.socalgas.com/ui/login"
LOGIN_API_FRAGMENT = "/authentication/login"
USAGE_URL_FRAGMENT = "usagewidget"
DEBUG = config.get("debug", False)
# Debug screenshots/page text. /share is reachable from HA (Samba, File editor);
# locally they go in a gitignored folder.
DUMP_DIR = "/share/socalgas" if os.path.isdir("/share") else "debug_dumps"

# Seconds to wait after clicking login before deciding login never happened.
LOGIN_GRACE_SECONDS = 15
# Total seconds to wait for the usage widget payload.
USAGE_WAIT_SECONDS = 90
# Seconds to wait for the login form to render.
LOGIN_FORM_WAIT_SECONDS = 60
# Seconds a usagewidget 204 must stand (with no 200 payload) before we call it a rollover.
USAGE_204_GRACE_SECONDS = 5
# Consecutive seconds on an interstitial before giving up on dismissing it.
INTERSTITIAL_MAX_SECONDS = 10

# Buttons that dismiss a post-login interstitial without agreeing to anything.
# Deliberately excludes "Continue"/"Accept"/"Agree" so we never consent to terms.
INTERSTITIAL_SKIP = re.compile(r"^\s*(skip|not now|remind me later|maybe later|no thanks|close)\s*$", re.I)

# Third-party analytics/survey hosts the page doesn't need. Aborted up front so
# a slow or blackholed tracker can't stall rendering on low-powered add-on
# hosts. First-party socalgas.com scripts, including bot defense, are never
# blocked. split.io must NOT be listed: the site's feature flags come from
# sdk.split.io, and without them the usage widget never loads.
BLOCKED_HOSTS = re.compile(
    r"^https?://([^/]*\.)?(clarity\.ms|medallia\.com|kampyle\.com|"
    r"google-analytics\.com|googletagmanager\.com|doubleclick\.net|facebook\.(net|com)|"
    r"dataplane\.rum\.[^/]*\.amazonaws\.com)(:\d+)?/"
)
# Hosts worth tracing in debug output when diagnosing a missing usage payload.
TRACE_HOSTS = ("socalgas.com", "smartcmobile.com", "split.io")
# Feature-flag service the usage widget depends on (see BLOCKED_HOSTS).
FEATURE_FLAG_HOST = "sdk.split.io"
FEATURE_FLAG_HINT = (
    f" The site's feature-flag service ({FEATURE_FLAG_HOST}) never responded, and the usage "
    "widget won't load without it. If you run a DNS ad-blocker (AdGuard Home, Pi-hole), "
    "allowlist split.io for the Home Assistant host."
)


class IncompleteDataError(Exception):
    """Cycle-boundary payload: projection not yet computed upstream. Not a failure."""


class InterstitialBlockedError(Exception):
    """Login worked but SoCalGas parked us on an interstitial we couldn't dismiss."""


def is_usage_payload(data):
    try:
        cost_data = data["VerificationResponse"]["UserDetail"]["CostToDate"]
        return all(
            field in cost_data
            for field in ["ProjThermsToDateQty", "ProjThermsQty", "ProjBillAmt", "ProjCostToDateAmt"]
        )
    except (KeyError, TypeError):
        return False


def is_json_response(response):
    # Some gateways serve JSON as text/plain or omit the header; let
    # response.json() decide for those rather than dropping the payload.
    ctype = response.headers.get("content-type", "").lower()
    return not ctype or "json" in ctype or ctype.startswith("text/plain")


def is_post_login_url(url):
    """True only for a real authenticated app page, not login, error, or a foreign/blank page."""
    if not url.startswith("https://myaccount.socalgas.com/ui/"):
        return False
    return "/ui/login" not in url and "/ui/error" not in url


def login_and_get_usage():
    with sync_playwright() as p:
        # channel="chromium" uses new headless (full browser) instead of the
        # headless shell, whose sec-ch-ua header advertises "HeadlessChrome"
        # and gets the login POST rejected by the bot-defense edge with a 403.
        with p.chromium.launch(
            headless=True,
            channel="chromium",
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-setuid-sandbox",
                "--disable-blink-features=AutomationControlled",
            ],
        ) as browser:
            # Derive the UA from the bundled Chromium (so it stays in sync with
            # sec-ch-ua) and only strip the "Headless" marker.
            probe = browser.new_page()
            user_agent = probe.evaluate("navigator.userAgent").replace("HeadlessChrome", "Chrome")
            probe.close()
            context = browser.new_context(
                viewport={"width": 1280, "height": 900},
                user_agent=user_agent,
            )
            page = context.new_page()
            Stealth().apply_stealth_sync(page)
            context.route(BLOCKED_HOSTS, lambda route: route.abort())
            t0 = time.monotonic()

            state = {
                "login_verified": False,
                "login_failed": None,
                "usage_widget_data": None,
                "usage_empty_at": None,
                "feature_flags_ok": False,
            }

            def flag_hint():
                return "" if state["feature_flags_ok"] else FEATURE_FLAG_HINT

            def handle_request(request):
                if "accesstoken" in (k.lower() for k in request.headers.keys()):
                    if not state["login_verified"] and DEBUG:
                        print("Captured AccessToken header")
                    state["login_verified"] = True

            def handle_response(response):
                url = response.url

                if FEATURE_FLAG_HOST in url and response.status < 400:
                    state["feature_flags_ok"] = True

                if USAGE_URL_FRAGMENT in url.lower() and response.status == 204:
                    # Possible cycle rollover (no body). Only acted on if no 200
                    # payload follows within USAGE_204_GRACE_SECONDS.
                    if state["usage_empty_at"] is None:
                        state["usage_empty_at"] = time.monotonic()
                    print(f"204 on usagewidget, possible cycle rollover: {url}")
                    return

                # Any 4xx/5xx from the login endpoint (bad creds, bot block, 429
                # rate limit, outage) fails fast with the real status code.
                if response.status >= 400 and LOGIN_API_FRAGMENT in url:
                    state["login_failed"] = f"HTTP {response.status} from login endpoint"
                    # Body/headers distinguish bad credentials from a WAF/bot block.
                    # Debug only: the body may echo account identifiers.
                    if DEBUG:
                        try:
                            body = response.text()[:500]
                        except Exception as e:
                            body = f"<unreadable: {e}>"
                        headers = {
                            k: v for k, v in response.headers.items()
                            if k.lower() in ("server", "content-type", "via", "cf-ray", "x-cache")
                        }
                        print(f"Login endpoint {response.status}: headers={headers} body={body!r}")
                    return

                if response.status != 200:
                    # Pre-login 401s on validate-and-refresh-session are expected noise.
                    if DEBUG and "socalgas.com/api" in url:
                        print(f"Non-200 API response: {response.status} {url}")
                    return

                if not is_json_response(response):
                    return

                try:
                    data = response.json()
                except Exception as e:
                    # Common when the page navigates before the body is read.
                    if DEBUG:
                        print(f"Could not read JSON body from {url}: {e}")
                    return

                if not isinstance(data, dict):
                    return

                # Only treat error fields as a login failure on the login endpoint.
                if LOGIN_API_FRAGMENT in url:
                    err = data.get("errorCode") or data.get("error_code")
                    if err or data.get("status") == "error":
                        state["login_failed"] = f"errorCode={err!r} message={data.get('message')!r}"
                        return

                if is_usage_payload(data):
                    state["usage_widget_data"] = data
                    print("Captured valid billing data")
                    if DEBUG:
                        print(f"\n=== RESPONSE MATCH ===\n{url}")
                        print(json.dumps(data, indent=2)[:1000])

            page.on("request", handle_request)
            page.on("response", handle_response)

            if DEBUG:
                # Timeline of the requests that matter, to show where a run stalls.
                def trace(msg):
                    print(f"[{time.monotonic() - t0:5.1f}s] {msg}")

                page.on("framenavigated", lambda f: f == page.main_frame and trace(f"NAV {f.url}"))
                page.on("request", lambda r: USAGE_URL_FRAGMENT in r.url.lower() and trace(f"REQ {r.method} {r.url}"))
                page.on("response", lambda r: USAGE_URL_FRAGMENT in r.url.lower() and trace(f"RESP {r.status} {r.url}"))
                page.on(
                    "requestfailed",
                    lambda r: any(h in r.url for h in TRACE_HOSTS)
                    and not r.url.endswith(".json")
                    and trace(f"FAILED {r.failure} {r.url}"),
                )

            print("Navigating to login page...")
            page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30000)

            email_field = page.locator("scg-text-field input").nth(0)
            try:
                email_field.wait_for(state="visible", timeout=LOGIN_FORM_WAIT_SECONDS * 1000)
            except Exception:
                dump_page(page, "login_form_missing")
                raise RuntimeError(
                    f"Login form did not appear within {LOGIN_FORM_WAIT_SECONDS}s (URL: {page.url})."
                    + flag_hint()
                )
            email_field.fill(SOCALGAS_EMAIL)
            page.locator("scg-text-field input").nth(1).fill(SOCALGAS_PASSWORD)
            page.wait_for_timeout(500)

            print("Submitting login credentials...")
            page.locator('scg-button[data-testid="login-button"]').click()

            print("Waiting for usage widget response...")
            # Wall-clock deadlines: interstitial clicks and locator calls make
            # loop iterations take longer than 1s.
            start = time.monotonic()
            interstitial_since = None
            while time.monotonic() - start < USAGE_WAIT_SECONDS:
                if state["usage_widget_data"]:
                    break
                if state["login_failed"]:
                    raise RuntimeError(f"Login failed: {state['login_failed']}")
                empty_at = state["usage_empty_at"]
                if empty_at is not None and time.monotonic() - empty_at >= USAGE_204_GRACE_SECONDS:
                    raise IncompleteDataError("usagewidget returned 204 (cycle rollover).")

                current_url = page.url
                if "/ui/error" in current_url:
                    dump_page(page, "login_error")
                    raise RuntimeError(f"SoCalGas redirected to an error page after login: {current_url}")
                # Reaching a real app page counts as a successful login even if
                # the login API body couldn't be read before navigation.
                if is_post_login_url(current_url):
                    state["login_verified"] = True

                if "/interstitial" in current_url:
                    if interstitial_since is None:
                        interstitial_since = time.monotonic()
                        print(f"Landed on interstitial: {current_url}")
                        dump_page(page, "interstitial")
                    try_dismiss_interstitial(page)
                    # Judged on whether we actually left the page, not whether a
                    # click happened: a "Close" on a banner can succeed forever.
                    if "/interstitial" in page.url and time.monotonic() - interstitial_since >= INTERSTITIAL_MAX_SECONDS:
                        raise InterstitialBlockedError(
                            "Stuck on a SoCalGas interstitial page after login. "
                            "Log in once in a normal browser and clear the prompt "
                            f"(with debug on, see interstitial.png / interstitial.txt in {DUMP_DIR})."
                        )
                else:
                    interstitial_since = None

                if time.monotonic() - start >= LOGIN_GRACE_SECONDS and not state["login_verified"]:
                    dump_page(page, "login_stuck")
                    raise RuntimeError(
                        f"Still on login page after {LOGIN_GRACE_SECONDS}s, login likely did not complete."
                    )

                page.wait_for_timeout(1000)

            if not state["usage_widget_data"]:
                dump_page(page, "no_usage")
                raise RuntimeError(
                    f"Logged in but usage data never arrived (last URL: {page.url})."
                    + (flag_hint() or " Possible page structure change.")
                )

            return state["usage_widget_data"]


def try_dismiss_interstitial(page):
    """Click a skip-style button if one exists. Returns True if something was clicked."""
    try:
        for role in ("button", "link"):
            target = page.get_by_role(role, name=INTERSTITIAL_SKIP).first
            if target.count() and target.is_visible():
                label = target.inner_text().strip()
                target.click()
                print(f"Dismissed interstitial via {role} '{label}'")
                page.wait_for_timeout(1500)
                return True
    except Exception as e:
        if DEBUG:
            print(f"Interstitial dismiss attempt failed: {e}")
    return False


def dump_page(page, name):
    """Debug only: screenshot plus visible text, so you can see what the page actually is."""
    if not DEBUG:
        return
    try:
        os.makedirs(DUMP_DIR, exist_ok=True)
        base = os.path.join(DUMP_DIR, name)
        # Text first: it survives even if the screenshot below hangs.
        text = page.inner_text("body", timeout=10000)
        with open(f"{base}.txt", "w") as f:
            f.write(f"URL: {page.url}\n\n{text}")
        print(f"Saved {base}.txt")
        print(f"--- page text (first 800 chars) ---\n{text[:800]}\n---")
        page.screenshot(path=f"{base}.png", full_page=True, timeout=10000)
        print(f"Saved {base}.png")
    except Exception as e:
        print(f"Could not dump page: {e}")


def to_float(value, field_name):
    try:
        return float(value)
    except (TypeError, ValueError) as e:
        raise RuntimeError(f"Expected numeric value for {field_name}, got {value!r}") from e


# SoCalGas placeholder for a not-yet-computed numeric field: a long run of
# zeros, optionally signed or with a decimal part (e.g. "000000000000",
# "000000000.00"). Gated on zero count so a genuine short "0" or "0.00"
# reading isn't mistaken for the placeholder.
PLACEHOLDER_PATTERN = re.compile(r"^[+-]?0*(\.0*)?$")
PLACEHOLDER_MIN_ZEROS = 8


def is_blank(value):
    return value is None or (isinstance(value, str) and value.strip() == "")


def is_placeholder_value(value):
    if is_blank(value):
        return True
    if isinstance(value, str):
        v = value.strip()
        return bool(PLACEHOLDER_PATTERN.match(v)) and v.count("0") >= PLACEHOLDER_MIN_ZEROS
    return False


def build_payload(usage_data):
    verification = usage_data.get("VerificationResponse", {})
    user_detail = verification.get("UserDetail", {}) if isinstance(verification, dict) else {}
    cost_data = user_detail.get("CostToDate", {}) if isinstance(user_detail, dict) else {}

    required_fields = [
        "ProjThermsToDateQty",
        "ProjThermsQty",
        "ProjBillAmt",
        "ProjCostToDateAmt",
        "ProjStartDate",
        "ProjEndDate",
    ]
    missing = [f for f in required_fields if f not in cost_data]
    if missing:
        raise RuntimeError(f"Schema drift detected, missing fields: {missing}")

    # During the ~1-day window between a cycle's ProjEndDate and the backend
    # finalizing the next projection, ProjThermsToDateQty comes back as "",
    # dates can be blank, and projection fields are zero-padded placeholders.
    # Bail before a fake 0.0 lands in a state_class sensor. To-date amounts
    # are only rejected when blank: a zero-padded cost-to-date alongside a
    # real therms-to-date is a genuine zero (e.g. day 1 of a cycle).
    blank_fields = [
        f for f in ("ProjThermsToDateQty", "ProjCostToDateAmt", "ProjStartDate", "ProjEndDate")
        if is_blank(cost_data[f])
    ]
    placeholder_fields = blank_fields + [
        f for f in ("ProjThermsQty", "ProjBillAmt") if is_placeholder_value(cost_data[f])
    ]
    if placeholder_fields:
        raise IncompleteDataError(
            f"Cycle boundary (ProjEndDate={cost_data.get('ProjEndDate')!r}), "
            f"{', '.join(placeholder_fields)} not yet computed upstream."
        )

    return {
        "therms_to_date": to_float(cost_data["ProjThermsToDateQty"], "ProjThermsToDateQty"),
        "projected_therms": to_float(cost_data["ProjThermsQty"], "ProjThermsQty"),
        "projected_bill": to_float(cost_data["ProjBillAmt"], "ProjBillAmt"),
        "cost_to_date": to_float(cost_data["ProjCostToDateAmt"], "ProjCostToDateAmt"),
        "billing_cycle_start": cost_data["ProjStartDate"],
        "billing_cycle_end": cost_data["ProjEndDate"],
        "updated_at": datetime.now().astimezone().isoformat(),
    }


def publish_mqtt(payload):
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    if MQTT_USER:
        client.username_pw_set(MQTT_USER, MQTT_PASSWORD)

    conn = {"rc": None}

    def on_connect(client, userdata, flags, reason_code, properties):
        conn["rc"] = reason_code

    client.on_connect = on_connect

    try:
        client.connect(MQTT_HOST, MQTT_PORT, 60)
        client.loop_start()

        # Wait for CONNACK so broker auth failures surface as auth failures,
        # not as a misleading publish timeout.
        for _ in range(50):
            if conn["rc"] is not None:
                break
            time.sleep(0.1)
        if conn["rc"] is None:
            raise TimeoutError("No CONNACK from MQTT broker within 5s.")
        if conn["rc"].is_failure:
            raise RuntimeError(f"MQTT broker rejected connection: {conn['rc']}")

        msg_info = client.publish(MQTT_TOPIC, json.dumps(payload), qos=1, retain=True)
        msg_info.wait_for_publish(timeout=10)
        if not msg_info.is_published():
            raise TimeoutError("MQTT publish timed out.")

        print(f"Published MQTT message successfully to topic: {MQTT_TOPIC}")
    finally:
        client.loop_stop()
        client.disconnect()


def debug_config():
    if not DEBUG:
        return
    safe_config = dict(config)
    for key in ["password", "mqtt_password"]:
        if key in safe_config:
            safe_config[key] = "********"
    if safe_config.get("email"):
        user, _, domain = safe_config["email"].partition("@")
        safe_config["email"] = f"{user[:2]}***@{domain}"
    print("\n=== CONFIG ===")
    print(json.dumps(safe_config, indent=2))
    print("==============\n")


def main():
    if not SOCALGAS_EMAIL or not SOCALGAS_PASSWORD:
        print("Error: Missing SoCalGas credentials.")
        sys.exit(1)
    if not MQTT_HOST:
        print("Error: MQTT_HOST is not configured.")
        sys.exit(1)

    debug_config()

    try:
        usage_data = login_and_get_usage()
        payload = build_payload(usage_data)
        if DEBUG:
            print("\n=== PAYLOAD TO SEND ===")
            print(json.dumps(payload, indent=2))
        publish_mqtt(payload)
        print("Script executed successfully.")
    except IncompleteDataError as e:
        # Not a failure. Retained MQTT message keeps last-known-good.
        print(f"Skipping publish, {e}")
        sys.exit(0)
    except InterstitialBlockedError as e:
        print(f"Execution Failed: {e}", file=sys.stderr)
        sys.exit(2)
    except Exception as e:
        print(f"Execution Failed: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()