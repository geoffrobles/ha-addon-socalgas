import json
import os
import sys
from datetime import datetime

import paho.mqtt.client as mqtt
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

load_dotenv()

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
DEBUG = config.get("debug", False)


def is_usage_payload(data):
    try:
        cost_data = data["VerificationResponse"]["UserDetail"]["CostToDate"]
        return all(
            field in cost_data
            for field in [
                "ProjThermsToDateQty",
                "ProjThermsQty",
                "ProjBillAmt",
                "ProjCostToDateAmt",
            ]
        )
    except (KeyError, TypeError):
        return False


def login_and_get_usage():
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-setuid-sandbox",
                "--disable-blink-features=AutomationControlled"
            ]
        )
        
        # Using a typical desktop user agent
        context = browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
        
        page = context.new_page()
        Stealth().apply_stealth_sync(page)

        state = {
            "login_verified": False,
            "usage_widget_data": None
        }

        def handle_request(request):
            if "accesstoken" in [k.lower() for k in request.headers.keys()]:
                state["login_verified"] = True
                if DEBUG:
                    print("Captured AccessToken header")

        def handle_response(response):
            if response.status != 200:
                return

            try:
                data = response.json()
            except Exception:
                return  # not JSON, ignore quietly

            if isinstance(data, dict) and is_usage_payload(data):
                state["usage_widget_data"] = data
                print("\nCaptured valid billing data")

                if DEBUG:
                    print(f"\n=== RESPONSE MATCH ===\n{response.url}")
                    print(json.dumps(data, indent=2)[:1000])

        page.on("request", handle_request)
        page.on("response", handle_response)
        
        print("Navigating to login page...")
        page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30000)  
        
        # Fill credentials safely using locators
        email_field = page.locator("scg-text-field input").nth(0)
        email_field.wait_for(state="visible")
        email_field.fill(SOCALGAS_EMAIL)
        
        password_field = page.locator("scg-text-field input").nth(1)
        password_field.fill(SOCALGAS_PASSWORD)
        
        page.wait_for_timeout(500) # Natural human pause
        
        print("Submitting login credentials...")
        page.locator('scg-button[data-testid="login-button"]').click()

        # Wait loop with timeout boundary
        print("Waiting for usage widget response...")
        for _ in range(30):  # 30 seconds total max
            if state["login_verified"] and state["usage_widget_data"]:
                break
            page.wait_for_timeout(1000)

        # Cleanup browser resources immediately before making logical assessments
        browser.close()

        if not state["login_verified"]:
            raise RuntimeError("Login verification token missed or failed.")
        if not state["usage_widget_data"]:
            raise RuntimeError("Could not capture backend usage widget data.")

        return state["usage_widget_data"]


def to_float(value, field_name):
    try:
        return float(value)
    except (TypeError, ValueError) as e:
        raise RuntimeError(f"Expected numeric value for {field_name}, got {value!r}") from e



def build_payload(usage_data):
    # Safely walk through nested fields to prevent crashing on unforeseen schema shifts
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
        raise RuntimeError(f"Schema drift detected — missing fields: {missing}")

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

    try:
        result = client.connect(MQTT_HOST, MQTT_PORT, 60)
        if result != mqtt.MQTT_ERR_SUCCESS:
            raise RuntimeError(f"MQTT connection failed with code {result}")

        client.loop_start()

        msg_info = client.publish(
            MQTT_TOPIC,
            json.dumps(payload),
            qos=1,
            retain=True,
        )

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
    except Exception as e:
        print(f"Execution Failed: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()