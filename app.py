
import streamlit as st
import pandas as pd
from dotenv import load_dotenv
from database import (init_db,add_product,list_products,get_dashboard_stats,get_price_history,
 delete_product,seed_demo_data,register_user,list_users,save_user_pins,add_stock,list_stock,connect)
from engine import scan_all_products,scan_one_product

load_dotenv()
init_db()
st.set_page_config(page_title="India Deal Radar",page_icon="🎯",layout="wide")
st.title("🎯 India Deal Radar")
st.caption("Beauty & Cosmetics • Mobile & Electronics • Reseller Stock Board")
st.warning("MVP mode: demo prices are simulated. Live merchant feeds, phone OTP and payments stay disabled until approved credentials and production security are configured.")

with st.sidebar:
    st.header("Control Centre")
    page=st.radio("Sections",["Overview","Buyer & Reseller Accounts","Beauty Radar","Mobile Radar","Add Product","Deal Scanner","Reseller Stock Board","Price History","Membership & Payments","Setup & Safety"])
    if st.button("Load demo products",use_container_width=True):
        st.success(f"Added {seed_demo_data()} demo products.")
        st.rerun()

if page=="Overview":
    s=get_dashboard_stats()
    c=st.columns(4)
    for col,label,key in zip(c,["Active products","Price observations","Deals detected","Telegram alerts sent"],["products","observations","deals","alerts"]): col.metric(label,s[key])
    st.subheader("Platform modules")
    st.markdown("- **Beauty Radar:** skincare, makeup, grooming and personal care.\n- **Mobile Radar:** smartphones and electronics.\n- **Deal Intelligence:** observed price history and discount scoring.\n- **Reseller Board:** seller-submitted stock listings, pending admin approval.\n- **Membership:** Buyer VIP ₹99 / Reseller Pro ₹499 per 30 days.")
    st.info("PIN availability is shown only when the configured feed explicitly verifies that PIN. Otherwise users must check delivery at merchant checkout.")
elif page=="Buyer & Reseller Accounts":
    st.subheader("Create local MVP account")
    st.caption("This is a local prototype account record, not phone-OTP verification. Do not expose publicly until OTP authentication is connected.")
    with st.form("register"):
        name=st.text_input("Name")
        phone=st.text_input("Mobile number")
        role=st.selectbox("Account type",["buyer","reseller"])
        pin=st.text_input("Primary delivery PIN code",max_chars=6)
        tg=st.text_input("Telegram chat ID (optional)")
        submit=st.form_submit_button("Save account")
    if submit:
        try:
            uid=register_user(phone,name,role,pin,tg)
            st.success(f"Account saved. Local user ID: {uid}")
        except Exception as e: st.error(str(e))
    users=list_users()
    if users:
        st.subheader("Saved accounts")
        st.dataframe(pd.DataFrame(users),use_container_width=True,hide_index=True)
        uid=st.selectbox("Choose account to add PIN",[u["id"] for u in users])
        pins=st.text_input("PIN codes separated by commas")
        if st.button("Save PIN list"):
            try: save_user_pins(uid,pins.split(",")); st.success("PIN list saved.")
            except Exception as e: st.error(str(e))
elif page in ("Beauty Radar","Mobile Radar"):
    category="beauty" if page=="Beauty Radar" else "mobile"
    st.subheader(page)
    items=[p for p in list_products() if p["category"]==category]
    if items: st.dataframe(pd.DataFrame(items)[["sku","name","platform","target_price","min_discount","active"]],use_container_width=True,hide_index=True)
    else: st.info("No products yet. Add products or load demo data.")
elif page=="Add Product":
    st.subheader("Add product / approved feed")
    with st.form("product"):
        sku=st.text_input("SKU")
        name=st.text_input("Product name")
        category=st.selectbox("Category",["beauty","mobile"])
        platform=st.text_input("Merchant/platform")
        url=st.text_input("Product URL")
        aff=st.text_input("Approved affiliate URL (optional)")
        feed=st.text_input("Authorized JSON feed URL (optional)")
        target=st.number_input("Target price (₹)",min_value=1.0,value=1000.0)
        discount=st.number_input("Minimum discount (%)",min_value=1.0,max_value=95.0,value=20.0)
        pins=st.text_input("PIN codes this feed can check (comma separated)")
        submit=st.form_submit_button("Add product")
    if submit:
        try:
            if not url.startswith("https://"): raise ValueError("Use a valid HTTPS product URL.")
            p={"sku":sku.strip(),"name":name.strip(),"category":category,"platform":platform.strip(),"product_url":url.strip(),
               "affiliate_url":aff.strip(),"feed_url":feed.strip(),"target_price":target,"min_discount":discount,
               "pincodes":[x.strip() for x in pins.split(",") if x.strip()]}
            add_product(p); st.success("Product added.")
        except Exception as e: st.error(str(e))
    products=list_products()
    if products:
        sku_del=st.selectbox("Delete product SKU",[p["sku"] for p in products])
        if st.button("Delete selected product"): delete_product(sku_del); st.rerun()
