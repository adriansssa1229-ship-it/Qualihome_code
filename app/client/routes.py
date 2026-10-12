import os
import re
import json
from urllib.parse import quote
from decimal import Decimal
from flask import render_template, redirect, url_for, session, flash, current_app, request, make_response
from flask_login import login_required, current_user
from functools import wraps
from werkzeug.utils import secure_filename

from . import client_bp
from .forms import QualifyForm
from ..models import db, UserProfile, QualificationResult, ActivityLog, TrippingRequest, Property, User, AgentNotification, log_activity
from flask import jsonify
import datetime as dt
from ..ml import c50_engine

# --- Session key prefix -------------------------------------------------------
_SESSION_PREFIX = "qualify_"


def client_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if current_user.role != "client":
            flash("Only registered clients can access the qualification wizard.", "warning")
            return redirect(url_for("main.index"))
        return f(*args, **kwargs)
    return decorated


# --- Helpers -----------------------------------------------------------------

_COMMA_NUMERIC_FIELDS = {"gross_monthly_income", "monthly_debt_loans", "budget_min", "budget_max"}


def _strip_comma_fields(req, field_names: set | None = None) -> None:
    """Remove thousands separators from numeric POST fields in-place.

    Must run BEFORE the WTForms form object is constructed, because
    DecimalField.process_formdata (Decimal("1,000")) executes before any
    field-level `filters` and would otherwise record "Not a valid decimal
    value." Safe to call on GET (no-op when form is empty).
    """
    targets = field_names or _COMMA_NUMERIC_FIELDS
    try:
        form = req.form
        if not form:
            return
        from werkzeug.datastructures import ImmutableMultiDict
        cleaned = []
        touched = False
        for key in form.keys():
            for value in form.getlist(key):
                if key in targets and isinstance(value, str) and "," in value:
                    value = value.replace(",", "")
                    touched = True
                cleaned.append((key, value))
        if touched:
            req.form = ImmutableMultiDict(cleaned)
    except Exception:
        pass


def _compute_result(gross_income: float, monthly_debt: float,
                    employment_type: str = "employed",
                    tenure_months: int = 0, age: int = 30,
                    dependents: int = 0):
    """
    Runs the Similarity-Augmented C5.0 Classifier.
    Falls back to rule-based DTI logic when not enough training data exists.
    """
    status, dti, max_loanable, similarity_score, factors_json = c50_engine.predict(
        gross_income   = gross_income,
        monthly_loans  = monthly_debt,
        tenure_months  = tenure_months,
        employment_type= employment_type,
        age            = age,
        dependents     = dependents,
    )
    return status, dti, max_loanable, similarity_score, factors_json


# --- Combined Wizard (single-page) -------------------------------------------

