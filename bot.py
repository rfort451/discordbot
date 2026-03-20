"""
Discord Bot - Single File, Clean Build
All commands in ONE file to prevent ANY duplicates
Uses PostgreSQL for Railway (persistent) or SQLite for local
"""
import os
import discord
from discord.ext import commands
import aiohttp
import aiosqlite
import random
import asyncio
import html
from datetime import datetime
from io import BytesIO
from dotenv import load_dotenv

try:
    import asyncpg
    HAS_ASYNCPG = True
except:
    HAS_ASYNCPG = False

try:
    from PIL import Image, ImageDraw, ImageFont
    HAS_PIL = True
except:
    HAS_PIL = False

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
PREFIX = os.getenv("BOT_PREFIX", "!")
DATABASE_URL = os.getenv("DATABASE_URL")  # Railway PostgreSQL

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix=PREFIX, intents=intents, help_command=None)

# ==================== STORAGE ====================
active_games = {}
active_quizzes = {}
boss_battles = {}
last_attack = {}
message_cooldowns = {}
daily_render = {}
db_pool = None  # PostgreSQL connection pool

# Image directories
BASE_PATH = os.path.join(os.path.dirname(__file__), "images")
for pool in ["gm", "gn", "ga", "render", "welcome"]:
    os.makedirs(os.path.join(BASE_PATH, pool), exist_ok=True)

# ==================== DATABASE ====================
USE_POSTGRES = bool(DATABASE_URL and HAS_ASYNCPG)

async def init_db():
    global db_pool
    if USE_POSTGRES:
        print("📦 Using PostgreSQL (persistent)")
        db_pool = await asyncpg.create_pool(DATABASE_URL)
        async with db_pool.acquire() as conn:
            await conn.execute("""CREATE TABLE IF NOT EXISTS user_coins 
                (guild_id BIGINT, user_id BIGINT, coins INTEGER DEFAULT 0, PRIMARY KEY (guild_id, user_id))""")
            await conn.execute("""CREATE TABLE IF NOT EXISTS shop_items 
                (guild_id BIGINT, item_id INTEGER, name TEXT, price INTEGER, PRIMARY KEY (guild_id, item_id))""")
            await conn.execute("""CREATE TABLE IF NOT EXISTS shop_purchases 
                (id SERIAL PRIMARY KEY, guild_id BIGINT, user_id BIGINT, item_name TEXT, price INTEGER, purchased_at TIMESTAMP)""")
            await conn.execute("""CREATE TABLE IF NOT EXISTS guild_settings 
                (guild_id BIGINT PRIMARY KEY, welcome_channel_id BIGINT, minigame_channel_id BIGINT, gambling_channel_id BIGINT, reaction_channel_id BIGINT, reaction_emotes TEXT)""")
            await conn.execute("""CREATE TABLE IF NOT EXISTS custom_commands 
                (guild_id BIGINT, name TEXT, response TEXT, PRIMARY KEY (guild_id, name))""")
    else:
        print("📦 Using SQLite (local)")
        async with aiosqlite.connect("bot.db") as db:
            await db.execute("""CREATE TABLE IF NOT EXISTS user_coins 
                (guild_id INTEGER, user_id INTEGER, coins INTEGER DEFAULT 0, PRIMARY KEY (guild_id, user_id))""")
            await db.execute("""CREATE TABLE IF NOT EXISTS shop_items 
                (guild_id INTEGER, item_id INTEGER, name TEXT, price INTEGER, PRIMARY KEY (guild_id, item_id))""")
            await db.execute("""CREATE TABLE IF NOT EXISTS shop_purchases 
                (id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER, user_id INTEGER, item_name TEXT, price INTEGER, purchased_at TIMESTAMP)""")
            await db.execute("""CREATE TABLE IF NOT EXISTS guild_settings 
                (guild_id INTEGER PRIMARY KEY, welcome_channel_id INTEGER, minigame_channel_id INTEGER, gambling_channel_id INTEGER, reaction_channel_id INTEGER, reaction_emotes TEXT)""")
            await db.execute("""CREATE TABLE IF NOT EXISTS custom_commands 
                (guild_id INTEGER, name TEXT, response TEXT, PRIMARY KEY (guild_id, name))""")
            await db.commit()

# ==================== HELPERS ====================
async def fetch_api(url):
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=10) as r:
                if r.status == 200:
                    return await r.json()
    except:
        pass
    return None

async def get_coins(guild_id, user_id):
    if USE_POSTGRES:
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow("SELECT coins FROM user_coins WHERE guild_id=$1 AND user_id=$2", guild_id, user_id)
            return row['coins'] if row else 0
    else:
        async with aiosqlite.connect("bot.db") as db:
            cur = await db.execute("SELECT coins FROM user_coins WHERE guild_id=? AND user_id=?", (guild_id, user_id))
            row = await cur.fetchone()
            return row[0] if row else 0

async def add_coins(guild_id, user_id, amount):
    current = await get_coins(guild_id, user_id)
    new = max(0, current + amount)
    if USE_POSTGRES:
        async with db_pool.acquire() as conn:
            await conn.execute("""INSERT INTO user_coins (guild_id, user_id, coins) VALUES ($1, $2, $3)
                ON CONFLICT (guild_id, user_id) DO UPDATE SET coins = $3""", guild_id, user_id, new)
    else:
        async with aiosqlite.connect("bot.db") as db:
            await db.execute("INSERT OR REPLACE INTO user_coins VALUES (?,?,?)", (guild_id, user_id, new))
            await db.commit()
    return new

async def get_channel_setting(guild_id, setting):
    if USE_POSTGRES:
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow(f"SELECT {setting} FROM guild_settings WHERE guild_id=$1", guild_id)
            return row[setting] if row else None
    else:
        async with aiosqlite.connect("bot.db") as db:
            cur = await db.execute(f"SELECT {setting} FROM guild_settings WHERE guild_id=?", (guild_id,))
            row = await cur.fetchone()
            return row[0] if row else None

async def set_guild_setting(guild_id, setting, value):
    """Update a single guild setting without wiping others"""
    if USE_POSTGRES:
        async with db_pool.acquire() as conn:
            await conn.execute("INSERT INTO guild_settings (guild_id) VALUES ($1) ON CONFLICT (guild_id) DO NOTHING", guild_id)
            await conn.execute(f"UPDATE guild_settings SET {setting}=$1 WHERE guild_id=$2", value, guild_id)
    else:
        async with aiosqlite.connect("bot.db") as db:
            await db.execute("INSERT OR IGNORE INTO guild_settings (guild_id) VALUES (?)", (guild_id,))
            await db.execute(f"UPDATE guild_settings SET {setting}=? WHERE guild_id=?", (value, guild_id))
            await db.commit()

async def get_reaction_settings(guild_id):
    """Get reaction channel and emotes for a guild"""
    if USE_POSTGRES:
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow("SELECT reaction_channel_id, reaction_emotes FROM guild_settings WHERE guild_id=$1", guild_id)
            return (row['reaction_channel_id'], row['reaction_emotes']) if row else (None, None)
    else:
        async with aiosqlite.connect("bot.db") as db:
            cur = await db.execute("SELECT reaction_channel_id, reaction_emotes FROM guild_settings WHERE guild_id=?", (guild_id,))
            row = await cur.fetchone()
            return (row[0], row[1]) if row else (None, None)

async def get_custom_command(guild_id, name):
    """Get a custom command response"""
    if USE_POSTGRES:
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow("SELECT response FROM custom_commands WHERE guild_id=$1 AND name=$2", guild_id, name)
            return row['response'] if row else None
    else:
        async with aiosqlite.connect("bot.db") as db:
            cur = await db.execute("SELECT response FROM custom_commands WHERE guild_id=? AND name=?", (guild_id, name))
            row = await cur.fetchone()
            return row[0] if row else None

