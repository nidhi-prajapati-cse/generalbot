from flask import Flask, request, jsonify
from flask_cors import CORS
import subprocess
import sys
import time
from gtts import gTTS
import pygame
import os
import speech_recognition as sr
import google.generativeai as genai
from dotenv import load_dotenv
from datetime import datetime, timedelta
import re
import jwt
import mysql.connector
from flask_bcrypt import Bcrypt
from functools import wraps

# =========================================================
# LOAD ENV
# =========================================================

load_dotenv()

# =========================================================
# APP SETUP
# =========================================================

app = Flask(__name__)

CORS(app)

app.config["SECRET_KEY"] = "speakease_super_secret_key_2026_for_jwt_authentication"

bcrypt = Bcrypt(app)

# =========================================================
# DATABASE
# =========================================================


def get_db_connection():
    return mysql.connector.connect(
        host=os.getenv("MYSQLHOST"),
        user=os.getenv("MYSQLUSER"),
        password=os.getenv("MYSQLPASSWORD"),
        database=os.getenv("MYSQLDATABASE"),
        port=os.getenv("MYSQLPORT")
    )

# =========================================================
# UPDATE USER STATS
# =========================================================


def update_user_stats(
    user_id,
    xp=0,
    chats=0,
    voice=0,
    pdf=0
):

    try:

        conn = get_db_connection()

        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT
                xp_points,
                streak_days,
                badges_count,
                total_chats,
                total_voice_uses,
                pdf_exports,
                last_active
            FROM user_stats
            WHERE user_id = %s
        """, (user_id,))

        stats = cursor.fetchone()

        if not stats:
            return

        # =====================================================
        # DAILY STREAK
        # =====================================================

        streak = stats["streak_days"] or 0

        last_active = stats["last_active"]

        today = datetime.utcnow().date()

        if last_active:

            if isinstance(last_active, datetime):
                last_date = last_active.date()
            else:
                last_date = last_active

            difference = (today - last_date).days

            if difference == 1:
                streak += 1

            elif difference > 1:
                streak = 1

        else:
            streak = 1

        # =====================================================
        # UPDATED VALUES
        # =====================================================

        new_xp = stats["xp_points"] + xp

        new_chats = stats["total_chats"] + chats

        new_voice = stats["total_voice_uses"] + voice

        new_pdf = stats["pdf_exports"] + pdf

        # =====================================================
        # BADGES
        # =====================================================

        badges = 0

        if new_chats >= 1:
            badges += 1

        if new_voice >= 5:
            badges += 1

        if new_pdf >= 1:
            badges += 1

        if new_xp >= 1000:
            badges += 1

        if streak >= 7:
            badges += 1

        # =====================================================
        # UPDATE DATABASE
        # =====================================================

        cursor.execute("""
            UPDATE user_stats
            SET
                xp_points = %s,
                streak_days = %s,
                badges_count = %s,
                total_chats = %s,
                total_voice_uses = %s,
                pdf_exports = %s,
                last_active = %s
            WHERE user_id = %s
        """, (
            new_xp,
            streak,
            badges,
            new_chats,
            new_voice,
            new_pdf,
            today,
            user_id
        ))

        conn.commit()

        cursor.close()
        conn.close()

    except Exception as e:
        print("Stats update error:", e)

# =========================================================
# AUTH MIDDLEWARE
# =========================================================


def token_required(f):

    @wraps(f)
    def decorated(*args, **kwargs):

        auth_header = request.headers.get("Authorization")

        if not auth_header or not auth_header.startswith("Bearer "):

            return jsonify({
                "error": "Token is missing"
            }), 401

        token = auth_header.split(" ")[1]

        try:

            data = jwt.decode(
                token,
                app.config["SECRET_KEY"],
                algorithms=["HS256"]
            )

            current_user_id = data["user_id"]

        except jwt.ExpiredSignatureError:

            return jsonify({
                "error": "Token expired"
            }), 401

        except Exception:

            return jsonify({
                "error": "Invalid token"
            }), 401

        return f(current_user_id, *args, **kwargs)

    return decorated

# =========================================================
# GEMINI SETUP
# =========================================================


GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

genai.configure(api_key=GEMINI_API_KEY)

try:

    gemini_model = genai.GenerativeModel("gemini-2.5-flash")

except Exception as e:

    print("Gemini Init Error:", e)

    gemini_model = None

# =========================================================
# SPEAK FUNCTION
# =========================================================


def speak(text):

    safe_text = str(text)

    try:

        subprocess.run(
            [
                sys.executable,
                "-c",
                f"""
