import os
import uuid
import re
import json
import base64
from collections import namedtuple
from threading import Thread, Lock
from datetime import datetime, timezone, date, time, timedelta
from flask import Blueprint, render_template, redirect, url_for, request, jsonify, current_app, send_from_directory, make_response, session, g, abort
from flask_login import login_required, current_user
from sqlalchemy.orm import selectinload, defer
from werkzeug.security import generate_password_hash
from werkzeug.utils import secure_filename
from ..models import db, Property, User, UserProfile, QualificationResult, TrippingRequest, PropertySale, Project, Subdivision, ActivityLog, AgentNotification, HistoricalBuyer, HistoricalBuyerRecord, PropertyPricingDetailRequest, PropertyPricingDetailRequestHistory, SystemConfig, AgentAvailability, log_activity
from ..ml import c50_engine
from .psgc import list_regions, list_provinces, list_cities, list_barangays

main_bp = Blueprint("main", __name__)

_AUTO_SYNC_NOTE_PREFIX = "AUTO_SYNC_SALE_ID="
_AUTO_SYNC_NOTE_RE = re.compile(r"AUTO_SYNC_SALE_ID=(\d+)")
_C50_RETRAIN_LOCK = Lock()


@main_bp.route("/health", methods=["GET"])
def health():
    return {"status": "ok"}, 200


def _normalize_outcome_label(value: str | None) -> str:
    raw = (value or "").strip().lower()
    if raw in {"qualified"}:
        return "Qualified"
    if raw in {"conditionally qualified", "conditional", "conditionally-qualified", "conditional qualified"}:
        return "Conditionally Qualified"
    if raw in {"not qualified", "not-qualified", "disqualified"}:
        return "Not Qualified"
    return "Conditionally Qualified"


def _build_auto_sync_note(sale_id: int, extra_note: str | None = None) -> str:
    base = f"{_AUTO_SYNC_NOTE_PREFIX}{int(sale_id)}"
    suffix = (extra_note or "").strip()
    if not suffix:
        return base[:255]
    return f"{base} | {suffix}"[:255]


def _extract_auto_sync_sale_id(note: str | None) -> int | None:
    m = _AUTO_SYNC_NOTE_RE.search(str(note or ""))
    if not m:
        return None
    try:
        return int(m.group(1))
    except (TypeError, ValueError):
        return None


def _sync_single_historical_to_training(record: HistoricalBuyerRecord, extra_note: str | None = None,
                                        synced_sale_ids: set[int] | None = None) -> tuple[bool, str | None]:
    sale_id = int(record.sale_id or 0)
    if sale_id <= 0:
        return False, "missing_sale_id"

    if synced_sale_ids is not None:
        # Prefetched set supplied by the caller — O(1) duplicate check,
        # no per-record SELECT.
        if sale_id in synced_sale_ids:
            return False, "duplicate"
    else:
        exists = (HistoricalBuyer.query
                  .filter(HistoricalBuyer.notes.like(f"{_AUTO_SYNC_NOTE_PREFIX}{sale_id}%"))
                  .first())
        if exists:
            return False, "duplicate"

    dti_ratio = None
    if record.dti_ratio is not None:
        try:
            dti_ratio = round(float(record.dti_ratio), 2)
        except (TypeError, ValueError):
            dti_ratio = None

    db.session.add(HistoricalBuyer(
        civil_status=record.civil_status,
        dependents=int(record.dependents or 0),
        age=int(record.age or 30),
        employment_type=record.employment_type or "employed",
        tenure_months=int(record.tenure_months or 0),
        gross_income=float(record.gross_income or 0),
        monthly_loans=float(record.monthly_loans or 0),
        dti_ratio=dti_ratio,
        outcome=_normalize_outcome_label(record.outcome),
        notes=_build_auto_sync_note(sale_id, extra_note or record.notes),
    ))
    if synced_sale_ids is not None:
        synced_sale_ids.add(sale_id)
    return True, None


def _get_synced_sale_ids_from_training() -> set[int]:
    note_rows = (db.session.query(HistoricalBuyer.notes)
                 .filter(HistoricalBuyer.notes.like(f"{_AUTO_SYNC_NOTE_PREFIX}%"))
                 .all())
    sale_ids: set[int] = set()
    for (note,) in note_rows:
        sid = _extract_auto_sync_sale_id(note)
        if sid is not None:
            sale_ids.add(sid)
    return sale_ids


def _trigger_c50_retrain_async(reason: str) -> bool:
    app_obj = current_app._get_current_object()
    if not _C50_RETRAIN_LOCK.acquire(blocking=False):
        return False

    def _worker():
        try:
            with app_obj.app_context():
                buyers = HistoricalBuyer.query.all()
                c50_engine.train(buyers)
                meta = c50_engine.get_meta()
                app_obj.logger.info(
                    "C5.0 retrain finished (%s): trained=%s samples=%s accuracy=%s",
                    reason,
                    meta.get("trained", False),
                    meta.get("n_samples", 0),
                    meta.get("train_accuracy", "N/A"),
                )
        except Exception:
            app_obj.logger.exception("C5.0 retrain failed (%s)", reason)
        finally:
            _C50_RETRAIN_LOCK.release()

    try:
        Thread(target=_worker, daemon=True, name="c50-retrain").start()
        return True
    except Exception:
        _C50_RETRAIN_LOCK.release()
        app_obj.logger.exception("Unable to start C5.0 retrain thread (%s)", reason)
        return False

def _load_local_ph_banks() -> list[str]:
    candidate_paths = [
        os.path.normpath(os.path.join(current_app.root_path, "..", "banks.json")),
        os.path.normpath(os.path.join(current_app.root_path, "banks.json")),
        os.path.normpath(os.path.join(os.getcwd(), "banks.json")),
    ]
    for local_banks_path in candidate_paths:
        if not os.path.exists(local_banks_path):
            continue
        try:
            # utf-8-sig handles optional BOM from edited JSON files on Windows.
            with open(local_banks_path, "r", encoding="utf-8-sig") as fp:
                payload = json.load(fp)
            rows = payload if isinstance(payload, list) else []
            names = []
            seen = set()
            for row in rows:
                if not isinstance(row, dict):
                    continue
                name = str(row.get("name") or "").strip()
                key = name.lower()
                if name and key not in seen:
                    seen.add(key)
                    names.append(name)
            if names:
                return names
        except Exception:
            continue
    return []


def _load_countries() -> list[dict]:
    candidate_paths = [
        os.path.normpath(os.path.join(current_app.root_path, "..", "countries.json")),
        os.path.normpath(os.path.join(current_app.root_path, "countries.json")),
        os.path.normpath(os.path.join(os.getcwd(), "countries.json")),
    ]
    for local_path in candidate_paths:
        if not os.path.exists(local_path):
            continue
        try:
            with open(local_path, "r", encoding="utf-8-sig") as fp:
                payload = json.load(fp)
            rows = payload if isinstance(payload, list) else []
            countries = []
            seen = set()
            for row in rows:
                if not isinstance(row, dict):
                    continue
                code = str(row.get("code") or "").strip().upper()
                name = str(row.get("name") or "").strip()
                if not name:
                    continue
                key = name.lower()
                if key in seen:
                    continue
                seen.add(key)
                countries.append({"code": code, "name": name})
            if countries:
                return countries
        except Exception:
            continue
    return []


# ── Landing Page ──────────────────────────────────────────────────────────────

SUBDIVISIONS = [
    "Camella Homes — Laguna",
    "Crown Asia — Cavite",
    "Brittany — Sta. Rosa",
    "Camella — Pampanga",
    "Bria Homes — Bulacan",
    "Lumina Homes — Batangas",
    "Lancaster New City — Cavite",
    "Idesia — Dasmarinas",
    "Ponticelli — Bacoor",
    "Savannah Trails — Iloilo",
]


def _cloudinary_enabled() -> bool:
    return bool(
        current_app.config.get("CLOUDINARY_CLOUD_NAME")
        and current_app.config.get("CLOUDINARY_API_KEY")
        and current_app.config.get("CLOUDINARY_API_SECRET")
    )


def _store_image_file(file_obj) -> str:
    """Persist an uploaded image.

    Returns an absolute Cloudinary URL when Cloudinary is configured,
    otherwise saves to the local uploads folder and returns the filename.
    The returned reference is stored in the DB as-is.
    """
    if _cloudinary_enabled():
        try:
            import cloudinary.uploader
            if hasattr(file_obj, "stream"):
                file_obj.stream.seek(0)
            result = cloudinary.uploader.upload(
                file_obj,
                folder=current_app.config.get("CLOUDINARY_FOLDER", "qualihome"),
            )
            if result and result.get("secure_url"):
                return result["secure_url"]
        except Exception as exc:
            current_app.logger.warning("Cloudinary upload failed for %s: %s; falling back to local file", getattr(file_obj, "filename", ""), exc)
            if hasattr(file_obj, "stream"):
                try:
                    file_obj.stream.seek(0)
                except Exception:
                    pass
    return _save_local_image_file(file_obj)


def _save_local_image_file(file_obj):
    upload_dir = current_app.config["UPLOAD_FOLDER"]
    os.makedirs(upload_dir, exist_ok=True)
    raw_name = getattr(file_obj, "filename", "") or ""
    clean_name = secure_filename(raw_name)
    ext = os.path.splitext(clean_name)[1].lower()
    if not ext:
        ext = os.path.splitext(raw_name)[1].lower()
    if not ext:
        mime = (getattr(file_obj, "mimetype", "") or "").lower()
        if "png" in mime:
            ext = ".png"
        elif "webp" in mime:
            ext = ".webp"
        elif "gif" in mime:
            ext = ".gif"
        else:
            ext = ".jpg"
    filename = f"sub_{uuid.uuid4().hex}{ext}"
    if hasattr(file_obj, "stream"):
        try:
            file_obj.stream.seek(0)
        except Exception:
            pass
    file_obj.save(os.path.join(upload_dir, filename))
    return filename


def _is_remote_image_ref(ref) -> bool:
    ref = (ref or "").strip()
    return ref.startswith("http://") or ref.startswith("https://")


def _delete_stored_image(ref) -> None:
    """Delete a stored image reference: Cloudinary asset or legacy local file."""
    ref = (ref or "").strip()
    if not ref:
        return
    if _is_remote_image_ref(ref):
        if "res.cloudinary.com" in ref and _cloudinary_enabled():
            try:
                import cloudinary.uploader
                m = re.search(r"/upload/(?:v\d+/)?(.+?)\.[A-Za-z0-9]+$", ref)
                if m:
                    cloudinary.uploader.destroy(m.group(1))
            except Exception:
                current_app.logger.exception("Cloudinary destroy failed for %s", ref)
        return
    upload_dir = current_app.config["UPLOAD_FOLDER"]
    fpath = os.path.join(upload_dir, os.path.basename(ref))
    if os.path.exists(fpath):
        try:
            os.remove(fpath)
        except OSError:
            pass


def _save_subdivision_image_file(file_obj):
    return _store_image_file(file_obj)


def _subdivision_images_to_list(subdivision):
    return list(subdivision.images or [])


def _set_subdivision_images(subdivision, filenames):
    subdivision.images = [name for name in filenames if name]


def _notify_all_agents(event_type: str, message: str, property_id=None) -> None:
    with db.session.no_autoflush:
        agent_ids = [row[0] for row in db.session.query(User.id).filter_by(role="agent", is_active=True).all()]
    for agent_id in agent_ids:
        db.session.add(AgentNotification(
            agent_id=agent_id,
            property_id=property_id,
            event_type=event_type,
            message=message,
        ))


def _get_config_map() -> dict:
    """Load SystemConfig once per request, cached on flask.g."""
    if not hasattr(g, "_sqh_config_cache"):
        g._sqh_config_cache = {
            r.key: r.value
            for r in SystemConfig.query.all()
        }
    return g._sqh_config_cache


def _get_system_config_float(key: str, default_value: float) -> float:
    raw = _get_config_map().get(key)
    if raw is None or str(raw).strip() == "":
        return float(default_value)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return float(default_value)


def _get_system_config_int(key: str, default_value: int) -> int:
    raw = _get_config_map().get(key)
    if raw is None or str(raw).strip() == "":
        return int(default_value)
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return int(default_value)


@main_bp.route("/api/reference/ph-banks", methods=["GET"])
@login_required
def api_reference_ph_banks():
    if current_user.role not in ("client", "agent", "admin"):
        return jsonify(ok=False, error="Forbidden"), 403

    local_banks = _load_local_ph_banks()
    if local_banks:
        return jsonify(ok=True, banks=local_banks)
    return jsonify(ok=False, error="Unable to load banks.json"), 500


def _get_live_meter_criteria() -> dict:
    """Fetch qualification criteria for client-side live DTI meter."""
    return {
        "dti_qualified_max": _get_system_config_float("dti_qualified_max", 30.0),
        "dti_conditional_max": _get_system_config_float("dti_conditional_max", 42.0),
        "min_tenure_months": _get_system_config_int("min_tenure_months", 6),
        "stability_scores": {
            "employed": _get_system_config_int("stability_employed", 5),
            "ofw-landbased": _get_system_config_int("stability_ofw_landbased", 4),
            "ofw-seafarer": _get_system_config_int("stability_ofw_seafarer", 4),
            "licensed-professional": _get_system_config_int("stability_licensed_professional", 5),
            "with-financial-support": _get_system_config_int("stability_with_financial_support", 3),
            "with-attorney-in-fact": _get_system_config_int("stability_with_attorney_in_fact", 3),
            "with-co-borrower": _get_system_config_int("stability_with_co_borrower", 4),
        },
    }


def _format_time_label(value: time | None) -> str:
    if not value:
        return ""
    return value.strftime("%I:%M %p").lstrip("0")


def _normalize_client_doc_kind(doc_kind: str | None) -> str | None:
    value = (doc_kind or "").strip().lower()
    return value if value in {"valid-id", "income-proof"} else None


def _resolve_client_doc(profile: UserProfile | None, doc_kind: str) -> tuple[bytes | None, str, str]:
    if not profile:
        return None, "application/octet-stream", "document"
    if doc_kind == "valid-id":
        return (
            profile.valid_id_data,
            profile.valid_id_mimetype or "application/octet-stream",
            profile.valid_id_filename or "valid-id",
        )
    return (
        profile.income_proof_data,
        profile.income_proof_mimetype or "application/octet-stream",
        profile.income_proof_filename or "proof-of-income",
    )


def _normalize_assessment_mode(value: str | None, fallback: str) -> str:
    return "new" if (value or "").strip().lower() == "new" else fallback


def _agent_can_view_client(agent_user_id: int, client_user_id: int) -> bool:
    my_prop_ids = [p.id for p in Property.query.filter_by(agent_id=agent_user_id).all()]
    if not my_prop_ids:
        return False
    connected = TrippingRequest.query.filter(
        TrippingRequest.client_id == client_user_id,
        TrippingRequest.property_id.in_(my_prop_ids),
    ).first()
    return connected is not None


def _build_agent_availability_summary(all_agents: list[User]) -> dict[int, dict]:
    today = datetime.now(timezone.utc).date()
    agent_ids = [a.id for a in all_agents]
    if not agent_ids:
        return {}

    rows = (AgentAvailability.query
            .filter(AgentAvailability.agent_id.in_(agent_ids),
                    AgentAvailability.available_date >= today)
            .order_by(AgentAvailability.available_date.asc(), AgentAvailability.start_time.asc())
            .all())

    by_agent: dict[int, list[AgentAvailability]] = {}
    for row in rows:
        by_agent.setdefault(int(row.agent_id), []).append(row)

    summary = {}
    for agent in all_agents:
        slots = by_agent.get(int(agent.id), [])
        if not slots:
            summary[int(agent.id)] = {
                "label": "No availability posted",
                "next_date": None,
                "next_range": None,
                "slot_count": 0,
            }
            continue
        first = slots[0]
        summary[int(agent.id)] = {
            "label": f"{first.available_date.strftime('%b %d')} • {_format_time_label(first.start_time)}-{_format_time_label(first.end_time)}",
            "next_date": first.available_date.strftime("%Y-%m-%d"),
            "next_range": f"{_format_time_label(first.start_time)}-{_format_time_label(first.end_time)}",
            "slot_count": len(slots),
        }
    return summary


def _resolve_pricing_inputs(overrides: dict | None = None, config_map: dict | None = None) -> dict:
    """Resolve pricing inputs from optional overrides, falling back to system config defaults."""
    overrides = overrides or {}
    if config_map is None:
        config_map = _get_config_map()

    def _num(key, default):
        raw = overrides.get(key)
        if raw in (None, ""):
            return float(default)
        try:
            return float(raw)
        except (TypeError, ValueError):
            return float(default)

    try:
        downpayment_terms_default = int(float(config_map.get("pricing_equity_months", 24))) or 24
    except (TypeError, ValueError):
        downpayment_terms_default = 24
    raw_terms = overrides.get("downpayment_terms_months")
    if raw_terms in (None, ""):
        downpayment_terms = int(downpayment_terms_default)
    else:
        try:
            downpayment_terms = int(float(raw_terms))
        except (TypeError, ValueError):
            downpayment_terms = int(downpayment_terms_default)

    def _cfg_float(key, default):
        raw = config_map.get(key)
        if raw is None or str(raw).strip() == "":
            return float(default)
        try:
            return float(raw)
        except (TypeError, ValueError):
            return float(default)

    return {
        "price": _num("price", 0.0),
        "promo_discount_rate": _num("promo_discount_rate", 0.0),
        "reservation_fee": _num("reservation_fee", 0.0),
        "downpayment_rate": _num("downpayment_rate", _cfg_float("pricing_dp_rate", 20.0)),
        "downpayment_terms_months": downpayment_terms,
        "loanable_percentage": _num("loanable_percentage", 80.0),
        "vat_rate": _num("vat_rate", 12.0),
        "lmf_rate": _num("lmf_rate", 10.0),
        "annual_interest_rate": _num("annual_interest_rate", _cfg_float("pricing_annual_interest_rate", 7.5)),
        "required_income_ratio": _num("required_income_ratio", _cfg_float("pricing_required_income_ratio", 30.0)),
    }


def _compute_pricing_values(values: dict) -> dict:
    total_selling_price = float(values.get("price") or 0)
    promo_discount_rate = float(values.get("promo_discount_rate") or 0)
    reservation_fee = float(values.get("reservation_fee") or 0)
    downpayment_rate = float(values.get("downpayment_rate") or 0)
    downpayment_terms = int(values.get("downpayment_terms_months") or 24)
    loanable_percentage = float(values.get("loanable_percentage") or 0)
    vat_rate = float(values.get("vat_rate") or 0)
    lmf_rate = float(values.get("lmf_rate") or 0)
    annual_interest_rate = float(values.get("annual_interest_rate") or 0)
    required_income_ratio = float(values.get("required_income_ratio") or 0)

    downpayment_terms = max(downpayment_terms, 1)
    required_income_ratio = max(required_income_ratio, 1.0)

    net_selling_price = total_selling_price * (1.0 - (promo_discount_rate / 100.0))
    vat_amount = net_selling_price * (vat_rate / 100.0)
    lmf_amount = net_selling_price * (lmf_rate / 100.0)
    total_contract_price = net_selling_price + vat_amount + lmf_amount

    total_downpayment = total_contract_price * (downpayment_rate / 100.0)
    monthly_downpayment = max(total_downpayment - reservation_fee, 0.0) / float(downpayment_terms)
    total_loanable_amount = total_contract_price * (loanable_percentage / 100.0)

    amortization = {}
    required_monthly_income = {}
    monthly_rate = annual_interest_rate / 100.0 / 12.0
    for years in (5, 10, 15, 20):
        n = years * 12
        if total_loanable_amount <= 0:
            monthly_payment = 0.0
        elif monthly_rate <= 0:
            monthly_payment = total_loanable_amount / n
        else:
            factor = (1 + monthly_rate) ** n
            monthly_payment = total_loanable_amount * monthly_rate * factor / (factor - 1)
        amortization[str(years)] = round(monthly_payment, 2)
        required_monthly_income[str(years)] = round(monthly_payment / (required_income_ratio / 100.0), 2)

    return {
        "total_selling_price": round(total_selling_price, 2),
        "promo_discount_rate": round(promo_discount_rate, 2),
        "net_selling_price": round(net_selling_price, 2),
        "vat_rate": round(vat_rate, 2),
        "vat_amount": round(vat_amount, 2),
        "lmf_rate": round(lmf_rate, 2),
        "lmf_amount": round(lmf_amount, 2),
        "total_contract_price": round(total_contract_price, 2),
        "reservation_fee": round(reservation_fee, 2),
        "downpayment_rate": round(downpayment_rate, 2),
        "total_downpayment": round(total_downpayment, 2),
        "downpayment_terms_months": downpayment_terms,
        "monthly_downpayment": round(monthly_downpayment, 2),
        "loanable_percentage": round(loanable_percentage, 2),
        "total_loanable_amount": round(total_loanable_amount, 2),
        "annual_interest_rate": round(annual_interest_rate, 2),
        "required_income_ratio": round(required_income_ratio, 2),
        "amortization": amortization,
        "required_monthly_income": required_monthly_income,
        # Legacy aliases retained for existing UI hooks.
        "tcp": round(total_selling_price, 2),
        "down_payment_rate": round(downpayment_rate, 2),
        "down_payment": round(total_downpayment, 2),
        "equity": round(total_loanable_amount, 2),
        "equity_months": downpayment_terms,
        "equity_monthly": round(monthly_downpayment, 2),
        "misc_rate": round(vat_rate + lmf_rate, 2),
        "misc_fees": round(vat_amount + lmf_amount, 2),
        "financed_amount": round(total_loanable_amount, 2),
        "fully_computed_house_price": round(total_contract_price, 2),
    }


def _compute_property_pricing(property_item: Property) -> dict:
    overrides = {
        "price": property_item.price,
        "promo_discount_rate": property_item.promo_discount_rate,
        "reservation_fee": property_item.reservation_fee,
        "downpayment_rate": property_item.downpayment_rate,
        "downpayment_terms_months": property_item.downpayment_terms_months,
        "loanable_percentage": property_item.loanable_percentage,
        "vat_rate": property_item.vat_rate,
        "lmf_rate": property_item.lmf_rate,
        "annual_interest_rate": property_item.annual_interest_rate,
    }
    return _compute_pricing_values(_resolve_pricing_inputs(overrides))


def _is_affordable_any_term(pricing: dict | None, client_income: float) -> bool:
    """True if client income qualifies (Qualified or Conditionally Qualified)
    for at least one loan term (5/10/15/20 yrs).

    Uses per-term required_monthly_income vs client gross income with the
    30/42 DTI threshold ratio (income >= req → Qualified;
    income >= req * qualified/conditional → Conditionally Qualified).
    Missing pricing/income never hides a card (fail-open).
    """
    if not pricing or client_income <= 0:
        return True
    req_map = pricing.get("required_monthly_income") or {}
    req_vals = []
    for year in ("5", "10", "15", "20"):
        try:
            val = float(req_map.get(year) or 0)
        except (TypeError, ValueError):
            val = 0
        if val > 0:
            req_vals.append(val)
    if not req_vals:
        return True
    try:
        qualified_max = _get_system_config_float("dti_qualified_max", 30.0)
        conditional_max = _get_system_config_float("dti_conditional_max", 42.0)
        ratio = (qualified_max / conditional_max) if conditional_max > 0 else (30.0 / 42.0)
    except Exception:
        ratio = 30.0 / 42.0
    for req in req_vals:
        if client_income >= req or client_income >= req * ratio:
            return True
    return False


def _is_qualified_5_year(pricing: dict | None, client_income: float) -> bool:
    """True if client income is fully qualified for the 5-year loan term
    (income >= required_monthly_income['5']).
    Being fully qualified for 5 years guarantees qualification across
    10, 15, and 20-year terms as well.
    """
    if not pricing or client_income <= 0:
        return False
    req_map = pricing.get("required_monthly_income") or {}
    try:
        req5 = float(req_map.get("5") or 0)
    except (TypeError, ValueError):
        req5 = 0.0
    if req5 <= 0:
        return False
    return client_income >= req5


@main_bp.route("/admin/property/pricing-preview", methods=["GET"])
@login_required
def admin_property_pricing_preview():
    if current_user.role != "admin":
        return jsonify(ok=False, error="Forbidden"), 403
    overrides = {}
    for key in (
        "price", "promo_discount_rate", "reservation_fee", "downpayment_rate",
        "downpayment_terms_months", "loanable_percentage", "vat_rate", "lmf_rate",
        "annual_interest_rate", "required_income_ratio",
    ):
        raw = _clean_numeric_str(request.args.get(key))
        if raw:
            overrides[key] = raw
    try:
        values = _resolve_pricing_inputs(overrides)
        pricing = _compute_pricing_values(values)
        return jsonify(ok=True, pricing=pricing)
    except Exception as exc:
        return jsonify(ok=False, error=f"Pricing preview failed: {exc}"), 400


@main_bp.route("/")
def index():
    if current_user.is_authenticated:
        from app.auth.routes import _dashboard_url
        return redirect(_dashboard_url(current_user.role))

    # Listings data for inline section.
    # One catalog load feeds everything below (filter dropdowns + card list);
    # the two former DISTINCT scans are derived in Python.
    all_catalog = (Property.query
                   .filter(db.func.lower(Property.status) == "available")
                   .filter(db.or_(Property.approval_status == "approved",
                                  Property.approval_status.is_(None)))
                   .options(selectinload(Property.subdivision))
                   .order_by(Property.created_at.desc())
                   .all())

    city_locations = sorted({(p.citymun_name or "").strip() for p in all_catalog if (p.citymun_name or "").strip()})
    legacy_locations = [
        loc for loc in (
            (p.location or "").split(",")[0].strip() if p.location else ""
            for p in all_catalog
        ) if loc
    ]
    all_locations = sorted(set(SUBDIVISIONS + city_locations + legacy_locations))

    location   = request.args.get("location", "").strip()
    max_budget = request.args.get("max_budget", "").strip()
    bedrooms   = request.args.get("bedrooms", "").strip()
    storeys    = request.args.get("storeys", "").strip()
    bathrooms  = request.args.get("bathrooms", "").strip()

    def _within_budget(p):
        if not max_budget:
            return True
        try:
            return p.price is not None and float(p.price) <= float(max_budget)
        except ValueError:
            return True

    def _int_filter(p_value, raw, special):
        if not raw:
            return True
        try:
            val = int(raw)
        except ValueError:
            return True
        if val >= special:
            return p_value is not None and int(p_value) >= val
        return p_value is not None and int(p_value) == val

    def _loc_match(p):
        if not location:
            return True
        needle = location.lower()
        return ((p.citymun_name and needle in p.citymun_name.lower())
                or (p.location and needle in p.location.lower()))

    properties = [
        p for p in all_catalog
        if _loc_match(p)
        and _within_budget(p)
        and _int_filter(p.bedrooms, bedrooms, 5)
        and _int_filter(p.storeys, storeys, 3)
        and _int_filter(p.bathrooms, bathrooms, 4)
    ]
    filters = dict(
        location=location, max_budget=max_budget,
        bedrooms=bedrooms, storeys=storeys, bathrooms=bathrooms,
    )

    return render_template(
        "index.html", title="Home",
        properties=properties,
        all_locations=all_locations,
        filters=filters,
    )


@main_bp.route("/api/psgc/regions")
def psgc_regions():
    try:
        return jsonify(ok=True, items=list_regions())
    except Exception as exc:
        return jsonify(ok=False, error=f"PSGC API unavailable: {exc}"), 503


@main_bp.route("/api/psgc/provinces")
def psgc_provinces():
    region_code = (request.args.get("region_code") or "").strip()
    if not region_code:
        return jsonify(ok=False, error="region_code is required"), 400
    try:
        return jsonify(ok=True, items=list_provinces(region_code))
    except Exception as exc:
        return jsonify(ok=False, error=f"PSGC API unavailable: {exc}"), 503


