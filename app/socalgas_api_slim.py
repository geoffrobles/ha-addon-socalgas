import json
import os
from datetime import datetime

import paho.mqtt.client as mqtt
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

load_dotenv()

SOCALGAS_EMAIL = os.getenv("SOCALGAS_EMAIL")
SOCALGAS_PASSWORD = os.getenv("SOCALGAS_PASSWORD")

MQTT_HOST = os.getenv("MQTT_HOST")
MQTT_PORT = int(os.getenv("MQTT_PORT", 1883))
MQTT_USER = os.getenv("MQTT_USER")
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD")
MQTT_TOPIC = "home/socalgas/total"

LOGIN_URL = "https://myaccount.socalgas.com/ui/login"

DEBUG = False


def login_and_get_usage():

    with sync_playwright() as p:

        browser = p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled"
            ]
        )

        page = browser.new_page(
            viewport={
                "width": 1280,
                "height": 900
            }
        )

        page.set_extra_http_headers({
            "User-Agent": (
                "Mozilla/5.0 "
                "(X11; Linux x86_64) "
                "AppleWebKit/537.36 "
                "(KHTML, like Gecko) "
                "Chrome/148.0.0.0 "
                "Safari/537.36"
            )
        })

        Stealth().apply_stealth_sync(page)

        login_verified = False
        usage_widget_data = None

        def handle_request(request):
            nonlocal login_verified
            headers = request.headers
            for key, value in headers.items():
                if key.lower() == "accesstoken":
                    login_verified = True

                    if DEBUG:
                        print(
                            f"Captured AccessToken: "
                            f"{value[:25]}..."
                        )

        def handle_response(response):
            nonlocal usage_widget_data
            url = response.url.lower()
            if response.status != 200:
                return

            # Only inspect likely usage endpoints
            if (
                "usage" not in url
                and "billing" not in url
                and "daily" not in url
            ):
                return
            if DEBUG:
                print(f"\n=== RESPONSE ===")
                print(response.url)
            try:
                data = response.json()
                if isinstance(data, dict):
                    # Current billing summary endpoint
                    if (
                        isinstance(data, dict)
                        and "UsageSoFar" in data
                    ):
                        usage_widget_data = data
                        print(
                            "\nCaptured valid "
                            "billing data"
                        )

                    # Optional debugging
                    if DEBUG:
                        print(
                            f"Keys: {list(data.keys())}"
                        )

                        print(
                            json.dumps(
                                data,
                                indent=2
                            )[:1500]
                        )

            except Exception:
                if DEBUG:
                    print("(non-json response)")

        page.on("request", handle_request)
        page.on("response", handle_response)
        page.goto(LOGIN_URL)
        page.wait_for_selector(
            "scg-text-field"
        )
        email_input = (
            page.locator("scg-text-field")
            .nth(0)
            .locator("input")
        )

        email_input.click()

        email_input.type(
            SOCALGAS_EMAIL,
            delay=100
        )

        page.wait_for_timeout(1000)

        password_input = (
            page.locator("scg-text-field")
            .nth(1)
            .locator("input")
        )

        password_input.click()

        password_input.type(
            SOCALGAS_PASSWORD,
            delay=100
        )

        page.wait_for_timeout(1000)

        page.locator(
            'scg-button[data-testid="login-button"]'
        ).click()

        print(
            "Waiting for usage widget response..."
        )

        for _ in range(60):

            if (
                login_verified
                and usage_widget_data
            ):
                break

            page.wait_for_timeout(1000)

        print(f"Current URL: {page.url}")

        if not login_verified:

            page.screenshot(
                path="login_failure.png"
            )

            raise Exception(
                "Login could not be verified"
            )

        if not usage_widget_data:

            page.screenshot(
                path="usage_widget_failure.png"
            )

            raise Exception(
                "Could not capture usage widget data"
            )

        return usage_widget_data

def build_payload(usage_data):

    cost_data = (
        usage_data["VerificationResponse"]
        ["UserDetail"]
        ["CostToDate"]
    )

    return {
        "usage_so_far_ccf": float(
            usage_data.get(
                "UsageSoFar",
                0
            )
        ),

        "projected_total_ccf": float(
            cost_data.get(
                "ProjThermsQty",
                0
            )
        ),

        "billing_cycle_start": (
            cost_data.get(
                "ProjStartDate"
            )
        ),

        "billing_cycle_end": (
            cost_data.get(
                "ProjEndDate"
            )
        ),

        "updated_at": (
            datetime.now().isoformat()
        )
    }
 
def publish_mqtt(payload):
    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2
    )
    if MQTT_USER:
        client.username_pw_set(
            MQTT_USER,
            MQTT_PASSWORD
        )
    client.connect(MQTT_HOST, MQTT_PORT, 60)
    client.publish(
        MQTT_TOPIC,
        json.dumps(payload),
        retain=True
    )
    print(
    "Publishing MQTT"
    )
    client.disconnect()
    
def main():

    if not MQTT_HOST:
        raise Exception(
            "MQTT_HOST is not configured"
        )

    print(
        "Logging into SoCalGas..."
    )

    usage_data = (
        login_and_get_usage()
    )

    payload = build_payload(
        usage_data
    )

    print(
        json.dumps(
            payload,
            indent=2
        )
    )

    publish_mqtt(payload)

    print("Done.")

if __name__ == "__main__":
    main()