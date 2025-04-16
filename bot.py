import discord 
import psycopg2
import psycopg2.extras
from psycopg2 import pool
from discord.ext import commands
from discord.ext.commands import cooldown, BucketType
from contextlib import contextmanager
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
from PIL import Image, ImageDraw, ImageFont  # Requiere instalar Pillow (pip install Pillow)
import aiohttp  # Se utiliza para obtener el config.json desde la URL
import aiohttp, async_timeout
from datetime import datetime, timedelta

@contextmanager
def get_db_connection():
    conn = get_conn()  # O la función que uses para obtener la conexión
    try:
        yield conn
    finally:
        db_pool.putconn(conn)

# Mapeo de países a zonas horarias
country_timezones = {
    "Argentina": "America/Argentina/Buenos_Aires",
    "Bolivia": "America/La_Paz",
    "Chile": "America/Santiago",
    "Colombia": "America/Bogota",
    "Costa Rica": "America/Costa_Rica",
    "Cuba": "America/Havana",
    "Ecuador": "America/Guayaquil",
    "El Salvador": "America/El_Salvador",
    "España": "Europe/Madrid",
    "Guatemala": "America/Guatemala",
    "Honduras": "America/Tegucigalpa",
    "México": "America/Mexico_City",
    "Nicaragua": "America/Managua",
    "Panamá": "America/Panama",
    "Paraguay": "America/Asuncion",
    "Perú": "America/Lima",
    "Puerto Rico": "America/Puerto_Rico",
    "República Dominicana": "America/Santo_Domingo",
    "Uruguay": "America/Montevideo",
    "Venezuela": "America/Caracas",
    "Guinea Ecuatorial": "Africa/Malabo"
}

######################################
# CONFIGURACIÓN: IDs y Servidor
######################################
OWNER_ID = 1336609089656197171         # Tu Discord ID (único autorizado para comandos sensibles)
#PRIVATE_CHANNEL_ID = 1338130641354620988
PUBLIC_CHANNEL_ID  = 1338126297666424874
SPECIAL_HELP_CHANNEL = 1338747286901100554
GUILD_ID = 1337387112403697694
GENERAL_CHANNEL_ID = 1337387113444020257

API_SECRET = os.environ.get("API_SECRET")  # Para la API privada (opcional)

######################################
# CONEXIÓN A LA BASE DE DATOS POSTGRESQL
######################################
DATABASE_URL = os.environ.get("DATABASE_URL")
db_pool = pool.SimpleConnectionPool(1, 10, DATABASE_URL)
conn = db_pool.getconn()

def get_conn():
    global conn
    if conn.closed:
        conn = db_pool.getconn()
    return conn