@client_bp.route("/qualify", methods=["GET", "POST"])
@login_required
@client_required
def qualify():
    # Strip thousands separators BEFORE WTForms binds request.form.
    # (DecimalField.process_formdata runs before field `filters`, so commas
    # must be removed from the raw form data, not just via filters.)
    if request.method == "POST":
        _strip_comma_fields(request, {"gross_monthly_income", "monthly_debt_loans", "budget_min", "budget_max"})
    form     = QualifyForm()
    fail_step = 1
    legacy_pref_values = {
        "house-and-lot",
        "pre-selling",
        "ready-for-occupancy",
        "resale",
    }

    available_models = (db.session.query(Property.name)
                        .filter(db.func.lower(Property.status) == "available")
                        .filter(db.or_(Property.approval_status == "approved", Property.approval_status.is_(None)))
                        .order_by(Property.name.asc())
                        .all())
    model_names = sorted({(name or "").strip() for (name,) in available_models if (name or "").strip()}, key=lambda x: x.lower())
    model_choices = [("", "Any model (optional)")] + [(name, name) for name in model_names]

    # Backward compatibility: old saved values were model type keys (e.g. house-and-lot)
    # while current UI stores preferred model name.
    submitted_pref = (request.form.get("preferred_type") or "").strip() if request.method == "POST" else ""
    if submitted_pref and submitted_pref in legacy_pref_values and not any(v == submitted_pref for v, _ in model_choices):
        model_choices.append((submitted_pref, submitted_pref.replace("-", " ").title() + " (legacy)"))

    form.preferred_type.choices = model_choices

    if request.method == "GET":
        profile = current_user.profile
        if profile:
            form.gross_monthly_income.data = profile.gross_income
            form.monthly_debt_loans.data   = profile.monthly_loans if profile.monthly_loans and profile.monthly_loans > 0 else None
            form.sss_gsis_umid.data        = profile.sss_gsis_umid or ""
            form.tin_no.data               = profile.tin_no or ""
            form.employment_status.data    = profile.employment_type or ""
            form.tenure_months.data        = profile.tenure_months
            form.age.data                  = profile.age
            form.dependents.data           = profile.dependents if profile.dependents is not None else 0
            preferred_model = (profile.preferred_type or "").strip()
            if preferred_model and not any(v == preferred_model for v, _ in form.preferred_type.choices):
                form.preferred_type.choices.append((preferred_model, preferred_model))
            form.preferred_type.data       = preferred_model
            form.budget_min.data           = profile.budget_min if profile.budget_min and profile.budget_min > 0 else None
            form.budget_max.data           = profile.budget_max if profile.budget_max and profile.budget_max > 0 else None

    if form.validate_on_submit():
        assessment_mode = (request.form.get("assessment_mode") or "reassess").strip().lower()
        gross   = float(form.gross_monthly_income.data)
        debt    = float(form.monthly_debt_loans.data or 0)

        profile = current_user.profile
        if not profile:
            profile = UserProfile(user_id=current_user.id)
            db.session.add(profile)

        profile.gross_income       = form.gross_monthly_income.data
        profile.monthly_loans      = form.monthly_debt_loans.data or 0
        if "sss_gsis_umid" in request.form:
            profile.sss_gsis_umid  = (form.sss_gsis_umid.data or "").strip()[:60] or None
        if "tin_no" in request.form:
            profile.tin_no         = (form.tin_no.data or "").strip()[:30] or None
        profile.employment_type    = form.employment_status.data
        profile.tenure_months      = form.tenure_months.data or 0
        profile.age                = form.age.data
        profile.dependents         = int(form.dependents.data or 0)
        preferred_model = (form.preferred_type.data or "").strip()[:40]
        if preferred_model in legacy_pref_values:
            preferred_model = ""
        profile.preferred_type     = preferred_model or None
        profile.budget_min         = form.budget_min.data or 0
        profile.budget_max         = form.budget_max.data or 0

        status, dti, max_loanable, score, factors_json = _compute_result(
            gross_income    = gross,
            monthly_debt    = debt,
            employment_type = form.employment_status.data or "employed",
            tenure_months   = int(form.tenure_months.data or 0),
            age             = int(form.age.data or 30),
            dependents      = int(form.dependents.data or 0),
        )
        result = QualificationResult(
            user_id          = current_user.id,
            status           = status,
            dti_ratio        = dti,
            max_loanable     = max_loanable,
            similarity_score = score,
            assessment_mode  = ("new" if assessment_mode == "new" else "reassess"),
            factors_json     = factors_json,
        )

        # "New Assessment" replaces prior records; "Re-Assess" keeps history.
        if assessment_mode == "new":
            QualificationResult.query.filter_by(user_id=current_user.id).delete(synchronize_session=False)

        db.session.add(result)
        log_activity("assessment", f"Assessment submitted — {status} (DTI: {dti:.1f}%)")
        db.session.commit()

        session["last_result_id"] = result.id
        for key in ("step1", "step2", "step3"):
            session.pop(_SESSION_PREFIX + key, None)

        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"ok": True})

        return redirect(url_for("client.result"))

    # Determine which step to open when server-side errors are returned
    if form.errors:
        step_fields = [
            {"gross_monthly_income", "monthly_debt_loans", "sss_gsis_umid", "tin_no"},
            {"employment_status", "tenure_months", "age", "dependents"},
            {"preferred_type", "budget_min", "budget_max"},
        ]
        for idx, fields in enumerate(step_fields, 1):
            if fields & set(form.errors.keys()):
                fail_step = idx
                break

    if request.method == "POST" and request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({"ok": False, "fail_step": fail_step, "errors": form.errors}), 422

    return redirect(url_for("main.client_dashboard") + "?page=assessment")


# --- Step routes (redirect to combined wizard) --------------------------------

@client_bp.route("/step1", methods=["GET", "POST"])
@login_required
@client_required
def step1():
    return redirect(url_for("client.qualify"))


# --- Step 2 - Employment ------------------------------------------------------

