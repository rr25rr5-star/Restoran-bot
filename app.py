# app.py
import os
import json
import logging
import asyncio
import qrcode
from pathlib import Path
from urllib.parse import quote
from aiohttp import web
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy import Column, Integer, String, DateTime, func, select
from sqlalchemy import text   # eng yuqoriga, boshqa importlardan keyin
from aiogram import Bot, Dispatcher, types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from aiogram.filters import Command
from aiogram.webhook.aiohttp_server import SimpleRequestHandler
from dotenv import load_dotenv
import aiofiles

# ---------- ENV ----------
load_dotenv()
BOT_TOKEN    = os.getenv("BOT_TOKEN")
ADMIN_ID     = int(os.getenv("ADMIN_ID") or 0)
ADMIN_GROUP  = os.getenv("ADMIN_GROUP", "")
BOT_USERNAME = os.getenv("BOT_USERNAME")
WEBHOOK_URL  = os.getenv("WEBHOOK_URL")
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./app.db")
PORT         = int(os.getenv("PORT", 8000))

if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

logging.basicConfig(level=logging.INFO)

# ---------- DB ----------
engine = create_async_engine(DATABASE_URL, echo=False, future=True)
async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
Base = declarative_base()

class Menu(Base):
    __tablename__ = "menu"
    id          = Column(Integer, primary_key=True)
    name        = Column(String, nullable=False)
    price       = Column(Integer, nullable=False)
    image       = Column(String, nullable=True)
    description = Column(String, nullable=True)
    category    = Column(String, nullable=False)

class Order(Base):
    __tablename__ = "orders"
    id         = Column(Integer, primary_key=True)
    table      = Column(String, nullable=False)
    items      = Column(String, nullable=False)
    total      = Column(Integer, nullable=False)
    created_at = Column(DateTime, server_default=func.now())
    user_id    = Column(Integer, nullable=False)

# ---------- INIT ----------
async def init_db():
    async with engine.begin() as conn:
        # Eski jadvalni o‘chiramiz (bepul uchun)
        await conn.execute(text("DROP TABLE IF EXISTS menu CASCADE"))
        await conn.execute(text("DROP TABLE IF EXISTS orders CASCADE"))
        # Yangi jadvalni yaratamiz
        await conn.run_sync(Base.metadata.create_all)

# ---------- QR ----------
def generate_qr_codes():
    os.makedirs("qr", exist_ok=True)
    for t in [f"stol{i}" for i in range(1, 6)]:
        link = f"https://t.me/{BOT_USERNAME}?start={t}"
        qrcode.make(link).save(f"qr/{t}.png")
        logging.info("qr/%s.png yaratildi", t)

# ---------- UTILS ----------
async def send_to_admin(text: str):
    try:
        if ADMIN_GROUP.startswith("@"):
            await bot.send_message(ADMIN_GROUP, text, parse_mode="HTML")
        else:
            await bot.send_message(int(ADMIN_GROUP), text, parse_mode="HTML")
    except Exception as e:
        logging.warning("Admin xabar yuborilmadi: %s", e)

# ---------- BOT ----------
bot = Bot(token=BOT_TOKEN, parse_mode="HTML")
dp  = Dispatcher()

@dp.message(Command("start"))
async def start_cmd(msg: types.Message):
    table = msg.text.partition(" ")[2] or "Noma’lum"
    safe_table = quote(table)
    web_app_url = f"{WEBHOOK_URL.rstrip('/')}/?table={safe_table}"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Menyu (mini-app)",
                              web_app=WebAppInfo(url=web_app_url))]
    ])
    await msg.answer(f"Salom 👋\n🪑 Stol: <b>{table}</b>",
                     parse_mode="HTML", reply_markup=kb)

