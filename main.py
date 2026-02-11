import os
import discord
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv
import asyncio
import sqlite3
import json
from datetime import datetime, timezone, timedelta
import random

# === НАСТРОЙКИ ===
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise RuntimeError("❌ Файл .env должен содержать DISCORD_TOKEN=ваш_токен")
OWNER_ID = 930371869176127568  # ← ЗАМЕНИТЕ НА ВАШ ID

# === ID РОЛЕЙ И КАНАЛОВ ===
OWNER_ROLE_ID = 1470473505702281265
DEP_OWNER_ROLE_ID = 1470473509015781416
HIGH_RANG_ROLE_ID = 1470473522873634858
RECRUIT_ROLE_ID = 1470473526556491861
MAIN_ROLE_ID = 1470473528955371763
NEWBIE_ROLE_ID = 1470473532327854173
COMMON_ROLE_ID = 1470473534999494827
THREADS_CHANNEL_ID = 1470473650338660352
LOG_CHANNEL_ID = 1470473620336935034

FAMILY_ROLES = {
    "owner": OWNER_ROLE_ID,
    "dep_owner": DEP_OWNER_ROLE_ID,
    "high_rang": HIGH_RANG_ROLE_ID,
    "recruit": RECRUIT_ROLE_ID,
    "main": MAIN_ROLE_ID,
    "newbie": NEWBIE_ROLE_ID,
    "common": COMMON_ROLE_ID
}

MANAGE_APPLICATIONS_ROLES = [
    FAMILY_ROLES["recruit"],
    FAMILY_ROLES["high_rang"],
    FAMILY_ROLES["dep_owner"],
    FAMILY_ROLES["owner"]
]

# === НАСТРОЙКА БОТА ===
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.voice_states = True
intents.presences = True
intents.guilds = True
bot = commands.Bot(command_prefix="!", intents=intents)

# === ГЛОБАЛЬНОЕ СОЕДИНЕНИЕ С БД ===
_db_conn = None

def get_db_connection():
    global _db_conn
    if _db_conn is None:
        _db_conn = sqlite3.connect("voice_data.db", check_same_thread=False)
        _db_conn.row_factory = sqlite3.Row
    return _db_conn

def init_db():
    os.makedirs("backups", exist_ok=True)
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS voice_sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        channel_id INTEGER NOT NULL,
        start_time TEXT NOT NULL,
        end_time TEXT
    )
    ''')
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS family_blacklist (
        user_id INTEGER PRIMARY KEY,
        reason TEXT NOT NULL,
        added_by INTEGER NOT NULL,
        added_at TEXT NOT NULL
    )
    ''')
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS applications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        submitted_at TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending'
    )
    ''')
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS profiles (
        user_id INTEGER PRIMARY KEY,
        nickname TEXT,
        static_id TEXT
    )
    ''')
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS casino_balance (
        user_id INTEGER PRIMARY KEY,
        balance INTEGER NOT NULL DEFAULT 10000
    )
    ''')
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS work_timer (
        user_id INTEGER PRIMARY KEY,
        last_work TEXT
    )
    ''')
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS casino_ban (
        user_id INTEGER PRIMARY KEY
    )
    ''')
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS white_list (
        user_id INTEGER PRIMARY KEY
    )
    ''')
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS security_violations (
        user_id INTEGER PRIMARY KEY,
        strikes INTEGER NOT NULL DEFAULT 0
    )
    ''')
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS user_threads (
        user_id INTEGER PRIMARY KEY,
        thread_url TEXT NOT NULL
    )
    ''')
    conn.commit()

# === ФУНКЦИИ ДЛЯ РАБОТЫ С БД (используют одно соединение) ===
def get_balance(user_id: int) -> int:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT balance FROM casino_balance WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    if result is None:
        cursor.execute("INSERT INTO casino_balance (user_id, balance) VALUES (?, 10000)", (user_id,))
        conn.commit()
        return 10000
    return result[0]

def set_balance(user_id: int, amount: int):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO casino_balance (user_id, balance) VALUES (?, ?)", (user_id, max(0, amount)))
    conn.commit()

def is_casino_banned(user_id: int) -> bool:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM casino_ban WHERE user_id = ?", (user_id,))
    return cursor.fetchone() is not None

def ban_from_casino(user_id: int):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO casino_ban (user_id) VALUES (?)", (user_id,))
    conn.commit()

def unban_from_casino(user_id: int):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM casino_ban WHERE user_id = ?", (user_id,))
    conn.commit()

def can_work(user_id: int) -> bool:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT last_work FROM work_timer WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    if not result:
        return True
    last_work = datetime.fromisoformat(result[0].replace("Z", "+00:00"))
    return datetime.now(timezone.utc) - last_work > timedelta(minutes=5)

def update_work_time(user_id: int):
    conn = get_db_connection()
    cursor = conn.cursor()
    now = datetime.now(timezone.utc).isoformat()
    cursor.execute("INSERT OR REPLACE INTO work_timer (user_id, last_work) VALUES (?, ?)", (user_id, now))
    conn.commit()

def add_voice_session(user_id: int, channel_id: int, start_time: datetime):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO voice_sessions (user_id, channel_id, start_time, end_time) VALUES (?, ?, ?, ?)",
        (user_id, channel_id, start_time.isoformat(), None)
    )
    conn.commit()

def end_voice_session(user_id: int, end_time: datetime):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE voice_sessions SET end_time = ? WHERE user_id = ? AND end_time IS NULL",
        (end_time.isoformat(), user_id)
    )
    conn.commit()

def get_user_sessions(user_id: int):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT channel_id, start_time, end_time FROM voice_sessions WHERE user_id = ? ORDER BY start_time DESC LIMIT 20",
        (user_id,)
    )
    return cursor.fetchall()

def add_to_family_blacklist(user_id: int, reason: str, added_by: int):
    conn = get_db_connection()
    cursor = conn.cursor()
    now = datetime.now(timezone.utc).isoformat()
    cursor.execute(
        "INSERT OR REPLACE INTO family_blacklist (user_id, reason, added_by, added_at) VALUES (?, ?, ?, ?)",
        (user_id, reason, added_by, now)
    )
    conn.commit()

def remove_from_family_blacklist(user_id: int):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM family_blacklist WHERE user_id = ?", (user_id,))
    conn.commit()

def is_in_family_blacklist(user_id: int) -> bool:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM family_blacklist WHERE user_id = ?", (user_id,))
    return cursor.fetchone() is not None

def get_blacklist_reason(user_id: int) -> str:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT reason FROM family_blacklist WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    return result[0] if result else "Не указана"

def can_submit_application(user_id: int) -> bool:
    conn = get_db_connection()
    cursor = conn.cursor()
    one_day_ago = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    cursor.execute(
        "SELECT 1 FROM applications WHERE user_id = ? AND submitted_at > ?",
        (user_id, one_day_ago)
    )
    return cursor.fetchone() is None

def record_application(user_id: int):
    conn = get_db_connection()
    cursor = conn.cursor()
    now = datetime.now(timezone.utc).isoformat()
    cursor.execute(
        "INSERT INTO applications (user_id, submitted_at) VALUES (?, ?)",
        (user_id, now)
    )
    conn.commit()

def get_pending_applications_count() -> int:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM applications WHERE status = 'pending'")
    return cursor.fetchone()[0]

