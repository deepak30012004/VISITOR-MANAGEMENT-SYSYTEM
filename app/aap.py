from flask import Flask, request, jsonify, send_from_directory
import sqlite3
import os
import base64
from datetime import timedelta
from flask_cors import CORS
from flask_jwt_extended import (
    JWTManager, create_access_token, jwt_required, get_jwt_identity
)
from werkzeug.security import generate_password_hash, check_password_hash
from flask_caching import Cache
import logging

# Initialize Flask App
app = Flask(__name__)
app.config["JWT_SECRET_KEY"] = "supersecurejwtkey"  # Change this for security
app.config["JWT_ACCESS_TOKEN_EXPIRES"] = timedelta(hours=1)

# Caching Configuration
app.config['CACHE_TYPE'] = 'SimpleCache'  # In-memory cache
app.config['CACHE_DEFAULT_TIMEOUT'] = 300  # Cache timeout (seconds)

# Initialize Caching
cache = Cache(app)

CORS(app, supports_credentials=True)
jwt = JWTManager(app)

DB_FILE = "visitor.db"
UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# # System Failure Handling: Database Connection with Fault Tolerance
# def get_db_connection(retries=3, delay=2):
#     """
#     Establish a database connection with fault tolerance.
#     Retries up to retries times with a delay between attempts.
#     """
#     for attempt in range(retries):
#         try:
#             conn = sqlite3.connect(DB_FILE)
#             return conn
#         except sqlite3.Error as e:
#             logging.error(f"Database connection failed (Attempt {attempt+1}): {e}")
#             time.sleep(delay)  # Wait before retrying
#     raise ConnectionError("Failed to connect to the database after multiple attempts.")
# Initialize Database and Ensure 'role' Column Exists
def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    # Create Users table for staff and managers
    cursor.execute('''  
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'staff'
        )
    ''')

    # Create Visitors table for visitors
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS visitors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            full_name TEXT NOT NULL,
            contact_info TEXT NOT NULL,
            purpose_of_visit TEXT NOT NULL,
            host_employee_name TEXT NOT NULL,
            host_department TEXT NOT NULL,
            company_name TEXT,
            check_in_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            check_out_time TIMESTAMP,
            photo_path TEXT,
            status TEXT DEFAULT 'pending'
        )
    ''')

    # Ensure the 'role' column exists
    cursor.execute("PRAGMA table_info(users);")
    columns = [column[1] for column in cursor.fetchall()]
    if 'role' not in columns:
        cursor.execute("ALTER TABLE users ADD COLUMN role TEXT DEFAULT 'staff';")
        conn.commit()
        app.logger.info("Added 'role' column to the 'users' table.")

    conn.commit()
    conn.close()

init_db()


# User class for staff and managers (Encapsulation, Inheritance)
class User:
    def __init__(self, username, password_hash=None, role=None):
        self.username = username
        self.password_hash = password_hash
        self.role = role  # 'staff' or 'manager'

    def save_to_db(self):
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        try:
            cursor.execute("INSERT INTO users (username, password, role) VALUES (?, ?, ?)", 
                           (self.username, self.password_hash, self.role))
            conn.commit()
        except sqlite3.IntegrityError:
            raise ValueError("Username already exists")
        finally:
            conn.close()

    @staticmethod
    def get_user_by_username(username):
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("SELECT id, password, role FROM users WHERE username = ?", (username,))
        user = cursor.fetchone()
        conn.close()
        if user:
            return User(username, user[1], user[2])
        return None

# Visitor class for visitor management
class Visitor:
    def __init__(self, full_name, contact_info, purpose_of_visit, host_employee_name, host_department, company_name="", photo_path=None):
        self.full_name = full_name
        self.contact_info = contact_info
        self.purpose_of_visit = purpose_of_visit
        self.host_employee_name = host_employee_name
        self.host_department = host_department
        self.company_name = company_name
        self.photo_path = photo_path

    def save_to_db(self):
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute(''' 
            INSERT INTO visitors (full_name, contact_info, purpose_of_visit, host_employee_name, host_department, company_name, photo_path)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (self.full_name, self.contact_info, self.purpose_of_visit, self.host_employee_name, self.host_department, self.company_name, self.photo_path))
        conn.commit()
        visitor_id = cursor.lastrowid
        conn.close()
        return visitor_id

    @classmethod
    def get_all_visitors(cls):
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("SELECT id, full_name, contact_info, purpose_of_visit, host_employee_name, status, photo_path FROM visitors")
        result = cursor.fetchall()
        conn.close()
        return result

