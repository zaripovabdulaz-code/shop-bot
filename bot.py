import asyncio
import os
import re
import sqlite3
from datetime import datetime

from aiogram import Bot, Dispatcher, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart, Filter, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton,
    Message, ReplyKeyboardMarkup, ReplyKeyboardRemove,
)
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))  # asosiy admin
if not TOKEN:
    raise SystemExit("BOT_TOKEN topilmadi. .env faylni tekshiring.")

DB_FILE = "shop.db"
STATUS = {
    "new": "🆕 Yangi",
    "accepted": "✅ Qabul qilindi",
    "delivered": "🚚 Yetkazildi",
    "cancelled": "❌ Bekor qilindi",
}

bot = Bot(TOKEN)
dp = Dispatcher()


# ---------- Ma'lumotlar bazasi ----------
def db(sql, args=(), last=False):
    c = sqlite3.connect(DB_FILE)
    try:
        cur = c.execute(sql, args)
        c.commit()
        return cur.lastrowid if last else cur.fetchall()
    finally:
        c.close()


def db_init():
    db("CREATE TABLE IF NOT EXISTS admins(id INTEGER PRIMARY KEY)")
    db("""CREATE TABLE IF NOT EXISTS categories(
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE)""")
    db("""CREATE TABLE IF NOT EXISTS products(
        id INTEGER PRIMARY KEY AUTOINCREMENT, category_id INTEGER,
        name TEXT, price INTEGER, description TEXT, photo TEXT)""")
    db("""CREATE TABLE IF NOT EXISTS cart(
        user_id INTEGER, product_id INTEGER, qty INTEGER,
        PRIMARY KEY(user_id, product_id))""")
    db("""CREATE TABLE IF NOT EXISTS orders(
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, name TEXT,
        phone TEXT, address TEXT, total INTEGER,
        status TEXT DEFAULT 'new', created TEXT)""")
    db("""CREATE TABLE IF NOT EXISTS order_items(
        order_id INTEGER, name TEXT, price INTEGER, qty INTEGER)""")


def seed():
    if db("SELECT COUNT(*) FROM products")[0][0]:
        return
    demo = {
        "🍎 Oziq-ovqat": [("Non", 5000, "Yangi yopilgan issiq non"),
                          ("Sut 1 litr", 12000, "Tabiiy sigir suti")],
        "📱 Elektronika": [("Quloqchin", 150000, "Simsiz quloqchin"),
                           ("Zaryadlovchi", 80000, "Tez zaryadlovchi, 20W")],
        "🧴 Uy-ro'zg'or": [("Sovun", 7000, "Kir sovun"),
                           ("Idish yuvish vositasi", 25000, "500 ml")],
    }
    for cat, items in demo.items():
        cid = db("INSERT INTO categories(name) VALUES(?)", (cat,), last=True)
        for name, price, desc in items:
            db("INSERT INTO products(category_id,name,price,description) "
               "VALUES(?,?,?,?)", (cid, name, price, desc))


# ---------- Yordamchi funksiyalar ----------
def money(n: int) -> str:
    return f"{n:,}".replace(",", " ") + " so'm"


def is_admin(uid: int) -> bool:
    return uid == ADMIN_ID or bool(db("SELECT 1 FROM admins WHERE id=?", (uid,)))


def all_admins() -> set:
    ids = {r[0] for r in db("SELECT id FROM admins")}
    if ADMIN_ID:
        ids.add(ADMIN_ID)
    return ids


class IsAdmin(Filter):
    async def __call__(self, event) -> bool:
        return is_admin(event.from_user.id)


class IsSuper(Filter):
    async def __call__(self, event) -> bool:
        return event.from_user.id == ADMIN_ID


def ib(text, data):
    return InlineKeyboardButton(text=text, callback_data=data)


def kb(rows):
    return InlineKeyboardMarkup(inline_keyboard=rows)


def menu(uid: int) -> ReplyKeyboardMarkup:
    rows = [[KeyboardButton(text="🛍 Katalog"), KeyboardButton(text="🛒 Savat")],
            [KeyboardButton(text="📦 Buyurtmalarim")]]
    if is_admin(uid):
        rows.append([KeyboardButton(text="🛠 Admin panel")])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


async def show(c: CallbackQuery, text, markup=None):
    """Xabarni tahrirlaydi; rasmli xabar bo'lsa, o'chirib yangisini yuboradi."""
    try:
        await c.message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest:
        try:
            await c.message.delete()
        except Exception:
            pass
        await c.message.answer(text, reply_markup=markup)


