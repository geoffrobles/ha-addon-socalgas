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
                    token = request.headers.get("accesstoken", "") or request.headers.get("AccessToken", "")
                    print(f"Captured AccessToken: {token[:25]}...")

        def handle_response(response):
            url = response.url.lower()
            if response.status != 200:
                return

            if not any(k in url for k in ["usage", "billing", "daily"]):
                return

            try:
                data = response.json()
                if isinstance(data, dict) and "UsageSoFar" in data:
                    state["usage_widget_data"] = data
                    print("\nCaptured valid billing data")
                    
                    if DEBUG:
                        print(f"\n=== RESPONSE MATCH ===\n{response.url}")
                        print(json.dumps(data, indent=2)[:1000])
            except Exception:
                pass  # Ignore non-JSON responses quietly

        page.on("request", handle_request)
        page.on("response", handle_response)
        
        print("Navigating to login page...")
        page.goto(LOGIN_URL, wait_until="networkidle")
        
        # Fill credentials safely using modern locators
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


def build_payload(usage_data):
    # Safely walk through nested fields to prevent crashing on unforeseen schema shifts
    verification = usage_data.get("VerificationResponse", {})
    user_detail = verification.get("UserDetail", {}) if isinstance(verification, dict) else {}
    cost_data = user_detail.get("CostToDate", {}) if isinstance(user_detail, dict) else {}

    return {
        "therms_to_date": float(  cost_data.get("ProjThermsToDateQty") or 0),
        "projected_therms": float(cost_data.get("ProjThermsQty") or 0),
        "projected_bill": float(  cost_data.get("ProjBillAmt") or 0),
        "cost_to_date": float(    cost_data.get("ProjCostToDateAmt") or 0 ),
        "billing_cycle_start": cost_data.get("ProjStartDate"),
        "billing_cycle_end": cost_data.get("ProjEndDate"),
        "updated_at": datetime.now().isoformat()
    }


def publish_mqtt(payload):
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    
    if MQTT_USER:
        client.username_pw_set(MQTT_USER, MQTT_PASSWORD)
        
    client.connect(MQTT_HOST, MQTT_PORT, 60)
    
    # Start loop thread to process delivery buffers smoothly
    client.loop_start()
    
    msg_info = client.publish(MQTT_TOPIC, json.dumps(payload), retain=True)
    msg_info.wait_for_publish()  # Blocks until packet handshaking finishes safely
    
    print(f"Published MQTT message successfully to topic: {MQTT_TOPIC}")
    
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