import json
import os
from datetime import datetime

import paho.mqtt.client as mqtt
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

load_dotenv()

# Home Assistant add-on config
with open("/data/options.json") as f:
    config = json.load(f)

#print(json.dumps(config, indent=2))

SOCALGAS_EMAIL = config["email"]
SOCALGAS_PASSWORD = config["password"]

MQTT_HOST = config["mqtt_host"]
MQTT_PORT = int(config.get("mqtt_port", 1883))
MQTT_USER = config.get("mqtt_user", "")
MQTT_PASSWORD = config.get("mqtt_password", "")

MQTT_TOPIC = "home/socalgas/total"

print(f"MQTT_HOST={MQTT_HOST}")
print(f"EMAIL={SOCALGAS_EMAIL}")

LOGIN_URL = "https://myaccount.socalgas.com/ui/login"

DEBUG = config.get("debug", False)
def login_and_get_usage():  

        debug_log("Clicking login button")

        page.locator(
            'scg-button[data-testid="login-button"]'
        ).click()
        page.wait_for_timeout(5000)
        debug_log(
            f"URL after login click: {page.url}"
        )


        debug_log("Login button clicked")

        print(
            "Waiting for usage widget response..."
        )

        for i in range(60):

            if (
                login_verified
                and usage_widget_data
            ):
                debug_log(
                    "Login verified and usage data captured"
                )
                break

            if DEBUG and i % 5 == 0:
                debug_log(
                    f"Waiting... "
                    f"login_verified={login_verified} "
                    f"usage_widget={usage_widget_data is not None} "
                    f"url={page.url}"
                )

            page.wait_for_timeout(1000)

        print(f"Current URL: {page.url}")

        if not login_verified:

            debug_log(
                "Login verification failed"
            )

            debug_page_dump(page)

            if DEBUG:
                try:
                    print("\n===== PAGE TEXT =====")
                    print(page.locator("body").inner_text())
                    print("=====================\n")
                except Exception as e:
                    print(f"Could not dump page: {e}")


            raise Exception(
                f"Login could not be verified. "
                f"URL={page.url}"
            )

        if not usage_widget_data:

            debug_log(
                "Usage widget data not captured"
            )

            debug_page_dump(page)

            raise Exception(
                f"Could not capture usage widget data. "
                f"URL={page.url}"
            )

        browser.close()

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
    
def debug_log(msg):
    if DEBUG:
        print(
            f"[{datetime.now().strftime('%H:%M:%S')}] "
            f"{msg}"
        )

def debug_page_dump(page):
    if not DEBUG:
        return

    try:
        print("\n===== PAGE TEXT =====")
        print(page.locator("body").inner_text())
        print("=====================\n")
    except Exception as e:
        print(f"Could not read page text: {e}")


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