@dp.message(Command("admin"))
async def admin_cmd(msg: types.Message):
    if msg.from_user.id != ADMIN_ID:
        return await msg.answer("❌ Siz admin emassiz!")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Admin panel (WebApp)",
                              web_app=WebAppInfo(url=f"{WEBHOOK_URL.rstrip('/')}/admin"))]
    ])
    await msg.answer("🛠️ Admin panel:", reply_markup=kb)

@dp.message(Command("add_full"))
async def add_full(msg: types.Message):
    if msg.from_user.id != ADMIN_ID:
        return await msg.answer("❌ Siz admin emassiz!")
    try:
        _, name, price, category, image, desc = msg.text.split(" ", maxsplit=5)
        assert category in {"ovqat", "ichimlik", "desert"}
        async with async_session() as s:
            s.add(Menu(name=name, price=int(price), image=image,
                     description=desc, category=category))
            await s.commit()
        await msg.answer("✅ Taom to‘liq qo‘shildi!")
    except Exception as e:
        await msg.answer("❌ Format: /add_full Nomi Narxi Kategoriya RasmURL Tarif\nKategoriya: ovqat | ichimlik | desert")

# ---------- WEB APP ----------
UPLOAD_DIR = Path("static/images")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

async def mini_app(request: web.Request):
    table = request.query.get("table", "Noma’lum")
    # CSS ni f-string emas, oddiy qator bilan
    css = """
    body{margin:0;font-family:Arial;background:#f2f2f2;padding:20px 20px 180px 20px}
    .cat{margin-top:20px;font-weight:bold}
    .dish{background:#fff;margin:8px 0;padding:10px;border-radius:8px;display:flex;gap:10px;align-items:center}
    .dish img{width:60px;height:60px;object-fit:cover;border-radius:6px}
    .info{flex:1}
    .info div:first-child{font-weight:bold}
    .info small{color:#555}
    button{background:#007bff;color:#fff;border:none;padding:8px 12px;border-radius:6px;cursor:pointer}
    .cart{position:fixed;bottom:0;left:0;right:0;background:#fff;border-top:1px solid #ccc;padding:10px 15px;max-height:170px;overflow-y:auto;font-size:14px;z-index:999}
    .cart-header{font-weight:bold;margin-bottom:5px}
    .cart-item{display:flex;justify-content:space-between;padding:2px 0}
    .cart-total{font-weight:bold;margin-top:5px}
    """.strip()

    html = f"""
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8"/>
  <title>Menyu</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <script src="https://telegram.org/js/telegram-web-app.js"></script>
  <style>{css}</style>
</head>
<body>
  <h2>Menyu – Stol: """ + table + """</h2>
  <div id="list"></div>
  <div id="cart" class="cart">
    <div class="cart-header">🛒 Savatcha</div>
    <div id="cart-list"></div>
    <div id="cart-total" class="cart-total">Jami: 0 so'm</div>
  </div>
  <button id="send" style="margin-top:20px;width:100%">📤 Buyurtma yuborish</button>
  <script>
    const tg = window.Telegram.WebApp; tg.expand();
    const table = new URLSearchParams(location.search).get("table");
    let cart = [];

    async function loadMenu(){{
      const res = await fetch('/api/menu');
      const data = await res.json();
      const list = document.getElementById('list');
      list.innerHTML = '';
      const cats = [...new Set(data.map(it=>it.category))];
      cats.forEach(cat=>{{
        list.insertAdjacentHTML('beforeend', `<div class="cat">${{cat.toUpperCase()}}</div>`);
        data.filter(it=>it.category===cat).forEach(it=>{{
          list.insertAdjacentHTML('beforeend', `
            <div class="dish">
              <img src="/static/images/${{it.image}}" onerror="this.src='https://via.placeholder.com/60'"/>
              <div class="info">
                <div>${{it.name}}</div>
                <div>${{it.price}} so'm</div>
                <small>${{it.description}}</small>
              </div>
              <button onclick="add(${{it.id}},'${{it.name}}',${{it.price}})">+</button>
            </div>`);
        }});
      }});
    }}

    function updateCart(){{
      const listBox=document.getElementById('cart-list');
      const totalBox=document.getElementById('cart-total');
      listBox.innerHTML=''; let total=0;
      cart.forEach(it=>{{
        total+=it.price*it.qty;
        listBox.insertAdjacentHTML('beforeend', `
          <div class="cart-item">
            <span>
              <button onclick="dec(${{it.id}})">–</button>
              ${{it.name}} (x${{it.qty}})
              <button onclick="add(${{it.id}},'${{it.name}}',${{it.price}})">+</button>
            </span>
            <span>${{it.price*it.qty}} so'm</span>
          </div>`);
      }});
      totalBox.textContent=`Jami: ${{total}} so'm`;
    }}

    function add(id,name,price){{
      const found=cart.find(item=>item.id===id);
      if(found){{ found.qty+=1; }}
      else {{ cart.push({{id,name,price,qty:1}}); }}
      updateCart();
    }}
    function dec(id){{
      const it=cart.find(x=>x.id===id);
      if(!it) return;
      it.qty-=1;
      if(it.qty===0) cart=cart.filter(x=>x.id!==id);
      updateCart();
    }}

    document.getElementById('send').onclick = async ()=>{{
      if(!cart.length){{ tg.showAlert('Savatcha bo‘sh!'); return; }}
      await fetch('/api/order',{{
        method:'POST',
        headers:{{'Content-Type':'application/json'}},
        body: JSON.stringify({{table:table, items:cart, user_id:tg.initDataUnsafe.user.id}})
      }});
      tg.showAlert('Buyurtma yuborildi!');
      cart=[]; updateCart();
    }};

    loadMenu();
  </script>
</body>
</html>"""
    return web.Response(text=html, content_type="text/html")

