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
import asyncio  # Para usar asyncio.sleep
import datetime  # Para gestionar fechas y tiempos
from flask import Flask, request, jsonify
from zoneinfo import ZoneInfo  # Para trabajar con zonas horarias

######################################
# CONFIGURACIÓN: IDs y Servidor
######################################
OWNER_ID = 1336609089656197171         # Tu Discord ID (único autorizado para comandos sensibles)
PRIVATE_CHANNEL_ID = 1338130641354620988  # Canal privado para comandos sensibles (no se utiliza en la versión final)
PUBLIC_CHANNEL_ID  = 1338126297666424874  # Canal público donde se muestran resultados sensibles
SPECIAL_HELP_CHANNEL = 1338608387197243422  # Canal especial para que el owner reciba la lista extendida de comandos
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
        # Se asume que la tabla 'registrations' ya existe en la base de datos para almacenar el país de los usuarios
        # Ejemplo:
        # CREATE TABLE IF NOT EXISTS registrations (
        #    user_id TEXT PRIMARY KEY,
        #    discord_name TEXT,
        #    fortnite_username TEXT,
        #    platform TEXT,
        #    country TEXT
        # );
init_db()

######################################
# CONFIGURACIÓN INICIAL DEL TORNEO
######################################
PREFIX = '!'
# Configuración de etapas: cada etapa tiene un número determinado de jugadores.
# Se agregan las etapas 7 y 8.
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
        cur.execute("SELECT * FROM participants WHERE id = %s", (user_id,))
        return cur.fetchone()

def get_all_participants():
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM participants")
        rows = cur.fetchall()
        data = {"participants": {}}
        for row in rows:
            data["participants"][row["id"]] = row
        return data