# ---------- Savat va buyurtma matnlari ----------
def cart_view(uid: int):
    rows = db("""SELECT p.id, p.name, p.price, c.qty FROM cart c
                 JOIN products p ON p.id = c.product_id WHERE c.user_id=?""", (uid,))
    if not rows:
        return "🛒 Savat bo'sh.", None
    lines, buttons, total = [], [], 0
    for pid, name, price, qty in rows:
        s = price * qty
        total += s
        lines.append(f"{name}\n   {qty} × {money(price)} = {money(s)}")
        buttons.append([ib("➖", f"dec:{pid}"),
                        ib(f"{name[:18]} ({qty})", "noop"),
                        ib("➕", f"inc:{pid}")])
    buttons.append([ib("✅ Buyurtma berish", "checkout")])
    buttons.append([ib("🗑 Tozalash", "clear")])
    text = "🛒 Savat\n\n" + "\n\n".join(lines) + f"\n\nJami: {money(total)}"
    return text, kb(buttons)


def order_text(oid: int) -> str:
    o = db("SELECT id,name,phone,address,total,status,created FROM orders WHERE id=?",
           (oid,))[0]
    items = db("SELECT name,price,qty FROM order_items WHERE order_id=?", (oid,))
    lines = "\n".join(f"• {n} — {q} × {money(p)}" for n, p, q in items)
    return (f"📦 Buyurtma #{o[0]}\n👤 {o[1]}\n📞 {o[2]}\n📍 {o[3]}\n🕒 {o[6]}\n\n"
            f"{lines}\n\nJami: {money(o[4])}\nHolat: {STATUS[o[5]]}")


def order_kb(oid: int):
    return kb([[ib("✅ Qabul qilish", f"st:{oid}:accepted"),
                ib("🚚 Yetkazildi", f"st:{oid}:delivered")],
               [ib("❌ Bekor qilish", f"st:{oid}:cancelled")]])


async def notify_admins(oid: int):
    for aid in all_admins():
        try:
            await bot.send_message(aid, order_text(oid), reply_markup=order_kb(oid))
        except Exception:
            pass


def products_admin_view():
    rows = db("""SELECT p.id, p.name, p.price FROM products p
                 JOIN categories c ON c.id = p.category_id
                 ORDER BY c.name, p.name""")
    if not rows:
        return "Hozircha mahsulot yo'q.", None
    buttons = [[ib(f"❌ {n} ({money(pr)})", f"del:{pid}")] for pid, n, pr in rows[:50]]
    return "O'chirish uchun mahsulotni bosing:", kb(buttons)


# ---------- FSM holatlari ----------
class Order(StatesGroup):
    name = State()
    phone = State()
    address = State()


class AddProduct(StatesGroup):
    category = State()
    name = State()
    price = State()
    description = State()
    photo = State()