@client_bp.route("/step2", methods=["GET", "POST"])
@login_required
@client_required
def step2():
    return redirect(url_for("client.qualify"))


# --- Step 3 - Requirements Check ----------------------------------------------

@client_bp.route("/step3", methods=["GET", "POST"])
@login_required
@client_required
def step3():
    return redirect(url_for("client.qualify"))


# --- Save & Compute Result ----------------------------------------------------

@client_bp.route("/save", methods=["GET", "POST"])
@login_required
@client_required
def save_and_result():
    # Accept either GET (from step3 redirect) or POST (direct form)
    for key in ("step1", "step2"):
        if _SESSION_PREFIX + key not in session:
            flash("Incomplete assessment - please start from Step 1.", "warning")
            return redirect(url_for("client.step1"))

    s1 = session[_SESSION_PREFIX + "step1"]
    s2 = session[_SESSION_PREFIX + "step2"]

    gross  = float(str(s1.get("gross_monthly_income", 0) or 0).replace(",", ""))
    debt   = float(str(s1.get("monthly_debt_loans", 0)   or 0).replace(",", ""))
    # -- Update / create normalized profile -----------------------------------
    profile = current_user.profile
    if not profile:
        profile = UserProfile(user_id=current_user.id)
        db.session.add(profile)

    profile.gross_income    = Decimal(str(s1["gross_monthly_income"]).replace(",", ""))
    profile.monthly_loans   = Decimal(str(s1["monthly_debt_loans"]).replace(",", ""))
    profile.employment_type = s2["employment_status"]
    profile.tenure_months   = int(s2["tenure_months"])
    profile.age             = int(s2["age"])
    profile.dependents      = int(s2.get("dependents", 0) or 0)

    # -- Compute result via C5.0 engine ----------------------------------------
    status, dti, max_loanable, score, factors_json = _compute_result(
        gross_income    = gross,
        monthly_debt    = debt,
        employment_type = s2.get("employment_status", "employed"),
        tenure_months   = int(s2.get("tenure_months", 0) or 0),
        age             = int(s2.get("age", 30) or 30),
        dependents      = int(s2.get("dependents", 0) or 0),
    )

    result = QualificationResult(
        user_id          = current_user.id,
        status           = status,
        dti_ratio        = dti,
        max_loanable     = max_loanable,
        similarity_score = score,
        assessment_mode  = "reassess",
        factors_json     = factors_json,
    )
    db.session.add(result)
    log_activity("assessment", f"Assessment submitted — {status} (DTI: {dti:.1f}%)")
    db.session.commit()

    session["last_result_id"] = result.id
    for key in ("step1", "step2", "step3"):
        session.pop(_SESSION_PREFIX + key, None)

    return redirect(url_for("client.result"))


@client_bp.route("/result")
@login_required
@client_required
def result():
    result_id = session.get("last_result_id")
    if not result_id:
        qr = (
            QualificationResult.query
            .filter_by(user_id=current_user.id)
            .order_by(QualificationResult.id.desc())
            .first()
        )
    else:
        qr = db.session.get(QualificationResult, result_id)

    if not qr:
        flash("No assessment found. Please complete the qualification form.", "info")
        return redirect(url_for("client.step1"))

    return redirect(url_for("main.client_dashboard") + "?page=assessment")


@client_bp.route("/trip/<int:trip_id>/continue-purchase", methods=["GET"])
@login_required
@client_required
def continue_to_purchase(trip_id):
    trip = db.session.get(TrippingRequest, trip_id)
    if not trip or trip.client_id != current_user.id:
        flash("Trip request not found.", "warning")
        return redirect(url_for("main.client_dashboard") + "?page=trips")

    if (trip.status or "").strip().lower() != "visited":
        flash("Continue to Purchase is only available after the trip is marked as visited.", "warning")
        return redirect(url_for("main.client_dashboard") + "?page=trips")

    if trip.sale_record is not None or (trip.property_item and (trip.property_item.status or "").lower() == "sold"):
        flash("This model has already been marked as sold.", "info")
        return redirect(url_for("main.client_dashboard") + "?page=trips")

    return redirect(url_for("main.client_dashboard", page="trips", open_purchase_trip=trip.id))


