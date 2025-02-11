import psycopg2
import os
import unicodedata

# Conexión a la base de datos
DATABASE_URL = os.environ.get("DATABASE_URL")  # Asegúrate de que esta variable de entorno esté configurada
conn = psycopg2.connect(DATABASE_URL)
conn.autocommit = True

def normalize_string(s):
    return ''.join(c for c in unicodedata.normalize('NFKD', s)
                   if not unicodedata.combining(c)).replace(" ", "").lower()

def load_jokes(jokes):
    with conn.cursor() as cur:
        for joke in jokes:
            cur.execute("INSERT INTO jokes (joke_text) VALUES (%s)", (joke,))
    print(f"Se han insertado {len(jokes)} chistes en la base de datos.")

def load_trivia(trivia_list):
    with conn.cursor() as cur:
        for trivia in trivia_list:
            cur.execute("""
                INSERT INTO trivia (question, answer, hint)
                VALUES (%s, %s, %s)
            """, (trivia['question'], trivia['answer'], trivia['hint']))
    print(f"Se han insertado {len(trivia_list)} trivias en la base de datos.")

if __name__ == "__main__":
    # Lista de chistes
    jokes = [
        "¿Por qué los programadores confunden Halloween y Navidad? Porque OCT 31 == DEC 25.",
        "¿Qué hace una abeja en el gimnasio? ¡Zum-ba!",
        # Agrega aquí todos tus chistes
    ]

    # Lista de trivias
    trivia_list = [
        {"question": "¿Quién escribió 'Cien Años de Soledad'?", "answer": "gabriel garcía márquez", "hint": "Su nombre comienza con 'G'."},
        {"question": "¿Cuál es el río más largo del mundo?", "answer": "amazonas", "hint": "Está en América del Sur y su nombre comienza con 'A'."},
        # Agrega aquí todas tus trivias con sus pistas
    ]

    load_jokes(jokes)
    load_trivia(trivia_list)

    conn.close()
