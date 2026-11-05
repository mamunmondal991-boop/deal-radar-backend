import os
import requests
from dotenv import load_dotenv

from database import (
    list_products, get_product, get_baseline, add_observation,
    is_duplicate_alert, record_deal, record_alert
)

load_dotenv()

def fetch_product_data(product):
    """
    Demo products use simulated values for local testing.
    Real products must use an authorized feed URL returning the documented JSON.
    """
    if product.get("demo_price") is not None:
        return {
            "price": float(product["demo_price"]),
            "seller": product.get("demo_seller") or "Demo seller",
            "available": True,
            "verified_pincodes": product["pincodes"],
            "coupon": product.get("demo_coupon") or "",
            "source_mode": "DEMO - NOT A LIVE OFFER"
        }

    feed_url = (product.get("feed_url") or "").strip()
    if not feed_url:
        raise ValueError("No authorized feed URL configured and no demo price set.")

    response = requests.get(feed_url, timeout=20, headers={"Accept":"application/json"})
    response.raise_for_status()
    data = response.json()

    required = ["price", "available", "verified_pincodes"]
    missing = [key for key in required if key not in data]
    if missing:
        raise ValueError("Feed missing required fields: " + ", ".join(missing))

    price = float(data["price"])
    if price <= 0:
        raise ValueError("Feed returned invalid price.")

    requested_pins = set(product["pincodes"])
    returned_pins = set(str(x) for x in data.get("verified_pincodes", []))
    # Never claim PIN coverage beyond both the product's configured PINs
    # and the feed's independently verified PINs.
    verified = sorted(requested_pins.intersection(returned_pins))

    return {
        "price": price,
        "seller": data.get("seller", "Not supplied"),
        "available": bool(data["available"]),
        "verified_pincodes": verified,
        "coupon": data.get("coupon", ""),
        "source_mode": "AUTHORIZED FEED"
    }

def telegram_send(category, message):
    enabled = os.getenv("TELEGRAM_ENABLED", "false").lower() == "true"
    if not enabled:
        return None, "telegram_disabled"

    prefix = "MOBILE" if category == "mobile" else "BEAUTY"
    token = os.getenv(f"{prefix}_BOT_TOKEN", "")
    channel = os.getenv(f"{prefix}_CHANNEL_ID", "")
    if not token or not channel:
        raise ValueError(f"Missing {prefix} Telegram token or channel ID.")

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    response = requests.post(url, json={
        "chat_id": channel,
        "text": message,
        "disable_web_page_preview": False
    }, timeout=20)
    response.raise_for_status()
    payload = response.json()
    if not payload.get("ok"):
        raise RuntimeError(payload)
    return payload["result"]["message_id"], "sent"

def scan_one_product(sku):
    product = get_product(sku)
    if not product:
        return {"sku":sku, "status":"not_found"}

    try:
        data = fetch_product_data(product)
        # Calculate the baseline from prior observations before storing this scan.
        # This prevents the current price from influencing its own comparison.
        baseline, count = get_baseline(sku)
        add_observation(sku, data)

        result = {
            "sku": sku,
            "product": product["name"],
            "price": data["price"],
            "mode": data["source_mode"],
            "baseline_observations": count,
            "status": "recorded"
        }

        if baseline is None:
            result["status"] = "building_baseline"
            return result

        discount = ((baseline - data["price"]) / baseline) * 100
        result["baseline_median"] = round(baseline, 2)
        result["discount_percent"] = round(discount, 2)

        # A deal requires price threshold, discount threshold,
        # available status and at least one independently verified PIN.
        if data["price"] > product["target_price"]:
            result["status"] = "above_target"
            return result
        if discount < product["min_discount"]:
            result["status"] = "discount_too_small"
            return result
        if not data["available"] or not data["verified_pincodes"]:
            result["status"] = "availability_or_pin_not_verified"
            return result

        deal_id = record_deal(sku, data["price"], baseline, discount, data["verified_pincodes"])
        result["deal_id"] = deal_id

        channel_key = "mobile" if product["category"] == "mobile" else "beauty"
        if is_duplicate_alert(sku, data["price"], channel_key):
            result["status"] = "deal_detected_duplicate_alert_suppressed"
            return result

        link = product.get("affiliate_url") or product["product_url"]
        pins = ", ".join(data["verified_pincodes"])
        message = (
            f"🎯 INDIA DEAL RADAR — {product['category'].upper()}\\n\\n"
            f"📦 {product['name']}\\n"
            f"🏪 Platform: {product['platform']}\\n"
            f"💰 Current observed price: ₹{data['price']:,.0f}\\n"
            f"📊 7-day observed median: ₹{baseline:,.0f}\\n"
            f"📉 Difference: {discount:.1f}%\\n"
            f"📍 Verified PIN(s): {pins}\\n"
            f"🏷 Seller: {data.get('seller') or 'Not supplied'}\\n"
            f"🎟 Coupon: {data.get('coupon') or 'None supplied'}\\n"
            f"🕒 Source mode: {data['source_mode']}\\n\\n"
            f"🔗 Check product: {link}\\n\\n"
            "Price and delivery availability can change at checkout. "
            "Historical median is based on this app's recorded observations. "
            "Affiliate disclosure: this channel may earn a commission."
        )

        try:
            message_id, status = telegram_send(product["category"], message)
            record_alert(sku, data["price"], channel_key, message_id, status)
            result["status"] = "deal_detected_" + status
        except Exception as notify_error:
            record_alert(sku, data["price"], channel_key, "", "failed")
            result["status"] = "deal_detected_notification_failed"
            result["notification_error"] = str(notify_error)

        return result

    except Exception as error:
        return {"sku":sku, "status":"error", "error":str(error)}

def scan_all_products():
    products = [p for p in list_products() if p["active"]]
    return [scan_one_product(p["sku"]) for p in products]