@client_bp.route("/trip/<int:trip_id>/buyer-form-submit", methods=["POST"])
@login_required
@client_required
def submit_buyer_form(trip_id):
    trip = db.session.get(TrippingRequest, trip_id)
    if not trip or trip.client_id != current_user.id:
        return jsonify(ok=False, error="Trip request not found."), 404

    status = (trip.status or "").strip().lower()
    if status != "visited":
        return jsonify(ok=False, error="Buyer form can only be submitted after visit is marked completed."), 400

    if trip.sale_record is not None or (trip.property_item and (trip.property_item.status or "").lower() == "sold"):
        return jsonify(ok=False, error="This model has already been marked as sold."), 400

    if bool(trip.purchase_form_submitted):
        return jsonify(ok=False, error="Buyer Information Form has already been submitted for this trip."), 400

    profile = current_user.profile
    if not profile or not profile.esignature_data:
        return jsonify(ok=False, error="Please upload your e-signature photo before submitting."), 400

    data = request.get_json(silent=True) or {}
    if not bool(data.get("consent_accepted")):
        return jsonify(ok=False, error="Please agree to the Consent & Authorization Clause before submitting."), 400

    form_data = data.get("form_data") if isinstance(data.get("form_data"), dict) else {}
    non_empty_count = sum(1 for v in form_data.values() if str(v or "").strip())

    history = []
    if (trip.purchase_form_data or "").strip():
        try:
            prev_payload = json.loads(trip.purchase_form_data)
            if isinstance(prev_payload, dict):
                history = prev_payload.get("_purchase_form_history") if isinstance(prev_payload.get("_purchase_form_history"), list) else []
                prev_snapshot = {k: v for k, v in prev_payload.items() if not str(k).startswith("_purchase_form_")}
                if prev_snapshot:
                    history.append({
                        "status": str(prev_payload.get("_purchase_form_status") or "submitted"),
                        "submitted_at": prev_payload.get("_purchase_form_submitted_at") or (trip.purchase_form_submitted_at.isoformat() if trip.purchase_form_submitted_at else None),
                        "archived_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                        "data": prev_snapshot,
                    })
        except Exception:
            history = []

    payload_to_store = dict(form_data)
    payload_to_store["_purchase_form_status"] = "submitted"
    payload_to_store["_purchase_form_submitted_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    if history:
        payload_to_store["_purchase_form_history"] = history

    trip.purchase_form_submitted = True
    trip.purchase_form_submitted_at = dt.datetime.now(dt.timezone.utc)
    trip.purchase_form_data = json.dumps(payload_to_store, ensure_ascii=True)

    property_name = trip.property_item.name if trip.property_item else f"Property #{trip.property_id}"
    summary = (
        f"Buyer Information Form submitted for Trip #{trip.id} ({property_name}); "
        f"filled fields: {non_empty_count}."
    )
    log_activity("buyer_form_submit", summary)

    admins = User.query.filter_by(role="admin").all()
    for admin in admins:
        db.session.add(AgentNotification(
            agent_id=admin.id,
            property_id=trip.property_id,
            event_type="buyer_form_submit",
            message=(f"Buyer form submitted by {current_user.full_name} for Trip #{trip.id}.")[:255],
            is_read=False,
        ))

    db.session.commit()
    return jsonify(ok=True)


@client_bp.route("/trip/<int:trip_id>/buyer-signature-upload", methods=["POST"])
@login_required
@client_required
def upload_buyer_signature(trip_id):
    trip = db.session.get(TrippingRequest, trip_id)
    if not trip or trip.client_id != current_user.id:
        return jsonify({"error": "Trip request not found."}), 404

    status = (trip.status or "").strip().lower()
    if status != "visited":
        return jsonify({"error": "E-signature upload is only available after visit is marked completed."}), 400

    if trip.sale_record is not None or (trip.property_item and (trip.property_item.status or "").lower() == "sold"):
        return jsonify({"error": "This model has already been marked as sold."}), 400

    file = request.files.get("esignature")
    if not file or not file.filename:
        return jsonify({"error": "No e-signature file provided."}), 400

    ext = os.path.splitext(secure_filename(file.filename))[1].lower()
    if ext not in _ALLOWED_ESIGN_EXTS:
        return jsonify({"error": "Unsupported file type. Use JPG, PNG, or WEBP."}), 400

    payload = file.read()
    if not payload:
        return jsonify({"error": "Uploaded file is empty."}), 400
    if len(payload) > _MAX_DOC_BYTES:
        return jsonify({"error": "E-signature file too large. Maximum is 10 MB."}), 413

    profile = current_user.profile
    if not profile:
        profile = UserProfile(user_id=current_user.id)
        db.session.add(profile)

    profile.esignature_data = payload
    profile.esignature_mimetype = file.mimetype or "application/octet-stream"
    profile.esignature_filename = secure_filename(file.filename)[:255]

    db.session.commit()
    return jsonify({"success": True, "filename": profile.esignature_filename})


# ── Client AJAX: request a tripping visit ─────────────────────────────────
@client_bp.route("/trip/request", methods=["POST"])
@login_required
@client_required
def request_trip():
    data = request.get_json(silent=True) or {}
    prop_id   = data.get("property_id")
    pref_date = data.get("preferred_date", "").strip()
    pref_time = data.get("preferred_time", "").strip() or None

    if not prop_id or not pref_date:
        return jsonify(ok=False, error="Property and preferred date are required."), 400

    prop = db.session.get(Property, prop_id)
    if not prop:
        return jsonify(ok=False, error="Property not found."), 404
    if (prop.status or "").lower() != "available":
        return jsonify(ok=False, error="This property is no longer available."), 400

    try:
        parsed_date = dt.date.fromisoformat(pref_date)
    except ValueError:
        return jsonify(ok=False, error="Invalid date format."), 400

    if parsed_date < dt.date.today():
        return jsonify(ok=False, error="Preferred date must be today or in the future."), 400

    trip = TrippingRequest(
        client_id=current_user.id,
        property_id=prop_id,
        preferred_date=parsed_date,
        preferred_time=pref_time,
        status="pending",
    )
    db.session.add(trip)
    db.session.commit()
    return jsonify(ok=True, trip_id=trip.id)


# ── Client AJAX: remove own tripping request ─────────────────────────────
@client_bp.route("/trip/<int:trip_id>/cancel", methods=["POST"])
@login_required
@client_required
def cancel_trip(trip_id):
    trip = db.session.get(TrippingRequest, trip_id)
    if not trip or trip.client_id != current_user.id:
        return jsonify(ok=False, error="Request not found."), 404
    db.session.delete(trip)
    db.session.commit()
    return jsonify(ok=True)


# ── Client AJAX: save profile ─────────────────────────────────────────────
@client_bp.route("/profile/save", methods=["POST"])
@login_required
@client_required
def save_profile():
    data = request.get_json(silent=True) or {}

    def _opt_yes_no_to_bool(raw_val):
        val = (raw_val or "").strip().lower()
        if val == "yes":
            return True
        if val == "no":
            return False
        return None

    # Update basic user fields
    first_name = data.get("first_name", "").strip()
    middle_name = data.get("middle_name", "").strip()
    last_name  = data.get("last_name", "").strip()
    email      = data.get("email", "").strip()
    username   = data.get("username", "").strip()

    if not first_name or not last_name or not email or not username:
        return jsonify(ok=False, error="First name, last name, email, and username are required."), 400
    if len(username) < 3:
        return jsonify(ok=False, error="Username must be at least 3 characters."), 400
    if not re.fullmatch(r"[\w.]+", username):
        return jsonify(ok=False, error="Username may contain only letters, numbers, dots, and underscores."), 400

    # Check email uniqueness (allow keeping own email)
    existing = User.query.filter(User.email == email, User.id != current_user.id).first()
    if existing:
        return jsonify(ok=False, error="That email is already registered to another account."), 409
    existing_username = User.query.filter(User.username == username, User.id != current_user.id).first()
    if existing_username:
        return jsonify(ok=False, error="That username is already taken."), 409

    current_user.first_name = first_name
    current_user.middle_name = middle_name or None
    current_user.last_name  = last_name
    current_user.email      = email
    current_user.username   = username

    # Upsert normalized profile
    profile = current_user.profile
    if not profile:
        profile = UserProfile(user_id=current_user.id)
        db.session.add(profile)

    contact_number = data.get("contact_number", "").strip() or None
    profile.contact_number   = contact_number
    current_user.contact_number = contact_number  # keep User table in sync
    profile.civil_status     = data.get("civil_status", "").strip() or None
    profile.citizenship      = data.get("citizenship", "").strip() or None
    profile.gender           = data.get("gender", "").strip() or None
    profile.address          = data.get("address", "").strip() or None
    if "address_line" in data:
        profile.address_line = data.get("address_line", "").strip() or None

    try:
        profile.dependents   = int(data.get("dependents", 0) or 0)
    except (ValueError, TypeError):
        profile.dependents   = 0

    try:
        profile.age          = int(data.get("age") or 0) or None
    except (ValueError, TypeError):
        profile.age          = None

    bd = data.get("birth_date", "").strip()
    if bd:
        try:
            profile.birth_date = dt.date.fromisoformat(bd)
        except ValueError:
            pass
    else:
        profile.birth_date = None

    profile.birthplace = data.get("birthplace", "").strip() or None
    profile.birth_region_code = data.get("birth_region_code", "").strip() or None
    profile.birth_region_name = data.get("birth_region_name", "").strip() or None
    profile.birth_province_code = data.get("birth_province_code", "").strip() or None
    profile.birth_province_name = data.get("birth_province_name", "").strip() or None
    profile.birth_citymun_code = data.get("birth_citymun_code", "").strip() or None
    profile.birth_citymun_name = data.get("birth_citymun_name", "").strip() or None
    profile.birth_barangay_code = data.get("birth_barangay_code", "").strip() or None
    profile.birth_barangay_name = data.get("birth_barangay_name", "").strip() or None

    if "employment_type" in data:
        profile.employment_type = data.get("employment_type", "").strip() or None
    if "employer_name" in data:
        profile.employer_name = data.get("employer_name", "").strip() or None
    if "employer_phone" in data:
        profile.employer_phone = data.get("employer_phone", "").strip() or None
    if "employer_email" in data:
        profile.employer_email = data.get("employer_email", "").strip() or None
    profile.employer_business_address = data.get("employer_business_address", "").strip() or None
    profile.employer_region_code = data.get("employer_region_code", "").strip() or None
    profile.employer_region_name = data.get("employer_region_name", "").strip() or None
    profile.employer_province_code = data.get("employer_province_code", "").strip() or None
    profile.employer_province_name = data.get("employer_province_name", "").strip() or None
    profile.employer_citymun_code = data.get("employer_citymun_code", "").strip() or None
    profile.employer_citymun_name = data.get("employer_citymun_name", "").strip() or None
    profile.employer_barangay_code = data.get("employer_barangay_code", "").strip() or None
    profile.employer_barangay_name = data.get("employer_barangay_name", "").strip() or None

    def _float(val, default=None):
        try:
            return float(val) if val not in (None, "") else default
        except (ValueError, TypeError):
            return default

    def _int(val, default=None):
        try:
            return int(val) if val not in (None, "") else default
        except (ValueError, TypeError):
            return default

    if "tenure_months" in data:
        profile.tenure_months = _int(data.get("tenure_months"))
    if "gross_income" in data:
        profile.gross_income = _float(data.get("gross_income"))
    if "monthly_loans" in data:
        profile.monthly_loans = _float(data.get("monthly_loans"), 0)
    if "other_deductions" in data:
        profile.other_deductions = _float(data.get("other_deductions"), 0)

    profile.home_region_code = data.get("home_region_code", "").strip() or None
    profile.home_region_name = data.get("home_region_name", "").strip() or None
    profile.home_province_code = data.get("home_province_code", "").strip() or None
    profile.home_province_name = data.get("home_province_name", "").strip() or None
    profile.home_citymun_code = data.get("home_citymun_code", "").strip() or None
    profile.home_citymun_name = data.get("home_citymun_name", "").strip() or None
    profile.home_barangay_code = data.get("home_barangay_code", "").strip() or None
    profile.home_barangay_name = data.get("home_barangay_name", "").strip() or None
    profile.street = data.get("street", "").strip() or None
    profile.blk = data.get("blk", "").strip() or None
    profile.lot = data.get("lot", "").strip() or None
    profile.country = data.get("country", "").strip() or None
    umid_raw = data.get("sss_gsis_umid", "").strip()
    if umid_raw and not re.fullmatch(r"\d{2}-\d{7}-\d", umid_raw):
        return jsonify(ok=False, error="SSS/GSIS/UMID must follow 00-0000000-0 format."), 400
    tin_raw = data.get("tin_no", "").strip()
    if tin_raw and not re.fullmatch(r"\d{3}-\d{3}-\d{3}-\d{3}", tin_raw):
        return jsonify(ok=False, error="TIN must follow 000-000-000-000 format."), 400
    profile.sss_gsis_umid = umid_raw or None
    profile.tin_no = tin_raw or None
    profile.zip_code = data.get("zip_code", "").strip() or None
    profile.subdivision_name = data.get("subdivision_name", "").strip() or None
    profile.social_instagram = data.get("social_instagram", "").strip() or None
    profile.social_twitter_x = data.get("social_twitter_x", "").strip() or None
    profile.social_viber = data.get("social_viber", "").strip() or None
    profile.social_whatsapp = data.get("social_whatsapp", "").strip() or None
    profile.has_valid_id = _opt_yes_no_to_bool(data.get("has_valid_id"))
    profile.has_income_proof = _opt_yes_no_to_bool(data.get("has_income_proof"))

    if not profile.address:
        addr_parts = [
            profile.address_line,
            profile.home_barangay_name,
            profile.home_citymun_name,
            profile.home_province_name,
            profile.home_region_name,
        ]
        merged = ", ".join([p for p in addr_parts if p])
        profile.address = merged or None

    # Password change (optional)
    old_password = data.get("old_password", "").strip()
    new_password = data.get("new_password", "").strip()
    if new_password:
        if not old_password:
            return jsonify(ok=False, error="Current password is required to set a new password."), 400
        if not current_user.check_password(old_password):
            return jsonify(ok=False, error="Current password is incorrect."), 400
        if len(new_password) < 6:
            return jsonify(ok=False, error="New password must be at least 6 characters."), 400
        current_user.set_password(new_password)

    db.session.commit()
    return jsonify(ok=True, full_name=current_user.full_name)


_ALLOWED_IMG_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
_ALLOWED_DOC_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".pdf"}
_ALLOWED_ESIGN_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
_MAX_DOC_BYTES = 10 * 1024 * 1024