# ------------------- ADMIN PANEL -------------------
async def admin_panel(request: web.Request):
    html = """
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8"/>
  <title>Admin panel</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <script src="https://telegram.org/js/telegram-web-app.js"></script>
  <style>
    body{font-family:Arial,Helvetica,sans-serif;background:#f2f2f2;margin:0;padding:20px}
    .form,.list{background:#fff;margin:10px 0;padding:15px;border-radius:8px}
    input,select,button{width:100%;padding:8px;margin:5px 0;border:1px solid #ccc;border-radius:4px}
    button{background:#007bff;color:#fff;cursor:pointer}
    img{width:60px;height:60px;object-fit:cover;border-radius:4px}
    .item{display:flex;justify-content:space-between;align-items:center;margin:6px 0}
  </style>
</head>
<body>
  <h2>Admin panel</h2>
  <div class="form">
    <h3>Taom qo‘shish</h3>
    <input type="text" id="name" placeholder="Nomi">
    <input type="number" id="price" placeholder="Narxi">
    <input type="text" id="desc" placeholder="Tarif">
    <select id="cat">
      <option value="ovqat">Ovqat</option>
      <option value="ichimlik">Ichimlik</option>
      <option value="desert">Desert</option>
    </select>
    <input type="file" id="imgFile" accept="image/*">
    <button onclick="addDish()">➕ Qo‘shish</button>
  </div>
  <div class="list">
    <h3>Taomlar</h3>
    <div id="list"></div>
  </div>
  <script>
    const tg = window.Telegram.WebApp; tg.expand();
    async function loadMenu(){
      const res = await fetch('/api/menu');
      const data = await res.json();
      const box = document.getElementById('list');
      box.innerHTML = '';
      data.forEach(it=>{
        box.insertAdjacentHTML('beforeend', `
          <div class="item">
            <div style="display:flex;align-items:center;gap:8px">
              <img src="/static/images/${it.image}" onerror="this.src='https://via.placeholder.com/60'"/>
              <div><b>${it.name}</b> – ${it.price} so‘m<br><small>${it.description}</small><br><i>${it.category}</i></div>
            </div>
            <button onclick="delItem(${it.id})">🗑️</button>
          </div>`);
      });
    }
    async function addDish(){
      const fd = new FormData();
      fd.append('name', document.getElementById('name').value.trim());
      fd.append('price', document.getElementById('price').value);
      fd.append('description', document.getElementById('desc').value.trim());
      fd.append('category', document.getElementById('cat').value);
      const file = document.getElementById('imgFile').files[0];
      if(file) fd.append('image', file);
      await fetch('/api/admin/add-file', {method:'POST', body: fd});
      tg.showAlert('Qo‘shildi!'); loadMenu();
    }
    async function delItem(id){
      await fetch('/api/admin/delete', {
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify({id})
      });
      tg.showAlert('O‘chirildi!'); loadMenu();
    }
    loadMenu();
  </script>
</body>
</html>"""
    return web.Response(text=html, content_type="text/html")

