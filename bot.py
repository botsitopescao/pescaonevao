import discord 
import psycopg2
import psycopg2.extras
from discord.ext import commands
import json
import random
import os
import re
import threading
import unicodedata
import asyncio
import datetime
from flask import Flask, request, jsonify
from zoneinfo import ZoneInfo

######################################
# CONFIGURACIÓN: IDs y Servidor
######################################
OWNER_ID = 1336609089656197171         # Tu Discord ID (único autorizado para comandos sensibles)
PRIVATE_CHANNEL_ID = 1338130641354620988  # Canal privado para comandos sensibles (no se utiliza en la versión final)
PUBLIC_CHANNEL_ID  = 1338126297666424874  # Canal público donde se muestran resultados sensibles
SPECIAL_HELP_CHANNEL = 1338747286901100554  # Canal especial para que el owner reciba la lista extendida de comandos
GUILD_ID = 123456789012345678            # REEMPLAZA con el ID real de tu servidor (guild)

API_SECRET = os.environ.get("API_SECRET")  # Para la API privada (opcional)

######################################
# CONEXIÓN A LA BASE DE DATOS POSTGRESQL
######################################
DATABASE_URL = os.environ.get("DATABASE_URL")  # Usualmente la Internal Database URL de Render
conn = psycopg2.connect(DATABASE_URL)
conn.autocommit = True

def init_db():
    with conn.cursor() as cur:
        # Tabla de participantes (antigua)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS participants (
                id TEXT PRIMARY KEY,
                nombre TEXT,
                puntos INTEGER DEFAULT 0,
                symbolic INTEGER DEFAULT 0,
                etapa INTEGER DEFAULT 1,
                logros JSONB DEFAULT '[]'
            )
        """)
        # Tabla de registros (modificada)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS registrations (
                user_id TEXT PRIMARY KEY,
                discord_name TEXT,
                fortnite_username TEXT,
                platform TEXT,
                country TEXT,
                puntuacion INTEGER DEFAULT 0,
                etapa INTEGER DEFAULT 1
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS jokes (
                id SERIAL PRIMARY KEY,
                content TEXT NOT NULL
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS trivias (
                id SERIAL PRIMARY KEY,
                question TEXT NOT NULL,
                answer TEXT NOT NULL,
                hint TEXT
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS memes (
                id SERIAL PRIMARY KEY,
                url TEXT NOT NULL
            )
        """)
        # Tabla de eventos del calendario
        cur.execute("""
            CREATE TABLE IF NOT EXISTS calendar_events (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                event_datetime TIMESTAMP WITH TIME ZONE NOT NULL,
                target_stage INTEGER NOT NULL,
                initial_notified BOOLEAN DEFAULT FALSE
            )
        """)
init_db()

######################################
# CONFIGURACIÓN INICIAL DEL TORNEO
######################################
PREFIX = '!'
# Configuración de etapas
STAGES = {1: 60, 2: 48, 3: 32, 4: 24, 5: 14, 6: 1, 7: 1, 8: 1}
current_stage = 1
stage_names = {
    1: "Battle Royale",
    2: "Snipers vs Runners",
    3: "Boxfight duos",
    4: "Pescadito dice",
    5: "Gran Final",
    6: "CAMPEON",
    7: "FALTA ESCOGER OBJETOS",
    8: "FIN"
}

# Variables para gestionar el reenvío de mensajes del campeón
champion_id = None
forwarding_enabled = False
forwarding_end_time = None

######################################
# VARIABLE GLOBAL PARA TRIVIA
######################################
active_trivia = {}  # key: channel.id, value: {"question": ..., "answer": ..., "hint": ...}

######################################
# FUNCIONES PARA LA BASE DE DATOS
######################################
def get_participant(user_id):
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM registrations WHERE user_id = %s", (user_id,))
        return cur.fetchone()

def get_all_participants():
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM registrations")
        rows = cur.fetchall()
        data = {"participants": {}}
        for row in rows:
            data["participants"][row["user_id"]] = row
        return data