def _normalize_doc_kind(raw_kind: str | None) -> str | None:
    key = (raw_kind or "").strip().lower().replace("_", "-")
    if key in {"valid-id", "validid"}:
        return "valid-id"
    if key in {"income-proof", "incomeproof", "proof-of-income"}:
        return "income-proof"
    return None


# ── Client: upload profile avatar ─────────────────────────────────────────────
@client_bp.route("/profile/upload-avatar", methods=["POST"])
@login_required
@client_required
def upload_client_avatar():
    file = request.files.get("avatar")
    if not file or not file.filename:
        return jsonify({"error": "No file provided"}), 400
    ext = os.path.splitext(secure_filename(file.filename))[1].lower()
    if ext not in _ALLOWED_IMG_EXTS:
        return jsonify({"error": "Unsupported file type. Use JPG, PNG, WEBP, or GIF."}), 400
    profile = current_user.profile
    if not profile:
        profile = UserProfile(user_id=current_user.id)
        db.session.add(profile)
    profile.avatar_data     = file.read()
    profile.avatar_mimetype = file.mimetype or "image/jpeg"
    db.session.commit()
    return jsonify({"success": True, "url": url_for("main.serve_client_avatar", user_id=current_user.id)})


# ── Client: upload profile banner ─────────────────────────────────────────────
@client_bp.route("/profile/upload-banner", methods=["POST"])
@login_required
@client_required
def upload_client_banner():
    file = request.files.get("banner")
    if not file or not file.filename:
        return jsonify({"error": "No file provided"}), 400
    ext = os.path.splitext(secure_filename(file.filename))[1].lower()
    if ext not in _ALLOWED_IMG_EXTS:
        return jsonify({"error": "Unsupported file type. Use JPG, PNG, WEBP, or GIF."}), 400
    profile = current_user.profile
    if not profile:
        profile = UserProfile(user_id=current_user.id)
        db.session.add(profile)
    profile.banner_data     = file.read()
    profile.banner_mimetype = file.mimetype or "image/jpeg"
    db.session.commit()
    return jsonify({"success": True, "url": url_for("main.serve_client_banner", user_id=current_user.id)})