import pyttsx3
engine = pyttsx3.init()
engine.setProperty("rate", 170)
engine.say({repr(safe_text)})
engine.runAndWait()
"""
            ],
            check=True
        )

        return "Spoken successfully"

    except Exception:

        try:

            filename = "temp_tts.mp3"

            tts = gTTS(text=safe_text, lang="en")

            tts.save(filename)

            pygame.mixer.init()

            pygame.mixer.music.load(filename)

            pygame.mixer.music.play()

            while pygame.mixer.music.get_busy():
                time.sleep(0.1)

            pygame.mixer.music.unload()

            pygame.mixer.quit()

            if os.path.exists(filename):
                os.remove(filename)

            return "Spoken successfully"

        except Exception as e:

            return str(e)

# =========================================================
# SPEECH TO TEXT
# =========================================================


def speech_to_text():

    recognizer = sr.Recognizer()

    with sr.Microphone() as source:

        print("Listening...")

        audio = recognizer.listen(source)

    try:

        return recognizer.recognize_google(audio)

    except sr.UnknownValueError:

        return "Could not understand audio"

    except sr.RequestError as e:

        return str(e)

# =========================================================
# CHATBOT RESPONSE
# =========================================================


def chatbot_response(user_input):

    if not gemini_model:
        return "Gemini model not initialized"

    now = datetime.now()

    prompt = f"""
You are a helpful assistant.

Current date: {now.strftime('%d %B %Y')}
Current day: {now.strftime('%A')}
Current time: {now.strftime('%I:%M %p')}

User question: {user_input}
"""

    try:

        response = gemini_model.generate_content(prompt)

        return response.text.strip()

    except Exception as e:

        return f"Gemini API error: {e}"

# =========================================================
# HOME
# =========================================================


@app.route("/", methods=["GET"])
def home():

    return jsonify({
        "status": "SpeakEase backend running"
    })

# =========================================================
# SIGNUP
# =========================================================


@app.route("/signup", methods=["POST"])
def signup():

    data = request.get_json()

    name = data.get("name", "").strip()

    email = data.get("email", "").strip().lower()

    password = data.get("password", "").strip()

    if not name or not email or not password:

        return jsonify({
            "error": "All fields are required"
        }), 400

    if len(password) < 6:

        return jsonify({
            "error": "Password must be at least 6 characters"
        }), 400

    hashed_password = bcrypt.generate_password_hash(
        password
    ).decode("utf-8")

    try:

        conn = get_db_connection()

        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO users
            (name, email, password)
            VALUES (%s, %s, %s)
        """, (
            name,
            email,
            hashed_password
        ))

        conn.commit()

        user_id = cursor.lastrowid

        cursor.execute("""
            INSERT INTO user_stats
            (
                user_id,
                xp_points,
                streak_days,
                badges_count,
                total_chats,
                total_voice_uses,
                pdf_exports,
                last_active
            )
            VALUES
            (
                %s,
                0,
                0,
                0,
                0,
                0,
                0,
                NULL
            )
        """, (user_id,))

        conn.commit()

        token = jwt.encode(
            {
                "user_id": user_id,
                "email": email,
                "exp": datetime.utcnow() + timedelta(hours=24)
            },
            app.config["SECRET_KEY"],
            algorithm="HS256"
        )

        cursor.close()
        conn.close()

        return jsonify({
            "message": "Signup successful",
            "token": token,
            "user": {
                "id": user_id,
                "name": name,
                "email": email
            }
        }), 201

    except mysql.connector.IntegrityError:

        return jsonify({
            "error": "Email already exists"
        }), 409

    except Exception as e:

        return jsonify({
            "error": str(e)
        }), 500