def get_last_application_time() -> str:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT submitted_at FROM applications ORDER BY submitted_at DESC LIMIT 1")
    result = cursor.fetchone()
    if not result:
        return "Никогда"
    dt = datetime.fromisoformat(result[0].replace("Z", "+00:00"))
    now = datetime.now(timezone.utc)
    diff = now - dt
    hours = int(diff.total_seconds() // 3600)
    if hours < 1:
        return "менее часа назад"
    elif hours == 1:
        return "1 час назад"
    else:
        return f"{hours} часов назад"

def save_profile(user_id: int, nickname: str, static_id: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR REPLACE INTO profiles (user_id, nickname, static_id) VALUES (?, ?, ?)",
        (user_id, nickname, static_id)
    )
    conn.commit()

def get_profile(user_id: int):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT nickname, static_id FROM profiles WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    return result

def get_all_family_members(guild: discord.Guild) -> list:
    members = []
    for member in guild.members:
        if member.bot:
            continue
        if any(role.id in FAMILY_ROLES.values() for role in member.roles):
            members.append(member)
    return members

def is_in_white_list(user_id: int) -> bool:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM white_list WHERE user_id = ?", (user_id,))
    return cursor.fetchone() is not None

def add_to_white_list(user_id: int):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO white_list (user_id) VALUES (?)", (user_id,))
    conn.commit()

def get_strikes(user_id: int) -> int:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT strikes FROM security_violations WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    return result[0] if result else 0

def add_strike(user_id: int):
    conn = get_db_connection()
    cursor = conn.cursor()
    current = get_strikes(user_id)
    cursor.execute("INSERT OR REPLACE INTO security_violations (user_id, strikes) VALUES (?, ?)", (user_id, current + 1))
    conn.commit()
    return current + 1

def reset_strikes(user_id: int):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM security_violations WHERE user_id = ?", (user_id,))
    conn.commit()

def save_thread_link(user_id: int, thread_id: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO user_threads (user_id, thread_url) VALUES (?, ?)", (user_id, thread_id))
    conn.commit()

def get_thread_link(user_id: int) -> str:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT thread_url FROM user_threads WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    return result[0] if result else None

# === ЛОГИРОВАНИЕ ===
async def log_action(guild, action: str, details: str, color=0x2b2d31):
    if not guild:
        return
    log_channel = guild.get_channel(LOG_CHANNEL_ID)
    if log_channel:
        embed = discord.Embed(
            title="📋 Аудит действий",
            description=f"Действие: {action}\n{details}",
            color=color,
            timestamp=discord.utils.utcnow()
        )
        try:
            await log_channel.send(embed=embed)
        except discord.Forbidden:
            pass

def has_any_role(member: discord.Member, role_ids: list) -> bool:
    if member.guild_permissions.administrator:
        return True
    return any(role.id in role_ids for role in member.roles)

# === БЭКАП ===
def backup_guild(guild: discord.Guild):
    if not guild:
        return
    data = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "guild_id": guild.id,
        "guild_name": guild.name,
        "members": []
    }
    for member in guild.members:
        if member.bot:
            continue
        roles = [role.id for role in member.roles if role.id in FAMILY_ROLES.values()]
        if roles:
            data["members"].append({
                "user_id": member.id,
                "name": member.name,
                "display_name": member.display_name,
                "roles": roles,
                "joined_at": member.joined_at.isoformat() if member.joined_at else None
            })
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
    filename = f"backups/backup_{timestamp}.json"
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    cutoff = datetime.now() - timedelta(days=30)
    for file in os.listdir("backups"):
        try:
            file_time = datetime.strptime(file.replace("backup_", "").replace(".json", ""), "%Y-%m-%d_%H-%M")
            if file_time < cutoff:
                os.remove(f"backups/{file}")
        except Exception:
            pass

# === ТАСКИ ===
async def change_status():
    while True:
        pending = get_pending_applications_count()
        activity = discord.Game(f"Заявок: {pending}")
        await bot.change_presence(activity=activity)
        await asyncio.sleep(60)

async def backup_task():
    await bot.wait_until_ready()
    while not bot.is_closed():
        for guild in bot.guilds:
            backup_guild(guild)
        await asyncio.sleep(3600)

# === СОБЫТИЯ ===
@bot.event
async def on_ready():
    print(f'✅ Бот {bot.user} запущен!')

    # ВРЕМЕННО: синхронизация при старте (удалить после теста!)
    try:
        synced = await bot.tree.sync()
        print(f'[AUTO-SYNC] Загружено {len(synced)} слэш-команд.')
    except Exception as e:
        print(f'[AUTO-SYNC ERROR] {e}')

    bot.loop.create_task(change_status())
    bot.loop.create_task(backup_task())

@bot.event
async def on_voice_state_update(member, before, after):
    if member.bot:
        return
    now = datetime.now(timezone.utc)
    if before.channel and not after.channel:
        end_voice_session(member.id, now)
    elif before.channel and after.channel and before.channel != after.channel:
        end_voice_session(member.id, now)
        add_voice_session(member.id, after.channel.id, now)
    elif not before.channel and after.channel:
        add_voice_session(member.id, after.channel.id, now)

@bot.event
async def on_member_update(before, after):
    if not after.guild:
        return
    added_roles = set(after.roles) - set(before.roles)
    if not added_roles:
        return
    family_role_ids = set(FAMILY_ROLES.values())
    given_family_roles = [r for r in added_roles if r.id in family_role_ids]
    if not given_family_roles or not is_in_family_blacklist(after.id):
        return

    await after.remove_roles(*given_family_roles)

    issuer = None
    try:
        async for entry in after.guild.audit_logs(action=discord.AuditLogAction.member_role_update, limit=10):
            if entry.target.id == after.id and any(r.id in family_role_ids for r in getattr(entry.after, 'roles', [])):
                issuer = entry.user
                break
    except Exception:
        pass

    issuer_roles_to_remove = []
    if issuer and issuer != bot.user and issuer != after:
        issuer_roles_to_remove = [r for r in issuer.roles if r.id in family_role_ids]
        if issuer_roles_to_remove:
            await issuer.remove_roles(*issuer_roles_to_remove)

    reason = get_blacklist_reason(after.id)
    details = f"Участник: {after.mention} (ID: {after.id})\nПричина ЧС: {reason}"
    if issuer:
        details += f"\nВыдавший: {issuer.mention} (ID: {issuer.id})"
        if issuer_roles_to_remove:
            details += f"\nСняты роли с выдавшего: {', '.join(r.name for r in issuer_roles_to_remove)}"

    await log_action(after.guild, "Попытка выдать роль участнику из ЧС", details, color=0xff0000)

# === !sync ===
@bot.command()
async def sync(ctx):
    """Синхронизация слэш-команд"""
    if ctx.author.id != OWNER_ID:
        return
    try:
        synced = await bot.tree.sync()
        await ctx.send(f"✅ Синхронизировано команд: {len(synced)}")
    except Exception as e:
        await ctx.send(f"❌ Ошибка синхронизации: {e}")

# === /выдать_вайт ===
@bot.tree.command(name="выдать_вайт", description="Добавить пользователя в вайт-лист")
@app_commands.describe(member="Участник")
async def give_white(interaction: discord.Interaction, member: discord.Member):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ Эта команда доступна только владельцу бота.", ephemeral=True)
        return
    add_to_white_list(member.id)
    embed = discord.Embed(
        title="🛡️ Вайт-лист",
        description=f"Владелец {interaction.user.mention} добавил {member.mention} в вайт-лист.",
        color=0x2ecc71
    )
    await interaction.response.send_message(embed=embed)

# === /обнуление_кд ===
@bot.tree.command(name="обнуление_кд", description="Сбросить все кулдауны для всех участников семьи")
async def reset_all_cooldowns(interaction: discord.Interaction):
    if DEP_OWNER_ROLE_ID not in [role.id for role in interaction.user.roles]:
        await interaction.response.send_message("❌ Эта команда доступна только Заместителю Владельца.", ephemeral=True)
        return
    with sqlite3.connect("voice_data.db") as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM applications")
        cursor.execute("DELETE FROM work_timer")
        conn.commit()
    embed = discord.Embed(
        title="🔄 Все кулдауны сброшены!",
        description=f"Заместитель {interaction.user.mention} сбросил все кулдауны для участников семьи.",
        color=0x2ecc71
    )
    embed.add_field(name="Что сброшено", value="• Кд на подачу заявки\n• Кд на команду `/work`", inline=False)
    await interaction.response.send_message(embed=embed)

# === СИСТЕМА БЕЗОПАСНОСТИ ===
@bot.event
async def on_guild_channel_delete(channel):
    if not channel.guild or not channel.guild.me:
        return
    try:
        await handle_security_violation(channel.guild, channel.last_message.author if channel.last_message else None, "удаление канала")
    except Exception:
        pass

@bot.event
async def on_guild_channel_update(before, after):
    if not after.guild or not after.guild.me:
        return
    if before.name != after.name or before.overwrites != after.overwrites:
        try:
            async for entry in after.guild.audit_logs(action=discord.AuditLogAction.channel_update, limit=1):
                if entry.target.id == after.id:
                    await handle_security_violation(after.guild, entry.user, "редактирование канала")
                    break
        except (discord.Forbidden, discord.NotFound):
            pass

@bot.event
async def on_guild_role_delete(role):
    if not role.guild or not role.guild.me:
        return
    try:
        async for entry in role.guild.audit_logs(action=discord.AuditLogAction.role_delete, limit=1):
            if entry.target.id == role.id:
                await handle_security_violation(role.guild, entry.user, "удаление роли")
                break
    except (discord.Forbidden, discord.NotFound):
        pass

@bot.event
async def on_guild_role_update(before, after):
    if not after.guild or not after.guild.me:
        return
    if before.name != after.name or before.permissions != after.permissions or before.color != after.color:
        try:
            async for entry in after.guild.audit_logs(action=discord.AuditLogAction.role_update, limit=1):
                if entry.target.id == after.id:
                    await handle_security_violation(after.guild, entry.user, "редактирование роли")
                    break
        except (discord.Forbidden, discord.NotFound):
            pass

async def handle_security_violation(guild, user, action):
    if not guild or not user or user.bot or user.id == bot.user.id:
        return
    if user.id == OWNER_ID or is_in_white_list(user.id):
        return
    if not any(role.id in FAMILY_ROLES.values() for role in user.roles):
        return
    strikes = add_strike(user.id)

    if strikes == 1:
        roles_to_remove = [role for role in user.roles if role.id in FAMILY_ROLES.values()]
        if roles_to_remove:
            try:
                await user.remove_roles(*roles_to_remove)
            except discord.Forbidden:
                pass
        await log_action(guild, "Нарушение безопасности (1)", f"Участник: {user.mention}\nДействие: {action}", color=0xffa500)

    elif strikes == 2:
        try:
            await user.kick(reason="2 нарушения безопасности")
            await log_action(guild, "Кик за нарушение (2)", f"Участник: {user.mention}\nДействие: {action}", color=0xff4500)
        except discord.Forbidden:
            pass

    elif strikes >= 3:
        try:
            await user.ban(reason="3+ нарушения безопасности")
            await log_action(guild, "Бан за нарушение (3+)", f"Участник: {user.mention}\nДействие: {action}", color=0xff0000)
        except discord.Forbidden:
            pass

# === /чс_семьи ===
@bot.tree.command(name="чс_семьи", description="Выдать чёрный список семьи участнику")
@app_commands.describe(user_id="ID пользователя", reason="Причина ЧС")
async def blacklist_family(interaction: discord.Interaction, user_id: str, reason: str):
    if FAMILY_ROLES["dep_owner"] not in [role.id for role in interaction.user.roles]:
        await interaction.response.send_message("❌ Эта команда доступна только Заместителю Владельца.", ephemeral=True)
        return
    try:
        uid = int(user_id)
    except ValueError:
        await interaction.response.send_message("❌ ID должен быть числом.", ephemeral=True)
        return
    member = interaction.guild.get_member(uid)
    if not member:
        await interaction.response.send_message("❌ Пользователь не найден на сервере.", ephemeral=True)
        return

    roles_to_remove = [interaction.guild.get_role(rid) for rid in FAMILY_ROLES.values()]
    roles_to_remove = [r for r in roles_to_remove if r and r in member.roles]
    if roles_to_remove:
        try:
            await member.remove_roles(*roles_to_remove)
        except discord.Forbidden:
            pass

    add_to_family_blacklist(uid, reason, interaction.user.id)
    await log_action(
        interaction.guild,
        "Выдача ЧС семьи",
        f"Участник: {member.mention} (ID: {uid})\nПричина: {reason}\nВыдал: {interaction.user.mention}",
        color=0xff0000
    )

    embed = discord.Embed(
        title="🚫 Чёрный список семьи Mercuri Famq",
        description=f"Пользователь {member.mention} добавлен в ЧС семьи.",
        color=0xff0000
    )
    embed.add_field(name="Причина", value=reason, inline=False)
    if roles_to_remove:
        embed.add_field(name="Снятые роли", value=", ".join(r.name for r in roles_to_remove), inline=False)
    embed.set_footer(text=f"Выдал: {interaction.user}")
    await interaction.response.send_message(embed=embed)

# === /снять_чс ===
@bot.tree.command(name="снять_чс", description="Снять чёрный список семьи с участника")
@app_commands.describe(user_id="ID пользователя")
async def unblacklist_family(interaction: discord.Interaction, user_id: str):
    if FAMILY_ROLES["dep_owner"] not in [role.id for role in interaction.user.roles]:
        await interaction.response.send_message("❌ Эта команда доступна только Заместителю Владельца.", ephemeral=True)
        return
    try:
        uid = int(user_id)
    except ValueError:
        await interaction.response.send_message("❌ ID должен быть числом.", ephemeral=True)
        return
    if not is_in_family_blacklist(uid):
        await interaction.response.send_message("❌ Пользователь не в чёрном списке семьи.", ephemeral=True)
        return

    remove_from_family_blacklist(uid)
    await log_action(
        interaction.guild,
        "Снятие ЧС семьи",
        f"Участник ID: {uid}\nСнял: {interaction.user.mention}",
        color=0x00ff00
    )

    member = interaction.guild.get_member(uid)
    mention = member.mention if member else f"ID: {uid}"
    embed = discord.Embed(
        title="✅ ЧС семьи снят",
        description=f"С пользователя {mention} снят чёрный список семьи Mercuri Famq.",
        color=0x00ff00
    )
    embed.set_footer(text=f"Снял: {interaction.user}")
    await interaction.response.send_message(embed=embed)

# === /набор ===
@bot.tree.command(name="набор", description="Открыть набор в указанном канале")
@app_commands.describe(channel_id="ID канала, куда будут приходить заявки")
async def recruitment(interaction: discord.Interaction, channel_id: str):
    allowed_roles = [FAMILY_ROLES["owner"], FAMILY_ROLES["dep_owner"]]
    if not has_any_role(interaction.user, allowed_roles):
        await interaction.response.send_message("❌ Эта команда доступна только Владельцу и Заместителю.", ephemeral=True)
        return
    try:
        cid = int(channel_id)
    except ValueError:
        await interaction.response.send_message("❌ ID канала должен быть числом.", ephemeral=True)
        return
    target_channel = interaction.guild.get_channel(cid)
    if not target_channel or not isinstance(target_channel, discord.TextChannel):
        await interaction.response.send_message("❌ Канал не найден или недоступен.", ephemeral=True)
        return

    if is_in_family_blacklist(interaction.user.id):
        await interaction.response.send_message("❌ Вы не можете открывать набор, находясь в ЧС семьи.", ephemeral=True)
        return

    embed = discord.Embed(
        title="🔥 Открыты заявки в **Mercuri Famq**!",
        description=(
            "✨ **Здравый и дружный коллектив**\n"
            "🎮 **Постоянный контент и активности**\n"
            "🎲 **Игры в кости, розыгрыши, ивенты**\n"
            "🛡️ **Семья — это навсегда**\n\n"
            "Если ты хочешь стать частью чего-то большего — жми кнопку ниже!"
        ),
        color=0xc41e3a
    )
    if interaction.guild.icon:
        embed.set_thumbnail(url=interaction.guild.icon.url)

    class ApplyButton(discord.ui.View):
        def __init__(self):
            super().__init__(timeout=None)

        @discord.ui.button(label="📄 Подать заявку", style=discord.ButtonStyle.green, emoji="📝")
        async def apply(self, inter: discord.Interaction, button: discord.ui.Button):
            if is_in_family_blacklist(inter.user.id):
                reason = get_blacklist_reason(inter.user.id)
                await inter.response.send_message(
                    f"❌ Вы находитесь в чёрном списке семьи.\n**Причина:** {reason}",
                    ephemeral=True
                )
                return
            if not can_submit_application(inter.user.id):
                await inter.response.send_message(
                    "❌ Вы можете подавать заявку не чаще одного раза в день.",
                    ephemeral=True
                )
                return
            modal = ApplicationModal(target_channel=target_channel)
            await inter.response.send_modal(modal)

    await interaction.response.send_message("✅ Набор открыт! Форма отправлена в этот канал.", ephemeral=True)
    await interaction.followup.send(embed=embed, view=ApplyButton())

# === МОДАЛЬНОЕ ОКНО ЗАЯВКИ ===
class ApplicationModal(discord.ui.Modal, title="Заявка в Mercuri Famq"):
    def __init__(self, target_channel: discord.TextChannel):
        super().__init__()
        self.target_channel = target_channel
        self.nick = discord.ui.TextInput(
            label="Ваш никнейм на сервере",
            placeholder="Пример: Nick Name",
            required=True,
            max_length=32
        )
        self.static_id = discord.ui.TextInput(
            label="Ваш Static ID",
            placeholder="Пример: 66666",
            required=True,
            max_length=10
        )
        self.age = discord.ui.TextInput(
            label="Сколько вам лет в IRL?",
            placeholder="Пример: 18",
            required=True,
            max_length=3
        )
        self.real_name = discord.ui.TextInput(
            label="Ваше имя в IRL",
            placeholder="Пример: Анатолий",
            required=True,
            max_length=30
        )
        self.details = discord.ui.TextInput(
            label="Время в игре + Откуда узнали?",
            placeholder="Пример: 5 часов в день\nTikTok / Друг",
            required=True,
            max_length=200,
            style=discord.TextStyle.paragraph
        )
        for item in [self.nick, self.static_id, self.age, self.real_name, self.details]:
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        if is_in_family_blacklist(interaction.user.id):
            reason = get_blacklist_reason(interaction.user.id)
            await interaction.response.send_message(
                f"❌ Вы находитесь в чёрном списке семьи.\n**Причина:** {reason}",
                ephemeral=True
            )
            return
        if not can_submit_application(interaction.user.id):
            await interaction.response.send_message(
                "❌ Вы можете подавать заявку не чаще одного раза в день.",
                ephemeral=True
            )
            return

        record_application(interaction.user.id)

        embed = discord.Embed(
            title="📄 Новая заявка на вступление",
            color=0x2b2d31,
            timestamp=discord.utils.utcnow()
        )
        embed.add_field(name="👤 Никнейм", value=self.nick.value, inline=True)
        embed.add_field(name="🆔 Static ID", value=self.static_id.value, inline=True)
        embed.add_field(name="🎂 Возраст (IRL)", value=self.age.value, inline=True)
        embed.add_field(name="📛 Имя (IRL)", value=self.real_name.value, inline=True)
        detail_value = self.details.value[:1020] + ("..." if len(self.details.value) > 1020 else "")
        embed.add_field(name="ℹ️ Детали", value=detail_value, inline=False)
        embed.set_footer(text=f"Заявитель: {interaction.user} | ID: {interaction.user.id}")

        view = ApplicationControlView(applicant=interaction.user)
        await self.target_channel.send(embed=embed, view=view)
        await interaction.response.send_message("✅ Ваша заявка отправлена! Ожидайте обзвона.", ephemeral=True)

# === УПРАВЛЕНИЕ ЗАЯВКОЙ ===
class ApplicationControlView(discord.ui.View):
    def __init__(self, applicant: discord.Member):
        super().__init__(timeout=None)
        self.applicant = applicant

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not has_any_role(interaction.user, MANAGE_APPLICATIONS_ROLES):
            await interaction.response.send_message("❌ У вас нет прав для управления заявками.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="📞 Вызвать на обзвон", style=discord.ButtonStyle.blurple, emoji="🔊")
    async def call_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await self.applicant.send("🔔 **Вы вызваны на обзвон в семью `Mercuri Famq`!**\nЗайдите в любой открытый голосовой канал.")
            await interaction.response.send_message("✅ Уведомление отправлено.", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("❌ Не удалось отправить ЛС.", ephemeral=True)

    @discord.ui.button(label="✅ Одобрено", style=discord.ButtonStyle.green, emoji="🟢")
    async def approve_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await self.applicant.send("🎉 **Поздравляем!** Вы приняты в **Mercuri Famq**!")
            common_role = interaction.guild.get_role(FAMILY_ROLES["common"])
            newbie_role = interaction.guild.get_role(FAMILY_ROLES["newbie"])
            roles_to_add = []
            if common_role:
                roles_to_add.append(common_role)
            if newbie_role:
                roles_to_add.append(newbie_role)
            if roles_to_add:
                await self.applicant.add_roles(*roles_to_add)
        except discord.Forbidden:
            pass

        try:
            welcome_msg = (
                "🛡️ **Добро пожаловать в Mercuri Famq!**\n\n"
                "Чтобы стать полноценным участником семьи, выполните следующие шаги:\n\n"
                "1️⃣ **Заполните профиль**\n"
                "→ Пропишите команду `/профиль`\n"
                "→ Укажите свой никнейм и Static ID\n\n"
                "2️⃣ **Создайте личную ветку**\n"
                "→ Перейдите в канал <#1470473650338660352>\n"
                "→ Нажмите «Создать ветку»\n"
                "→ Название: `ВашНик | StaticID`\n"
                "→ Отправьте **ссылку на ветку** этому боту в ЛС\n\n"
                "3️⃣ **Присылайте скриншоты активности**\n"
                "→ Когда будете кататься на МП от семьи — делайте скрины\n"
                "→ Присылайте их **этому боту в ЛС**\n"
                "→ Бот автоматически отправит их в вашу ветку с пингом лидеров!\n\n"
                "4️⃣ **Казино и развлечения**\n"
                "→ `/казино` — играйте в кости, слоты, рулетку\n"
                "→ `/work` — зарабатывайте $10 000 каждые 5 минут\n"
                "→ `/магазин` — покупайте роли и вирты\n\n"
                "5️⃣ **Правила поведения**\n"
                "→ ❌ Нельзя оскорблять, фрикать, троллить\n"
                "→ ❌ Запрещено попрошайничать (`/выдать_денег` только по заслугам)\n"
                "→ ✅ Будьте активны в голосовых каналах, когда находитесь на сервере\n\n"
                "💡 **Совет**: чем активнее вы — тем быстрее получите высокий ранг!\n"
                "Удачи, брат! 💪"
            )
            await self.applicant.send(welcome_msg)
        except discord.Forbidden:
            pass

        embed = interaction.message.embeds[0]
        embed.color = discord.Color.green()
        embed.title = "✅ Заявка одобрена"
        for child in self.children:
            child.disabled = True
        await interaction.message.edit(embed=embed, view=self)
        await interaction.response.defer()
        await log_action(
            interaction.guild,
            "Заявка одобрена",
            f"Заявитель: {self.applicant.mention}\nОдобрил: {interaction.user.mention}",
            color=0x00ff00
        )

    @discord.ui.button(label="❌ Отказано", style=discord.ButtonStyle.red, emoji="🔴")
    async def reject_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(RejectReasonModal(self.applicant, interaction.message))

class RejectReasonModal(discord.ui.Modal, title="Причина отказа"):
    def __init__(self, applicant: discord.Member, message: discord.Message):
        super().__init__()
        self.applicant = applicant
        self.message = message
        self.reason = discord.ui.TextInput(
            label="Причина отказа",
            placeholder="Например: низкая активность",
            required=True,
            max_length=200,
            style=discord.TextStyle.paragraph
        )
        self.add_item(self.reason)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            await self.applicant.send(f"❌ Ваша заявка отклонена.\n**Причина:** {self.reason.value}")
        except discord.Forbidden:
            pass
        embed = self.message.embeds[0]
        embed.color = discord.Color.red()
        embed.title = "❌ Заявка отклонена"
        reason_value = self.reason.value[:1020] + ("..." if len(self.reason.value) > 1020 else "")
        embed.add_field(name="💬 Причина", value=reason_value, inline=False)
        await self.message.edit(embed=embed, view=None)
        await interaction.response.send_message("✅ Отказ обработан.", ephemeral=True)
        await log_action(
            interaction.guild,
            "Заявка отклонена",
            f"Заявитель: {self.applicant.mention}\nПричина: {self.reason.value}\nОтклонил: {interaction.user.mention}",
            color=0xff0000
        )

# === /статус_заявок ===
@bot.tree.command(name="статус_заявок", description="Показать статус обработки заявок")
async def application_status(interaction: discord.Interaction):
    if not has_any_role(interaction.user, MANAGE_APPLICATIONS_ROLES):
        await interaction.response.send_message("❌ У вас нет прав для просмотра статуса заявок.", ephemeral=True)
        return
    pending_count = get_pending_applications_count()
    last_time = get_last_application_time()
    embed = discord.Embed(title="📊 Статус заявок", color=0xc41e3a)
    embed.add_field(name="Всего нерассмотренных", value=str(pending_count), inline=True)
    embed.add_field(name="Последняя заявка", value=last_time, inline=True)
    embed.add_field(name="Обработка", value="Доступна для ролей [ʀᴇᴄʀᴜɪᴛ] и выше", inline=False)
    embed.set_footer(text="Используйте /набор для открытия нового набора")
    await interaction.response.send_message(embed=embed)

# === /состав_семьи ===
@bot.tree.command(name="состав_семьи", description="Показать состав семьи по рангам")
async def family_members(interaction: discord.Interaction):
    if not any(role.id == FAMILY_ROLES["common"] for role in interaction.user.roles):
        await interaction.response.send_message("❌ Эта команда доступна только участникам семьи.", ephemeral=True)
        return
    rank_order = [
        (FAMILY_ROLES["owner"], "[Владелец]"),
        (FAMILY_ROLES["dep_owner"], "[Заместитель Владельца]"),
        (FAMILY_ROLES["high_rang"], "[ʜɪɢʜ ʀᴀɴɢ]"),
        (FAMILY_ROLES["recruit"], "[ʀᴇᴄʀᴜɪᴛ]"),
        (FAMILY_ROLES["main"], "[ᴍᴀɪɴ]"),
        (FAMILY_ROLES["newbie"], "[ɴᴇᴡʙɪᴇ]"),
    ]
    embed = discord.Embed(
        title="👨‍👩‍👧‍👦 Состав семьи Mercuri Famq",
        color=0xc41e3a,
        timestamp=discord.utils.utcnow()
    )
    status_map = {
        discord.Status.online: "🟢 Онлайн",
        discord.Status.idle: "🌙 Отошёл",
        discord.Status.dnd: "⛔ Не беспокоить",
        discord.Status.offline: "⚫ Не в сети"
    }
    for role_id, rank_name in rank_order:
        role = interaction.guild.get_role(role_id)
        if not role:
            continue
        members = [m for m in role.members if not m.bot]
        if not members:
            continue
        members.sort(key=lambda m: m.display_name.lower())
        lines = [f"{status_map.get(m.status, '⚫ Не в сети')} — {m.mention}" for m in members]
        full_text = "\n".join(lines)
        if len(full_text) <= 1024:
            embed.add_field(name=rank_name, value=full_text, inline=False)
        else:
            half = len(lines) // 2
            part1 = "\n".join(lines[:half])[:1024]
            part2 = "\n".join(lines[half:])[:1024]
            embed.add_field(name=rank_name, value=part1, inline=False)
            if part2.strip():
                embed.add_field(name=f"{rank_name} (продолжение)", value=part2, inline=False)
    if len(embed) > 6000:
        embed = discord.Embed(
            title="👨‍👩‍👧‍👦 Состав семьи Mercuri Famq",
            description="Семья слишком велика для отображения.",
            color=0xc41e3a
        )
    await interaction.response.send_message(embed=embed)

# === /состояние ===
@bot.tree.command(name="состояние", description="Показать статистику пользователя по голосовым каналам")
@app_commands.describe(user="Пользователь для проверки")
async def user_state(interaction: discord.Interaction, user: discord.User):
    allowed_roles = [FAMILY_ROLES["owner"], FAMILY_ROLES["dep_owner"], 1460688847267565744]
    if not has_any_role(interaction.user, allowed_roles):
        await interaction.response.send_message("❌ У вас нет прав для просмотра статистики.", ephemeral=True)
        return
    member = interaction.guild.get_member(user.id)
    if not member:
        await interaction.response.send_message("❌ Пользователь не на сервере.", ephemeral=True)
        return
    sessions = get_user_sessions(user.id)
    if not sessions:
        await interaction.response.send_message(f"🔇 У {user.mention} нет записей о пребывании в голосовых.", ephemeral=True)
        return
    total_seconds = 0
    details = []
    for channel_id, start_str, end_str in sessions[:10]:
        start = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
        end = datetime.fromisoformat((end_str or datetime.now(timezone.utc).isoformat()).replace("Z", "+00:00"))
        channel = interaction.guild.get_channel(channel_id)
        name = channel.name if channel else f"ID:{channel_id}"
        duration = int((end - start).total_seconds() // 60)
        total_seconds += (end - start).total_seconds()
        details.append(f"🎙️ {name} — {start.strftime('%d.%m %H:%M')} → {end.strftime('%H:%M')} ({duration} мин)")
    hours, minutes = divmod(int(total_seconds // 60), 60)
    embed = discord.Embed(
        title=f"📊 Голосовая активность: {user.display_name}",
        description=f"Общее время: {hours} ч {minutes} мин",
        color=0xc41e3a
    )
    embed.add_field(name="Последние сессии", value="\n".join(details) or "Нет данных", inline=False)
    await interaction.response.send_message(embed=embed)

# === /профиль ===
@bot.tree.command(name="профиль", description="Заполнить свой профиль семьи")
async def profile_command(interaction: discord.Interaction):
    if FAMILY_ROLES["common"] not in [role.id for role in interaction.user.roles]:
        await interaction.response.send_message("❌ Эта команда доступна только участникам семьи.", ephemeral=True)
        return

    class ProfileModal(discord.ui.Modal, title="Ваш профиль семьи"):
        def __init__(self):
            super().__init__()
            self.nick = discord.ui.TextInput(
                label="Ваш никнейм",
                placeholder="Пример: Nick Name",
                required=True,
                max_length=32
            )
            self.static_id = discord.ui.TextInput(
                label="Ваш Static ID",
                placeholder="Пример: 66666",
                required=True,
                max_length=10
            )
            self.add_item(self.nick)
            self.add_item(self.static_id)

        async def on_submit(self, inter: discord.Interaction):
            save_profile(inter.user.id, self.nick.value, self.static_id.value)
            await inter.response.send_message("✅ Ваш профиль успешно сохранён!", ephemeral=True)

    await interaction.response.send_modal(ProfileModal())

# === /посмотреть_профиль ===
@bot.tree.command(name="посмотреть_профиль", description="Просмотреть профиль участника")
@app_commands.describe(member="Участник для просмотра")
async def view_profile(interaction: discord.Interaction, member: discord.Member):
    if DEP_OWNER_ROLE_ID not in [role.id for role in interaction.user.roles]:
        await interaction.response.send_message("❌ Эта команда доступна только Заместителю Владельца.", ephemeral=True)
        return
    profile = get_profile(member.id)
    embed = discord.Embed(title=f"📄 Профиль: {member.display_name}", color=0xc41e3a)
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="👤 Упоминание", value=member.mention, inline=True)
    embed.add_field(name="🆔 ID", value=str(member.id), inline=True)
    if profile:
        embed.add_field(name="📛 Никнейм", value=profile[0], inline=False)
        embed.add_field(name="🎮 Static ID", value=profile[1], inline=False)
    else:
        embed.description = "❌ Профиль не заполнен."
    await interaction.response.send_message(embed=embed)

# === /восстановить_состав ===
@bot.tree.command(name="восстановить_состав", description="Восстановить состав семьи из бэкапа")
@app_commands.describe(date="Дата бэкапа (формат: YYYY-MM-DD_HH-MM)")
async def restore_backup(interaction: discord.Interaction, date: str):
    if OWNER_ROLE_ID not in [role.id for role in interaction.user.roles]:
        await interaction.response.send_message("❌ Только Владелец может восстанавливать состав.", ephemeral=True)
        return
    filepath = f"backups/backup_{date}.json"
    if not os.path.exists(filepath):
        files = "\n".join(f"`{f.replace('backup_', '').replace('.json', '')}`" for f in sorted(os.listdir("backups")))
        await interaction.response.send_message(f"❌ Бэкап не найден.\nДоступные даты:\n{files}", ephemeral=True)
        return
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)
    restored = 0
    for member_data in data["members"]:
        member = interaction.guild.get_member(member_data["user_id"])
        if not member:
            continue
        roles_to_add = []
        for role_id in member_data["roles"]:
            role = interaction.guild.get_role(role_id)
            if role and role not in member.roles:
                roles_to_add.append(role)
        if roles_to_add:
            try:
                await member.add_roles(*roles_to_add)
                restored += 1
            except discord.Forbidden:
                pass
    embed = discord.Embed(
        title="✅ Восстановление завершено",
        description=f"Восстановлено ролей для {restored} участников.",
        color=0x00ff00
    )
    embed.add_field(name="Файл", value=f"`{date}.json`", inline=False)
    await interaction.response.send_message(embed=embed)

# === КАЗИНО ===
def create_casino_view(user_id: int):
    class CasinoView(discord.ui.View):
        def __init__(self):
            super().__init__(timeout=300)

        async def interaction_check(self, interaction: discord.Interaction) -> bool:
            if is_casino_banned(interaction.user.id):
                await interaction.response.send_message("❌ Вы забанены в казино.", ephemeral=True)
                return False
            if interaction.user.id != user_id:
                await interaction.response.send_message("❌ Эта игра не для вас.", ephemeral=True)
                return False
            return True

        @discord.ui.button(label="🎲 Кости", style=discord.ButtonStyle.blurple, emoji="🎲")
        async def dice_button(self, inter: discord.Interaction, button: discord.ui.Button):
            await inter.response.send_modal(DiceModal(min_bet=1000, user_id=user_id))

        @discord.ui.button(label="🎰 Слоты", style=discord.ButtonStyle.green, emoji="🎰")
        async def slots_button(self, inter: discord.Interaction, button: discord.ui.Button):
            await inter.response.send_modal(SlotsModal(min_bet=500, user_id=user_id))

        @discord.ui.button(label="🔮 Шанс", style=discord.ButtonStyle.red, emoji="🔮")
        async def chance_button(self, inter: discord.Interaction, button: discord.ui.Button):
            await inter.response.send_modal(ChanceModal(min_bet=100, user_id=user_id))

        @discord.ui.button(label="🎡 Рулетка", style=discord.ButtonStyle.grey, emoji="🎡")
        async def roulette_button(self, inter: discord.Interaction, button: discord.ui.Button):
            await inter.response.send_modal(RouletteModal(min_bet=1000, user_id=user_id))

    return CasinoView()

class DiceModal(discord.ui.Modal, title="🎲 Кости"):
    def __init__(self, min_bet=1000, user_id=None):
        super().__init__()
        self.min_bet = min_bet
        self.user_id = user_id
        self.bet = discord.ui.TextInput(label=f"Ставка (мин. ${min_bet:,})", placeholder="Сумма", required=True, max_length=10)
        self.add_item(self.bet)

    async def on_submit(self, inter: discord.Interaction):
        try:
            amount = int(self.bet.value.replace(",", "").replace(" ", ""))
        except ValueError:
            await inter.response.send_message("❌ Сумма должна быть числом.", ephemeral=True)
            return
        if amount < self.min_bet or amount > get_balance(inter.user.id):
            await inter.response.send_message("❌ Неверная сумма.", ephemeral=True)
            return
        balance = get_balance(inter.user.id)
        set_balance(inter.user.id, balance - amount)
        if random.random() < 0.35:
            prize = amount * 2
            set_balance(inter.user.id, balance - amount + prize)
            result = f"🎉 Вы выиграли **${prize:,}**!\nВаш бросок оказался удачным!"
            color = 0x2ecc71
        else:
            result = f"💀 Вы проиграли **${amount:,}**.\nПовезёт в следующий раз!"
            color = 0xe74c3c
        new_balance = get_balance(inter.user.id)
        embed = discord.Embed(title="🎲 Кости", description=result, color=color)
        embed.set_footer(text=f"Баланс: ${new_balance:,}")
        await inter.response.edit_message(embed=embed, view=create_casino_view(self.user_id))

class SlotsModal(discord.ui.Modal, title="🎰 Слоты"):
    def __init__(self, min_bet=500, user_id=None):
        super().__init__()
        self.min_bet = min_bet
        self.user_id = user_id
        self.bet = discord.ui.TextInput(label=f"Ставка (мин. ${min_bet:,})", placeholder="Сумма", required=True, max_length=10)
        self.add_item(self.bet)

    async def on_submit(self, inter: discord.Interaction):
        try:
            amount = int(self.bet.value.replace(",", "").replace(" ", ""))
        except ValueError:
            await inter.response.send_message("❌ Сумма должна быть числом.", ephemeral=True)
            return
        if amount < self.min_bet or amount > get_balance(inter.user.id):
            await inter.response.send_message("❌ Неверная сумма.", ephemeral=True)
            return
        balance = get_balance(inter.user.id)
        set_balance(inter.user.id, balance - amount)
        symbols = ["🍒", "🍋", "🍊", "🍇", "💎", "7️⃣"]
        spin = [random.choice(symbols) for _ in range(3)]
        spin_str = " | ".join(spin)
        if random.random() < 0.35:
            if spin[0] == spin[1] == spin[2]:
                prize = amount * 3
                set_balance(inter.user.id, balance - amount + prize)
                result = f"🏆 Джекпот! Вы выиграли **${prize:,}**!\n{spin_str}"
                color = 0x2ecc71
            elif spin[0] == spin[1] or spin[1] == spin[2] or spin[0] == spin[2]:
                prize = amount * 2
                set_balance(inter.user.id, balance - amount + prize)
                result = f"👍 Два одинаковых! Вы выиграли **${prize:,}**!\n{spin_str}"
                color = 0x3498db
            else:
                prize = amount * 2
                set_balance(inter.user.id, balance - amount + prize)
                result = f"✨ Удача на вашей стороне! Вы выиграли **${prize:,}**!\n{spin_str}"
                color = 0x2ecc71
        else:
            result = f"💔 Повезёт в следующий раз!\n{spin_str}"
            color = 0xe74c3c
        new_balance = get_balance(inter.user.id)
        embed = discord.Embed(title="🎰 Слоты", description=result, color=color)
        embed.set_footer(text=f"Баланс: ${new_balance:,}")
        await inter.response.edit_message(embed=embed, view=create_casino_view(self.user_id))

class ChanceModal(discord.ui.Modal, title="🔮 Шанс"):
    def __init__(self, min_bet=100, user_id=None):
        super().__init__()
        self.min_bet = min_bet
        self.user_id = user_id
        self.bet = discord.ui.TextInput(label=f"Ставка (мин. ${min_bet:,})", placeholder="Сумма", required=True, max_length=10)
        self.add_item(self.bet)

    async def on_submit(self, inter: discord.Interaction):
        try:
            amount = int(self.bet.value.replace(",", "").replace(" ", ""))
        except ValueError:
            await inter.response.send_message("❌ Сумма должна быть числом.", ephemeral=True)
            return
        if amount < self.min_bet or amount > get_balance(inter.user.id):
            await inter.response.send_message("❌ Неверная сумма.", ephemeral=True)
            return
        balance = get_balance(inter.user.id)
        set_balance(inter.user.id, balance - amount)
        if random.random() < 0.35:
            prize = amount * 3
            set_balance(inter.user.id, balance - amount + prize)
            result = f"✨ Удача на вашей стороне! Вы умножили ставку на 3!\nВыигрыш: **${prize:,}**"
            color = 0x2ecc71
        else:
            result = f"🌑 Вам не повезло. Ставка потеряна."
            color = 0xe74c3c
        new_balance = get_balance(inter.user.id)
        embed = discord.Embed(title="🔮 Шанс", description=result, color=color)
        embed.set_footer(text=f"Баланс: ${new_balance:,}")
        await inter.response.edit_message(embed=embed, view=create_casino_view(self.user_id))

class RouletteModal(discord.ui.Modal, title="🎡 Рулетка"):
    def __init__(self, min_bet=1000, user_id=None):
        super().__init__()
        self.min_bet = min_bet
        self.user_id = user_id
        self.number = discord.ui.TextInput(label="Число (1-36)", placeholder="1-36", required=True, max_length=2)
        self.bet = discord.ui.TextInput(label=f"Ставка (мин. ${min_bet:,})", placeholder="Сумма", required=True, max_length=10)
        self.add_item(self.number)
        self.add_item(self.bet)

    async def on_submit(self, inter: discord.Interaction):
        try:
            number = int(self.number.value)
            amount = int(self.bet.value.replace(",", "").replace(" ", ""))
        except ValueError:
            await inter.response.send_message("❌ Число и сумма должны быть числами.", ephemeral=True)
            return
        if number < 1 or number > 36 or amount < self.min_bet or amount > get_balance(inter.user.id):
            await inter.response.send_message("❌ Неверные данные.", ephemeral=True)
            return
        balance = get_balance(inter.user.id)
        set_balance(inter.user.id, balance - amount)
        bot_number = random.randint(1, 36)
        if random.random() < 0.1:
            if number == bot_number:
                prize = amount * 36
                set_balance(inter.user.id, balance - amount + prize)
                result = f"🎯 БИНГО! Вы угадали число **{bot_number}**!\nВы выиграли **${prize:,}**!"
                color = 0x2ecc71
            else:
                prize = amount * 2
                set_balance(inter.user.id, balance - amount + prize)
                result = f"✨ Удача на вашей стороне! Вы выиграли **${prize:,}**!\nВыпало число: {bot_number}"
                color = 0x2ecc71
        else:
            result = f"🔴 Выпало число **{bot_number}**. Вы проиграли **${amount:,}**."
            color = 0xe74c3c
        new_balance = get_balance(inter.user.id)
        embed = discord.Embed(title="🎡 Рулетка", description=result, color=color)
        embed.set_footer(text=f"Баланс: ${new_balance:,}")
        await inter.response.edit_message(embed=embed, view=create_casino_view(self.user_id))

@bot.tree.command(name="казино", description="Играть в казино")
async def casino_command(interaction: discord.Interaction):
    if is_casino_banned(interaction.user.id):
        await interaction.response.send_message("❌ Вы забанены в казино.", ephemeral=True)
        return
    balance = get_balance(interaction.user.id)
    embed = discord.Embed(
        title="🎰 Казино Mercuri Famq",
        description=f"{interaction.user.mention}, ваш баланс: ${balance:,}\nВыберите игру:",
        color=0x9b59b6
    )
    await interaction.response.send_message(embed=embed, view=create_casino_view(interaction.user.id))

@bot.tree.command(name="топ_казино", description="Топ-10 богачей казино")
async def top_casino(interaction: discord.Interaction):
    with sqlite3.connect("voice_data.db") as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, balance FROM casino_balance ORDER BY balance DESC LIMIT 10")
        top_players = cursor.fetchall()
    if not top_players:
        await interaction.response.send_message("Никто ещё не играл в казино.", ephemeral=True)
        return
    description = ""
    for i, (user_id, balance) in enumerate(top_players, 1):
        user = await bot.fetch_user(user_id)
        name = user.display_name if user else f"ID: {user_id}"
        description += f"{i}. {name} — ${balance:,}\n"
    embed = discord.Embed(title="🏆 Топ-10 казино", description=description, color=0xf1c40f)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="work", description="Работать и получить $10,000")
async def work_command(interaction: discord.Interaction):
    if FAMILY_ROLES["common"] not in [role.id for role in interaction.user.roles]:
        await interaction.response.send_message("❌ Эта команда доступна только участникам семьи.", ephemeral=True)
        return
    if not can_work(interaction.user.id):
        await interaction.response.send_message("⏳ Вы можете работать раз в 5 минут.", ephemeral=True)
        return
    current = get_balance(interaction.user.id)
    new_balance = current + 10000
    set_balance(interaction.user.id, new_balance)
    update_work_time(interaction.user.id)
    embed = discord.Embed(
        title="💼 Работа завершена!",
        description=f"Вы заработали $10,000!\nВаш новый баланс: ${new_balance:,}",
        color=0x2ecc71
    )
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="выдать_денег", description="Выдать деньги участнику")
@app_commands.describe(member="Участник", amount="Сумма в долларах")
async def give_money(interaction: discord.Interaction, member: discord.Member, amount: int):
    if DEP_OWNER_ROLE_ID not in [role.id for role in interaction.user.roles]:
        await interaction.response.send_message("❌ Эта команда доступна только Заместителю Владельца.", ephemeral=True)
        return
    if amount <= 0:
        await interaction.response.send_message("❌ Сумма должна быть положительной.", ephemeral=True)
        return
    current = get_balance(member.id)
    new_balance = current + amount
    set_balance(member.id, new_balance)
    embed = discord.Embed(
        title="💸 Выдача денег",
        description=f"Заместитель {interaction.user.mention} выдал ${amount:,} участнику {member.mention}.",
        color=0x2ecc71
    )
    embed.add_field(name="Новый баланс", value=f"${new_balance:,}", inline=False)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="обнулить_баланс", description="Обнулить баланс участника за нарушения")
@app_commands.describe(member="Участник")
async def reset_balance(interaction: discord.Interaction, member: discord.Member):
    if DEP_OWNER_ROLE_ID not in [role.id for role in interaction.user.roles]:
        await interaction.response.send_message("❌ Эта команда доступна только Заместителю Владельца.", ephemeral=True)
        return
    old_balance = get_balance(member.id)
    set_balance(member.id, 0)
    embed = discord.Embed(
        title="⚖️ Баланс обнулён",
        description=f"Заместитель {interaction.user.mention} обнулил баланс участника {member.mention} за нарушения.",
        color=0xff0000
    )
    embed.add_field(name="Предыдущий баланс", value=f"${old_balance:,}", inline=False)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="обнулить_всех", description="Обнулить балансы всех участников семьи")