# ------------------- API -------------------
async def api_menu(request: web.Request):
    cat = request.query.get("cat")
    async with async_session() as s:
        q = select(Menu)
        if cat:
            q = q.where(Menu.category == cat)
        rows = (await s.execute(q)).scalars().all()
    return web.json_response([{"id": r.id, "name": r.name, "price": r.price,
                               "image": r.image or "", "description": r.description,
                               "category": r.category} for r in rows])

async def api_order(request: web.Request):
    data  = await request.json()
    table = data.get("table", "Noma’lum")
    items = data.get("items", [])
    total = sum(it["price"] * it.get("qty", 1) for it in items)
    user_id = data.get("user_id") or 0
    async with async_session() as s:
        s.add(Order(table=table, items=json.dumps(items, ensure_ascii=False),
                    total=total, user_id=user_id))
        await s.commit()
    text = (f"📥 <b>Yangi buyurtma</b>\n🪑 Stol: <b>{table}</b>\n\n" +
            "\n".join(f"{i+1}. {it['name']} (x{it.get('qty',1)}) – {it['price']*it.get('qty',1)} so‘m"
                      for i, it in enumerate(items)) +
            f"\n\n💰 Jami: <b>{total}</b> so‘m")
    await send_to_admin(text)
    return web.json_response({"ok": True})
async def api_admin_add_file(request: web.Request):
    reader = await request.multipart()
    data = {}
    async for field in reader:
        if field.name == "image" and field.filename:
            ext = Path(field.filename).suffix
            name = f"{field.name}_{asyncio.get_event_loop().time()}{ext}"
            path = UPLOAD_DIR / name
            async with aiofiles.open(path, 'wb') as f:
                while True:
                    chunk = await field.read_chunk(1024)
                    if not chunk:
                        break
                    await f.write(chunk)
            data['image'] = name
        elif field.name == 'price':
            data['price'] = int((await field.read()).decode())
        else:
            data[field.name] = (await field.read()).decode()
    async with async_session() as s:
        s.add(Menu(**data))
        await s.commit()
    return web.json_response({"ok": True})

async def api_admin_delete(request: web.Request):
    data = await request.json()
    idx = data.get("id")
    async with async_session() as s:
        row = (await s.execute(select(Menu).where(Menu.id == idx))).scalar_one_or_none()
        if row:
            await s.delete(row)
            await s.commit()
    return web.json_response({"ok": True})

# ------------------- APP -------------------
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
    app.router.add_get("/", mini_app)
    app.router.add_get("/admin", admin_panel)
    app.router.add_get("/api/menu", api_menu)
    app.router.add_post("/api/order", api_order)
    app.router.add_post("/api/admin/add-file", api_admin_add_file)
    app.router.add_post("/api/admin/delete", api_admin_delete)
    app.router.add_static('/static', path='static', name='static')
    SimpleRequestHandler(dispatcher=dp, bot=bot).register(
        app, path=f"/bot{BOT_TOKEN.split(':')[1]}")
    return app

if __name__ == "__main__":
    from aiohttp.web import run_app
    run_app(create_app(), port=PORT)