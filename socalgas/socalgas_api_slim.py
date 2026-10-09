import json
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
MQTT_TOPIC = config.get("mqtt_topic", "home/socalgas/total")

LOGIN_URL = "https://myaccount.socalgas.com/ui/login"
LOGIN_API_FRAGMENT = "/authentication/login"
USAGE_URL_FRAGMENT = "usagewidget"
DEBUG = config.get("debug", False)

# Seconds to wait after clicking login before deciding login never happened.
LOGIN_GRACE_SECONDS = 15
# Total seconds to wait for the usage widget payload.
USAGE_WAIT_SECONDS = 45

# Buttons that dismiss a post-login interstitial without agreeing to anything.
# Deliberately excludes "Continue"/"Accept"/"Agree" so we never consent to terms.
INTERSTITIAL_SKIP = re.compile(r"^\s*(skip|not now|remind me later|maybe later|no thanks|close)\s*$", re.I)


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
    ctype = response.headers.get("content-type", "")
    return "application/json" in ctype


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

            state = {
                "login_verified": False,
                "login_failed": None,
                "usage_widget_data": None,
                "usage_empty": False,
            }

            def handle_request(request):
                if "accesstoken" in (k.lower() for k in request.headers.keys()):
                    if not state["login_verified"] and DEBUG:
                        print("Captured AccessToken header")
                    state["login_verified"] = True

            def handle_response(response):
                url = response.url

                if USAGE_URL_FRAGMENT in url.lower() and response.status == 204:
                    # Cycle rollover: endpoint returns no body at all.
                    state["usage_empty"] = True
                    print(f"204 on usagewidget, cycle rollover, no data yet: {url}")
                    return

                if response.status in (401, 403) and LOGIN_API_FRAGMENT in url:
                    state["login_failed"] = f"HTTP {response.status} from login endpoint"
                    # Body/headers distinguish bad credentials from a WAF/bot block.
                    try:
                        body = response.text()[:500]
                    except Exception as e:
                        body = f"<unreadable: {e}>"
                    headers = {
                        k: v for k, v in response.headers.items()
                        if k.lower() in ("server", "content-type", "x-akamai-request-id", "cf-ray", "x-cache")
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

            print("Navigating to login page...")
            page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30000)

            email_field = page.locator("scg-text-field input").nth(0)
            email_field.wait_for(state="visible")
            email_field.fill(SOCALGAS_EMAIL)
            page.locator("scg-text-field input").nth(1).fill(SOCALGAS_PASSWORD)
            page.wait_for_timeout(500)

            print("Submitting login credentials...")
            page.locator('scg-button[data-testid="login-button"]').click()

            print("Waiting for usage widget response...")
            interstitial_attempts = 0
            for second in range(USAGE_WAIT_SECONDS):
                if state["usage_widget_data"]:
                    break
                if state["usage_empty"]:
                    raise IncompleteDataError("usagewidget returned 204 (cycle rollover).")
                if state["login_failed"]:
                    raise RuntimeError(f"Login failed: {state['login_failed']}")

                current_url = page.url
                # Leaving /ui/login counts as a successful login even if the
                # login API body couldn't be read before navigation.
                if "/ui/login" not in current_url:
                    state["login_verified"] = True

                if "/interstitial" in current_url:
                    interstitial_attempts += 1
                    if interstitial_attempts == 1:
                        print(f"Landed on interstitial: {current_url}")
                        dump_page(page, "interstitial")
                    if not try_dismiss_interstitial(page) and interstitial_attempts >= 8:
                        raise InterstitialBlockedError(
                            "Stuck on a SoCalGas interstitial page after login. "
                            "Log in once in a normal browser and clear the prompt "
                            "(see interstitial.png / interstitial.txt if debug is on)."
                        )

                if second >= LOGIN_GRACE_SECONDS and not state["login_verified"]:
                    dump_page(page, "login_stuck")
                    raise RuntimeError(
                        f"Still on login page after {LOGIN_GRACE_SECONDS}s, login likely did not complete."
                    )

                page.wait_for_timeout(1000)

            if not state["usage_widget_data"]:
                dump_page(page, "no_usage")
                raise RuntimeError(
                    f"Logged in but usage data never arrived (last URL: {page.url}). "
                    "Possible page structure change."
                )

            return state["usage_widget_data"]


def try_dismiss_interstitial(page):
    """Click a skip-style button if one exists. Returns True if something was clicked."""
    try:
        button = page.get_by_role("button", name=INTERSTITIAL_SKIP).first
        if button.count() and button.is_visible():
            label = button.inner_text().strip()
            button.click()
            print(f"Dismissed interstitial via '{label}'")
            page.wait_for_timeout(1500)
            return True
        link = page.get_by_role("link", name=INTERSTITIAL_SKIP).first
        if link.count() and link.is_visible():
            label = link.inner_text().strip()
            link.click()
            print(f"Dismissed interstitial via link '{label}'")
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
        page.screenshot(path=f"{name}.png", full_page=True)
        text = page.inner_text("body")
        with open(f"{name}.txt", "w") as f:
            f.write(f"URL: {page.url}\n\n{text}")
        print(f"Saved {name}.png and {name}.txt")
        print(f"--- page text (first 800 chars) ---\n{text[:800]}\n---")
    except Exception as e:
        print(f"Could not dump page: {e}")


def to_float(value, field_name):
    try:
        return float(value)
    except (TypeError, ValueError) as e:
        raise RuntimeError(f"Expected numeric value for {field_name}, got {value!r}") from e


# SoCalGas placeholder for a not-yet-computed numeric field: a long run of
# zero digits (e.g. "000000000000"). Length-gated so a genuine short "0"
# reading isn't mistaken for the placeholder.
PLACEHOLDER_PATTERN = re.compile(r"^0{8,}$")


def is_placeholder_value(value):
    if value is None:
        return True
    if isinstance(value, str):
        v = value.strip()
        return v == "" or bool(PLACEHOLDER_PATTERN.match(v))
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
    # finalizing the next projection, numeric fields come back as zero-padded
    # placeholders (or "" for ProjThermsToDateQty) and dates can be blank.
    # Bail before a fake 0.0 lands in a state_class sensor.
    check_fields = required_fields
    placeholder_fields = [f for f in check_fields if is_placeholder_value(cost_data[f])]
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