@main_bp.route("/api/psgc/cities")
def psgc_cities():
    province_code = (request.args.get("province_code") or "").strip()
    region_code = (request.args.get("region_code") or "").strip()
    if not province_code and not region_code:
        return jsonify(ok=False, error="province_code or region_code is required"), 400
    try:
        return jsonify(ok=True, items=list_cities(province_code=province_code or None, region_code=region_code or None))
    except Exception as exc:
        return jsonify(ok=False, error=f"PSGC API unavailable: {exc}"), 503


@main_bp.route("/api/psgc/barangays")
def psgc_barangays():
    city_mun_code = (request.args.get("city_mun_code") or "").strip()
    if not city_mun_code:
        return jsonify(ok=False, error="city_mun_code is required"), 400
    try:
        return jsonify(ok=True, items=list_barangays(city_mun_code))
    except Exception as exc:
        return jsonify(ok=False, error=f"PSGC API unavailable: {exc}"), 503


# ── Client Dashboard ──────────────────────────────────────────────────────────

@main_bp.route("/dashboard/client")
@login_required
def client_dashboard():
    if current_user.role not in ("client",):
        return redirect(url_for("main.index"))

    from ..client.forms import QualifyForm
    qualify_form = QualifyForm()

    profile = current_user.profile
    if profile:
        qualify_form.gross_monthly_income.data = profile.gross_income
        qualify_form.monthly_debt_loans.data   = profile.monthly_loans if profile.monthly_loans and profile.monthly_loans > 0 else None
        qualify_form.employment_status.data    = profile.employment_type or ""
        qualify_form.tenure_months.data        = profile.tenure_months
        qualify_form.age.data                  = profile.age
        qualify_form.dependents.data           = profile.dependents if profile.dependents is not None else 0
        qualify_form.budget_min.data           = profile.budget_min if profile.budget_min and profile.budget_min > 0 else None
        qualify_form.budget_max.data           = profile.budget_max if profile.budget_max and profile.budget_max > 0 else None

    # Latest and all qualification results
    all_results = (QualificationResult.query
                   .filter_by(user_id=current_user.id)
                   .order_by(QualificationResult.created_at.desc())
                   .all())
    qual_result = all_results[0] if all_results else None

    # All approved/available properties for browsing
    all_props = (Property.query
                 .filter(db.func.lower(Property.status) == "available")
                 .filter(db.or_(Property.approval_status == "approved",
                                Property.approval_status.is_(None)))
                 .options(
                     selectinload(Property.agent).selectinload(User.profile).options(
                         defer(UserProfile.avatar_data),
                         defer(UserProfile.banner_data),
                         defer(UserProfile.valid_id_data),
                         defer(UserProfile.income_proof_data),
                         defer(UserProfile.esignature_data),
                     ),
                     selectinload(Property.subdivision),
                 )
                 .order_by(Property.created_at.desc())
                 .all())

    model_name_choices = [
        ("", "Any model (optional)")
    ] + [
        (name, name)
        for name in sorted({(p.name or "").strip() for p in all_props if (p.name or "").strip()}, key=lambda x: x.lower())
    ]
    selected_model_name = (profile.preferred_type or "").strip() if profile and profile.preferred_type else ""
    if selected_model_name and not any(v == selected_model_name for v, _ in model_name_choices):
        model_name_choices.append((selected_model_name, selected_model_name))
    qualify_form.preferred_type.choices = model_name_choices
    qualify_form.preferred_type.data = selected_model_name
    # Agent contact details per property (for the Conditional Requirements modal).
    agent_contact_map = {}
    for _prop in all_props:
        _ag = _prop.agent if _prop else None
        if not _ag:
            continue
        _ap = _ag.profile
        agent_contact_map[int(_prop.id)] = {
            "name": _ag.full_name,
            "phone": (_ap.contact_no if _ap and _ap.contact_no else "") or (_ag.contact_number or ""),
            "instagram": (_ap.social_instagram if _ap else "") or "",
            "viber": (_ap.social_viber if _ap else "") or "",
            "whatsapp": (_ap.social_whatsapp if _ap else "") or "",
        }

    # Client's tripping requests
    my_trips = (TrippingRequest.query
                .filter_by(client_id=current_user.id)
                .order_by(TrippingRequest.created_at.desc())
                .all())

    # Backfill legacy trips that were submitted before purchase_form_submitted existed.
    # We infer submission from historical activity logs containing the trip id.
    buyer_submit_logs = (ActivityLog.query
                         .filter_by(actor_id=current_user.id, action="buyer_form_submit")
                         .order_by(ActivityLog.created_at.desc())
                         .all())
    log_trip_ts = {}
    for row in buyer_submit_logs:
        msg = str(row.description or "")
        m = re.search(r"Trip\s+#(\d+)", msg)
        if not m:
            continue
        tid = int(m.group(1))
        if tid not in log_trip_ts:
            log_trip_ts[tid] = row.created_at

    trip_backfilled = False
    for trip in my_trips:
        if bool(trip.purchase_form_submitted):
            continue
        inferred_ts = log_trip_ts.get(int(trip.id))
        if not inferred_ts:
            continue
        trip.purchase_form_submitted = True
        trip.purchase_form_submitted_at = inferred_ts
        trip_backfilled = True
    if trip_backfilled:
        db.session.commit()

    # Single load — sold_trip_ids derived from the same rows (removes a dup query).
    bought_sales = (PropertySale.query
                    .filter_by(client_id=current_user.id)
                    .options(
                        selectinload(PropertySale.property_item).selectinload(Property.subdivision),
                        selectinload(PropertySale.property_item).selectinload(Property.agent),
                        selectinload(PropertySale.trip_item),
                    )
                    .order_by(PropertySale.sold_at.desc())
                    .all())
    sold_trip_ids = {
        int(s.trip_id) for s in bought_sales
        if s.trip_id
    }

    # Only pending visit requests should block creating a new request in the UI.
    requested_prop_ids = sorted({
        int(t.property_id) for t in my_trips
        if t.property_id and t.status == "pending"
    })

    latest_qualification_status = qual_result.status if qual_result else None
    detail_requests = (PropertyPricingDetailRequest.query
                       .filter_by(client_id=current_user.id)
                       .all())
    detail_request_notifications = (PropertyPricingDetailRequestHistory.query
                                    .filter_by(client_id=current_user.id)
                                    .filter(PropertyPricingDetailRequestHistory.status.in_(["approved", "rejected"]))
                                    .order_by(PropertyPricingDetailRequestHistory.reviewed_at.desc(),
                                              PropertyPricingDetailRequestHistory.requested_at.desc(),
                                              PropertyPricingDetailRequestHistory.id.desc())
                                    .limit(60)
                                    .all())
    purchase_form_notifications = (AgentNotification.query
                                   .filter_by(agent_id=current_user.id)
                                   .filter(AgentNotification.event_type.in_(["purchase_form_rejected", "purchase_form_deleted"]))
                                   .order_by(AgentNotification.created_at.desc())
                                   .limit(60)
                                   .all())
    trip_status_notifications = (AgentNotification.query
                                 .filter_by(agent_id=current_user.id)
                                 .filter(AgentNotification.event_type.in_(["trip_approved_client"]))
                                 .order_by(AgentNotification.created_at.desc())
                                 .limit(60)
                                 .all())
    detail_request_status_by_property = {
        int(r.property_id): (r.status or "pending")
        for r in detail_requests
        if r.property_id
    }
    approved_detail_property_ids = sorted([
        int(r.property_id) for r in detail_requests
        if r.property_id and r.status == "approved"
    ])
    detail_request_note_by_property = {}
    for r in detail_requests:
        note_text = (r.agent_note or "").strip()
        if r.property_id and note_text:
            detail_request_note_by_property[int(r.property_id)] = note_text
    for h in detail_request_notifications:
        note_text = (h.agent_note or "").strip()
        if h.property_id and note_text and int(h.property_id) not in detail_request_note_by_property:
            detail_request_note_by_property[int(h.property_id)] = note_text
    pricing_props_by_id = {int(prop.id): prop for prop in all_props if prop and prop.id}
    for sale in bought_sales:
        prop = sale.property_item
        if prop and prop.id:
            pricing_props_by_id.setdefault(int(prop.id), prop)
    property_pricing_map = {
        prop_id: _compute_property_pricing(prop)
        for prop_id, prop in pricing_props_by_id.items()
    }
    try:
        _client_income_val = float(profile.gross_income) if (profile and profile.gross_income) else 0.0
    except (TypeError, ValueError):
        _client_income_val = 0.0

    # Qualified Models & Recommended Models computation
    qualified_props = []
    recommended_props = []

    if qual_result:
        max_loanable_val = float(qual_result.max_loanable) if (qual_result.max_loanable and float(qual_result.max_loanable) > 0) else None
        bmin = float(profile.budget_min) if (profile and profile.budget_min and float(profile.budget_min) > 0) else None
        bmax = float(profile.budget_max) if (profile and profile.budget_max and float(profile.budget_max) > 0) else None
        if bmin is not None and bmax is not None and bmin > bmax:
            bmin, bmax = bmax, bmin

        for p in all_props:
            price_val = float(p.price) if p.price is not None else 0.0

            # 1. Max loanable ceiling check
            if max_loanable_val is not None and price_val > max_loanable_val:
                continue

            pricing = property_pricing_map.get(int(p.id)) if p.id else None

            # 2. Qualified Models check: must be fully qualified in the 5-year loan term
            # (which automatically qualifies for 5, 10, 15, and 20-year terms as well).
            # If not qualified for 5 years, it will not appear in Qualified Models for You.
            if _client_income_val > 0 and _is_qualified_5_year(pricing, _client_income_val):
                qualified_props.append(p)

            # 3. Recommended filter: qualified in at least one loan term (e.g. 20-year term)
            # AND falls within Current Model Preferences budget range (min & max)
            if _client_income_val > 0 and not _is_affordable_any_term(pricing, _client_income_val):
                continue
            if bmin is not None and price_val < bmin:
                continue
            if bmax is not None and price_val > bmax:
                continue

            recommended_props.append(p)

        # Fallback for recommendations: if budget filter was too narrow and produced 0 recommendations,
        # recommend the properties affordable within loanable amount / income
        if not recommended_props and qualified_props:
            recommended_props = list(qualified_props)

        # Qualified models: sort by price ascending
        qualified_props.sort(key=lambda p: (float(p.price) if p.price is not None else 0.0))

        # Recommended models: prioritize preferred model if specified, then sort by price ascending
        if selected_model_name:
            recommended_props.sort(key=lambda p: (
                0 if (p.name or "").strip().lower() == selected_model_name.lower() else 1,
                float(p.price) if p.price is not None else 0.0
            ))
        else:
            recommended_props.sort(key=lambda p: (float(p.price) if p.price is not None else 0.0))

    # Keep matched_props for backward compatibility
    matched_props = recommended_props
    bought_pricing_map = {}
    for sale in bought_sales:
        prop = sale.property_item
        base_pricing = {}
        if prop and prop.id:
            try:
                base_pricing = dict(property_pricing_map.get(int(prop.id), {}) or {})
            except Exception:
                base_pricing = {}

        purchase_payload = {}
        trip = sale.trip_item
        if trip and (trip.purchase_form_data or "").strip():
            try:
                parsed_payload = json.loads(trip.purchase_form_data)
                if isinstance(parsed_payload, dict):
                    purchase_payload = parsed_payload
            except Exception:
                purchase_payload = {}

        def _first_non_blank(*keys):
            for key in keys:
                if key not in purchase_payload:
                    continue
                value = purchase_payload.get(key)
                if value is None:
                    continue
                text = str(value).strip()
                if text:
                    return text
            return ""

        base_pricing["loanProcessingFee"] = _first_non_blank("loanProcessingFee", "loan_processing_fee")
        base_pricing["loanOrPrNo"] = _first_non_blank("loanOrPrNo", "loan_or_pr_no")
        base_pricing["loanOrPrDate"] = _first_non_blank("loanOrPrDate", "loan_or_pr_date")
        base_pricing["loanDownpaymentTerm"] = _first_non_blank("loanDownpaymentTerm", "loan_downpayment_term")
        base_pricing["loanTerm"] = _first_non_blank("loanTerm", "loan_term")

        bought_pricing_map[int(sale.id)] = base_pricing

    buyer_bank_options = _load_local_ph_banks()
    country_options = _load_countries()
    live_meter_cfg = _get_live_meter_criteria()

    return render_template(
        "dashboard/client.html",
        title="My Dashboard",
        profile=profile,
        qual_result=qual_result,
        all_results=all_results,
        all_props=all_props,
        qualified_props=qualified_props,
        recommended_props=recommended_props,
        matched_props=matched_props,
        my_trips=my_trips,
        sold_trip_ids=sold_trip_ids,
        bought_sales=bought_sales,
        bought_pricing_map=bought_pricing_map,
        requested_prop_ids=requested_prop_ids,
        assessment_count=len(all_results),
        trips_count=len(my_trips),
        matched_count=len(qualified_props),
        latest_qualification_status=latest_qualification_status,
        detail_request_notifications=detail_request_notifications,
        purchase_form_notifications=purchase_form_notifications,
        trip_status_notifications=trip_status_notifications,
        detail_request_status_by_property=detail_request_status_by_property,
        approved_detail_property_ids=approved_detail_property_ids,
        detail_request_note_by_property=detail_request_note_by_property,
        property_pricing_map=property_pricing_map,
        agent_contact_map=agent_contact_map,
        buyer_bank_options=buyer_bank_options,
        country_options=country_options,
        live_meter_cfg=live_meter_cfg,
        qualify_form=qualify_form,
        avatar_url=url_for("main.serve_client_avatar", user_id=current_user.id) if profile and profile.avatar_data else None,
        banner_url=url_for("main.serve_client_banner", user_id=current_user.id) if profile and profile.banner_data else None,
    )


# ── Agent Dashboard ───────────────────────────────────────────────────────────

