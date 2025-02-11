import discord 
import psycopg2
import psycopg2.extras
from discord.ext import commands
from discord.ext.commands import cooldown, BucketType
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
import urllib.parse  # para parsear la URL de la base de datos

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
DATABASE_URL = os.environ.get("DATABASE_URL")

conn = psycopg2.connect(DATABASE_URL)
conn.autocommit = True

def init_db():
    with conn.cursor() as cur:
        # Tabla de registros
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
        # Asegurarse de que la tabla 'registrations' tenga todas las columnas necesarias
        cur.execute("ALTER TABLE registrations ADD COLUMN IF NOT EXISTS discord_name TEXT")
        cur.execute("ALTER TABLE registrations ADD COLUMN IF NOT EXISTS fortnite_username TEXT")
        cur.execute("ALTER TABLE registrations ADD COLUMN IF NOT EXISTS platform TEXT")
        cur.execute("ALTER TABLE registrations ADD COLUMN IF NOT EXISTS country TEXT")
        cur.execute("ALTER TABLE registrations ADD COLUMN IF NOT EXISTS puntuacion INTEGER DEFAULT 0")
        cur.execute("ALTER TABLE registrations ADD COLUMN IF NOT EXISTS etapa INTEGER DEFAULT 1")
        
        # Tabla de chistes: se crea la tabla (si no existe) y se asegura que exista la columna "joke_text"
        cur.execute("""
            CREATE TABLE IF NOT EXISTS jokes (
                id SERIAL PRIMARY KEY
            )
        """)
        cur.execute("""
            ALTER TABLE jokes
            ADD COLUMN IF NOT EXISTS joke_text TEXT NOT NULL DEFAULT ''
        """)
        
        # Tabla de trivias: se crea la tabla (si no existe) y se aseguran las columnas necesarias
        cur.execute("""
            CREATE TABLE IF NOT EXISTS trivias (
                id SERIAL PRIMARY KEY
            )
        """)
        cur.execute("""
            ALTER TABLE trivias
            ADD COLUMN IF NOT EXISTS question TEXT NOT NULL DEFAULT ''
        """)
        cur.execute("""
            ALTER TABLE trivias
            ADD COLUMN IF NOT EXISTS answer TEXT NOT NULL DEFAULT ''
        """)
        # Se reemplaza la columna 'hint' por dos columnas: 'hint1' y 'hint2'
        cur.execute("""
            ALTER TABLE trivias
            ADD COLUMN IF NOT EXISTS hint1 TEXT
        """)
        cur.execute("""
            ALTER TABLE trivias
            ADD COLUMN IF NOT EXISTS hint2 TEXT
        """)
        
        # Tabla de memes
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
    6: "CAMPEÓN",
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
active_trivia = {}  # key: channel.id, value: {"question": ..., "answer": ..., "hint1": ..., "hint2": ...}

# Variables globales para cache de chistes y trivias (para evitar repeticiones hasta agotar la lista)
global_jokes_cache = []
global_trivias_cache = []

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
# FUNCIONES DE CHISTES Y TRIVIAS
######################################
def get_random_joke():
    global global_jokes_cache
    # Si la cache está vacía, cargar todos los chistes desde la base de datos.
    if not global_jokes_cache:
        with conn.cursor() as cur:
            cur.execute("SELECT joke_text FROM jokes")
            rows = cur.fetchall()
            global_jokes_cache = [row[0] for row in rows]
    if global_jokes_cache:
        index = random.randrange(len(global_jokes_cache))
        joke = global_jokes_cache.pop(index)
        return joke
    else:
        return "No tengo chistes para contar ahora mismo."

def add_jokes_bulk(jokes_list):
    with conn.cursor() as cur:
        for joke in jokes_list:
            cur.execute("INSERT INTO jokes (joke_text) VALUES (%s)", (joke,))
            asyncio.sleep(0.1)
    global global_jokes_cache
    global_jokes_cache = []

def delete_all_jokes():
    with conn.cursor() as cur:
        cur.execute("DELETE FROM jokes")
    global global_jokes_cache
    global_jokes_cache = []