# ---------- Umumiy buyruqlar ----------
@dp.message(Command("cancel"), StateFilter("*"))
async def cancel(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("Bekor qilindi.", reply_markup=menu(m.from_user.id))


@dp.message(CommandStart())
async def start(m: Message):
    await m.answer(
        "👋 Do'konimizga xush kelibsiz!\n\n"
        "🛍 Katalogdan mahsulot tanlang, savatga qo'shing va buyurtma bering.",
        reply_markup=menu(m.from_user.id),
    )


@dp.message(Command("id"))
async def my_id(m: Message):
    await m.answer(f"Sizning ID'ingiz: {m.from_user.id}")


@dp.callback_query(F.data == "noop")
async def noop(c: CallbackQuery):
    await c.answer()


# ---------- Katalog ----------
def categories_markup():
    rows = db("SELECT id, name FROM categories ORDER BY name")
    return kb([[ib(name, f"cat:{cid}")] for cid, name in rows]) if rows else None


@dp.message(F.text == "🛍 Katalog")
async def katalog(m: Message):
    markup = categories_markup()
    if not markup:
        await m.answer("Hozircha mahsulotlar yo'q.")
        return
    await m.answer("Kategoriyani tanlang:", reply_markup=markup)


@dp.callback_query(F.data == "cats")
async def kategoriyalar(c: CallbackQuery):
    await c.answer()
    markup = categories_markup()
    await show(c, "Kategoriyani tanlang:" if markup else "Mahsulotlar yo'q.", markup)


@dp.callback_query(F.data.startswith("cat:"))
async def kategoriya(c: CallbackQuery):
    cid = int(c.data.split(":")[1])
    prods = db("SELECT id, name, price FROM products WHERE category_id=?", (cid,))
    rows = [[ib(f"{n} — {money(p)}", f"prod:{pid}")] for pid, n, p in prods]
    rows.append([ib("⬅️ Kategoriyalar", "cats")])
    await c.answer()
    await show(c, "Mahsulotni tanlang:", kb(rows))


@dp.callback_query(F.data.startswith("prod:"))
async def mahsulot(c: CallbackQuery):
    pid = int(c.data.split(":")[1])
    r = db("SELECT id, category_id, name, price, description, photo "
           "FROM products WHERE id=?", (pid,))
    if not r:
        await c.answer("Mahsulot topilmadi", show_alert=True)
        return
    _, cid, name, price, desc, photo = r[0]
    text = f"{name}\n💰 {money(price)}\n\n{desc or ''}"
    markup = kb([[ib("➕ Savatga qo'shish", f"add:{pid}")],
                 [ib("🛒 Savat", "cart"), ib("⬅️ Orqaga", f"cat:{cid}")]])
    await c.answer()
    if photo:
        try:
            await c.message.delete()
        except Exception:
            pass
        await c.message.answer_photo(photo, caption=text, reply_markup=markup)
    else:
        await show(c, text, markup)


# ---------- Savat ----------
@dp.callback_query(F.data.startswith("add:"))
async def savatga(c: CallbackQuery):
    pid = int(c.data.split(":")[1])
    db("""INSERT INTO cart(user_id, product_id, qty) VALUES(?, ?, 1)
          ON CONFLICT(user_id, product_id) DO UPDATE SET qty = qty + 1""",
       (c.from_user.id, pid))
    await c.answer("✅ Savatga qo'shildi")


@dp.message(F.text == "🛒 Savat")
async def savat(m: Message):
    text, markup = cart_view(m.from_user.id)
    await m.answer(text, reply_markup=markup)


@dp.callback_query(F.data == "cart")
async def savat_cb(c: CallbackQuery):
    await c.answer()
    text, markup = cart_view(c.from_user.id)
    await show(c, text, markup)


@dp.callback_query(F.data.startswith(("inc:", "dec:")))
async def miqdor(c: CallbackQuery):
    action, pid = c.data.split(":")
    delta = 1 if action == "inc" else -1
    db("UPDATE cart SET qty = qty + ? WHERE user_id=? AND product_id=?",
       (delta, c.from_user.id, int(pid)))
    db("DELETE FROM cart WHERE user_id=? AND qty <= 0", (c.from_user.id,))
    await c.answer()
    text, markup = cart_view(c.from_user.id)
    await show(c, text, markup)


@dp.callback_query(F.data == "clear")
async def tozalash(c: CallbackQuery):
    db("DELETE FROM cart WHERE user_id=?", (c.from_user.id,))
    await c.answer("Savat tozalandi")
    await show(c, "🛒 Savat bo'sh.")


# ---------- Buyurtma berish ----------
@dp.callback_query(F.data == "checkout")
async def checkout(c: CallbackQuery, state: FSMContext):
    if not db("SELECT 1 FROM cart WHERE user_id=?", (c.from_user.id,)):
        await c.answer("Savat bo'sh", show_alert=True)
        return
    await c.answer()
    await state.set_state(Order.name)
    await c.message.answer("Ismingizni yozing (bekor qilish: /cancel):")


@dp.message(Order.name, F.text)
async def order_name(m: Message, state: FSMContext):
    await state.update_data(name=m.text.strip()[:60])
    await state.set_state(Order.phone)
    markup = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📞 Raqamni yuborish", request_contact=True)]],
        resize_keyboard=True, one_time_keyboard=True,
    )
    await m.answer("Telefon raqamingizni yuboring (tugma yoki qo'lda yozing):",
                   reply_markup=markup)


@dp.message(Order.phone)
async def order_phone(m: Message, state: FSMContext):
    phone = m.contact.phone_number if m.contact else (m.text or "").strip()
    if len(re.sub(r"\D", "", phone)) < 9:
        await m.answer("Raqam noto'g'ri. Masalan: +998901234567")
        return
    await state.update_data(phone=phone)
    await state.set_state(Order.address)
    await m.answer("Yetkazib berish manzilini yozing:", reply_markup=ReplyKeyboardRemove())