def upsert_participant(user_id, participant):
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO participants (id, nombre, puntos, symbolic, etapa, logros)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                nombre = EXCLUDED.nombre,
                puntos = EXCLUDED.puntos,
                symbolic = EXCLUDED.symbolic,
                etapa = EXCLUDED.etapa,
                logros = EXCLUDED.logros
        """, (
            user_id,
            participant["nombre"],
            participant.get("puntos", 0),
            participant.get("symbolic", 0),
            participant.get("etapa", current_stage),
            json.dumps(participant.get("logros", []))
        ))

def update_score(user: discord.Member, delta: int):
    user_id = str(user.id)
    participant = get_participant(user_id)
    if participant is None:
        participant = {
            "nombre": user.display_name,
            "puntos": 0,
            "symbolic": 0,
            "etapa": current_stage,
            "logros": []
        }
    new_points = int(participant.get("puntos", 0)) + delta
    participant["puntos"] = new_points
    upsert_participant(user_id, participant)
    return new_points

def award_symbolic_reward(user: discord.Member, reward: int):
    user_id = str(user.id)
    participant = get_participant(user_id)
    if participant is None:
        participant = {
            "nombre": user.display_name,
            "puntos": 0,
            "symbolic": 0,
            "etapa": current_stage,
            "logros": []
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
intents.members = True
intents.messages = True
intents.message_content = True
bot = commands.Bot(command_prefix=PREFIX, intents=intents)

async def send_public_message(message: str, view: discord.ui.View = None):
    public_channel = bot.get_channel(PUBLIC_CHANNEL_ID)
    if public_channel:
        await public_channel.send(message, view=view)
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
        cur.execute("DELETE FROM participants WHERE id = %s", (str(member.id),))
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
# COMANDOS SENSIBLES (solo OWNER_ID, en DM o en canal PUBLIC_CHANNEL_ID)
######################################
def is_owner_and_allowed(ctx):
    return ctx.author.id == OWNER_ID and (ctx.guild is None or ctx.channel.id == PUBLIC_CHANNEL_ID)

@bot.command()
async def actualizar_puntuacion(ctx, jugador: str, puntos: int):
    if not is_owner_and_allowed(ctx):
        try:
            await ctx.message.delete()
        except:
            pass
        return
    match = re.search(r'\d+', jugador)
    if not match:
        await send_public_message("No se pudo encontrar al miembro.")
        try:
            await ctx.message.delete()
        except:
            pass
        return
    member_id = int(match.group())
    guild = ctx.guild or bot.get_guild(GUILD_ID)
    if guild is None:
        await send_public_message("No se pudo determinar el servidor.")
        try:
            await ctx.message.delete()
        except:
            pass
        return
    try:
        member = guild.get_member(member_id)
        if member is None:
            member = await guild.fetch_member(member_id)
    except Exception as e:
        await send_public_message("No se pudo encontrar al miembro en el servidor.")
        try:
            await ctx.message.delete()
        except:
            pass
        return
    try:
        puntos = int(puntos)
    except ValueError:
        await send_public_message("Por favor, proporciona un número válido de puntos.")
        try:
            await ctx.message.delete()
        except:
            pass
        return
    new_points = update_score(member, puntos)
    await send_public_message(f"✅ Puntuación actualizada: {member.display_name} ahora tiene {new_points} puntos")
    try:
        await ctx.message.delete()
    except:
        pass

@bot.command()
async def reducir_puntuacion(ctx, jugador: str, puntos: int):
    if not is_owner_and_allowed(ctx):
        try:
            await ctx.message.delete()
        except:
            pass
        return
    await actualizar_puntuacion(ctx, jugador, -puntos)
    try:
        await ctx.message.delete()
    except:
        pass

@bot.command()
async def ver_puntuacion(ctx):
    participant = get_participant(str(ctx.author.id))
    if participant:
        await ctx.send(f"🏆 Tu puntaje del torneo es: {participant.get('puntos', 0)}")
    else:
        await ctx.send("❌ No estás registrado en el torneo")

@bot.command()
async def clasificacion(ctx):
    data = get_all_participants()
    sorted_players = sorted(data["participants"].items(), key=lambda item: int(item[1].get("puntos", 0)), reverse=True)
    ranking = "🏅 Clasificación del Torneo:\n"
    for idx, (uid, player) in enumerate(sorted_players, 1):
        ranking += f"{idx}. {player['nombre']} - {player.get('puntos', 0)} puntos\n"
    await ctx.send(ranking)
    
@bot.command()
async def avanzar_etapa(ctx):
    if not is_owner_and_allowed(ctx):
        try:
            await ctx.message.delete()
        except:
            pass
        return
    global current_stage, champion_id, forwarding_enabled, forwarding_end_time
    current_stage += 1
    data = get_all_participants()
    sorted_players = sorted(data["participants"].items(), key=lambda item: int(item[1].get("puntos", 0)), reverse=True)
    cutoff = STAGES.get(current_stage)
    if cutoff is None:
        await send_public_message("No hay configuración para esta etapa.")
        try:
            await ctx.message.delete()
        except:
            pass
        return
    avanzan = sorted_players[:cutoff]
    eliminados = sorted_players[cutoff:]
    for uid, player in avanzan:
        player["etapa"] = current_stage
        upsert_participant(uid, player)
        try:
            member = ctx.guild.get_member(int(uid)) or await ctx.guild.fetch_member(int(uid))
            if current_stage == 6:
                msg = (f"🏆 ¡Enhorabuena {member.display_name}! Has sido coronado como el CAMPEON del torneo. "
                       f"Además, has ganado 2800 paVos, que serán entregados en forma de regalos de la tienda de objetos de Fortnite. "
                       f"Puedes escoger los objetos que desees de la tienda, siempre que el valor total de ellos sume 2800. "
                       f"Por favor, escribe en este chat el nombre de los objetos que has escogido (tal como aparecen en la tienda de objetos de Fortnite).")
                champion_id = member.id
                forwarding_enabled = True
                forwarding_end_time = None
            elif current_stage == 7:
                msg = f"❗ Aún te faltan escoger objetos. Por favor, escribe tus objetos escogidos. 😕"
                champion_id = member.id
                forwarding_enabled = True
                forwarding_end_time = None
            elif current_stage == 8:
                msg = f"✅ Tus objetos han sido entregados, muchas gracias por participar, nos vemos pronto. 🙌"
                champion_id = member.id
                forwarding_enabled = True
                forwarding_end_time = datetime.datetime.utcnow() + datetime.timedelta(days=2)
            else:
                msg = f"🎉 ¡Felicidades! Has avanzado a la etapa {current_stage} ({stage_names.get(current_stage, 'Etapa ' + str(current_stage))})."
                champion_id = None
                forwarding_enabled = False
                forwarding_end_time = None
            await member.send(msg)
            await asyncio.sleep(1)
        except Exception as e:
            print(f"Error al enviar mensaje a {uid}: {e}")
    for uid, player in eliminados:
        try:
            member = ctx.guild.get_member(int(uid)) or await ctx.guild.fetch_member(int(uid))
            await member.send(f"❌ Lo siento, has sido eliminado del torneo en la etapa {current_stage - 1}.")
            await asyncio.sleep(1)
        except Exception as e:
            print(f"Error al enviar mensaje a {uid}: {e}")
    await send_public_message(f"✅ Etapa {current_stage} iniciada. {cutoff} jugadores avanzaron y {len(eliminados)} fueron eliminados.")
    try:
        await ctx.message.delete()
    except:
        pass

@bot.command()
async def retroceder_etapa(ctx):
    if not is_owner_and_allowed(ctx):
        try:
            await ctx.message.delete()
        except:
            pass
        return
    global current_stage, champion_id, forwarding_enabled, forwarding_end_time
    if current_stage <= 1:
        await send_public_message("No se puede retroceder de la etapa 1.")
        try:
            await ctx.message.delete()
        except:
            pass
        return
    current_stage -= 1
    data = get_all_participants()
    for uid, player in data["participants"].items():
        player["etapa"] = current_stage
        upsert_participant(uid, player)
    if current_stage not in [6,7,8]:
        champion_id = None
        forwarding_enabled = False
        forwarding_end_time = None
    await send_public_message(f"✅ Etapa retrocedida. Ahora la etapa es {current_stage} ({stage_names.get(current_stage, 'Etapa ' + str(current_stage))}).")
    try:
        await ctx.message.delete()
    except:
        pass

@bot.command()
async def eliminar_jugador(ctx, jugador: str):
    if not is_owner_and_allowed(ctx):
        try:
            await ctx.message.delete()
        except:
            pass
        return
    match = re.search(r'\d+', jugador)
    if not match:
        await send_public_message("No se pudo encontrar al miembro.")
        try:
            await ctx.message.delete()
        except:
            pass
        return
    member_id = int(match.group())
    guild = ctx.guild or bot.get_guild(GUILD_ID)
    if guild is None:
        await send_public_message("No se pudo determinar el servidor.")
        try:
            await ctx.message.delete()
        except:
            pass
        return
    try:
        member = guild.get_member(member_id) or await guild.fetch_member(member_id)
    except Exception as e:
        await send_public_message("No se pudo encontrar al miembro en el servidor.")
        try:
            await ctx.message.delete()
        except:
            pass
        return
    user_id = str(member.id)
    with conn.cursor() as cur:
        cur.execute("DELETE FROM participants WHERE id = %s", (user_id,))
    await send_public_message(f"✅ {member.display_name} eliminado del torneo")
    try:
        await ctx.message.delete()
    except:
        pass

@bot.command()
async def configurar_etapa(ctx, etapa: int):
    if not is_owner_and_allowed(ctx):
        try:
            await ctx.message.delete()
        except:
            pass
        return
    global current_stage, champion_id, forwarding_enabled, forwarding_end_time
    current_stage = etapa
    if current_stage not in [6,7,8]:
        champion_id = None
        forwarding_enabled = False
        forwarding_end_time = None
    await send_public_message(f"✅ Etapa actual configurada a {etapa}")
    try:
        await ctx.message.delete()
    except:
        pass

@bot.command()
async def saltar_etapa(ctx, etapa: int):
    if not is_owner_and_allowed(ctx):
        try:
            await ctx.message.delete()
        except:
            pass
        return
    global current_stage, champion_id, forwarding_enabled, forwarding_end_time
    current_stage = etapa
    data = get_all_participants()
    sorted_players = sorted(data["participants"].items(), key=lambda item: int(item[1].get("puntos", 0)), reverse=True)
    if sorted_players:
        champ_uid, champ_player = sorted_players[0]
        try:
            guild = ctx.guild or bot.get_guild(GUILD_ID)
            champion = guild.get_member(int(champ_uid)) or await guild.fetch_member(int(champ_uid))
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
                await champion.send(msg)
            elif current_stage == 7:
                msg = f"❗ Aún te faltan escoger objetos. Por favor, escribe tus objetos escogidos. 😕"
                champion_id = champion.id
                forwarding_enabled = True
                forwarding_end_time = None
                await champion.send(msg)
            elif current_stage == 8:
                msg = f"✅ Tus objetos han sido entregados, muchas gracias por participar, nos vemos pronto. 🙌"
                champion_id = champion.id
                forwarding_enabled = True
                forwarding_end_time = datetime.datetime.utcnow() + datetime.timedelta(days=2)
                await champion.send(msg)
            else:
                champion_id = None
                forwarding_enabled = False
                forwarding_end_time = None
    await send_public_message(f"✅ Etapa saltada. Ahora la etapa es {current_stage} ({stage_names.get(current_stage, 'Etapa ' + str(current_stage))}).")
    try:
        await ctx.message.delete()
    except:
        pass

######################################
# COMANDOS PÚBLICOS (chistes y trivia) – disponibles para cualquier usuario
######################################
@bot.command()
async def trivia(ctx):
    if ctx.channel.id in active_trivia:
        del active_trivia[ctx.channel.id]
    trivia_item = get_random_trivia()
    active_trivia[ctx.channel.id] = trivia_item
    await ctx.send(f"**Trivia:** {trivia_item['question']}\n_Responde en el chat._")
    try:
        await ctx.message.delete()
    except:
        pass

@bot.command()
async def chiste(ctx):
    await ctx.send(get_random_joke())

######################################
# COMANDOS SENSIBLES PARA GESTIÓN DE CALENDARIO (EVENTOS) – SOLO OWNER_ID
######################################
@bot.command()
async def agregar_evento(ctx, *, evento_data: str):
    if not is_owner_and_allowed(ctx):
        try:
            await ctx.message.delete()
        except:
            pass
        return
    # Formato: nombre | DD/MM/YYYY | HH:MM | etapa
    parts = [part.strip() for part in evento_data.split("|")]
    if len(parts) < 4:
        await send_public_message("❌ Formato incorrecto. Usa: nombre | DD/MM/YYYY | HH:MM | etapa")
        return
    name = parts[0]
    date_str = parts[1]
    time_str = parts[2]
    stage_str = parts[3]
    try:
        tz = ZoneInfo("America/Lima")
        event_dt = datetime.datetime.strptime(f"{date_str} {time_str}", "%d/%m/%Y %H:%M")
        event_dt = event_dt.replace(tzinfo=tz)
    except Exception as e:
        await send_public_message("❌ Error al parsear la fecha/hora. Usa formato DD/MM/YYYY y HH:MM.")
        return
    try:
        target_stage = int(stage_str)
    except:
        await send_public_message("❌ Error: La etapa debe ser un número.")
        return
    with conn.cursor() as cur:
        cur.execute("INSERT INTO calendar_events (name, event_datetime, target_stage) VALUES (%s, %s, %s) RETURNING id", (name, event_dt, target_stage))
        event_id = cur.fetchone()[0]
    await send_public_message(f"✅ Evento agregado con ID {event_id}: **{name}** para {event_dt.strftime('%d/%m/%Y %H:%M')} dirigido a la etapa {target_stage}.")
    try:
        await ctx.message.delete()
    except:
        pass

@bot.command()
async def eliminar_evento(ctx, event_id: int):
    if not is_owner_and_allowed(ctx):
        try:
            await ctx.message.delete()
        except:
            pass
        return
    with conn.cursor() as cur:
        cur.execute("DELETE FROM calendar_events WHERE id = %s", (event_id,))
        if cur.rowcount > 0:
            await send_public_message(f"✅ Evento con ID {event_id} eliminado.")
        else:
            await send_public_message(f"❌ No se encontró el evento con ID {event_id}.")
    try:
        await ctx.message.delete()
    except:
        pass

# MODIFICACIÓN: notificar_evento ahora permite seleccionar el evento (si no se pasa el ID, muestra un menú interactivo)
@bot.command()
async def notificar_evento(ctx, event_id: int = None):
    if not is_owner_and_allowed(ctx):
        try:
            await ctx.message.delete()
        except:
            pass
        return
    if event_id is None:
        view = EventSelectionView(ctx)
        await send_public_message("Selecciona el evento a notificar:", view=view)
        return
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM calendar_events WHERE id = %s", (event_id,))
        event = cur.fetchone()
    if not event:
        await send_public_message(f"❌ No se encontró el evento con ID {event_id}.")
        return
    # Aquí ya no dependemos de que se haya notificado manualmente; los recordatorios se enviarán automáticamente
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM participants WHERE etapa = %s", (event["target_stage"],))
        participants = cur.fetchall()
    count = 0
    for participant in participants:
        try:
            guild = bot.get_guild(GUILD_ID)
            member = guild.get_member(int(participant["id"]))
            if member is None:
                member = await guild.fetch_member(int(participant["id"]))
            # Consultar el país del usuario en la tabla 'registrations'
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur_reg:
                cur_reg.execute("SELECT country FROM registrations WHERE user_id = %s", (str(participant["id"]),))
                reg = cur_reg.fetchone()
            if reg and reg.get("country"):
                country_str = f" {reg['country']}"
            else:
                country_str = ""
            event_time_str = event['event_datetime'].strftime('%d/%m/%Y %H:%M') + country_str
            await member.send(f"📅 Notificación de evento: **{event['name']}** se realizará el {event_time_str}. ¡No te lo pierdas!")
            count += 1
            await asyncio.sleep(1)
        except Exception as e:
            print(f"Error notificar_evento a {participant['id']}: {e}")
    # Se marca la notificación inicial como enviada (aunque ahora el recordatorio automático se enviará sin intervención previa)
    with conn.cursor() as cur:
        cur.execute("UPDATE calendar_events SET initial_notified = TRUE WHERE id = %s", (event_id,))
    await send_public_message(f"✅ Notificación enviada a {count} participantes para el evento ID {event_id}.")
    try:
        await ctx.message.delete()
    except:
        pass

class EventSelectionView(discord.ui.View):
    def __init__(self, ctx):
        super().__init__(timeout=60)
        self.ctx = ctx
        options = []
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT id, name, event_datetime, target_stage FROM calendar_events ORDER BY event_datetime ASC")
            events = cur.fetchall()
        for event in events:
            dt_str = event["event_datetime"].strftime("%d/%m/%Y %H:%M")
            option_label = f"ID: {event['id']} | {event['name']} ({dt_str})"
            options.append(discord.SelectOption(label=option_label, value=str(event["id"])))
        select = discord.ui.Select(placeholder="Selecciona un evento...", options=options)
        async def select_callback(interaction: discord.Interaction):
            selected_event_id = int(select.values[0])
            await notificar_evento(self.ctx, event_id=selected_event_id)
            await interaction.response.send_message(f"Notificando el evento ID {selected_event_id}.", ephemeral=True)
        select.callback = select_callback
        self.add_item(select)

@bot.command()
async def ver_eventos(ctx):
    if not is_owner_and_allowed(ctx):
        try:
            await ctx.message.delete()
        except:
            pass
        return
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM calendar_events ORDER BY event_datetime ASC")
        events = cur.fetchall()
    if not events:
        await send_public_message("No hay eventos registrados.")
        try:
            await ctx.message.delete()
        except:
            pass
        return
    message_lines = ["**Lista de Eventos Registrados:**"]
    for event in events:
        event_dt = event["event_datetime"]
        line = f"ID: {event['id']} | Nombre: {event['name']} | Fecha: {event_dt.strftime('%d/%m/%Y %H:%M')} | Etapa: {event['target_stage']}"
        message_lines.append(line)
    full_message = "\n".join(message_lines)
    await send_public_message(full_message)
    try:
        await ctx.message.delete()
    except:
        pass

######################################
# COMANDOS DE ADMINISTRACIÓN PARA REGISTROS (solo OWNER_ID)
######################################
@bot.command()
async def lista_registros(ctx):
    if not is_owner_and_allowed(ctx):
        try:
            await ctx.message.delete()
        except:
            pass
        return
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM registrations ORDER BY discord_name ASC")
        rows = cur.fetchall()
    if not rows:
        await send_public_message("No hay registros.")
        try:
            await ctx.message.delete()
        except:
            pass
        return
    lines = ["**Lista de Registros:**"]
    for row in rows:
        line = f"Discord: {row['discord_name']} (ID: {row['user_id']}) | Fortnite: {row['fortnite_username']} | Plataforma: {row['platform']} | País: {row['country']}"
        lines.append(line)
    full_message = "\n".join(lines)
    await send_public_message(full_message)
    try:
        await ctx.message.delete()
    except:
        pass

@bot.command()
async def agregar_registro_manual(ctx, *, data_str: str):
    if not is_owner_and_allowed(ctx):
        try:
            await ctx.message.delete()
        except:
            pass
        return
    # Formato: discord_user_id | discord_name | fortnite_username | platform | country
    parts = [part.strip() for part in data_str.split("|")]
    if len(parts) < 5:
        await send_public_message("❌ Formato incorrecto. Usa: discord_user_id | discord_name | fortnite_username | platform | country")
        return
    user_id, discord_name, fortnite_username, platform, country = parts
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO registrations (user_id, discord_name, fortnite_username, platform, country)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (user_id) DO UPDATE SET
                discord_name = EXCLUDED.discord_name,
                fortnite_username = EXCLUDED.fortnite_username,
                platform = EXCLUDED.platform,
                country = EXCLUDED.country
        """, (user_id, discord_name, fortnite_username, platform, country))
    await send_public_message("✅ Registro manual agregado.")
    try:
        await ctx.message.delete()
    except:
        pass

######################################
# EVENTO ON_MESSAGE: Comandos de Lenguaje Natural y reenvío de DMs del campeón
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
            except Exception as e:
                print(f"Error forwarding message: {e}")
    if message.content.startswith("!") and message.author.id != OWNER_ID:
        try:
            await message.delete()
        except:
            pass
        return
    if message.content.startswith("!") and message.author.id == OWNER_ID:
        await bot.process_commands(message)
        return
    if message.author.bot:
        return
    def normalize_string_local(s):
        return ''.join(c for c in unicodedata.normalize('NFKD', s) if not unicodedata.combining(c)).replace(" ", "").lower()
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