def get_random_trivia():
    global global_trivias_cache
    # Si la cache está vacía, cargar todas las trivias desde la base de datos.
    if not global_trivias_cache:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM trivias")
            rows = cur.fetchall()
            global_trivias_cache = rows
    if global_trivias_cache:
        index = random.randrange(len(global_trivias_cache))
        trivia = global_trivias_cache.pop(index)
        return {
            "question": trivia["question"],
            "answer": trivia["answer"],
            "hint1": trivia.get("hint1", ""),
            "hint2": trivia.get("hint2", "")
        }
    else:
        return None

def add_trivias_bulk(trivias_list):
    with conn.cursor() as cur:
        for trivia in trivias_list:
            question = trivia.get("question")
            answer = trivia.get("answer")
            hint1 = trivia.get("hint1", "")
            hint2 = trivia.get("hint2", "")
            cur.execute("INSERT INTO trivias (question, answer, hint1, hint2) VALUES (%s, %s, %s, %s)", (question, answer, hint1, hint2))
            asyncio.sleep(0.1)
    global global_trivias_cache
    global_trivias_cache = []

def delete_all_trivias():
    with conn.cursor() as cur:
        cur.execute("DELETE FROM trivias")
    global global_trivias_cache
    global_trivias_cache = []

######################################
# INICIALIZACIÓN DEL BOT
######################################
intents = discord.Intents.default()
intents.members = True
intents.messages = True
intents.message_content = True
bot = commands.Bot(command_prefix=PREFIX, intents=intents)

async def send_public_message(message: str, view: discord.ui.View = None):
    public_channel = bot.get_channel(PUBLIC_CHANNEL_ID)
    if public_channel:
        try:
            await public_channel.send(message, view=view)
            await asyncio.sleep(1)
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

######################################
# FUNCIONES AUXILIARES
######################################
def is_owner_and_allowed(ctx):
    return ctx.author.id == OWNER_ID and (isinstance(ctx.channel, discord.DMChannel) or ctx.channel.id == SPECIAL_HELP_CHANNEL)

######################################
# COMANDOS PRO (SOLO OWNER_ID)
######################################
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
        await asyncio.sleep(1)
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
            await asyncio.sleep(1)
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
async def registrar_usuario(ctx, *, args: str):
    if not is_owner_and_allowed(ctx):
        return
    parts = args.split('|')
    if len(parts) != 5:
        await ctx.send("❌ Formato incorrecto. Utiliza: !registrar_usuario <Discord ID> | <nombre de discord> | <nombre de Fortnite> | <Plataforma> | <País>")
        return
    discord_id, discord_name, fortnite_username, platform, country = [p.strip() for p in parts]
    participant = {
        "discord_name": discord_name,
        "fortnite_username": fortnite_username,
        "platform": platform,
        "country": country,
        "puntuacion": 0,
        "etapa": current_stage
    }
    upsert_participant(discord_id, participant)
    await ctx.send(f"✅ Usuario {discord_name} registrado correctamente con Discord ID {discord_id}.")
    await asyncio.sleep(1)

@bot.command()
async def borrar_usuario(ctx, user_id: str):
    if not is_owner_and_allowed(ctx):
        return
    with conn.cursor() as cur:
        cur.execute("DELETE FROM registrations WHERE user_id = %s", (user_id,))
    await ctx.send(f"✅ Se ha eliminado el usuario con ID {user_id} del registro.")
    await asyncio.sleep(1)