# Initialize Flask routes
@app.route("/")
def home():
    return "Visitor Management System API is running!"

@app.route('/signup', methods=['POST'])
def signup():
    try:
        data = request.json
        username = data.get("username")
        password = data.get("password")
        role = data.get("role")  # staff or manager

        if not username or not password or not role:
            return jsonify({"error": "Username, password, and role are required"}), 400

        hashed_password = generate_password_hash(password)

        user = User(username, hashed_password, role)
        user.save_to_db()
        return jsonify({"message": "User registered successfully"}), 201
    except Exception as e:
        app.logger.error(f"Error during signup: {e}")
        return jsonify({"error": "An error occurred during signup"}), 500


##LOGIN AUTHENTICATION
@app.route('/login', methods=['POST'])
def login():
    data = request.json
    username = data.get("username")
    password = data.get("password")

    user = User.get_user_by_username(username)
    if user and check_password_hash(user.password_hash, password):
        token = create_access_token(identity=username)
        return jsonify({"message": "Login successful", "token": token, "role": user.role}), 200
    else:
        return jsonify({"error": "Invalid credentials"}), 401

@app.route('/visitors', methods=['POST'])
@jwt_required()
def add_visitor():
    current_user = get_jwt_identity()
    user = User.get_user_by_username(current_user)

    if user.role != 'staff':
        return jsonify({"error": "You do not have permission to add visitors"}), 403

    data = request.json
    full_name = data.get("full_name")
    contact_info = data.get("contact_info")
    purpose_of_visit = data.get("purpose_of_visit")
    host_employee_name = data.get("host_employee_name")
    host_department = data.get("host_department")
    company_name = data.get("company_name", "")
    photo_base64 = data.get("photo", "")

    photo_filename = None
    if photo_base64:
        try:
            photo_data = base64.b64decode(photo_base64.split(",")[1])
            photo_filename = f"{full_name.replace(' ', '_')}.jpg"
            photo_path = os.path.join(UPLOAD_FOLDER, photo_filename)
            with open(photo_path, "wb") as f:
                f.write(photo_data)
        except Exception as e:
            app.logger.error(f"Failed to save image for visitor {full_name}: {e}")
            photo_filename = None

    visitor = Visitor(full_name, contact_info, purpose_of_visit, host_employee_name, host_department, company_name, photo_filename)
    visitor_id = visitor.save_to_db()
    return jsonify({"message": "Visitor added successfully", "id": visitor_id}), 201


##AUTHENTICATION REQUIRE FOR FETTING VISITOR
@app.route('/visitors', methods=['GET'])
@jwt_required()
 # Cache for 60 seconds
def get_visitors():
    current_user = get_jwt_identity()
    user = User.get_user_by_username(current_user)

    # if user.role != 'manager':
    #     return jsonify({"error": "You do not have permission to view visitors"}), 403

    visitors = Visitor.get_all_visitors()
    visitor_list = [
        {"id": v[0], "full_name": v[1], "contact_info": v[2], "purpose_of_visit": v[3], "host_employee_name": v[4], "status": v[5], "photo_path": v[6]}
        for v in visitors
    ]
    return jsonify(visitor_list), 200


##AUTHENTICATION REQUIRED FOR APPROVAL
@app.route('/visitors/approve/<int:visitor_id>', methods=['PUT'])
@jwt_required()
def approve_visitor(visitor_id):
    current_user = get_jwt_identity()
    user = User.get_user_by_username(current_user)

    if user.role != 'manager':
        return jsonify({"error": "You do not have permission to approve visitors"}), 403

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    try:
        cursor.execute("UPDATE visitors SET status = 'approved' WHERE id = ?", (visitor_id,))
        conn.commit()
    except Exception as e:
        conn.rollback()
        app.logger.error(f"Failed to approve visitor {visitor_id}: {e}")
        return jsonify({"error": "Failed to approve visitor"}), 500
    finally:
        conn.close()

    return jsonify({"message": "Visitor approved"}), 200

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)

if __name__ == "__main__":
    app.run(debug=True)
