# app.py
import os
import json
import logging
import asyncio
import qrcode
from aiohttp import web
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy import Column, Integer, String, select
from aiogram import Bot, Dispatcher, types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from aiogram.filters import Command
from aiogram.webhook.aiohttp_server import SimpleRequestHandler
from dotenv import load_dotenv

load_dotenv()
BOT_TOKEN    = os.getenv("BOT_TOKEN")
ADMIN_ID     = int(os.getenv("ADMIN_ID") or 0)
ADMIN_GROUP  = os.getenv("ADMIN_GROUP", "")   # @username yoki raqam
BOT_USERNAME = os.getenv("BOT_USERNAME")
WEBHOOK_URL  = os.getenv("WEBHOOK_URL")
DATABASE_URL = os.getenv("DATABASE_URL")

# postgres+asyncpg formatiga o‘tkazamiz
if DATABASE_URL and DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

logging.basicConfig(level=logging.INFO)

engine = create_async_engine(DATABASE_URL, echo=False, future=True)
async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
Base = declarative_base()

class Menu(Base):
    __tablename__ = "menu"
    id    = Column(Integer, primary_key=True)
    name  = Column(String, nullable=False)
    price = Column(Integer, nullable=False)

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

bot = Bot(token=BOT_TOKEN, parse_mode="HTML")
dp  = Dispatcher()
user_orders = {}

# ---------- QR-kodlar ----------
def generate_qr_codes():
    os.makedirs("qr", exist_ok=True)
    for t in [f"stol{i}" for i in range(1, 6)]:
        link = f"https://t.me/{BOT_USERNAME}?start={t}"
        qrcode.make(link).save(f"qr/{t}.png")
        logging.info("qr/%s.png yaratildi", t)

# ---------- yordamchi yuborish ----------
async def send_to_admin(text: str):
    # username (@...) yoki ID bilan yuboramiz
    if ADMIN_GROUP.startswith("@"):
        await bot.send_message(ADMIN_GROUP, text, parse_mode="HTML")
    else:
        await bot.send_message(int(ADMIN_GROUP), text, parse_mode="HTML")

# ---------- TG ----------
@dp.message(Command("start"))
async def start_cmd(msg: types.Message):
    table = msg.text.partition(" ")[2] or "Noma’lum"
    web_app = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="📋 Menyu (mini-app)",
            web_app=WebAppInfo(url=f"{WEBHOOK_URL}/?table={table}"))]
    ])
    await msg.answer(
        f"Salom 👋\n🪑 Stol: <b>{table}</b>\nBuyurtma berish uchun menyuni tanlang:",
        reply_markup=web_app)

@dp.message(Command("add"))
async def add_item(msg: types.Message):
    if msg.from_user.id != ADMIN_ID:
        return await msg.reply("❌ Siz admin emassiz!")
    try:
        _, name, price = msg.text.split(maxsplit=2)
        async with async_session() as s:
            s.add(Menu(name=name, price=int(price)))
            await s.commit()
        await msg.reply(f"✅ Taom qo‘shildi: {name} – {price} so‘m")
    except Exception as e:
        await msg.reply("❌ Foydalanish: /add Nomi Narxi")

@dp.message(Command("menu"))
async def show_menu_cmd(msg: types.Message):
    async with async_session() as s:
        rows = (await s.execute(select(Menu))).scalars().all()
    if not rows:
        return await msg.answer("❌ Menyu bo‘sh!")
    text = "📋 Hozirgi menyu:\n\n" + \
           "\n".join(f"{i+1}. {r.name} – {r.price} so‘m" for i, r in enumerate(rows))
    await msg.answer(text)

@dp.callback_query(lambda c: c.data.startswith("order:"))
async def add_to_cart(cb: types.CallbackQuery):
    _, item_id, table = cb.data.split(":")
    async with async_session() as s:
        r = (await s.execute(select(Menu).where(Menu.id == int(item_id)))).scalar_one()
    uid = cb.from_user.id
    user_orders.setdefault(uid, {"table": table, "items": []})["items"].append((r.name, r.price))
    total = sum(p for _, p in user_orders[uid]["items"])
    await cb.answer(f"➕ {r.name} savatchaga qo‘shildi. Jami: {total} so‘m", show_alert=True)