@main_bp.route("/dashboard/agent")
@login_required
def agent_dashboard():
    if current_user.role not in ("agent", "admin"):
        return redirect(url_for("main.index"))

    my_props_q = (Property.query
                  .filter(db.func.lower(Property.status) == "available")
                  .filter(db.or_(Property.approval_status == "approved", Property.approval_status.is_(None))))
    # Agents now see all published models per 09_02_2026 feedback; admins keep full view.
    # (Removed agent_id filter so agent listings show all approved models, not just own.)
    my_props = (my_props_q
                .options(
                    selectinload(Property.sale_record).selectinload(PropertySale.client),
                    selectinload(Property.subdivision).selectinload(Subdivision.project),
                )
                .order_by(Property.created_at.desc())
                .all())
    prop_ids = [p.id for p in my_props]

    if prop_ids:
        my_trips = (TrippingRequest.query
                    .join(Property, Property.id == TrippingRequest.property_id)
                    .filter(Property.agent_id == current_user.id,
                    TrippingRequest.status.in_(["pending", "approved", "visited", "rejected"]))
                    .options(
                        selectinload(TrippingRequest.client),
                        selectinload(TrippingRequest.property_item),
                    )
                    .order_by(TrippingRequest.created_at.desc())
                    .all())
    else:
        my_trips = []

    pending_trips_count   = sum(1 for t in my_trips if t.status == "pending")
    active_listings_count = sum(1 for p in my_props if p.status == "available")

    agent_property_pricing_map = {
        int(p.id): _compute_property_pricing(p)
        for p in my_props if p and p.id
    }

    all_subdivisions = Subdivision.query.order_by(Subdivision.name).all()

    def _norm_name(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", "", (value or "").strip().lower())

    sub_name_set = {_norm_name(s.name) for s in all_subdivisions if (s.name or "").strip()}
    all_projects_raw = Project.query.order_by(Project.name).all()
    all_projects = [p for p in all_projects_raw if _norm_name(p.name) not in sub_name_set]

    agent_info       = current_user.profile

    avatar_url = (url_for("main.serve_agent_avatar", user_id=agent_info.user_id)
                  if agent_info and agent_info.avatar_data else None)
    banner_url = (url_for("main.serve_agent_banner", user_id=agent_info.user_id)
                  if agent_info and agent_info.banner_data else None)
    sold_trip_ids = {
        int(s.trip_id) for s in PropertySale.query.filter(
            PropertySale.trip_id.isnot(None),
            PropertySale.property_id.in_(prop_ids if prop_ids else [-1])
        ).all()
    }
    sold_sales = (PropertySale.query
                  .filter_by(agent_id=current_user.id)
                  .order_by(PropertySale.sold_at.desc())
                  .all())
    agent_prop_notifs = (AgentNotification.query
                         .filter(AgentNotification.agent_id == current_user.id,
                     AgentNotification.event_type.in_(["trip_assignment", "trip_sold", "trip_visited"]))
                         .order_by(AgentNotification.created_at.desc())
                         .limit(20)
                         .all())
    unread_trip_notif_count = 0
    pending_pricing_requests = (PropertyPricingDetailRequest.query
                                .join(Property, Property.id == PropertyPricingDetailRequest.property_id)
                                .filter(Property.agent_id == current_user.id,
                                        PropertyPricingDetailRequest.status == "pending")
                                .order_by(PropertyPricingDetailRequest.created_at.desc())
                                .limit(12)
                                .all())
    pricing_request_pending_count_by_property = {}
    pricing_request_total_count_by_property = {}
    if prop_ids:
        pending_rows = (db.session.query(PropertyPricingDetailRequest.property_id, db.func.count(PropertyPricingDetailRequest.id))
                        .join(Property, Property.id == PropertyPricingDetailRequest.property_id)
                        .filter(Property.agent_id == current_user.id,
                                PropertyPricingDetailRequest.status == "pending")
                        .group_by(PropertyPricingDetailRequest.property_id)
                        .all())
        total_rows = (db.session.query(PropertyPricingDetailRequest.property_id, db.func.count(PropertyPricingDetailRequest.id))
                      .join(Property, Property.id == PropertyPricingDetailRequest.property_id)
                      .filter(Property.agent_id == current_user.id)
                      .group_by(PropertyPricingDetailRequest.property_id)
                      .all())
        pricing_request_pending_count_by_property = {int(pid): int(cnt) for pid, cnt in pending_rows if pid}
        pricing_request_total_count_by_property = {int(pid): int(cnt) for pid, cnt in total_rows if pid}

    all_agents = (User.query
                  .filter_by(role="agent", is_active=True)
                  .order_by(User.first_name.asc(), User.last_name.asc())
                  .all())
    my_availability = (AgentAvailability.query
                       .filter_by(agent_id=current_user.id)
                       .order_by(AgentAvailability.available_date.asc(), AgentAvailability.start_time.asc())
                       .all())
    unread_agent_prop_notif_count = sum(1 for n in agent_prop_notifs if not n.is_read)
    unread_notif_count = unread_agent_prop_notif_count

    today = datetime.now(timezone.utc).date()

    def _format_trip_time(value):
        if not value:
            return "—"
        if isinstance(value, time):
            return value.strftime("%I:%M %p")
        if isinstance(value, datetime):
            return value.strftime("%I:%M %p")
        txt = str(value).strip()
        for fmt in ("%H:%M:%S", "%H:%M"):
            try:
                return datetime.strptime(txt, fmt).strftime("%I:%M %p")
            except ValueError:
                continue
        return txt

    assignment_feed_items = []
    assigned_trip_candidates = [t for t in my_trips if t.status == "approved"]
    assigned_trip_candidates.sort(key=lambda t: (t.preferred_date or today, t.created_at or datetime.now(timezone.utc)))
    for t in assigned_trip_candidates[:6]:
        assignment_feed_items.append({
            "client_name": t.client.full_name if t.client else "Client",
            "property_name": t.property_item.name if t.property_item else "Property",
            "preferred_date": t.preferred_date.strftime("%b %d, %Y") if t.preferred_date else "—",
            "preferred_time": _format_trip_time(t.preferred_time),
            "status": (t.status or "pending").capitalize(),
            "created_at": t.created_at,
            "page": "trips",
        })

    sold_this_month = [
        s for s in sold_sales
        if s.sold_at and s.sold_at.year == today.year and s.sold_at.month == today.month
    ]
    total_sales_value_month = sum(float(s.selling_price or 0) for s in sold_this_month)
    avg_sale_value = (total_sales_value_month / len(sold_this_month)) if sold_this_month else 0
    best_sale_value = max((float(s.selling_price or 0) for s in sold_this_month), default=0)
    total_trip_volume = len(my_trips)
    close_rate = ((len(sold_sales) / total_trip_volume) * 100.0) if total_trip_volume else 0.0
    last_sale = sold_sales[0] if sold_sales else None

    sales_snapshot = {
        "month_label": today.strftime("%B %Y"),
        "closed_count": len(sold_this_month),
        "sales_value": total_sales_value_month,
        "avg_sale": avg_sale_value,
        "best_sale": best_sale_value,
        "lifetime_closed": len(sold_sales),
        "close_rate": close_rate,
        "trip_volume": total_trip_volume,
        "last_sale_date": last_sale.sold_at.strftime("%b %d, %Y") if last_sale and last_sale.sold_at else "—",
    }

    return render_template(
        "dashboard/agent.html",
        title="Agent Dashboard",
        my_props=my_props,
        my_trips=my_trips,
        agent_prop_notifs=agent_prop_notifs,
        pending_pricing_requests=pending_pricing_requests,
        pricing_request_pending_count_by_property=pricing_request_pending_count_by_property,
        pricing_request_total_count_by_property=pricing_request_total_count_by_property,
        sold_trip_ids=sold_trip_ids,
        sold_sales=sold_sales,
        all_agents=all_agents,
        my_availability=my_availability,
        pending_trips_count=pending_trips_count,
        active_listings_count=active_listings_count,
        unread_trip_notif_count=unread_trip_notif_count,
        unread_agent_prop_notif_count=unread_agent_prop_notif_count,
        unread_notif_count=unread_notif_count,
        assignment_feed_items=assignment_feed_items,
        sales_snapshot=sales_snapshot,
        all_projects=all_projects,
        all_subdivisions=all_subdivisions,
        agent_property_pricing_map=agent_property_pricing_map,
        agent_info=agent_info,
        avatar_url=avatar_url,
        banner_url=banner_url,
    )


# ── Agent: mark trip notification as read ─────────────────────────────────────

@main_bp.route("/agent/notif/<int:trip_id>/read", methods=["POST"])
@login_required
def agent_notif_read(trip_id):
    from flask import jsonify
    if current_user.role not in ("agent", "admin"):
        return jsonify(ok=False), 403
    trip = db.session.get(TrippingRequest, trip_id)
    if not trip:
        return jsonify(ok=False), 404
    prop_ids = {p.id for p in Property.query.filter_by(agent_id=current_user.id).all()}
    if trip.property_id not in prop_ids or (trip.status or "").lower() != "approved":
        return jsonify(ok=False), 403
    trip.notification_read = True
    db.session.commit()
    return jsonify(ok=True)


@main_bp.route("/agent/property-notif/<int:notif_id>/read", methods=["POST"])
@login_required
def agent_property_notif_read(notif_id):
    if current_user.role not in ("agent", "admin"):
        return jsonify(ok=False), 403
    notif = db.session.get(AgentNotification, notif_id)
    if not notif:
        return jsonify(ok=False), 404
    if notif.agent_id != current_user.id:
        return jsonify(ok=False), 403
    notif.is_read = True
    db.session.commit()
    return jsonify(ok=True)


@main_bp.route("/agent/notif/read-all", methods=["POST"])
@login_required
def agent_notif_read_all():
    if current_user.role not in ("agent", "admin"):
        return jsonify(ok=False), 403
    prop_ids = [p.id for p in Property.query.filter_by(agent_id=current_user.id).all()]
    trip_marked = 0
    if prop_ids:
        trip_marked = (TrippingRequest.query
                       .filter(TrippingRequest.property_id.in_(prop_ids),
                               TrippingRequest.status == "approved",
                               TrippingRequest.notification_read.is_(False))
                       .update({TrippingRequest.notification_read: True}, synchronize_session=False))
    prop_marked = (AgentNotification.query
                   .filter_by(agent_id=current_user.id, is_read=False)
                   .update({AgentNotification.is_read: True}, synchronize_session=False))
    db.session.commit()
    return jsonify(ok=True, trip_marked=trip_marked, prop_marked=prop_marked)


# ── Admin: dismiss notification ───────────────────────────────────────────────

@main_bp.route("/admin/notif/dismiss", methods=["POST"])
@login_required
def admin_notif_dismiss():
    if current_user.role != "admin":
        return jsonify(ok=False), 403
    data = request.get_json(silent=True) or {}
    dismissed_prop_ids = set(current_user.get_admin_dismissed_property_notifs())
    dismissed_asmnt_ids = set(current_user.get_admin_dismissed_assessment_notifs())
    dismissed_sale_ids = set(current_user.get_admin_dismissed_sale_notifs())
    if data.get("dismiss_all"):
        prop_ids  = [p.id for p in Property.query.filter_by(approval_status="pending").all()]
        asmnt_ids = [r.id for r in QualificationResult.query.order_by(
                         QualificationResult.created_at.desc()).limit(5).all()]
        sale_ids = [s.id for s in PropertySale.query.order_by(PropertySale.sold_at.desc()).limit(8).all()]
        dismissed_prop_ids.update(prop_ids)
        dismissed_asmnt_ids.update(asmnt_ids)
        dismissed_sale_ids.update(sale_ids)
        (AgentNotification.query
         .filter_by(agent_id=current_user.id, event_type="buyer_form_submit", is_read=False)
         .update({AgentNotification.is_read: True}, synchronize_session=False))
    else:
        notif_type = data.get("type")
        notif_id   = data.get("id")
        if notif_type == "property" and notif_id:
            dismissed_prop_ids.add(int(notif_id))
        elif notif_type == "assessment" and notif_id:
            dismissed_asmnt_ids.add(int(notif_id))
        elif notif_type == "sale" and notif_id:
            dismissed_sale_ids.add(int(notif_id))
        elif notif_type == "buyer-form" and notif_id:
            notif_row = db.session.get(AgentNotification, int(notif_id))
            if notif_row and notif_row.agent_id == current_user.id:
                notif_row.is_read = True
    current_user.set_admin_dismissed_property_notifs(list(dismissed_prop_ids))
    current_user.set_admin_dismissed_assessment_notifs(list(dismissed_asmnt_ids))
    current_user.set_admin_dismissed_sale_notifs(list(dismissed_sale_ids))
    db.session.commit()
    return jsonify(ok=True)


@main_bp.route("/admin/activity/<int:log_id>/delete", methods=["POST"])
@login_required
def admin_delete_activity_log(log_id):
    if current_user.role != "admin":
        return jsonify(ok=False), 403
    row = db.session.get(ActivityLog, log_id)
    if not row:
        return jsonify(ok=False, error="Activity log not found."), 404
    db.session.delete(row)
    db.session.commit()
    return jsonify(ok=True, id=log_id)


# ── Admin Dashboard ───────────────────────────────────────────────────────────

@main_bp.route("/dashboard/admin")
@login_required
def admin_dashboard():
    if current_user.role != "admin":
        return redirect(url_for("main.index"))

    # Full lists for sub-pages (loaded once, reused for stats/recents below)
    all_properties = (Property.query
                      .options(
                          selectinload(Property.subdivision).selectinload(Subdivision.project),
                          selectinload(Property.sale_record).selectinload(PropertySale.client),
                          selectinload(Property.agent),
                      )
                      .order_by(Property.created_at.desc())
                      .all())
    all_clients    = User.query.filter_by(role="client").order_by(User.created_at.desc()).all()
    all_agents     = User.query.filter_by(role="agent").order_by(User.created_at.desc()).all()
    # Pricing map for admin model detail breakdown (mirrors agent/client)
    try:
        property_pricing_map = {int(p.id): _compute_property_pricing(p) for p in all_properties if p and p.id}
    except Exception:
        property_pricing_map = {}

    # Overview stats — derived from the lists above instead of extra COUNT queries
    total_users    = len(all_clients) + len(all_agents)
    total_clients  = len(all_clients)
    total_agents   = len(all_agents)
    total_props    = len(all_properties)
    total_sold     = PropertySale.query.count()
    today_start = datetime.combine(date.today(), time.min)
    today_end = today_start + timedelta(days=1)
    assessments_today = QualificationResult.query.filter(
        QualificationResult.created_at >= today_start,
        QualificationResult.created_at < today_end,
    ).count()
    recent_users = User.query.filter(User.role != "admin").order_by(User.created_at.desc()).limit(5).all()
    recent_props = Property.query.order_by(Property.created_at.desc()).limit(5).all()

    agent_availability_summary = _build_agent_availability_summary(all_agents)
    def _normalize_trip_status(value):
        s = (value or "").strip().lower()
        if s in {"pending", "approved", "visited", "rejected", "sold"}:
            return s
        return "pending"

    all_trip_requests = (TrippingRequest.query
                         .options(
                             selectinload(TrippingRequest.client),
                             selectinload(TrippingRequest.property_item).selectinload(Property.agent),
                             selectinload(TrippingRequest.sale_record),
                         )
                         .order_by(TrippingRequest.created_at.desc())
                         .all())
    pending_trip_requests = [t for t in all_trip_requests if _normalize_trip_status(t.status) == "pending"]
    pending_trip_count = len(pending_trip_requests)

    pending_details_by_prop = dict(
        db.session.query(
            PropertyPricingDetailRequest.property_id,
            db.func.count(PropertyPricingDetailRequest.id)
        )
        .filter(PropertyPricingDetailRequest.status == "pending")
        .group_by(PropertyPricingDetailRequest.property_id)
        .all()
    )
    pending_detail_request_count = sum(pending_details_by_prop.values())

    pending_purchases_by_prop = dict(
        db.session.query(
            TrippingRequest.property_id,
            db.func.count(TrippingRequest.id)
        )
        .outerjoin(PropertySale, PropertySale.trip_id == TrippingRequest.id)
        .filter(
            TrippingRequest.purchase_form_submitted.is_(True),
            PropertySale.id.is_(None)
        )
        .group_by(TrippingRequest.property_id)
        .all()
    )
    pending_purchases_total = sum(pending_purchases_by_prop.values())

    purchase_candidates_by_prop = dict(
        db.session.query(
            TrippingRequest.property_id,
            db.func.count(TrippingRequest.id)
        )
        .filter(
            (TrippingRequest.status == "visited") |
            (TrippingRequest.purchase_form_submitted.is_(True))
        )
        .group_by(TrippingRequest.property_id)
        .all()
    )
    total_models_badge_count = pending_detail_request_count + pending_purchases_total
    default_annual_interest_rate = _get_system_config_float("pricing_annual_interest_rate", 7.5)

    trip_dates = sorted({t.preferred_date for t in pending_trip_requests if t.preferred_date})
    available_agent_ids_by_date = {}
    if trip_dates:
        avail_rows = (AgentAvailability.query
                      .filter(AgentAvailability.available_date.in_(trip_dates),
                              AgentAvailability.availability_status == "available")
                      .all())
        for row in avail_rows:
            key = row.available_date.isoformat()
            if key not in available_agent_ids_by_date:
                available_agent_ids_by_date[key] = set()
            available_agent_ids_by_date[key].add(int(row.agent_id))

    available_agents_by_trip = {}
    for trip in pending_trip_requests:
        key = trip.preferred_date.isoformat() if trip.preferred_date else ""
        available_ids = available_agent_ids_by_date.get(key, set())
        options = []
        for ag in all_agents:
            if int(ag.id) in available_ids and ag.is_active:
                options.append({"id": ag.id, "name": ag.full_name})
        available_agents_by_trip[int(trip.id)] = options
    all_results    = (QualificationResult.query
                      .options(selectinload(QualificationResult.user))
                      .order_by(QualificationResult.created_at.desc())
                      .all())
    # Derive per-status counts and the recents list from all_results
    # (identical data — removes 4 extra queries).
    qual_counts = dict(
        qualified    = sum(1 for r in all_results if r.status == "Qualified"),
        conditional  = sum(1 for r in all_results if r.status == "Conditionally Qualified"),
        not_qualified= sum(1 for r in all_results if r.status == "Not Qualified"),
    )
    recent_assessments = all_results[:5]
    recent_trip_requests = all_trip_requests[:20]
    all_subdivisions = Subdivision.query.order_by(Subdivision.name).all()
    def _norm_name(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", "", (value or "").strip().lower())

    sub_name_set = {_norm_name(s.name) for s in all_subdivisions if (s.name or "").strip()}
    all_projects_raw = Project.query.order_by(Project.name).all()
    all_projects = [p for p in all_projects_raw if _norm_name(p.name) not in sub_name_set]
    all_historical   = HistoricalBuyer.query.order_by(HistoricalBuyer.id.desc()).all()
    all_historical_records = HistoricalBuyerRecord.query.order_by(HistoricalBuyerRecord.created_at.desc()).all()
    model_meta       = c50_engine.get_meta()
    # Request-cached SystemConfig — synthesized into row-like objects so the
    # template's selectattr('key', …) filtering keeps working without a query.
    config_map = _get_config_map()
    ConfigRow = namedtuple("ConfigRow", ["key", "value"])
    criteria_config  = [ConfigRow(k, v) for k, v in sorted(config_map.items())]
    settings_dict    = dict(config_map)
    sub_counts = dict(
        db.session.query(Subdivision.project_id, db.func.count(Subdivision.id))
        .group_by(Subdivision.project_id)
        .all()
    )
    try:
        activity_logs = ActivityLog.query.order_by(ActivityLog.created_at.desc()).limit(1000).all()
    except Exception:
        activity_logs = []

    # Notifications data — merge and sort all notification types by latest timestamp.
    dismissed_prop_ids  = set(current_user.get_admin_dismissed_property_notifs())
    dismissed_asmnt_ids = set(current_user.get_admin_dismissed_assessment_notifs())
    dismissed_sale_ids  = set(current_user.get_admin_dismissed_sale_notifs())
    pending_props       = Property.query.filter_by(approval_status="pending").order_by(Property.created_at.desc()).all()
    recent_sales        = PropertySale.query.order_by(PropertySale.sold_at.desc()).limit(8).all()
    recent_buyer_form_submissions = (AgentNotification.query
                                     .filter_by(agent_id=current_user.id, event_type="buyer_form_submit")
                                     .order_by(AgentNotification.created_at.desc())
                                     .limit(20)
                                     .all())
    recent_detail_requests = (PropertyPricingDetailRequestHistory.query
                              .order_by(PropertyPricingDetailRequestHistory.reviewed_at.is_(None),
                                        PropertyPricingDetailRequestHistory.reviewed_at.desc(),
                                        PropertyPricingDetailRequestHistory.requested_at.desc(),
                                        PropertyPricingDetailRequestHistory.id.desc())
                              .limit(20)
                              .all())

    admin_notifications = []
    for p in pending_props:
        admin_notifications.append({
            "kind": "property",
            "id": p.id,
            "ts": p.created_at,
            "read": p.id in dismissed_prop_ids,
            "item": p,
        })
    for r in recent_assessments:
        admin_notifications.append({
            "kind": "assessment",
            "id": r.id,
            "ts": r.created_at,
            "read": r.id in dismissed_asmnt_ids,
            "item": r,
        })
    for s in recent_sales:
        admin_notifications.append({
            "kind": "sale",
            "id": s.id,
            "ts": s.sold_at,
            "read": s.id in dismissed_sale_ids,
            "item": s,
        })
    for t in recent_trip_requests:
        admin_notifications.append({
            "kind": "trip",
            "id": t.id,
            "ts": t.created_at,
            "read": False,
            "item": t,
        })
    for bf in recent_buyer_form_submissions:
        admin_notifications.append({
            "kind": "buyer_form",
            "id": bf.id,
            "ts": bf.created_at,
            "read": bool(bf.is_read),
            "item": bf,
        })
    for d in recent_detail_requests:
        admin_notifications.append({
            "kind": "detail",
            "id": d.id,
            "ts": d.requested_at,
            "read": False,
            "item": d,
        })
    admin_notifications.sort(
        key=lambda n: (n["ts"].timestamp() if n.get("ts") else 0),
        reverse=True,
    )

    notif_count = sum(1 for n in admin_notifications if not n["read"])

    return render_template("dashboard/admin.html", title="Admin Dashboard",
                           total_users=total_users, total_agents=total_agents,
                           total_props=total_props, total_sold=total_sold,
                           assessments_today=assessments_today,
                           qual_counts=qual_counts, recent_users=recent_users,
                           recent_props=recent_props, total_clients=total_clients,
                           all_properties=all_properties, all_clients=all_clients,
                           all_agents=all_agents, all_results=all_results,
                           agent_availability_summary=agent_availability_summary,
                           pending_trip_requests=all_trip_requests,
                           pending_trip_count=pending_trip_count,
                           pending_detail_request_count=pending_detail_request_count,
                           pending_details_by_prop=pending_details_by_prop,
                           pending_purchases_by_prop=pending_purchases_by_prop,
                           purchase_candidates_by_prop=purchase_candidates_by_prop,
                           total_models_badge_count=total_models_badge_count,
                           default_annual_interest_rate=default_annual_interest_rate,
                           available_agents_by_trip=available_agents_by_trip,
                           activity_logs=activity_logs,
                           pending_props=pending_props,
                           recent_assessments=recent_assessments,
                           recent_sales=recent_sales,
                           admin_notifications=admin_notifications,
                           dismissed_prop_ids=dismissed_prop_ids,
                           dismissed_asmnt_ids=dismissed_asmnt_ids,
                           dismissed_sale_ids=dismissed_sale_ids,
                           notif_count=notif_count,
                           property_pricing_map=property_pricing_map,
                            all_projects=all_projects,
                            all_subdivisions=all_subdivisions,
                            sub_counts=sub_counts,
                           all_historical=all_historical,
                           all_historical_records=all_historical_records,
                           model_meta=model_meta,
                           criteria_config=criteria_config,
                           settings_dict=settings_dict,
                           avatar_url=(url_for("main.serve_admin_avatar", user_id=current_user.id)
                                       if current_user.profile and current_user.profile.avatar_data else None),
                           banner_url=(url_for("main.serve_admin_banner", user_id=current_user.id)
                                       if current_user.profile and current_user.profile.banner_data else None))

# ── Admin: toggle user active status ───────────────────────────────────────────────

@main_bp.route("/admin/user/<int:user_id>/toggle", methods=["POST"])
@login_required
def admin_toggle_user(user_id):
    if current_user.role != "admin":
        return jsonify({"error": "Forbidden"}), 403
    user = db.session.get(User, user_id)
    if not user:
        return jsonify({"error": "Not found"}), 404
    if user.role == "admin":
        return jsonify({"error": "Cannot modify admin accounts"}), 400
    user.is_active = not user.is_active
    action_desc = "activated" if user.is_active else "deactivated"
    log_activity("user_toggle", f"Account {action_desc}: {user.full_name} ({user.role})")
    db.session.commit()
    return jsonify({"is_active": user.is_active, "user_id": user.id})


# ── Admin: full user profile (JSON) ────────────────────────────────────────────────

@main_bp.route("/admin/user/<int:user_id>/profile")
@login_required
def admin_user_profile(user_id):
    if current_user.role != "admin":
        return jsonify({"error": "Forbidden"}), 403
    user = db.session.get(User, user_id)
    if not user:
        return jsonify({"error": "Not found"}), 404

    data = {
        "id":             user.id,
        "first_name":     user.first_name,
        "middle_name":    user.middle_name,
        "last_name":      user.last_name,
        "full_name":      user.full_name,
        "initials":       user.full_name[:2].upper(),
        "email":          user.email,
        "role":           user.role,
        "username":       user.username or "—",
        "contact_number": user.contact_number or "—",
        "is_active":      user.is_active,
        "joined_at":      user.created_at.strftime("%b %d, %Y") if user.created_at else "—",
        "joined":         user.created_at.strftime("%b %d, %Y"),
    }

    if user.role == "client":
        cp = user.profile
        latest_result = (QualificationResult.query
                         .filter_by(user_id=user.id)
                         .order_by(QualificationResult.created_at.desc())
                         .first())
        if cp:
            data["profile"] = {
                "civil_status":       (cp.civil_status or "—").replace("-", " ").title(),
                "citizenship":        (cp.citizenship or "—").replace("-", " ").title(),
                "gender":             (cp.gender or "—").replace("-", " ").title(),
                "dependents":         cp.dependents if cp.dependents is not None else "—",
                "birth_date":         cp.birth_date.strftime("%b %d, %Y") if cp.birth_date else "—",
                "birthplace":         cp.birthplace or "—",
                "age":                cp.age or "—",
                "address":            cp.address or "—",
                "address_line":       cp.address_line or "—",
                "street":             cp.street or "—",
                "blk":                cp.blk or "—",
                "lot":                cp.lot or "—",
                "subdivision_name":   cp.subdivision_name or "—",
                "home_region_name":   cp.home_region_name or "—",
                "home_province_name": cp.home_province_name or "—",
                "home_citymun_name":  cp.home_citymun_name or "—",
                "home_barangay_name": cp.home_barangay_name or "—",
                "country":            cp.country or "—",
                "zip_code":           cp.zip_code or "—",
                "employment_type":    (cp.employment_type or "—").replace("-", " ").title(),
                "employer_name":      cp.employer_name or "—",
                "employer_phone":     cp.employer_phone or "—",
                "employer_email":     cp.employer_email or "—",
                "employer_business_address": cp.employer_business_address or "—",
                "sss_gsis_umid":      cp.sss_gsis_umid or "—",
                "tin_no":             cp.tin_no or "—",
                "tenure_months":      cp.tenure_months if cp.tenure_months is not None else "—",
                "gross_income":       f"₱{float(cp.gross_income):,.2f}" if cp.gross_income else "—",
                "monthly_loans":      f"₱{float(cp.monthly_loans):,.2f}" if cp.monthly_loans else "₱0.00",
                "other_deductions":   f"₱{float(cp.other_deductions):,.2f}" if cp.other_deductions else "₱0.00",
                "preferred_type":     (cp.preferred_type or "—").replace("-", " ").title(),
                "budget_min":         f"₱{float(cp.budget_min):,.0f}" if cp.budget_min else "—",
                "budget_max":         f"₱{float(cp.budget_max):,.0f}" if cp.budget_max else "—",
                "social_instagram":   cp.social_instagram or "—",
                "social_twitter_x":   cp.social_twitter_x or "—",
                "social_viber":       cp.social_viber or "—",
                "social_whatsapp":    cp.social_whatsapp or "—",
            }
            data["avatar_url"] = (
                url_for("main.serve_client_avatar", user_id=user.id)
                if cp.avatar_data else None
            )
        else:
            data["profile"] = None
            data["avatar_url"] = None

        data["assessment"] = {
            "date": latest_result.created_at.strftime("%b %d, %Y") if latest_result and latest_result.created_at else "—",
            "status": latest_result.status if latest_result else "—",
            "dti": f"{latest_result.dti_ratio:.1f}%" if latest_result and latest_result.dti_ratio is not None else "—",
            "max_loanable": f"₱{float(latest_result.max_loanable):,.0f}" if latest_result and latest_result.max_loanable else "—",
            "similarity": ("High" if latest_result.similarity_score >= 0.70 else ("Moderate" if latest_result.similarity_score >= 0.40 else "Low")) if latest_result and latest_result.similarity_score is not None else "—",
        } if latest_result else None

        assessment_rows = sorted(user.qualification_results, key=lambda x: x.created_at, reverse=True)[:5]
        assessment_total = len(assessment_rows)
        data["assessments"] = []
        for idx, r in enumerate(assessment_rows):
            fallback_mode = "new" if idx == assessment_total - 1 else "reassess"
            data["assessments"].append({
                "date": r.created_at.strftime("%b %d, %Y"),
                "status": r.status,
                "assessment_mode": _normalize_assessment_mode(r.assessment_mode, fallback_mode),
                "dti": f"{r.dti_ratio:.1f}%" if r.dti_ratio is not None else "—",
                "max_loanable": f"₱{float(r.max_loanable):,.0f}" if r.max_loanable else "—",
                "similarity": ("High" if r.similarity_score >= 0.70 else ("Moderate" if r.similarity_score >= 0.40 else "Low")) if r.similarity_score is not None else "—",
            })

        has_id_val = "Yes" if (cp and cp.has_valid_id is True) else ("No" if (cp and cp.has_valid_id is False) else "—")
        has_inc_val = "Yes" if (cp and cp.has_income_proof is True) else ("No" if (cp and cp.has_income_proof is False) else "—")
        data["documents"] = {
            "has_valid_id": has_id_val,
            "has_income_proof": has_inc_val,
        }
        client_sales = (PropertySale.query
                        .filter_by(client_id=user.id)
                        .options(
                            selectinload(PropertySale.property_item).selectinload(Property.subdivision),
                        )
                        .order_by(PropertySale.sold_at.desc())
                        .all())
        data["bought_properties"] = [
            {
                "property": s.property_item.name if s.property_item else "—",
                "location": s.property_item.location if s.property_item else "—",
                "sold_at": s.sold_at.strftime("%b %d, %Y %I:%M %p") if s.sold_at else "—",
                "price": f"₱{float(s.selling_price):,.0f}" if s.selling_price is not None else "—",
            }
            for s in client_sales
        ]

    elif user.role == "agent":
        ai = user.profile
        data["agent"] = {
            "license_no": ai.license_no or "—",
            "contact_no": ai.contact_no or "—",
            "bio":        ai.bio or "—",
        } if ai else None
        data["avatar_url"] = (
            url_for("main.serve_agent_avatar", user_id=user.id)
            if ai and ai.avatar_data else None
        )
        agent_sales = (PropertySale.query
                       .filter_by(agent_id=user.id)
                       .options(
                           selectinload(PropertySale.property_item),
                           selectinload(PropertySale.client),
                       )
                       .order_by(PropertySale.sold_at.desc())
                       .all())
        data["sold_properties"] = [
            {
                "property": s.property_item.name if s.property_item else "—",
                "location": s.property_item.location if s.property_item else "—",
                "buyer": s.client.full_name if s.client else "—",
                "sold_at": s.sold_at.strftime("%b %d, %Y %I:%M %p") if s.sold_at else "—",
                "price": f"₱{float(s.selling_price):,.0f}" if s.selling_price is not None else "—",
            }
            for s in agent_sales
        ]

    return jsonify(data)


@main_bp.route("/admin/client/<int:user_id>/document-view/<doc_kind>")
@login_required
def admin_view_client_document_tab(user_id, doc_kind):
    if current_user.role != "admin":
        return "Forbidden", 403

    normalized_kind = _normalize_client_doc_kind(doc_kind)
    if not normalized_kind:
        return "Not Found", 404

    client = db.session.get(User, user_id)
    if not client or client.role != "client":
        return "Not Found", 404

    payload, mimetype, filename = _resolve_client_doc(client.profile, normalized_kind)
    if not payload:
        return "Not Found", 404

    file_url = url_for("main.admin_serve_client_document", user_id=user_id, doc_kind=normalized_kind)
    return render_template("client/document_view.html", file_url=file_url, filename=filename)


@main_bp.route("/admin/client/<int:user_id>/document/<doc_kind>")
@login_required
def admin_serve_client_document(user_id, doc_kind):
    if current_user.role != "admin":
        return "Forbidden", 403

    normalized_kind = _normalize_client_doc_kind(doc_kind)
    if not normalized_kind:
        return "Not Found", 404

    client = db.session.get(User, user_id)
    if not client or client.role != "client":
        return "Not Found", 404

    payload, mimetype, filename = _resolve_client_doc(client.profile, normalized_kind)
    if not payload:
        return "Not Found", 404

    safe_filename = secure_filename(filename) or ("valid-id" if normalized_kind == "valid-id" else "proof-of-income")
    resp = make_response(payload)
    resp.headers["Content-Type"] = mimetype
    resp.headers["Content-Disposition"] = f'inline; filename="{safe_filename}"'
    resp.headers["Cache-Control"] = "no-store"
    return resp


@main_bp.route("/admin/agent/<int:agent_id>/availability")
@login_required
def admin_agent_availability(agent_id):
    if current_user.role != "admin":
        return jsonify(ok=False, error="Forbidden"), 403

    agent = db.session.get(User, agent_id)
    if not agent or agent.role != "agent":
        return jsonify(ok=False, error="Agent not found."), 404

    rows = (AgentAvailability.query
            .filter_by(agent_id=agent_id)
            .order_by(AgentAvailability.available_date.asc(), AgentAvailability.start_time.asc())
            .all())

    items = [{
        "id": row.id,
        "available_date": row.available_date.strftime("%Y-%m-%d"),
        "availability_status": (row.availability_status or "available"),
        "start_time": row.start_time.strftime("%H:%M"),
        "end_time": row.end_time.strftime("%H:%M"),
        "notes": row.notes or "",
        "created_at": row.created_at.strftime("%b %d") if row.created_at else "",
    } for row in rows]

    return jsonify(ok=True, agent_name=agent.full_name, items=items)

# ── Admin: create agent ────────────────────────────────────────────────────────

@main_bp.route("/admin/agent/create", methods=["POST"])
@login_required
def admin_create_agent():
    if current_user.role != "admin":
        return jsonify({"error": "Forbidden"}), 403
    data = request.get_json(silent=True) or {}
    first_name = (data.get("first_name") or "").strip()
    last_name  = (data.get("last_name") or "").strip()
    email      = (data.get("email") or "").strip()
    password   = (data.get("password") or "").strip()
    contact    = (data.get("contact_number") or "").strip()
    license_no = (data.get("license_no") or "").strip()

    if not first_name or not last_name or not email or not password:
        return jsonify({"error": "First name, last name, email, and password are required."}), 400
    if User.query.filter_by(email=email).first():
        return jsonify({"error": "A user with this email already exists."}), 409

    user = User(first_name=first_name, last_name=last_name, email=email,
                contact_number=contact, role="agent", is_active=True)
    user.set_password(password)
    db.session.add(user)
    db.session.flush()
    agent = UserProfile(user_id=user.id, license_no=license_no, contact_no=contact)
    db.session.add(agent)
    log_activity("agent_create", f"New agent account created: {user.full_name} ({email})")
    db.session.commit()
    return jsonify({"success": True, "user_id": user.id, "full_name": user.full_name})


# ── Admin: create client ───────────────────────────────────────────────────────

@main_bp.route("/admin/client/create", methods=["POST"])
@login_required
def admin_create_client():
    if current_user.role != "admin":
        return jsonify({"error": "Forbidden"}), 403
    data = request.get_json(silent=True) or {}
    first_name = (data.get("first_name") or "").strip()
    last_name  = (data.get("last_name") or "").strip()
    email      = (data.get("email") or "").strip()
    password   = (data.get("password") or "").strip()
    contact    = (data.get("contact_number") or "").strip()

    if not first_name or not last_name or not email or not password:
        return jsonify({"error": "First name, last name, email, and password are required."}), 400
    if User.query.filter_by(email=email).first():
        return jsonify({"error": "A user with this email already exists."}), 409

    user = User(first_name=first_name, last_name=last_name, email=email,
                contact_number=contact, role="client", is_active=True)
    user.set_password(password)
    db.session.add(user)
    log_activity("client_create", f"New client account created: {first_name} {last_name} ({email})")
    db.session.commit()
    return jsonify({"success": True, "user_id": user.id, "full_name": user.full_name})


# ── Admin: approve / reject property ───────────────────────────────────────────

@main_bp.route("/admin/property/<int:prop_id>/approve", methods=["POST"])
@login_required
def admin_approve_property(prop_id):
    if current_user.role != "admin":
        return jsonify({"error": "Forbidden"}), 403
    prop = db.session.get(Property, prop_id)
    if not prop:
        return jsonify({"error": "Not found"}), 404
    prop.approval_status = "approved"
    log_activity("prop_approve", f"Property approved: \"{prop.name}\"")
    db.session.commit()
    return jsonify({"success": True, "property_id": prop.id, "approval_status": "approved"})


@main_bp.route("/admin/property/<int:prop_id>/reject", methods=["POST"])
@login_required
def admin_reject_property(prop_id):
    if current_user.role != "admin":
        return jsonify({"error": "Forbidden"}), 403
    prop = db.session.get(Property, prop_id)
    if not prop:
        return jsonify({"error": "Not found"}), 404
    prop.approval_status = "rejected"
    log_activity("prop_reject", f"Property rejected: \"{prop.name}\"")
    db.session.commit()
    return jsonify({"success": True, "property_id": prop.id, "approval_status": "rejected"})


@main_bp.route("/admin/property/<int:prop_id>/delete", methods=["POST"])
@login_required
def admin_delete_property(prop_id):
    if current_user.role != "admin":
        return jsonify({"error": "Forbidden"}), 403
    prop = db.session.get(Property, prop_id)
    if not prop:
        return jsonify({"error": "Not found"}), 404
    if (prop.status or "").lower() == "sold":
        return jsonify({"error": "Sold properties cannot be deleted to preserve sales history."}), 409
    try:
        for fname in (prop.images or "").split(","):
            _delete_stored_image(fname)

        prop_name = prop.name

        sale_ids = [s.id for s in PropertySale.query.filter_by(property_id=prop.id).all()]

        if sale_ids:
            HistoricalBuyerRecord.query.filter(
                HistoricalBuyerRecord.sale_id.in_(sale_ids)
            ).delete(synchronize_session=False)

        HistoricalBuyerRecord.query.filter_by(property_id=prop.id).delete(synchronize_session=False)
        PropertyPricingDetailRequest.query.filter_by(property_id=prop.id).delete(synchronize_session=False)
        PropertySale.query.filter_by(property_id=prop.id).delete(synchronize_session=False)
        TrippingRequest.query.filter_by(property_id=prop.id).delete(synchronize_session=False)

        # Keep old notifications but detach deleted property reference.
        AgentNotification.query.filter_by(property_id=prop.id).update(
            {"property_id": None},
            synchronize_session=False,
        )

        db.session.delete(prop)
        log_activity("prop_delete", f"Admin deleted property: \"{prop_name}\"")
        db.session.commit()
        return jsonify({"success": True})
    except Exception as exc:
        db.session.rollback()
        current_app.logger.exception("Failed to delete property %s", prop_id)
        return jsonify({"error": f"Failed to delete property: {exc}"}), 500


# ── Admin: create project ──────────────────────────────────────────────────────

@main_bp.route("/admin/project/create", methods=["POST"])
@login_required
def admin_create_project():
    if current_user.role != "admin":
        return jsonify({"error": "Forbidden"}), 403

    name = (request.form.get("name") or "").strip()
    street = (request.form.get("street") or "").strip()
    block = (request.form.get("block") or "").strip()
    lot_no = (request.form.get("lot_no") or "").strip()
    region_code = (request.form.get("region_code") or "").strip()
    region_name = (request.form.get("region_name") or "").strip()
    province_code = (request.form.get("province_code") or "").strip()
    province_name = (request.form.get("province_name") or "").strip()
    citymun_code = (request.form.get("citymun_code") or "").strip()
    citymun_name = (request.form.get("citymun_name") or "").strip()
    barangay_code = (request.form.get("barangay_code") or "").strip()
    barangay_name = (request.form.get("barangay_name") or "").strip()
    tail = ", ".join([p for p in [barangay_name, citymun_name, province_name, region_name] if p])
    location = _compose_full_location(street, block, lot_no, tail)
    desc = (request.form.get("description") or "").strip()
    if not name:
        return jsonify({"error": "Project name is required."}), 400

    if Project.query.filter_by(name=name).first():
        return jsonify({"error": "A project with this name already exists."}), 409

    project = Project(
        name=name,
        street=street or None,
        block=block or None,
        lot_no=lot_no or None,
        location=location,
        region_code=region_code or None,
        region_name=region_name or None,
        province_code=province_code or None,
        province_name=province_name or None,
        citymun_code=citymun_code or None,
        citymun_name=citymun_name or None,
        barangay_code=barangay_code or None,
        barangay_name=barangay_name or None,
        description=desc,
    )
    db.session.add(project)
    db.session.flush()
    image_files = []
    files = request.files.getlist("image_files")
    for f in files:
        if f and f.filename:
            try:
                f.stream.seek(0)
            except Exception:
                pass
            image_files.append(_save_subdivision_image_file(f))
    project.images = image_files
    db.session.commit()
    log_activity("project_create", f"Project created: {name}")
    return jsonify({
        "success": True,
        "id": project.id,
        "name": project.name,
        "street": project.street or "",
        "block": project.block or "",
        "lot_no": project.lot_no or "",
        "location": project.location or "",
        "region_code": project.region_code or "",
        "region_name": project.region_name or "",
        "province_code": project.province_code or "",
        "province_name": project.province_name or "",
        "citymun_code": project.citymun_code or "",
        "citymun_name": project.citymun_name or "",
        "barangay_code": project.barangay_code or "",
        "barangay_name": project.barangay_name or "",
        "description": project.description or "",
        "image_ids": list(project.images or []),
    })


@main_bp.route("/admin/project/<int:project_id>/delete", methods=["POST"])
@login_required
def admin_delete_project(project_id):
    if current_user.role != "admin":
        return jsonify({"error": "Forbidden"}), 403

    project = db.session.get(Project, project_id)
    if not project:
        return jsonify({"error": "Not found"}), 404
    if project.subdivisions:
        return jsonify({"error": "Cannot delete project with subdivisions assigned."}), 400

    for fname in list(project.images or []):
        _delete_stored_image(fname)

    project_name = project.name
    db.session.delete(project)
    log_activity("project_delete", f"Project deleted: {project_name}")
    db.session.commit()
    return jsonify({"success": True, "id": project_id})


@main_bp.route("/admin/project/<int:project_id>/detail")
@login_required
def admin_project_detail(project_id):
    if current_user.role != "admin":
        return jsonify({"error": "Forbidden"}), 403
    project = db.session.get(Project, project_id)
    if not project:
        return jsonify({"error": "Not found"}), 404
    return jsonify({
        "success": True,
        "id": project.id,
        "name": project.name or "",
        "street": project.street or "",
        "block": project.block or "",
        "lot_no": project.lot_no or "",
        "location": project.location or "",
        "region_code": project.region_code or "",
        "region_name": project.region_name or "",
        "province_code": project.province_code or "",
        "province_name": project.province_name or "",
        "citymun_code": project.citymun_code or "",
        "citymun_name": project.citymun_name or "",
        "barangay_code": project.barangay_code or "",
        "barangay_name": project.barangay_name or "",
        "description": project.description or "",
        "image_ids": list(project.images or []),
    })


@main_bp.route("/admin/project/<int:project_id>/edit", methods=["POST"])
@login_required
def admin_edit_project(project_id):
    if current_user.role != "admin":
        return jsonify({"error": "Forbidden"}), 403

    project = db.session.get(Project, project_id)
    if not project:
        return jsonify({"error": "Not found"}), 404

    name = (request.form.get("name") or "").strip()
    street = (request.form.get("street") or "").strip()
    block = (request.form.get("block") or "").strip()
    lot_no = (request.form.get("lot_no") or "").strip()
    region_code = (request.form.get("region_code") or "").strip()
    region_name = (request.form.get("region_name") or "").strip()
    province_code = (request.form.get("province_code") or "").strip()
    province_name = (request.form.get("province_name") or "").strip()
    citymun_code = (request.form.get("citymun_code") or "").strip()
    citymun_name = (request.form.get("citymun_name") or "").strip()
    barangay_code = (request.form.get("barangay_code") or "").strip()
    barangay_name = (request.form.get("barangay_name") or "").strip()
    tail = ", ".join([p for p in [barangay_name, citymun_name, province_name, region_name] if p])
    location = _compose_full_location(street, block, lot_no, tail)
    desc = (request.form.get("description") or "").strip()

    if not name:
        return jsonify({"error": "Project name is required."}), 400
    existing = Project.query.filter_by(name=name).first()
    if existing and existing.id != project_id:
        return jsonify({"error": "A project with this name already exists."}), 409

    geo_provided = any([region_code, region_name, province_code, province_name,
                        citymun_code, citymun_name, barangay_code, barangay_name])

    remove_ids = [str(x).strip() for x in request.form.getlist("remove_image_ids") if str(x).strip()]
    current_images = list(project.images or [])
    if remove_ids:
        current_images = [img for img in current_images if img not in remove_ids]
        for image_key in remove_ids:
            _delete_stored_image(image_key)

    files = request.files.getlist("image_files")
    for f in files:
        if f and f.filename:
            try:
                f.stream.seek(0)
            except Exception:
                pass
            current_images.append(_save_subdivision_image_file(f))

    project.name = name
    project.street = street or None
    project.block = block or None
    project.lot_no = lot_no or None
    if geo_provided or location:
        project.location = location
    if geo_provided:
        project.region_code = region_code or None
        project.region_name = region_name or None
        project.province_code = province_code or None
        project.province_name = province_name or None
        project.citymun_code = citymun_code or None
        project.citymun_name = citymun_name or None
        project.barangay_code = barangay_code or None
        project.barangay_name = barangay_name or None
    project.description = desc
    project.images = current_images

    log_activity("project_edit", f"Project updated: {project.name}" + (f" — {location}" if location else ""))
    db.session.commit()
    return jsonify({
        "success": True,
        "id": project.id,
        "name": project.name,
        "street": project.street or "",
        "block": project.block or "",
        "lot_no": project.lot_no or "",
        "location": project.location or "",
        "description": project.description or "",
        "image_ids": list(project.images or []),
    })

@main_bp.route("/admin/subdivision/create", methods=["POST"])
@login_required
def admin_create_subdivision():
    if current_user.role != "admin":
        return jsonify({"error": "Forbidden"}), 403
    name     = (request.form.get("name") or "").strip()
    project_id_raw = (request.form.get("project_id") or "").strip()
    street = (request.form.get("street") or "").strip()
    block = (request.form.get("block") or "").strip()
    lot_no = (request.form.get("lot_no") or "").strip()
    region_code = (request.form.get("region_code") or "").strip()
    region_name = (request.form.get("region_name") or "").strip()
    province_code = (request.form.get("province_code") or "").strip()
    province_name = (request.form.get("province_name") or "").strip()
    citymun_code = (request.form.get("citymun_code") or "").strip()
    citymun_name = (request.form.get("citymun_name") or "").strip()
    barangay_code = (request.form.get("barangay_code") or "").strip()
    barangay_name = (request.form.get("barangay_name") or "").strip()
    tail = ", ".join([p for p in [barangay_name, citymun_name, province_name, region_name] if p])
    location = _compose_full_location(street, block, lot_no, tail)
    desc     = (request.form.get("description") or "").strip()
    if not project_id_raw:
        return jsonify({"error": "Select a project first before creating a subdivision."}), 400
    try:
        project_id = int(project_id_raw)
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid project selection."}), 400
    project = db.session.get(Project, project_id)
    if not project:
        return jsonify({"error": "Selected project was not found."}), 404
    if not name:
        return jsonify({"error": "Subdivision name is required."}), 400
    if Subdivision.query.filter_by(name=name).first():
        return jsonify({"error": "A subdivision with this name already exists."}), 409
    try:
        sub = Subdivision(
            name=name,
            project_id=project.id,
            street=street or None,
            block=block or None,
            lot_no=lot_no or None,
            location=location,
            region_code=region_code or None,
            region_name=region_name or None,
            province_code=province_code or None,
            province_name=province_name or None,
            citymun_code=citymun_code or None,
            citymun_name=citymun_name or None,
            barangay_code=barangay_code or None,
            barangay_name=barangay_name or None,
            description=desc,
        )
        db.session.add(sub)
        db.session.flush()
        image_files = []
        files = request.files.getlist("image_files")
        for f in files:
            if f and f.filename:
                try:
                    f.stream.seek(0)
                except Exception:
                    pass
                image_files.append(_save_subdivision_image_file(f))
        _set_subdivision_images(sub, image_files)
        log_activity("sub_create", f"Subdivision created: {name} under {project.name}" + (f" — {location}" if location else ""))
        db.session.commit()
        return jsonify({"success": True, "id": sub.id, "name": sub.name, "project_id": project.id, "project_name": project.name, "image_ids": _subdivision_images_to_list(sub)})
    except Exception as exc:
        db.session.rollback()
        current_app.logger.exception("Failed to create project")
        payload = {"error": "Failed to create project. Please try again."}
        if current_app.debug:
            payload["detail"] = str(exc)
        return jsonify(payload), 500


@main_bp.route("/admin/subdivision-image/<path:image_key>")
def serve_subdivision_image(image_key):
    if _is_remote_image_ref(image_key):
        return redirect(image_key)
    upload_dir = current_app.config["UPLOAD_FOLDER"]
    clean_key = os.path.basename((image_key or "").strip())
    if not clean_key:
        return "", 404
    return send_from_directory(upload_dir, clean_key)


# backward-compat: redirect old per-project URL to first image
@main_bp.route("/admin/subdivision/<int:sub_id>/image")
def subdivision_image(sub_id):
    sub = db.session.get(Subdivision, sub_id)
    if not sub or not sub.images:
        return "", 404
    return redirect(url_for("main.serve_subdivision_image", image_key=sub.images[0]))


@main_bp.route("/admin/subdivision-image/<path:image_key>/delete", methods=["POST"])
@login_required
def delete_subdivision_image(image_key):
    if current_user.role != "admin":
        return jsonify({"error": "Forbidden"}), 403
    image_key = (image_key or "").strip()
    if not image_key:
        return jsonify({"error": "Not found"}), 404

    found = False
    for sub in Subdivision.query.all():
        imgs = _subdivision_images_to_list(sub)
        if image_key in imgs:
            imgs = [name for name in imgs if name != image_key]
            _set_subdivision_images(sub, imgs)
            found = True

    _delete_stored_image(image_key)

    if not found:
        return jsonify({"error": "Not found"}), 404

    db.session.commit()
    return jsonify({"success": True})


@main_bp.route("/uploads/<path:filename>")
def serve_upload(filename):
    if _is_remote_image_ref(filename):
        return redirect(filename)
    upload_dir = current_app.config["UPLOAD_FOLDER"]
    return send_from_directory(upload_dir, filename)


@main_bp.route("/admin/subdivision/<int:sub_id>/edit", methods=["POST"])
@login_required
def admin_edit_subdivision(sub_id):
    if current_user.role != "admin":
        return jsonify({"error": "Forbidden"}), 403
    sub = db.session.get(Subdivision, sub_id)
    if not sub:
        return jsonify({"error": "Not found"}), 404
    name     = (request.form.get("name") or "").strip()
    project_id_raw = (request.form.get("project_id") or "").strip()
    street = (request.form.get("street") or "").strip()
    block = (request.form.get("block") or "").strip()
    lot_no = (request.form.get("lot_no") or "").strip()
    region_code = (request.form.get("region_code") or "").strip()
    region_name = (request.form.get("region_name") or "").strip()
    province_code = (request.form.get("province_code") or "").strip()
    province_name = (request.form.get("province_name") or "").strip()
    citymun_code = (request.form.get("citymun_code") or "").strip()
    citymun_name = (request.form.get("citymun_name") or "").strip()
    barangay_code = (request.form.get("barangay_code") or "").strip()
    barangay_name = (request.form.get("barangay_name") or "").strip()
    tail = ", ".join([p for p in [barangay_name, citymun_name, province_name, region_name] if p])
    location = _compose_full_location(street, block, lot_no, tail)
    desc     = (request.form.get("description") or "").strip()
    if not project_id_raw:
        return jsonify({"error": "Select a project first before saving subdivision."}), 400
    try:
        project_id = int(project_id_raw)
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid project selection."}), 400
    project = db.session.get(Project, project_id)
    if not project:
        return jsonify({"error": "Selected project was not found."}), 404
    if not name:
        return jsonify({"error": "Subdivision name is required."}), 400
    existing = Subdivision.query.filter_by(name=name).first()
    if existing and existing.id != sub_id:
        return jsonify({"error": "A subdivision with this name already exists."}), 409
    sub.name = name
    sub.project_id = project.id
    sub.street = street or None
    sub.block = block or None
    sub.lot_no = lot_no or None
    sub.location = location
    if region_code or region_name or province_code or province_name or citymun_code or citymun_name or barangay_code or barangay_name:
        sub.region_code = region_code or None
        sub.region_name = region_name or None
        sub.province_code = province_code or None
        sub.province_name = province_name or None
        sub.citymun_code = citymun_code or None
        sub.citymun_name = citymun_name or None
        sub.barangay_code = barangay_code or None
        sub.barangay_name = barangay_name or None
    sub.description = desc
    current_images = _subdivision_images_to_list(sub)
    files = request.files.getlist("image_files")
    for f in files:
        if f and f.filename:
            current_images.append(_save_subdivision_image_file(f))
    _set_subdivision_images(sub, current_images)
    log_activity("sub_edit", f"Subdivision updated: {name} under {project.name}" + (f" — {location}" if location else ""))
    db.session.commit()
    return jsonify({"success": True, "id": sub.id, "name": sub.name, "project_id": project.id, "project_name": project.name, "image_ids": _subdivision_images_to_list(sub)})


@main_bp.route("/admin/subdivision/<int:sub_id>/detail")
@login_required
def admin_subdivision_detail(sub_id):
    if current_user.role != "admin":
        return jsonify({"error": "Forbidden"}), 403
    sub = db.session.get(Subdivision, sub_id)
    if not sub:
        return jsonify({"error": "Not found"}), 404
    return jsonify({
        "success": True,
        "id": sub.id,
        "name": sub.name or "",
        "project_id": sub.project_id,
        "project_name": sub.project.name if sub.project else "",
        "street": sub.street or "",
        "block": sub.block or "",
        "lot_no": sub.lot_no or "",
        "location": sub.location or "",
        "region_code": sub.region_code or "",
        "region_name": sub.region_name or "",
        "province_code": sub.province_code or "",
        "province_name": sub.province_name or "",
        "citymun_code": sub.citymun_code or "",
        "citymun_name": sub.citymun_name or "",
        "barangay_code": sub.barangay_code or "",
        "barangay_name": sub.barangay_name or "",
        "description": sub.description or "",
        "image_ids": _subdivision_images_to_list(sub),
    })


@main_bp.route("/admin/subdivision/<int:sub_id>/delete", methods=["POST"])
@login_required
def admin_delete_subdivision(sub_id):
    if current_user.role != "admin":
        return jsonify({"error": "Forbidden"}), 403
    sub = db.session.get(Subdivision, sub_id)
    if not sub:
        return jsonify({"error": "Not found"}), 404
    if sub.properties:
        return jsonify({"error": "Cannot delete — project has properties assigned."}), 400
    for fname in _subdivision_images_to_list(sub):
        _delete_stored_image(fname)
    sub_name = sub.name
    db.session.delete(sub)
    log_activity("sub_delete", f"Subdivision deleted: {sub_name}")
    db.session.commit()
    return jsonify({"success": True})


# ── About Us (redirect to landing page anchor) ───────────────────────────────

@main_bp.route("/about")
def about():
    return redirect(url_for("main.index") + "#about")


# ── Listings (redirect to landing page anchor) ───────────────────────────────

@main_bp.route("/listings")
def listings():
    return redirect(url_for("main.index") + "#listings")


# ── Admin: C5.0 / Historical-buyer management ─────────────────────────────────

@main_bp.route("/admin/c50/training-data/add", methods=["POST"])
@login_required
def admin_c50_add_record():
    if current_user.role != "admin":
        return jsonify({"error": "Forbidden"}), 403
    data = request.get_json(silent=True) or {}

    try:
        gross_income  = float(_clean_numeric_str(data.get("gross_income"))  or 0)
        monthly_loans = float(_clean_numeric_str(data.get("monthly_loans")) or 0)
        tenure_months = int(data.get("tenure_months")   or 0)
        age           = int(data.get("age")             or 30)
        dependents    = int(data.get("dependents")      or 0)
        employment    = (data.get("employment_type")    or "employed").strip()
        civil_status  = (data.get("civil_status")       or "single").strip()
        outcome       = (data.get("outcome")            or "").strip()
        notes         = (data.get("notes")              or "").strip()[:255]
    except (TypeError, ValueError) as e:
        return jsonify({"error": f"Invalid data: {e}"}), 400

    if outcome not in ("Qualified", "Conditionally Qualified", "Not Qualified"):
        return jsonify({"error": "Outcome must be Qualified, Conditionally Qualified, or Not Qualified."}), 400
    if gross_income <= 0:
        return jsonify({"error": "Gross income must be greater than 0."}), 400

    dti = (monthly_loans / gross_income * 100) if gross_income > 0 else 0.0

    buyer = HistoricalBuyer(
        civil_status    = civil_status,
        dependents      = dependents,
        age             = age,
        employment_type = employment,
        tenure_months   = tenure_months,
        gross_income    = gross_income,
        monthly_loans   = monthly_loans,
        dti_ratio       = round(dti, 2),
        outcome         = outcome,
        notes           = notes,
    )
    db.session.add(buyer)
    log_activity("c50_add_record", f"Training record added: {outcome} — ₱{gross_income:,.0f}/mo")
    db.session.commit()

    # Retrain with updated dataset
    buyers = HistoricalBuyer.query.all()
    c50_engine.train(buyers)

    return jsonify({"success": True, "id": buyer.id, "meta": c50_engine.get_meta()})


@main_bp.route("/admin/c50/training-data/add-only", methods=["POST"])
@login_required
def admin_c50_add_record_only():
    """Add a training record without triggering a retrain."""
    if current_user.role != "admin":
        return jsonify({"error": "Forbidden"}), 403
    data = request.get_json(silent=True) or {}
    try:
        gross_income  = float(_clean_numeric_str(data.get("gross_income"))  or 0)
        monthly_loans = float(_clean_numeric_str(data.get("monthly_loans")) or 0)
        tenure_months = int(data.get("tenure_months")   or 0)
        age           = int(data.get("age")             or 30)
        dependents    = int(data.get("dependents")      or 0)
        employment    = (data.get("employment_type")    or "employed").strip()
        civil_status  = (data.get("civil_status")       or "single").strip()
        outcome       = (data.get("outcome")            or "").strip()
    except (TypeError, ValueError) as e:
        return jsonify({"error": f"Invalid data: {e}"}), 400
    if outcome not in ("Qualified", "Conditionally Qualified", "Not Qualified"):
        return jsonify({"error": "Outcome must be Qualified, Conditionally Qualified, or Not Qualified."}), 400
    if gross_income <= 0:
        return jsonify({"error": "Gross income must be greater than 0."}), 400
    dti = (monthly_loans / gross_income * 100) if gross_income > 0 else 0.0
    buyer = HistoricalBuyer(
        civil_status    = civil_status,
        dependents      = dependents,
        age             = age,
        employment_type = employment,
        tenure_months   = tenure_months,
        gross_income    = gross_income,
        monthly_loans   = monthly_loans,
        dti_ratio       = round(dti, 2),
        outcome         = outcome,
    )
    db.session.add(buyer)
    log_activity("c50_add_record", f"Training record added: {outcome} — ₱{gross_income:,.0f}/mo")
    db.session.commit()
    return jsonify({"success": True, "id": buyer.id})


@main_bp.route("/admin/c50/training-data/<int:record_id>/edit", methods=["POST"])
@login_required
def admin_c50_edit_record(record_id):
    if current_user.role != "admin":
        return jsonify({"error": "Forbidden"}), 403
    buyer = db.session.get(HistoricalBuyer, record_id)
    if not buyer:
        return jsonify({"error": "Not found"}), 404
    data = request.get_json(silent=True) or {}
    try:
        gross_income  = float(_clean_numeric_str(data.get("gross_income"))  or 0)
        monthly_loans = float(_clean_numeric_str(data.get("monthly_loans")) or 0)
        tenure_months = int(data.get("tenure_months")   or 0)
        age           = int(data.get("age")             or 30)
        dependents    = int(data.get("dependents")      or 0)
        employment    = (data.get("employment_type")    or "employed").strip()
        civil_status  = (data.get("civil_status")       or "single").strip()
        outcome       = (data.get("outcome")            or "").strip()
        notes         = (data.get("notes")              or "").strip()[:255]
    except (TypeError, ValueError) as e:
        return jsonify({"error": f"Invalid data: {e}"}), 400
    if outcome not in ("Qualified", "Conditionally Qualified", "Not Qualified"):
        return jsonify({"error": "Outcome must be Qualified, Conditionally Qualified, or Not Qualified."}), 400
    if gross_income <= 0:
        return jsonify({"error": "Gross income must be greater than 0."}), 400
    dti = (monthly_loans / gross_income * 100) if gross_income > 0 else 0.0
    buyer.civil_status    = civil_status
    buyer.dependents      = dependents
    buyer.age             = age
    buyer.employment_type = employment
    buyer.tenure_months   = tenure_months
    buyer.gross_income    = gross_income
    buyer.monthly_loans   = monthly_loans
    buyer.dti_ratio       = round(dti, 2)
    buyer.outcome         = outcome
    buyer.notes           = notes
    log_activity("c50_edit_record", f"Training record #{record_id} updated: {outcome} — ₱{gross_income:,.0f}/mo")
    db.session.commit()
    buyers = HistoricalBuyer.query.all()
    c50_engine.train(buyers)
    return jsonify({"success": True, "meta": c50_engine.get_meta(), "record": {
        "id":              buyer.id,
        "employment_type": buyer.employment_type,
        "civil_status":    buyer.civil_status,
        "age":             buyer.age,
        "dependents":      buyer.dependents or 0,
        "tenure_months":   buyer.tenure_months,
        "gross_income":    float(buyer.gross_income),
        "monthly_loans":   float(buyer.monthly_loans or 0),
        "dti_ratio":       float(buyer.dti_ratio or 0),
        "outcome":         buyer.outcome,
        "notes":           buyer.notes or "",
    }})


@main_bp.route("/admin/c50/training-data/<int:record_id>/delete", methods=["POST"])
@login_required
def admin_c50_delete_record(record_id):
    if current_user.role != "admin":
        return jsonify({"error": "Forbidden"}), 403
    buyer = db.session.get(HistoricalBuyer, record_id)
    if not buyer:
        return jsonify({"error": "Not found"}), 404
    db.session.delete(buyer)
    log_activity("c50_del_record", f"Training record #{record_id} deleted")
    db.session.commit()

    buyers = HistoricalBuyer.query.all()
    c50_engine.train(buyers)

    return jsonify({"success": True, "meta": c50_engine.get_meta()})


@main_bp.route("/admin/c50/retrain", methods=["POST"])
@login_required
def admin_c50_retrain():
    if current_user.role != "admin":
        return jsonify({"error": "Forbidden"}), 403
    buyers = HistoricalBuyer.query.all()
    c50_engine.train(buyers)
    meta = c50_engine.get_meta()
    log_activity("c50_retrain", f"Model retrained — {meta.get('n_samples', 0)} samples")
    return jsonify({"success": True, "meta": meta})


@main_bp.route("/admin/c50/sync-status", methods=["GET"])
@login_required
def admin_c50_sync_status():
    if current_user.role != "admin":
        return jsonify({"error": "Forbidden"}), 403

    historical_sale_ids = {
        int(sid) for (sid,) in db.session.query(HistoricalBuyerRecord.sale_id).all() if sid is not None
    }
    synced_sale_ids = _get_synced_sale_ids_from_training()
    synced_count = len(historical_sale_ids & synced_sale_ids)
    total_count = len(historical_sale_ids)

    return jsonify({
        "success": True,
        "total": total_count,
        "synced": synced_count,
        "unsynced": max(0, total_count - synced_count),
    })


@main_bp.route("/admin/c50/sync-historical", methods=["POST"])
@login_required
def admin_c50_sync_historical():
    if current_user.role != "admin":
        return jsonify({"error": "Forbidden"}), 403

    payload = request.get_json(silent=True) or {}
    dry_run = bool(payload.get("dry_run"))

    records = HistoricalBuyerRecord.query.order_by(HistoricalBuyerRecord.created_at.asc()).all()
    inserted = 0
    duplicates = 0
    missing_sale_ids = 0
    # One prefetch for the whole batch — replaces the per-record LIKE query.
    synced_sale_ids = _get_synced_sale_ids_from_training()

    for rec in records:
        created, reason = _sync_single_historical_to_training(rec, synced_sale_ids=synced_sale_ids)
        if created:
            inserted += 1
        elif reason == "duplicate":
            duplicates += 1
        elif reason == "missing_sale_id":
            missing_sale_ids += 1

    if dry_run:
        db.session.rollback()
        return jsonify({
            "success": True,
            "dry_run": True,
            "total_historical": len(records),
            "would_insert": inserted,
            "duplicates": duplicates,
            "missing_sale_ids": missing_sale_ids,
        })

    if inserted > 0:
        db.session.commit()
    else:
        db.session.rollback()

    retrain_started = False
    if inserted > 0:
        retrain_started = _trigger_c50_retrain_async("historical_sync")

    log_activity(
        "c50_sync_historical",
        f"Historical sync completed — inserted={inserted}, duplicates={duplicates}, missing_sale_ids={missing_sale_ids}, retrain_started={retrain_started}",
    )

    return jsonify({
        "success": True,
        "dry_run": False,
        "total_historical": len(records),
        "inserted": inserted,
        "duplicates": duplicates,
        "missing_sale_ids": missing_sale_ids,
        "retrain_started": retrain_started,
        "meta": c50_engine.get_meta(),
    })


@main_bp.route("/admin/c50/seed", methods=["POST"])
@login_required
def admin_c50_seed():
    """Seed the training_data table with 80 synthetic records for dev/demo."""
    if current_user.role != "admin":
        return jsonify({"error": "Forbidden"}), 403

    req_json = request.get_json(silent=True) or {}
    replace = request.args.get("replace", "false").lower() in ("true", "1") or bool(req_json.get("replace"))

    if HistoricalBuyer.query.count() >= 10 and not replace:
        return jsonify({"error": "Training data already exists. Delete records first or retrain."}), 409

    if replace:
        HistoricalBuyer.query.delete()
        db.session.commit()

    import random
    random.seed(2026)

    _civils = ["single", "married", "married", "single", "widowed"]

    # Keep seed generation aligned with admin-configured employment stability scores.
    emp_scores = {
        "employed": _get_system_config_int("stability_employed", 5),
        "ofw-landbased": _get_system_config_int("stability_ofw_landbased", 4),
        "ofw-seafarer": _get_system_config_int("stability_ofw_seafarer", 4),
        "licensed-professional": _get_system_config_int("stability_licensed_professional", 5),
        "with-financial-support": _get_system_config_int("stability_with_financial_support", 3),
        "with-attorney-in-fact": _get_system_config_int("stability_with_attorney_in_fact", 3),
        "with-co-borrower": _get_system_config_int("stability_with_co_borrower", 4),
    }

    def _pick_employment(outcome: str) -> str:
        weighted_pool: list[str] = []
        for emp, score in emp_scores.items():
            s = int(score or 0)
            if outcome == "Qualified":
                # Higher stability appears more often in clearly qualified profiles.
                w = max(1, s - 1)
            elif outcome == "Conditionally Qualified":
                # Mid-range distribution for conditional outcomes.
                w = max(1, 4 - abs(s - 4))
            else:
                # Lower stability appears more often in not-qualified profiles.
                w = max(1, 7 - s)
            weighted_pool.extend([emp] * w)
        return random.choice(weighted_pool) if weighted_pool else "employed"

    q_max = _get_system_config_float("dti_qualified_max", 30.0) / 100.0
    c_max = _get_system_config_float("dti_conditional_max", 42.0) / 100.0

    records = []
    # --- Clearly Qualified profiles (DTI <= 30%) ---
    for _ in range(30):
        gi  = random.uniform(35_000, 120_000)
        ml  = random.uniform(0, gi * q_max)
        ten = random.randint(12, 120)
        age = random.randint(25, 50)
        dep = random.randint(0, 3)
        dti = ml / gi * 100
        records.append(dict(
            civil_status=random.choice(["married", "single"]),
            dependents=dep, age=age,
            employment_type=_pick_employment("Qualified"),
            tenure_months=ten, gross_income=gi,
            monthly_loans=ml, dti_ratio=round(dti, 2),
            outcome="Qualified"
        ))

    # --- Conditionally Qualified profiles (DTI 30% to 42%) ---
    for _ in range(25):
        gi  = random.uniform(18_000, 50_000)
        ml  = random.uniform(gi * q_max, gi * c_max)
        ten = random.randint(3, 24)
        age = random.randint(22, 55)
        dep = random.randint(0, 4)
        dti = ml / gi * 100
        records.append(dict(
            civil_status=random.choice(_civils),
            dependents=dep, age=age,
            employment_type=_pick_employment("Conditionally Qualified"),
            tenure_months=ten, gross_income=gi,
            monthly_loans=ml, dti_ratio=round(dti, 2),
            outcome="Conditionally Qualified"
        ))

    # --- Not Qualified profiles (DTI > 42%) ---
    for _ in range(25):
        gi  = random.uniform(8_000, 30_000)
        ml  = random.uniform(gi * c_max, gi * 0.85)
        ten = random.randint(0, 12)
        age = random.randint(18, 60)
        dep = random.randint(0, 5)
        dti = ml / gi * 100
        records.append(dict(
            civil_status=random.choice(_civils),
            dependents=dep, age=age,
            employment_type=_pick_employment("Not Qualified"),
            tenure_months=ten, gross_income=gi,
            monthly_loans=ml, dti_ratio=round(dti, 2),
            outcome="Not Qualified"
        ))

    for r in records:
        db.session.add(HistoricalBuyer(**r))

    db.session.commit()

    # Train immediately
    buyers = HistoricalBuyer.query.all()
    c50_engine.train(buyers)
    meta = c50_engine.get_meta()
    log_activity("c50_seed", f"Seeded {len(records)} historical training records")

    return jsonify({"success": True, "seeded": len(records), "meta": meta})


# ── Admin: save qualification criteria ─────────────────────────────────────────

@main_bp.route("/admin/criteria/save", methods=["POST"])
@login_required
def admin_criteria_save():
    if current_user.role != "admin":
        return jsonify({"error": "Forbidden"}), 403

    data = request.get_json(silent=True) or {}

    allowed = {
        "dti_qualified_max", "dti_conditional_max",
        "confidence_threshold", "min_tenure_months", "min_gross_income",
        "stability_employed", "stability_ofw_landbased", "stability_ofw_seafarer",
        "stability_licensed_professional", "stability_with_financial_support",
        "stability_with_attorney_in_fact", "stability_with_co_borrower",
    }

    try:
        updated = {}
        for key in allowed:
            if key in data:
                val = float(data[key])
                rec = SystemConfig.query.filter_by(key=key).first()
                if rec:
                    rec.value = str(val)
                else:
                    db.session.add(SystemConfig(key=key, value=str(val)))
                updated[key] = val

        db.session.commit()

        # Reload c50_engine criteria
        configs = {c.key: c.value for c in SystemConfig.query.all()}
        c50_engine.update_criteria({
            "dti_qualified_max":    float(configs.get("dti_qualified_max",    30.0)),
            "dti_conditional_max":  float(configs.get("dti_conditional_max",  42.0)),
            "confidence_threshold": float(configs.get("confidence_threshold", 72)) / 100.0,
            "min_tenure_months":    int(float(configs.get("min_tenure_months", 6))),
            "min_gross_income":     float(configs.get("min_gross_income",     15000)),
            "stability_employed":                 int(float(configs.get("stability_employed",                 5))),
            "stability_ofw_landbased":            int(float(configs.get("stability_ofw_landbased",            4))),
            "stability_ofw_seafarer":             int(float(configs.get("stability_ofw_seafarer",             4))),
            "stability_licensed_professional":    int(float(configs.get("stability_licensed_professional",    5))),
            "stability_with_financial_support":   int(float(configs.get("stability_with_financial_support",   3))),
            "stability_with_attorney_in_fact":    int(float(configs.get("stability_with_attorney_in_fact",    3))),
            "stability_with_co_borrower":         int(float(configs.get("stability_with_co_borrower",         4))),
        })

        log_activity("criteria_update", f"Updated qualification criteria: {list(updated.keys())}")
        return jsonify({"success": True, "updated": updated})

    except Exception as exc:
        db.session.rollback()
        return jsonify({"error": str(exc)}), 500


# ── Admin: save general / security settings ────────────────────────────────────

@main_bp.route("/admin/settings/general", methods=["POST"])
@login_required
def admin_settings_general():
    if current_user.role != "admin":
        return jsonify({"error": "Forbidden"}), 403

    data = request.get_json(silent=True) or {}
    allowed = {"company_name", "platform_name", "contact_email", "contact_phone", "office_address"}

    try:
        updated = {}
        for key in allowed:
            if key in data:
                val = str(data[key]).strip()
                rec = SystemConfig.query.filter_by(key=key).first()
                if rec:
                    rec.value = val
                else:
                    db.session.add(SystemConfig(key=key, value=val))
                updated[key] = val
        db.session.commit()
        log_activity("settings_update", f"Updated general settings: {list(updated.keys())}")
        return jsonify({"success": True, "updated": updated})
    except Exception as exc:
        db.session.rollback()
        return jsonify({"error": str(exc)}), 500


@main_bp.route("/admin/settings/security", methods=["POST"])
@login_required
def admin_settings_security():
    if current_user.role != "admin":
        return jsonify({"error": "Forbidden"}), 403

    data = request.get_json(silent=True) or {}
    allowed = {"max_login_attempts", "max_forgot_password_attempts", "session_timeout_mins", "min_password_length", "email_verification_enabled"}

    try:
        updated = {}
        for key in allowed:
            if key in data:
                if key == "email_verification_enabled":
                    raw_val = data[key]
                    val = "true" if (raw_val is True or str(raw_val).lower() in {"true", "1", "yes"}) else "false"
                else:
                    val = int(float(data[key]))
                    if key == "max_login_attempts" and (val < 1 or val > 20):
                        return jsonify({"error": "Max login attempts must be between 1 and 20."}), 400
                    if key == "max_forgot_password_attempts" and (val < 1 or val > 20):
                        return jsonify({"error": "Max forgot-password attempts must be between 1 and 20."}), 400
                    if key == "session_timeout_mins" and (val < 5 or val > 1440):
                        return jsonify({"error": "Session timeout must be between 5 and 1440 minutes."}), 400
                    if key == "min_password_length" and (val < 8 or val > 32):
                        return jsonify({"error": "Min password length must be between 8 and 32."}), 400
                rec = SystemConfig.query.filter_by(key=key).first()
                if rec:
                    rec.value = str(val)
                else:
                    db.session.add(SystemConfig(key=key, value=str(val)))
                updated[key] = val
        db.session.commit()
        config_map = {
            "max_login_attempts": "MAX_LOGIN_ATTEMPTS",
            "max_forgot_password_attempts": "MAX_FORGOT_PASSWORD_ATTEMPTS",
            "session_timeout_mins": "SESSION_TIMEOUT_MINS",
            "min_password_length": "MIN_PASSWORD_LENGTH",
        }
        for key, val in updated.items():
            mapped = config_map.get(key)
            if mapped:
                current_app.config[mapped] = val
        log_activity("settings_update", f"Updated security settings: {list(updated.keys())}")
        return jsonify({"success": True, "updated": updated})
    except Exception as exc:
        db.session.rollback()
        return jsonify({"error": str(exc)}), 500


@main_bp.route("/admin/settings/system-info")
@login_required
def admin_system_info():
    if current_user.role != "admin":
        return jsonify({"error": "Forbidden"}), 403

    import sys
    import platform as plat

    total_users   = User.query.count()
    total_props   = Property.query.count()
    total_subs    = Subdivision.query.count()
    total_results = QualificationResult.query.count()
    total_training = HistoricalBuyer.query.count()
    model_meta = c50_engine.get_meta()

    db_size_str = '—'
    if str(db.engine.url).startswith('sqlite'):
        db_path_str = db.engine.url.database
        if db_path_str and os.path.exists(db_path_str):
            b = os.path.getsize(db_path_str)
            db_size_str = (f"{b / (1024*1024):.2f} MB" if b >= 1024*1024
                           else f"{b / 1024:.1f} KB" if b >= 1024
                           else f"{b} B")
    else:
        try:
            with db.engine.connect() as _conn:
                _row = _conn.execute(db.text(
                    "SELECT SUM(data_length + index_length) FROM information_schema.tables "
                    "WHERE table_schema = DATABASE()"
                )).fetchone()
                if _row and _row[0] is not None:
                    _b = int(_row[0])
                    db_size_str = (f"{_b / (1024*1024):.2f} MB" if _b >= 1024*1024
                                   else f"{_b / 1024:.1f} KB" if _b >= 1024
                                   else f"{_b} B")
        except Exception:
            db_size_str = '—'

    return jsonify({
        "python_version": sys.version.split()[0],
        "os_platform":    plat.platform(),
        "db_engine":      str(db.engine.url).split("://")[0],
        "total_users":    total_users,
        "total_properties": total_props,
        "total_subdivisions": total_subs,
        "total_assessments": total_results,
        "total_training":  total_training,
        "model_trained":   model_meta.get("trained", False),
        "model_accuracy":  model_meta.get("train_accuracy", "N/A"),
        "db_size":         db_size_str,
    })


@main_bp.route("/admin/settings/backup-db")
@login_required
def admin_backup_db():
    if current_user.role != "admin":
        return jsonify({"error": "Forbidden"}), 403

    from datetime import datetime as _dt
    import json as _json
    timestamp  = _dt.now().strftime("%Y%m%d_%H%M%S")
    is_sqlite  = str(db.engine.url).startswith("sqlite")

    if is_sqlite:
        db_path = db.engine.url.database
        if not db_path or not os.path.exists(db_path):
            return jsonify({"error": "Database file not found on disk."}), 404
        with open(db_path, "rb") as f:
            data = f.read()
        response = make_response(data)
        response.headers["Content-Disposition"] = f"attachment; filename=smartqualihome_backup_{timestamp}.db"
        response.headers["Content-Type"] = "application/octet-stream"
        return response
    else:
        from sqlalchemy import inspect as sa_inspect
        inspector  = sa_inspect(db.engine)
        tables     = inspector.get_table_names()
        engine_tag = str(db.engine.url).split("://")[0]
        backup     = {"_meta": {"timestamp": timestamp, "engine": engine_tag}, "tables": {}}
        with db.engine.connect() as conn:
            for tbl in tables:
                cols = [c["name"] for c in inspector.get_columns(tbl)]
                rows = conn.execute(db.text(f"SELECT * FROM `{tbl}`")).fetchall()
                backup["tables"][tbl] = [dict(zip(cols, row)) for row in rows]
        payload  = _json.dumps(backup, default=str, indent=2).encode("utf-8")
        response = make_response(payload)
        response.headers["Content-Disposition"] = f"attachment; filename=smartqualihome_backup_{timestamp}.json"
        response.headers["Content-Type"] = "application/json"
        return response


# ── Agent: submit new property ────────────────────────────────────────────────

ALLOWED_IMG_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}


def _save_property_images(files):
    """Persist uploaded property images; return list of stored references."""
    saved = []
    for f in files:
        if f and getattr(f, "filename", None):
            raw_name = f.filename.strip()
            if not raw_name:
                continue
            clean = secure_filename(raw_name)
            ext = os.path.splitext(clean)[1].lower()
            if not ext:
                ext = os.path.splitext(raw_name)[1].lower()
            if ext not in ALLOWED_IMG_EXTS:
                continue
            try:
                if hasattr(f, "stream"):
                    f.stream.seek(0)
                stored = _store_image_file(f)
                if stored:
                    saved.append(stored)
            except Exception as exc:
                current_app.logger.warning("Failed to store property image %s: %s", raw_name, exc)
    return saved


def _generate_property_unit_id(prop_id):
    try:
        return f"U{int(prop_id):06d}"
    except Exception:
        return f"U{uuid.uuid4().hex[:6].upper()}"


def _clean_numeric_str(raw) -> str:
    """Normalize user-entered numeric text (strip thousands separators/currency marks)."""
    return str(raw if raw is not None else "").strip().replace(",", "").replace("₱", "")


def _compose_location_prefix(street: str, block: str, lot_no: str) -> str:
    parts = []
    street = (street or "").strip()
    block = (block or "").strip()
    lot_no = (lot_no or "").strip()
    if street:
        parts.append(street)
    if block:
        parts.append(f"Block {block}")
    if lot_no:
        parts.append(f"Lot {lot_no}")
    return ", ".join(parts)


def _compose_full_location(street: str, block: str, lot_no: str, tail: str) -> str:
    prefix = _compose_location_prefix(street, block, lot_no)
    tail = (tail or "").strip()
    if prefix and tail:
        return f"{prefix}, {tail}"
    return prefix or tail


def _find_property_with_same_block_lot(street: str, block: str, lot_no: str, subdivision_id: int | None = None, exclude_property_id: int | None = None) -> Property | None:
    street_norm = (street or "").strip().lower()
    block_norm = (block or "").strip().lower()
    lot_norm = (lot_no or "").strip().lower()
    if not street_norm or not block_norm or not lot_norm:
        return None

    q = Property.query.filter(
        db.func.lower(db.func.trim(Property.street)) == street_norm,
        db.func.lower(db.func.trim(Property.block)) == block_norm,
        db.func.lower(db.func.trim(Property.lot_no)) == lot_norm,
    )
    if subdivision_id is None:
        q = q.filter(Property.subdivision_id.is_(None))
    else:
        q = q.filter(Property.subdivision_id == int(subdivision_id))
    if exclude_property_id:
        q = q.filter(Property.id != int(exclude_property_id))
    return q.first()


def _find_property_with_unit_id(unit_id: str, exclude_property_id: int | None = None) -> Property | None:
    unit_norm = (unit_id or "").strip().lower()
    if not unit_norm:
        return None
    q = Property.query.filter(db.func.lower(db.func.trim(Property.unit_id)) == unit_norm)
    if exclude_property_id:
        q = q.filter(Property.id != int(exclude_property_id))
    return q.first()


@main_bp.route("/admin/property/check-block-lot", methods=["GET"])
@login_required
def admin_check_property_block_lot():
    if current_user.role != "admin":
        return jsonify(ok=False, error="Forbidden"), 403

    street = (request.args.get("street") or "").strip()
    block = (request.args.get("block") or "").strip()
    lot_no = (request.args.get("lot_no") or "").strip()
    sub_raw = (request.args.get("subdivision_id") or "").strip()
    exclude_raw = (request.args.get("exclude_property_id") or "").strip()

    subdivision_id = None
    if sub_raw:
        try:
            subdivision_id = int(sub_raw)
        except (TypeError, ValueError):
            return jsonify(ok=False, error="Invalid subdivision id."), 400

    exclude_property_id = None
    if exclude_raw:
        try:
            exclude_property_id = int(exclude_raw)
        except (TypeError, ValueError):
            return jsonify(ok=False, error="Invalid property id."), 400

    if not street or not block or not lot_no:
        return jsonify(ok=True, exists=False)

    existing = _find_property_with_same_block_lot(
        street=street,
        block=block,
        lot_no=lot_no,
        subdivision_id=subdivision_id,
        exclude_property_id=exclude_property_id,
    )
    if not existing:
        return jsonify(ok=True, exists=False)

    return jsonify(
        ok=True,
        exists=True,
        property_id=existing.id,
        property_name=existing.name or "Property",
    )


@main_bp.route("/admin/property/create", methods=["POST"])
@main_bp.route("/agent/property/submit", methods=["POST"])
@login_required
def agent_submit_property():
    if current_user.role != "admin":
        return jsonify({"error": "Forbidden"}), 403

    name      = (request.form.get("name") or "").strip()
    street    = (request.form.get("street") or "").strip()
    block     = (request.form.get("block") or "").strip()
    lot_no    = (request.form.get("lot_no") or "").strip()
    region    = (request.form.get("region") or "").strip()
    region_code = (request.form.get("region_code") or "").strip()
    region_name = (request.form.get("region_name") or "").strip()
    province_code = (request.form.get("province_code") or "").strip()
    province_name = (request.form.get("province_name") or "").strip()
    citymun_code = (request.form.get("citymun_code") or "").strip()
    citymun_name = (request.form.get("citymun_name") or "").strip()
    barangay_code = (request.form.get("barangay_code") or "").strip()
    barangay_name = (request.form.get("barangay_name") or "").strip()
    prop_type = "house-and-lot"
    unit_type = (request.form.get("unit_type") or "").strip()
    price_str = (request.form.get("price") or "").strip()

    if not name or not unit_type or not price_str:
        return jsonify({"error": "Name, unit type, and total selling price are required."}), 400

    sub_id_raw = request.form.get("subdivision_id")
    try:
        price         = float(_clean_numeric_str(price_str))
        bedrooms      = int(request.form.get("bedrooms") or 0)
        bathrooms     = int(request.form.get("bathrooms") or 0)
        storeys       = int(request.form.get("storeys") or 1)
        floor_area_s  = _clean_numeric_str(request.form.get("floor_area"))
        lot_area_s    = _clean_numeric_str(request.form.get("lot_area"))
        sub_id_s      = (sub_id_raw or "").strip()
        promo_discount_rate = float(_clean_numeric_str(request.form.get("promo_discount_rate")) or 0)
        reservation_fee = float(_clean_numeric_str(request.form.get("reservation_fee")) or 0)
        downpayment_rate = float(_clean_numeric_str(request.form.get("downpayment_rate")) or 0)
        downpayment_terms_months = int(_clean_numeric_str(request.form.get("downpayment_terms_months")) or 0)
        loanable_percentage = float(_clean_numeric_str(request.form.get("loanable_percentage")) or 0)
        vat_rate = float(_clean_numeric_str(request.form.get("vat_rate")) or 0)
        lmf_rate = float(_clean_numeric_str(request.form.get("lmf_rate")) or 0)
        _air_raw = _clean_numeric_str(request.form.get("annual_interest_rate"))
        annual_interest_rate = float(_air_raw) if _air_raw else None
        floor_area    = float(floor_area_s) if floor_area_s else None
        lot_area      = float(lot_area_s)   if lot_area_s   else None
        subdivision_id = int(sub_id_s) if sub_id_s else None
    except (ValueError, TypeError) as exc:
        return jsonify({"error": f"Invalid numeric field: {exc}"}), 400

    description  = (request.form.get("description") or "").strip()
    image_names  = _save_property_images(request.files.getlist("images"))

    subdivision = db.session.get(Subdivision, subdivision_id) if subdivision_id else None
    if subdivision_id and not subdivision:
        return jsonify({"error": "Selected subdivision was not found."}), 404

    if subdivision:
        region_code = (subdivision.region_code or "").strip()
        region_name = (subdivision.region_name or "").strip()
        province_code = (subdivision.province_code or "").strip()
        province_name = (subdivision.province_name or "").strip()
        citymun_code = (subdivision.citymun_code or "").strip()
        citymun_name = (subdivision.citymun_name or "").strip()
        barangay_code = (subdivision.barangay_code or "").strip()
        barangay_name = (subdivision.barangay_name or "").strip()
        region = region_name
        tail = (subdivision.location or "").strip()
    else:
        tail = ", ".join([p for p in [barangay_name, citymun_name, province_name, region_name] if p])
        if not region:
            region = region_name

    location = _compose_full_location(street, block, lot_no, tail)
    if not location:
        return jsonify({"error": "Please provide Street, Block, Lot, or PSGC location details."}), 400

    duplicate_block_lot = _find_property_with_same_block_lot(street, block, lot_no, subdivision_id)
    if duplicate_block_lot:
        return jsonify({
            "error": f"Block {block} and Lot {lot_no} are already used by {duplicate_block_lot.name or 'another model'}."
        }), 409

    unit_id_raw = (request.form.get("unit_id") or "").strip()
    if unit_id_raw:
        existing_unit = _find_property_with_unit_id(unit_id_raw)
        if existing_unit:
            return jsonify({
                "error": f"Unit ID \"{unit_id_raw}\" is already used by {existing_unit.name or 'another model'}."
            }), 409
    else:
        unit_id_raw = None

    agent_id_raw = (request.form.get("agent_id") or "").strip()
    assigned_agent = None
    if agent_id_raw:
        try:
            assigned_agent = db.session.get(User, int(agent_id_raw))
        except (TypeError, ValueError):
            assigned_agent = None

    status_raw = (request.form.get("status") or "available").strip().lower()
    if status_raw not in ("available", "reserved", "sold"):
        status_raw = "available"

    prop = Property(
        name=name,
        street=street or None,
        block=block or None,
        lot_no=lot_no or None,
        location=location,
        region=region or None,
        region_code=region_code or None,
        region_name=region_name or None,
        province_code=province_code or None,
        province_name=province_name or None,
        citymun_code=citymun_code or None,
        citymun_name=citymun_name or None,
        barangay_code=barangay_code or None,
        barangay_name=barangay_name or None,
        prop_type=prop_type, price=price,
        unit_type=unit_type,
        unit_id=unit_id_raw,
        promo_discount_rate=promo_discount_rate,
        reservation_fee=reservation_fee,
        downpayment_rate=downpayment_rate,
        downpayment_terms_months=downpayment_terms_months,
        loanable_percentage=loanable_percentage,
        vat_rate=vat_rate,
        lmf_rate=lmf_rate,
        annual_interest_rate=annual_interest_rate,
        bedrooms=bedrooms, bathrooms=bathrooms, storeys=storeys,
        floor_area=floor_area, lot_area=lot_area,
        description=description,
        images=",".join(image_names) if image_names else None,
        agent_id=(assigned_agent.id if assigned_agent else None),
        subdivision_id=subdivision_id,
        status=status_raw,
        approval_status="approved",
    )
    db.session.add(prop)
    db.session.flush()
    if not prop.unit_id:
        prop.unit_id = _generate_property_unit_id(prop.id)
    log_activity("prop_submit", f"Property created by admin: \"{name}\"")
    db.session.commit()
    return jsonify({
        "success": True,
        "id": prop.id,
        "property": {
            "id": prop.id,
            "name": prop.name,
            "street": prop.street or "",
            "block": prop.block or "",
            "lot_no": prop.lot_no or "",
            "location": prop.location,
            "region": prop.region,
            "region_code": prop.region_code,
            "region_name": prop.region_name,
            "province_code": prop.province_code,
            "province_name": prop.province_name,
            "citymun_code": prop.citymun_code,
            "citymun_name": prop.citymun_name,
            "barangay_code": prop.barangay_code,
            "barangay_name": prop.barangay_name,
            "prop_type": prop.prop_type,
            "unit_type": prop.unit_type,
            "price": float(prop.price) if prop.price is not None else None,
            "promo_discount_rate": float(prop.promo_discount_rate) if prop.promo_discount_rate is not None else 0,
            "reservation_fee": float(prop.reservation_fee) if prop.reservation_fee is not None else 0,
            "downpayment_rate": float(prop.downpayment_rate) if prop.downpayment_rate is not None else 0,
            "downpayment_terms_months": prop.downpayment_terms_months,
            "loanable_percentage": float(prop.loanable_percentage) if prop.loanable_percentage is not None else 0,
            "vat_rate": float(prop.vat_rate) if prop.vat_rate is not None else 0,
            "lmf_rate": float(prop.lmf_rate) if prop.lmf_rate is not None else 0,
            "annual_interest_rate": float(prop.annual_interest_rate) if prop.annual_interest_rate is not None else None,
            "unit_id": prop.unit_id or "",
            "bedrooms": prop.bedrooms,
            "bathrooms": prop.bathrooms,
            "storeys": prop.storeys,
            "floor_area": float(prop.floor_area) if prop.floor_area is not None else None,
            "lot_area": float(prop.lot_area) if prop.lot_area is not None else None,
            "description": prop.description,
            "subdivision_id": prop.subdivision_id,
            "subdivision_name": (prop.subdivision.name if prop.subdivision else ""),
            "agent_name": (prop.agent.full_name if prop.agent else ""),
            "approval_status": (prop.approval_status or "approved"),
            "listing_status": (prop.status or "available"),
            "created_at": (prop.created_at.strftime("%b %d, %Y") if prop.created_at else ""),
            "images": (prop.images or ""),
        }
    })


# ── Agent: edit existing property ─────────────────────────────────────────────

@main_bp.route("/agent/property/<int:prop_id>/edit", methods=["POST"])
@login_required
def agent_edit_property(prop_id):
    if current_user.role != "admin":
        return jsonify({"error": "Forbidden"}), 403
    prop = db.session.get(Property, prop_id)
    if not prop:
        return jsonify({"error": "Not found"}), 404

    name      = (request.form.get("name") or "").strip()
    street    = (request.form.get("street") or "").strip()
    block     = (request.form.get("block") or "").strip()
    lot_no    = (request.form.get("lot_no") or "").strip()
    region    = (request.form.get("region") or "").strip()
    region_code = (request.form.get("region_code") or "").strip()
    region_name = (request.form.get("region_name") or "").strip()
    province_code = (request.form.get("province_code") or "").strip()
    province_name = (request.form.get("province_name") or "").strip()
    citymun_code = (request.form.get("citymun_code") or "").strip()
    citymun_name = (request.form.get("citymun_name") or "").strip()
    barangay_code = (request.form.get("barangay_code") or "").strip()
    barangay_name = (request.form.get("barangay_name") or "").strip()
    prop_type = "house-and-lot"
    unit_type = (request.form.get("unit_type") or "").strip()
    price_str = (request.form.get("price") or "").strip()

    if not name or not unit_type or not price_str:
        return jsonify({"error": "Name, unit type, and total selling price are required."}), 400

    sub_id_raw = request.form.get("subdivision_id")
    try:
        price         = float(_clean_numeric_str(price_str))
        bedrooms      = int(request.form.get("bedrooms") or 0)
        bathrooms     = int(request.form.get("bathrooms") or 0)
        storeys       = int(request.form.get("storeys") or 1)
        floor_area_s  = _clean_numeric_str(request.form.get("floor_area"))
        lot_area_s    = _clean_numeric_str(request.form.get("lot_area"))
        sub_id_s      = (sub_id_raw or "").strip()
        promo_discount_rate = float(_clean_numeric_str(request.form.get("promo_discount_rate") or str(prop.promo_discount_rate or 0)) or 0)
        reservation_fee = float(_clean_numeric_str(request.form.get("reservation_fee") or str(prop.reservation_fee or 0)) or 0)
        downpayment_rate = float(_clean_numeric_str(request.form.get("downpayment_rate") or str(prop.downpayment_rate or 20)) or 20)
        downpayment_terms_months = int(_clean_numeric_str(request.form.get("downpayment_terms_months") or str(prop.downpayment_terms_months or 24)) or 24)
        loanable_percentage = float(_clean_numeric_str(request.form.get("loanable_percentage") or str(prop.loanable_percentage or 80)) or 80)
        vat_rate = float(_clean_numeric_str(request.form.get("vat_rate") or str(prop.vat_rate or 12)) or 12)
        lmf_rate = float(_clean_numeric_str(request.form.get("lmf_rate") or str(prop.lmf_rate or 10)) or 10)
        if "annual_interest_rate" in request.form:
            _air_raw = _clean_numeric_str(request.form.get("annual_interest_rate"))
            annual_interest_rate = float(_air_raw) if _air_raw else None
        else:
            annual_interest_rate = prop.annual_interest_rate
        floor_area    = float(floor_area_s) if floor_area_s else None
        lot_area      = float(lot_area_s)   if lot_area_s   else None
        subdivision_id = int(sub_id_s) if sub_id_s else None
    except (ValueError, TypeError) as exc:
        return jsonify({"error": f"Invalid numeric field: {exc}"}), 400

    subdivision = db.session.get(Subdivision, subdivision_id) if subdivision_id else None
    if subdivision_id and not subdivision:
        return jsonify({"error": "Selected subdivision was not found."}), 404

    if subdivision:
        region_code = (subdivision.region_code or "").strip()
        region_name = (subdivision.region_name or "").strip()
        province_code = (subdivision.province_code or "").strip()
        province_name = (subdivision.province_name or "").strip()
        citymun_code = (subdivision.citymun_code or "").strip()
        citymun_name = (subdivision.citymun_name or "").strip()
        barangay_code = (subdivision.barangay_code or "").strip()
        barangay_name = (subdivision.barangay_name or "").strip()
        region = region_name
        tail = (subdivision.location or "").strip()
    else:
        tail = ", ".join([p for p in [barangay_name, citymun_name, province_name, region_name] if p])
        if not region:
            region = region_name

    location = _compose_full_location(street, block, lot_no, tail)
    if not location:
        return jsonify({"error": "Please provide Street, Block, Lot, or PSGC location details."}), 400

    duplicate_block_lot = _find_property_with_same_block_lot(street, block, lot_no, subdivision_id, exclude_property_id=prop.id)
    if duplicate_block_lot:
        return jsonify({
            "error": f"Block {block} and Lot {lot_no} are already used by {duplicate_block_lot.name or 'another model'}."
        }), 409

    unit_id_raw = (request.form.get("unit_id") or "").strip()
    if unit_id_raw and unit_id_raw.lower() != (prop.unit_id or "").strip().lower():
        existing_unit = _find_property_with_unit_id(unit_id_raw, exclude_property_id=prop.id)
        if existing_unit:
            return jsonify({
                "error": f"Unit ID \"{unit_id_raw}\" is already used by {existing_unit.name or 'another model'}."
            }), 409

    prop.name         = name
    prop.street       = street or None
    prop.block        = block or None
    prop.lot_no       = lot_no or None
    prop.location     = location
    prop.region       = region or None
    if region_code or region_name or province_code or province_name or citymun_code or citymun_name or barangay_code or barangay_name:
        prop.region_code = region_code or None
        prop.region_name = region_name or None
        prop.province_code = province_code or None
        prop.province_name = province_name or None
        prop.citymun_code = citymun_code or None
        prop.citymun_name = citymun_name or None
        prop.barangay_code = barangay_code or None
        prop.barangay_name = barangay_name or None
    prop.prop_type    = prop_type
    prop.unit_type    = unit_type
    prop.price        = price
    prop.promo_discount_rate = promo_discount_rate
    prop.reservation_fee = reservation_fee
    prop.downpayment_rate = downpayment_rate
    prop.downpayment_terms_months = downpayment_terms_months
    prop.loanable_percentage = loanable_percentage
    prop.vat_rate = vat_rate
    prop.lmf_rate = lmf_rate
    prop.annual_interest_rate = annual_interest_rate
    prop.bedrooms     = bedrooms
    prop.bathrooms    = bathrooms
    prop.storeys      = storeys
    prop.floor_area   = floor_area
    prop.lot_area     = lot_area
    prop.description  = (request.form.get("description") or "").strip()
    if sub_id_raw is not None:
        prop.subdivision_id = subdivision_id
    agent_id_raw = (request.form.get("agent_id") or "").strip()
    if agent_id_raw:
        try:
            assigned_agent = db.session.get(User, int(agent_id_raw))
            if assigned_agent and assigned_agent.role == "agent":
                prop.agent_id = assigned_agent.id
        except (TypeError, ValueError):
            pass

    prop.approval_status = "approved"
    status_raw = (request.form.get("status") or "").strip().lower()
    if status_raw in ("available", "reserved", "sold"):
        prop.status = status_raw
    if unit_id_raw:
        prop.unit_id = unit_id_raw
    elif not prop.unit_id:
        prop.unit_id = _generate_property_unit_id(prop.id)

    # Handle image removals
    existing   = [x for x in (prop.images or "").split(",") if x]
    for fname in request.form.getlist("remove_images"):
        fname = fname.strip()
        if fname in existing:
            existing.remove(fname)
            _delete_stored_image(fname)

    new_names  = _save_property_images(request.files.getlist("images"))
    existing.extend(new_names)
    prop.images = ",".join(existing) if existing else None

    log_activity("prop_edit", f"Property updated by admin: \"{prop.name}\" (status: {prop.status})")
    db.session.commit()
    return jsonify({"success": True, "id": prop.id, "status": prop.status})


# ── Agent: delete own property ────────────────────────────────────────────────

@main_bp.route("/agent/property/<int:prop_id>/delete", methods=["POST"])
@login_required
def agent_delete_property(prop_id):
    if current_user.role != "admin":
        return jsonify({"error": "Forbidden"}), 403
    prop = db.session.get(Property, prop_id)
    if not prop:
        return jsonify({"error": "Not found"}), 404
    if (prop.status or "").lower() == "sold":
        return jsonify({"error": "Sold properties cannot be deleted to preserve sales history."}), 409
    # Remove stored images (Cloudinary or legacy local files)
    for fname in (prop.images or "").split(","):
        _delete_stored_image(fname)

    prop_name = prop.name
    db.session.delete(prop)
    log_activity("prop_delete", f"Property deleted by admin: \"{prop_name}\"")
    db.session.commit()
    return jsonify({"success": True})


@main_bp.route("/qualify/property/<int:prop_id>/request-full-details", methods=["POST"])
@login_required
def client_request_full_property_details(prop_id):
    if current_user.role != "client":
        return jsonify(ok=False, error="Forbidden"), 403

    prop = db.session.get(Property, prop_id)
    if not prop:
        return jsonify(ok=False, error="Property not found."), 404
    if (prop.status or "").lower() != "available":
        return jsonify(ok=False, error="Only available properties can receive detail requests."), 409

    latest_result = (QualificationResult.query
                     .filter_by(user_id=current_user.id)
                     .order_by(QualificationResult.created_at.desc())
                     .first())
    if not latest_result or latest_result.status not in ("Qualified", "Conditionally Qualified"):
        return jsonify(ok=False, error="You must be Qualified or Conditionally Qualified to request full details."), 403

    now_utc = datetime.now(timezone.utc)
    existing = PropertyPricingDetailRequest.query.filter_by(
        client_id=current_user.id,
        property_id=prop_id,
    ).first()

    if existing and existing.status == "pending":
        return jsonify(ok=True, status="pending")

    if not existing:
        existing = PropertyPricingDetailRequest(
            client_id=current_user.id,
            property_id=prop_id,
            status="pending",
        )
        db.session.add(existing)
        db.session.flush()
    else:
        existing.status = "pending"
        existing.agent_note = None
        existing.reviewed_by_agent_id = None
        existing.reviewed_at = None

    db.session.add(PropertyPricingDetailRequestHistory(
        request_id=existing.id,
        client_id=current_user.id,
        property_id=prop_id,
        status="pending",
        requested_at=now_utc,
    ))

    detail_msg = f"{current_user.full_name} requested full pricing details for \"{prop.name}\"."

    log_activity("full_details_request", detail_msg)
    db.session.commit()
    return jsonify(ok=True, status="pending")


@main_bp.route("/agent/property/<int:prop_id>/full-detail-requests")
@login_required
def agent_property_full_detail_requests(prop_id):
    if current_user.role not in ("agent", "admin"):
        return jsonify(ok=False, error="Forbidden"), 403

    prop = db.session.get(Property, prop_id)
    if not prop:
        return jsonify(ok=False, error="Property not found."), 404
    if current_user.role != "admin" and prop.agent_id != current_user.id:
        return jsonify(ok=False, error="Forbidden"), 403

    rows = (PropertyPricingDetailRequestHistory.query
            .filter_by(property_id=prop_id)
            .options(selectinload(PropertyPricingDetailRequestHistory.client))
            .order_by(PropertyPricingDetailRequestHistory.requested_at.desc(),
                      PropertyPricingDetailRequestHistory.id.desc())
            .all())

    items = []
    for row in rows:
        client_obj = row.client or db.session.get(User, row.client_id)
        items.append({
            "id": row.id,
            "request_id": row.request_id,
            "client_name": client_obj.full_name if client_obj else "Client",
            "client_id": row.client_id,
            "status": row.status,
            "agent_note": row.agent_note or "",
            "created_at": row.requested_at.strftime("%b %d, %Y %I:%M %p") if row.requested_at else "",
            "reviewed_at": row.reviewed_at.strftime("%b %d, %Y %I:%M %p") if row.reviewed_at else "",
        })

    return jsonify(ok=True, property_id=prop_id, property_name=prop.name, requests=items)


@main_bp.route("/agent/full-details-request/<int:req_id>/approve", methods=["POST"])
@login_required
def agent_approve_full_details_request(req_id):
    if current_user.role != "admin":
        return jsonify(ok=False, error="Forbidden"), 403

    req_row = db.session.get(PropertyPricingDetailRequest, req_id)
    if not req_row:
        return jsonify(ok=False, error="Request not found."), 404
    if req_row.property_item and req_row.property_item.agent_id and req_row.property_item.agent_id != current_user.id and current_user.role != "admin":
        return jsonify(ok=False, error="Forbidden"), 403

    data = request.get_json(silent=True) or {}
    req_row.status = "approved"
    req_row.agent_note = (data.get("note") or "").strip() or None
    req_row.reviewed_by_agent_id = current_user.id
    req_row.reviewed_at = datetime.now(timezone.utc)
    hist_row = (PropertyPricingDetailRequestHistory.query
                .filter_by(request_id=req_row.id, status="pending")
                .order_by(PropertyPricingDetailRequestHistory.id.desc())
                .first())
    if hist_row:
        hist_row.status = "approved"
        hist_row.agent_note = req_row.agent_note
        hist_row.reviewed_by_agent_id = current_user.id
        hist_row.reviewed_at = req_row.reviewed_at
    log_activity("full_details_approved", f"Full details approved for {req_row.client.full_name} on \"{req_row.property_item.name}\"")
    db.session.commit()
    return jsonify(ok=True, status="approved", request_id=req_row.id)


@main_bp.route("/agent/full-details-request/<int:req_id>/reject", methods=["POST"])
@login_required
def agent_reject_full_details_request(req_id):
    if current_user.role != "admin":
        return jsonify(ok=False, error="Forbidden"), 403

    req_row = db.session.get(PropertyPricingDetailRequest, req_id)
    if not req_row:
        return jsonify(ok=False, error="Request not found."), 404
    if req_row.property_item and req_row.property_item.agent_id and req_row.property_item.agent_id != current_user.id and current_user.role != "admin":
        return jsonify(ok=False, error="Forbidden"), 403

    data = request.get_json(silent=True) or {}
    req_row.status = "rejected"
    req_row.agent_note = (data.get("note") or "").strip() or None
    req_row.reviewed_by_agent_id = current_user.id
    req_row.reviewed_at = datetime.now(timezone.utc)
    hist_row = (PropertyPricingDetailRequestHistory.query
                .filter_by(request_id=req_row.id, status="pending")
                .order_by(PropertyPricingDetailRequestHistory.id.desc())
                .first())
    if hist_row:
        hist_row.status = "rejected"
        hist_row.agent_note = req_row.agent_note
        hist_row.reviewed_by_agent_id = current_user.id
        hist_row.reviewed_at = req_row.reviewed_at
    log_activity("full_details_rejected", f"Full details request rejected for {req_row.client.full_name} on \"{req_row.property_item.name}\"")
    db.session.commit()
    return jsonify(ok=True, status="rejected", request_id=req_row.id)


@main_bp.route("/qualify/property/<int:prop_id>/detail-status")
@login_required
def client_property_detail_status(prop_id):
    if current_user.role != "client":
        return jsonify(ok=False, error="Forbidden"), 403

    req = PropertyPricingDetailRequest.query.filter_by(
        client_id=current_user.id,
        property_id=prop_id
    ).first()

    note = (req.agent_note or "").strip() if req else ""
    if not note:
        hist = (PropertyPricingDetailRequestHistory.query
                .filter_by(client_id=current_user.id, property_id=prop_id)
                .order_by(PropertyPricingDetailRequestHistory.id.desc())
                .first())
        if hist and hist.agent_note:
            note = (hist.agent_note or "").strip()

    status = (req.status if req else "none").lower()
    is_approved = (status == "approved")

    return jsonify(
        ok=True,
        property_id=prop_id,
        status=status,
        approved=is_approved,
        note=note
    )


@main_bp.route("/agent/full-details-history/<int:history_id>/delete", methods=["POST"])
@login_required
def agent_delete_full_details_history(history_id):
    if current_user.role != "admin":
        return jsonify(ok=False, error="Forbidden"), 403

    hist = db.session.get(PropertyPricingDetailRequestHistory, history_id)
    if not hist:
        return jsonify(ok=False, error="Request history not found."), 404

    prop = hist.property_item or db.session.get(Property, hist.property_id)
    if not prop:
        return jsonify(ok=False, error="Property not found."), 404

    client_id = hist.client_id
    property_id = hist.property_id
    client_obj = hist.client or db.session.get(User, client_id)
    client_name = client_obj.full_name if client_obj else f"Client #{client_id}"

    # Delete history record
    db.session.delete(hist)
    db.session.flush()

    # Also delete the active PropertyPricingDetailRequest for this client and property!
    # This immediately revokes the client's "View Full Details" access and resets it back to "Request Full Details".
    PropertyPricingDetailRequest.query.filter_by(
        client_id=client_id,
        property_id=property_id
    ).delete(synchronize_session=False)

    log_activity(
        "full_details_deleted",
        f"Full details request deleted for {client_name} on \"{prop.name}\". Full details access revoked and reset."
    )

    db.session.commit()
    return jsonify(ok=True, deleted_id=history_id, client_id=client_id, property_id=property_id)


# ── Agent: approve / reject tripping request ──────────────────────────────────

@main_bp.route("/agent/trip/<int:trip_id>/approve", methods=["POST"])
@login_required
def agent_approve_trip(trip_id):
    if current_user.role != "admin":
        return jsonify({"error": "Forbidden"}), 403
    trip = db.session.get(TrippingRequest, trip_id)
    if not trip:
        return jsonify({"error": "Not found"}), 404
    if trip.property_item.agent_id != current_user.id and current_user.role != "admin":
        return jsonify({"error": "Forbidden"}), 403
    if trip.property_item.status == "sold" or trip.sale_record is not None:
        return jsonify({"error": "Cannot approve a request for a sold property."}), 409

    data = request.get_json(silent=True) or {}
    agent_id_raw = str(data.get("agent_id") or "").strip()
    if not agent_id_raw:
        return jsonify({"error": "Please select an available agent."}), 400
    try:
        agent_id = int(agent_id_raw)
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid agent selection."}), 400

    assigned_agent = db.session.get(User, agent_id)
    if not assigned_agent or assigned_agent.role != "agent" or not assigned_agent.is_active:
        return jsonify({"error": "Selected agent is invalid or inactive."}), 400

    availability_row = (AgentAvailability.query
                        .filter_by(agent_id=assigned_agent.id,
                                   available_date=trip.preferred_date,
                                   availability_status="available")
                        .first())
    if not availability_row:
        return jsonify({"error": "Selected agent is not available on the requested date."}), 400

    trip.status     = "approved"
    trip.agent_note = (data.get("note") or "").strip()
    if trip.property_item:
        trip.property_item.agent_id = assigned_agent.id
    db.session.add(AgentNotification(
        agent_id=assigned_agent.id,
        property_id=trip.property_id,
        event_type="trip_assignment",
        message=f'Tripping assigned: {trip.client.full_name} • "{trip.property_item.name}" on {trip.preferred_date.strftime("%b %d, %Y")}.',
    ))
    db.session.add(AgentNotification(
        agent_id=trip.client_id,
        property_id=trip.property_id,
        event_type="trip_approved_client",
        message=f'Your tripping request for "{trip.property_item.name}" was approved for {trip.preferred_date.strftime("%b %d, %Y")}.',
    ))
    log_activity(
        "trip_approve",
        f"Tripping approved and assigned to {assigned_agent.full_name}: {trip.client.full_name} → \"{trip.property_item.name}\""
    )
    db.session.commit()
    return jsonify({"success": True, "trip_id": trip.id, "status": "approved", "agent_id": assigned_agent.id})


@main_bp.route("/agent/trip/<int:trip_id>/reject", methods=["POST"])
@login_required
def agent_reject_trip(trip_id):
    if current_user.role != "admin":
        return jsonify({"error": "Forbidden"}), 403
    trip = db.session.get(TrippingRequest, trip_id)
    if not trip:
        return jsonify({"error": "Not found"}), 404
    if trip.property_item.agent_id != current_user.id and current_user.role != "admin":
        return jsonify({"error": "Forbidden"}), 403

    data = request.get_json(silent=True) or {}
    trip.status     = "rejected"
    trip.agent_note = (data.get("note") or "").strip()
    log_activity(
        "trip_reject",
        f"Tripping rejected: {trip.client.full_name} → \"{trip.property_item.name}\""
    )
    db.session.commit()
    return jsonify({"success": True, "trip_id": trip.id, "status": "rejected"})


@main_bp.route("/agent/trip/<int:trip_id>/mark-visited", methods=["POST"])
@login_required
def agent_mark_trip_visited(trip_id):
    if current_user.role != "admin":
        return jsonify({"error": "Forbidden"}), 403
    trip = db.session.get(TrippingRequest, trip_id)
    if not trip:
        return jsonify({"error": "Not found"}), 404
    if trip.property_item.agent_id != current_user.id and current_user.role != "admin":
        return jsonify({"error": "Forbidden"}), 403
    if trip.status != "approved":
        return jsonify({"error": "Only approved trips can be marked as visited."}), 400

    data = request.get_json(silent=True) or {}
    note = (data.get("note") or "").strip()

    trip.status = "visited"
    if note:
        trip.agent_note = note

    if trip.property_item and trip.property_item.agent_id:
        db.session.add(AgentNotification(
            agent_id=trip.property_item.agent_id,
            property_id=trip.property_id,
            event_type="trip_visited",
            message=f'Trip visited: {trip.client.full_name} completed visit for "{trip.property_item.name}".',
        ))

    log_activity(
        "trip_visit",
        f"Trip marked as visited: {trip.client.full_name} -> \"{trip.property_item.name}\""
    )
    db.session.commit()
    return jsonify({
        "success": True,
        "trip_id": trip.id,
        "status": "visited",
        "note": trip.agent_note or "Trip marked as visited by admin.",
    })


def _build_fallback_purchase_payload(trip_row):
    client = trip_row.client
    profile = client.profile if client else None
    prop_row = trip_row.property_item
    assigned_agent_name = (
        prop_row.agent.full_name
        if prop_row and prop_row.agent and str(prop_row.agent.role or '').lower() == 'agent'
        else ''
    )
    selling_price = float(prop_row.price or 0) if prop_row else 0.0
    reservation_fee = float(getattr(prop_row, "reservation_fee", 0) or 0) if prop_row else 0.0
    downpayment_rate = float(getattr(prop_row, "downpayment_rate", 0) or 0) if prop_row else 0.0
    loanable_pct = float(getattr(prop_row, "loanable_percentage", 0) or 0) if prop_row else 0.0
    downpayment = (selling_price * (downpayment_rate / 100.0)) if (selling_price > 0 and downpayment_rate > 0) else 0.0
    loan_amount = (
        (selling_price * (loanable_pct / 100.0))
        if (selling_price > 0 and loanable_pct > 0)
        else max(0.0, selling_price - downpayment)
    )

    def _fmt_php(value):
        try:
            n = float(value or 0)
        except Exception:
            n = 0.0
        return f"PHP {n:,.2f}" if n > 0 else ""

    return {
        "pbLastName": client.last_name if client else "",
        "pbFirstName": client.first_name if client else "",
        "pbMiddleName": client.middle_name if client else "",
        "pbEmailPersonal": client.email if client else "",
        "pbMobile": (profile.contact_number if profile and profile.contact_number else (client.contact_number if client else "")),
        "pbTelephone": profile.contact_no if profile and profile.contact_no else "",
        "pbBirthDate": profile.birth_date.strftime("%Y-%m-%d") if profile and profile.birth_date else "",
        "pbBirthPlace": profile.birthplace if profile and profile.birthplace else "",
        "pbCivilStatus": profile.civil_status if profile and profile.civil_status else "",
        "pbCitizenship": profile.citizenship if profile and profile.citizenship else "",
        "pbGender": profile.gender if profile and profile.gender else "",
        "pbTin": profile.tin_no if profile and profile.tin_no else "",
        "pbGrossIncome": str(profile.gross_income) if profile and profile.gross_income is not None else "",
        "pbDependentsChildren": str(profile.dependents) if profile and profile.dependents is not None else "",
        "pbHomeStreet": profile.street if profile and profile.street else "",
        "pbHomeNo": profile.blk if profile and profile.blk else "",
        "pbHomeSubdivision": profile.subdivision_name if profile and profile.subdivision_name else "",
        "pbHomeLocation": profile.address if profile and profile.address else "",
        "pbEmploymentType": profile.employment_type if profile and profile.employment_type else "",
        "loanUnitId": str(prop_row.unit_id) if prop_row and prop_row.unit_id is not None else "",
        "loanSellingPrice": _fmt_php(selling_price),
        "loanProcessingFee": "",
        "loanAmount": _fmt_php(loan_amount),
        "loanDownpayment": _fmt_php(downpayment),
        "loanReservationFee": _fmt_php(reservation_fee or 20000),
        "loanPromoDisc": "",
        "loanOrPrNo": "",
        "loanOrPrDate": "",
        "loanBookingOfficer": assigned_agent_name,
        "loanFinancing": "",
        "loanCashDays": "",
        "loanFinancingOther": "",
        "loanDownpaymentTerm": "",
        "loanDownpaymentTermOther": "",
        "loanTerm": "",
        "loanTermOther": "",
        "buyerConsentAccepted": True,
        "_fallback": True,
    }


def _parse_purchase_payload(raw):
    try:
        parsed = json.loads(raw or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _merge_loan_details_into_trip(trip, loan_details, status=None):
    current_payload = {}
    if (trip.purchase_form_data or "").strip():
        try:
            parsed = json.loads(trip.purchase_form_data)
            if isinstance(parsed, dict):
                current_payload = parsed
        except Exception:
            current_payload = {}
    else:
        current_payload = _build_fallback_purchase_payload(trip)

    if isinstance(loan_details, dict):
        for k, v in loan_details.items():
            if v is not None:
                current_payload[k] = str(v).strip()

    if status:
        current_payload["_purchase_form_status"] = status

    trip.purchase_form_data = json.dumps(current_payload, ensure_ascii=True)
    trip.purchase_form_submitted = True
    if not trip.purchase_form_submitted_at:
        trip.purchase_form_submitted_at = datetime.now(timezone.utc)


@main_bp.route("/agent/trip/<int:trip_id>/mark-bought", methods=["POST"])
@login_required
def agent_mark_trip_bought(trip_id):
    if current_user.role != "admin":
        return jsonify({"error": "Forbidden"}), 403
    trip = db.session.get(TrippingRequest, trip_id)
    if not trip:
        return jsonify({"error": "Not found"}), 404
    if trip.property_item.agent_id != current_user.id and current_user.role != "admin":
        return jsonify({"error": "Forbidden"}), 403
    if trip.status not in ("approved", "visited", "sold"):
        return jsonify({"error": "Only approved or visited trips can be marked as sold."}), 400

    data = request.get_json(silent=True) or {}
    existing = PropertySale.query.filter_by(property_id=trip.property_id).first()
    if existing:
        if existing.trip_id == trip.id:
            trip.status = "sold"
            loan_details = data.get("loan_details")
            if isinstance(loan_details, dict) and loan_details:
                _merge_loan_details_into_trip(trip, loan_details, status="approved")
            db.session.commit()
            return jsonify({"success": True, "trip_id": trip.id, "property_id": trip.property_id, "status": "sold"})
        return jsonify({"error": "This property has already been marked as sold."}), 409

    note = (data.get("note") or "").strip()
    try:
        if data.get("selling_price") not in (None, ""):
            selling_price = float(data.get("selling_price"))
            if selling_price <= 0:
                return jsonify({"error": "Selling price must be greater than 0."}), 400
        else:
            selling_price = float(trip.property_item.price or 0) if trip.property_item.price is not None else None
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid selling price."}), 400

    sale = PropertySale(
        property_id=trip.property_id,
        client_id=trip.client_id,
        trip_id=trip.id,
        agent_id=trip.property_item.agent_id,
        selling_price=selling_price,
        note=note or None,
    )
    db.session.add(sale)
    db.session.flush()

    latest_result = (QualificationResult.query
                     .filter_by(user_id=trip.client_id)
                     .order_by(QualificationResult.created_at.desc())
                     .first())
    profile = trip.client.profile
    gross_income = float(profile.gross_income) if (profile and profile.gross_income is not None) else 0.0
    monthly_loans = float(profile.monthly_loans) if (profile and profile.monthly_loans is not None) else 0.0
    dti_ratio = (monthly_loans / gross_income * 100.0) if gross_income > 0 else None
    if latest_result and latest_result.dti_ratio is not None:
        dti_ratio = float(latest_result.dti_ratio)

    db.session.add(HistoricalBuyerRecord(
        sale_id=sale.id,
        client_id=trip.client_id,
        property_id=trip.property_id,
        civil_status=(profile.civil_status if profile and profile.civil_status else None),
        dependents=(profile.dependents if profile and profile.dependents is not None else 0),
        age=(profile.age if profile and profile.age is not None else 30),
        employment_type=(profile.employment_type if profile and profile.employment_type else None),
        tenure_months=(profile.tenure_months if profile and profile.tenure_months is not None else 0),
        gross_income=(profile.gross_income if profile and profile.gross_income is not None else 0),
        monthly_loans=(profile.monthly_loans if profile and profile.monthly_loans is not None else 0),
        dti_ratio=(round(dti_ratio, 2) if dti_ratio is not None else None),
        outcome=(latest_result.status if latest_result and latest_result.status else "Conditionally Qualified"),
        notes=("Auto-captured from closed sale" if not note else f"Auto-captured from closed sale. Agent note: {note[:180]}"),
    ))

    # Auto-sync each closed sale to training_data so future retraining uses real buyer outcomes.
    auto_sync_record = HistoricalBuyerRecord(
        sale_id=sale.id,
        client_id=trip.client_id,
        property_id=trip.property_id,
        civil_status=(profile.civil_status if profile and profile.civil_status else None),
        dependents=(profile.dependents if profile and profile.dependents is not None else 0),
        age=(profile.age if profile and profile.age is not None else 30),
        employment_type=(profile.employment_type if profile and profile.employment_type else None),
        tenure_months=(profile.tenure_months if profile and profile.tenure_months is not None else 0),
        gross_income=(profile.gross_income if profile and profile.gross_income is not None else 0),
        monthly_loans=(profile.monthly_loans if profile and profile.monthly_loans is not None else 0),
        dti_ratio=(round(dti_ratio, 2) if dti_ratio is not None else None),
        outcome=(latest_result.status if latest_result and latest_result.status else "Conditionally Qualified"),
        notes=("Auto-captured from closed sale" if not note else f"Auto-captured from closed sale. Agent note: {note[:180]}"),
    )
    _sync_single_historical_to_training(
        auto_sync_record,
        extra_note=("Auto-synced from closed sale" if not note else f"Auto-synced from closed sale. Agent note: {note[:180]}"),
    )

    trip.property_item.status = "sold"
    trip.status = "sold"
    if note:
        trip.agent_note = note
    else:
        trip.agent_note = "Property marked as sold by admin."

    loan_details = data.get("loan_details")
    if isinstance(loan_details, dict) and loan_details:
        _merge_loan_details_into_trip(trip, loan_details, status="approved")
    elif not trip.purchase_form_data:
        trip.purchase_form_data = json.dumps(_build_fallback_purchase_payload(trip), ensure_ascii=True)
        trip.purchase_form_submitted = True
        if not trip.purchase_form_submitted_at:
            trip.purchase_form_submitted_at = datetime.now(timezone.utc)

    if trip.property_item and trip.property_item.agent_id:
        db.session.add(AgentNotification(
            agent_id=trip.property_item.agent_id,
            property_id=trip.property_id,
            event_type="trip_sold",
            message=f'Model sold: "{trip.property_item.name}" for client {trip.client.full_name}.',
        ))

    other_trips = TrippingRequest.query.filter(
        TrippingRequest.property_id == trip.property_id,
        TrippingRequest.id != trip.id,
        TrippingRequest.status.in_(["pending", "approved"]),
    ).all()
    for other in other_trips:
        other.status = "rejected"
        if not other.agent_note:
            other.agent_note = "Request closed because the property has been sold."

    log_activity(
        "sale_marked",
        f"Property sold: \"{trip.property_item.name}\" to {trip.client.full_name}"
    )
    db.session.commit()

    retrain_started = _trigger_c50_retrain_async("sale_close_auto_sync")
    if retrain_started:
        log_activity("c50_retrain_async", f"Background C5.0 retrain queued after closed sale #{sale.id}")

    return jsonify({
        "success": True,
        "trip_id": trip.id,
        "property_id": trip.property_id,
        "status": "sold",
        "note": trip.agent_note or "Property marked as sold by admin.",
        "c50_retrain_started": retrain_started,
    })


@main_bp.route("/admin/trip/<int:trip_id>/save-loan-details", methods=["POST"])
@login_required
def admin_save_loan_details(trip_id):
    if current_user.role != "admin":
        return jsonify(ok=False, error="Forbidden"), 403

    trip = db.session.get(TrippingRequest, trip_id)
    if not trip:
        return jsonify(ok=False, error="Trip request not found."), 404

    data = request.get_json(silent=True) or {}
    loan_details = data.get("loan_details")
    if not isinstance(loan_details, dict):
        return jsonify(ok=False, error="Missing loan details."), 400

    _merge_loan_details_into_trip(trip, loan_details, status=None)

    sp_raw = loan_details.get("loanSellingPrice")
    if sp_raw and trip.sale_record:
        try:
            sp_clean = float(re.sub(r"[^0-9.]", "", str(sp_raw)))
            if sp_clean > 0:
                trip.sale_record.selling_price = sp_clean
        except Exception:
            pass

    log_activity("loan_details_save", f"Loan details saved for Trip #{trip.id}: {trip.client.full_name if trip.client else 'Client'}")
    db.session.commit()
    return jsonify(ok=True, trip_id=trip.id, purchase_form_data=trip.purchase_form_data)


@main_bp.route("/admin/property/<int:prop_id>/purchase-list", methods=["GET"])
@login_required
def admin_property_purchase_list(prop_id):
    if current_user.role != "admin":
        return jsonify(ok=False, error="Forbidden"), 403

    prop = db.session.get(Property, prop_id)
    if not prop:
        return jsonify(ok=False, error="Property not found."), 404

    trips = (TrippingRequest.query
             .filter(TrippingRequest.property_id == prop_id)
             .options(
                 selectinload(TrippingRequest.client).selectinload(User.profile).options(
                     defer(UserProfile.avatar_data),
                     defer(UserProfile.banner_data),
                     defer(UserProfile.valid_id_data),
                     defer(UserProfile.income_proof_data),
                     defer(UserProfile.esignature_data),
                 ),
                 selectinload(TrippingRequest.property_item).selectinload(Property.agent),
                 selectinload(TrippingRequest.sale_record),
             )
             .order_by(TrippingRequest.created_at.desc())
             .all())

    trips = [t for t in trips if (t.status or "").lower() in ("visited", "sold") or t.sale_record is not None or bool(t.purchase_form_submitted)]

    buyer_form_notifs = (AgentNotification.query
                         .filter_by(agent_id=current_user.id, event_type="buyer_form_submit")
                         .order_by(AgentNotification.created_at.desc())
                         .all())
    buyer_form_logs = (ActivityLog.query
                       .filter_by(action="buyer_form_submit")
                       .order_by(ActivityLog.created_at.desc())
                       .all())
    notif_trip_ts = {}
    for notif in buyer_form_notifs:
        m = re.search(r"Trip\s+#(\d+)", str(notif.message or ""))
        if not m:
            continue
        tid = int(m.group(1))
        if tid not in notif_trip_ts:
            notif_trip_ts[tid] = notif.created_at
    for row in buyer_form_logs:
        m = re.search(r"Trip\s+#(\d+)", str(row.description or ""))
        if not m:
            continue
        tid = int(m.group(1))
        if tid not in notif_trip_ts:
            notif_trip_ts[tid] = row.created_at

    rows = []
    trip_backfilled = False

    _fallback_purchase_payload = _build_fallback_purchase_payload
    _parse_payload = _parse_purchase_payload

    for trip in trips:
        payload_raw = trip.purchase_form_data or ""
        payload_obj = _parse_payload(payload_raw)
        payload_status = str(payload_obj.get("_purchase_form_status") or "").strip().lower()

        has_submitted = bool(trip.purchase_form_submitted)
        submitted_at = trip.purchase_form_submitted_at
        if not has_submitted:
            inferred_ts = notif_trip_ts.get(int(trip.id))
            if inferred_ts and payload_status != "rejected":
                has_submitted = True
                submitted_at = inferred_ts
                trip.purchase_form_submitted = True
                if not trip.purchase_form_submitted_at:
                    trip.purchase_form_submitted_at = inferred_ts
                trip_backfilled = True

        submitted_label = "Not submitted yet"
        if has_submitted:
            submitted_label = (
                f"Submitted {submitted_at.strftime('%b %d, %Y %I:%M %p')}" if submitted_at else "Submitted"
            )
        purchase_status = "none"
        if bool(trip.sale_record):
            purchase_status = "approved"
        elif bool(has_submitted):
            purchase_status = "pending"
        elif payload_status == "rejected":
            purchase_status = "rejected"
        elif bool(payload_raw.strip()):
            purchase_status = "rejected"

        if has_submitted and not payload_raw:
            payload_raw = json.dumps(_fallback_purchase_payload(trip), ensure_ascii=True)
            payload_obj = _parse_payload(payload_raw)

        can_view_form = bool((payload_raw or "").strip())

        client_user = trip.client
        client_prof = client_user.profile if client_user else None
        client_phone = (
            (client_prof.contact_number if client_prof and client_prof.contact_number else None) or
            (client_prof.contact_no if client_prof and client_prof.contact_no else None) or
            (client_user.contact_number if client_user and client_user.contact_number else None) or
            ""
        )
        client_email = client_user.email if client_user and client_user.email else ""

        loan_unit = str(payload_obj.get("loanUnitId") or (trip.property_item.unit_id if trip.property_item and trip.property_item.unit_id else "") or "").strip()
        loan_price = str(payload_obj.get("loanSellingPrice") or "").strip()
        if not loan_price:
            if trip.sale_record and trip.sale_record.selling_price:
                loan_price = f"PHP {float(trip.sale_record.selling_price):,.2f}"
            elif trip.property_item and trip.property_item.price:
                loan_price = f"PHP {float(trip.property_item.price):,.2f}"

        selected_unit_price = ""
        if loan_unit and loan_price:
            selected_unit_price = f"Unit {loan_unit} • {loan_price}"
        elif loan_unit:
            selected_unit_price = f"Unit {loan_unit}"
        elif loan_price:
            selected_unit_price = loan_price
        else:
            selected_unit_price = "—"

        submitted_date_short = submitted_at.strftime('%b %d, %Y') if submitted_at else "—"
        visit_date_formatted = trip.preferred_date.strftime("%b %d, %Y") if trip.preferred_date else "—"

        rows.append({
            "trip_id": trip.id,
            "client_id": trip.client_id,
            "client_name": trip.client.full_name if trip.client else "Client",
            "client_email": client_email,
            "client_phone": client_phone,
            "selected_unit": loan_unit or "—",
            "selected_price": loan_price or "—",
            "selected_unit_price": selected_unit_price,
            "visit_date": visit_date_formatted,
            "submitted_date": submitted_date_short,
            "assigned_agent_name": (
                trip.property_item.agent.full_name
                if trip.property_item and trip.property_item.agent and str(trip.property_item.agent.role or '').lower() == 'agent'
                else ''
            ),
            "client_gross_income": (
                float(trip.client.profile.gross_income)
                if trip.client and trip.client.profile and trip.client.profile.gross_income is not None
                else None
            ),
            "esignature_url": url_for("main.admin_client_esignature", user_id=trip.client_id),
            "preferred_date": visit_date_formatted,
            "status": (trip.status or "pending").lower(),
            "is_sold": bool(trip.sale_record) or (trip.status or "").lower() == "sold",
            "purchase_form_submitted": bool(has_submitted),
            "purchase_form_label": submitted_label,
            "purchase_form_submitted_at": submitted_at.isoformat() if submitted_at else None,
            "purchase_form_data": payload_raw,
            "purchase_status": purchase_status,
            "can_view_form": can_view_form,
        })

    if trip_backfilled:
        db.session.commit()

    return jsonify(ok=True, property_id=prop.id, property_name=prop.name, rows=rows)


@main_bp.route("/admin/trip/<int:trip_id>/purchase-form-action", methods=["POST"])
@login_required
def admin_purchase_form_action(trip_id):
    if current_user.role != "admin":
        return jsonify(ok=False, error="Forbidden"), 403

    trip = db.session.get(TrippingRequest, trip_id)
    if not trip:
        return jsonify(ok=False, error="Trip request not found."), 404

    data = request.get_json(silent=True) or {}
    action = (data.get("action") or "").strip().lower()
    if action not in {"reject", "delete"}:
        return jsonify(ok=False, error="Invalid action."), 400

    if not bool(trip.purchase_form_submitted or (trip.purchase_form_data or "").strip()):
        return jsonify(ok=False, error="No submitted purchase form to process."), 409

    client_name = trip.client.full_name if trip.client else "Client"
    prop_name = trip.property_item.name if trip.property_item else f"Property #{trip.property_id}"

    if action == "reject":
        payload_obj = {}
        if (trip.purchase_form_data or "").strip():
            try:
                parsed = json.loads(trip.purchase_form_data)
                if isinstance(parsed, dict):
                    payload_obj = parsed
            except Exception:
                payload_obj = {}
        payload_obj["_purchase_form_status"] = "rejected"
        payload_obj["_purchase_form_rejected_at"] = datetime.now(timezone.utc).isoformat()
        trip.purchase_form_data = json.dumps(payload_obj, ensure_ascii=True)
        trip.purchase_form_submitted = False
        trip.purchase_form_submitted_at = None
        db.session.add(AgentNotification(
            agent_id=trip.client_id,
            property_id=trip.property_id,
            event_type="purchase_form_rejected",
            message=(f"Your submitted purchase form for {prop_name} (Trip #{trip.id}) was rejected by admin.")[:255],
            is_read=False,
        ))
        log_activity("purchase_form_reject", f"Purchase form rejected for Trip #{trip.id}: {client_name} -> {prop_name}")
    else:
        trip.purchase_form_submitted = False
        trip.purchase_form_submitted_at = None
        trip.purchase_form_data = None
        db.session.add(AgentNotification(
            agent_id=trip.client_id,
            property_id=trip.property_id,
            event_type="purchase_form_deleted",
            message=(f"Your submitted purchase form for {prop_name} (Trip #{trip.id}) was deleted by admin.")[:255],
            is_read=False,
        ))
        log_activity("purchase_form_delete", f"Purchase form deleted for Trip #{trip.id}: {client_name} -> {prop_name}")

    db.session.commit()
    return jsonify(ok=True, action=action, trip_id=trip.id)


@main_bp.route("/admin/client/<int:user_id>/esignature")
@login_required
def admin_client_esignature(user_id):
    if current_user.role != "admin":
        return "Forbidden", 403

    client = db.session.get(User, user_id)
    if not client or client.role != "client":
        return "Not Found", 404

    profile = client.profile
    if not profile or not profile.esignature_data:
        return "Not Found", 404

    filename = secure_filename(profile.esignature_filename or "e-signature")
    resp = make_response(profile.esignature_data)
    resp.headers["Content-Type"] = profile.esignature_mimetype or "application/octet-stream"
    resp.headers["Content-Disposition"] = f'inline; filename="{filename}"'
    resp.headers["Cache-Control"] = "no-store"
    return resp


@main_bp.route("/agent/trip/<int:trip_id>/delete", methods=["POST"])
@login_required
def agent_delete_trip(trip_id):
    if current_user.role != "admin":
        return jsonify({"error": "Forbidden"}), 403
    trip = db.session.get(TrippingRequest, trip_id)
    if not trip:
        return jsonify({"error": "Not found"}), 404
    if trip.property_item.agent_id != current_user.id and current_user.role != "admin":
        return jsonify({"error": "Forbidden"}), 403
    if trip.status == "pending":
        return jsonify({"error": "Only approved or rejected requests can be deleted."}), 400

    status = trip.status
    client_name = trip.client.full_name
    property_name = trip.property_item.name
    db.session.delete(trip)
    log_activity(
        "trip_delete",
        f"Tripping request deleted ({status}): {client_name} -> \"{property_name}\""
    )
    db.session.commit()
    return jsonify({"success": True, "trip_id": trip_id})


@main_bp.route("/agent/availability", methods=["GET", "POST"])
@login_required
def agent_availability_collection():
    if current_user.role not in ("agent", "admin"):
        return jsonify(ok=False, error="Forbidden"), 403

    if request.method == "GET":
        rows = (AgentAvailability.query
                .filter_by(agent_id=current_user.id)
                .order_by(AgentAvailability.available_date.asc(), AgentAvailability.start_time.asc())
                .all())
        items = [{
            "id": row.id,
            "available_date": row.available_date.strftime("%Y-%m-%d"),
            "availability_status": (row.availability_status or "available"),
            "start_time": row.start_time.strftime("%H:%M"),
            "end_time": row.end_time.strftime("%H:%M"),
            "notes": row.notes or "",
        } for row in rows]
        return jsonify(ok=True, items=items)

    data = request.get_json(silent=True) or {}
    dates_raw = data.get("available_dates")
    if isinstance(dates_raw, list):
        date_inputs = [str(v).strip() for v in dates_raw if str(v).strip()]
    else:
        date_inputs = [str(data.get("available_date") or "").strip()]
    date_inputs = sorted(set(date_inputs))

    status_raw = (data.get("availability_status") or "available").strip().lower()
    start_raw = (data.get("start_time") or "").strip()
    end_raw = (data.get("end_time") or "").strip()
    notes = (data.get("notes") or "").strip()

    if not date_inputs:
        return jsonify(ok=False, error="Please provide at least one availability date."), 400

    if status_raw not in ("available", "not_available"):
        return jsonify(ok=False, error="Invalid availability status."), 400

    try:
        available_dates = [date.fromisoformat(d) for d in date_inputs]
        if status_raw == "not_available":
            start_time = time.fromisoformat("00:00")
            end_time = time.fromisoformat("23:59")
        else:
            start_time = time.fromisoformat(start_raw)
            end_time = time.fromisoformat(end_raw)
    except ValueError:
        return jsonify(ok=False, error="Invalid date or time format."), 400

    today = datetime.now(timezone.utc).date()
    if any(d < today for d in available_dates):
        return jsonify(ok=False, error="Availability date cannot be in the past."), 400
    if status_raw == "available" and start_time >= end_time:
        return jsonify(ok=False, error="End time must be later than start time."), 400
    if status_raw == "not_available" and not notes:
        return jsonify(ok=False, error="Please provide a reason for not availability."), 400

    created_ids = []
    skipped = []
    for available_date in available_dates:
        overlap = (AgentAvailability.query
                   .filter(
                       AgentAvailability.agent_id == current_user.id,
                       AgentAvailability.available_date == available_date,
                       AgentAvailability.start_time < end_time,
                       AgentAvailability.end_time > start_time,
                   )
                   .first())
        if overlap:
            if status_raw == "not_available" or (overlap.availability_status or "available") == "not_available":
                skipped.append({
                    "available_date": available_date.strftime("%Y-%m-%d"),
                    "reason": "This date already has an availability state entry.",
                })
            else:
                skipped.append({
                    "available_date": available_date.strftime("%Y-%m-%d"),
                    "reason": "This time slot overlaps an existing availability entry.",
                })
            continue

        row = AgentAvailability(
            agent_id=current_user.id,
            available_date=available_date,
            availability_status=status_raw,
            start_time=start_time,
            end_time=end_time,
            notes=notes or None,
        )
        db.session.add(row)
        db.session.flush()
        created_ids.append(row.id)

    if not created_ids:
        return jsonify(ok=False, error="No availability entries were created due to conflicts.", skipped=skipped), 409

    db.session.commit()
    return jsonify(ok=True, ids=created_ids, created_count=len(created_ids), skipped=skipped)


@main_bp.route("/agent/availability/<int:slot_id>/delete", methods=["POST"])
@login_required
def agent_delete_availability(slot_id):
    if current_user.role not in ("agent", "admin"):
        return jsonify(ok=False, error="Forbidden"), 403
    row = db.session.get(AgentAvailability, slot_id)
    if not row:
        return jsonify(ok=False, error="Availability slot not found."), 404
    if current_user.role != "admin" and row.agent_id != current_user.id:
        return jsonify(ok=False, error="Forbidden"), 403
    db.session.delete(row)
    db.session.commit()
    return jsonify(ok=True, id=slot_id)


# ── Agent: save profile ───────────────────────────────────────────────────────

@main_bp.route("/agent/profile/save", methods=["POST"])
@login_required
def agent_profile_save():
    if current_user.role not in ("agent", "admin"):
        return jsonify({"error": "Forbidden"}), 403

    data           = request.get_json(silent=True) or {}
    first_name     = (data.get("first_name")     or "").strip()
    last_name      = (data.get("last_name")      or "").strip()
    email          = (data.get("email")          or "").strip()
    username       = (data.get("username")       or "").strip()
    contact_number = (data.get("contact_number") or "").strip()
    license_no     = (data.get("license_no")     or "").strip()
    contact_no     = (data.get("contact_no")     or "").strip()
    bio            = (data.get("bio")            or "").strip()
    old_password   = (data.get("old_password")   or "").strip()
    new_password   = (data.get("new_password")   or "").strip()

    if not first_name or not last_name or not username:
        return jsonify({"error": "First name, last name, and username are required."}), 400
    if len(username) < 3:
        return jsonify({"error": "Username must be at least 3 characters."}), 400
    if not re.fullmatch(r"[\w.]+", username):
        return jsonify({"error": "Username may contain only letters, numbers, dots, and underscores."}), 400

    if email:
        existing = User.query.filter(User.email == email, User.id != current_user.id).first()
        if existing:
            return jsonify({"error": "That email address is already in use."}), 400
        current_user.email = email

    existing_username = User.query.filter(User.username == username, User.id != current_user.id).first()
    if existing_username:
        return jsonify({"error": "That username is already taken."}), 400

    current_user.first_name     = first_name
    current_user.last_name      = last_name
    current_user.username       = username
    current_user.contact_number = contact_number

    agent_rec = current_user.profile
    if not agent_rec:
        agent_rec = UserProfile(user_id=current_user.id)
        db.session.add(agent_rec)
    agent_rec.license_no = license_no
    agent_rec.contact_no = contact_no
    agent_rec.bio        = bio

    if new_password:
        if not old_password:
            return jsonify({"error": "Current password is required to set a new password."}), 400
        if not current_user.check_password(old_password):
            return jsonify({"error": "Current password is incorrect."}), 400
        if len(new_password) < 6:
            return jsonify({"error": "New password must be at least 6 characters."}), 400
        current_user.set_password(new_password)

    log_activity("profile_update", f"Agent profile updated: {current_user.full_name}")
    db.session.commit()
    return jsonify({"success": True, "full_name": current_user.full_name})


# ── Agent: upload profile avatar ──────────────────────────────────────────────

ALLOWED_AVATAR_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}

@main_bp.route("/agent/profile/upload-avatar", methods=["POST"])
@login_required
def agent_upload_avatar():
    if current_user.role not in ("agent", "admin"):
        return jsonify({"error": "Forbidden"}), 403
    file = request.files.get("avatar")
    if not file or not file.filename:
        return jsonify({"error": "No file provided"}), 400
    ext = os.path.splitext(secure_filename(file.filename))[1].lower()
    if ext not in ALLOWED_AVATAR_EXTS:
        return jsonify({"error": "Unsupported file type. Use JPG, PNG, WEBP, or GIF."}), 400

    agent_rec = current_user.profile
    if not agent_rec:
        agent_rec = UserProfile(user_id=current_user.id)
        db.session.add(agent_rec)
    agent_rec.avatar_data     = file.read()
    agent_rec.avatar_mimetype = file.mimetype or "image/jpeg"
    db.session.commit()
    return jsonify({"success": True, "url": url_for("main.serve_agent_avatar", user_id=current_user.id)})


# ── Agent: upload profile banner ──────────────────────────────────────────────

@main_bp.route("/agent/profile/upload-banner", methods=["POST"])
@login_required
def agent_upload_banner():
    if current_user.role not in ("agent", "admin"):
        return jsonify({"error": "Forbidden"}), 403
    file = request.files.get("banner")
    if not file or not file.filename:
        return jsonify({"error": "No file provided"}), 400
    ext = os.path.splitext(secure_filename(file.filename))[1].lower()
    if ext not in ALLOWED_AVATAR_EXTS:
        return jsonify({"error": "Unsupported file type. Use JPG, PNG, WEBP, or GIF."}), 400

    agent_rec = current_user.profile
    if not agent_rec:
        agent_rec = UserProfile(user_id=current_user.id)
        db.session.add(agent_rec)
    agent_rec.banner_data     = file.read()
    agent_rec.banner_mimetype = file.mimetype or "image/jpeg"
    db.session.commit()
    return jsonify({"success": True, "url": url_for("main.serve_agent_banner", user_id=current_user.id)})


# ── Agent: serve avatar / banner from DB ──────────────────────────────────────

@main_bp.route("/agent/avatar/<int:user_id>")
def serve_agent_avatar(user_id):
    agent = UserProfile.query.filter_by(user_id=user_id).first()
    if not agent or not agent.avatar_data:
        return "", 404
    resp = make_response(agent.avatar_data)
    resp.headers["Content-Type"]  = agent.avatar_mimetype or "image/jpeg"
    resp.headers["Cache-Control"] = "public, max-age=86400"
    return resp


@main_bp.route("/agent/banner/<int:user_id>")
def serve_agent_banner(user_id):
    agent = UserProfile.query.filter_by(user_id=user_id).first()
    if not agent or not agent.banner_data:
        return "", 404
    resp = make_response(agent.banner_data)
    resp.headers["Content-Type"]  = agent.banner_mimetype or "image/jpeg"
    resp.headers["Cache-Control"] = "public, max-age=86400"
    return resp


# ── Client: serve avatar / banner from DB ──────────────────────────────────────

@main_bp.route("/client/avatar/<int:user_id>")
@login_required
def serve_client_avatar(user_id):
    if current_user.id != user_id and current_user.role not in ("admin", "agent"):
        return "", 403
    profile = UserProfile.query.filter_by(user_id=user_id).first()
    if not profile or not profile.avatar_data:
        return "", 404
    resp = make_response(profile.avatar_data)
    resp.headers["Content-Type"]  = profile.avatar_mimetype or "image/jpeg"
    resp.headers["Cache-Control"] = "private, max-age=3600"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


@main_bp.route("/client/banner/<int:user_id>")
@login_required
def serve_client_banner(user_id):
    if current_user.id != user_id and current_user.role not in ("admin", "agent"):
        return "", 403
    profile = UserProfile.query.filter_by(user_id=user_id).first()
    if not profile or not profile.banner_data:
        return "", 404
    resp = make_response(profile.banner_data)
    resp.headers["Content-Type"]  = profile.banner_mimetype or "image/jpeg"
    resp.headers["Cache-Control"] = "private, max-age=3600"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


# ── Agent: delete avatar / banner ─────────────────────────────────────────────

@main_bp.route("/agent/profile/delete-avatar", methods=["POST"])
@login_required
def agent_delete_avatar():
    if current_user.role not in ("agent", "admin"):
        return jsonify({"error": "Forbidden"}), 403
    agent_rec = current_user.profile
    if agent_rec:
        agent_rec.avatar_data     = None
        agent_rec.avatar_mimetype = None
        db.session.commit()
    return jsonify({"success": True})


@main_bp.route("/agent/profile/delete-banner", methods=["POST"])
@login_required
def agent_delete_banner():
    if current_user.role not in ("agent", "admin"):
        return jsonify({"error": "Forbidden"}), 403
    agent_rec = current_user.profile
    if agent_rec:
        agent_rec.banner_data     = None
        agent_rec.banner_mimetype = None
        db.session.commit()
    return jsonify({"success": True})


# ── Admin: save profile ───────────────────────────────────────────────────────

@main_bp.route("/admin/profile/save", methods=["POST"])
@login_required
def admin_profile_save():
    if current_user.role != "admin":
        return jsonify({"error": "Forbidden"}), 403

    data           = request.get_json(silent=True) or {}
    first_name     = (data.get("first_name")     or "").strip()
    last_name      = (data.get("last_name")      or "").strip()
    email          = (data.get("email")          or "").strip()
    username       = (data.get("username")       or "").strip()
    contact_number = (data.get("contact_number") or "").strip()
    old_password   = (data.get("old_password")   or "").strip()
    new_password   = (data.get("new_password")   or "").strip()

    if not first_name or not last_name or not username:
        return jsonify({"error": "First name, last name, and username are required."}), 400
    if len(username) < 3:
        return jsonify({"error": "Username must be at least 3 characters."}), 400
    if not re.fullmatch(r"[\w.]+", username):
        return jsonify({"error": "Username may contain only letters, numbers, dots, and underscores."}), 400

    if email:
        existing = User.query.filter(User.email == email, User.id != current_user.id).first()
        if existing:
            return jsonify({"error": "That email address is already in use."}), 400
        current_user.email = email

    existing_username = User.query.filter(User.username == username, User.id != current_user.id).first()
    if existing_username:
        return jsonify({"error": "That username is already taken."}), 400

    current_user.first_name     = first_name
    current_user.last_name      = last_name
    current_user.username       = username
    current_user.contact_number = contact_number

    if new_password:
        if not old_password:
            return jsonify({"error": "Current password is required to set a new password."}), 400
        if not current_user.check_password(old_password):
            return jsonify({"error": "Current password is incorrect."}), 400
        if len(new_password) < 6:
            return jsonify({"error": "New password must be at least 6 characters."}), 400
        current_user.set_password(new_password)

    log_activity("profile_update", f"Admin profile updated: {current_user.full_name}")
    db.session.commit()
    return jsonify({"success": True, "full_name": current_user.full_name})


# ── Admin: upload profile avatar ──────────────────────────────────────────────

@main_bp.route("/admin/profile/upload-avatar", methods=["POST"])
@login_required
def admin_upload_avatar():
    if current_user.role != "admin":
        return jsonify({"error": "Forbidden"}), 403
    file = request.files.get("avatar")
    if not file or not file.filename:
        return jsonify({"error": "No file provided"}), 400
    ext = os.path.splitext(secure_filename(file.filename))[1].lower()
    if ext not in ALLOWED_AVATAR_EXTS:
        return jsonify({"error": "Unsupported file type. Use JPG, PNG, WEBP, or GIF."}), 400

    admin_rec = current_user.profile
    if not admin_rec:
        admin_rec = UserProfile(user_id=current_user.id)
        db.session.add(admin_rec)
    admin_rec.avatar_data     = file.read()
    admin_rec.avatar_mimetype = file.mimetype or "image/jpeg"
    db.session.commit()
    return jsonify({"success": True, "url": url_for("main.serve_admin_avatar", user_id=current_user.id)})


# ── Admin: upload profile banner ──────────────────────────────────────────────

@main_bp.route("/admin/profile/upload-banner", methods=["POST"])
@login_required
def admin_upload_banner():
    if current_user.role != "admin":
        return jsonify({"error": "Forbidden"}), 403
    file = request.files.get("banner")
    if not file or not file.filename:
        return jsonify({"error": "No file provided"}), 400
    ext = os.path.splitext(secure_filename(file.filename))[1].lower()
    if ext not in ALLOWED_AVATAR_EXTS:
        return jsonify({"error": "Unsupported file type. Use JPG, PNG, WEBP, or GIF."}), 400

    admin_rec = current_user.profile
    if not admin_rec:
        admin_rec = UserProfile(user_id=current_user.id)
        db.session.add(admin_rec)
    admin_rec.banner_data     = file.read()
    admin_rec.banner_mimetype = file.mimetype or "image/jpeg"
    db.session.commit()
    return jsonify({"success": True, "url": url_for("main.serve_admin_banner", user_id=current_user.id)})


# ── Admin: serve avatar / banner from DB ──────────────────────────────────────

@main_bp.route("/admin/avatar/<int:user_id>")
def serve_admin_avatar(user_id):
    admin_rec = UserProfile.query.filter_by(user_id=user_id).first()
    if not admin_rec or not admin_rec.avatar_data:
        return "", 404
    resp = make_response(admin_rec.avatar_data)
    resp.headers["Content-Type"]  = admin_rec.avatar_mimetype or "image/jpeg"
    resp.headers["Cache-Control"] = "public, max-age=86400"
    return resp


@main_bp.route("/admin/banner/<int:user_id>")
def serve_admin_banner(user_id):
    admin_rec = UserProfile.query.filter_by(user_id=user_id).first()
    if not admin_rec or not admin_rec.banner_data:
        return "", 404
    resp = make_response(admin_rec.banner_data)
    resp.headers["Content-Type"]  = admin_rec.banner_mimetype or "image/jpeg"
    resp.headers["Cache-Control"] = "public, max-age=86400"
    return resp


# ── Admin: delete avatar / banner ─────────────────────────────────────────────

@main_bp.route("/admin/profile/delete-avatar", methods=["POST"])
@login_required
def admin_delete_avatar():
    if current_user.role != "admin":
        return jsonify({"error": "Forbidden"}), 403
    admin_rec = current_user.profile
    if admin_rec:
        admin_rec.avatar_data     = None
        admin_rec.avatar_mimetype = None
        db.session.commit()
    return jsonify({"success": True})


@main_bp.route("/admin/profile/delete-banner", methods=["POST"])
@login_required
def admin_delete_banner():
    if current_user.role != "admin":
        return jsonify({"error": "Forbidden"}), 403
    admin_rec = current_user.profile
    if admin_rec:
        admin_rec.banner_data     = None
        admin_rec.banner_mimetype = None
        db.session.commit()
    return jsonify({"success": True})


# ── Agent: view client detail (scoped to agent's leads) ───────────────────────

@main_bp.route("/agent/client/<int:user_id>/profile")
@login_required
def agent_client_profile(user_id):
    if current_user.role not in ("agent", "admin"):
        return jsonify({"error": "Forbidden"}), 403

    # Agents may only view clients who have a trip on one of their properties
    if current_user.role == "agent":
        if not _agent_can_view_client(current_user.id, user_id):
            return jsonify({"error": "Access denied — client not connected to your listings."}), 403

    client = db.session.get(User, user_id)
    if not client or client.role != "client":
        return jsonify({"error": "Not found"}), 404

    result = (QualificationResult.query
              .filter_by(user_id=user_id)
              .order_by(QualificationResult.created_at.desc())
              .first())
    assessment_history = (QualificationResult.query
                          .filter_by(user_id=user_id)
                          .order_by(QualificationResult.created_at.desc())
                          .limit(5)
                          .all())
    cp = client.profile

    assessment_total = len(assessment_history)
    data: dict = {
        "id":             client.id,
        "first_name":     client.first_name,
        "middle_name":    client.middle_name,
        "last_name":      client.last_name,
        "full_name":      client.full_name,
        "initials":       client.full_name[:2].upper(),
        "avatar_url":      url_for("main.serve_client_avatar", user_id=client.id) if cp and cp.avatar_data else None,
        "username":       client.username or "—",
        "email":          client.email,
        "contact_number": client.contact_number or "—",
        "joined":         client.created_at.strftime("%b %d, %Y"),
        "joined_at":      client.created_at.strftime("%b %d, %Y %I:%M %p"),
        "assessment": {
            "date":         result.created_at.strftime("%b %d, %Y"),
            "status":       result.status,
            "assessment_mode": _normalize_assessment_mode(result.assessment_mode, "reassess") if result else "reassess",
            "dti":          f"{result.dti_ratio:.1f}%" if result.dti_ratio is not None else "—",
            "max_loanable": f"₱{float(result.max_loanable):,.0f}" if result.max_loanable else "—",
            "similarity":   ("High" if result.similarity_score >= 0.70 else ("Moderate" if result.similarity_score >= 0.40 else "Low")) if result and result.similarity_score is not None else "—",
        } if result else None,
        "assessments": [
            {
                "date": qr.created_at.strftime("%b %d, %Y"),
                "status": qr.status,
                "assessment_mode": _normalize_assessment_mode(
                    qr.assessment_mode,
                    "new" if idx == assessment_total - 1 else "reassess",
                ),
                "dti": f"{qr.dti_ratio:.1f}%" if qr.dti_ratio is not None else "—",
                "max_loanable": f"₱{float(qr.max_loanable):,.0f}" if qr.max_loanable else "—",
                "similarity": ("High" if qr.similarity_score >= 0.70 else ("Moderate" if qr.similarity_score >= 0.40 else "Low")) if qr.similarity_score is not None else "—",
            }
            for idx, qr in enumerate(assessment_history)
        ],
        "documents": {
            "has_valid_id": "Yes" if (cp and cp.has_valid_id is True) else ("No" if (cp and cp.has_valid_id is False) else "—"),
            "has_income_proof": "Yes" if (cp and cp.has_income_proof is True) else ("No" if (cp and cp.has_income_proof is False) else "—"),
        },
        "profile": {
            "civil_status":       (cp.civil_status or "—").replace("-", " ").title(),
            "citizenship":        (cp.citizenship or "—").replace("-", " ").title(),
            "gender":             (cp.gender or "—").replace("-", " ").title(),
            "dependents":         cp.dependents if cp and cp.dependents is not None else "—",
            "birth_date":         cp.birth_date.strftime("%b %d, %Y") if cp and cp.birth_date else "—",
            "birthplace":         cp.birthplace or "—",
            "employment_type":    (cp.employment_type or "—").replace("-", " ").title(),
            "employer_name":      cp.employer_name or "—",
            "employer_phone":     cp.employer_phone or "—",
            "employer_email":     cp.employer_email or "—",
            "employer_business_address": cp.employer_business_address or "—",
            "sss_gsis_umid":      cp.sss_gsis_umid or "—",
            "tin_no":             cp.tin_no or "—",
            "tenure_months":      cp.tenure_months if cp and cp.tenure_months is not None else "—",
            "gross_income":       f"₱{float(cp.gross_income):,.2f}" if cp and cp.gross_income else "—",
            "monthly_loans":      f"₱{float(cp.monthly_loans):,.2f}" if cp and cp.monthly_loans else "₱0.00",
            "other_deductions":   f"₱{float(cp.other_deductions):,.2f}" if cp and cp.other_deductions else "₱0.00",
            "preferred_type":     (cp.preferred_type or "—").replace("-", " ").title(),
            "budget_min":         f"₱{float(cp.budget_min):,.0f}" if cp and cp.budget_min else "—",
            "budget_max":         f"₱{float(cp.budget_max):,.0f}" if cp and cp.budget_max else "—",
            "address_line":       cp.address_line or "—",
            "street":             cp.street or "—",
            "blk":                cp.blk or "—",
            "lot":                cp.lot or "—",
            "subdivision_name":   cp.subdivision_name or "—",
            "country":            cp.country or "—",
            "zip_code":           cp.zip_code or "—",
            "home_region_name":   cp.home_region_name or "—",
            "home_province_name": cp.home_province_name or "—",
            "home_citymun_name":  cp.home_citymun_name or "—",
            "home_barangay_name": cp.home_barangay_name or "—",
            "social_instagram":   cp.social_instagram or "—",
            "social_twitter_x":   cp.social_twitter_x or "—",
            "social_viber":       cp.social_viber or "—",
            "social_whatsapp":    cp.social_whatsapp or "—",
        } if cp else None,
    }
    return jsonify(data)


@main_bp.route("/agent/client/<int:user_id>/document-view/<doc_kind>")
@login_required
def agent_view_client_document_tab(user_id, doc_kind):
    if current_user.role not in ("agent", "admin"):
        return "Forbidden", 403

    normalized_kind = _normalize_client_doc_kind(doc_kind)
    if not normalized_kind:
        return "Not Found", 404

    client = db.session.get(User, user_id)
    if not client or client.role != "client":
        return "Not Found", 404

    if current_user.role == "agent" and not _agent_can_view_client(current_user.id, user_id):
        return "Forbidden", 403

    payload, mimetype, filename = _resolve_client_doc(client.profile, normalized_kind)
    if not payload:
        return "Not Found", 404

    file_url = url_for("main.agent_serve_client_document", user_id=user_id, doc_kind=normalized_kind)
    return render_template("client/document_view.html", file_url=file_url, filename=filename)


@main_bp.route("/agent/client/<int:user_id>/document/<doc_kind>")
@login_required
def agent_serve_client_document(user_id, doc_kind):
    if current_user.role not in ("agent", "admin"):
        return "Forbidden", 403

    normalized_kind = _normalize_client_doc_kind(doc_kind)
    if not normalized_kind:
        return "Not Found", 404

    client = db.session.get(User, user_id)
    if not client or client.role != "client":
        return "Not Found", 404

    if current_user.role == "agent" and not _agent_can_view_client(current_user.id, user_id):
        return "Forbidden", 403

    payload, mimetype, filename = _resolve_client_doc(client.profile, normalized_kind)
    if not payload:
        return "Not Found", 404

    safe_filename = secure_filename(filename) or ("valid-id" if normalized_kind == "valid-id" else "proof-of-income")
    resp = make_response(payload)
    resp.headers["Content-Type"] = mimetype
    resp.headers["Content-Disposition"] = f'inline; filename="{safe_filename}"'
    resp.headers["Cache-Control"] = "no-store"
    return resp


@main_bp.route("/trip/<int:trip_id>/buyer-form/print")
@login_required
def print_buyer_information_sheet(trip_id):
    trip = (TrippingRequest.query
            .filter_by(id=trip_id)
            .options(
                selectinload(TrippingRequest.client).selectinload(User.profile),
                selectinload(TrippingRequest.property_item).selectinload(Property.agent),
                selectinload(TrippingRequest.property_item).selectinload(Property.subdivision),
                selectinload(TrippingRequest.sale_record)
            )
            .first())
    if not trip:
        abort(404)

    # Permission check: accessible by client for their own trip, or admin/agent
    if current_user.role not in ("admin", "agent") and trip.client_id != current_user.id:
        abort(403)

    client = trip.client
    profile = client.profile if client else None
    prop = trip.property_item
    assigned_agent = prop.agent if prop and prop.agent else None

    # Parse purchase_form_data JSON if available
    payload = {}
    if (trip.purchase_form_data or "").strip():
        try:
            parsed = json.loads(trip.purchase_form_data)
            if isinstance(parsed, dict):
                payload = parsed
        except Exception:
            payload = {}

    # Extract or infer E-signature base64 data URL
    esignature_base64 = None
    if profile and profile.esignature_data:
        try:
            mimetype = profile.esignature_mimetype or "image/png"
            b64 = base64.b64encode(profile.esignature_data).decode("ascii")
            esignature_base64 = f"data:{mimetype};base64,{b64}"
        except Exception:
            esignature_base64 = None

    def get_val(*keys, default=""):
        for k in keys:
            v = payload.get(k)
            if v is not None and str(v).strip() and str(v).strip() != "—":
                return str(v).strip()
        return default

    def fmt_date(d_val):
        if not d_val:
            return ""
        if isinstance(d_val, (datetime, date)):
            return d_val.strftime("%m/%d/%Y")
        s = str(d_val).strip()
        m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)
        if m:
            return f"{m.group(2)}/{m.group(3)}/{m.group(1)}"
        return s

    def fmt_php(val):
        if val is None or val == "":
            return ""
        s = str(val).strip()
        try:
            cleaned = s.replace(",", "").replace("PHP", "").replace("₱", "").strip()
            if cleaned:
                n = float(cleaned)
                return f"PHP {n:,.2f}"
            return ""
        except Exception:
            return s

    # Calculated or retrieved loan details
    selling_price_raw = get_val("loanSellingPrice")
    if not selling_price_raw and prop and prop.price:
        selling_price_raw = fmt_php(prop.price)

    loan_amount_raw = get_val("loanAmount")
    downpayment_raw = get_val("loanDownpayment")
    reservation_fee_raw = get_val("loanReservationFee", default="PHP 20,000.00")

    # Date signed
    raw_date = trip.purchase_form_submitted_at or trip.updated_at or trip.created_at
    date_signed_formatted = fmt_date(raw_date)

    b = {
        # Section 1: Principal Buyer
        "last_name": get_val("pbLastName", default=client.last_name if client else ""),
        "first_name": get_val("pbFirstName", default=client.first_name if client else ""),
        "middle_name": get_val("pbMiddleName", default=client.middle_name if client else ""),
        "mother_maiden_name": get_val("pbMotherMaiden", "mother_maiden_name"),
        "citizenship": get_val("pbCitizenship", default=profile.citizenship if profile else "Filipino"),
        "dependents_yes_no": get_val("pbDependentsYesNo", default="yes" if (profile and profile.dependents and profile.dependents > 0) else "no").lower(),
        "dependents_type": get_val("pbDependentsType").lower(),
        "dependents_children": get_val("pbDependentsChildren", default=str(profile.dependents) if (profile and profile.dependents) else ""),
        "dependents_parents": get_val("pbDependentsParents"),
        "dependents_others": get_val("pbDependentsOthers"),
        "birth_date": fmt_date(get_val("pbBirthDate", default=profile.birth_date if profile else "")),
        "birth_place": get_val("pbBirthPlace", default=profile.birthplace if profile else ""),
        "telephone": get_val("pbTelephone", default=profile.contact_no if profile else ""),
        "mobile": get_val("pbMobile", default=profile.contact_number if profile and profile.contact_number else (client.contact_number if client else "")),
        "email_personal": get_val("pbEmailPersonal", default=client.email if client else ""),
        "email_work": get_val("pbEmailWork", default=profile.employer_email if profile else ""),
        "sss_gsis_umid": get_val("pbUmid", default=profile.sss_gsis_umid if profile else ""),
        "tin": get_val("pbTin", default=profile.tin_no if profile else ""),
        "gender": get_val("pbGender", default=profile.gender if profile else "").lower(),
        "civil_status": get_val("pbCivilStatus", default=profile.civil_status if profile else "").lower(),
        "gross_income": fmt_php(get_val("pbGrossIncome", default=profile.gross_income if profile else "")),

        # Social media
        "facebook": get_val("auto_facebook_1", "pbFacebook", "social_facebook"),
        "twitter": get_val("auto_twitter_1", "pbTwitter", default=profile.social_twitter_x if profile else ""),
        "instagram": get_val("auto_instagram_1", "pbInstagram", default=profile.social_instagram if profile else ""),
        "whatsapp": get_val("pbWhatsApp", default=profile.social_whatsapp if profile else ""),
        "viber": get_val("pbViber", default=profile.social_viber if profile else ""),

        # Section 2: Residence Address
        "home_no": get_val("pbHomeNo", default=profile.blk if profile else ""),
        "home_street": get_val("pbHomeStreet", default=profile.street if profile else ""),
        "home_subdivision": get_val("pbHomeSubdivision", default=profile.subdivision_name if profile else ""),
        "home_brgy": get_val("pbHomeBarangayName", "pbHomeBarangaySelect", default=profile.home_barangay_name if profile else ""),
        "home_municipality": get_val("pbHomeCityName", "pbHomeCitySelect", default=profile.home_citymun_name if profile else ""),
        "home_city": get_val("pbHomeCityName", "pbHomeCitySelect", default=profile.home_citymun_name if profile else ""),
        "home_province": get_val("pbHomeProvinceName", "pbHomeProvinceSelect", default=profile.home_province_name if profile else ""),
        "home_country": get_val("pbHomeCountry", default=profile.country if profile and profile.country else "Philippines"),
        "home_zip": get_val("pbHomeZip", default=profile.zip_code if profile else ""),

        # Section 3: Employment
        "employment_type": get_val("pbEmploymentType", default=profile.employment_type if profile else "").lower(),
        "employer_name": get_val("auto_employer_business_name_1", "pbEmployerBusinessName", default=profile.employer_name if profile else ""),
        "employer_phone": get_val("pbEmployerTelephone", default=profile.employer_phone if profile else ""),
        "employer_email": get_val("auto_e_mail_address_1", "pbEmployerEmail", default=profile.employer_email if profile else ""),
        "emp_no": get_val("pbEmpAddressNo"),
        "emp_street": get_val("pbEmpStreet", default=profile.employer_business_address if profile else ""),
        "emp_brgy": get_val("pbEmpBarangayName", "pbEmpBarangaySelect", default=profile.employer_barangay_name if profile else ""),
        "emp_municipality": get_val("pbEmpCityName", "pbEmpCitySelect", default=profile.employer_citymun_name if profile else ""),
        "emp_city": get_val("pbEmpCityName", "pbEmpCitySelect", default=profile.employer_citymun_name if profile else ""),
        "emp_province": get_val("pbEmpProvinceName", "pbEmpProvinceSelect", default=profile.employer_province_name if profile else ""),
        "emp_country": get_val("pbEmpCountry", default="Philippines"),
        "emp_zip": get_val("auto_zip_code_1", "pbEmpZip", default=profile.zip_code if profile else ""),
        "occupation": get_val("pbOccupationProfession"),
        "position": get_val("pbOccupationalPosition"),
        "tenure": get_val("auto_tenure_1", "pbTenure", default=str(profile.tenure_months) if profile and profile.tenure_months else ""),
        "preferred_mailing": get_val("pbMailPref", default="residence").lower(),

        # Mailing Address
        "mail_no": get_val("pbMailNo"),
        "mail_street": get_val("pbMailStreet"),
        "mail_subdivision": get_val("pbMailSubdivision"),
        "mail_brgy": get_val("pbMailBarangayName", "pbMailBarangaySelect"),
        "mail_municipality": get_val("pbMailCityName", "pbMailCitySelect"),
        "mail_city": get_val("pbMailCityName", "pbMailCitySelect"),
        "mail_province": get_val("pbMailProvinceName", "pbMailProvinceSelect"),
        "mail_country": get_val("pbMailCountry", default="Philippines"),
        "mail_zip": get_val("pbMailZip"),

        # Section 4: Spouse
        "has_spouse": get_val("pbHasSpouse", default="no").lower() == "yes",
        "sp_last_name": get_val("spLastName"),
        "sp_first_name": get_val("spFirstName"),
        "sp_middle_name": get_val("spMiddleName"),
        "sp_birth_date": fmt_date(get_val("spBirthDate")),
        "sp_birth_place": get_val("spBirthPlace"),
        "sp_citizenship": get_val("spCitizenship"),
        "sp_mobile": get_val("spMobile"),
        "sp_telephone": get_val("spTelephone"),
        "sp_email": get_val("spEmail"),
        "sp_tin": get_val("spTin"),
        "sp_umid": get_val("spUmid"),
        "sp_gross_income": fmt_php(get_val("spGrossIncome")),
        "sp_emp_type": get_val("spEmploymentType").lower(),
        "sp_emp_name": get_val("spEmpBusinessName"),
        "sp_emp_phone": get_val("spEmpTelephone"),
        "sp_emp_email": get_val("spEmpEmail"),
        "sp_emp_no": get_val("spEmpAddressNo"),
        "sp_emp_street": get_val("spEmpStreet"),
        "sp_emp_brgy": get_val("spEmpBarangayName", "spEmpBarangaySelect"),
        "sp_emp_municipality": get_val("spEmpCityName", "spEmpCitySelect"),
        "sp_emp_city": get_val("spEmpCityName", "spEmpCitySelect"),
        "sp_emp_province": get_val("spEmpProvinceName", "spEmpProvinceSelect"),
        "sp_emp_country": get_val("spEmpCountry", default="Philippines"),
        "sp_emp_zip": get_val("spEmpZip"),
        "sp_occupation": get_val("spEmpOccupationProfession"),
        "sp_position": get_val("spEmpOccupationalPosition"),
        "sp_tenure": get_val("spEmpTenure"),

        # Section 5: Beneficiaries
        "ben1_last": get_val("beneficiary1LastName"),
        "ben1_first": get_val("beneficiary1FirstName"),
        "ben1_mid": get_val("beneficiary1MiddleName"),
        "ben1_bday": fmt_date(get_val("beneficiary1Birthday")),
        "ben1_rel": get_val("beneficiary1Relationship"),

        "ben2_last": get_val("beneficiary2LastName"),
        "ben2_first": get_val("beneficiary2FirstName"),
        "ben2_mid": get_val("beneficiary2MiddleName"),
        "ben2_bday": fmt_date(get_val("beneficiary2Birthday")),
        "ben2_rel": get_val("beneficiary2Relationship"),

        # Section 6: Buyer's Profile
        "buyer_rank": get_val("buyerRank").lower(),
        "buyer_funds": get_val("buyerFunds").lower(),
        "home_ownership": get_val("homeOwnership").lower(),
        "length_of_stay": get_val("pbLengthOfStay"),
        "household_count": get_val("householdCount"),
        "buying_purpose": get_val("buyingPurpose").lower(),
        "project_source": get_val("projectSource").lower(),
        "has_bank_account": get_val("hasBankAccount", default="no").lower() == "yes",
        "bank_name": get_val("pbBankName", "auto_with_which_bank_s_1", "auto_with_which_bank_s_2"),
        "bank_account_type": get_val("bankAccountType").lower(),

        # Section 7: Loan Details
        "unit_id": get_val("loanUnitId", default=str(prop.unit_id) if prop and prop.unit_id else ""),
        "selling_price": selling_price_raw,
        "processing_fee": fmt_php(get_val("loanProcessingFee")),
        "loan_amount": fmt_php(loan_amount_raw),
        "downpayment": fmt_php(downpayment_raw),
        "reservation_fee": fmt_php(reservation_fee_raw),
        "promo_disc": fmt_php(get_val("loanPromoDisc")),
        "or_pr_no": get_val("loanOrPrNo"),
        "or_pr_date": fmt_date(get_val("loanOrPrDate")),
        "booking_officer": get_val("loanBookingOfficer", default=assigned_agent.full_name if assigned_agent else ""),
        "loan_financing": get_val("loanFinancing").lower(),
        "loan_cash_days": get_val("loanCashDays", "loan_cash_days"),
        "loan_financing_other": get_val("loanFinancingOther", "loan_financing_other", "loan_financing_custom"),
        "downpayment_term": get_val("loanDownpaymentTerm"),
        "downpayment_term_other": get_val("loanDownpaymentTermOther", "loan_downpayment_term_other", "loan_dp_term_custom"),
        "loan_term": get_val("loanTerm"),
        "loan_term_other": get_val("loanTermOther", "loan_term_other", "loan_term_custom"),

        # Section 8 & 9: Agent & Signatures
        "group_name": "ANNREGAR REALTY",
        "division_manager": "ANGELINE R. GARDIOLA",
        "branch_manager": "",
        "unit_manager": "",
        "assigned_agent_name": assigned_agent.full_name if assigned_agent else (get_val("loanBookingOfficer") or "—"),
        "buyer_full_name": f"{client.first_name} {client.middle_name or ''} {client.last_name}".strip().upper() if client else "—",
        "date_signed": date_signed_formatted,
        "esignature_base64": esignature_base64,
    }

    return render_template(
        "client/buyer_information_sheet_print.html",
        trip=trip,
        prop=prop,
        client=client,
        b=b
    )



