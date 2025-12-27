import cv2
import face_recognition
import pickle
import time
import threading
import uvicorn
import datetime
import numpy as np
import telebot
import qrcode
import uuid
from io import BytesIO
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Boolean
from sqlalchemy.orm import sessionmaker, declarative_base
import RPi.GPIO as GPIO
from fastapi import FastAPI, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware

# --- CONFIGURATION ---
TELEGRAM_TOKEN = "YOUR_TELEGRAM_BOT_TOKEN"
ADMIN_ID = 00000000 # Replace with your ID
API_SECRET_KEY = "YOUR_SECRET_KEY"

RELAY_PIN = 17
SERVO_PIN = 18
SERVO_OPEN_ANGLE = 90
SERVO_CLOSE_ANGLE = 0

DATABASE_URL = "sqlite:///./door.db"

# --- DATABASE ---
Base = declarative_base()
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

class AccessLog(Base):
    __tablename__ = "logs"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    timestamp = Column(DateTime, default=datetime.datetime.now)
    method = Column(String) # "face", "qr", "remote"

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    qr_token = Column(String, unique=True) # Secret token encoded in QR
    is_active = Column(Boolean, default=True)

Base.metadata.create_all(bind=engine)

# --- INITIALIZATION ---
bot = telebot.TeleBot(TELEGRAM_TOKEN)
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# GPIO
GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)
GPIO.setup(RELAY_PIN, GPIO.OUT)
GPIO.output(RELAY_PIN, GPIO.LOW) 
GPIO.setup(SERVO_PIN, GPIO.OUT)
servo = GPIO.PWM(SERVO_PIN, 50)
servo.start(0)

# Face Recognition & QR
qr_detector = cv2.QRCodeDetector()
camera = cv2.VideoCapture(0)
is_door_opening = False

print("[INFO] Loading face database...")
try:
    data = pickle.loads(open("encodings.pickle", "rb").read())
    known_encodings = data["encodings"]
    known_names = data["names"]
except FileNotFoundError:
    print("[WARN] Face database is empty!")
    known_encodings = []
    known_names = []

# --- UTILITY FUNCTIONS ---
def get_db():
    return SessionLocal()

def log_access(name, method):
    db = get_db()
    new_log = AccessLog(name=name, method=method)
    db.add(new_log)
    db.commit()
    db.close()

def set_servo_angle(angle):
    duty = angle / 18 + 2
    GPIO.output(SERVO_PIN, True)
    servo.ChangeDutyCycle(duty)
    time.sleep(0.5)
    GPIO.output(SERVO_PIN, False)
    servo.ChangeDutyCycle(0)

def open_door_sequence(name, method="face"):
    global is_door_opening
    is_door_opening = True
    print(f"[ACCESS] Opening for: {name} ({method})")
    
    log_access(name, method)
    bot.send_message(ADMIN_ID, f"🔓 Access granted: {name} (via {method})")

    GPIO.output(RELAY_PIN, GPIO.HIGH) 
    time.sleep(0.2)
    set_servo_angle(SERVO_OPEN_ANGLE)
    
    time.sleep(5) 
    
    set_servo_angle(SERVO_CLOSE_ANGLE)
    GPIO.output(RELAY_PIN, GPIO.LOW) 
    
    is_door_opening = False

def send_alert_async(image_bytes):
    try:
        bot.send_photo(ADMIN_ID, photo=image_bytes, caption="⚠️ Unknown person at door!")
    except Exception as e:
        print(f"[TG ERROR] {e}")
        
# --- TELEGRAM COMMANDS ---
@bot.message_handler(commands=['start'])
def send_welcome(message):
    if message.chat.id == ADMIN_ID:
        bot.reply_to(message, "System is active.\nCommands:\n/open - Open door\n/invite <Name> - Create QR\n/users - List users")

@bot.message_handler(commands=['open'])
def remote_open(message):
    if message.chat.id == ADMIN_ID:
        if not is_door_opening:
            threading.Thread(target=open_door_sequence, args=("TelegramAdmin", "remote")).start()
            bot.reply_to(message, "Opening...")

@bot.message_handler(commands=['invite'])
def create_invite(message):
    if message.chat.id != ADMIN_ID: return
    
    try:
        # Parse username
        username = message.text.split()[1]
    except IndexError:
        bot.reply_to(message, "Usage: /invite UserName")
        return

    db = get_db()
    # Generate unique token
    token = str(uuid.uuid4())
    
    # Save to DB
    user = User(username=username, qr_token=token)
    try:
        db.add(user)
        db.commit()
    except Exception:
        bot.reply_to(message, "This user already exists!")
        db.close()
        return

    # Generate QR image
    qr = qrcode.QRCode(box_size=10, border=4)
    qr.add_data(token)
    qr.make(fit=True)
    img = qr.make_image(fill='black', back_color='white')
    
    # Send QR to admin
    bio = BytesIO()
    img.save(bio, 'PNG')
    bio.seek(0)
    bot.send_photo(message.chat.id, photo=bio, caption=f"QR pass for: {username}")
    db.close()