@dp.callback_query(lambda c: c.data == "confirm_order")
async def confirm(cb: types.CallbackQuery):
    uid = cb.from_user.id
    if uid not in user_orders or not user_orders[uid]["items"]:
        return await cb.answer("❌ Savatchangiz bo‘sh!", show_alert=True)
    table = user_orders[uid]["table"]
    items = user_orders[uid]["items"]
    total = sum(p for _, p in items)
    text = (f"📥 Yangi buyurtma!\n🪑 Stol: <b>{table}</b>\n\n" +
            "\n".join(f"{i+1}. {n} – {p} so‘m" for i, (n, p) in enumerate(items)) +
            f"\n\n💰 Jami: <b>{total}</b> so‘m")
    await send_to_admin(text)
    await cb.message.answer("✅ Buyurtmangiz qabul qilindi! Tez orada tayyor bo‘ladi.")
    user_orders[uid]["items"].clear()

# ---------- mini-app ----------
async def mini_app(request: web.Request):
    table = request.query.get("table", "Noma’lum")
    html = f"""
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8"/>
  <title>Restoran menyu</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <script src="https://telegram.org/js/telegram-web-app.js"></script>
  <style>
    body{{font-family:Arial,Helvetica,sans-serif;background:#f2f2f2;margin:0;padding:20px}}
    .dish{{background:#fff;margin:10px 0;padding:15px;border-radius:8px;display:flex;justify-content:space-between;align-items:center}}
    button{{background:#007bff;color:#fff;border:none;padding:10px 15px;border-radius:6px;cursor:pointer}}
  </style>
</head>
<body>
  <h2>Menyu – Stol: {table}</h2>
  <div id="list"></div>
  <button id="send" style="margin-top:20px;width:100%">📤 Buyurtma yuborish</button>
  <script>
    const tg = window.Telegram.WebApp; tg.expand();
    const table = new URLSearchParams(location.search).get("table");
    let cart = [];
    async function loadMenu(){{
      const res = await fetch('/api/menu');
      const data = await res.json();
      const list = document.getElementById('list');
      data.forEach(it=>{{
        const d=document.createElement('div');
        d.className='dish';
        d.innerHTML=`<div><div><strong>${it.name}</strong></div><div>${it.price} so'm</div></div>
                     <button onclick="add(${it.id},'${it.name}',${it.price})">+</button>`;
        list.appendChild(d);
      }});
    }}
    function add(id,name,price){{
      cart.push({{id:id,name:name,price:price}});
      tg.showAlert(name + ' qo‘shildi!');
    }}
    document.getElementById('send').onclick = async ()=>{{
      if(!cart.length) return tg.showAlert('Savatcha bo‘sh!');
      await fetch('/api/order',{{
        method:'POST',
        headers:{{'Content-Type':'application/json'}},
        body: JSON.stringify({{table:table, items:cart}})
      }});
      tg.showAlert('Buyurtma yuborildi!');
      cart=[];
    }};
    loadMenu();
  </script>
</body>
</html>"""
    return web.Response(text=html, content_type="text/html")

# ---------- API ----------
async def api_menu(request: web.Request):
    async with async_session() as s:
        rows = (await s.execute(select(Menu))).scalars().all()
    return web.json_response([{"id": r.id, "name": r.name, "price": r.price} for r in rows])

async def api_order(request: web.Request):
    data = await request.json()
    table = data.get("table", "Noma’lum")
    items = data.get("items", [])
    total = sum(it["price"] for it in items)
    text = (f"📥 Yangi buyurtma (mini-app)!\n🪑 Stol: <b>{table}</b>\n\n" +
            "\n".join(f"{i+1}. {it['name']} – {it['price']} so‘m" for i, it in enumerate(items)) +
            f"\n\n💰 Jami: <b>{total}</b> so‘m")
    await send_to_admin(text)
    return web.json_response({"ok": True})

# ---------- webhook ----------
async def on_startup(app: web.Application):
    await init_db()
    generate_qr_codes()
    await bot.set_webhook(f"{WEBHOOK_URL}/bot{BOT_TOKEN.split(':')[1]}")

async def on_cleanup(app: web.Application):
    await bot.delete_webhook()
    await engine.dispose()

def create_app():
    app = web.Application()
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    app.router.add_get("/", mini_app)
    app.router.add_get("/api/menu", api_menu)
    app.router.add_post("/api/order", api_order)
    SimpleRequestHandler(dispatcher=dp, bot=bot).register(
        app, path=f"/bot{BOT_TOKEN.split(':')[1]}")
    return app

if __name__ == "__main__":
    import sys
    from aiohttp.web import run_app
    run_app(create_app(), port=int(os.getenv("PORT", 8000)))