######################################
# INICIALIZACIÓN DE LA BASE DE DATOS
######################################
def init_db():
    try:
        with get_db_connection() as conn:  # Usa el context manager para asegurar que se libere la conexión
            with conn.cursor() as cur:
                # Desactivar el timeout para esta sesión (0 = sin límite)
                cur.execute("SET statement_timeout = 0;")
                
                # Ejecuta la creación de la tabla si no existe
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS registrations (
                        user_id TEXT PRIMARY KEY,
                        discord_name TEXT,
                        fortnite_username TEXT,
                        platform TEXT,
                        country TEXT,
                        puntuacion INTEGER DEFAULT 0,
                        etapa INTEGER DEFAULT 1,
                        grupo INTEGER DEFAULT 0,
                        experiencia INTEGER DEFAULT 0,
                        nivel INTEGER DEFAULT 1,
                        team_members TEXT DEFAULT ''  -- Se agrega la columna para los miembros del equipo
                    )
                """)
                # Se realizan los ALTER TABLE para asegurar que existan las columnas requeridas
                cur.execute("ALTER TABLE registrations ADD COLUMN IF NOT EXISTS discord_name TEXT")
                cur.execute("ALTER TABLE registrations ADD COLUMN IF NOT EXISTS fortnite_username TEXT")
                cur.execute("ALTER TABLE registrations ADD COLUMN IF NOT EXISTS platform TEXT")
                cur.execute("ALTER TABLE registrations ADD COLUMN IF NOT EXISTS country TEXT")
                cur.execute("ALTER TABLE registrations ADD COLUMN IF NOT EXISTS puntuacion INTEGER DEFAULT 0")
                cur.execute("ALTER TABLE registrations ADD COLUMN IF NOT EXISTS etapa INTEGER DEFAULT 1")
                cur.execute("ALTER TABLE registrations ADD COLUMN IF NOT EXISTS grupo INTEGER DEFAULT 0")
                cur.execute("ALTER TABLE registrations ADD COLUMN IF NOT EXISTS experiencia INTEGER DEFAULT 0")
                cur.execute("ALTER TABLE registrations ADD COLUMN IF NOT EXISTS nivel INTEGER DEFAULT 1")
                cur.execute("ALTER TABLE registrations ADD COLUMN IF NOT EXISTS team_members TEXT DEFAULT ''")
            conn.commit()
        print("Base de datos inicializada correctamente.")
    except Exception as e:
        print("Error en init_db:", e)
        
        # Tabla de chistes
        cur.execute("""
            CREATE TABLE IF NOT EXISTS jokes (
                id SERIAL PRIMARY KEY
            )
        """)
        cur.execute("""
            ALTER TABLE jokes
            ADD COLUMN IF NOT EXISTS joke_text TEXT NOT NULL DEFAULT ''
        """)
        
        # Tabla de trivias (dos pistas)
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
        
        # Tabla de eventos del calendario, en zona horaria de Perú
        cur.execute("""
            CREATE TABLE IF NOT EXISTS calendar_events (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                event_datetime TIMESTAMP WITH TIME ZONE NOT NULL,
                target_stage INTEGER NOT NULL,
                notified_10h BOOLEAN DEFAULT FALSE,
                notified_2h BOOLEAN DEFAULT FALSE
            )
        """)
        cur.execute("ALTER TABLE calendar_events ADD COLUMN IF NOT EXISTS name TEXT NOT NULL DEFAULT ''")
        cur.execute("ALTER TABLE calendar_events ADD COLUMN IF NOT EXISTS event_datetime TIMESTAMP WITH TIME ZONE NOT NULL")
        cur.execute("ALTER TABLE calendar_events ADD COLUMN IF NOT EXISTS target_stage INTEGER NOT NULL DEFAULT 0")
        cur.execute("ALTER TABLE calendar_events ADD COLUMN IF NOT EXISTS notified_10h BOOLEAN DEFAULT FALSE")
        cur.execute("ALTER TABLE calendar_events ADD COLUMN IF NOT EXISTS notified_2h BOOLEAN DEFAULT FALSE")
        cur.execute("ALTER TABLE calendar_events ADD COLUMN IF NOT EXISTS target_group INTEGER NOT NULL DEFAULT 0")
        cur.execute("ALTER TABLE calendar_events ADD COLUMN IF NOT EXISTS notified_10m BOOLEAN DEFAULT FALSE")
        cur.execute("ALTER TABLE calendar_events ADD COLUMN IF NOT EXISTS notified_2m BOOLEAN DEFAULT FALSE")
    get_conn().commit()

init_db()

######################################
# VARIABLES GLOBALES ADICIONALES
######################################
dm_forwarding = {}  # Diccionario: user_id (str) -> None o datetime

######################################
# CONFIGURACIÓN INICIAL DEL TORNEO
######################################
PREFIX = '!'
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

champion_id = None
forwarding_enabled = False
forwarding_end_time = None

######################################
# VARIABLE GLOBAL PARA TRIVIA
######################################
active_trivia = {}

global_jokes_cache = []
global_trivias_cache = []
global_triviafortnite_cache = []

# VARIABLE GLOBAL PARA EL CONFIG DEL TORNEO
# Valores posibles: "solo", "duos", "trios", "escuadrones"
tournament_mode = "solo"

######################################
# FUNCIONES PARA LA BASE DE DATOS
######################################
def get_participant(user_id):
    with get_conn().cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute("SELECT * FROM registrations WHERE user_id = %s", (user_id,))
        return cur.fetchone()

def get_all_participants():
    with get_conn().cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute("SELECT * FROM registrations")
        rows = cur.fetchall()
        data = {"participants": {}}
        for row in rows:
            data["participants"][row["user_id"]] = row
        return data

def upsert_participant(user_id, participant):
    with get_conn().cursor() as cur:
        cur.execute("""
            INSERT INTO registrations (user_id, discord_name, fortnite_username, platform, country, puntuacion, etapa, experiencia, nivel, team_members)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (user_id) DO UPDATE SET
                discord_name = EXCLUDED.discord_name,
                fortnite_username = EXCLUDED.fortnite_username,
                platform = EXCLUDED.platform,
                country = EXCLUDED.country,
                puntuacion = EXCLUDED.puntuacion,
                etapa = EXCLUDED.etapa,
                experiencia = EXCLUDED.experiencia,
                nivel = EXCLUDED.nivel,
                team_members = EXCLUDED.team_members
        """, (
            user_id,
            participant["discord_name"],
            participant.get("fortnite_username", ""),
            participant.get("platform", ""),
            participant.get("country", ""),
            participant.get("puntuacion", 0),
            participant.get("etapa", current_stage),
            participant.get("experiencia", 0),
            participant.get("nivel", 1),
            participant.get("team_members", "")
        ))
    get_conn().commit()

def update_score(user_id: str, delta: int):
    participant = get_participant(user_id)
    if participant is None:
        participant = {
            "discord_name": "Unknown",
            "fortnite_username": "",
            "platform": "",
            "country": "",
            "puntuacion": 0,
            "etapa": current_stage,
            "experiencia": 0,
            "nivel": 1,
            "team_members": ""
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
    if not global_jokes_cache:
        with get_conn().cursor() as cur:
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
    with get_conn().cursor() as cur:
        for joke in jokes_list:
            cur.execute("INSERT INTO jokes (joke_text) VALUES (%s)", (joke,))
            asyncio.sleep(0.1)
    global global_jokes_cache
    global_jokes_cache = []
    get_conn().commit()

def delete_all_jokes():
    with get_conn().cursor() as cur:
        cur.execute("DELETE FROM jokes")
    global global_jokes_cache
    global_jokes_cache = []
    get_conn().commit()

def get_random_trivia():
    global global_trivias_cache
    if not global_trivias_cache:
        with get_conn().cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
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
    with get_conn().cursor() as cur:
        for trivia in trivias_list:
            question = trivia.get("question")
            answer = trivia.get("answer")
            hint1 = trivia.get("hint1", "")
            hint2 = trivia.get("hint2", "")
            cur.execute("INSERT INTO trivias (question, answer, hint1, hint2) VALUES (%s, %s, %s, %s)", (question, answer, hint1, hint2))
            asyncio.sleep(0.1)
    global global_trivias_cache
    global_trivias_cache = []
    get_conn().commit()

def delete_all_trivias():
    with get_conn().cursor() as cur:
        cur.execute("DELETE FROM trivias")
    global global_trivias_cache
    global_trivias_cache = []
    get_conn().commit()

# NUEVA FUNCIÓN PARA AGREGAR TRIVIAS FORTNITE
def add_triviasfortnite_bulk(trivias_list):
    with get_conn().cursor() as cur:
        for trivia in trivias_list:
            question = trivia.get("question")
            answer = trivia.get("answer")
            hint1 = trivia.get("hint1", "")
            hint2 = trivia.get("hint2", "")
            cur.execute("INSERT INTO triviafortnite (question, answer, hint1, hint2) VALUES (%s, %s, %s, %s)", (question, answer, hint1, hint2))
            asyncio.sleep(0.1)
    global global_triviafortnite_cache
    global_triviafortnite_cache = []
    get_conn().commit()

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
# ENDPOINTS DE LA API REST
######################################

# 1. Gestión de Usuarios

# a) Consultar la lista completa de registros
@app.route("/api/registrados", methods=["GET"])
def api_registrados():
    if not check_auth(request):
        return jsonify({"error": "No autorizado"}), 401
    data = get_all_participants()  # Devuelve un diccionario con la clave "participants"
    return jsonify(data)

# b) Consultar una lista "simplificada" (por ejemplo, solo user_id, discord_name y puntuacion)
@app.route("/api/puntos", methods=["GET"])
def api_puntos():
    if not check_auth(request):
        return jsonify({"error": "No autorizado"}), 401
    data = get_all_participants()
    participantes = data.get("participants", {})
    puntos_simplificados = []
    for user_id, participant in participantes.items():
        puntos_simplificados.append({
            "user_id": user_id,
            "discord_name": participant.get("discord_name", ""),
            "puntuacion": participant.get("puntuacion", 0)
        })
    return jsonify({"participants": puntos_simplificados})

# c) Agregar o actualizar (upsert) un usuario
@app.route("/api/usuarios", methods=["POST"])
def api_registrar_usuario():
    if not check_auth(request):
        return jsonify({"error": "No autorizado"}), 401

    data = request.get_json()
    required_fields = ["user_id", "discord_name", "fortnite_username", "platform", "country"]
    if not data or not all(field in data for field in required_fields):
        return jsonify({
            "error": "Datos inválidos. Se requieren los campos 'user_id', 'discord_name', 'fortnite_username', 'platform' y 'country'."
        }), 400

    participant = {
        "discord_name": data["discord_name"],
        "fortnite_username": data["fortnite_username"],
        "platform": data["platform"],
        "country": data["country"],
        "puntuacion": data.get("puntuacion", 0),
        "etapa": data.get("etapa", current_stage),
        "experiencia": data.get("experiencia", 0),
        "nivel": data.get("nivel", 1),
        "team_members": data.get("team_members", "")
    }

    try:
        upsert_participant(data["user_id"], participant)
        return jsonify({"mensaje": f"Usuario {data['discord_name']} registrado/actualizado correctamente."}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# d) Eliminar un usuario
@app.route("/api/usuarios/<user_id>", methods=["DELETE"])
def api_eliminar_usuario(user_id):
    if not check_auth(request):
        return jsonify({"error": "No autorizado"}), 401

    try:
        with get_conn().cursor() as cur:
            cur.execute("DELETE FROM registrations WHERE user_id = %s", (user_id,))
        get_conn().commit()
        return jsonify({"mensaje": f"Usuario con ID {user_id} eliminado."}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# 2. Gestión de Puntos

# Actualizar (sumar/restar) puntos de un participante
@app.route("/api/puntos", methods=["POST"])
def api_actualizar_puntos():
    if not check_auth(request):
        return jsonify({"error": "No autorizado"}), 401

    data = request.get_json()
    if not data or "user_id" not in data or "delta" not in data:
        return jsonify({"error": "Datos inválidos. Se requieren 'user_id' y 'delta'."}), 400

    user_id = data["user_id"]
    delta = data["delta"]

    try:
        nuevos_puntos = update_score(user_id, delta)
        return jsonify({"user_id": user_id, "nuevos_puntos": nuevos_puntos}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# 3. Gestión de Chistes

# a) Listar chistes
@app.route("/api/chistes", methods=["GET"])
def api_chistes():
    if not check_auth(request):
        return jsonify({"error": "No autorizado"}), 401

    with get_conn().cursor() as cur:
        cur.execute("SELECT id, joke_text FROM jokes")
        rows = cur.fetchall()
    chistes = [{"id": row[0], "joke_text": row[1]} for row in rows]
    return jsonify({"chistes": chistes})

# b) Agregar chistes en bloque (en masa)
@app.route("/api/chistes", methods=["POST"])
def api_agregar_chistes():
    if not check_auth(request):
        return jsonify({"error": "No autorizado"}), 401

    data = request.get_json()
    if not data or "chistes" not in data:
        return jsonify({"error": "Se requiere un campo 'chistes' que contenga una lista de chistes."}), 400

    chistes_lista = data["chistes"]
    try:
        add_jokes_bulk(chistes_lista)
        return jsonify({"mensaje": f"Se han agregado {len(chistes_lista)} chistes."}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# c) Eliminar todos los chistes
@app.route("/api/chistes", methods=["DELETE"])
def api_eliminar_chistes():
    if not check_auth(request):
        return jsonify({"error": "No autorizado"}), 401

    try:
        delete_all_jokes()
        return jsonify({"mensaje": "Se han eliminado todos los chistes."}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# 4. Gestión de Trivias

# a) Listar trivias
@app.route("/api/trivias", methods=["GET"])
def api_trivias():
    if not check_auth(request):
        return jsonify({"error": "No autorizado"}), 401

    with get_conn().cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute("SELECT id, question, answer, hint1, hint2 FROM trivias")
        rows = cur.fetchall()
    trivias = []
    for row in rows:
        trivias.append({
            "id": row["id"],
            "question": row["question"],
            "answer": row["answer"],
            "hint1": row.get("hint1", ""),
            "hint2": row.get("hint2", "")
        })
    return jsonify({"trivias": trivias})

# b) Agregar trivias en bloque (en masa)
@app.route("/api/trivias", methods=["POST"])
def api_agregar_trivias():
    if not check_auth(request):
        return jsonify({"error": "No autorizado"}), 401

    data = request.get_json()
    if not data or "trivias" not in data:
        return jsonify({"error": "Se requiere un campo 'trivias' que contenga una lista de trivias."}), 400

    trivias_lista = data["trivias"]
    try:
        add_trivias_bulk(trivias_lista)
        return jsonify({"mensaje": f"Se han agregado {len(trivias_lista)} trivias."}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# c) Eliminar todas las trivias
@app.route("/api/trivias", methods=["DELETE"])
def api_eliminar_trivias():
    if not check_auth(request):
        return jsonify({"error": "No autorizado"}), 401

    try:
        delete_all_trivias()
        return jsonify({"mensaje": "Se han eliminado todas las trivias."}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

######################################
# FUNCIONES AUXILIARES
######################################
def is_owner_and_allowed(ctx):
    return ctx.author.id == OWNER_ID and (isinstance(ctx.channel, discord.DMChannel) or ctx.channel.id == SPECIAL_HELP_CHANNEL)

# Función auxiliar para determinar el team líder en modos de equipo
def get_team_leader(user_id: str, all_participants: dict):
    # Si el usuario tiene team_members no vacíos, es líder
    participant = all_participants.get(user_id)
    if participant:
        tm = participant.get("team_members", "")
        if tm is None:
            tm = ""
        if tm.strip() != "":
            return participant
    # Si no, se busca si su user_id aparece en algún team_members de algún líder
    for pid, part in all_participants.items():
        if part is None:
            continue
        tm = part.get("team_members", "")
        if tm is None:
            tm = ""
        tm = tm.strip()
        if tm:
            members = [m.strip() for m in tm.split(",") if m.strip() != ""]
            if user_id in members:
                return part
    return None

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
async def mensaje(ctx, *, content: str):
    if ctx.author.id != OWNER_ID:
        return
    target_channel = bot.get_channel(1337387113444020257)
    if target_channel is not None:
        await target_channel.send(content)
    else:
        await ctx.send("No se encontró el canal destino.")
    await asyncio.sleep(1)

@bot.command()
async def lista_registrados(ctx):
    if not is_owner_and_allowed(ctx):
        return
    data = get_all_participants()
    lines = ["**Lista de Usuarios Registrados:**"]
    for user_id, participant in data["participants"].items():
        line = (
            f"Discord: {participant['discord_name']} (ID: {user_id}) | Fortnite: {participant['fortnite_username']} | "
            f"Plataforma: {participant['platform']} | País: {participant['country']} | Puntos: {participant['puntuacion']} | "
            f"Etapa: {participant['etapa']} | Grupo: {participant.get('grupo', 'N/A')}"
        )
        lines.append(line)
    full_message = "\n".join(lines)
    
    # Dividir el mensaje en fragmentos de hasta 2000 caracteres
    max_length = 2000
    for i in range(0, len(full_message), max_length):
        await ctx.send(full_message[i:i+max_length])
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
        "etapa": current_stage,
        "experiencia": 0,
        "nivel": 1,
        "team_members": ""
    }
    upsert_participant(discord_id, participant)
    await ctx.send(f"✅ Usuario {discord_name} registrado correctamente con Discord ID {discord_id}.")
    await asyncio.sleep(1)

@bot.command()
async def borrar_usuario(ctx, user_id: str):
    if not is_owner_and_allowed(ctx):
        return
    with get_conn().cursor() as cur:
        cur.execute("DELETE FROM registrations WHERE user_id = %s", (user_id,))
    get_conn().commit()
    await ctx.send(f"✅ Se ha eliminado el usuario con ID {user_id} del registro.")
    await asyncio.sleep(1)

@bot.command()
async def asignadomanual(ctx, user_id: str, stage: int, group: int):
    if not is_owner_and_allowed(ctx):
        return
    participant = get_participant(user_id)
    if not participant:
        await ctx.send("❌ Usuario no registrado en el torneo.")
        return
    old_stage = int(participant.get("etapa", 1))
    with get_conn().cursor() as cur:
        cur.execute("UPDATE registrations SET etapa = %s, grupo = %s WHERE user_id = %s", (stage, group, user_id))
    get_conn().commit()
    participant["etapa"] = stage
    participant["grupo"] = group
    upsert_participant(user_id, participant)
    user = bot.get_user(int(user_id))
    if user is not None:
        try:
            if stage == 6:
                msg = "🏆 ¡Felicidades! Eres el campeón del torneo y acabas de ganar 2800 paVos que se te entregarán en forma de regalos de la tienda de objetos de Fortnite, así que envíame los nombres de los objetos que quieres que te regale que sumen 2800 paVos."
                dm_forwarding[str(user_id)] = None
            elif stage == 7:
                msg = "🎁 Todavía te quedan objetos por escoger para completar tu premio de 2800 paVos."
                dm_forwarding[str(user_id)] = None
            elif stage == 8:
                msg = "🥇 Tus objetos han sido entregados campeón, muchas gracias por participar, has sido el mejor pescadito del torneo, nos vemos pronto."
                dm_forwarding[str(user_id)] = datetime.datetime.utcnow() + datetime.timedelta(days=2)
            else:
                msg = f"🎉 ¡Felicidades! Has avanzado a la etapa {stage}."
            await user.send(msg)
        except Exception as e:
            print(f"Error enviando DM a {user_id}: {e}")
    await ctx.send(f"✅ Usuario {user_id} asignado a la etapa {stage} y grupo {group}.")
    await asyncio.sleep(1)

@bot.command(aliases=["agregar_evento"])
async def crear_evento(ctx, fase: int, grupo: int, date: str, time: str, *, event_name: str):
    if not is_owner_and_allowed(ctx):
        return
    dt_str = f"{date} {time}"
    try:
        event_dt = datetime.datetime.strptime(dt_str, "%d/%m/%Y %H:%M")
        event_dt = event_dt.replace(tzinfo=ZoneInfo("America/Lima"))
    except Exception as e:
        await ctx.send("❌ Formato de fecha u hora incorrecto. Usa dd/mm/aaaa hh:mm")
        return
    with get_conn().cursor() as cur:
        cur.execute(
            "INSERT INTO calendar_events (event_time, description, event_datetime, name, target_stage, target_group, notified_10h, notified_2h, notified_10m, notified_2m) VALUES (%s, %s, %s, %s, %s, %s, FALSE, FALSE, FALSE, FALSE)",
            (event_dt, "", event_dt, event_name, fase, grupo)
        )
    get_conn().commit()
    await ctx.send(f"✅ Evento '{event_name}' creado para la fase {fase} y grupo {grupo} para el {dt_str}.")

@bot.command()
async def ver_eventos(ctx):
    if not is_owner_and_allowed(ctx):
        return
    # Se obtiene la zona horaria registrada del usuario; si no se encuentra, se usa la hora de Perú
    participant = get_participant(str(ctx.author.id))
    if participant and participant.get("country"):
        user_tz = ZoneInfo(country_timezones.get(participant.get("country"), "America/Lima"))
    else:
        user_tz = ZoneInfo("America/Lima")
    with get_conn().cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute("SELECT id, name, event_datetime, target_stage, target_group FROM calendar_events ORDER BY id")
        events = cur.fetchall()
    if events:
        lines = ["**Eventos en el Calendario:**"]
        for ev in events:
            dt = ev["event_datetime"]
            # Convertir la hora del evento (almacenada en hora peruana) a la zona horaria del usuario
            dt_converted = dt.astimezone(user_tz)
            date_str = dt_converted.strftime("%d/%m/%Y")
            time_str = dt_converted.strftime("%H:%M")
            lines.append(f"ID: {ev['id']} - {date_str} {time_str} - {ev['name']} - Fase: {ev['target_stage']} - Grupo: {ev['target_group']}")
        await ctx.send("\n".join(lines))
    else:
        await ctx.send("No hay eventos en el calendario.")

@bot.command()
async def borrar_evento(ctx, event_id: int):
    if not is_owner_and_allowed(ctx):
        return
    with get_conn().cursor() as cur:
        cur.execute("DELETE FROM calendar_events WHERE id = %s", (event_id,))
        if cur.rowcount > 0:
            await ctx.send(f"✅ Evento con ID {event_id} borrado.")
        else:
            await ctx.send(f"❌ No se encontró un evento con ID {event_id}.")
    get_conn().commit()

# ----------------- MODIFICACIONES PARA TOURNAMENT_MODE (solo vs equipos) -----------------

@bot.command()
async def avanzar_etapa(ctx, etapa: int):
    if not is_owner_and_allowed(ctx):
        return
    global current_stage, tournament_mode
    old_stage = current_stage
    current_stage = etapa
    data = get_all_participants()
    limite = STAGES.get(etapa, None)
    if limite is None:
        await ctx.send(f"❌ La etapa {etapa} no está definida en STAGES.")
        await asyncio.sleep(1)
        return

    # Modo SOLO: función original
    if tournament_mode == "solo":
        sorted_players = sorted(data["participants"].items(), key=lambda item: int(item[1].get("puntuacion", 0)), reverse=True)
        advanced = sorted_players[:limite]
        for user_id, participant in advanced:
            participant["etapa"] = etapa
            upsert_participant(user_id, participant)
            await asyncio.sleep(1)
        if etapa in [1, 2, 3, 4, 5]:
            if etapa == 1:
                num_groups = 4
            elif etapa == 2:
                num_groups = 4
            elif etapa == 3:
                num_groups = 4
            elif etapa == 4:
                num_groups = 2
            elif etapa == 5:
                num_groups = 1
            else:
                num_groups = 1
            advanced_shuffled = advanced[:]
            random.shuffle(advanced_shuffled)
            group_size = limite // num_groups
            for i, (user_id, participant) in enumerate(advanced_shuffled):
                participant["grupo"] = (i // group_size) + 1
                upsert_participant(user_id, participant)
                await asyncio.sleep(1)
        await ctx.send(f"✅ Etapa actualizada a {etapa}. {limite} jugadores han avanzado a esta etapa.")
        if etapa > old_stage:
            for user_id, participant in advanced:
                user = bot.get_user(int(user_id))
                if user is not None:
                    try:
                        if etapa == 6:
                            msg = "🏆 ¡Felicidades! Eres el campeón del torneo y acabas de ganar 2800 paVos..."
                            dm_forwarding[str(user_id)] = None
                        elif etapa == 7:
                            msg = "🎁 Todavía te quedan objetos por escoger para completar tu premio de 2800 paVos."
                            dm_forwarding[str(user_id)] = None
                        elif etapa == 8:
                            msg = "🥇 Tus objetos han sido entregados campeón, muchas gracias por participar..."
                            dm_forwarding[str(user_id)] = datetime.datetime.utcnow() + datetime.timedelta(days=2)
                        else:
                            msg = f"🎉 ¡Felicidades! Has avanzado a la etapa {etapa}."
                        await user.send(msg)
                    except Exception as e:
                        print(f"Error enviando DM a {user_id}: {e}")
                    await asyncio.sleep(1)
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
        # Modos de equipo: duos, trios, escuadrones
        # Determinar el tamaño requerido del equipo
        if tournament_mode == "duos":
            required_team_members = 1  # además del líder
        elif tournament_mode == "trios":
            required_team_members = 2
        elif tournament_mode == "escuadrones":
            required_team_members = 3
        else:
            await ctx.send("❌ tournament_mode desconocido.")
            return

        # Construir equipos completos: solo se consideran registros donde el campo team_members tenga la cantidad requerida
        equipos = []
        for uid, participant in data["participants"].items():
            if participant is None:
                continue
            tm = participant.get("team_members", "")
            if tm is None:
                tm = ""
            tm = tm.strip()
            # Solo se toman equipos donde el líder tenga team_members y se ignoran a quienes aparezcan como miembro
            if tm != "":
                members = [m.strip() for m in tm.split(",") if m.strip() != ""]
                if len(members) == required_team_members:
                    equipos.append((uid, participant, members))
        # Ordenar equipos por la puntuación del líder (descendente)
        equipos.sort(key=lambda tup: int(tup[1].get("puntuacion", 0)), reverse=True)
        # Seleccionar los equipos que avanzan (se requieren 'limite' equipos)
        equipos_avanzados = equipos[:limite]
        # Actualizar la etapa para cada equipo completo: líder y sus miembros
        for leader_id, leader_part, members in equipos_avanzados:
            leader_part["etapa"] = etapa
            upsert_participant(leader_id, leader_part)
            await asyncio.sleep(1)
            for member_id in members:
                member = get_participant(member_id)
                if member:
                    member["etapa"] = etapa
                    upsert_participant(member_id, member)
                await asyncio.sleep(1)
        # Asignación de grupos a nivel de equipos (se conserva la misma lógica que en solo, pero por equipos)
        if etapa in [1, 2, 3, 4, 5]:
            if etapa == 1:
                num_groups = 4
            elif etapa == 2:
                num_groups = 4
            elif etapa == 3:
                num_groups = 4
            elif etapa == 4:
                num_groups = 2
            elif etapa == 5:
                num_groups = 1
            else:
                num_groups = 1
            equipos_shuffled = equipos_avanzados[:]
            random.shuffle(equipos_shuffled)
            group_size = limite // num_groups if num_groups > 0 else 1
            for i, (leader_id, leader_part, members) in enumerate(equipos_shuffled):
                grupo = (i // group_size) + 1
                leader_part["grupo"] = grupo
                upsert_participant(leader_id, leader_part)
                await asyncio.sleep(1)
                # Actualizar también el grupo para cada miembro
                for member_id in members:
                    member = get_participant(member_id)
                    if member:
                        member["grupo"] = grupo
                        upsert_participant(member_id, member)
                    await asyncio.sleep(1)
        await ctx.send(f"✅ Etapa actualizada a {etapa}. {len(equipos_avanzados)} equipos han avanzado a esta etapa.")
        # Enviar mensajes directos según avance o eliminación para equipos
        if etapa > old_stage:
            for leader_id, leader_part, members in equipos_avanzados:
                user = bot.get_user(int(leader_id))
                if user is not None:
                    try:
                        if etapa == 6:
                            msg = "🏆 ¡Felicidades! Eres el campeón del torneo y has ganado 2800 paVos..."
                            dm_forwarding[str(leader_id)] = None
                        elif etapa == 7:
                            msg = "🎁 Todavía te quedan objetos por escoger para completar tu premio de 2800 paVos."
                            dm_forwarding[str(leader_id)] = None
                        elif etapa == 8:
                            msg = "🥇 Tus objetos han sido entregados campeón, muchas gracias por participar..."
                            dm_forwarding[str(leader_id)] = datetime.datetime.utcnow() + datetime.timedelta(days=2)
                        else:
                            msg = f"🎉 ¡Felicidades! Has avanzado a la etapa {etapa}."
                        await user.send(msg)
                    except Exception as e:
                        print(f"Error enviando DM a {leader_id}: {e}")
                    await asyncio.sleep(1)
                    # Enviar el mismo mensaje a cada miembro del equipo
                    for member_id in members:
                        member_user = bot.get_user(int(member_id))
                        if member_user is not None:
                            try:
                                await member_user.send(msg)
                            except Exception as e:
                                print(f"Error enviando DM a {member_id}: {e}")
                        await asyncio.sleep(1)
            # Notificar a equipos completos no seleccionados
            # Se recorre todos los equipos completos y se notifica a quienes no avanzaron
            avanzados_set = set([leader_id for leader_id,_,_ in equipos_avanzados])
            for leader_id, leader_part, members in equipos:
                if leader_id not in avanzados_set:
                    user = bot.get_user(int(leader_id))
                    if user is not None:
                        try:
                            await user.send(f"😢 Lamentamos informarte que no has avanzado a la etapa {etapa} y tu equipo ha sido eliminado del torneo.")
                        except Exception as e:
                            print(f"Error enviando DM a {leader_id}: {e}")
                    await asyncio.sleep(1)
                    for member_id in members:
                        member_user = bot.get_user(int(member_id))
                        if member_user is not None:
                            try:
                                await member_user.send(f"😢 Lamentamos informarte que no has avanzado a la etapa {etapa} y tu equipo ha sido eliminado del torneo.")
                            except Exception as e:
                                print(f"Error enviando DM a {member_id}: {e}")
                        await asyncio.sleep(1)
        await asyncio.sleep(1)

# ----------------- COMANDOS COMUNES MODIFICADOS SEGÚN TOURNAMENT_MODE -----------------

@bot.command()
@cooldown(1, 10, BucketType.user)
async def ranking(ctx):
    # Para modos de equipo: si el usuario es miembro se consulta el registro de su líder
    global tournament_mode
    if tournament_mode == "solo":
        user_id = str(ctx.author.id)
        participant = get_participant(user_id)
        if participant:
            puntos = participant.get("puntuacion", 0)
            await ctx.send(f"🌟 {ctx.author.display_name}, tienes **{puntos} puntos** en el torneo.")
        else:
            await ctx.send("❌ No estás registrado en el torneo.")
    else:
        # Modo equipos
        user_id = str(ctx.author.id)
        all_parts = get_all_participants()["participants"]
        leader = None
        part = all_parts.get(user_id)
        # Si el usuario es líder (team_members no vacío)
        if part is not None:
            tm = part.get("team_members", "")
            if tm is None:
                tm = ""
            if tm.strip() != "":
                leader = part
            else:
                leader = get_team_leader(user_id, all_parts)
        else:
            # Buscar si el usuario es miembro
            leader = get_team_leader(user_id, all_parts)
        if leader:
            puntos = leader.get("puntuacion", 0)
            # Mostrar el fortnite_username correspondiente al líder
            await ctx.send(f"🌟 {leader.get('fortnite_username')}, tu equipo tiene **{puntos} puntos** en el torneo.")
        else:
            await ctx.send("❌ No estás registrado en el torneo o todavía no formas equipo. En la aplicación Pescadito FN o en la página web https://pescadito.lat ve a Ajustes y forma tu equipo.")
    await asyncio.sleep(1)

@bot.command()
@cooldown(1, 10, BucketType.user)
async def topmejores(ctx):
    global tournament_mode
    if tournament_mode == "solo":
        with get_conn().cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
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
    else:
        # Modo equipos: se agrupan únicamente equipos completos.
        all_parts = get_all_participants()["participants"]
        equipos = []
        if tournament_mode == "duos":
            required_team_members = 1
        elif tournament_mode == "trios":
            required_team_members = 2
        elif tournament_mode == "escuadrones":
            required_team_members = 3
        else:
            await ctx.send("❌ tournament_mode desconocido.")
            return
        for uid, part in all_parts.items():
            if part is None:
                continue
            tm = part.get("team_members", "")
            if tm is None:
                tm = ""
            tm = tm.strip()
            if tm != "":
                members = [m.strip() for m in tm.split(",") if m.strip() != ""]
                if len(members) == required_team_members:
                    equipos.append((uid, part, members))
        # Ordenar equipos por puntuación del líder descendente
        equipos.sort(key=lambda tup: int(tup[1].get("puntuacion", 0)), reverse=True)
        if equipos:
            lines = ["🏆 **Top Mejores del Torneo (Equipos Completos):**"]
            for idx, (leader_id, leader_part, members) in enumerate(equipos, start=1):
                # Obtener nombres usando fortnite_username
                nombres = [leader_part.get("fortnite_username", "N/D")]
                for m_id in members:
                    miembro = get_participant(m_id)
                    if miembro:
                        nombres.append(miembro.get("fortnite_username", "N/D"))
                linea = f"{idx}. " + ", ".join(nombres)
                lines.append(linea)
            # Enviar en fragmentos si es demasiado largo, asegurando que cada línea (equipo) se mantenga junta.
            mensaje_final = ""
            for linea in lines:
                if len(mensaje_final) + len(linea) + 1 > 1900:
                    await ctx.send(mensaje_final)
                    mensaje_final = linea + "\n"
                else:
                    mensaje_final += linea + "\n"
            if mensaje_final:
                await ctx.send(mensaje_final)
        else:
            await ctx.send("No hay equipos completos registrados en el torneo.")
    await asyncio.sleep(1)

@bot.command()
async def vermigrupo(ctx):
    global tournament_mode
    if tournament_mode == "solo":
        user_id = str(ctx.author.id)
        participant = get_participant(user_id)
        if participant:
            fortnite_username = participant.get("fortnite_username", user_id)
            etapa = participant.get("etapa", "N/A")
            grupo = participant.get("grupo", "N/A")
            await ctx.send(f"Hola {fortnite_username}, estás en la etapa {etapa} del torneo y tu grupo es el {grupo}.")
        else:
            await ctx.send("❌ No estás registrado en el torneo.")
    else:
        all_parts = get_all_participants()["participants"]
        user_id = str(ctx.author.id)
        leader = None
        part = all_parts.get(user_id)
        if part is not None:
            team_members = part.get("team_members")
            if team_members and team_members.strip() != "":
                leader = part
            if leader is None:
                leader = get_team_leader(user_id, all_parts)
        if leader:
            etapa = leader.get("etapa", "N/A")
            grupo = leader.get("grupo", "N/A")
            await ctx.send(f"Hola {leader.get('fortnite_username')}, tu equipo está en la etapa {etapa} y en el grupo {grupo}.")
        else:
            await ctx.send("❌ No estás registrado en el torneo o todavía no formas equipo. En la aplicación Pescadito FN o en la página web https://pescadito.lat ve a Ajustes y forma tu equipo.")
    await asyncio.sleep(1)

# ----------------- FIN MODIFICACIONES TOURNAMENT_MODE -----------------

@bot.command()
@cooldown(1, 10, BucketType.user)
async def triviagrupal(ctx):
    if ctx.author.id != OWNER_ID:
        return
    trivia_item = get_random_trivia()
    if not trivia_item:
        await ctx.send("No hay trivias disponibles.")
        return
    question = trivia_item["question"]
    answer = trivia_item["answer"]
    hint1 = trivia_item.get("hint1", "")
    hint2 = trivia_item.get("hint2", "")
    normalized_answer = normalize_string(answer)
    general_channel = bot.get_channel(GENERAL_CHANNEL_ID)
    if general_channel is None:
        await ctx.send("No se encontró el canal general.")
        return
    await general_channel.send(f"**Trivia Grupal:** {question}")
    
    def check(m):
        return m.channel.id == GENERAL_CHANNEL_ID and not m.author.bot and normalize_string(m.content) == normalized_answer

    answered = False
    try:
        msg = await bot.wait_for("message", timeout=2, check=check)
        answered = True
    except asyncio.TimeoutError:
        await general_channel.send(f"Pista: {hint1}")
        try:
            msg = await bot.wait_for("message", timeout=3, check=check)
            answered = True
        except asyncio.TimeoutError:
            await general_channel.send(f"Otra pista: {hint2}")
            try:
                msg = await bot.wait_for("message", timeout=3, check=check)
                answered = True
            except asyncio.TimeoutError:
                await general_channel.send("Nadie reespondió correctamente")
    if answered:
        participant = get_participant(str(msg.author.id))
        fortnite_name = participant["fortnite_username"] if participant and participant.get("fortnite_username") else msg.author.name
        update_score(str(msg.author.id), 15)
        await general_channel.send(f"Respuesta correcta, {fortnite_name}, has ganado 15 puntos.")

@bot.command()
async def minivel(ctx):
    user_id = str(ctx.author.id)
    participant = get_participant(user_id)
    if participant is None:
        await ctx.send("No estás registrado en el sistema de niveles.")
        return
    fortnite_name = participant.get("fortnite_username", ctx.author.display_name)
    nivel = participant.get("nivel", 1)
    experiencia = participant.get("experiencia", 0)
    threshold = nivel * 100
    ratio = experiencia / threshold if threshold > 0 else 0
    # Crear imagen con la información de nivel
    img = Image.new("RGB", (400, 120), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default()
    draw.text((10, 10), f"{fortnite_name}", fill=(0, 0, 0), font=font)
    draw.text((300, 10), f"Nivel {nivel}", fill=(0, 0, 0), font=font)
    bar_x = 10
    bar_y = 60
    bar_width = 380
    bar_height = 30
    draw.rectangle([bar_x, bar_y, bar_x+bar_width, bar_y+bar_height], fill=(200, 200, 200))
    fill_width = int(bar_width * ratio)
    draw.rectangle([bar_x, bar_y, bar_x+fill_width, bar_y+bar_height], fill=(100, 100, 255))
    draw.text((bar_x+10, bar_y+5), f"{experiencia}/{threshold} XP", fill=(0, 0, 0), font=font)
    import io
    bio = io.BytesIO()
    img.save(bio, format="PNG")
    bio.seek(0)
    file = discord.File(fp=bio, filename="minivel.png")
    await ctx.send(file=file)

@bot.command()
@cooldown(1, 10, BucketType.user)
async def trivia(ctx):
    if ctx.author.bot:
        return
    if ctx.channel.id in active_trivia:
        del active_trivia[ctx.channel.id]
    global global_trivias_cache
    # Si la caché de trivias está vacía, se recarga desde la base de datos
    if not global_trivias_cache:
        with get_conn().cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute("SELECT * FROM trivias")
            rows = cur.fetchall()
            global_trivias_cache.extend(rows)
    if global_trivias_cache:
        index = random.randrange(len(global_trivias_cache))
        trivia_item = global_trivias_cache.pop(index)
    else:
        trivia_item = None
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
async def triviafortnite(ctx):
    if ctx.author.bot:
        return
    if ctx.channel.id in active_trivia:
        del active_trivia[ctx.channel.id]
    global global_triviafortnite_cache
    # Si la caché de trivias de Fortnite está vacía, se recarga desde la base de datos
    if not global_triviafortnite_cache:
        with get_conn().cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute("SELECT * FROM triviafortnite")
            rows = cur.fetchall()
            global_triviafortnite_cache.extend(rows)
    if global_triviafortnite_cache:
        index = random.randrange(len(global_triviafortnite_cache))
        trivia_item = global_triviafortnite_cache.pop(index)
    else:
        trivia_item = None
    if trivia_item:
        active_trivia[ctx.channel.id] = {
            "question": trivia_item["question"],
            "answer": normalize_string(trivia_item["answer"]),
            "hint1": trivia_item.get("hint1", ""),
            "hint2": trivia_item.get("hint2", ""),
            "attempts": {}
        }
        await ctx.send(f"**Trivia Fortnite:** {trivia_item['question']}\n_Responde en el chat._")
    else:
        await ctx.send("No tengo más trivias de Fortnite disponibles en este momento.")

@bot.command()
@cooldown(1, 10, BucketType.user)
async def chiste(ctx):
    if ctx.author.bot:
        return
    joke = get_random_joke()
    await ctx.send(joke)

#@bot.command()
#@cooldown(1, 10, BucketType.user)
#async def vermigrupo(ctx):
    # La implementación para vermigrupo ya se encuentra en la sección modificada arriba.
    #pass  # (Ya se define en la sección correspondiente)

@bot.listen('on_message')
async def on_message_no_prefix(message):
    if message.author.bot:
        return
    if message.content.startswith(PREFIX):
        return
    content = message.content.lower().strip()
    if content in ('trivia', 'triviafortnite', 'chiste', 'ranking', 'topmejores', 'vermigrupo', 'minivel'):
        message.content = PREFIX + content
        ctx = await bot.get_context(message)
        await bot.invoke(ctx)

######################################
# EVENTO ON_MESSAGE (DM FORWARDING, RESPUESTAS DE TRIVIA Y SISTEMA DE NIVEL)
######################################
@bot.event
async def on_message(message):
    if message.author.bot:
        return

    ######################################
    # MINIJUEGO DE SUBIR DE NIVEL
    ######################################
    if message.guild is not None:
        participant = get_participant(str(message.author.id))
        if participant is not None:
            try:
                current_exp = participant.get("experiencia", 0)
                current_level = participant.get("nivel", 1)
                new_exp = current_exp + 5  # Se suma 5 XP por mensaje
                leveled_up = False
                threshold = current_level * 100
                while new_exp >= threshold:
                    new_exp -= threshold
                    current_level += 1
                    threshold = current_level * 100
                    leveled_up = True
                participant["experiencia"] = new_exp
                participant["nivel"] = current_level
                upsert_participant(str(message.author.id), participant)
                if leveled_up:
                    await message.channel.send(f"{message.author.display_name} subiste de nivel")
                    await asyncio.sleep(0.1)
            except Exception as e:
                print(f"Error actualizando nivel para {message.author.id}: {e}")

    if message.guild is None:
        if str(message.author.id) in dm_forwarding:
            end_time = dm_forwarding[str(message.author.id)]
            if end_time is not None and datetime.datetime.utcnow() > end_time:
                del dm_forwarding[str(message.author.id)]
            else:
                forward_channel = bot.get_channel(SPECIAL_HELP_CHANNEL)
                if forward_channel is not None:
                    forward_text = f"Mensaje de {message.author.mention}: {message.content}"
                    if message.attachments:
                        for attachment in message.attachments:
                            forward_text += f"\nAdjunto: {attachment.url}"
                    try:
                        await forward_channel.send(forward_text)
                    except Exception as e:
                        print(f"Error forwarding DM from {message.author.id}: {e}")
                    await asyncio.sleep(1)
        # ——— MENCIONES A @BOT ———
    # Solo en el canal GENERAL_CHANNEL_ID, ignorar DMs y otros canales
    if message.guild and message.channel.id == GENERAL_CHANNEL_ID:
        # Comprueba si el bot fue mencionado
        if bot.user in message.mentions:
            # Recupera los últimos 5 mensajes + el actual
            history = [msg async for msg in message.channel.history(limit=6, oldest_first=False)]
            # Construye el prompt para KoboldAI
            prompt = ""
            for msg in reversed(history):
                author = msg.author.display_name
                content = msg.content.replace(f"<@{bot.user.id}>", "").strip()
                prompt += f"{author}: {content}\n"
            prompt += f"{bot.user.display_name}:"
            # Envía a KoboldAI y reintenta si se pierde la conexión
            response = await query_kobold(prompt)
            if response:
                await message.channel.send(response)
            return
    # ————————————————

    await bot.process_commands(message)
    if message.content.startswith(PREFIX):
        return
    if message.channel.id in active_trivia:
        trivia_data = active_trivia[message.channel.id]
        user_attempts = trivia_data["attempts"].get(message.author.id, 0)
        max_attempts_per_user = 3
        if user_attempts >= max_attempts_per_user:
            if user_attempts == max_attempts_per_user:
                await message.channel.send(f"❌ Has agotado tus intentos, {message.author.mention}.")
                await asyncio.sleep(0.5)
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
                del active_trivia[message.channel.id]

######################################
# EVENTO ON_COMMAND_ERROR
######################################
@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandOnCooldown):
        await ctx.send(f"⏳ Este comando está en cooldown. Por favor, inténtalo de nuevo en {round(error.retry_after, 1)} segundos.")
    else:
        raise error

#import aiohttp
#import async_timeout
#from datetime import datetime, timedelta

# Endpoint OpenAI‑compatible que sí existe en tu KoboldCpp
KOBOLD_URL = os.environ.get(
    "KOBOLD_URL",
    "http://192.168.100.17:5001/v1/completions"
)
_last_reset = datetime.utcnow()

async def query_kobold(prompt: str) -> str:
    """
    Envía el prompt a KoboldCpp vía OpenAI‑compatible API (/v1/completions),
    reintenta al reconectar, resetea la sesión cada 30 minutos,
    y devuelve solo el texto de la IA.
    """
    global _last_reset

    # 1) Reset de sesión cada 30 min
    if datetime.utcnow() - _last_reset > timedelta(minutes=30):
        try:
            reset_url = KOBOLD_URL.replace("/completions", "/reset")
            async with aiohttp.ClientSession() as sess:
                await sess.post(reset_url)
            _last_reset = datetime.utcnow()
            print("[query_kobold] Sesión reiniciada tras 30 minutos.")
        except Exception as e:
            print(f"[query_kobold] fallo al resetear sesión: {e}")

    # 2) Intentar hasta 3 veces generar texto
    for attempt in range(3):
        try:
            async with async_timeout.timeout(30):
                async with aiohttp.ClientSession() as sess:
                    payload = {
                        "model": "gemma3",         # tu modelo GGUF
                        "prompt": prompt,          # <— aquí debe haber coma
                        "max_tokens": 256,
                        "temperature": 0.7,
                        "stop": ["\nHuman:", "\nSystem:"]
                    }
                    async with sess.post(KOBOLD_URL, json=payload) as resp:
                        data = await resp.json()
                        return data["choices"][0]["text"].strip()
        except Exception as e:
            print(f"[query_kobold] intento {attempt+1} falló: {e}")
            await asyncio.sleep(5)

    # 3) Si todo falla
    return "Lo siento, no pude conectarme al servicio de IA en este momento."




######################################
# EVENTO ON_READY
######################################
@bot.event
async def on_ready():
    print(f'Bot conectado como {bot.user.name}')
    # Obtener el tournament_mode desde la URL al iniciar el bot
    await fetch_config()
    bot.loop.create_task(event_notifier())

# Función asíncrona para obtener el tournament_mode desde la URL
async def fetch_config():
    global tournament_mode
    url = "https://pescadito-ddcb0.web.app/config.json"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status == 200:
                    config = await resp.json()
                    tournament_mode = config.get("tournament_mode", "solo").lower()
                    print(f"Config cargado: tournament_mode = {tournament_mode}")
                else:
                    print(f"Error al obtener config (status {resp.status}). Se usará el modo por defecto 'solo'.")
    except Exception as e:
        print(f"Excepción al obtener config: {e}. Se usará el modo por defecto 'solo'.")

async def event_notifier():
    await bot.wait_until_ready()
    tz_peru = ZoneInfo("America/Lima")
    while not bot.is_closed():
        now = datetime.now(tz_peru)
        with get_conn().cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute("SELECT id, name, event_datetime, target_stage, target_group, notified_10h, notified_2h, notified_10m, notified_2m FROM calendar_events")
            events = cur.fetchall()
        for ev in events:
            event_dt = ev["event_datetime"]
            diff = (event_dt - now).total_seconds()
            if diff > 0 and diff <= 10 * 3600 and not ev["notified_10h"]:
                with get_conn().cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                    cur.execute("SELECT user_id, country FROM registrations WHERE etapa = %s AND grupo = %s", (ev["target_stage"], ev["target_group"]))
                    users = cur.fetchall()
                for user_row in users:
                    user = bot.get_user(int(user_row["user_id"]))
                    if user is not None:
                        tz_user = ZoneInfo(country_timezones.get(user_row["country"], "UTC"))
                        local_dt = event_dt.astimezone(tz_user)
                        msg = (f"⏰ Faltan 10 horas para '{ev['name']}', que se realizará el "
                               f"{local_dt.strftime('%d/%m/%Y %H:%M')} hora {user_row['country']}. Recuerda que si llegas tarde, quedarás automáticamente eliminado del torneo.")
                        try:
                            await user.send(msg)
                        except Exception as e:
                            print(f"Error enviando DM de 10 horas a {user_row['user_id']}: {e}")
                        await asyncio.sleep(1)
                with get_conn().cursor() as cur:
                    cur.execute("UPDATE calendar_events SET notified_10h = TRUE WHERE id = %s", (ev["id"],))
                get_conn().commit()
            if diff > 0 and diff <= 2 * 3600 and not ev["notified_2h"]:
                with get_conn().cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                    cur.execute("SELECT user_id, country FROM registrations WHERE etapa = %s AND grupo = %s", (ev["target_stage"], ev["target_group"]))
                    users = cur.fetchall()
                for user_row in users:
                    user = bot.get_user(int(user_row["user_id"]))
                    if user is not None:
                        tz_user = ZoneInfo(country_timezones.get(user_row["country"], "UTC"))
                        local_dt = event_dt.astimezone(tz_user)
                        msg = (f"⏰ Faltan 2 horas para '{ev['name']}', que se realizará el "
                               f"{local_dt.strftime('%d/%m/%Y %H:%M')} hora {user_row['country']}. Recuerda que si llegas tarde, quedarás automáticamente eliminado del torneo.")
                        try:
                            await user.send(msg)
                        except Exception as e:
                            print(f"Error enviando DM de 2 horas a {user_row['user_id']}: {e}")
                        await asyncio.sleep(1)
                with get_conn().cursor() as cur:
                    cur.execute("UPDATE calendar_events SET notified_2h = TRUE WHERE id = %s", (ev["id"],))
                get_conn().commit()
            if diff > 0 and diff <= 10 * 60 and not ev["notified_10m"]:
                with get_conn().cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                    cur.execute("SELECT user_id, country FROM registrations WHERE etapa = %s AND grupo = %s", (ev["target_stage"], ev["target_group"]))
                    users = cur.fetchall()
                for user_row in users:
                    user = bot.get_user(int(user_row["user_id"]))
                    if user is not None:
                        tz_user = ZoneInfo(country_timezones.get(user_row["country"], "UTC"))
                        local_dt = event_dt.astimezone(tz_user)
                        msg = (f"⏰ Faltan 10 minutos para '{ev['name']}', que se realizará el "
                               f"{local_dt.strftime('%d/%m/%Y %H:%M')} hora {user_row['country']}. Recuerda que si llegas tarde, quedarás automáticamente eliminado del torneo.")
                        try:
                            await user.send(msg)
                        except Exception as e:
                            print(f"Error enviando DM de 10 minutos a {user_row['user_id']}: {e}")
                        await asyncio.sleep(1)
                with get_conn().cursor() as cur:
                    cur.execute("UPDATE calendar_events SET notified_10m = TRUE WHERE id = %s", (ev["id"],))
                get_conn().commit()
            if diff > 0 and diff <= 2 * 60 and not ev["notified_2m"]:
                with get_conn().cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                    cur.execute("SELECT user_id, country FROM registrations WHERE etapa = %s AND grupo = %s", (ev["target_stage"], ev["target_group"]))
                    users = cur.fetchall()
                for user_row in users:
                    user = bot.get_user(int(user_row["user_id"]))
                    if user is not None:
                        tz_user = ZoneInfo(country_timezones.get(user_row["country"], "UTC"))
                        local_dt = event_dt.astimezone(tz_user)
                        msg = (f"⏰ Faltan 2 minutos para '{ev['name']}', que se realizará el "
                               f"{local_dt.strftime('%d/%m/%Y %H:%M')} hora {user_row['country']}. Recuerda que si llegas tarde, quedarás automáticamente eliminado del torneo.")
                        try:
                            await user.send(msg)
                        except Exception as e:
                            print(f"Error enviando DM de 2 minutos a {user_row['user_id']}: {e}")
                        await asyncio.sleep(1)
                with get_conn().cursor() as cur:
                    cur.execute("UPDATE calendar_events SET notified_2m = TRUE WHERE id = %s", (ev["id"],))
                get_conn().commit()
        await asyncio.sleep(60)

######################################
# SERVIDOR WEB PARA MANTENER EL BOT ACTIVO (API PRIVADA)
######################################
def run_flask():
    port = int(os.environ.get("PORT", 8080))
    from waitress import serve
    serve(app, host="0.0.0.0", port=port)

if __name__ == '__main__':
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.start()
    bot.run(os.getenv('DISCORD_TOKEN'))