@bot.command()
async def avanzar_etapa(ctx, etapa: int):
    if not is_owner_and_allowed(ctx):
        return
    global current_stage
    old_stage = current_stage
    current_stage = etapa
    data = get_all_participants()
    limite_jugadores = STAGES.get(etapa, None)
    if limite_jugadores is not None:
        sorted_players = sorted(data["participants"].items(), key=lambda item: int(item[1].get("puntuacion", 0)), reverse=True)
        advanced = sorted_players[:limite_jugadores]
        for user_id, participant in advanced:
            participant["etapa"] = etapa
            upsert_participant(user_id, participant)
            await asyncio.sleep(0.1)
        await ctx.send(f"✅ Etapa actualizada a {etapa}. {limite_jugadores} jugadores han avanzado a esta etapa.")
        # Solo enviar notificaciones si se avanza a una etapa superior
        if etapa > old_stage:
            # Notificar a los jugadores que avanzaron
            for user_id, participant in advanced:
                user = bot.get_user(int(user_id))
                if user is not None:
                    try:
                        await user.send(f"🎉 ¡Felicidades! Has avanzado a la etapa {etapa}.")
                    except Exception as e:
                        print(f"Error enviando DM a {user_id}: {e}")
                await asyncio.sleep(1)
            # Notificar a los jugadores que quedaron fuera (aquellos que siguen en la etapa antigua)
            for user_id, participant in data["participants"].items():
                if participant.get("etapa", old_stage) == old_stage and user_id not in [uid for uid, _ in advanced]:
                    user = bot.get_user(int(user_id))
                    if user is not None:
                        try:
                            await user.send(f"😢 Lamentamos informarte que no has avanzado a la etapa {etapa} y has sido eliminado del torneo.")
                        except Exception as e:
                            print(f"Error enviando DM a {user_id}: {e}")
                    await asyncio.sleep(1)
        await asyncio.sleep(1)
    else:
        await ctx.send(f"❌ La etapa {etapa} no está definida en STAGES.")
        await asyncio.sleep(1)

@bot.command()
async def agregar_chistes_masivos(ctx, *, chistes_texto: str):
    if not is_owner_and_allowed(ctx):
        return
    chistes_lista = [chiste.strip() for chiste in chistes_texto.strip().split('\n') if chiste.strip()]
    if chistes_lista:
        add_jokes_bulk(chistes_lista)
        await ctx.send(f"✅ Se han agregado {len(chistes_lista)} chistes a la base de datos.")
    else:
        await ctx.send("❌ No se encontraron chistes para agregar.")
    await asyncio.sleep(1)

@bot.command()
async def agregar_trivias_masivas(ctx, *, trivias_json: str):
    if not is_owner_and_allowed(ctx):
        return
    try:
        trivias_lista = json.loads(trivias_json)
        if isinstance(trivias_lista, list):
            add_trivias_bulk(trivias_lista)
            await ctx.send(f"✅ Se han agregado {len(trivias_lista)} trivias a la base de datos.")
        else:
            await ctx.send("❌ El formato de las trivias es incorrecto. Debe ser una lista de objetos.")
    except json.JSONDecodeError:
        await ctx.send("❌ Error al procesar el JSON. Asegúrate de que el formato sea correcto.")
    await asyncio.sleep(1)

@bot.command()
async def eliminar_todos_chistes(ctx):
    if not is_owner_and_allowed(ctx):
        return
    delete_all_jokes()
    await ctx.send("✅ Se han eliminado todos los chistes de la base de datos.")
    await asyncio.sleep(1)

@bot.command()
async def eliminar_todas_trivias(ctx):
    if not is_owner_and_allowed(ctx):
        return
    delete_all_trivias()
    await ctx.send("✅ Se han eliminado todas las trivias de la base de datos.")
    await asyncio.sleep(1)

######################################
# COMANDOS COMUNES (DISPONIBLES PARA TODOS)
######################################
@bot.command()
@cooldown(1, 10, BucketType.user)
async def trivia(ctx):
    if ctx.author.bot:
        return
    if ctx.channel.id in active_trivia:
        del active_trivia[ctx.channel.id]
    trivia_item = get_random_trivia()
    if trivia_item:
        active_trivia[ctx.channel.id] = {
            "question": trivia_item["question"],
            "answer": normalize_string(trivia_item["answer"]),
            "hint1": trivia_item.get("hint1", ""),
            "hint2": trivia_item.get("hint2", ""),
            "attempts": {}
        }
        await ctx.send(f"**Trivia:** {trivia_item['question']}\n_Responde en el chat._")
    else:
        await ctx.send("No tengo más trivias disponibles en este momento.")

