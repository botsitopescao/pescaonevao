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
#PRIVATE_CHANNEL_ID = 1338130641354620988  # Canal privado para comandos sensibles (no se utiliza en la versión final)
PUBLIC_CHANNEL_ID  = 1338126297666424874  # Canal público donde se muestran resultados sensibles PUNTUACION CHANNEL
SPECIAL_HELP_CHANNEL = 1338747286901100554  # Canal especial para comandos pro COMANDOS PRO CHANNEL
GUILD_ID = 1337387112403697694            # REEMPLAZA con el ID real de tu servidor (guild)

API_SECRET = os.environ.get("API_SECRET")  # Para la API privada (opcional)

######################################
# CONEXIÓN A LA BASE DE DATOS POSTGRESQL
######################################
DATABASE_URL = os.environ.get("DATABASE_URL")  # Usualmente la Internal Database URL de Render
conn = psycopg2.connect(DATABASE_URL)
conn.autocommit = True

def init_db():
    with conn.cursor() as cur:
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

def update_score(user_id: str, delta: int):
    participant = get_participant(user_id)
    if participant is None:
        participant = {
            "discord_name": "Unknown",
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

# Otros endpoints de la API privada (sin cambios)...

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
async def agregar_puntos(ctx, user: discord.User, puntos: int):
    if not is_owner_and_allowed(ctx):
        return
    new_points = update_score(str(user.id), puntos)
    await ctx.send(f"✅ Se han agregado {puntos} puntos a {user.display_name}. Ahora tiene {new_points} puntos.")
    await asyncio.sleep(1)

@bot.command()
async def restar_puntos(ctx, user: discord.User, puntos: int):
    if not is_owner_and_allowed(ctx):
        return
    new_points = update_score(str(user.id), -puntos)
    await ctx.send(f"✅ Se han restado {puntos} puntos a {user.display_name}. Ahora tiene {new_points} puntos.")
    await asyncio.sleep(1)

@bot.command()
async def agregar_puntos_todos(ctx, puntos: int):
    if not is_owner_and_allowed(ctx):
        return
    data = get_all_participants()
    count = 0
    for user_id, participant in data["participants"].items():
        update_score(user_id, puntos)
        count += 1
        await asyncio.sleep(1)  # Delay para evitar exceder límites de solicitudes
    await ctx.send(f"✅ Se han agregado {puntos} puntos a {count} usuarios.")
    await asyncio.sleep(1)

@bot.command()
async def restar_puntos_todos(ctx, puntos: int, etapa: int = None):
    if not is_owner_and_allowed(ctx):
        return
    data = get_all_participants()
    count = 0
    for user_id, participant in data["participants"].items():
        if etapa is None or participant["etapa"] == etapa:
            update_score(user_id, -puntos)
            count += 1
            await asyncio.sleep(1)  # Delay para evitar exceder límites de solicitudes
    await ctx.send(f"✅ Se han restado {puntos} puntos a {count} usuarios{' de la etapa ' + str(etapa) if etapa else ''}.")
    await asyncio.sleep(1)

@bot.command()
async def lista_registrados(ctx):
    if not is_owner_and_allowed(ctx):
        return
    data = get_all_participants()
    lines = ["**Lista de Usuarios Registrados:**"]
    for user_id, participant in data["participants"].items():
        line = f"Discord: {participant['discord_name']} (ID: {user_id}) | Fortnite: {participant['fortnite_username']} | Plataforma: {participant['platform']} | País: {participant['country']} | Puntos: {participant['puntuacion']} | Etapa: {participant['etapa']}"
        lines.append(line)
    full_message = "\n".join(lines)
    await ctx.send(full_message)
    await asyncio.sleep(1)

@bot.command()
async def registrar_usuario(ctx, user: discord.User, fortnite_username: str, platform: str, country: str):
    if not is_owner_and_allowed(ctx):
        return
    participant = {
        "discord_name": user.display_name,
        "fortnite_username": fortnite_username,
        "platform": platform,
        "country": country,
        "puntuacion": 0,
        "etapa": current_stage
    }
    upsert_participant(str(user.id), participant)
    await ctx.send(f"✅ Usuario {user.display_name} registrado correctamente.")
    await asyncio.sleep(1)

@bot.command()
async def avanzar_etapa(ctx, etapa: int):
    if not is_owner_and_allowed(ctx):
        return
    global current_stage
    current_stage = etapa
    data = get_all_participants()
    limite_jugadores = STAGES.get(etapa, None)
    if limite_jugadores is not None:
        # Ordenar participantes por puntuación
        sorted_players = sorted(data["participants"].items(), key=lambda item: int(item[1].get("puntuacion", 0)), reverse=True)
        # Seleccionar los mejores según el límite de jugadores
        seleccionados = sorted_players[:limite_jugadores]
        # Actualizar la etapa de los seleccionados
        for user_id, participant in seleccionados:
            participant["etapa"] = etapa
            upsert_participant(user_id, participant)
            await asyncio.sleep(0.1)
        await ctx.send(f"✅ Etapa actualizada a {etapa}. {limite_jugadores} jugadores han avanzado a esta etapa.")
        await asyncio.sleep(1)
    else:
        await ctx.send(f"❌ La etapa {etapa} no está definida en STAGES.")
        await asyncio.sleep(1)

# Otros comandos Pro relacionados con eventos y registros...
# Aquí irían los demás comandos Pro marcados como # Comandos Pro

######################################
# COMANDOS COMUNES (DISPONIBLES PARA TODOS)
######################################
# Comandos Comunes

@bot.command()
async def trivia_command(ctx):
    if ctx.channel.id in active_trivia:
        del active_trivia[ctx.channel.id]
    trivia_item = get_random_trivia()
    active_trivia[ctx.channel.id] = trivia_item
    await ctx.send(f"**Trivia:** {trivia_item['question']}\n_Responde en el chat._")

@bot.command()
async def chiste_command(ctx):
    await ctx.send(get_random_joke())

# Habilitar comandos sin prefijo '!'
@bot.listen('on_message')
async def on_message_no_prefix(message):
    if message.author.bot:
        return
    content = message.content.lower().strip()
    ctx = await bot.get_context(message)
    if ctx.valid:
        return  # El mensaje ya es un comando válido
    if content == 'trivia':
        await trivia_command(ctx)
    elif content == 'chiste':
        await chiste_command(ctx)
    # Agrega aquí otros comandos comunes sin prefijo
    else:
        await bot.process_commands(message)

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
    # El resto de la lógica se maneja en on_message_no_prefix
    # await bot.process_commands(message)

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