async def set_custom_command(guild_id, name, response):
    """Add or update a custom command"""
    if USE_POSTGRES:
        async with db_pool.acquire() as conn:
            await conn.execute("""INSERT INTO custom_commands (guild_id, name, response) VALUES ($1, $2, $3)
                ON CONFLICT (guild_id, name) DO UPDATE SET response = $3""", guild_id, name, response)
    else:
        async with aiosqlite.connect("bot.db") as db:
            await db.execute("INSERT OR REPLACE INTO custom_commands VALUES (?,?,?)", (guild_id, name, response))
            await db.commit()

async def delete_custom_command(guild_id, name):
    """Delete a custom command, returns True if deleted"""
    if USE_POSTGRES:
        async with db_pool.acquire() as conn:
            result = await conn.execute("DELETE FROM custom_commands WHERE guild_id=$1 AND name=$2", guild_id, name)
            return "DELETE 1" in result
    else:
        async with aiosqlite.connect("bot.db") as db:
            cur = await db.execute("DELETE FROM custom_commands WHERE guild_id=? AND name=?", (guild_id, name))
            await db.commit()
            return cur.rowcount > 0

async def get_custom_commands_list(guild_id):
    """Get all custom command names for a guild"""
    if USE_POSTGRES:
        async with db_pool.acquire() as conn:
            rows = await conn.fetch("SELECT name FROM custom_commands WHERE guild_id=$1", guild_id)
            return [row['name'] for row in rows]
    else:
        async with aiosqlite.connect("bot.db") as db:
            cur = await db.execute("SELECT name FROM custom_commands WHERE guild_id=?", (guild_id,))
            rows = await cur.fetchall()
            return [row[0] for row in rows]

async def get_shop_items(guild_id):
    """Get all shop items for a guild"""
    if USE_POSTGRES:
        async with db_pool.acquire() as conn:
            rows = await conn.fetch("SELECT item_id, name, price FROM shop_items WHERE guild_id=$1", guild_id)
            return [(row['item_id'], row['name'], row['price']) for row in rows]
    else:
        async with aiosqlite.connect("bot.db") as db:
            cur = await db.execute("SELECT item_id, name, price FROM shop_items WHERE guild_id=?", (guild_id,))
            return await cur.fetchall()

async def get_shop_item(guild_id, item_id):
    """Get a specific shop item"""
    if USE_POSTGRES:
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow("SELECT name, price FROM shop_items WHERE guild_id=$1 AND item_id=$2", guild_id, item_id)
            return (row['name'], row['price']) if row else None
    else:
        async with aiosqlite.connect("bot.db") as db:
            cur = await db.execute("SELECT name, price FROM shop_items WHERE guild_id=? AND item_id=?", (guild_id, item_id))
            return await cur.fetchone()

async def add_shop_item(guild_id, name, price):
    """Add a shop item, returns the new item_id"""
    if USE_POSTGRES:
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow("SELECT COALESCE(MAX(item_id), 0) + 1 as next_id FROM shop_items WHERE guild_id=$1", guild_id)
            next_id = row['next_id']
            await conn.execute("INSERT INTO shop_items VALUES ($1, $2, $3, $4)", guild_id, next_id, name, price)
            return next_id
    else:
        async with aiosqlite.connect("bot.db") as db:
            cur = await db.execute("SELECT MAX(item_id) FROM shop_items WHERE guild_id=?", (guild_id,))
            row = await cur.fetchone()
            next_id = (row[0] or 0) + 1
            await db.execute("INSERT INTO shop_items VALUES (?,?,?,?)", (guild_id, next_id, name, price))
            await db.commit()
            return next_id

async def update_shop_item(guild_id, item_id, field, value):
    """Update a shop item field (name or price)"""
    if USE_POSTGRES:
        async with db_pool.acquire() as conn:
            await conn.execute(f"UPDATE shop_items SET {field}=$1 WHERE guild_id=$2 AND item_id=$3", value, guild_id, item_id)
    else:
        async with aiosqlite.connect("bot.db") as db:
            await db.execute(f"UPDATE shop_items SET {field}=? WHERE guild_id=? AND item_id=?", (value, guild_id, item_id))
            await db.commit()

async def delete_shop_item(guild_id, item_id):
    """Delete a shop item"""
    if USE_POSTGRES:
        async with db_pool.acquire() as conn:
            await conn.execute("DELETE FROM shop_items WHERE guild_id=$1 AND item_id=$2", guild_id, item_id)
    else:
        async with aiosqlite.connect("bot.db") as db:
            await db.execute("DELETE FROM shop_items WHERE guild_id=? AND item_id=?", (guild_id, item_id))
            await db.commit()

async def add_purchase(guild_id, user_id, item_name, price):
    """Record a purchase"""
    if USE_POSTGRES:
        async with db_pool.acquire() as conn:
            await conn.execute("INSERT INTO shop_purchases (guild_id, user_id, item_name, price, purchased_at) VALUES ($1, $2, $3, $4, NOW())", 
                guild_id, user_id, item_name, price)
    else:
        async with aiosqlite.connect("bot.db") as db:
            await db.execute("INSERT INTO shop_purchases VALUES (NULL,?,?,?,?,datetime('now'))", (guild_id, user_id, item_name, price))
            await db.commit()

async def get_purchases(guild_id, user_id, limit=10):
    """Get recent purchases for a user"""
    if USE_POSTGRES:
        async with db_pool.acquire() as conn:
            rows = await conn.fetch("SELECT item_name, price FROM shop_purchases WHERE guild_id=$1 AND user_id=$2 ORDER BY purchased_at DESC LIMIT $3", 
                guild_id, user_id, limit)
            return [(row['item_name'], row['price']) for row in rows]
    else:
        async with aiosqlite.connect("bot.db") as db:
            cur = await db.execute("SELECT item_name, price FROM shop_purchases WHERE guild_id=? AND user_id=? ORDER BY purchased_at DESC LIMIT ?", 
                (guild_id, user_id, limit))
            return await cur.fetchall()

async def set_coins(guild_id, user_id, amount):
    """Set coins directly (for editcoins)"""
    if USE_POSTGRES:
        async with db_pool.acquire() as conn:
            await conn.execute("""INSERT INTO user_coins (guild_id, user_id, coins) VALUES ($1, $2, $3)
                ON CONFLICT (guild_id, user_id) DO UPDATE SET coins = $3""", guild_id, user_id, amount)
    else:
        async with aiosqlite.connect("bot.db") as db:
            await db.execute("INSERT OR REPLACE INTO user_coins VALUES (?,?,?)", (guild_id, user_id, amount))
            await db.commit()

def is_admin(member):
    return member.guild_permissions.manage_guild or member.guild_permissions.administrator

