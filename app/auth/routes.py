from datetime import datetime, timedelta, timezone
from decimal import Decimal
import os
import secrets
import smtplib
import ssl
from email.message import EmailMessage
from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app, session, jsonify
from flask_login import login_user, logout_user, login_required, current_user
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from werkzeug.utils import secure_filename
from ..models import db, User, UserProfile, QualificationResult, ActivityLog, Property, SystemConfig, log_activity
from .forms import LoginForm, RegistrationForm, ForgotPasswordForm, ResetPasswordForm
from ..ml import c50_engine

auth_bp = Blueprint("auth", __name__)

# -- Helpers -------------------------------------------------------------------

def _dashboard_url(role: str) -> str:
    routes = {
        "admin":  "main.admin_dashboard",
        "agent":  "main.agent_dashboard",
        "client": "main.client_dashboard",
    }
    return url_for(routes.get(role, "main.client_dashboard"))


def _reset_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"])


def _make_reset_token(user: User) -> str:
    payload = {"uid": user.id, "ph": user.password_hash}
    return _reset_serializer().dumps(payload, salt="password-reset")


def _verify_reset_token(token: str, max_age: int = 3600) -> User | None:
    try:
        data = _reset_serializer().loads(token, salt="password-reset", max_age=max_age)
    except (BadSignature, SignatureExpired):
        return None
    uid = data.get("uid")
    ph = data.get("ph")
    if not uid or not ph:
        return None
    user = db.session.get(User, int(uid))
    if not user:
        return None
    if user.password_hash != ph:
        return None
    return user


def _get_security_int(key: str, fallback: int) -> int:
    try:
        rec = SystemConfig.query.filter_by(key=key).first()
        if rec and str(rec.value).strip():
            value = int(float(rec.value))
            if key == "min_password_length":
                value = max(value, 8)
            config_map = {
                "max_login_attempts": "MAX_LOGIN_ATTEMPTS",
                "max_forgot_password_attempts": "MAX_FORGOT_PASSWORD_ATTEMPTS",
                "session_timeout_mins": "SESSION_TIMEOUT_MINS",
                "min_password_length": "MIN_PASSWORD_LENGTH",
            }
            mapped = config_map.get(key)
            if mapped:
                current_app.config[mapped] = value
            return value
    except Exception:
        pass
    value = int(current_app.config.get({
        "max_login_attempts": "MAX_LOGIN_ATTEMPTS",
        "max_forgot_password_attempts": "MAX_FORGOT_PASSWORD_ATTEMPTS",
        "session_timeout_mins": "SESSION_TIMEOUT_MINS",
        "min_password_length": "MIN_PASSWORD_LENGTH",
    }.get(key, ""), fallback))
    if key == "min_password_length":
        value = max(value, 8)
        current_app.config["MIN_PASSWORD_LENGTH"] = value
    return value


def _is_email_verification_enabled() -> bool:
    """Returns True if admin requires OTP email verification on registration."""
    env_setting = os.environ.get("EMAIL_VERIFICATION_ENABLED")
    if env_setting is not None:
        return env_setting.strip().lower() in {"true", "1", "yes"}
    try:
        cfg = SystemConfig.query.filter_by(key="email_verification_enabled").first()
        if cfg and cfg.value is not None:
            return cfg.value.strip().lower() in {"true", "1", "yes"}
    except Exception:
        pass
    return True


def _send_via_brevo_api(api_key: str, to_email: str, subject: str, html_body: str, from_addr: str, text_body: str = "") -> tuple[bool, str | None]:
    import urllib.request
    import urllib.error
    import json
    try:
        sender_email = (
            os.environ.get("BREVO_SENDER_EMAIL")
            or os.environ.get("BREVO_FROM_EMAIL")
            or (from_addr.split("<")[-1].replace(">", "").strip() if "<" in from_addr else from_addr)
        ).strip()
        sender_name = (
            os.environ.get("BREVO_SENDER_NAME")
            or (from_addr.split("<")[0].strip() if "<" in from_addr else "")
            or "QUALIHOME"
        ).strip()
        payload = {
            "sender": {"name": sender_name, "email": sender_email},
            "to": [{"email": to_email}],
            "subject": subject,
            "htmlContent": html_body,
        }
        if text_body:
            payload["textContent"] = text_body

        req = urllib.request.Request(
            "https://api.brevo.com/v3/smtp/email",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "api-key": api_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=12) as resp:
            if resp.status in (200, 201):
                return True, None
            return False, f"Brevo HTTP {resp.status}"
    except urllib.error.HTTPError as exc:
        try:
            err_body = exc.read().decode("utf-8", errors="ignore")
        except Exception:
            err_body = str(exc)
        current_app.logger.warning("Brevo API HTTPError %s: %s", exc.code, err_body)
        return False, f"Brevo HTTP {exc.code}: {err_body}"
    except Exception as exc:
        current_app.logger.warning("Brevo API send failed: %s", exc)
        return False, str(exc)