@main_bp.route("/dashboard/admin/tripping-report/print")
@login_required
def print_tripping_monitoring_report():
    if current_user.role not in ("admin", "agent"):
        abort(403)

    today = date.today()
    period = (request.args.get("period") or "this_week").lower()
    start_date_str = (request.args.get("start_date") or "").strip()
    end_date_str = (request.args.get("end_date") or "").strip()
    prop_id = request.args.get("property_id")

    start_date = None
    end_date = None
    coverage_label = ""

    if period == "this_week":
        # Monday is 0, Sunday is 6
        start_date = today - timedelta(days=today.weekday())
        end_date = start_date + timedelta(days=6)
        coverage_label = f"{start_date.strftime('%B %d, %Y')} – {end_date.strftime('%B %d, %Y')} (This Week)"
    elif period == "today":
        start_date = today
        end_date = today
        coverage_label = f"{today.strftime('%B %d, %Y')} (Today Only)"
    elif period == "custom":
        if start_date_str:
            try:
                start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
            except ValueError:
                start_date = None
        if end_date_str:
            try:
                end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
            except ValueError:
                end_date = None

        if start_date and end_date:
            coverage_label = f"{start_date.strftime('%B %d, %Y')} – {end_date.strftime('%B %d, %Y')}"
        elif start_date:
            coverage_label = f"From {start_date.strftime('%B %d, %Y')}"
        elif end_date:
            coverage_label = f"Until {end_date.strftime('%B %d, %Y')}"
        else:
            coverage_label = "All Dates"
    else:
        coverage_label = "All Time"

    query = TrippingRequest.query.join(Property, TrippingRequest.property_id == Property.id) \
        .filter(TrippingRequest.status.in_(["approved", "visited"])) \
        .filter(~TrippingRequest.sale_record.has())

    if start_date:
        query = query.filter(TrippingRequest.preferred_date >= start_date)
    if end_date:
        query = query.filter(TrippingRequest.preferred_date <= end_date)

    selected_property = None
    if prop_id and prop_id not in ("", "all"):
        try:
            pid = int(prop_id)
            query = query.filter(TrippingRequest.property_id == pid)
            selected_property = Property.query.get(pid)
        except (ValueError, TypeError):
            pass

    trips = query.order_by(
        Property.name.asc(),
        TrippingRequest.preferred_date.asc(),
        TrippingRequest.preferred_time.asc()
    ).all()

    # Summary Statistics (Operational Dispatch: Approved & Visited Only)
    total_trips = len(trips)
    status_counts = {
        "approved": 0,
        "visited": 0,
    }
    model_counts = {}

    for t in trips:
        raw_st = (t.status or "").lower()
        if raw_st in status_counts:
            status_counts[raw_st] += 1

        pname = t.property_item.name if t.property_item else "Other Properties"
        model_counts[pname] = model_counts.get(pname, 0) + 1

    top_model = "—"
    top_model_count = 0
    if model_counts:
        sorted_models = sorted(model_counts.items(), key=lambda x: x[1], reverse=True)
        top_model = sorted_models[0][0]
        top_model_count = sorted_models[0][1]

    # Group trips by property model
    grouped_trips = {}
    for t in trips:
        prop = t.property_item
        prop_key = prop.name if prop else "Other Properties"
        if prop_key not in grouped_trips:
            grouped_trips[prop_key] = {
                "property": prop,
                "trips": []
            }
        grouped_trips[prop_key]["trips"].append(t)

    now_manila = datetime.now()

    return render_template(
        "dashboard/tripping_monitoring_report_print.html",
        trips=trips,
        grouped_trips=grouped_trips,
        total_trips=total_trips,
        status_counts=status_counts,
        top_model=top_model,
        top_model_count=top_model_count,
        coverage_label=coverage_label,
        selected_property=selected_property,
        now_manila=now_manila,
        admin_user=current_user
    )