def upsert_participant(user_id, participant):
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO registrations (user_id, discord_name, fortnite_username, platform, country, puntuacion, etapa)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (user_id) DO UPDATE SET
                discord_name = EXCLUDED.discord_name,
                fortnite_username = EXCLUDED.fortnite_username,
                platform = EXCLUDED.platform,
                country = EXCLUDED.country,
                puntuacion = EXCLUDED.puntuacion,
                etapa = EXCLUDED.etapa
        """, (
            user_id,
            participant["discord_name"],
            participant.get("fortnite_username", ""),
            participant.get("platform", ""),
            participant.get("country", ""),
            participant.get("puntuacion", 0),
            participant.get("etapa", current_stage)
        ))

def update_score(user: discord.Member, delta: int):
    user_id = str(user.id)
    participant = get_participant(user_id)
    if participant is None:
        participant = {
            "discord_name": user.display_name,
            "fortnite_username": "",
            "platform": "",
            "country": "",
            "puntuacion": 0,
            "etapa": current_stage
        }
    new_points = int(participant.get("puntuacion", 0)) + delta
    participant["puntuacion"] = new_points
    upsert_participant(user_id, participant)
    return new_points

def award_symbolic_reward(user: discord.Member, reward: int):
    user_id = str(user.id)
    participant = get_participant(user_id)
    if participant is None:
        participant = {
            "discord_name": user.display_name,
            "fortnite_username": "",
            "platform": "",
            "country": "",
            "puntuacion": 0,
            "etapa": current_stage
        }
    current_symbolic = int(participant.get("symbolic", 0))
    new_symbolic = current_symbolic + reward
    participant["symbolic"] = new_symbolic
    upsert_participant(user_id, participant)
    return new_symbolic

######################################
# NORMALIZACIÓN DE CADENAS
######################################
def normalize_string(s):
    return ''.join(c for c in unicodedata.normalize('NFKD', s) if not unicodedata.combining(c)).replace(" ", "").lower()

######################################
# LISTAS DE CHISTES Y TRIVIAS (sin cambios)
######################################
ALL_JOKES = [
    "¿Por qué los programadores confunden Halloween y Navidad? Porque OCT 31 == DEC 25.",
]
unused_jokes = ALL_JOKES.copy()

def get_random_joke():
    global unused_jokes, ALL_JOKES
    if not unused_jokes:
        unused_jokes = ALL_JOKES.copy()
    joke = random.choice(unused_jokes)
    unused_jokes.remove(joke)
    return joke

ALL_TRIVIA = [
    {"question": "¿Quién escribió 'Cien Años de Soledad'?", "answer": "gabriel garcía márquez", "hint": "Comienza con 'Gabriel'."},
    {"question": "¿Cuál es el río más largo del mundo?", "answer": "amazonas", "hint": "Comienza con 'A'."},
]
unused_trivia = ALL_TRIVIA.copy()

def get_random_trivia():
    global unused_trivia, ALL_TRIVIA
    if not unused_trivia:
        unused_trivia = ALL_TRIVIA.copy()
    trivia = random.choice(unused_trivia)
    unused_trivia.remove(trivia)
    return trivia

######################################
# INICIALIZACIÓN DEL BOT
######################################
intents = discord.Intents.default()
intents.members = True  # Asegúrate de tener habilitado "Server Members Intent" en el portal de Discord
intents.messages = True
intents.message_content = True
bot = commands.Bot(command_prefix=PREFIX, intents=intents)

async def send_public_message(message: str, view: discord.ui.View = None):
    public_channel = bot.get_channel(PUBLIC_CHANNEL_ID)
    if public_channel:
        try:
            await public_channel.send(message, view=view)
            await asyncio.sleep(1)  # Añadimos un delay por si se envían múltiples mensajes seguidos
        except discord.HTTPException as e:
            print(f"Error al enviar mensaje público: {e}")
    else:
        print("No se pudo encontrar el canal público.")

######################################
# ENDPOINTS DE LA API PRIVADA
######################################
app = Flask(__name__)

def check_auth(req):
    auth = req.headers.get("Authorization")
    if not auth or auth != f"Bearer {API_SECRET}":
        return False
    return True

@app.route("/", methods=["GET"])
def home_page():
    return "El bot está funcionando!", 200

@app.route("/api/update_points", methods=["POST"])
def api_update_points():
    if not check_auth(request):
        return jsonify({"error": "Unauthorized"}), 401
    data = request.get_json()
    if not data or "member_id" not in data or "points" not in data:
        return jsonify({"error": "Missing parameters"}), 400
    try:
        member_id = int(data["member_id"])
        points = int(data["points"])
    except ValueError:
        return jsonify({"error": "Invalid parameters"}), 400
    guild = bot.get_guild(GUILD_ID)
    if guild is None:
        return jsonify({"error": "Guild not found"}), 404
    try:
        member = guild.get_member(member_id)
        if member is None:
            member = bot.loop.run_until_complete(guild.fetch_member(member_id))
    except Exception as e:
        return jsonify({"error": "Member not found", "details": str(e)}), 404
    new_points = update_score(member, points)
    bot.loop.create_task(send_public_message(f"✅ API: Puntuación actualizada: {member.display_name} ahora tiene {new_points} puntos"))
    return jsonify({"message": "Puntuación actualizada", "new_points": new_points}), 200

@app.route("/api/delete_member", methods=["POST"])
def api_delete_member():
    if not check_auth(request):
        return jsonify({"error": "Unauthorized"}), 401
    data = request.get_json()
    if not data or "member_id" not in data:
        return jsonify({"error": "Missing parameter: member_id"}), 400
    try:
        member_id = int(data["member_id"])
    except ValueError:
        return jsonify({"error": "Invalid member_id"}), 400
    guild = bot.get_guild(GUILD_ID)
    if guild is None:
        return jsonify({"error": "Guild not found"}), 404
    try:
        member = guild.get_member(member_id)
        if member is None:
            member = bot.loop.run_until_complete(guild.fetch_member(member_id))
    except Exception as e:
        return jsonify({"error": "Member not found", "details": str(e)}), 404
    with conn.cursor() as cur:
        cur.execute("DELETE FROM registrations WHERE user_id = %s", (str(member.id),))
    bot.loop.create_task(send_public_message(f"✅ API: {member.display_name} eliminado del torneo"))
    return jsonify({"message": "Miembro eliminado"}), 200

@app.route("/api/set_stage", methods=["POST"])
def api_set_stage():
    if not check_auth(request):
        return jsonify({"error": "Unauthorized"}), 401
    data = request.get_json()
    if not data or "stage" not in data:
        return jsonify({"error": "Missing parameter: stage"}), 400
    try:
        stage = int(data["stage"])
    except ValueError:
        return jsonify({"error": "Invalid stage"}), 400
    global current_stage, champion_id, forwarding_enabled, forwarding_end_time
    current_stage = stage
    if current_stage not in [6,7,8]:
        champion_id = None
        forwarding_enabled = False
        forwarding_end_time = None
    bot.loop.create_task(send_public_message(f"✅ API: Etapa actual configurada a {stage}"))
    return jsonify({"message": "Etapa configurada", "stage": stage}), 200

######################################
# FUNCIONES AUXILIARES
######################################
def is_owner_and_allowed(ctx):
    return ctx.author.id == OWNER_ID and (ctx.channel.id == SPECIAL_HELP_CHANNEL or isinstance(ctx.channel, discord.DMChannel))

######################################
# COMANDOS PRO (SOLO OWNER_ID)
######################################
# Comandos Pro
@bot.command()
async def retroceder_etapa(ctx):
    if not is_owner_and_allowed(ctx):
        return
    global current_stage, champion_id, forwarding_enabled, forwarding_end_time
    if current_stage <= 1:
        await send_public_message("No se puede retroceder de la etapa 1.")
        return
    current_stage -= 1
    data = get_all_participants()
    for uid, player in data["participants"].items():
        player["etapa"] = current_stage
        upsert_participant(uid, player)
        await asyncio.sleep(0.1)  # Delay para evitar solicitudes masivas
    if current_stage not in [6,7,8]:
        champion_id = None
        forwarding_enabled = False
        forwarding_end_time = None
    await send_public_message(f"✅ Etapa retrocedida. Ahora la etapa es {current_stage} ({stage_names.get(current_stage, 'Etapa ' + str(current_stage))}).")

@bot.command()
async def eliminar_jugador(ctx, jugador: str):
    if not is_owner_and_allowed(ctx):
        return
    match = re.search(r'\d+', jugador)
    if not match:
        await send_public_message("No se pudo encontrar al miembro.")
        return
    member_id = int(match.group())
    guild = ctx.guild or bot.get_guild(GUILD_ID)
    if guild is None:
        await send_public_message("No se pudo determinar el servidor.")
        return
    try:
        member = guild.get_member(member_id)
        if member is None:
            member = await guild.fetch_member(member_id)
            await asyncio.sleep(1)  # Delay para evitar solicitudes masivas
    except Exception as e:
        await send_public_message("No se pudo encontrar al miembro en el servidor.")
        return
    user_id = str(member.id)
    with conn.cursor() as cur:
        cur.execute("DELETE FROM registrations WHERE user_id = %s", (user_id,))
    await send_public_message(f"✅ {member.display_name} eliminado del torneo")

@bot.command()
async def configurar_etapa(ctx, etapa: int):
    if not is_owner_and_allowed(ctx):
        return
    global current_stage, champion_id, forwarding_enabled, forwarding_end_time
    current_stage = etapa
    if current_stage not in [6,7,8]:
        champion_id = None
        forwarding_enabled = False
        forwarding_end_time = None
    await send_public_message(f"✅ Etapa actual configurada a {etapa}")

@bot.command()
async def saltar_etapa(ctx, etapa: int):
    if not is_owner_and_allowed(ctx):
        return
    global current_stage, champion_id, forwarding_enabled, forwarding_end_time
    current_stage = etapa
    data = get_all_participants()
    sorted_players = sorted(data["participants"].items(), key=lambda item: int(item[1].get("puntuacion", 0)), reverse=True)
    if sorted_players:
        champ_uid, champ_player = sorted_players[0]
        try:
            guild = ctx.guild or bot.get_guild(GUILD_ID)
            champion = guild.get_member(int(champ_uid)) or await guild.fetch_member(int(champ_uid))
            await asyncio.sleep(1)  # Delay para evitar solicitudes masivas
        except Exception as e:
            champion = None
        if champion:
            if current_stage == 6:
                msg = (f"🏆 ¡Enhorabuena {champion.display_name}! Has sido coronado como el CAMPEON del torneo. "
                       f"Además, has ganado 2800 paVos, que serán entregados en forma de regalos de la tienda de objetos de Fortnite. "
                       f"Puedes escoger los objetos que desees de la tienda, siempre que el valor total de ellos sume 2800. "
                       f"Por favor, escribe en este chat el nombre de los objetos que has escogido (tal como aparecen en la tienda de objetos de Fortnite).")
                champion_id = champion.id
                forwarding_enabled = True
                forwarding_end_time = None
                try:
                    await champion.send(msg)
                    await asyncio.sleep(1)
                except Exception as e:
                    print(f"Error al enviar mensaje al campeón: {e}")
            elif current_stage == 7:
                msg = f"❗ Aún te faltan escoger objetos. Por favor, escribe tus objetos escogidos. 😕"
                champion_id = champion.id
                forwarding_enabled = True
                forwarding_end_time = None
                try:
                    await champion.send(msg)
                    await asyncio.sleep(1)
                except Exception as e:
                    print(f"Error al enviar mensaje al campeón: {e}")
            elif current_stage == 8:
                msg = f"✅ Tus objetos han sido entregados, muchas gracias por participar, nos vemos pronto. 🙌"
                champion_id = champion.id
                forwarding_enabled = True
                forwarding_end_time = datetime.datetime.utcnow() + datetime.timedelta(days=2)
                try:
                    await champion.send(msg)
                    await asyncio.sleep(1)
                except Exception as e:
                    print(f"Error al enviar mensaje al campeón: {e}")
            else:
                champion_id = None
                forwarding_enabled = False
                forwarding_end_time = None
    await send_public_message(f"✅ Etapa saltada. Ahora la etapa es {current_stage} ({stage_names.get(current_stage, 'Etapa ' + str(current_stage))}).")

# Otros comandos Pro relacionados con eventos y registros...
# Aquí irían los demás comandos Pro marcados como # Comandos Pro

######################################
# COMANDOS COMUNES (DISPONIBLES PARA TODOS)
######################################
# Comandos Comunes
@bot.command()
async def trivia(ctx):
    if ctx.channel.id in active_trivia:
        del active_trivia[ctx.channel.id]
    trivia_item = get_random_trivia()
    active_trivia[ctx.channel.id] = trivia_item
    await ctx.send(f"**Trivia:** {trivia_item['question']}\n_Responde en el chat._")

@bot.command()
async def chiste(ctx):
    await ctx.send(get_random_joke())

# Aquí irían los demás comandos comunes marcados como # Comandos Comunes

######################################
# EVENTO ON_MESSAGE: Reenvío de DMs del campeón
######################################
@bot.event
async def on_message(message):
    global forwarding_enabled
    if message.guild is None and champion_id is not None and message.author.id == champion_id and forwarding_enabled:
        if forwarding_end_time is not None and datetime.datetime.utcnow() > forwarding_end_time:
            forwarding_enabled = False
        else:
            try:
                forward_channel = bot.get_channel(1338610365327474690)
                if forward_channel:
                    await forward_channel.send(f"**Mensaje del Campeón:** {message.content}")
                    await asyncio.sleep(1)
            except Exception as e:
                print(f"Error forwarding message: {e}")
    if message.author.bot:
        return
    await bot.process_commands(message)

######################################
# EVENTO ON_READY
######################################
@bot.event
async def on_ready():
    print(f'Bot conectado como {bot.user.name}')

######################################
# SERVIDOR WEB PARA MANTENER EL BOT ACTIVO (API PRIVADA)
######################################
def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

if __name__ == '__main__':
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.start()
    bot.run(os.getenv('DISCORD_TOKEN'))