@bot.command()
@cooldown(1, 10, BucketType.user)
async def chiste(ctx):
    if ctx.author.bot:
        return
    joke = get_random_joke()
    await ctx.send(joke)

@bot.command()
@cooldown(1, 10, BucketType.user)
async def ranking(ctx):
    if ctx.author.bot:
        return
    user_id = str(ctx.author.id)
    participant = get_participant(user_id)
    if participant:
        puntos = participant.get("puntuacion", 0)
        await ctx.send(f"🌟 {ctx.author.display_name}, tienes **{puntos} puntos** en el torneo.")
    else:
        await ctx.send("❌ No estás registrado en el torneo.")

@bot.command()
@cooldown(1, 10, BucketType.user)
async def topmejores(ctx):
    if ctx.author.bot:
        return
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            SELECT fortnite_username, puntuacion
            FROM registrations
            ORDER BY puntuacion DESC
            LIMIT 10
        """)
        top_players = cur.fetchall()
    if top_players:
        lines = ["🏆 **Top 10 Mejores del Torneo:**"]
        for idx, player in enumerate(top_players, start=1):
            lines.append(f"{idx}. {player['fortnite_username']} - {player['puntuacion']} puntos")
        message = "\n".join(lines)
        await ctx.send(message)
    else:
        await ctx.send("No hay participantes en el torneo.")

@bot.listen('on_message')
async def on_message_no_prefix(message):
    if message.author.bot:
        return
    content = message.content.lower().strip()
    ctx = await bot.get_context(message)
    if ctx.valid:
        return
    if content == 'trivia':
        await trivia(ctx)
    elif content == 'chiste':
        await chiste(ctx)
    elif content == 'ranking':
        await ranking(ctx)
    elif content == 'topmejores':
        await topmejores(ctx)
    else:
        await bot.process_commands(message)

######################################
# EVENTO ON_MESSAGE
######################################
@bot.event
async def on_message(message):
    if message.author.bot:
        return
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
    await bot.process_commands(message)
    
    if message.channel.id in active_trivia:
        trivia_data = active_trivia[message.channel.id]
        user_attempts = trivia_data["attempts"].get(message.author.id, 0)
        max_attempts_per_user = 3

        if user_attempts >= max_attempts_per_user:
            # Enviar la notificación de agotamiento de intentos una sola vez.
            if user_attempts == max_attempts_per_user:
                await message.channel.send(f"🚫 {message.author.mention}, has alcanzado el número máximo de intentos para esta trivia.")
                trivia_data["attempts"][message.author.id] = max_attempts_per_user + 1
            return

        normalized_answer = normalize_string(message.content)
        if normalized_answer == trivia_data["answer"]:
            await message.channel.send(f"🎉 ¡Correcto, {message.author.mention}! Has acertado la trivia.")
            await asyncio.sleep(0.5)
            del active_trivia[message.channel.id]
        else:
            trivia_data["attempts"][message.author.id] = user_attempts + 1
            attempts_left = max_attempts_per_user - trivia_data["attempts"][message.author.id]
            hint_message = ""
            if attempts_left == 2:
                hint_message = f" Pista: {trivia_data.get('hint1', '')}"
            elif attempts_left == 1:
                hint_message = f" Pista: {trivia_data.get('hint2', '')}"
            if attempts_left > 0:
                await message.channel.send(f"❌ Respuesta incorrecta, {message.author.mention}. Te quedan {attempts_left} intentos.{hint_message}")
                await asyncio.sleep(0.5)
            else:
                await message.channel.send(f"❌ Has agotado tus intentos, {message.author.mention}.")
                await asyncio.sleep(0.5)

######################################
# EVENTO ON_COMMAND_ERROR
######################################
@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandOnCooldown):
        await ctx.send(f"⏳ Este comando está en cooldown. Por favor, inténtalo de nuevo en {round(error.retry_after, 1)} segundos.")
    else:
        raise error

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
