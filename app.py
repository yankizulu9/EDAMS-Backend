from flask import Flask, request, jsonify, session
from flask_restful import Api, Resource
from flask_migrate import Migrate
from flask_login import LoginManager, UserMixin
from flask_jwt_extended import (
    JWTManager, create_access_token, jwt_required,
    get_jwt_identity, get_jwt
)
from flask_cors import CORS
from datetime import datetime, timedelta
import pytz
import os
import requests
import base64
import logging
import re
from sqlalchemy.orm import validates
from flask_bcrypt import Bcrypt
from flask_sqlalchemy import SQLAlchemy
from dotenv import load_dotenv

# --- INITIALIZATION ---
load_dotenv()

app = Flask(__name__)
api = Api(app)

# UPDATED: Secure CORS configuration for production
# This allows your specific Vercel frontend to communicate with this Render API
CORS(app, supports_credentials=True, resources={r"/*": {"origins": ["https://edams-relief.vercel.app"]}})

# Configuration
# Note: Render uses an ephemeral disk. SQLite data will reset on every deploy.
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv("DATABASE_URL", "sqlite:///edams.db")
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = os.getenv("SECRET_KEY", "super-secret-key-2026")
app.config['JWT_SECRET_KEY'] = os.getenv("JWT_SECRET_KEY", "jwt-secret-string-32-chars")
app.config['UPLOAD_FOLDER'] = os.path.join(os.getcwd(), 'uploads')
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024
app.config['REMEMBER_COOKIE_DURATION'] = timedelta(days=7)
app.config['SESSION_COOKIE_HTTPONLY'] = True

logging.basicConfig(level=logging.INFO)

db = SQLAlchemy()
bcrypt = Bcrypt()
migrate = Migrate()
login_manager = LoginManager()
jwt = JWTManager()

db.init_app(app)
bcrypt.init_app(app)
migrate.init_app(app, db)
login_manager.init_app(app)
jwt.init_app(app)

# --- MODELS ---

class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(128), nullable=False)
    role = db.Column(db.String(50), default='Donor') 
    is_approved = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    login_history = db.relationship('LoginHistory', backref='user', lazy=True, cascade='all, delete-orphan')
    payments = db.relationship('Payment', backref='user', lazy=True, cascade='all, delete-orphan')
    donations = db.relationship('Donation', backref='donor', lazy=True, cascade='all, delete-orphan')
    recipient_profile = db.relationship('RecipientProfile', backref='user', uselist=False, cascade='all, delete-orphan')
    tasks = db.relationship('Task', backref='volunteer', lazy=True, cascade='all, delete-orphan')

    def set_password(self, password):
        self.password = bcrypt.generate_password_hash(password).decode('utf-8')

    def check_password(self, password):
        return bcrypt.check_password_hash(self.password, password)

    @validates('email')
    def validate_email(self, key, email):
        regex = r'^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$'
        if not re.match(regex, email):
            raise ValueError("Invalid email address")
        return email

    def to_dict(self):
        return {
            "id": self.id,
            "username": self.username,
            "email": self.email,
            "role": self.role,
            "is_approved": self.is_approved,
            "created_at": self.created_at.isoformat()
        }

class Task(db.Model):
    __tablename__ = 'tasks'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    title = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text, nullable=True)
    is_completed = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "is_completed": self.is_completed
        }

