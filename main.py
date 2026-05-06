import os
import json
import requests
from flask import Flask, request, jsonify
from anthropic import Anthropic

app = Flask(__name__)
client = Anthropic()

# ========== الإعدادات - عدّلها حسب بياناتك ==========
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "my_secret_token_123")
WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN", "")
PHONE_NUMBER_ID = os.environ.get("PHONE_NUMBER_ID", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# ========== إعدادات المنتج ==========
PRODUCT = {
    "name": os.environ.get("PRODUCT_NAME", "iPhone 14 Pro"),
    "price": os.environ.get("PRODUCT_PRICE", "2500"),
    "condition": os.environ.get("PRODUCT_CONDITION", "ممتاز - مستخدم 6 أشهر"),
    "negotiation": os.environ.get("PRODUCT_NEGOTIATION", "10"),
}

SYSTEM_PROMPT = f"""أنت مساعد ذكي لمتجر بلوس على WhatsApp. مهمتك الرد على استفسارات الزبائن.

معلومات المنتج:
- الاسم: {PRODUCT['name']}
- السعر: {PRODUCT['price']} درهم
- الحالة: {PRODUCT['condition']}
- نسبة التفاوض المسموحة: {PRODUCT['negotiation']}% فقط، لا تقبل أكثر منها

قواعد مهمة:
- الشحن: مجاني لجميع المناطق والمدن، لا تذكر أي رسوم شحن أبداً
- التفاوض: اقبل فقط ضمن النسبة المحددة، وارفض بأدب ما زاد عنها
- عند رغبة الزبون بالشراء: اسأله "هل تريد الشراء؟ 🛒"
- عند تأكيده: اطلب منه الاسم الكامل ورقم الهاتف والعنوان
- عند إرسال بياناته: أكد الطلب بهذا الشكل فقط:
ORDER_CONFIRMED
الاسم: [الاسم]
الهاتف: [الهاتف]
العنوان: [العنوان]
- لا تقل أبداً "تواصل معنا لتحديد موعد معاينة"
- الرد بنفس لغة الزبون (عربي أو إنجليزي)
- كن موجزاً ومفيداً، لا تتجاوز 3-4 جمل
- استخدم إيموجي مناسبة"""

# تخزين محادثات الزبائن في الذاكرة
conversations = {}
# تخزين الطلبيات
orders = []


def get_claude_reply(phone, user_message):
    """الحصول على رد Claude للزبون"""
    if phone not in conversations:
        conversations[phone] = []

    conversations[phone].append({"role": "user", "content": user_message})

    # الاحتفاظ بآخر 20 رسالة فقط
    if len(conversations[phone]) > 20:
        conversations[phone] = conversations[phone][-20:]

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1000,
        system=SYSTEM_PROMPT,
        messages=conversations[phone],
    )

    reply = response.content[0].text
    conversations[phone].append({"role": "assistant", "content": reply})

    return reply


def parse_order(reply, phone):
    """استخراج بيانات الطلب من رد Claude"""
    if "ORDER_CONFIRMED" not in reply:
        return None

    lines = reply.split("\n")
    order = {
        "id": len(orders) + 1,
        "phone_customer": phone,
        "product": PRODUCT["name"],
        "price": PRODUCT["price"],
    }

    for line in lines:
        if line.startswith("الاسم:"):
            order["name"] = line.replace("الاسم:", "").strip()
        elif line.startswith("الهاتف:"):
            order["customer_phone"] = line.replace("الهاتف:", "").strip()
        elif line.startswith("العنوان:"):
            order["address"] = line.replace("العنوان:", "").strip()

    return order if "name" in order else None


def send_whatsapp_message(to, message):
    """إرسال رسالة عبر WhatsApp API"""
    url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
    }
    data = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": message},
    }
    response = requests.post(url, headers=headers, json=data)
    return response.json()


def notify_owner(order):
    """إرسال إشعار للمالك عند وصول طلب جديد"""
    owner_phone = os.environ.get("OWNER_PHONE", "")
    if not owner_phone:
        return

    message = (
        f"🛒 *طلب جديد #{order['id']}*\n\n"
        f"📦 المنتج: {order['product']}\n"
        f"💰 السعر: {order['price']} درهم\n"
        f"👤 الاسم: {order.get('name', '-')}\n"
        f"📞 الهاتف: {order.get('customer_phone', '-')}\n"
        f"📍 العنوان: {order.get('address', '-')}\n"
        f"🕐 الوقت: الآن"
    )
    send_whatsapp_message(owner_phone, message)


# ========== Webhook Routes ==========

@app.route("/webhook", methods=["GET"])
def verify_webhook():
    """التحقق من الـ Webhook مع Meta"""
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")

    if mode == "subscribe" and token == VERIFY_TOKEN:
        print("✅ Webhook verified!")
        return challenge, 200
    return "Forbidden", 403


@app.route("/webhook", methods=["POST"])
def handle_message():
    """استقبال ومعالجة الرسائل الواردة"""
    try:
        data = request.get_json()

        entry = data.get("entry", [{}])[0]
        changes = entry.get("changes", [{}])[0]
        value = changes.get("value", {})
        messages = value.get("messages", [])

        if not messages:
            return jsonify({"status": "no messages"}), 200

        message = messages[0]
        sender_phone = message.get("from")
        msg_type = message.get("type")

        if msg_type != "text":
            send_whatsapp_message(
                sender_phone,
                "عذراً، أستقبل الرسائل النصية فقط حالياً. كيف يمكنني مساعدتك؟ 😊"
            )
            return jsonify({"status": "ok"}), 200

        user_text = message.get("text", {}).get("body", "")
        if not user_text:
            return jsonify({"status": "empty"}), 200

        print(f"📩 رسالة من {sender_phone}: {user_text}")

        # الحصول على رد Claude
        reply = get_claude_reply(sender_phone, user_text)
        print(f"🤖 رد Claude: {reply}")

        # التحقق إن كان طلب شراء
        order = parse_order(reply, sender_phone)
        if order:
            orders.append(order)
            print(f"✅ طلب جديد: {order}")
            confirm_msg = (
                f"✅ لقد تم تأكيد طلبك بنجاح!\n\n"
                f"📦 المنتج: {order['product']}\n"
                f"💰 السعر: {order['price']} درهم\n"
                f"👤 الاسم: {order.get('name', '-')}\n"
                f"📞 الهاتف: {order.get('customer_phone', '-')}\n"
                f"📍 العنوان: {order.get('address', '-')}\n\n"
                f"🚀 سيتم التواصل معك في أقرب وقت"
            )
            send_whatsapp_message(sender_phone, confirm_msg)
            notify_owner(order)
        else:
            send_whatsapp_message(sender_phone, reply)

        return jsonify({"status": "ok"}), 200

    except Exception as e:
        print(f"❌ خطأ: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/orders", methods=["GET"])
def get_orders():
    """عرض الطلبيات"""
    return jsonify({"total": len(orders), "orders": orders})


@app.route("/", methods=["GET"])
def home():
    return "✅ بوت متجر بلوس يعمل!", 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