async def reset_all_balances(interaction: discord.Interaction):
    if DEP_OWNER_ROLE_ID not in [role.id for role in interaction.user.roles]:
        await interaction.response.send_message("❌ Эта команда доступна только Заместителю Владельца.", ephemeral=True)
        return
    members = get_all_family_members(interaction.guild)
    with sqlite3.connect("voice_data.db") as conn:
        cursor = conn.cursor()
        for member in members:
            cursor.execute("INSERT OR REPLACE INTO casino_balance (user_id, balance) VALUES (?, 10000)", (member.id,))
        conn.commit()
    embed = discord.Embed(
        title="🔄 Все балансы сброшены!",
        description=f"Заместитель {interaction.user.mention} сбросил балансы всех участников семьи до $10,000.",
        color=0xff0000
    )
    embed.add_field(name="Затронуто участников", value=str(len(members)), inline=False)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="выдать_всем_деньги", description="Выдать деньги всем участникам семьи")
@app_commands.describe(amount="Сумма в долларах")
async def give_money_to_all(interaction: discord.Interaction, amount: int):
    if DEP_OWNER_ROLE_ID not in [role.id for role in interaction.user.roles]:
        await interaction.response.send_message("❌ Эта команда доступна только Заместителю Владельца.", ephemeral=True)
        return
    if amount <= 0:
        await interaction.response.send_message("❌ Сумма должна быть положительной.", ephemeral=True)
        return
    members = get_all_family_members(interaction.guild)
    with sqlite3.connect("voice_data.db") as conn:
        cursor = conn.cursor()
        for member in members:
            cursor.execute("SELECT balance FROM casino_balance WHERE user_id = ?", (member.id,))
            result = cursor.fetchone()
            current = result[0] if result else 10000
            cursor.execute("INSERT OR REPLACE INTO casino_balance (user_id, balance) VALUES (?, ?)", (member.id, current + amount))
        conn.commit()
    embed = discord.Embed(
        title="💸 Массовая выдача денег",
        description=f"Заместитель {interaction.user.mention} выдал ${amount:,} каждому участнику семьи.",
        color=0x2ecc71
    )
    embed.add_field(name="Получателей", value=str(len(members)), inline=True)
    embed.add_field(name="Общая сумма", value=f"${amount * len(members):,}", inline=True)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="бан_казино", description="Забанить участника в казино")