@dp.message(Order.address, F.text)
async def order_address(m: Message, state: FSMContext):
    uid = m.from_user.id
    data = await state.get_data()
    await state.clear()
    items = db("""SELECT p.name, p.price, c.qty FROM cart c
                  JOIN products p ON p.id = c.product_id WHERE c.user_id=?""", (uid,))
    if not items:
        await m.answer("Savat bo'sh.", reply_markup=menu(uid))
        return
    total = sum(p * q for _, p, q in items)
    oid = db("INSERT INTO orders(user_id,name,phone,address,total,created) "
             "VALUES(?,?,?,?,?,?)",
             (uid, data["name"], data["phone"], m.text.strip()[:200], total,
              datetime.now().strftime("%d.%m.%Y %H:%M")), last=True)
    for n, p, q in items:
        db("INSERT INTO order_items(order_id,name,price,qty) VALUES(?,?,?,?)",
           (oid, n, p, q))
    db("DELETE FROM cart WHERE user_id=?", (uid,))
    await m.answer(f"✅ Buyurtma #{oid} qabul qilindi!\nJami: {money(total)}\n"
                   "Tez orada siz bilan bog'lanamiz.", reply_markup=menu(uid))
    await notify_admins(oid)


@dp.message(F.text == "📦 Buyurtmalarim")
async def mening_buyurtmalarim(m: Message):
    rows = db("SELECT id, created, total, status FROM orders WHERE user_id=? "
              "ORDER BY id DESC LIMIT 10", (m.from_user.id,))
    if not rows:
        await m.answer("Sizda hali buyurtma yo'q.")
        return
    await m.answer("📦 So'nggi buyurtmalaringiz:\n\n" + "\n".join(
        f"#{i} | {d} | {money(t)} | {STATUS[s]}" for i, d, t, s in rows))


# ---------- Admin: buyurtma holati ----------
@dp.callback_query(F.data.startswith("st:"), IsAdmin())
async def holat(c: CallbackQuery):
    _, oid, st = c.data.split(":")
    if st not in STATUS:
        await c.answer()
        return
    oid = int(oid)
    db("UPDATE orders SET status=? WHERE id=?", (st, oid))
    await c.answer("Yangilandi")
    try:
        await c.message.edit_text(order_text(oid), reply_markup=order_kb(oid))
    except TelegramBadRequest:
        pass
    user = db("SELECT user_id FROM orders WHERE id=?", (oid,))
    if user:
        try:
            await bot.send_message(user[0][0], f"📦 Buyurtma #{oid}: {STATUS[st]}")
        except Exception:
            pass


# ---------- Admin panel ----------
def admin_markup(uid: int):
    rows = [[ib("➕ Mahsulot qo'shish", "a:add")],
            [ib("📋 Mahsulotlar", "a:prods"), ib("📦 Buyurtmalar", "a:orders")]]
    if uid == ADMIN_ID:
        rows.append([ib("👥 Adminlar", "a:admins")])
    return kb(rows)


@dp.message(F.text == "🛠 Admin panel", IsAdmin())
@dp.message(Command("admin"), IsAdmin())
async def admin_panel(m: Message):
    await m.answer("🛠 Admin panel", reply_markup=admin_markup(m.from_user.id))


@dp.callback_query(F.data == "a:prods", IsAdmin())
async def a_prods(c: CallbackQuery):
    await c.answer()
    text, markup = products_admin_view()
    await show(c, text, markup)


@dp.callback_query(F.data.startswith("del:"), IsAdmin())
async def a_del(c: CallbackQuery):
    pid = int(c.data.split(":")[1])
    db("DELETE FROM products WHERE id=?", (pid,))
    db("DELETE FROM cart WHERE product_id=?", (pid,))
    await c.answer("O'chirildi")
    text, markup = products_admin_view()
    await show(c, text, markup)


@dp.callback_query(F.data == "a:orders", IsAdmin())
async def a_orders(c: CallbackQuery):
    await c.answer()
    rows = db("SELECT id FROM orders WHERE status IN ('new','accepted') "
              "ORDER BY id DESC LIMIT 10")
    if not rows:
        await c.message.answer("Faol buyurtmalar yo'q.")
        return
    for (oid,) in rows:
        await c.message.answer(order_text(oid), reply_markup=order_kb(oid))


# ---------- Admin: mahsulot qo'shish ----------
@dp.callback_query(F.data == "a:add", IsAdmin())
async def a_add(c: CallbackQuery, state: FSMContext):
    await c.answer()
    cats = [r[0] for r in db("SELECT name FROM categories ORDER BY name")]
    hint = ("\n\nMavjud kategoriyalar:\n" + "\n".join(cats)) if cats else ""
    await state.set_state(AddProduct.category)
    await c.message.answer("Kategoriya nomini yozing (yangisini ham yozish mumkin). "
                           "Bekor qilish: /cancel" + hint)