# =========================================================
# LOGIN
# =========================================================


@app.route("/login", methods=["POST"])
def login():

    data = request.get_json()

    email = data.get("email", "").strip().lower()

    password = data.get("password", "").strip()

    try:

        conn = get_db_connection()

        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT *
            FROM users
            WHERE email = %s
        """, (email,))

        user = cursor.fetchone()

        cursor.close()
        conn.close()

        if not user:

            return jsonify({
                "error": "Invalid credentials"
            }), 401

        if not bcrypt.check_password_hash(
            user["password"],
            password
        ):

            return jsonify({
                "error": "Invalid credentials"
            }), 401

        token = jwt.encode(
            {
                "user_id": user["id"],
                "email": user["email"],
                "exp": datetime.utcnow() + timedelta(hours=24)
            },
            app.config["SECRET_KEY"],
            algorithm="HS256"
        )

        return jsonify({
            "message": "Login successful",
            "token": token,
            "user": {
                "id": user["id"],
                "name": user["name"],
                "email": user["email"]
            }
        }), 200

    except Exception as e:

        return jsonify({
            "error": str(e)
        }), 500

# =========================================================
# GET CHAT SESSIONS
# =========================================================


@app.route("/chat/sessions", methods=["GET"])
@token_required
def get_chat_sessions(current_user_id):

    try:

        conn = get_db_connection()

        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT
                id,
                title,
                created_at,
                updated_at
            FROM chat_sessions
            WHERE user_id = %s
            ORDER BY updated_at DESC
        """, (current_user_id,))

        sessions = cursor.fetchall()

        cursor.close()
        conn.close()

        return jsonify({
            "sessions": sessions
        }), 200

    except Exception as e:

        return jsonify({
            "error": str(e)
        }), 500

# =========================================================
# CREATE NEW CHAT SESSION
# =========================================================


@app.route("/chat/session", methods=["POST"])
@token_required
def create_chat_session(current_user_id):

    try:

        conn = get_db_connection()

        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO chat_sessions
            (user_id, title)
            VALUES (%s, %s)
        """, (
            current_user_id,
            "New Chat"
        ))

        conn.commit()

        session_id = cursor.lastrowid

        cursor.close()
        conn.close()

        return jsonify({
            "message": "Session created",
            "session_id": session_id,
            "title": "New Chat"
        }), 201

    except Exception as e:

        return jsonify({
            "error": str(e)
        }), 500

# =========================================================
# GET SESSION MESSAGES
# =========================================================


@app.route("/chat/session/<int:session_id>", methods=["GET"])
@token_required
def get_session_messages(current_user_id, session_id):

    try:

        conn = get_db_connection()

        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT
                id,
                sender,
                message,
                created_at
            FROM chat_messages
            WHERE
                user_id = %s
                AND session_id = %s
            ORDER BY created_at ASC
        """, (
            current_user_id,
            session_id
        ))

        messages = cursor.fetchall()

        cursor.close()
        conn.close()

        return jsonify({
            "messages": messages
        }), 200

    except Exception as e:

        return jsonify({
            "error": str(e)
        }), 500

# =========================================================
# CHAT
# =========================================================