class Donation(db.Model):
    __tablename__ = 'donations'
    id = db.Column(db.Integer, primary_key=True)
    donor_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    target_area = db.Column(db.String(100), nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

class RecipientProfile(db.Model):
    __tablename__ = 'recipient_profiles'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    gender = db.Column(db.String(20))
    number_of_kids = db.Column(db.Integer, default=0)
    city = db.Column(db.String(100)) 
    urgent_need = db.Column(db.String(255)) 

    def to_dict(self):
        return {
            "gender": self.gender,
            "number_of_kids": self.number_of_kids,
            "city": self.city,
            "urgent_need": self.urgent_need
        }

class LoginHistory(db.Model):
    __tablename__ = 'login_history'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    login_time = db.Column(db.DateTime, default=datetime.utcnow)
    logout_time = db.Column(db.DateTime, nullable=True)

class Payment(db.Model):
    __tablename__ = 'payments'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    phone_number = db.Column(db.String(20), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    transaction_id = db.Column(db.String(100), nullable=True)
    result_code = db.Column(db.Integer, nullable=True)
    result_desc = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

# --- UTILS ---

def seed_admin():
    admin_email = "admin@edams.com"
    existing_admin = User.query.filter_by(email=admin_email).first()
    if not existing_admin:
        admin = User(username="SystemAdmin", email=admin_email, role="Admin", is_approved=True)
        admin.set_password("Admin@EDAMS2026") 
        db.session.add(admin)
        db.session.commit()
        print("✅ Default Admin Seeded")

def seed_tasks_for_user(user_id):
    existing_tasks = Task.query.filter_by(user_id=user_id).first()
    if not existing_tasks:
        t1 = Task(user_id=user_id, title="Verify Water Supply", description="Check local supply units.")
        t2 = Task(user_id=user_id, title="Food Distribution Log", description="Update crossing inventory.")
        db.session.add_all([t1, t2])
        db.session.commit()
        print(f"✅ Initial Tasks Seeded for User ID: {user_id}")

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

blacklist = set()

@jwt.token_in_blocklist_loader
def check_if_token_in_blacklist(jwt_header, jwt_payload):
    return jwt_payload['jti'] in blacklist

# --- MPESA UTILS ---

CONSUMER_KEY = os.getenv("CONSUMER_KEY")
CONSUMER_SECRET = os.getenv("CONSUMER_SECRET")
SHORTCODE = os.getenv("SHORTCODE")
PASSKEY = os.getenv("LIPA_NA_MPESA_ONLINE_PASSKEY")
CALLBACK_URL = os.getenv("CALLBACK_URL")

def format_phone(phone):
    phone = str(phone).strip().replace("+", "")
    if phone.startswith("0"):
        phone = "254" + phone[1:]
    return phone

def get_access_token():
    url = "https://sandbox.safaricom.co.ke/oauth/v1/generate?grant_type=client_credentials"
    if not CONSUMER_KEY or not CONSUMER_SECRET: return None
    credentials = base64.b64encode(f"{CONSUMER_KEY}:{CONSUMER_SECRET}".encode()).decode()
    headers = {"Authorization": f"Basic {credentials}"}
    try:
        response = requests.get(url, headers=headers)
        return response.json()["access_token"] if response.status_code == 200 else None
    except: return None

def generate_mpesa_password():
    timestamp = datetime.now(pytz.timezone("Africa/Nairobi")).strftime("%Y%m%d%H%M%S")
    raw_password = f"{SHORTCODE}{PASSKEY}{timestamp}"
    return base64.b64encode(raw_password.encode()).decode(), timestamp

# --- RESOURCES ---

class AdminSummaryResource(Resource):
    @jwt_required()
    def get(self):
        pending_count = User.query.filter_by(role='Recipient', is_approved=False).count()
        total_funds = db.session.query(db.func.sum(Payment.amount)).filter(Payment.result_code == 0).scalar() or 0
        active_volunteers = User.query.filter_by(role='Volunteer').count()

        return {
            "pending_requests": pending_count,
            "total_funds": float(total_funds),
            "active_volunteers": active_volunteers,
            "system_status": "Stable" 
        }, 200

class SystemLogsResource(Resource):
    @jwt_required()
    def get(self):
        user_id = get_jwt_identity()
        admin = User.query.get(user_id)
        if not admin or admin.role != 'Admin':
            return {"message": "Forbidden"}, 403

        logs = LoginHistory.query.order_by(LoginHistory.login_time.desc()).limit(50).all()
        return [{
            "username": log.user.username if log.user else "Unknown",
            "time": log.login_time.strftime("%Y-%m-%d %H:%M:%S"),
            "event": "System Login"
        } for log in logs], 200

class AidDistributionResource(Resource):
    @jwt_required()
    def get(self):
        user_id = get_jwt_identity()
        admin = User.query.get(user_id)
        if not admin or admin.role != 'Admin':
            return {"message": "Forbidden"}, 403

        payments = Payment.query.filter_by(result_code=0).order_by(Payment.created_at.desc()).all()
        return [{
            "transaction_id": p.transaction_id,
            "amount": p.amount,
            "phone": p.phone_number,
            "date": p.created_at.strftime("%Y-%m-%d %H:%M:%S")
        } for p in payments], 200

class Register(Resource):
    def post(self):
        data = request.get_json()
        role = data.get("role", "Donor")
        if role.lower() == "admin":
            return {"message": "Unauthorized role selection."}, 403
        if User.query.filter_by(email=data.get('email')).first():
            return {"message": "Email already exists"}, 400
        
        user = User(username=data.get("username"), email=data.get("email"), role=role)
        user.set_password(data.get("password"))
        db.session.add(user)
        db.session.commit()
        
        if role == "Volunteer":
            seed_tasks_for_user(user.id)
            
        return {"message": "Registration successful"}, 201

class Login(Resource):
    def post(self):
        data = request.get_json()
        user = User.query.filter_by(email=data.get('email')).first()
        if not user or not user.check_password(data.get('password')):
            return {"message": "Invalid credentials"}, 401
        
        token = create_access_token(identity=str(user.id))
        db.session.add(LoginHistory(user_id=user.id))
        db.session.commit()

        redirect_map = {
            "Admin": "AdminDashboard.html", 
            "Donor": "UserDashboard.html",
            "Volunteer": "VolunteerDashboard.html", 
            "Recipient": "RecipientDashboard.html"
        }
        return {"user": user.to_dict(), "access_token": token, "redirect_url": redirect_map.get(user.role, "index.html")}, 200

class Logout(Resource):
    @jwt_required()
    def post(self):
        blacklist.add(get_jwt()["jti"])
        return {"message": "Logged out"}, 200

class TaskListResource(Resource):
    @jwt_required()
    def get(self):
        user_id = get_jwt_identity()
        tasks = Task.query.filter_by(user_id=user_id).all()
        return [t.to_dict() for t in tasks], 200

class TaskCompleteResource(Resource):
    @jwt_required()
    def post(self, task_id):
        user_id = get_jwt_identity()
        task = Task.query.filter_by(id=task_id, user_id=user_id).first()
        if not task:
            return {"message": "Task not found"}, 404
        
        task.is_completed = True
        db.session.commit()
        return {"message": "Task marked as completed"}, 200

class RecipientProfileResource(Resource):
    @jwt_required()
    def get(self):
        user_id = get_jwt_identity()
        profile = RecipientProfile.query.filter_by(user_id=user_id).first()
        return (profile.to_dict(), 200) if profile else ({"message": "No profile"}, 404)

    @jwt_required()
    def post(self):
        data = request.get_json()
        user_id = get_jwt_identity()
        profile = RecipientProfile.query.filter_by(user_id=user_id).first() or RecipientProfile(user_id=user_id)
        profile.gender, profile.number_of_kids, profile.city, profile.urgent_need = data.get("gender"), data.get("number_of_kids"), data.get("city"), data.get("urgent_need")
        db.session.add(profile)
        db.session.commit()
        return {"message": "Profile updated"}, 200

class DonorStatsResource(Resource):
    @jwt_required()
    def get(self):
        user_id = get_jwt_identity()
        payments = Payment.query.filter_by(user_id=user_id, result_code=0).all()
        total = sum(p.amount for p in payments)
        return {
            "total_donated": float(total),
            "donation_count": len(payments),
            "history": [{"amount": p.amount, "date": p.created_at.strftime('%Y-%m-%d')} for p in payments]
        }, 200

class DonorHistoryResource(Resource):
    @jwt_required()
    def get(self):
        user_id = get_jwt_identity()
        payments = Payment.query.filter_by(user_id=user_id, result_code=0).order_by(Payment.created_at.desc()).all()
        return [{"transaction_id": p.transaction_id, "amount": p.amount, "phone": p.phone_number, "date": p.created_at.strftime("%Y-%m-%d %H:%M:%S"), "status": "Completed"} for p in payments], 200

class AdminRecipientsResource(Resource):
    @jwt_required()
    def get(self):
        user_id = get_jwt_identity()
        admin = User.query.get(user_id)
        if admin.role != 'Admin': return {"message": "Forbidden"}, 403
        
        recipients = User.query.filter_by(role='Recipient').all()
        return [r.to_dict() for r in recipients], 200

class AdminApproveResource(Resource):
    @jwt_required()
    def patch(self, recipient_id):
        user_id = get_jwt_identity()
        admin = User.query.get(user_id)
        if admin.role != 'Admin': return {"message": "Forbidden"}, 403

        recipient = User.query.get(recipient_id)
        if not recipient: return {"message": "User not found"}, 404
        
        data = request.get_json()
        recipient.is_approved = data.get("is_approved", True)
        db.session.commit()
        return {"message": f"Recipient status updated to {recipient.is_approved}"}, 200

class STKPushResource(Resource):
    @jwt_required()
    def post(self):
        data = request.get_json()
        amount = data.get("amount")
        phone = format_phone(data.get("phone_number"))
        access_token = get_access_token()
        
        if not access_token: return {"error": "M-Pesa auth failed"}, 401

        password, timestamp = generate_mpesa_password()
        payload = {
            "BusinessShortCode": SHORTCODE, "Password": password, "Timestamp": timestamp,
            "TransactionType": "CustomerPayBillOnline", "Amount": int(float(amount)),
            "PartyA": phone, "PartyB": SHORTCODE, "PhoneNumber": phone,
            "CallBackURL": CALLBACK_URL, "AccountReference": "EDAMS_Donation", "TransactionDesc": "Aid Relief"
        }
        
        try:
            resp = requests.post("https://sandbox.safaricom.co.ke/mpesa/stkpush/v1/processrequest", 
                                 json=payload, headers={"Authorization": f"Bearer {access_token}"})
            
            if resp.status_code == 200:
                db.session.add(Payment(
                    user_id=get_jwt_identity(), 
                    phone_number=phone, 
                    amount=float(amount), 
                    transaction_id=resp.json().get("CheckoutRequestID")
                ))
                db.session.commit()
            return resp.json(), resp.status_code
        except Exception as e:
            return {"error": str(e)}, 500

@app.route("/callback", methods=["POST"])
def mpesa_callback():
    data = request.get_json().get("Body", {}).get("stkCallback", {})
    checkout_id = data.get("CheckoutRequestID")
    payment = Payment.query.filter_by(transaction_id=checkout_id).first()
    
    if payment:
        payment.result_code = data.get("ResultCode") 
        payment.result_desc = data.get("ResultDesc")
        db.session.commit()
        logging.info(f"Payment {checkout_id} updated with code {payment.result_code}")
    
    return jsonify({"message": "Processed"}), 200

# --- ROUTES ---

api.add_resource(Register, '/auth/register')
api.add_resource(Login, '/auth/login')
api.add_resource(Logout, '/auth/logout')
api.add_resource(RecipientProfileResource, '/auth/recipient-profile')
api.add_resource(DonorHistoryResource, '/payment/history')
api.add_resource(STKPushResource, '/payment/stkpush')
api.add_resource(AdminRecipientsResource, '/admin/recipients')
api.add_resource(AdminApproveResource, '/admin/approve/<int:recipient_id>')
api.add_resource(TaskListResource, '/api/tasks')
api.add_resource(TaskCompleteResource, '/api/tasks/<int:task_id>/complete')
api.add_resource(DonorStatsResource, '/api/donor/stats')
api.add_resource(AdminSummaryResource, '/api/admin/summary')
api.add_resource(SystemLogsResource, '/api/admin/logs')
api.add_resource(AidDistributionResource, '/api/admin/aid-distribution')

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        seed_admin()
    # On Render, the port is provided by an environment variable
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)