@bot.message_handler(commands=['users'])
def list_users(message):
    if message.chat.id != ADMIN_ID: return
    db = get_db()
    users = db.query(User).all()
    text = "👥 Users with QR:\n"
    for u in users:
        text += f"- {u.username} (Active: {u.is_active})\n"
    bot.reply_to(message, text)
    db.close()

def start_bot():
    bot.polling(non_stop=True)
    
# --- MAIN LOOP ---
def generate_frames():
    last_telegram_alert_time = 0
    process_this_frame = True
    
    while True:
        success, frame = camera.read()
        if not success: break
        
        # 1. Find and Decode QR Code
        try:
            decoded_text, points, _ = qr_detector.detectAndDecode(frame)
            if decoded_text:
                # Draw QR frame
                if points is not None:
                    points = points[0].astype(int)
                    for i in range(len(points)):
                        pt1 = tuple(points[i])
                        pt2 = tuple(points[(i+1) % 4])
                        cv2.line(frame, pt1, pt2, (255, 0, 0), 3)

                # Check QR in DB
                if not is_door_opening:
                    db = get_db()
                    user = db.query(User).filter(User.qr_token == decoded_text).first()
                    db.close()
                    
                    if user and user.is_active:
                        threading.Thread(target=open_door_sequence, args=(user.username, "qr")).start()
                        time.sleep(3) 
                    else:
                         cv2.putText(frame, "INVALID QR", (50, 50), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)
        except Exception as e:
            print(f"QR Error: {e}")
            
        # 2. FACE RECOGNITION (On a smaller frame)
        if process_this_frame:
            small_frame = cv2.resize(frame, (0, 0), fx=0.5, fy=0.5)
            rgb_small_frame = cv2.cvtColor(small_frame, cv2.COLOR_BGR2RGB)
            
            face_locations = face_recognition.face_locations(rgb_small_frame)
            
            if len(known_encodings) > 0:
                face_encodings = face_recognition.face_encodings(rgb_small_frame, face_locations)
                
                face_names = []
                for face_encoding in face_encodings:
                    face_distances = face_recognition.face_distance(known_encodings, face_encoding)
                    best_match_index = np.argmin(face_distances)
                    name = "Unknown"

                    if face_distances[best_match_index] < 0.38:
                        name = known_names[best_match_index]
                        if not is_door_opening:
                            threading.Thread(target=open_door_sequence, args=(name, "face")).start()
                    
                    # Notify admin on unknown face
                    if name == "Unknown":
                        current_time = time.time()
                        if (current_time - last_telegram_alert_time) > 30:
                            last_telegram_alert_time = current_time
                            ret_alert, buf_alert = cv2.imencode('.jpg', frame)
                            if ret_alert:
                                threading.Thread(target=send_alert_async, args=(buf_alert.tobytes(),)).start()
                    
                    face_names.append(name)
            else:
                face_names = ["Unknown"] * len(face_locations)
        
        process_this_frame = not process_this_frame

        # Draw names
        for (top, right, bottom, left), name in zip(face_locations, face_names):
            top *= 2; right *= 2; bottom *= 2; left *= 2
            color = (0, 255, 0) if name != "Unknown" else (0, 0, 255)
            cv2.rectangle(frame, (left, top), (right, bottom), color, 2)
            cv2.putText(frame, name, (left, bottom + 20), cv2.FONT_HERSHEY_DUPLEX, 0.8, (255, 255, 255), 1)

        ret, buffer = cv2.imencode('.jpg', frame)
        yield (b'--frame\r\n' b'Content-Type: image/jpeg\r\n\r\n' + buffer.tobytes() + b'\r\n')
        
# --- API ---
@app.get("/")
def root(): return {"status": "Active with QR"}

@app.get("/video_feed")
def video_feed(): return StreamingResponse(generate_frames(), media_type="multipart/x-mixed-replace;boundary=frame")

@app.get("/logs")
def get_logs():
    db = get_db()
    logs = db.query(AccessLog).order_by(AccessLog.timestamp.desc()).limit(15).all()
    db.close()
    return logs

@app.post("/open_remote")
def open_remote(x_api_key: str = Header(None)): # Read x-api-key header
    # Check key
    if x_api_key != API_SECRET_KEY:
        raise HTTPException(status_code=403, detail="Invalid API key")
    
    if is_door_opening:
        raise HTTPException(status_code=400, detail="Door is already opening")
        
    threading.Thread(target=open_door_sequence, args=("AppUser", "remote")).start()
    return {"message": "Access Granted"}

if __name__ == "__main__":
    threading.Thread(target=start_bot, daemon=True).start()
    try:
        uvicorn.run(app, host="0.0.0.0", port=8000)
    finally:
        GPIO.cleanup()
