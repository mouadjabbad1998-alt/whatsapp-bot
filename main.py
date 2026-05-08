import os
import requests
from flask import Flask, request, jsonify
import google.generativeai as genai

app = Flask(__name__)

VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "my_secret_token_123")
WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN", "")
PHONE_NUMBER_ID = os.environ.get("PHONE_NUMBER_ID", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-1.5-flash")

PRODUCT_NAME = os.environ.get("PRODUCT_NAME", "iPhone 14 Pro")
PRODUCT_PRICE = os.environ.get("PRODUCT_PRICE", "2500")
PRODUCT_CONDITION = os.environ.get("PRODUCT_CONDITION", "ممتاز - مستخدم 6 أشهر")
PRODUCT_NEGOTIATION = os.environ.get("PRODUCT_NEGOTIATION", "10")

SYSTEM_PROMPT = f"""أنت مساعد ذكي لمتجر بلوس على WhatsApp.
معلومات المنتج:
- الاسم: {PRODUCT_NAME}
- السعر: {PRODUCT_PRICE} درهم
- الحالة: {PRODUCT_CONDITION}
- نسبة التفاوض: {PRODUCT_NEGOTIATION}% فقط
قواعد:
- الشحن مجاني لجميع المناطق
- لا تذكر رسوم شحن أبداً
- عند رغبة الشراء اسأل: هل تريد الشراء؟
- عند التأكيد اطلب: الاسم والهاتف والعنوان
- عند استلام البيانات رد بهذا الشكل فقط:
ORDER_CONFIRMED
الاسم: [الاسم]
الهاتف: [الهاتف]
العنوان: [العنوان]
- لا تذكر مواعيد المعاينة
- رد بنفس لغة الزبون
- لا تتجاوز 3-4 جمل"""

conversations = {}
orders = []

def get_gemini_reply(phone, user_message):
    if phone not in conversations:
        conversations[phone] = []
    conversations[phone].append(f"الزبون: {user_message}")
    if len(conversations[phone]) > 20:
        conversations[phone] = conversations[phone][-20:]
    history = "\n".join(conversations[phone])
    prompt = f"{SYSTEM_PROMPT}\n\nالمحادثة:\n{history}\n\nردك:"
    response = model.generate_content(prompt)
    reply = response.text.strip()
    conversations[phone].append(f"المساعد: {reply}")
    return reply

def parse_order(reply, phone):
    if "ORDER_CONFIRMED" not in reply:
        return None
    lines = reply.split("\n")
    order = {"id": len(orders)+1, "phone_customer": phone, "product": PRODUCT_NAME, "price": PRODUCT_PRICE}
    for line in lines:
        if line.startswith("الاسم:"): order["name"] = line.replace("الاسم:","").strip()
        elif line.startswith("الهاتف:"): order["customer_phone"] = line.replace("الهاتف:","").strip()
        elif line.startswith("العنوان:"): order["address"] = line.replace("العنوان:","").strip()
    return order if "name" in order else None

def send_whatsapp_message(to, message):
    url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
    data = {"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": message}}
    requests.post(url, headers=headers, json=data)

def notify_owner(order):
    owner_phone = os.environ.get("OWNER_PHONE", "")
    if not owner_phone: return
    msg = f"🛒 طلب جديد #{order['id']}\n📦 {order['product']}\n💰 {order['price']} درهم\n👤 {order.get('name','-')}\n📞 {order.get('customer_phone','-')}\n📍 {order.get('address','-')}"
    send_whatsapp_message(owner_phone, msg)

@app.route("/webhook", methods=["GET"])
def verify_webhook():
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    if mode == "subscribe" and token == VERIFY_TOKEN:
        return challenge, 200
    return "Forbidden", 403

@app.route("/webhook", methods=["POST"])
def handle_message():
    try:
        data = request.get_json()
        messages = data.get("entry",[{}])[0].get("changes",[{}])[0].get("value",{}).get("messages",[])
        if not messages: return jsonify({"status":"ok"}), 200
        message = messages[0]
        sender_phone = message.get("from")
        if message.get("type") != "text":
            send_whatsapp_message(sender_phone, "عذراً أستقبل النصوص فقط 😊")
            return jsonify({"status":"ok"}), 200
        user_text = message.get("text",{}).get("body","")
        if not user_text: return jsonify({"status":"ok"}), 200
        reply = get_gemini_reply(sender_phone, user_text)
        order = parse_order(reply, sender_phone)
        if order:
            orders.append(order)
            confirm = f"✅ تم تأكيد طلبك!\n📦 {order['product']}\n💰 {order['price']} درهم\n👤 {order.get('name','-')}\n📞 {order.get('customer_phone','-')}\n📍 {order.get('address','-')}\n\n🚀 سيتم التواصل معك قريباً"
            send_whatsapp_message(sender_phone, confirm)
            notify_owner(order)
        else:
            send_whatsapp_message(sender_phone, reply)
        return jsonify({"status":"ok"}), 200
    except Exception as e:
        return jsonify({"status":"error","message":str(e)}), 500

@app.route("/orders", methods=["GET"])
def get_orders():
    return jsonify({"total": len(orders), "orders": orders})

@app.route("/", methods=["GET"])
def home():
    return "✅ بوت متجر بلوس يعمل!", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