@app.route("/chat", methods=["POST"])
@token_required
def chat(current_user_id):

    data = request.get_json()

    user_message = data.get("message", "").strip()

    session_id = data.get("session_id")

    if not user_message:

        return jsonify({
            "response": "Please type something"
        }), 400

    try:

        conn = get_db_connection()

        cursor = conn.cursor(dictionary=True)

        # =====================================================
        # CREATE SESSION IF NOT EXISTS
        # =====================================================

        if not session_id:

            cursor.execute("""
                INSERT INTO chat_sessions
                (user_id, title)
                VALUES (%s, %s)
            """, (
                current_user_id,
                user_message[:40]
            ))

            conn.commit()

            session_id = cursor.lastrowid

        # =====================================================
        # BOT RESPONSE
        # =====================================================

        response = chatbot_response(user_message)

        # =====================================================
        # SAVE USER MESSAGE
        # =====================================================

        cursor.execute("""
            INSERT INTO chat_messages
            (
                session_id,
                user_id,
                sender,
                message
            )
            VALUES (%s, %s, %s, %s)
        """, (
            session_id,
            current_user_id,
            "user",
            user_message
        ))

        # =====================================================
        # SAVE BOT MESSAGE
        # =====================================================

        cursor.execute("""
            INSERT INTO chat_messages
            (
                session_id,
                user_id,
                sender,
                message
            )
            VALUES (%s, %s, %s, %s)
        """, (
            session_id,
            current_user_id,
            "bot",
            response
        ))

        # =====================================================
        # UPDATE SESSION TITLE
        # =====================================================

        cursor.execute("""
            UPDATE chat_sessions
            SET
                title = CASE
                    WHEN title = 'New Chat'
                    THEN %s
                    ELSE title
                END,
                updated_at = CURRENT_TIMESTAMP
            WHERE
                id = %s
                AND user_id = %s
        """, (
            user_message[:40],
            session_id,
            current_user_id
        ))

        conn.commit()

        cursor.close()
        conn.close()

        # =====================================================
        # UPDATE STATS
        # =====================================================

        update_user_stats(
            current_user_id,
            xp=10,
            chats=1
        )

        return jsonify({
            "response": response,
            "session_id": session_id
        }), 200

    except Exception as e:

        return jsonify({
            "error": str(e)
        }), 500

# =========================================================
# PROFILE STATS
# =========================================================


@app.route("/profile/stats", methods=["GET"])
@token_required
def get_profile_stats(current_user_id):

    try:

        conn = get_db_connection()

        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT
                xp_points,
                streak_days,
                badges_count,
                total_chats,
                total_voice_uses,
                pdf_exports
            FROM user_stats
            WHERE user_id = %s
        """, (current_user_id,))

        stats = cursor.fetchone()

        cursor.close()
        conn.close()

        if not stats:

            return jsonify({
                "xp_points": 0,
                "streak_days": 0,
                "badges_count": 0,
                "total_chats": 0,
                "total_voice_uses": 0,
                "pdf_exports": 0
            }), 200

        return jsonify(stats), 200

    except Exception as e:

        return jsonify({
            "error": str(e)
        }), 500

# =========================================================
# SPEAK
# =========================================================


@app.route("/speak", methods=["POST"])
@token_required
def api_speak(current_user_id):

    data = request.get_json()

    text = data.get("text", "")

    if not text.strip():

        return jsonify({
            "status": "No text"
        }), 400

    result = speak(text)

    return jsonify({
        "status": result
    })

# =========================================================
# SPEECH TO TEXT
# =========================================================


@app.route("/speech_to_text", methods=["GET"])
@token_required
def api_speech_to_text(current_user_id):

    result = speech_to_text()

    update_user_stats(
        current_user_id,
        xp=5,
        voice=1
    )

    return jsonify({
        "text": result
    })

# =========================================================
# CHAT VIA SPEECH
# =========================================================


@app.route("/chat_via_speech", methods=["GET"])
@token_required
def api_chat_via_speech(current_user_id):

    user_text = speech_to_text()

    if user_text.startswith("Could not"):

        return jsonify({
            "error": user_text
        }), 400

    bot_response = chatbot_response(user_text)

    speak(bot_response)

    update_user_stats(
        current_user_id,
        xp=15,
        chats=1,
        voice=1
    )

    return jsonify({
        "user_text": user_text,
        "bot_response": bot_response
    })

# =========================================================
# RUN
# =========================================================


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