@dp.message(AddProduct.category, F.text)
async def p_category(m: Message, state: FSMContext):
    await state.update_data(category=m.text.strip()[:40])
    await state.set_state(AddProduct.name)
    await m.answer("Mahsulot nomini yozing:")


@dp.message(AddProduct.name, F.text)
async def p_name(m: Message, state: FSMContext):
    await state.update_data(name=m.text.strip()[:80])
    await state.set_state(AddProduct.price)
    await m.answer("Narxini so'mda yozing (faqat raqam, masalan 25000):")


@dp.message(AddProduct.price, F.text)
async def p_price(m: Message, state: FSMContext):
    raw = m.text.replace(" ", "")
    if not raw.isdigit() or int(raw) <= 0:
        await m.answer("Faqat raqam yozing. Masalan: 25000")
        return
    await state.update_data(price=int(raw))
    await state.set_state(AddProduct.description)
    await m.answer("Tavsifini yozing (o'tkazib yuborish uchun - belgisini yuboring):")


@dp.message(AddProduct.description, F.text)
async def p_desc(m: Message, state: FSMContext):
    desc = "" if m.text.strip() == "-" else m.text.strip()[:300]
    await state.update_data(description=desc)
    await state.set_state(AddProduct.photo)
    await m.answer("Mahsulot rasmini yuboring (o'tkazib yuborish uchun - yozing):")


@dp.message(AddProduct.photo)
async def p_photo(m: Message, state: FSMContext):
    if m.photo:
        file_id = m.photo[-1].file_id
    elif m.text and m.text.strip() == "-":
        file_id = None
    else:
        await m.answer("Rasm yuboring yoki - yozing.")
        return
    d = await state.get_data()
    await state.clear()
    db("INSERT OR IGNORE INTO categories(name) VALUES(?)", (d["category"],))
    cid = db("SELECT id FROM categories WHERE name=?", (d["category"],))[0][0]
    db("INSERT INTO products(category_id,name,price,description,photo) "
       "VALUES(?,?,?,?,?)", (cid, d["name"], d["price"], d["description"], file_id))
    await m.answer(f"✅ Qo'shildi: {d['name']} — {money(d['price'])}",
                   reply_markup=menu(m.from_user.id))


# ---------- Asosiy admin: adminlarni boshqarish ----------
def admins_text() -> str:
    ids = [r[0] for r in db("SELECT id FROM admins")]
    lst = "\n".join(f"• {i}" for i in ids) or "(boshqa admin yo'q)"
    return (f"👥 Adminlar\n\n👑 Asosiy: {ADMIN_ID}\n{lst}\n\n"
            "Qo'shish: /addadmin ID\nO'chirish: /deladmin ID\n"
            "(ID ni odam botga /id yozib bilib oladi)")


@dp.callback_query(F.data == "a:admins", IsSuper())
async def a_admins(c: CallbackQuery):
    await c.answer()
    await c.message.answer(admins_text())


@dp.message(Command("admins"), IsSuper())
async def admins_list(m: Message):
    await m.answer(admins_text())


@dp.message(Command("addadmin"), IsSuper())
async def add_admin(m: Message):
    parts = (m.text or "").split()
    if len(parts) != 2 or not parts[1].isdigit():
        await m.answer("Ishlatilishi: /addadmin 123456789")
        return
    uid = int(parts[1])
    db("INSERT OR IGNORE INTO admins(id) VALUES(?)", (uid,))
    await m.answer(f"✅ {uid} admin qilindi.")
    try:
        await bot.send_message(uid, "🛠 Siz do'kon adminisiz. /start ni bosing.")
    except Exception:
        await m.answer("(Foydalanuvchi botni hali boshlamagan, unga /start yozishini ayting.)")


@dp.message(Command("deladmin"), IsSuper())
async def del_admin(m: Message):
    parts = (m.text or "").split()
    if len(parts) != 2 or not parts[1].isdigit():
        await m.answer("Ishlatilishi: /deladmin 123456789")
        return
    db("DELETE FROM admins WHERE id=?", (int(parts[1]),))
    await m.answer(f"✅ {parts[1]} adminlikdan olindi.")


# ---------- Boshqa xabarlar (ENG PASTDA) ----------
@dp.message()
async def boshqa(m: Message):
    await m.answer("Iltimos, menyudan tanlang 👇", reply_markup=menu(m.from_user.id))


async def main():
    db_init()
    seed()
    me = await bot.get_me()
    print(f">>> Do'kon boti ishga tushdi: @{me.username}")
    if ADMIN_ID:
        try:
            await bot.send_message(ADMIN_ID, "🟢 Do'kon boti ishga tushdi!")
        except Exception:
            pass
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())