
import os, hmac, hashlib, json
from fastapi import FastAPI, Request, HTTPException
from dotenv import load_dotenv
from database import init_db, process_captured_payment

load_dotenv()
app=FastAPI(title="India Deal Radar Payment Webhook")
init_db()

@app.get("/health")
def health():
    return {"status":"ok"}

@app.post("/razorpay/webhook")
async def razorpay_webhook(request: Request):
    secret=os.getenv("RAZORPAY_WEBHOOK_SECRET","")
    if not secret:
        raise HTTPException(503,"Webhook secret is not configured.")
    raw=await request.body()
    supplied=request.headers.get("X-Razorpay-Signature","")
    expected=hmac.new(secret.encode("utf-8"),raw,hashlib.sha256).hexdigest()
    if not supplied or not hmac.compare_digest(expected,supplied):
        raise HTTPException(400,"Invalid webhook signature.")
    event_id=request.headers.get("X-Razorpay-Event-Id","")
    try: data=json.loads(raw)
    except Exception: raise HTTPException(400,"Invalid JSON payload.")
    if data.get("event")!="payment.captured":
        return {"status":"ignored"}
    try:
        entity=data["payload"]["payment"]["entity"]
        order_id=entity.get("order_id")
        payment_id=entity.get("id")
        amount=int(entity.get("amount",0))
        currency=entity.get("currency","")
        if not event_id: raise ValueError("Missing Razorpay event ID header.")
        result=process_captured_payment(event_id,order_id,payment_id,amount,currency)
        return {"status":result}
    except Exception as e:
        raise HTTPException(400,str(e))