# ── Client: upload documentation (valid id / proof of income) ───────────────
@client_bp.route("/profile/upload-document", methods=["POST"])
@login_required
@client_required
def upload_client_document():
    doc_kind = _normalize_doc_kind(request.form.get("doc_kind"))
    if not doc_kind:
        return jsonify({"error": "Invalid document type."}), 400

    file = request.files.get("document")
    if not file or not file.filename:
        return jsonify({"error": "No file provided."}), 400

    ext = os.path.splitext(secure_filename(file.filename))[1].lower()
    if ext not in _ALLOWED_DOC_EXTS:
        return jsonify({"error": "Unsupported file type. Use JPG, PNG, WEBP, or PDF."}), 400

    payload = file.read()
    if not payload:
        return jsonify({"error": "Uploaded file is empty."}), 400
    if len(payload) > _MAX_DOC_BYTES:
        return jsonify({"error": "Document too large. Maximum is 10 MB."}), 413

    profile = current_user.profile
    if not profile:
        profile = UserProfile(user_id=current_user.id)
        db.session.add(profile)

    filename = secure_filename(file.filename)[:255]
    mimetype = file.mimetype or "application/octet-stream"
    if doc_kind == "valid-id":
        profile.valid_id_data = payload
        profile.valid_id_mimetype = mimetype
        profile.valid_id_filename = filename
    else:
        profile.income_proof_data = payload
        profile.income_proof_mimetype = mimetype
        profile.income_proof_filename = filename

    db.session.commit()
    return jsonify({
        "success": True,
        "doc_kind": doc_kind,
        "filename": filename,
        "url": url_for("client.view_client_document", doc_kind=doc_kind),
    })