elif page=="Deal Scanner":
    st.subheader("Run authorised feed scanner")
    st.warning("Only add feeds you are contractually authorised to use. Demo mode is clearly labelled and must not be marketed as a real deal.")
    products=list_products()
    if products:
        if st.button("Scan all active products",type="primary"):
            with st.spinner("Scanning configured feeds..."):
                results=scan_all_products()
            st.dataframe(pd.DataFrame(results),use_container_width=True,hide_index=True)
        sku=st.selectbox("Scan one SKU",[p["sku"] for p in products])
        if st.button("Scan selected"): st.json(scan_one_product(sku))
    else: st.info("Add products first.")
elif page=="Reseller Stock Board":
    st.subheader("Reseller stock exchange")
    users=list_users()
    if not users: st.info("Create a reseller account first.")
    else:
        reseller_ids=[u["id"] for u in users if u["user_type"]=="reseller"]
        if reseller_ids:
            with st.form("stock"):
                uid=st.selectbox("Seller account",reseller_ids)
                model=st.text_input("Product/model")
                qty=st.number_input("Quantity",min_value=1,value=1)
                price=st.number_input("Asking price per unit ₹",min_value=1.0,value=1000.0)
                pin=st.text_input("Stock location PIN",max_chars=6)
                invoice=st.checkbox("Invoice available")
                submit=st.form_submit_button("Submit listing for review")
            if submit:
                try: add_stock(uid,model,qty,price,pin,invoice); st.success("Listing submitted. It remains hidden until admin approval.")
                except Exception as e: st.error(str(e))
        else: st.info("No reseller account yet.")
    with connect() as con:
        pending=con.execute("SELECT * FROM reseller_stock WHERE status='pending' ORDER BY id DESC").fetchall()
    if pending:
        st.subheader("Admin moderation queue")
        st.dataframe(pd.DataFrame([dict(x) for x in pending]),use_container_width=True,hide_index=True)
        chosen=st.selectbox("Listing ID to moderate",[x["id"] for x in pending])
        a,b=st.columns(2)
        if a.button("Approve listing"):
            with connect() as con: con.execute("UPDATE reseller_stock SET status='approved' WHERE id=?",(chosen,))
            st.success("Approved."); st.rerun()
        if b.button("Reject listing"):
            with connect() as con: con.execute("UPDATE reseller_stock SET status='rejected' WHERE id=?",(chosen,))
            st.success("Rejected."); st.rerun()
    stock=list_stock()
    if stock: st.dataframe(pd.DataFrame(stock),use_container_width=True,hide_index=True)
    else: st.caption("No approved stock listings yet.")
elif page=="Price History":
    products=list_products()
    if products:
        sku=st.selectbox("Product",[p["sku"] for p in products])
        hist=get_price_history(sku)
        if hist:
            df=pd.DataFrame(hist); df["observed_at"]=pd.to_datetime(df["observed_at"])
            st.line_chart(df.set_index("observed_at")["price"])
            st.dataframe(df,use_container_width=True,hide_index=True)
        else: st.info("No observations yet.")
    else: st.info("Add products first.")
elif page=="Membership & Payments":
    st.subheader("Membership plans")
    a,b=st.columns(2)
    a.metric("Buyer VIP","₹99 / 30 days"); a.write("Deal alerts, watchlist, verified PIN-based feed when available.")
    b.metric("Reseller Pro","₹499 / 30 days"); b.write("Reseller opportunities, approved stock board and deal intelligence.")
    st.error("Payment activation is intentionally disabled until Razorpay account credentials, server-side order creation, webhook secret and verified webhook configuration are supplied. Never activate membership from a screenshot or amount-only check.")
    st.code("Razorpay webhook endpoint: POST /razorpay/webhook\nExpected event: payment.captured\nRequires a matching server-created pending order and verified signature.")
elif page=="Setup & Safety":
    st.markdown("""
### Required before public launch
1. Connect only approved affiliate APIs/merchant feeds. EarnKaro endpoint in the supplied plan is unverified; this project does not assume it is valid.
2. Phone OTP login must be connected before public accounts are enabled.
3. Configure HTTPS, secrets, backups, database migrations, monitoring and admin access control.
4. Razorpay webhook must validate HMAC signature, pending order ID, amount, currency and unique event ID.
5. PIN availability must come from a source that checked the exact PIN. Unknown means **Check delivery at checkout**.
6. Reseller listings stay pending until manual review. Verify seller identity, invoice and stock before marketplace launch.
7. Use actual recorded price observations; never call target price or offline asking price a historical baseline.
8. Show affiliate/sponsored disclosures and final checkout caveats.

### Demo disclaimer
Seeded products and historical prices are simulated for software testing only. Do not share as live offers.
""")
