# app.py
import os
import logging
import asyncio
import qrcode
from aiohttp import web
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy import Column, Integer, String, select
from aiogram import Bot, Dispatcher, types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.filters import Command
from aiogram.webhook.aiohttp_server import SimpleRequestHandler
from dotenv import load_dotenv

# ---------- env ----------
load_dotenv()
BOT_TOKEN    = os.getenv("BOT_TOKEN")
ADMIN_ID     = int(os.getenv("ADMIN_ID"))
ADMIN_GROUP  = os.getenv("ADMIN_GROUP")
BOT_USERNAME = os.getenv("BOT_USERNAME")
WEBHOOK_URL  = os.getenv("WEBHOOK_URL")
DATABASE_URL = os.getenv("DATABASE_URL")

if DATABASE_URL and DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

logging.basicConfig(level=logging.INFO)

# ---------- db ----------
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

# ---------- bot ----------
bot = Bot(token=BOT_TOKEN, parse_mode="HTML")
dp  = Dispatcher()
user_orders = {}

# ---------- helpers ----------
def generate_qr_codes():
    for t in [f"stol{i}" for i in range(1,6)]:
        link = f"https://t.me/{BOT_USERNAME}?start={t}"
        qrcode.make(link).save(f"{t}.png")
        print(f"{t}.png yaratildi")

# ---------- handlers ----------
@dp.message(Command("start"))
async def start_cmd(msg: types.Message):
    table = msg.text.split(maxsplit=1)[1] if len(msg.text.split())>1 else "Noma’lum"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🍽 Menyu", callback_data=f"menu:{table}")]
    ])
    await msg.answer(f"Salom 👋\n🪑 Stol: {table}\nBuyurtma berish uchun menyuni tanlang:", reply_markup=kb)

@dp.message(Command("add"))
async def add_item(msg: types.Message):
    if msg.from_user.id != ADMIN_ID:
        return await msg.reply("❌ Siz admin emassiz!")
    try:
        _, name, price = msg.text.split(maxsplit=2)
        async with async_session() as s:
            s.add(Menu(name=name, price=int(price)))
            await s.commit()
        await msg.reply(f"✅ Taom qo‘shildi: {name} - {price} so‘m")
    except:
        await msg.reply("❌ Foydalanish: /add Nomi Narxi")

@dp.message(Command("menu"))
async def show_menu(msg: types.Message):
    async with async_session() as s:
        rows = (await s.execute(select(Menu))).scalars().all()
    if not rows:
        return await msg.answer("❌ Menyu bo‘sh!")
    text = "📋 Hozirgi menyu:\n\n" + "\n".join(f"{i+1}. {r.name} - {r.price} so‘m" for i,r in enumerate(rows))
    await msg.answer(text)

@dp.callback_query(lambda c: c.data.startswith("menu:"))
async def show_menu_cb(cb: types.CallbackQuery):
    table = cb.data.split(":")[1]
    async with async_session() as s:
        rows = (await s.execute(select(Menu))).scalars().all()
    if not rows:
        return await cb.message.answer("❌ Menyu bo‘sh. Admin taom qo‘shishi kerak.")
    for r in rows:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🛒 Savatchaga qo‘shish", callback_data=f"order:{r.id}:{table}")]
        ])
        await cb.message.answer(f"🍲 {r.name} - {r.price} so‘m", reply_markup=kb)

@dp.callback_query(lambda c: c.data.startswith("order:"))
async def add_to_cart(cb: types.CallbackQuery):
    _, item_id, table = cb.data.split(":")
    async with async_session() as s:
        r = (await s.execute(select(Menu).where(Menu.id==int(item_id)))).scalar_one()
    uid = cb.from_user.id
    user_orders.setdefault(uid, {"table":table, "items":[]})["items"].append((r.name, r.price))
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Buyurtmani tasdiqlash", callback_data="confirm_order")]
    ])
    await cb.message.answer(
        f"➕ Savatchaga qo‘shildi: {r.name} - {r.price} so‘m\n"
        f"🛒 Hozirgi buyurtmalar soni: {len(user_orders[uid]['items'])}", reply_markup=kb)

@dp.callback_query(lambda c: c.data=="confirm_order")
async def confirm(cb: types.CallbackQuery):
    uid = cb.from_user.id
    if uid not in user_orders or not user_orders[uid]["items"]:
        return await cb.message.answer("❌ Savatchangiz bo‘sh!")
    table = user_orders[uid]["table"]
    items = user_orders[uid]["items"]
    total = sum(p for _,p in items)
    text = f"📥 Yangi buyurtma!\n🪑 Stol: {table}\n\n" + "\n".join(f"{i+1}. {n} - {p} so‘m" for i,(n,p) in enumerate(items)) + f"\n\n💰 Jami: {total} so‘m"
    await bot.send_message(ADMIN_GROUP, text)
    await cb.message.answer("✅ Buyurtmangiz qabul qilindi! Tez orada tayyor bo‘ladi.")
    user_orders[uid]["items"].clear()

# ---------- webhook aiohttp ----------
async def on_startup(app: web.Application):
    await init_db()
    generate_qr_codes()
    await bot.set_webhook(f"{WEBHOOK_URL}/bot{BOT_TOKEN.split(':')[1]}")

async def on_cleanup(app: web.Application):
    await bot.delete_webhook()
    await engine.dispose()

def create_app(argv=None):
    app = web.Application()
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path=f"/bot{BOT_TOKEN.split(':')[1]}")
    return app

# ---------- entry point for aiohttp.web ----------
if __name__ == "__main__":
    import sys
    from aiohttp.web import main
    sys.argv[0] = "aiohttp.web"
    sys.exit(main(sys.argv))