def _send_via_resend_api(api_key: str, to_email: str, subject: str, html_body: str, from_addr: str) -> tuple[bool, str | None]:
    import urllib.request
    import json
    try:
        sender_email = from_addr.split("<")[-1].replace(">", "").strip() if "<" in from_addr else from_addr
        sender_name = from_addr.split("<")[0].strip() if "<" in from_addr else "QUALIHOME"
        payload = {
            "from": f"{sender_name} <{sender_email}>",
            "to": [to_email],
            "subject": subject,
            "html": html_body,
        }
        req = urllib.request.Request(
            "https://api.resend.com/emails",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status in (200, 201):
                return True, None
            return False, f"Resend HTTP {resp.status}"
    except Exception as exc:
        current_app.logger.warning("Resend API send failed: %s", exc)
        return False, str(exc)


def _send_reset_email(to_email: str, reset_url: str) -> tuple[bool, str | None]:
    cfg = current_app.config
    subject = "QUALIHOME Password Reset"
    text_body = (
        "We received a request to reset your QUALIHOME password.\n\n"
        f"Open this link to reset your password:\n{reset_url}\n\n"
        "This link expires in 1 hour. If you did not request this, you can ignore this email.\n\n"
        "— Annregar Realty & QUALIHOME Team"
    )
    html_body = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <style>
    body {{ font-family: 'Segoe UI', Arial, sans-serif; background-color: #f7f9fc; margin: 0; padding: 20px; }}
    .container {{ max-width: 520px; margin: 0 auto; background: #ffffff; border-radius: 12px; overflow: hidden; border: 1px solid #e2e8f0; box-shadow: 0 4px 14px rgba(0,0,0,0.06); }}
    .header {{ background: #7d1a1a; padding: 24px 20px; text-align: center; }}
    .header h1 {{ color: #ffffff; margin: 0; font-size: 24px; letter-spacing: 1px; font-weight: 800; }}
    .header p {{ color: #fbd38d; margin: 5px 0 0; font-size: 13px; font-weight: 600; text-transform: uppercase; letter-spacing: 1.5px; }}
    .content {{ padding: 30px 24px; color: #2d3748; line-height: 1.6; }}
    .greeting {{ font-size: 18px; font-weight: 700; color: #1a202c; margin-bottom: 12px; }}
    .btn-wrap {{ text-align: center; margin: 26px 0; }}
    .btn-reset {{ display: inline-block; background-color: #7d1a1a; color: #ffffff !important; padding: 12px 28px; font-size: 15px; font-weight: 700; text-decoration: none; border-radius: 8px; }}
    .footer {{ background: #edf2f7; padding: 16px 20px; text-align: center; font-size: 12px; color: #718096; border-top: 1px solid #e2e8f0; }}
  </style>
</head>
<body>
  <div class="container">
    <div class="header">
      <h1>QUALIHOME</h1>
      <p>Annregar Realty &bull; Account Security</p>
    </div>
    <div class="content">
      <div class="greeting">Reset Your Password</div>
      <p>We received a request to reset your QUALIHOME account password. Click the button below to choose a new password:</p>
      <div class="btn-wrap">
        <a href="{reset_url}" class="btn-reset">Reset My Password</a>
      </div>
      <p style="font-size: 13px; color: #718096;">This password reset link is valid for <strong>1 hour</strong>. If you did not make this request, you can safely ignore this email.</p>
    </div>
    <div class="footer">
      &copy; 2026 QUALIHOME &bull; Annregar Realty. All rights reserved.
    </div>
  </div>
</body>
</html>"""

    from_addr = (cfg.get("MAIL_FROM") or cfg.get("MAIL_USERNAME") or "no-reply@qualihome.local").strip()

    # 1. Try Brevo HTTP API (Port 443 — bypasses Railway SMTP port blocks)
    brevo_key = (os.environ.get("BREVO_API_KEY") or cfg.get("BREVO_API_KEY") or "").strip()
    if brevo_key:
        ok, err = _send_via_brevo_api(brevo_key, to_email, subject, html_body, from_addr, text_body)
        if ok:
            return True, None
        current_app.logger.warning("Brevo API reset email failed: %s; falling back to SMTP", err)

    # 2. Try Resend HTTP API
    resend_key = (os.environ.get("RESEND_API_KEY") or cfg.get("RESEND_API_KEY") or "").strip()
    if resend_key:
        ok, err = _send_via_resend_api(resend_key, to_email, subject, html_body, from_addr)
        if ok:
            return True, None
        current_app.logger.warning("Resend API reset email failed: %s; falling back to SMTP", err)

    # 3. SMTP fallback
    server = (cfg.get("MAIL_SERVER") or "").strip()
    username = (cfg.get("MAIL_USERNAME") or "").strip()
    password = cfg.get("MAIL_PASSWORD") or ""
    if not server or not username or not password:
        return False, "Missing SMTP configuration (server/username/password)."

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to_email
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")

    port = int(cfg.get("MAIL_PORT", 587))
    use_ssl = bool(cfg.get("MAIL_USE_SSL", False))
    use_tls = bool(cfg.get("MAIL_USE_TLS", True))
    try:
        if use_ssl:
            with smtplib.SMTP_SSL(server, port, context=ssl.create_default_context(), timeout=10) as smtp:
                smtp.login(username, password)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(server, port, timeout=10) as smtp:
                smtp.ehlo()
                if use_tls:
                    smtp.starttls(context=ssl.create_default_context())
                    smtp.ehlo()
                smtp.login(username, password)
                smtp.send_message(msg)
        return True, None
    except Exception as exc:
        current_app.logger.warning("Failed to send password reset email via SMTP: %s", exc)
        return False, str(exc)


def _send_otp_email(to_email: str, otp_code: str, first_name: str = "") -> tuple[bool, str | None]:
    """Sends a 6-digit verification code to the client via configured Gmail SMTP, HTTP API, or demo fallback."""
    cfg = current_app.config
    subject = f"QUALIHOME Verification Code: {otp_code}"
    display_name = first_name.strip() if first_name else "Future Homeowner"

    text_body = (
        f"Hello {display_name},\n\n"
        f"Thank you for registering with QUALIHOME.\n\n"
        f"Your 6-digit email verification code is: {otp_code}\n\n"
        f"This code is valid for 15 minutes. Please do not share this code with anyone.\n\n"
        f"If you did not create an account on QUALIHOME, you can ignore this email.\n\n"
        f"— Annregar Realty & QUALIHOME Team"
    )

    html_body = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <style>
    body {{ font-family: 'Segoe UI', Arial, sans-serif; background-color: #f7f9fc; margin: 0; padding: 20px; }}
    .container {{ max-width: 520px; margin: 0 auto; background: #ffffff; border-radius: 12px; overflow: hidden; border: 1px solid #e2e8f0; box-shadow: 0 4px 14px rgba(0,0,0,0.06); }}
    .header {{ background: #7d1a1a; padding: 24px 20px; text-align: center; }}
    .header h1 {{ color: #ffffff; margin: 0; font-size: 24px; letter-spacing: 1px; font-weight: 800; }}
    .header p {{ color: #fbd38d; margin: 5px 0 0; font-size: 13px; font-weight: 600; text-transform: uppercase; letter-spacing: 1.5px; }}
    .content {{ padding: 30px 24px; color: #2d3748; line-height: 1.6; }}
    .greeting {{ font-size: 18px; font-weight: 700; color: #1a202c; margin-bottom: 12px; }}
    .otp-box {{ background: #fff5f5; border: 2px dashed #7d1a1a; border-radius: 10px; padding: 20px; text-align: center; margin: 22px 0; }}
    .otp-code {{ font-size: 36px; font-weight: 900; letter-spacing: 12px; color: #7d1a1a; font-family: monospace; }}
    .otp-sub {{ font-size: 12px; color: #718096; margin-top: 8px; font-weight: 600; }}
    .footer {{ background: #edf2f7; padding: 16px 20px; text-align: center; font-size: 12px; color: #718096; border-top: 1px solid #e2e8f0; }}
  </style>
</head>
<body>
  <div class="container">
    <div class="header">
      <h1>QUALIHOME</h1>
      <p>Annregar Realty &bull; Smart Qualification</p>
    </div>
    <div class="content">
      <div class="greeting">Hello, {display_name}!</div>
      <p>Thank you for signing up with QUALIHOME. To verify your email address and activate your pre-qualification dashboard, please enter the verification code below:</p>
      <div class="otp-box">
        <div class="otp-code">{otp_code}</div>
        <div class="otp-sub">Valid for 15 minutes &bull; Single-use only</div>
      </div>
      <p style="font-size: 13px; color: #4a5568;">If you did not create an account on QUALIHOME, please disregard this email.</p>
    </div>
    <div class="footer">
      &copy; 2026 QUALIHOME &bull; Annregar Realty. All rights reserved.
    </div>
  </div>
</body>
</html>"""

    from_addr = (cfg.get("MAIL_FROM") or cfg.get("MAIL_USERNAME") or "no-reply@qualihome.local").strip()

    # 1. Try Brevo HTTP API (Port 443 — bypasses Railway SMTP port blocks)
    brevo_key = (os.environ.get("BREVO_API_KEY") or cfg.get("BREVO_API_KEY") or "").strip()
    if brevo_key:
        ok, err = _send_via_brevo_api(brevo_key, to_email, subject, html_body, from_addr, text_body)
        if ok:
            return True, None
        current_app.logger.warning("Brevo API failed: %s; falling back to SMTP", err)

    # 2. Try Resend HTTP API (Port 443 — bypasses Railway SMTP port blocks)
    resend_key = (os.environ.get("RESEND_API_KEY") or cfg.get("RESEND_API_KEY") or "").strip()
    if resend_key:
        ok, err = _send_via_resend_api(resend_key, to_email, subject, html_body, from_addr)
        if ok:
            return True, None
        current_app.logger.warning("Resend API failed: %s; falling back to SMTP", err)

    # 3. SMTP fallback
    server = (cfg.get("MAIL_SERVER") or "").strip()
    username = (cfg.get("MAIL_USERNAME") or "").strip()
    password = cfg.get("MAIL_PASSWORD") or ""
    if not server or not username or not password:
        return False, "Missing SMTP configuration (server/username/password)."

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to_email
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")

    port = int(cfg.get("MAIL_PORT", 587))
    use_ssl = bool(cfg.get("MAIL_USE_SSL", False))
    use_tls = bool(cfg.get("MAIL_USE_TLS", True))
    try:
        if use_ssl:
            with smtplib.SMTP_SSL(server, port, context=ssl.create_default_context(), timeout=10) as smtp:
                smtp.login(username, password)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(server, port, timeout=10) as smtp:
                smtp.ehlo()
                if use_tls:
                    smtp.starttls(context=ssl.create_default_context())
                    smtp.ehlo()
                smtp.login(username, password)
                smtp.send_message(msg)
        return True, None
    except Exception as exc:
        current_app.logger.warning("Failed to send OTP verification email via SMTP: %s", exc)
        return False, str(exc)


def _ensure_active_otp(user: User) -> tuple[bool, str | None]:
    """Ensures user has an unexpired OTP; generates and emails a new one if missing or expired."""
    now = datetime.now(timezone.utc)
    expiry = user.otp_expiry
    if expiry and expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    if not user.otp_code or not expiry or now >= expiry:
        otp = f"{secrets.randbelow(900000) + 100000:06d}"
        user.otp_code = otp
        user.otp_expiry = now + timedelta(minutes=15)
        db.session.commit()
        return _send_otp_email(user.email, otp, user.first_name)
    return True, None


def _compute_result(gross_income: float, monthly_debt: float,
                    employment_type: str = "employed",
                    tenure_months: int = 0, age: int = 30,
                    dependents: int = 0):
    """Runs the same C5.0 ML engine used by the dashboard re-assessment."""
    status, dti, max_loanable, similarity_score, factors_json = c50_engine.predict(
        gross_income    = gross_income,
        monthly_loans   = monthly_debt,
        tenure_months   = tenure_months,
        employment_type = employment_type,
        age             = age,
        dependents      = dependents,
    )
    return status, dti, max_loanable, similarity_score, factors_json


def _get_live_meter_criteria() -> dict:
    """Fetch qualification criteria needed by the registration live meter.

    Delegates to the canonical implementation in main.routes (which reads
    SystemConfig through a request-scoped cache).
    """
    from ..main.routes import _get_live_meter_criteria as _main_get_live_meter_criteria
    return _main_get_live_meter_criteria()


ALLOWED_DOC_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".pdf"}
MAX_DOC_BYTES = 10 * 1024 * 1024


def _read_registration_doc(file_obj) -> dict | None:
    if not file_obj or not getattr(file_obj, "filename", ""):
        return None

    original = secure_filename(file_obj.filename or "")
    ext = os.path.splitext(original)[1].lower()
    if ext not in ALLOWED_DOC_EXTS:
        raise ValueError("Invalid file type. Allowed: JPG, JPEG, PNG, WEBP, PDF.")
    payload = file_obj.read()
    if not payload:
        return None
    if len(payload) > MAX_DOC_BYTES:
        raise ValueError("Document is too large. Maximum file size is 10 MB.")
    return {
        "filename": original,
        "mimetype": (file_obj.mimetype or "application/octet-stream"),
        "data": payload,
    }


# -- Login --------------------------------------------------------------------

@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(_dashboard_url(current_user.role))

    form = LoginForm()
    if request.method == "POST":
        if form.validate_on_submit():
            email_key = f"_login_fails_{form.email.data.lower().strip()}"
            attempts  = session.get(email_key, 0)
            max_attempts = _get_security_int("max_login_attempts", 5)
            if attempts >= max_attempts:
                flash("Too many failed login attempts. Please wait a few minutes before trying again.", "danger")
                return redirect(url_for("auth.login"))

            user = User.query.filter_by(email=form.email.data.lower()).first()
            if user and user.check_password(form.password.data):
                if not user.is_active:
                    flash("Your account has been deactivated. Please contact support.", "danger")
                    return redirect(url_for("auth.login"))
                if _is_email_verification_enabled() and not getattr(user, "is_verified", True):
                    _ensure_active_otp(user)
                    session["pending_verify_user_id"] = user.id
                    session["pending_verify_email"] = user.email
                    flash("Please verify your email address to access your account. A 6-digit verification code has been sent to your Gmail.", "warning")
                    return redirect(url_for("auth.verify_otp"))
                session.pop(email_key, None)  # Clear failed-attempt counter on success
                login_user(user, remember=form.remember.data)
                try:
                    log_activity("login", f"{user.full_name} signed in.", actor=user)
                    db.session.commit()
                except Exception:
                    db.session.rollback()
                next_page = request.args.get("next")
                flash(f"Welcome back, {user.first_name}!", "success")
                return redirect(next_page or _dashboard_url(user.role))
            session[email_key] = attempts + 1
            flash("Invalid email or password. Please try again.", "danger")
        else:
            flash("Please enter your email and password.", "warning")
        return redirect(url_for("auth.login"))

    fp_form = ForgotPasswordForm()
    return render_template("auth/login.html", form=form, fp_form=fp_form, title="Sign In")


# -- Register (Get Qualified) --------------------------------------------------

@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(_dashboard_url(current_user.role))

    if request.method == "POST":
        # Anti-bot honeypot check (catches automated scripts immediately)
        if request.form.get("user_website_trap"):
            current_app.logger.warning("Bot registration blocked via honeypot trap.")
            flash("Account created! Please sign in to access your dashboard.", "success")
            return redirect(url_for("auth.login"))

        # Strip thousands separators BEFORE WTForms binds request.form
        # (DecimalField.process_formdata runs before field `filters`).
        try:
            from werkzeug.datastructures import ImmutableMultiDict
            _targets = {"gross_monthly_income", "monthly_debt_loans", "budget_min", "budget_max"}
            if request.form:
                _cleaned, _touched = [], False
                for _key in request.form.keys():
                    for _value in request.form.getlist(_key):
                        if _key in _targets and isinstance(_value, str) and "," in _value:
                            _value = _value.replace(",", "")
                            _touched = True
                        _cleaned.append((_key, _value))
                if _touched:
                    request.form = ImmutableMultiDict(_cleaned)
        except Exception:
            pass

    form = RegistrationForm()
    available_models = (db.session.query(Property.name)
                        .filter(Property.status == "available")
                        .filter(db.or_(Property.approval_status == "approved", Property.approval_status.is_(None)))
                        .order_by(Property.name.asc())
                        .all())
    model_names = sorted({(name or "").strip() for (name,) in available_models if (name or "").strip()}, key=lambda x: x.lower())
    form.preferred_type.choices = [("", "Any model (optional)")] + [(name, name) for name in model_names]
    live_meter_cfg = _get_live_meter_criteria()
    if form.validate_on_submit():

        # 1. Create User
        verification_enabled = _is_email_verification_enabled()
        user = User(
            first_name     = form.first_name.data.strip(),
            middle_name    = (form.middle_name.data or "").strip() or None,
            last_name      = form.last_name.data.strip(),
            username       = form.username.data.strip(),
            email          = form.email.data.lower().strip(),
            contact_number = form.contact_number.data.strip(),
            role           = "client",
            is_verified    = not verification_enabled,
        )
        if verification_enabled:
            otp = f"{secrets.randbelow(900000) + 100000:06d}"
            user.otp_code = otp
            user.otp_expiry = datetime.now(timezone.utc) + timedelta(minutes=15)

        user.set_password(form.password.data)
        db.session.add(user)
        db.session.flush()   # generates user.id before commit

        # 3. Create normalized profile
        gross  = float(form.gross_monthly_income.data or 0)
        debt   = float(form.monthly_debt_loans.data   or 0)

        profile = UserProfile(
            user_id               = user.id,
            civil_status          = form.civil_status.data,
            citizenship           = form.citizenship.data,
            gender                = form.gender.data,
            dependents            = form.dependents.data,
            birth_date            = form.birth_date.data,
            gross_income          = form.gross_monthly_income.data,
            monthly_loans         = form.monthly_debt_loans.data or Decimal("0"),
            employment_type       = form.employment_status.data,
            tenure_months         = form.tenure_months.data or 0,
            sss_gsis_umid         = (form.sss_gsis_umid.data or "").strip() or None,
            tin_no                = (form.tin_no.data or "").strip() or None,
            age                   = form.age.data,
            preferred_type        = (((form.preferred_type.data or "").strip()[:40]) or None),
            budget_min            = form.budget_min.data or Decimal("0"),
            budget_max            = form.budget_max.data or Decimal("0"),
            has_valid_id          = (form.has_valid_id.data == "yes"),
            has_income_proof      = (form.has_income_proof.data == "yes"),
        )
        db.session.add(profile)

        # 5. Compute qualification result using the full ML engine
        status, dti, max_loanable, score, factors_json = _compute_result(
            gross_income    = gross,
            monthly_debt    = debt,
            employment_type = form.employment_status.data or "employed",
            tenure_months   = int(form.tenure_months.data or 0),
            age             = int(form.age.data or 30),
            dependents      = int(form.dependents.data or 0),
        )
        result = QualificationResult(
            user_id          = user.id,
            status           = status,
            dti_ratio        = dti,
            max_loanable     = max_loanable,
            similarity_score = score,
            assessment_mode  = "new",
            factors_json     = factors_json,
        )
        db.session.add(result)
        proof_summary = []
        if getattr(form, "has_valid_id", None) and form.has_valid_id.data:
            proof_summary.append(f"valid ID: {form.has_valid_id.data}")
        if getattr(form, "has_income_proof", None) and form.has_income_proof.data:
            proof_summary.append(f"income proof: {form.has_income_proof.data}")
        log_activity(
            "register_documents",
            f"Registration honesty declaration captured ({', '.join(proof_summary) or 'declared'}).",
            actor=user,
        )
        log_activity("assessment",
                 f"Assessment submitted — {status} (DTI: {dti:.1f}%).",
                 actor=user)
        log_activity("register",
                     f"New client {user.full_name} registered. Assessment: {status} (DTI: {dti:.1f}%).",
                     actor=user)
        db.session.commit()

        if verification_enabled:
            sent, mail_err = _send_otp_email(user.email, user.otp_code, user.first_name)
            session["pending_verify_user_id"] = user.id
            session["pending_verify_email"] = user.email
            session["otp_sent_at"] = datetime.now(timezone.utc).timestamp()
            session["otp_delivery_failed"] = not sent
            if sent:
                flash(f"A 6-digit verification code has been sent to {user.email}. Please check your inbox.", "info")
            else:
                current_app.logger.warning("Failed to send OTP email to %s: %s", user.email, mail_err)
                flash("Account created! Verification code provided below for thesis evaluation (cloud SMTP restricted).", "info")
            return redirect(url_for("auth.verify_otp"))
        else:
            # 6. Show result page (user must sign in manually)
            flash(f"Account created! Sign in to access your dashboard, {user.first_name}.", "success")
            return render_template("auth/register.html",
                                   result=result,
                                   user_name=user.first_name,
                                   live_meter_cfg=live_meter_cfg,
                                   title="Registration Complete")

    return render_template("auth/register.html", form=form, live_meter_cfg=live_meter_cfg, title="Get Qualified")


@auth_bp.route("/register/check-field")
def register_check_field():
    field = (request.args.get("field") or "").strip().lower()
    value = (request.args.get("value") or "").strip()

    if field not in {"username", "email"}:
        return jsonify(ok=False, error="Invalid field"), 400

    if not value:
        return jsonify(ok=True, available=True, exists=False)

    if field == "username":
        exists = User.query.filter_by(username=value).first() is not None
    else:
        exists = User.query.filter_by(email=value.lower()).first() is not None

    return jsonify(ok=True, available=(not exists), exists=exists)


# -- Email OTP Verification ----------------------------------------------------

@auth_bp.route("/verify-otp", methods=["GET", "POST"])
def verify_otp():
    if current_user.is_authenticated and getattr(current_user, "is_verified", True):
        return redirect(_dashboard_url(current_user.role))

    user_id = session.get("pending_verify_user_id")
    if not user_id:
        flash("No pending verification session. Please sign in or register.", "info")
        return redirect(url_for("auth.login"))

    user = db.session.get(User, int(user_id))
    if not user:
        session.pop("pending_verify_user_id", None)
        flash("User account not found. Please register again.", "danger")
        return redirect(url_for("auth.login"))

    if getattr(user, "is_verified", False):
        session.pop("pending_verify_user_id", None)
        session.pop("pending_verify_email", None)
        flash("Your account is already verified! Please sign in.", "success")
        return redirect(url_for("auth.login"))

    if request.method == "POST":
        entered = (request.form.get("otp") or "").strip()
        now = datetime.now(timezone.utc)
        expiry = user.otp_expiry
        if expiry and expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)

        if not user.otp_code:
            flash("No verification code found. Please click 'Resend Code'.", "warning")
            return render_template("auth/verify_otp.html", email=user.email, demo_otp=None)

        if expiry and now > expiry:
            flash("Verification code has expired. Please click 'Resend Code' to receive a new one.", "danger")
            return render_template("auth/verify_otp.html", email=user.email, demo_otp=None)

        if entered != str(user.otp_code).strip():
            flash("Invalid 6-digit verification code. Please try again.", "danger")
            return render_template("auth/verify_otp.html", email=user.email, demo_otp=user.otp_code)

        # Verification successful!
        user.is_verified = True
        user.otp_code = None
        user.otp_expiry = None
        try:
            log_activity("email_verified", f"{user.email} verified account email.", actor=user)
            db.session.commit()
        except Exception:
            db.session.rollback()

        session.pop("pending_verify_user_id", None)
        session.pop("pending_verify_email", None)
        session.pop("otp_sent_at", None)

        login_user(user)
        flash(f"Email verified successfully! Welcome to QUALIHOME, {user.first_name}!", "success")
        return redirect(_dashboard_url(user.role))

    show_demo = user.otp_code if session.get("otp_delivery_failed", False) else None
    return render_template("auth/verify_otp.html", email=user.email, demo_otp=show_demo)


@auth_bp.route("/resend-otp", methods=["POST"])
def resend_otp():
    user_id = session.get("pending_verify_user_id")
    if not user_id:
        return jsonify({"ok": False, "error": "No pending verification session. Please sign in."}), 400

    user = db.session.get(User, int(user_id))
    if not user:
        return jsonify({"ok": False, "error": "User account not found."}), 404

    if getattr(user, "is_verified", False):
        return jsonify({"ok": False, "error": "Account is already verified. Please sign in."}), 400

    now = datetime.now(timezone.utc)
    now_ts = now.timestamp()
    last_sent = float(session.get("otp_sent_at", 0))
    if now_ts - last_sent < 60:
        remaining = int(60 - (now_ts - last_sent))
        return jsonify({"ok": False, "error": f"Please wait {remaining} seconds before requesting a new code."}), 429

    otp = f"{secrets.randbelow(900000) + 100000:06d}"
    user.otp_code = otp
    user.otp_expiry = now + timedelta(minutes=15)
    db.session.commit()

    sent, mail_err = _send_otp_email(user.email, otp, user.first_name)
    session["otp_sent_at"] = now_ts
    session["otp_delivery_failed"] = not sent

    if sent:
        return jsonify({
            "ok": True,
            "delivered": True,
            "message": f"A new 6-digit verification code was sent to {user.email}.",
        })
    else:
        return jsonify({
            "ok": True,
            "delivered": False,
            "message": f"Code refreshed: {otp} (Cloud SMTP restricted by Railway)",
            "demo_otp": otp,
        })


@auth_bp.route("/cancel-verification")
def cancel_verification():
    session.pop("pending_verify_user_id", None)
    session.pop("pending_verify_email", None)
    session.pop("otp_sent_at", None)
    flash("Verification cancelled. You can sign in or register with another email.", "info")
    return redirect(url_for("auth.login"))


# -- Logout -------------------------------------------------------------------

@auth_bp.route("/logout")
@login_required
def logout():
    try:
        log_activity("logout", f"{current_user.full_name} signed out.")
        db.session.commit()
    except Exception:
        db.session.rollback()
    logout_user()
    flash("You have been signed out.", "info")
    return redirect(url_for("main.index"))


# -- Forgot Password (placeholder) --------------------------------------------

@auth_bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    form = ForgotPasswordForm()
    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    if request.method == "POST":
        if form.validate_on_submit():
            email = form.email.data.lower().strip()
            user = User.query.filter_by(email=email).first()
            reset_url = None
            sent = False
            mail_error = None
            if user:
                max_forgot = _get_security_int("max_forgot_password_attempts", 5)
                now = datetime.now(timezone.utc)
                window_start = user.forgot_password_window_started_at
                if window_start and window_start.tzinfo is None:
                    window_start = window_start.replace(tzinfo=timezone.utc)
                if not window_start or now - window_start >= timedelta(hours=1):
                    user.forgot_password_attempts = 0
                    user.forgot_password_window_started_at = now
                if user.forgot_password_attempts >= max_forgot:
                    log_activity(
                        "forgot_password_blocked",
                        f"Forgot-password blocked for {user.email} after reaching the {max_forgot}/hour limit.",
                        actor=user,
                    )
                    db.session.commit()
                    if is_ajax:
                        debug_error = "Forgot-password request limit reached for this account." if current_app.debug else None
                        return jsonify({"ok": True, "mail_sent": False, "reset_url": None, "mail_error": debug_error})
                    flash("If that email is registered, a reset link has been sent.", "info")
                    return redirect(url_for("auth.login"))
                token = _make_reset_token(user)
                reset_url = url_for("auth.reset_password", token=token, _external=True)
                sent, mail_error = _send_reset_email(email, reset_url)
                user.forgot_password_attempts = int(user.forgot_password_attempts or 0) + 1
                if not user.forgot_password_window_started_at:
                    user.forgot_password_window_started_at = now
                log_activity(
                    "forgot_password",
                    f"Forgot-password requested for {user.email}. Delivery: {'sent' if sent else 'fallback'}.",
                    actor=user,
                )
                db.session.commit()
                if sent:
                    current_app.logger.info("Password reset email sent to %s via SMTP.", email)
                else:
                    # Never log the reset URL (token equivalent) in production.
                    if current_app.debug:
                        current_app.logger.warning("Password reset email fallback mode for %s (SMTP not sent). Link: %s | err=%s", email, reset_url, mail_error)
                    else:
                        current_app.logger.warning("Password reset email failed for %s (SMTP not sent). err=%s", email, mail_error)
                        flash("Email service is temporarily unavailable. Please contact support and try again later.", "warning")
            if is_ajax:
                # Only expose link in development fallback when email was not sent.
                dev_link = reset_url if (reset_url and not sent) else None
                debug_error = (mail_error if (not sent and current_app.debug) else None)
                return jsonify({"ok": True, "mail_sent": bool(sent), "reset_url": dev_link, "mail_error": debug_error})
            flash("If that email is registered, a reset link has been sent.", "info")
            if reset_url and not sent and current_app.debug:
                flash(f"Dev reset link: {reset_url}", "warning")
            return redirect(url_for("auth.login"))
        else:
            if is_ajax:
                errors = {f: errs for f, errs in form.errors.items()}
                return jsonify({"ok": False, "errors": errors}), 422
            flash("Please enter a valid email address.", "warning")
            return redirect(url_for("auth.login"))
    return render_template("auth/forgot_password.html", form=form, title="Forgot Password")


@auth_bp.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    if current_user.is_authenticated:
        # Allow reset links to work consistently even if a user is already signed in.
        logout_user()
        session.clear()
        flash("Please set your new password, then sign in again.", "info")

    user = _verify_reset_token(token)
    if not user:
        flash("This password reset link is invalid or has expired.", "danger")
        return redirect(url_for("auth.login"))

    form = ResetPasswordForm()
    if form.validate_on_submit():
        min_length = max(_get_security_int("min_password_length", 8), 8)
        if len(form.password.data or "") < min_length:
            form.password.errors.append(f"Password must be at least {min_length} characters.")
            return render_template("auth/reset_password.html", form=form, token=token, title="Reset Password")
        user.set_password(form.password.data)
        user.forgot_password_attempts = 0
        user.forgot_password_window_started_at = None
        log_activity("password_reset", f"Password reset completed for {user.email}.", actor=user)
        db.session.commit()
        flash("Your password has been reset. You can now sign in.", "success")
        return redirect(url_for("auth.login"))

    return render_template("auth/reset_password.html", form=form, token=token, title="Reset Password")