def get_pool_images(pool_name):
    pool_path = os.path.join(BASE_PATH, pool_name)
    if not os.path.exists(pool_path):
        return []
    return sorted([f for f in os.listdir(pool_path) if f.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp'))])

def get_random_image(pool_name):
    images = get_pool_images(pool_name)
    if not images:
        return None
    img_path = os.path.join(BASE_PATH, pool_name, random.choice(images))
    with open(img_path, 'rb') as f:
        return BytesIO(f.read())

async def save_image(attachment, pool_name):
    if not attachment.content_type or not attachment.content_type.startswith("image/"):
        return False, "Not an image!"
    pool_path = os.path.join(BASE_PATH, pool_name)
    count = len(get_pool_images(pool_name)) + 1
    ext = attachment.filename.split('.')[-1] or 'png'
    filename = f"{pool_name}_{count}.{ext}"
    filepath = os.path.join(pool_path, filename)
    await attachment.save(filepath)
    return True, filename

# ==================== EVENTS ====================
@bot.event
async def on_ready():
    await init_db()
    print(f"✅ {bot.user} is online!")
    print(f"📊 Connected to {len(bot.guilds)} guilds")

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.reply("❌ You don't have permission!")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.reply(f"❌ Missing: {error.param.name}")
    elif isinstance(error, commands.CommandNotFound):
        # Check custom commands
        cmd = ctx.message.content[1:].split()[0].lower() if ctx.message.content.startswith("!") else None
        if cmd:
            response = await get_custom_command(ctx.guild.id, cmd)
            if response:
                await ctx.send(response)

@bot.event
async def on_message(message):
    if message.author.bot or not message.guild:
        return
    
    # React to images in reaction channel
    if message.attachments:
        has_image = any(a.content_type and a.content_type.startswith("image/") for a in message.attachments)
        if has_image:
            reaction_ch, reaction_emotes = await get_reaction_settings(message.guild.id)
            if reaction_ch and reaction_emotes and message.channel.id == reaction_ch:
                emotes = reaction_emotes.split(",")
                for emote in emotes:
                    try:
                        await message.add_reaction(emote.strip())
                    except:
                        pass
    
    # Coin earning
    key = f"{message.guild.id}_{message.author.id}"
    now = datetime.now()
    if key not in message_cooldowns or (now - message_cooldowns[key]).total_seconds() >= 60:
        message_cooldowns[key] = now
        await add_coins(message.guild.id, message.author.id, random.randint(1, 3))
    
    await bot.process_commands(message)

@bot.event
async def on_member_join(member):
    welcome_ch = await get_channel_setting(member.guild.id, "welcome_channel_id")
    if not welcome_ch:
        return
    channel = member.guild.get_channel(welcome_ch)
    if not channel:
        return
    
    embed = discord.Embed(
        title=f"Welcome {member.name}!",
        description=f"🎉 {member.mention} joined **{member.guild.name}**!\nMember #{member.guild.member_count}",
        color=discord.Color.green()
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    
    img = get_random_image("welcome")
    if img:
        file = discord.File(img, "welcome.png")
        embed.set_image(url="attachment://welcome.png")
        await channel.send(file=file, embed=embed)
    else:
        await channel.send(embed=embed)

# ==================== HELP ====================
@bot.command()
async def help(ctx):
    user_embed = discord.Embed(title="📚 Commands", color=discord.Color.blue())
    user_embed.add_field(name="🎮 Fun", value="`!meme` `!dadjoke` `!jokeoftheday` `!dirtyjoke` `!8ball` `!quote` `!roast`", inline=False)
    user_embed.add_field(name="🎯 Games", value="`!minigame` `!quiz` `!stopquiz` `!pausequiz`", inline=False)
    user_embed.add_field(name="💰 Economy", value="`!coins` `!gamble` `!slots` `!blackjack` `!coinflip` `!dice` `!roulette`", inline=False)
    user_embed.add_field(name="🎲 More Games", value="`!treasurehunt` `!heist` `!crime` `!boss`", inline=False)
    user_embed.add_field(name="🛒 Shop", value="`!shop` `!buy` `!purchases`", inline=False)
    user_embed.add_field(name="👋 Greetings", value="`!gm` `!gn` `!ga` `!render`", inline=False)
    user_embed.add_field(name="ℹ️ Info", value="`!ping` `!serverinfo` `!userinfo` `!avatar`", inline=False)
    await ctx.reply(embed=user_embed)
    
    if is_admin(ctx.author):
        admin_embed = discord.Embed(title="🔧 Admin Commands", color=discord.Color.red())
        admin_embed.add_field(name="⚔️ Mod", value="`!ban` `!kick` `!warn` `!mute` `!unmute` `!clear` `!modlogs`", inline=False)
        admin_embed.add_field(name="💵 Economy", value="`!editcoins` `!shopadd` `!editshop`", inline=False)
        admin_embed.add_field(name="📍 Setup", value="`!thischannelwelcome` `!thischannelminigame` `!thischannelgamble` `!thischannelreaction` `!editchannelreaction`", inline=False)
        admin_embed.add_field(name="🖼️ Images", value="`!gmimage` `!gmimagedelete` etc.", inline=False)
        admin_embed.add_field(name="⚙️ Custom", value="`!addcmd` `!delcmd` `!cmdlist`", inline=False)
        await ctx.send(embed=admin_embed)

# ==================== FUN COMMANDS ====================
@bot.command()
async def ping(ctx):
    await ctx.reply(f"🏓 Pong! {round(bot.latency*1000)}ms")

@bot.command()
async def meme(ctx):
    data = await fetch_api("https://meme-api.com/gimme/memes")
    if data and data.get("url"):
        embed = discord.Embed(title=data.get("title","Meme"), color=discord.Color.blue())
        embed.set_image(url=data["url"])
        await ctx.reply(embed=embed)
    else:
        await ctx.reply("❌ Could not fetch meme")

@bot.command()
async def dadjoke(ctx):
    for sub in ["dadjokes", "cleanjokes", "puns"]:
        data = await fetch_api(f"https://meme-api.com/gimme/{sub}")
        if data and data.get("url"):
            embed = discord.Embed(title="😂 Dad Joke", color=discord.Color.orange())
            embed.set_image(url=data["url"])
            await ctx.reply(embed=embed)
            return
    await ctx.reply("❌ Could not fetch joke")

@bot.command()
async def jokeoftheday(ctx):
    for sub in ["memes", "funny", "jokes"]:
        data = await fetch_api(f"https://meme-api.com/gimme/{sub}")
        if data and data.get("url"):
            embed = discord.Embed(title="😆 Joke of the Day", color=discord.Color.purple())
            embed.set_image(url=data["url"])
            await ctx.reply(embed=embed)
            return
    await ctx.reply("❌ Could not fetch joke")

@bot.command()
async def dirtyjoke(ctx):
    data = await fetch_api("https://v2.jokeapi.dev/joke/Dark?type=twopart")
    if data:
        text = f"**{data.get('setup','')}**\n\n||{data.get('delivery','')}||" if data.get("type") == "twopart" else data.get("joke","")
        if text:
            embed = discord.Embed(title="🔞 Dirty Joke", description=text, color=discord.Color.dark_red())
            await ctx.reply(embed=embed)
            return
    await ctx.reply("❌ Could not fetch joke")

@bot.command(name="8ball")
async def eightball(ctx, *, question=None):
    if not question:
        return await ctx.reply("❌ Ask a question!")
    answers = ["Yes", "No", "Maybe", "Definitely", "Absolutely not", "Ask again later"]
    embed = discord.Embed(color=discord.Color.purple())
    embed.add_field(name="Question", value=question, inline=False)
    embed.add_field(name="🎱 Answer", value=random.choice(answers), inline=False)
    await ctx.reply(embed=embed)

@bot.command()
async def quote(ctx):
    quotes = [
        ("The only way to do great work is to love what you do.", "Steve Jobs"),
        ("Stay hungry, stay foolish.", "Steve Jobs"),
        ("Believe you can and you're halfway there.", "Theodore Roosevelt"),
    ]
    q, a = random.choice(quotes)
    await ctx.reply(f"💬 *\"{q}\"*\n— **{a}**")

@bot.command()
async def roast(ctx, member: discord.Member = None):
    member = member or ctx.author
    roasts = [
        f"{member.mention}, you're the reason the gene pool needs a lifeguard.",
        f"{member.mention}, I'd agree with you but then we'd both be wrong.",
        f"{member.mention}, you bring everyone joy... when you leave.",
    ]
    await ctx.reply(f"🔥 {random.choice(roasts)}")

@bot.command()
async def serverinfo(ctx):
    g = ctx.guild
    embed = discord.Embed(title=g.name, color=discord.Color.blurple())
    if g.icon: embed.set_thumbnail(url=g.icon.url)
    embed.add_field(name="Members", value=g.member_count)
    embed.add_field(name="Channels", value=len(g.channels))
    embed.add_field(name="Roles", value=len(g.roles))
    await ctx.reply(embed=embed)

@bot.command()
async def userinfo(ctx, member: discord.Member = None):
    m = member or ctx.author
    embed = discord.Embed(title=str(m), color=m.color)
    embed.set_thumbnail(url=m.display_avatar.url)
    embed.add_field(name="ID", value=m.id)
    embed.add_field(name="Joined", value=m.joined_at.strftime("%Y-%m-%d") if m.joined_at else "?")
    await ctx.reply(embed=embed)

@bot.command()
async def avatar(ctx, member: discord.Member = None):
    m = member or ctx.author
    embed = discord.Embed(title=f"{m.name}'s Avatar", color=m.color)
    embed.set_image(url=m.display_avatar.url)
    await ctx.reply(embed=embed)

# ==================== GAMES ====================
@bot.command()
@commands.has_permissions(manage_guild=True)
async def thischannelminigame(ctx):
    await set_guild_setting(ctx.guild.id, "minigame_channel_id", ctx.channel.id)
    await ctx.reply(f"✅ Minigame channel set!")

@bot.command()
async def minigame(ctx):
    mg_ch = await get_channel_setting(ctx.guild.id, "minigame_channel_id")
    if not mg_ch:
        return await ctx.reply("❌ Minigame not set up! Admin: `!thischannelminigame`")
    if ctx.channel.id != mg_ch and not is_admin(ctx.author):
        return await ctx.reply(f"❌ Use in <#{mg_ch}>!")
    
    if ctx.author.id in active_games:
        return await ctx.reply("❌ Finish your current game!")
    
    data = await fetch_api("https://opentdb.com/api.php?amount=1&type=multiple")
    if not data or not data.get("results"):
        return await ctx.reply("❌ Could not fetch question")
    
    q = data["results"][0]
    question = html.unescape(q["question"])
    correct = html.unescape(q["correct_answer"])
    options = [html.unescape(o) for o in q["incorrect_answers"]] + [correct]
    random.shuffle(options)
    
    active_games[ctx.author.id] = correct
    
    embed = discord.Embed(title="🎮 Minigame!", description=question, color=discord.Color.gold())
    for i, opt in enumerate(options, 1):
        embed.add_field(name=f"{i}", value=opt, inline=False)
    embed.set_footer(text="Type 1-4 to answer!")
    await ctx.reply(embed=embed)
    
    def check(m):
        return m.author == ctx.author and m.channel == ctx.channel and m.content in ["1","2","3","4"]
    
    try:
        msg = await bot.wait_for("message", check=check, timeout=30)
        answer = options[int(msg.content)-1]
        del active_games[ctx.author.id]
        if answer == correct:
            await ctx.reply("🎉 **CORRECT!**")
        else:
            await ctx.reply(f"❌ Wrong! Answer: **{correct}**")
    except asyncio.TimeoutError:
        if ctx.author.id in active_games: del active_games[ctx.author.id]
        await ctx.reply(f"⏰ Time's up! Answer: **{correct}**")

@bot.command()
async def quiz(ctx):
    mg_ch = await get_channel_setting(ctx.guild.id, "minigame_channel_id")
    if not mg_ch:
        return await ctx.reply("❌ Minigame not set up! Admin: `!thischannelminigame`")
    if ctx.channel.id != mg_ch and not is_admin(ctx.author):
        return await ctx.reply(f"❌ Use in <#{mg_ch}>!")
    
    if ctx.author.id in active_quizzes:
        return await ctx.reply("❌ You have an active quiz!")
    
    data = await fetch_api("https://opentdb.com/api.php?amount=5&type=multiple")
    if not data or not data.get("results"):
        return await ctx.reply("❌ Could not fetch questions")
    
    active_quizzes[ctx.author.id] = {"stopped": False}
    score = 0
    await ctx.reply("📝 **Quiz! 5 questions. Type `stop` to end.**")
    
    for i, q in enumerate(data["results"], 1):
        if active_quizzes.get(ctx.author.id, {}).get("stopped"):
            break
        
        question = html.unescape(q["question"])
        correct = html.unescape(q["correct_answer"])
        options = [html.unescape(o) for o in q["incorrect_answers"]] + [correct]
        random.shuffle(options)
        
        embed = discord.Embed(title=f"Q{i}/5", description=question, color=discord.Color.blue())
        for j, opt in enumerate(options, 1):
            embed.add_field(name=f"{j}", value=opt, inline=False)
        await ctx.send(embed=embed)
        
        def check(m):
            return m.author == ctx.author and m.channel == ctx.channel and m.content.lower() in ["1","2","3","4","stop"]
        
        try:
            msg = await bot.wait_for("message", check=check, timeout=15)
            if msg.content.lower() == "stop":
                active_quizzes[ctx.author.id]["stopped"] = True
                await ctx.send("🛑 Stopped!")
                break
            if options[int(msg.content)-1] == correct:
                score += 1
                await ctx.send("✅ Correct!")
            else:
                await ctx.send(f"❌ Wrong! It was: **{correct}**")
        except asyncio.TimeoutError:
            await ctx.send(f"⏰ Time's up! It was: **{correct}**")
    
    if ctx.author.id in active_quizzes: del active_quizzes[ctx.author.id]
    await ctx.reply(f"🏆 Score: **{score}/5**")

@bot.command()
async def stopquiz(ctx):
    if ctx.author.id in active_quizzes:
        active_quizzes[ctx.author.id]["stopped"] = True
        await ctx.reply("🛑 Quiz stopped!")
    else:
        await ctx.reply("❌ No active quiz!")

@bot.command()
async def pausequiz(ctx):
    await ctx.reply("⏸️ Type `stop` during quiz to end it!")

# ==================== ECONOMY ====================
@bot.command()
async def coins(ctx, member: discord.Member = None):
    m = member or ctx.author
    c = await get_coins(ctx.guild.id, m.id)
    embed = discord.Embed(title="💰 Balance", description=f"{m.mention}: **{c:,}** coins", color=discord.Color.gold())
    await ctx.reply(embed=embed)

@bot.command()
@commands.has_permissions(manage_guild=True)
async def editcoins(ctx, member: discord.Member, amount: str):
    current = await get_coins(ctx.guild.id, member.id)
    if amount.startswith("+"): new = current + int(amount[1:])
    elif amount.startswith("-"): new = max(0, current - int(amount[1:]))
    else: new = int(amount)
    await set_coins(ctx.guild.id, member.id, new)
    await ctx.reply(f"✅ {member.mention}: {current:,} → {new:,}")

@bot.command()
@commands.has_permissions(manage_guild=True)
async def thischannelgamble(ctx):
    await set_guild_setting(ctx.guild.id, "gambling_channel_id", ctx.channel.id)
    await ctx.reply(f"🎰 Gambling channel set!")

@bot.command()
@commands.has_permissions(manage_guild=True)
async def thischannelreaction(ctx, *emotes):
    if not emotes:
        return await ctx.reply("❌ Usage: `!thischannelreaction 😀 🎉 👍 ...` (add as many emotes as you want!)")
    # Store emotes as-is (works for unicode and custom emotes)
    emote_str = ",".join(emotes)
    await set_guild_setting(ctx.guild.id, "reaction_channel_id", ctx.channel.id)
    await set_guild_setting(ctx.guild.id, "reaction_emotes", emote_str)
    await ctx.reply(f"✅ Reaction channel set! Will react with {len(emotes)} emote(s): {' '.join(emotes)}")

@bot.command()
@commands.has_permissions(manage_guild=True)
async def editchannelreaction(ctx, *emotes):
    if not emotes:
        return await ctx.reply("❌ Usage: `!editchannelreaction 😀 🎉 👍 ...` (add as many emotes as you want!)")
    emote_str = ",".join(emotes)
    await set_guild_setting(ctx.guild.id, "reaction_emotes", emote_str)
    await ctx.reply(f"✅ Reactions updated to {len(emotes)} emote(s): {' '.join(emotes)}")

async def check_gamble_channel(ctx):
    ch = await get_channel_setting(ctx.guild.id, "gambling_channel_id")
    if not ch:
        return False, None  # Not set up
    if ctx.channel.id != ch and not is_admin(ctx.author):
        return False, ch  # Wrong channel
    return True, ch  # OK

@bot.command()
async def gamble(ctx, amount: int = None):
    ok, ch = await check_gamble_channel(ctx)
    if not ok:
        if not ch:
            return await ctx.reply("❌ Gambling not set up! Admin: `!thischannelgamble`")
        return await ctx.reply(f"❌ Use in <#{ch}>!")
    if not amount or amount < 1: return await ctx.reply("❌ Usage: !gamble 100")
    current = await get_coins(ctx.guild.id, ctx.author.id)
    if current < amount: return await ctx.reply(f"❌ You have {current:,} coins")
    
    won = random.choice([True, False])
    new = await add_coins(ctx.guild.id, ctx.author.id, amount if won else -amount)
    embed = discord.Embed(
        title="🎰 WON!" if won else "💸 LOST!",
        description=f"{'+' if won else '-'}{amount:,} coins\nBalance: {new:,}",
        color=discord.Color.green() if won else discord.Color.red()
    )
    await ctx.reply(embed=embed)

@bot.command()
async def treasurehunt(ctx):
    ok, ch = await check_gamble_channel(ctx)
    if not ok:
        if not ch:
            return await ctx.reply("❌ Gambling not set up! Admin: `!thischannelgamble`")
        return await ctx.reply(f"❌ Use in <#{ch}>!")
    cost = 50
    current = await get_coins(ctx.guild.id, ctx.author.id)
    if current < cost: return await ctx.reply(f"❌ Need {cost} coins!")
    await add_coins(ctx.guild.id, ctx.author.id, -cost)
    reward = random.randint(0, 200)
    new = await add_coins(ctx.guild.id, ctx.author.id, reward)
    await ctx.reply(f"🗺️ Found {reward} coins! (Profit: {reward-cost:+}) Balance: {new:,}")

@bot.command()
async def heist(ctx):
    ok, ch = await check_gamble_channel(ctx)
    if not ok:
        if not ch:
            return await ctx.reply("❌ Gambling not set up! Admin: `!thischannelgamble`")
        return await ctx.reply(f"❌ Use in <#{ch}>!")
    cost = 100
    current = await get_coins(ctx.guild.id, ctx.author.id)
    if current < cost: return await ctx.reply(f"❌ Need {cost} coins!")
    await add_coins(ctx.guild.id, ctx.author.id, -cost)
    reward = random.randint(0, 500)
    new = await add_coins(ctx.guild.id, ctx.author.id, reward)
    await ctx.reply(f"🏦 Stole {reward} coins! (Profit: {reward-cost:+}) Balance: {new:,}")

@bot.command()
async def crime(ctx):
    ok, ch = await check_gamble_channel(ctx)
    if not ok:
        if not ch:
            return await ctx.reply("❌ Gambling not set up! Admin: `!thischannelgamble`")
        return await ctx.reply(f"❌ Use in <#{ch}>!")
    cost = 25
    current = await get_coins(ctx.guild.id, ctx.author.id)
    if current < cost: return await ctx.reply(f"❌ Need {cost} coins!")
    await add_coins(ctx.guild.id, ctx.author.id, -cost)
    reward = random.randint(0, 100)
    new = await add_coins(ctx.guild.id, ctx.author.id, reward)
    await ctx.reply(f"🔪 Got {reward} coins! (Profit: {reward-cost:+}) Balance: {new:,}")

# ==================== CASINO GAMES ====================
@bot.command()
async def slots(ctx, bet: int = None):
    ok, ch = await check_gamble_channel(ctx)
    if not ok:
        if not ch: return await ctx.reply("❌ Gambling not set up! Admin: `!thischannelgamble`")
        return await ctx.reply(f"❌ Use in <#{ch}>!")
    if not bet or bet < 10: return await ctx.reply("❌ Min bet: 10 coins")
    current = await get_coins(ctx.guild.id, ctx.author.id)
    if current < bet: return await ctx.reply(f"❌ You have {current:,} coins")
    
    symbols = ["🍒", "🍋", "🍊", "🍇", "💎", "7️⃣", "🔔"]
    weights = [30, 25, 20, 15, 5, 3, 2]
    reels = [random.choices(symbols, weights)[0] for _ in range(3)]
    
    if reels[0] == reels[1] == reels[2]:
        if reels[0] == "7️⃣": mult = 10
        elif reels[0] == "💎": mult = 7
        elif reels[0] == "🔔": mult = 5
        else: mult = 3
        winnings = bet * mult
        result = f"🎰 **JACKPOT!** x{mult}"
        color = discord.Color.gold()
    elif reels[0] == reels[1] or reels[1] == reels[2]:
        winnings = bet
        result = "🎰 Two match! x1"
        color = discord.Color.green()
    else:
        winnings = -bet
        result = "🎰 No match..."
        color = discord.Color.red()
    
    new = await add_coins(ctx.guild.id, ctx.author.id, winnings)
    embed = discord.Embed(title=f"[ {reels[0]} | {reels[1]} | {reels[2]} ]", description=f"{result}\n{'+' if winnings > 0 else ''}{winnings:,} coins\nBalance: {new:,}", color=color)
    await ctx.reply(embed=embed)

@bot.command()
async def coinflip(ctx, choice: str = None, bet: int = None):
    ok, ch = await check_gamble_channel(ctx)
    if not ok:
        if not ch: return await ctx.reply("❌ Gambling not set up! Admin: `!thischannelgamble`")
        return await ctx.reply(f"❌ Use in <#{ch}>!")
    if not choice or choice.lower() not in ["heads", "tails", "h", "t"]:
        return await ctx.reply("❌ Usage: `!coinflip heads 100` or `!coinflip tails 100`")
    if not bet or bet < 1: return await ctx.reply("❌ Enter a bet amount!")
    current = await get_coins(ctx.guild.id, ctx.author.id)
    if current < bet: return await ctx.reply(f"❌ You have {current:,} coins")
    
    choice = "heads" if choice.lower() in ["heads", "h"] else "tails"
    result = random.choice(["heads", "tails"])
    won = choice == result
    emoji = "🪙" if result == "heads" else "⭕"
    
    new = await add_coins(ctx.guild.id, ctx.author.id, bet if won else -bet)
    embed = discord.Embed(
        title=f"{emoji} {result.upper()}!",
        description=f"You chose: {choice}\n{'✅ WON' if won else '❌ LOST'} {bet:,} coins\nBalance: {new:,}",
        color=discord.Color.green() if won else discord.Color.red()
    )
    await ctx.reply(embed=embed)

@bot.command()
async def dice(ctx, bet: int = None):
    ok, ch = await check_gamble_channel(ctx)
    if not ok:
        if not ch: return await ctx.reply("❌ Gambling not set up! Admin: `!thischannelgamble`")
        return await ctx.reply(f"❌ Use in <#{ch}>!")
    if not bet or bet < 10: return await ctx.reply("❌ Min bet: 10 coins")
    current = await get_coins(ctx.guild.id, ctx.author.id)
    if current < bet: return await ctx.reply(f"❌ You have {current:,} coins")
    
    player = [random.randint(1, 6), random.randint(1, 6)]
    dealer = [random.randint(1, 6), random.randint(1, 6)]
    p_total, d_total = sum(player), sum(dealer)
    
    dice_emoji = ["⚀", "⚁", "⚂", "⚃", "⚄", "⚅"]
    p_str = f"{dice_emoji[player[0]-1]} {dice_emoji[player[1]-1]} = {p_total}"
    d_str = f"{dice_emoji[dealer[0]-1]} {dice_emoji[dealer[1]-1]} = {d_total}"
    
    if p_total > d_total:
        winnings = bet
        result = "🎲 You WIN!"
        color = discord.Color.green()
    elif p_total < d_total:
        winnings = -bet
        result = "🎲 Dealer wins..."
        color = discord.Color.red()
    else:
        winnings = 0
        result = "🎲 TIE! Bet returned"
        color = discord.Color.gold()
    
    new = await add_coins(ctx.guild.id, ctx.author.id, winnings)
    embed = discord.Embed(title=result, color=color)
    embed.add_field(name="Your Roll", value=p_str, inline=True)
    embed.add_field(name="Dealer Roll", value=d_str, inline=True)
    embed.add_field(name="Result", value=f"{'+' if winnings > 0 else ''}{winnings:,} coins\nBalance: {new:,}", inline=False)
    await ctx.reply(embed=embed)

@bot.command()
async def roulette(ctx, choice: str = None, bet: int = None):
    ok, ch = await check_gamble_channel(ctx)
    if not ok:
        if not ch: return await ctx.reply("❌ Gambling not set up! Admin: `!thischannelgamble`")
        return await ctx.reply(f"❌ Use in <#{ch}>!")
    if not choice: return await ctx.reply("❌ Usage: `!roulette red 100`, `!roulette black 100`, `!roulette 7 100`")
    if not bet or bet < 10: return await ctx.reply("❌ Min bet: 10 coins")
    current = await get_coins(ctx.guild.id, ctx.author.id)
    if current < bet: return await ctx.reply(f"❌ You have {current:,} coins")
    
    choice = choice.lower()
    number = random.randint(0, 36)
    red_nums = [1,3,5,7,9,12,14,16,18,19,21,23,25,27,30,32,34,36]
    color = "🟢" if number == 0 else ("🔴" if number in red_nums else "⚫")
    color_name = "green" if number == 0 else ("red" if number in red_nums else "black")
    
    winnings = 0
    if choice.isdigit() and int(choice) == number:
        winnings = bet * 35
        result = f"🎯 NUMBER {number}! x35"
    elif choice == color_name:
        winnings = bet * 2 if choice != "green" else bet * 35
        result = f"✅ {color_name.upper()}! x{'2' if choice != 'green' else '35'}"
    elif choice in ["red", "black", "green"] or choice.isdigit():
        winnings = -bet
        result = "❌ Wrong!"
    else:
        return await ctx.reply("❌ Choose: red, black, green, or a number 0-36")
    
    new = await add_coins(ctx.guild.id, ctx.author.id, winnings)
    embed = discord.Embed(title=f"{color} {number}", description=f"{result}\n{'+' if winnings > 0 else ''}{winnings:,} coins\nBalance: {new:,}", color=discord.Color.gold())
    await ctx.reply(embed=embed)

@bot.command()
async def blackjack(ctx, bet: int = None):
    ok, ch = await check_gamble_channel(ctx)
    if not ok:
        if not ch: return await ctx.reply("❌ Gambling not set up! Admin: `!thischannelgamble`")
        return await ctx.reply(f"❌ Use in <#{ch}>!")
    if not bet or bet < 10: return await ctx.reply("❌ Min bet: 10 coins")
    current = await get_coins(ctx.guild.id, ctx.author.id)
    if current < bet: return await ctx.reply(f"❌ You have {current:,} coins")
    if ctx.author.id in active_games: return await ctx.reply("❌ Finish your current game!")
    
    suits = ["♠️", "♥️", "♦️", "♣️"]
    ranks = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]
    deck = [(r, s) for s in suits for r in ranks]
    random.shuffle(deck)
    
    def card_value(hand):
        val, aces = 0, 0
        for r, s in hand:
            if r in ["J", "Q", "K"]: val += 10
            elif r == "A": val += 11; aces += 1
            else: val += int(r)
        while val > 21 and aces: val -= 10; aces -= 1
        return val
    
    def hand_str(hand): return " ".join([f"{r}{s}" for r, s in hand])
    
    player = [deck.pop(), deck.pop()]
    dealer = [deck.pop(), deck.pop()]
    active_games[ctx.author.id] = {"deck": deck, "player": player, "dealer": dealer, "bet": bet}
    
    p_val = card_value(player)
    if p_val == 21:
        del active_games[ctx.author.id]
        winnings = int(bet * 1.5)
        new = await add_coins(ctx.guild.id, ctx.author.id, winnings)
        embed = discord.Embed(title="🃏 BLACKJACK!", color=discord.Color.gold())
        embed.add_field(name="Your Hand", value=f"{hand_str(player)} ({p_val})", inline=False)
        embed.add_field(name="Winnings", value=f"+{winnings:,} coins\nBalance: {new:,}", inline=False)
        return await ctx.reply(embed=embed)
    
    embed = discord.Embed(title="🃏 Blackjack", color=discord.Color.blue())
    embed.add_field(name="Your Hand", value=f"{hand_str(player)} ({p_val})", inline=False)
    embed.add_field(name="Dealer Shows", value=f"{dealer[0][0]}{dealer[0][1]} ??", inline=False)
    embed.set_footer(text="Type 'hit' or 'stand'")
    await ctx.reply(embed=embed)
    
    def check(m): return m.author == ctx.author and m.channel == ctx.channel and m.content.lower() in ["hit", "stand", "h", "s"]
    
    while True:
        try:
            msg = await bot.wait_for("message", check=check, timeout=60)
            game = active_games.get(ctx.author.id)
            if not game: return
            
            if msg.content.lower() in ["hit", "h"]:
                game["player"].append(game["deck"].pop())
                p_val = card_value(game["player"])
                if p_val > 21:
                    del active_games[ctx.author.id]
                    new = await add_coins(ctx.guild.id, ctx.author.id, -bet)
                    embed = discord.Embed(title="💥 BUST!", color=discord.Color.red())
                    embed.add_field(name="Your Hand", value=f"{hand_str(game['player'])} ({p_val})", inline=False)
                    embed.add_field(name="Lost", value=f"-{bet:,} coins\nBalance: {new:,}", inline=False)
                    return await ctx.reply(embed=embed)
                embed = discord.Embed(title="🃏 Blackjack", color=discord.Color.blue())
                embed.add_field(name="Your Hand", value=f"{hand_str(game['player'])} ({p_val})", inline=False)
                embed.add_field(name="Dealer Shows", value=f"{dealer[0][0]}{dealer[0][1]} ??", inline=False)
                embed.set_footer(text="Type 'hit' or 'stand'")
                await ctx.reply(embed=embed)
            else:
                del active_games[ctx.author.id]
                d_val = card_value(game["dealer"])
                while d_val < 17:
                    game["dealer"].append(game["deck"].pop())
                    d_val = card_value(game["dealer"])
                
                p_val = card_value(game["player"])
                if d_val > 21 or p_val > d_val:
                    winnings = bet
                    title, color = "🎉 YOU WIN!", discord.Color.green()
                elif p_val < d_val:
                    winnings = -bet
                    title, color = "😢 Dealer Wins", discord.Color.red()
                else:
                    winnings = 0
                    title, color = "🤝 Push (Tie)", discord.Color.gold()
                
                new = await add_coins(ctx.guild.id, ctx.author.id, winnings)
                embed = discord.Embed(title=title, color=color)
                embed.add_field(name="Your Hand", value=f"{hand_str(game['player'])} ({p_val})", inline=True)
                embed.add_field(name="Dealer Hand", value=f"{hand_str(game['dealer'])} ({d_val})", inline=True)
                embed.add_field(name="Result", value=f"{'+' if winnings > 0 else ''}{winnings:,} coins\nBalance: {new:,}", inline=False)
                return await ctx.reply(embed=embed)
        except asyncio.TimeoutError:
            if ctx.author.id in active_games: del active_games[ctx.author.id]
            new = await add_coins(ctx.guild.id, ctx.author.id, -bet)
            return await ctx.reply(f"⏰ Timed out! Lost {bet:,} coins. Balance: {new:,}")

@bot.command()
async def boss(ctx):
    gid, uid = ctx.guild.id, ctx.author.id
    today = datetime.now().date()
    
    if gid not in boss_battles or boss_battles[gid]["date"] < today:
        boss_battles[gid] = {"hp": 10000, "max": 10000, "date": today, "players": set()}
    
    boss = boss_battles[gid]
    if boss["hp"] <= 0:
        return await ctx.reply("💀 Boss defeated! Come back tomorrow!")
    
    key = f"{gid}_{uid}"
    if key in last_attack:
        elapsed = (datetime.now() - last_attack[key]).total_seconds()
        if elapsed < 600:
            return await ctx.reply(f"⏰ Wait {int((600-elapsed)/60)}m {int((600-elapsed)%60)}s")
    
    damage = random.randint(100, 300)
    boss["hp"] -= damage
    boss["players"].add(uid)
    last_attack[key] = datetime.now()
    
    if boss["hp"] <= 0:
        boss["hp"] = 0
        for pid in boss["players"]:
            await add_coins(gid, pid, 500)
        await ctx.reply(f"🏆 **BOSS DEFEATED!** Final blow: {ctx.author.mention}! Everyone gets 500 coins!")
    else:
        await ctx.reply(f"⚔️ Hit for {damage}! Boss: {boss['hp']:,}/{boss['max']:,} HP")

# ==================== SHOP ====================
@bot.command()
async def shop(ctx):
    items = await get_shop_items(ctx.guild.id)
    if not items:
        return await ctx.reply("🏪 Shop empty! Admin: `!shopadd name price`")
    embed = discord.Embed(title="🏪 Shop", color=discord.Color.blue())
    for iid, name, price in items:
        embed.add_field(name=f"#{iid} {name}", value=f"💰 {price:,}", inline=False)
    await ctx.reply(embed=embed)

@bot.command()
@commands.has_permissions(manage_guild=True)
async def shopadd(ctx, name: str, price: int):
    next_id = await add_shop_item(ctx.guild.id, name.replace("_"," "), price)
    await ctx.reply(f"✅ Added #{next_id} {name} for {price:,}")

@bot.command()
@commands.has_permissions(manage_guild=True)
async def editshop(ctx, item_id: int, action: str, value: str = None):
    item = await get_shop_item(ctx.guild.id, item_id)
    if not item:
        return await ctx.reply("❌ Item not found!")
    action = action.lower()
    if action == "delete":
        await delete_shop_item(ctx.guild.id, item_id)
        await ctx.reply(f"✅ Deleted #{item_id}")
    elif action == "price" and value:
        await update_shop_item(ctx.guild.id, item_id, "price", int(value))
        await ctx.reply(f"✅ Price: {item[1]} → {value}")
    elif action == "name" and value:
        await update_shop_item(ctx.guild.id, item_id, "name", value)
        await ctx.reply(f"✅ Name: {item[0]} → {value}")
    else:
        await ctx.reply("❌ Usage: `!editshop 1 delete` or `!editshop 1 price 500`")

@bot.command()
async def buy(ctx, item_id: int):
    item = await get_shop_item(ctx.guild.id, item_id)
    if not item:
        return await ctx.reply("❌ Item not found!")
    name, price = item
    current = await get_coins(ctx.guild.id, ctx.author.id)
    if current < price:
        return await ctx.reply(f"❌ Need {price:,}, you have {current:,}")
    new = await add_coins(ctx.guild.id, ctx.author.id, -price)
    await add_purchase(ctx.guild.id, ctx.author.id, name, price)
    await ctx.reply(f"✅ Bought **{name}**! Balance: {new:,}")
    if ctx.guild.owner:
        try: await ctx.guild.owner.send(f"🛒 {ctx.author} bought {name} in {ctx.guild.name}")
        except: pass

@bot.command()
async def purchases(ctx, member: discord.Member = None):
    m = member or ctx.author
    rows = await get_purchases(ctx.guild.id, m.id)
    if not rows:
        return await ctx.reply(f"📋 {m.name} has no purchases")
    embed = discord.Embed(title=f"📋 {m.name}'s Purchases", color=discord.Color.blue())
    for name, price in rows:
        embed.add_field(name=name, value=f"{price:,} coins", inline=False)
    await ctx.reply(embed=embed)

# ==================== MODERATION ====================
@bot.command()
@commands.has_permissions(ban_members=True)
async def ban(ctx, member: discord.Member = None, *, reason=None):
    if not member: return await ctx.reply("❌ Specify member!")
    if member.top_role >= ctx.author.top_role: return await ctx.reply("❌ Can't ban higher role!")
    await ctx.guild.ban(member, reason=reason)
    await ctx.reply(f"🔨 Banned {member}")

@bot.command()
@commands.has_permissions(kick_members=True)
async def kick(ctx, member: discord.Member = None, *, reason=None):
    if not member: return await ctx.reply("❌ Specify member!")
    if member.top_role >= ctx.author.top_role: return await ctx.reply("❌ Can't kick higher role!")
    await ctx.guild.kick(member, reason=reason)
    await ctx.reply(f"👢 Kicked {member}")

@bot.command()
@commands.has_permissions(manage_messages=True)
async def warn(ctx, member: discord.Member = None, *, reason=None):
    if not member or not reason: return await ctx.reply("❌ Usage: !warn @user reason")
    await ctx.reply(f"⚠️ {member.mention} warned: {reason}")

@bot.command()
@commands.has_permissions(manage_roles=True)
async def mute(ctx, member: discord.Member = None, duration: str = None):
    if not member or not duration: return await ctx.reply("❌ Usage: !mute @user 10m")
    try:
        unit = duration[-1].lower()
        secs = int(duration[:-1]) * {"s":1,"m":60,"h":3600,"d":86400}[unit]
    except: return await ctx.reply("❌ Invalid duration!")
    mute_role = discord.utils.get(ctx.guild.roles, name="Muted")
    if not mute_role:
        mute_role = await ctx.guild.create_role(name="Muted")
        for ch in ctx.guild.channels:
            await ch.set_permissions(mute_role, send_messages=False, speak=False)
    await member.add_roles(mute_role)
    await ctx.reply(f"🔇 Muted {member} for {duration}")
    await asyncio.sleep(secs)
    if mute_role in member.roles:
        await member.remove_roles(mute_role)

@bot.command()
@commands.has_permissions(manage_roles=True)
async def unmute(ctx, member: discord.Member = None):
    if not member: return await ctx.reply("❌ Specify member!")
    mute_role = discord.utils.get(ctx.guild.roles, name="Muted")
    if mute_role and mute_role in member.roles:
        await member.remove_roles(mute_role)
        await ctx.reply(f"🔊 Unmuted {member}")
    else:
        await ctx.reply("❌ Not muted!")

@bot.command()
@commands.has_permissions(manage_messages=True)
async def clear(ctx, amount: int = None):
    if not amount or amount < 1 or amount > 100: return await ctx.reply("❌ Amount 1-100!")
    deleted = await ctx.channel.purge(limit=amount+1)
    await ctx.send(f"🧹 Deleted {len(deleted)-1} messages", delete_after=3)

@bot.command()
@commands.has_permissions(manage_guild=True)
async def modlogs(ctx):
    await ctx.reply("📋 Mod logs coming soon!")

# ==================== GREETINGS ====================
@bot.command()
async def gm(ctx):
    img = get_random_image("gm")
    if img:
        await ctx.send(file=discord.File(img, "gm.png"))
    else:
        await ctx.reply("☀️ Good morning!")

@bot.command()
async def gn(ctx):
    img = get_random_image("gn")
    if img:
        await ctx.send(file=discord.File(img, "gn.png"))
    else:
        await ctx.reply("🌙 Good night!")

@bot.command()
async def ga(ctx):
    img = get_random_image("ga")
    if img:
        await ctx.send(file=discord.File(img, "ga.png"))
    else:
        await ctx.reply("🌤️ Good afternoon!")

@bot.command()
async def render(ctx):
    today = datetime.now().date()
    if ctx.author.id in daily_render and daily_render[ctx.author.id] == today:
        return await ctx.reply("❌ Already got render today!")
    img = get_random_image("render")
    if img:
        daily_render[ctx.author.id] = today
        await ctx.send(file=discord.File(img, "render.png"))
    else:
        await ctx.reply("❌ No renders! Admin: `!renderimage`")

# ==================== IMAGE MANAGEMENT ====================
@bot.command()
@commands.has_permissions(manage_guild=True)
async def thischannelwelcome(ctx):
    await set_guild_setting(ctx.guild.id, "welcome_channel_id", ctx.channel.id)
    await ctx.reply(f"✅ Welcome channel set!")

@bot.command()
@commands.has_permissions(manage_guild=True)
async def testwelcome(ctx, member: discord.Member = None):
    m = member or ctx.author
    embed = discord.Embed(
        title=f"Welcome {m.name}!",
        description=f"🎉 {m.mention} joined **{ctx.guild.name}**!\nMember #{ctx.guild.member_count}",
        color=discord.Color.green()
    )
    embed.set_thumbnail(url=m.display_avatar.url)
    img = get_random_image("welcome")
    if img:
        file = discord.File(img, "welcome.png")
        embed.set_image(url="attachment://welcome.png")
        await ctx.send(file=file, embed=embed)
    else:
        await ctx.send(embed=embed)

# GM images
@bot.command()
@commands.has_permissions(manage_guild=True)
async def gmimage(ctx):
    if not ctx.message.attachments: return await ctx.reply("❌ Attach image!")
    ok, r = await save_image(ctx.message.attachments[0], "gm")
    await ctx.reply(f"✅ Saved: {r}" if ok else f"❌ {r}")

@bot.command()
@commands.has_permissions(manage_guild=True)
async def gmimagelist(ctx):
    images = get_pool_images("gm")
    if not images: return await ctx.reply("📋 No GM images")
    await ctx.reply("☀️ GM Images:\n" + "\n".join(f"#{i}: {img}" for i, img in enumerate(images, 1)))

@bot.command()
@commands.has_permissions(manage_guild=True)
async def gmimagedelete(ctx, num: int):
    images = get_pool_images("gm")
    if not images or num < 1 or num > len(images): return await ctx.reply(f"❌ Invalid! Use 1-{len(images)}")
    os.remove(os.path.join(BASE_PATH, "gm", images[num-1]))
    await ctx.reply(f"✅ Deleted #{num}")

# GN images
@bot.command()
@commands.has_permissions(manage_guild=True)
async def gnimage(ctx):
    if not ctx.message.attachments: return await ctx.reply("❌ Attach image!")
    ok, r = await save_image(ctx.message.attachments[0], "gn")
    await ctx.reply(f"✅ Saved: {r}" if ok else f"❌ {r}")

@bot.command()
@commands.has_permissions(manage_guild=True)
async def gnimagelist(ctx):
    images = get_pool_images("gn")
    if not images: return await ctx.reply("📋 No GN images")
    await ctx.reply("🌙 GN Images:\n" + "\n".join(f"#{i}: {img}" for i, img in enumerate(images, 1)))

@bot.command()
@commands.has_permissions(manage_guild=True)
async def gnimagedelete(ctx, num: int):
    images = get_pool_images("gn")
    if not images or num < 1 or num > len(images): return await ctx.reply(f"❌ Invalid! Use 1-{len(images)}")
    os.remove(os.path.join(BASE_PATH, "gn", images[num-1]))
    await ctx.reply(f"✅ Deleted #{num}")

# GA images
@bot.command()
@commands.has_permissions(manage_guild=True)
async def gaimage(ctx):
    if not ctx.message.attachments: return await ctx.reply("❌ Attach image!")
    ok, r = await save_image(ctx.message.attachments[0], "ga")
    await ctx.reply(f"✅ Saved: {r}" if ok else f"❌ {r}")

@bot.command()
@commands.has_permissions(manage_guild=True)
async def gaimagelist(ctx):
    images = get_pool_images("ga")
    if not images: return await ctx.reply("📋 No GA images")
    await ctx.reply("🌤️ GA Images:\n" + "\n".join(f"#{i}: {img}" for i, img in enumerate(images, 1)))

@bot.command()
@commands.has_permissions(manage_guild=True)
async def gaimagedelete(ctx, num: int):
    images = get_pool_images("ga")
    if not images or num < 1 or num > len(images): return await ctx.reply(f"❌ Invalid! Use 1-{len(images)}")
    os.remove(os.path.join(BASE_PATH, "ga", images[num-1]))
    await ctx.reply(f"✅ Deleted #{num}")

# Render images
@bot.command()
@commands.has_permissions(manage_guild=True)
async def renderimage(ctx):
    if not ctx.message.attachments: return await ctx.reply("❌ Attach image!")
    ok, r = await save_image(ctx.message.attachments[0], "render")
    await ctx.reply(f"✅ Saved: {r}" if ok else f"❌ {r}")

@bot.command()
@commands.has_permissions(manage_guild=True)
async def renderimagelist(ctx):
    images = get_pool_images("render")
    if not images: return await ctx.reply("📋 No render images")
    await ctx.reply("🎨 Render Images:\n" + "\n".join(f"#{i}: {img}" for i, img in enumerate(images, 1)))

@bot.command()
@commands.has_permissions(manage_guild=True)
async def renderimagedelete(ctx, num: int):
    images = get_pool_images("render")
    if not images or num < 1 or num > len(images): return await ctx.reply(f"❌ Invalid! Use 1-{len(images)}")
    os.remove(os.path.join(BASE_PATH, "render", images[num-1]))
    await ctx.reply(f"✅ Deleted #{num}")

# Welcome images
@bot.command()
@commands.has_permissions(manage_guild=True)
async def welcomeimage(ctx):
    if not ctx.message.attachments: return await ctx.reply("❌ Attach image!")
    ok, r = await save_image(ctx.message.attachments[0], "welcome")
    await ctx.reply(f"✅ Saved: {r}" if ok else f"❌ {r}")

@bot.command()
@commands.has_permissions(manage_guild=True)
async def welcomeimagelist(ctx):
    images = get_pool_images("welcome")
    if not images: return await ctx.reply("📋 No welcome images")
    await ctx.reply("🎉 Welcome Images:\n" + "\n".join(f"#{i}: {img}" for i, img in enumerate(images, 1)))

@bot.command()
@commands.has_permissions(manage_guild=True)
async def welcomeimagedelete(ctx, num: int):
    images = get_pool_images("welcome")
    if not images or num < 1 or num > len(images): return await ctx.reply(f"❌ Invalid! Use 1-{len(images)}")
    os.remove(os.path.join(BASE_PATH, "welcome", images[num-1]))
    await ctx.reply(f"✅ Deleted #{num}")

# ==================== CUSTOM COMMANDS ====================
@bot.command()
@commands.has_permissions(manage_guild=True)
async def addcmd(ctx, name: str, *, response: str):
    name = name.lower().strip()
    await set_custom_command(ctx.guild.id, name, response)
    await ctx.reply(f"✅ Added `!{name}`")

@bot.command()
@commands.has_permissions(manage_guild=True)
async def delcmd(ctx, name: str):
    deleted = await delete_custom_command(ctx.guild.id, name.lower())
    if not deleted: return await ctx.reply("❌ Not found!")
    await ctx.reply(f"✅ Deleted `!{name}`")

@bot.command()
async def cmdlist(ctx):
    names = await get_custom_commands_list(ctx.guild.id)
    if not names: return await ctx.reply("📋 No custom commands")
    await ctx.reply("📋 Custom: " + ", ".join(f"`!{n}`" for n in names))

@bot.command()
async def shutdown(ctx):
    if ctx.author.id != ctx.guild.owner_id:
        return await ctx.reply("❌ Owner only!")
    await ctx.reply("👋 Shutting down...")
    await bot.close()

# ==================== RUN ====================
if __name__ == "__main__":
    if not BOT_TOKEN:
        print("❌ No BOT_TOKEN!")
        exit(1)
    print("🚀 Starting bot...")
    bot.run(BOT_TOKEN)