# ── Client: view own documentation in a tab with app favicon ─────────────────
@client_bp.route("/profile/document-view/<doc_kind>")
@login_required
@client_required
def view_client_document(doc_kind):
    doc_kind = _normalize_doc_kind(doc_kind)
    if not doc_kind:
        return "Not Found", 404

    profile = current_user.profile
    if not profile:
        return "Not Found", 404

    if doc_kind == "valid-id":
        has_payload = bool(profile.valid_id_data)
        filename = profile.valid_id_filename or "valid-id"
    else:
        has_payload = bool(profile.income_proof_data)
        filename = profile.income_proof_filename or "proof-of-income"

    if not has_payload:
        return "Not Found", 404

    file_url = url_for("client.serve_client_document", doc_kind=doc_kind)
    return render_template("client/document_view.html", file_url=file_url, filename=filename)


# ── Client: serve own documentation file from DB ─────────────────────────────
@client_bp.route("/profile/document/<doc_kind>")
@login_required
@client_required
def serve_client_document(doc_kind):
    doc_kind = _normalize_doc_kind(doc_kind)
    if not doc_kind:
        return "Not Found", 404

    profile = current_user.profile
    if not profile:
        return "Not Found", 404

    if doc_kind == "valid-id":
        payload = profile.valid_id_data
        mimetype = profile.valid_id_mimetype or "application/octet-stream"
        filename = profile.valid_id_filename or "valid-id"
    else:
        payload = profile.income_proof_data
        mimetype = profile.income_proof_mimetype or "application/octet-stream"
        filename = profile.income_proof_filename or "proof-of-income"

    if not payload:
        return "Not Found", 404

    resp = make_response(payload)
    resp.headers["Content-Type"] = mimetype
    resp.headers["Content-Disposition"] = f"inline; filename*=UTF-8''{quote(filename)}"
    resp.headers["Cache-Control"] = "no-store"
    return resp


