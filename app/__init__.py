from __future__ import annotations

import os
from datetime import datetime
from decimal import Decimal

from flask import Flask
from flask_login import LoginManager
from flask_wtf.csrf import CSRFProtect

from .config import Config
from .models import User, UserProfile, db

login_manager = LoginManager()
csrf = CSRFProtect()


def _ensure_default_users(app: Flask) -> None:
	"""Create documented demo accounts when they do not yet exist."""
	default_users = [
		{
			"first_name": "System",
			"last_name": "Admin",
			"email": "admin@smartqualihome.com",
			"username": "admin",
			"role": "admin",
			"password": "Admin@2026!",
		},
		{
			"first_name": "Demo",
			"last_name": "Agent",
			"email": "agent@smartqualihome.com",
			"username": "agent",
			"role": "agent",
			"password": "Agent@2026!",
		},
		{
			"first_name": "Demo",
			"last_name": "Client",
			"email": "client@smartqualihome.com",
			"username": "client",
			"role": "client",
			"password": "Client@2026!",
		},
	]

	created = 0
	for item in default_users:
		existing = User.query.filter_by(email=item["email"]).first()
		if existing:
			continue

		user = User(
			first_name=item["first_name"],
			last_name=item["last_name"],
			email=item["email"],
			username=item["username"],
			role=item["role"],
			is_active=True,
		)
		user.set_password(item["password"])
		db.session.add(user)
		created += 1

	# Ensure the demo agent's contact profile is populated ("Ann Regar") so the
	# client-side Conditional Requirements modal shows live contact buttons.
	# Existing values are preserved so admin edits are never overwritten.
	dirty = False
	agent_user = User.query.filter_by(email="agent@smartqualihome.com").first()
	if agent_user:
		if agent_user.first_name == "Demo" and agent_user.last_name == "Agent":
			agent_user.first_name = "Ann"
			agent_user.last_name = "Regar"
			dirty = True
		agent_profile = agent_user.profile
		if not agent_profile:
			agent_profile = UserProfile(user_id=agent_user.id)
			db.session.add(agent_profile)
			dirty = True
		agent_defaults = {
			"license_no": "REBL-2026-0001",
			"contact_no": "09171234567",
			"social_instagram": "annregarrealty",
			"social_viber": "09171234567",
			"social_whatsapp": "09171234567",
			"bio": "Licensed real estate broker at Annregar Realty & Development Corp.",
		}
		for field, value in agent_defaults.items():
			if not getattr(agent_profile, field, None):
				setattr(agent_profile, field, value)
				dirty = True

	if not created and not dirty:
		return

	try:
		db.session.commit()
		if created:
			app.logger.info("Seeded %s default user account(s).", created)
		if dirty:
			app.logger.info("Ensured demo agent (Ann Regar) contact profile.")
	except Exception:
		db.session.rollback()
		app.logger.exception("Failed seeding default user accounts.")


def create_app(config_class=Config):
	app = Flask(__name__, instance_relative_config=True)
	app.config.from_object(config_class)

	# Ensure writable instance paths exist.
	os.makedirs(app.instance_path, exist_ok=True)
	os.makedirs(app.config.get("UPLOAD_FOLDER", os.path.join(app.instance_path, "uploads")), exist_ok=True)

	# Configure Cloudinary when credentials are provided (production/Render).
	# Without them the app falls back to the local uploads folder.
	if (
		app.config.get("CLOUDINARY_CLOUD_NAME")
		and app.config.get("CLOUDINARY_API_KEY")
		and app.config.get("CLOUDINARY_API_SECRET")
	):
		import cloudinary
		cloudinary.config(
			cloud_name=app.config["CLOUDINARY_CLOUD_NAME"],
			api_key=app.config["CLOUDINARY_API_KEY"],
			api_secret=app.config["CLOUDINARY_API_SECRET"],
			secure=True,
		)
		app.logger.info("Cloudinary configured (cloud=%s).", app.config["CLOUDINARY_CLOUD_NAME"])

	db.init_app(app)
	login_manager.init_app(app)
	csrf.init_app(app)

	login_manager.login_view = "auth.login"
	login_manager.login_message_category = "warning"

	@login_manager.user_loader
	def load_user(user_id: str):
		try:
			return db.session.get(User, int(user_id))
		except Exception:
			return None

	@app.template_filter("img_src")
	def img_src_filter(ref):
		"""Resolve a stored image reference to a browser-ready src.

		Absolute Cloudinary URLs are emitted directly; legacy local
		filenames are served through /uploads/<filename>.
		"""
		ref = (ref or "").strip()
		if ref.startswith(("http://", "https://")):
			return ref
		if not ref:
			return ""
		return "/uploads/" + ref

	@app.template_filter("ph_datetime")
	def ph_datetime_filter(value, fmt="%b %d, %Y %I:%M %p"):
		if not value:
			return ""
		if isinstance(value, datetime):
			return value.strftime(fmt)
		return str(value)

	@app.template_filter("ph_time")
	def ph_time_filter(value):
		if not value:
			return ""
		try:
			return value.strftime("%I:%M %p")
		except Exception:
			return str(value)

	@app.template_filter("commas_input")
	def commas_input_filter(val):
		"""Format numeric values with commas and no trailing .00 for form inputs."""
		if val is None or val == "":
			return ""
		try:
			d = Decimal(str(val))
			if d == 0:
				return ""
			if d == d.to_integral():
				return f"{int(d):,}"
			return f"{d:,.2f}"
		except Exception:
			return str(val)

	# Register blueprints that are available in the current workspace state.
	try:
		from .auth.routes import auth_bp
		app.register_blueprint(auth_bp)
	except Exception:
		app.logger.exception("Failed to register auth_bp")

	try:
		from .client.routes import client_bp
		app.register_blueprint(client_bp)
	except Exception:
		app.logger.exception("Failed to register client_bp")

	try:
		from .main.routes import main_bp
		app.register_blueprint(main_bp)
	except Exception:
		app.logger.exception("Failed to register main_bp")

	# Create tables if they do not exist yet, then seed demo accounts.
	try:
		with app.app_context():
			db.create_all()
			_ensure_default_users(app)
			app.logger.info("Database bootstrap complete: tables and demo accounts verified.")
	except Exception:
		app.logger.exception("Default account bootstrap failed.")

	# Warm up the in-memory C5.0 model on startup when enough data exists.
	try:
		from .models import HistoricalBuyer
		from .ml import c50_engine
		with app.app_context():
			buyers = HistoricalBuyer.query.all()
			if len(buyers) >= 10:
				c50_engine.train(buyers)
				meta = c50_engine.get_meta()
				app.logger.info(
					"Startup C5.0 train complete: trained=%s samples=%s accuracy=%s",
					meta.get("trained", False),
					meta.get("n_samples", 0),
					meta.get("train_accuracy", "N/A"),
				)
			else:
				app.logger.warning(
					"Startup C5.0 skipped: only %s training records (need at least 10).",
					len(buyers),
				)
	except Exception:
		app.logger.exception("Startup C5.0 initialization failed; continuing with rule fallback.")

	# Fallback endpoint so app always boots even if a blueprint is unavailable.
	if "main.index" not in app.view_functions:
		@app.route("/")
		def _fallback_index():
			return "SMARTQUALIHOME is running."

	return app