@app_commands.describe(member="Участник")
async def ban_casino(interaction: discord.Interaction, member: discord.Member):
    if DEP_OWNER_ROLE_ID not in [role.id for role in interaction.user.roles]:
        await interaction.response.send_message("❌ Эта команда доступна только Заместителю Владельца.", ephemeral=True)
        return
    if is_casino_banned(member.id):
        await interaction.response.send_message("❌ Этот участник уже забанен в казино.", ephemeral=True)
        return
    ban_from_casino(member.id)
    embed = discord.Embed(
        title="🚫 Бан в казино",
        description=f"Заместитель {interaction.user.mention} забанил {member.mention} в казино.",
        color=0xff0000
    )
    await interaction.response.send_message(embed=embed)

# === ОБРАБОТКА ЛИЧНЫХ СООБЩЕНИЙ ===
@bot.event
async def on_message(message):
    if message.author == bot.user or message.guild is not None:
        return
    content = message.content.strip()
    if "https://discord.com/channels/" in content:
        try:
            parts = content.split("/")
            thread_id = int(parts[-1])
            try:
                thread = await bot.fetch_channel(thread_id)
                if thread.parent_id != THREADS_CHANNEL_ID:
                    await message.channel.send("❌ Эта ветка не из канала для заявок.")
                    return
            except discord.NotFound:
                await message.channel.send("❌ Ветка не найдена. Убедитесь, что ссылка правильная и бот имеет к ней доступ.")
                return
            except discord.Forbidden:
                await message.channel.send("❌ Бот не имеет доступа к этой ветке. Убедитесь, что ветка публичная или бот добавлен в неё.")
                return
            save_thread_link(message.author.id, str(thread_id))
            await message.channel.send("✅ Ссылка на ветку сохранена! Теперь присылайте скриншоты активности.")
        except (ValueError, IndexError):
            await message.channel.send("❌ Неверная ссылка на ветку.")
        return

    if message.attachments:
        thread_id_str = get_thread_link(message.author.id)
        if not thread_id_str:
            await message.channel.send("❌ Сначала отправьте ссылку на свою ветку!")
            return
        try:
            thread_id = int(thread_id_str)
            thread = await bot.fetch_channel(thread_id)
            embed = discord.Embed(
                title="📸 Новая активность",
                description=f"Участник {message.author.mention} прислал скриншот:",
                color=0x2ecc71,
                timestamp=discord.utils.utcnow()
            )
            embed.set_image(url=message.attachments[0].url)

            # 🔧 ИСПРАВЛЕНО: используем thread.guild, а не message.guild
            guild = thread.guild
            if not guild:
                await message.channel.send("❌ Ветка не привязана к серверу.")
                return

            owner = guild.get_role(OWNER_ROLE_ID)
            dep_owner = guild.get_role(DEP_OWNER_ROLE_ID)
            ping_text = ""
            if owner:
                ping_text += owner.mention + " "
            if dep_owner:
                ping_text += dep_owner.mention

            await thread.send(content=ping_text, embed=embed)
            await message.channel.send("✅ Скриншот отправлен в вашу ветку!")
        except discord.NotFound:
            await message.channel.send("❌ Ветка была удалена. Пожалуйста, пришлите новую ссылку.")
            with sqlite3.connect("voice_data.db") as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM user_threads WHERE user_id = ?", (message.author.id,))
                conn.commit()
        except discord.Forbidden:
            await message.channel.send("❌ Бот не может отправить сообщение в вашу ветку. Убедитесь, что он имеет права.")
        except Exception as e:
            await message.channel.send(f"❌ Ошибка: {str(e)}")
        return

    await message.channel.send(
        "ℹ️ **Подсказка**:\n"
        "- Чтобы зарегистрировать ветку — пришлите ссылку на неё\n"
        "- Чтобы отправить скриншот — просто прикрепите изображение\n"
        "- Команды работают только на сервере!"
    )

import atexit

def close_db():
    global _db_conn
    if _db_conn:
        _db_conn.close()

atexit.register(close_db)

# === ЗАПУСК ===
if __name__ == "__main__":
    init_db()
    bot.run(TOKEN)
