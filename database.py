
import sqlite3, json
from pathlib import Path
from datetime import datetime, timedelta, timezone
from statistics import median

DB_PATH = Path(__file__).resolve().parent / "data" / "deal_radar.db"
IST = timezone(timedelta(hours=5, minutes=30))

def now_iso():
    return datetime.now(IST).isoformat(timespec="seconds")

def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    return con

def init_db():
    with connect() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT, sku TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL, category TEXT NOT NULL, platform TEXT NOT NULL,
            product_url TEXT NOT NULL, affiliate_url TEXT, feed_url TEXT,
            target_price REAL NOT NULL, min_discount REAL NOT NULL DEFAULT 20,
            pincodes_json TEXT NOT NULL DEFAULT '[]', active INTEGER NOT NULL DEFAULT 1,
            demo_price REAL, demo_baseline REAL, demo_seller TEXT, demo_coupon TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS price_observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT, sku TEXT NOT NULL, price REAL NOT NULL,
            seller TEXT, available INTEGER, verified_pincodes_json TEXT NOT NULL DEFAULT '[]',
            coupon TEXT, observed_at TEXT NOT NULL,
            FOREIGN KEY(sku) REFERENCES products(sku) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS deals (
            id INTEGER PRIMARY KEY AUTOINCREMENT, sku TEXT NOT NULL, price REAL NOT NULL,
            baseline REAL NOT NULL, discount_percent REAL NOT NULL,
            verified_pincodes_json TEXT NOT NULL, created_at TEXT NOT NULL,
            FOREIGN KEY(sku) REFERENCES products(sku) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT, sku TEXT NOT NULL, price REAL NOT NULL,
            channel TEXT NOT NULL, message_id TEXT, status TEXT NOT NULL, sent_at TEXT NOT NULL,
            FOREIGN KEY(sku) REFERENCES products(sku) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT, phone TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL, user_type TEXT NOT NULL DEFAULT 'buyer',
            plan TEXT NOT NULL DEFAULT 'free', plan_valid_till TEXT,
            telegram_chat_id TEXT, saved_pincodes_json TEXT NOT NULL DEFAULT '[]',
            is_verified INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS reseller_stock (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
            model_name TEXT NOT NULL, quantity INTEGER NOT NULL CHECK(quantity > 0),
            asking_price REAL NOT NULL CHECK(asking_price > 0), location_pin TEXT NOT NULL,
            invoice_available INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS pending_payment_orders (
            order_id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, plan TEXT NOT NULL,
            amount_paise INTEGER NOT NULL, currency TEXT NOT NULL DEFAULT 'INR',
            status TEXT NOT NULL DEFAULT 'created', created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS payment_events (
            event_id TEXT PRIMARY KEY, order_id TEXT, payment_id TEXT,
            event_type TEXT NOT NULL, processed_at TEXT NOT NULL
        );
        """)
        # Safe additive migrations for older MVP databases.
        cols = {r["name"] for r in con.execute("PRAGMA table_info(users)").fetchall()}
        if "is_verified" not in cols:
            con.execute("ALTER TABLE users ADD COLUMN is_verified INTEGER NOT NULL DEFAULT 0")

def add_product(p):
    with connect() as con:
        con.execute("""INSERT INTO products
        (sku,name,category,platform,product_url,affiliate_url,feed_url,target_price,
         min_discount,pincodes_json,active,demo_price,demo_baseline,demo_seller,demo_coupon,created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (p["sku"],p["name"],p["category"],p["platform"],p["product_url"],
         p.get("affiliate_url",""),p.get("feed_url",""),float(p["target_price"]),
         float(p.get("min_discount",20)),json.dumps(p.get("pincodes",[])),
         int(p.get("active",1)),p.get("demo_price"),p.get("demo_baseline"),
         p.get("demo_seller",""),p.get("demo_coupon",""),now_iso()))

def list_products():
    with connect() as con:
        rows=con.execute("SELECT * FROM products ORDER BY category,name").fetchall()
    out=[]
    for r in rows:
        d=dict(r); d["pincodes"]=json.loads(d.pop("pincodes_json")); out.append(d)
    return out

def get_product(sku):
    with connect() as con: r=con.execute("SELECT * FROM products WHERE sku=?",(sku,)).fetchone()
    if not r: return None
    d=dict(r); d["pincodes"]=json.loads(d.pop("pincodes_json")); return d

def delete_product(sku):
    with connect() as con: con.execute("DELETE FROM products WHERE sku=?",(sku,))

def add_observation(sku,data,observed_at=None):
    with connect() as con:
        con.execute("""INSERT INTO price_observations
        (sku,price,seller,available,verified_pincodes_json,coupon,observed_at)
        VALUES (?,?,?,?,?,?,?)""",
        (sku,float(data["price"]),data.get("seller"),int(bool(data.get("available"))),
         json.dumps(data.get("verified_pincodes",[])),data.get("coupon"),observed_at or now_iso()))

def get_baseline(sku):
    cutoff=(datetime.now(IST)-timedelta(days=7)).isoformat()
    with connect() as con:
        rows=con.execute("SELECT price FROM price_observations WHERE sku=? AND observed_at>=? ORDER BY observed_at DESC",(sku,cutoff)).fetchall()
    prices=[float(x["price"]) for x in rows]
    return (median(prices),len(prices)) if len(prices)>=3 else (None,len(prices))

def get_price_history(sku):
    with connect() as con:
        rows=con.execute("""SELECT price,seller,available,verified_pincodes_json,coupon,observed_at
        FROM price_observations WHERE sku=? ORDER BY observed_at""",(sku,)).fetchall()
    result=[]
    for r in rows:
        d=dict(r); d["verified_pincodes"]=", ".join(json.loads(d.pop("verified_pincodes_json")))
        d["available"]=bool(d["available"]); result.append(d)
    return result

def is_duplicate_alert(sku,price,channel,hours=12):
    cutoff=(datetime.now(IST)-timedelta(hours=hours)).isoformat()
    with connect() as con:
        return con.execute("""SELECT 1 FROM alerts WHERE sku=? AND price=? AND channel=?
        AND status='sent' AND sent_at>=? LIMIT 1""",(sku,price,channel,cutoff)).fetchone() is not None

def record_deal(sku,price,baseline,discount,pins):
    with connect() as con:
        cur=con.execute("""INSERT INTO deals(sku,price,baseline,discount_percent,verified_pincodes_json,created_at)
        VALUES(?,?,?,?,?,?)""",(sku,price,baseline,discount,json.dumps(pins),now_iso()))
        return cur.lastrowid

def record_alert(sku,price,channel,message_id,status):
    with connect() as con:
        con.execute("INSERT INTO alerts(sku,price,channel,message_id,status,sent_at) VALUES(?,?,?,?,?,?)",
                    (sku,price,channel,str(message_id or ""),status,now_iso()))

def get_dashboard_stats():
    with connect() as con:
        return {"products":con.execute("SELECT COUNT(*) FROM products WHERE active=1").fetchone()[0],
                "observations":con.execute("SELECT COUNT(*) FROM price_observations").fetchone()[0],
                "deals":con.execute("SELECT COUNT(*) FROM deals").fetchone()[0],
                "alerts":con.execute("SELECT COUNT(*) FROM alerts WHERE status='sent'").fetchone()[0]}

def register_user(phone,name,user_type,pincode,telegram_id=""):
    phone="".join(ch for ch in phone if ch.isdigit())
    if len(phone) not in (10,12): raise ValueError("Enter a valid 10-digit Indian mobile number (or country code + number).")
    if user_type not in ("buyer","reseller"): raise ValueError("Invalid account type.")
    if not (pincode.isdigit() and len(pincode)==6): raise ValueError("PIN code must be 6 digits.")
    with connect() as con:
        con.execute("""INSERT INTO users(phone,name,user_type,telegram_chat_id,saved_pincodes_json,created_at)
        VALUES(?,?,?,?,?,?) ON CONFLICT(phone) DO UPDATE SET name=excluded.name,
        user_type=excluded.user_type,telegram_chat_id=excluded.telegram_chat_id,
        saved_pincodes_json=excluded.saved_pincodes_json""",
        (phone,name.strip(),user_type,telegram_id,json.dumps([pincode]),now_iso()))
        return con.execute("SELECT id FROM users WHERE phone=?",(phone,)).fetchone()["id"]

def list_users():
    with connect() as con:
        rows=con.execute("SELECT id,phone,name,user_type,plan,plan_valid_till,is_verified,saved_pincodes_json,created_at FROM users ORDER BY id DESC").fetchall()
    out=[]
    for r in rows:
        d=dict(r); d["saved_pincodes"]=json.loads(d.pop("saved_pincodes_json")); out.append(d)
    return out

def save_user_pins(user_id,pins):
    clean=sorted(set(str(p).strip() for p in pins if str(p).strip().isdigit() and len(str(p).strip())==6))
    if not clean: raise ValueError("Add at least one valid six-digit PIN.")
    with connect() as con: con.execute("UPDATE users SET saved_pincodes_json=? WHERE id=?",(json.dumps(clean),user_id))

def add_stock(user_id,model_name,quantity,asking_price,pin,invoice):
    if not pin.isdigit() or len(pin)!=6: raise ValueError("PIN must be six digits.")
    with connect() as con:
        con.execute("""INSERT INTO reseller_stock(user_id,model_name,quantity,asking_price,location_pin,invoice_available,status,created_at)
        VALUES(?,?,?,?,?,?,?,?)""",(user_id,model_name.strip(),int(quantity),float(asking_price),pin,int(invoice),"pending",now_iso()))

def list_stock():
    with connect() as con:
        return [dict(r) for r in con.execute("""SELECT s.*,u.name AS seller_name,u.phone AS seller_phone
        FROM reseller_stock s JOIN users u ON u.id=s.user_id WHERE s.status='approved' ORDER BY s.created_at DESC""").fetchall()]

def create_pending_order(order_id,user_id,plan,amount_paise):
    expected={"vip_99":9900,"reseller_499":49900}
    if plan not in expected or amount_paise!=expected[plan]: raise ValueError("Plan amount mismatch.")
    with connect() as con:
        con.execute("""INSERT INTO pending_payment_orders(order_id,user_id,plan,amount_paise,created_at)
        VALUES(?,?,?,?,?)""",(order_id,user_id,plan,amount_paise,now_iso()))

def process_captured_payment(event_id,order_id,payment_id,amount_paise,currency):
    if not event_id or not order_id: raise ValueError("Missing payment event/order identifier.")
    with connect() as con:
        if con.execute("SELECT 1 FROM payment_events WHERE event_id=?",(event_id,)).fetchone():
            return "duplicate"
        order=con.execute("SELECT * FROM pending_payment_orders WHERE order_id=?",(order_id,)).fetchone()
        if not order or order["status"]!="created": raise ValueError("No matching pending order.")
        if int(order["amount_paise"])!=int(amount_paise) or order["currency"]!=currency:
            raise ValueError("Payment amount/currency does not match pending order.")
        plan_days=30
        expiry=(datetime.now(IST)+timedelta(days=plan_days)).isoformat(timespec="seconds")
        con.execute("UPDATE users SET plan=?,plan_valid_till=? WHERE id=?",(order["plan"],expiry,order["user_id"]))
        con.execute("UPDATE pending_payment_orders SET status='paid' WHERE order_id=?",(order_id,))
        con.execute("INSERT INTO payment_events(event_id,order_id,payment_id,event_type,processed_at) VALUES(?,?,?,?,?)",
                    (event_id,order_id,payment_id,"payment.captured",now_iso()))
        return "activated"

def seed_demo_data():
    demos=[
      {"sku":"DEMO-SERUM-30","name":"Demo Vitamin C Face Serum 30ml","category":"beauty","platform":"demo",
       "product_url":"https://example.com/demo-serum","target_price":400,"min_discount":20,
       "pincodes":["700001"],"demo_price":349,"demo_baseline":699,"demo_seller":"Demo merchant",
       "demo_coupon":"SIMULATED — NOT VALID"},
      {"sku":"DEMO-PHONE-128","name":"Demo Android Smartphone 128GB","category":"mobile","platform":"demo",
       "product_url":"https://example.com/demo-phone","target_price":12000,"min_discount":15,
       "pincodes":["700001"],"demo_price":10999,"demo_baseline":13999,"demo_seller":"Demo seller",
       "demo_coupon":"SIMULATED — NOT VALID"}]
    added=0
    for p in demos:
        if get_product(p["sku"]): continue
        add_product(p)
        for day in range(7,0,-1):
            stamp=(datetime.now(IST)-timedelta(days=day)).isoformat(timespec="seconds")
            add_observation(p["sku"],{"price":p["demo_baseline"],"seller":p["demo_seller"],
              "available":True,"verified_pincodes":p["pincodes"],"coupon":""},stamp)
        added+=1
    return added