# ── Client: delete documentation file ────────────────────────────────────────
@client_bp.route("/profile/delete-document/<doc_kind>", methods=["POST"])
@login_required
@client_required
def delete_client_document(doc_kind):
    doc_kind = _normalize_doc_kind(doc_kind)
    if not doc_kind:
        return jsonify({"error": "Invalid document type."}), 400

    profile = current_user.profile
    if not profile:
        return jsonify({"success": True})

    if doc_kind == "valid-id":
        profile.valid_id_data = None
        profile.valid_id_mimetype = None
        profile.valid_id_filename = None
    else:
        profile.income_proof_data = None
        profile.income_proof_mimetype = None
        profile.income_proof_filename = None

    db.session.commit()
    return jsonify({"success": True, "doc_kind": doc_kind})


# ── Client: delete avatar ──────────────────────────────────────────────────────
@client_bp.route("/profile/delete-avatar", methods=["POST"])
@login_required
@client_required
def delete_client_avatar():
    profile = current_user.profile
    if profile:
        profile.avatar_data     = None
        profile.avatar_mimetype = None
        db.session.commit()
    return jsonify({"success": True})


# ── Client: delete banner ──────────────────────────────────────────────────────
@client_bp.route("/profile/delete-banner", methods=["POST"])
@login_required
@client_required
def delete_client_banner():
    profile = current_user.profile
    if profile:
        profile.banner_data     = None
        profile.banner_mimetype = None
        db.session.commit()
    return jsonify({"success": True})
