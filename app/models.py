import json
import re
from datetime import datetime, timezone
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()

# ---------------------------------------------------------------------------
# USERS  (clients, agents, admins)
# ---------------------------------------------------------------------------

class User(UserMixin, db.Model):
    __tablename__ = "users"

    id             = db.Column(db.Integer, primary_key=True)
    first_name     = db.Column(db.String(80),  nullable=False)
    middle_name    = db.Column(db.String(80),  nullable=True)
    last_name      = db.Column(db.String(80),  nullable=False)
    username       = db.Column(db.String(60),  unique=True, nullable=True, index=True)
    email          = db.Column(db.String(120), unique=True, nullable=False, index=True)
    contact_number = db.Column(db.String(20),  nullable=True)
    password_hash  = db.Column(db.String(256), nullable=False)
    role           = db.Column(db.Enum("client", "agent", "admin", name="user_role"),
                                nullable=False, default="client")
    is_active      = db.Column(db.Boolean, default=True, nullable=False)
    is_verified    = db.Column(db.Boolean, default=True, nullable=False)
    otp_code       = db.Column(db.String(10), nullable=True)
    otp_expiry     = db.Column(db.DateTime, nullable=True)
    forgot_password_attempts = db.Column(db.Integer, default=0, nullable=False)
    forgot_password_window_started_at = db.Column(db.DateTime, nullable=True)
    admin_dismissed_property_notifs = db.Column(db.Text, nullable=True)
    admin_dismissed_assessment_notifs = db.Column(db.Text, nullable=True)
    admin_dismissed_sale_notifs = db.Column(db.Text, nullable=True)
    created_at     = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    # Relationships
    profile            = db.relationship("UserProfile", back_populates="user", uselist=False)
    qualification_results = db.relationship("QualificationResult", back_populates="user")
    tripping_requests  = db.relationship("TrippingRequest", back_populates="client",
                                          foreign_keys="TrippingRequest.client_id")
    pricing_detail_requests = db.relationship(
        "PropertyPricingDetailRequest",
        foreign_keys="PropertyPricingDetailRequest.client_id",
        back_populates="client",
    )
    agent_notifications = db.relationship("AgentNotification", back_populates="agent")
    availability_slots = db.relationship("AgentAvailability", back_populates="agent", cascade="all, delete-orphan")
    bought_properties  = db.relationship("PropertySale", foreign_keys="PropertySale.client_id", back_populates="client")
    sold_properties    = db.relationship("PropertySale", foreign_keys="PropertySale.agent_id", back_populates="agent")

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    @property
    def full_name(self) -> str:
        parts = [self.first_name, self.middle_name, self.last_name]
        return " ".join([p.strip() for p in parts if p and str(p).strip()])

    def _load_id_list(self, raw_value: str | None) -> list[int]:
        if not raw_value:
            return []
        try:
            values = json.loads(raw_value)
        except (TypeError, ValueError):
            return []
        if not isinstance(values, list):
            return []
        parsed = []
        for value in values:
            try:
                parsed.append(int(value))
            except (TypeError, ValueError):
                continue
        return parsed

    def _dump_id_list(self, values: list[int]) -> str:
        unique_sorted = sorted({int(value) for value in values})
        return json.dumps(unique_sorted)

    def get_admin_dismissed_property_notifs(self) -> list[int]:
        return self._load_id_list(self.admin_dismissed_property_notifs)

    def set_admin_dismissed_property_notifs(self, values: list[int]) -> None:
        self.admin_dismissed_property_notifs = self._dump_id_list(values)

    def get_admin_dismissed_assessment_notifs(self) -> list[int]:
        return self._load_id_list(self.admin_dismissed_assessment_notifs)

    def set_admin_dismissed_assessment_notifs(self, values: list[int]) -> None:
        self.admin_dismissed_assessment_notifs = self._dump_id_list(values)

    def get_admin_dismissed_sale_notifs(self) -> list[int]:
        return self._load_id_list(self.admin_dismissed_sale_notifs)

    def set_admin_dismissed_sale_notifs(self, values: list[int]) -> None:
        self.admin_dismissed_sale_notifs = self._dump_id_list(values)

    def __repr__(self):
        return f"<User {self.email} [{self.role}]>"


# ---------------------------------------------------------------------------
# USER PROFILE
# ---------------------------------------------------------------------------

class UserProfile(db.Model):
    __tablename__ = "user_profiles"

    id              = db.Column(db.Integer, primary_key=True)
    user_id         = db.Column(db.Integer, db.ForeignKey("users.id"), unique=True, nullable=False)

    # Personal
    civil_status    = db.Column(db.String(20))   # single / married / widowed / separated
    citizenship     = db.Column(db.String(30))   # filipino / dual-citizen / foreign-national
    gender          = db.Column(db.String(30))   # male / female / non-binary / prefer-not-to-say
    dependents      = db.Column(db.Integer, default=0)
    birth_date      = db.Column(db.Date)
    birthplace      = db.Column(db.String(120))
    birth_region_code = db.Column(db.String(10))
    birth_region_name = db.Column(db.String(100))
    birth_province_code = db.Column(db.String(10))
    birth_province_name = db.Column(db.String(100))
    birth_citymun_code = db.Column(db.String(10))
    birth_citymun_name = db.Column(db.String(120))
    birth_barangay_code = db.Column(db.String(15))
    birth_barangay_name = db.Column(db.String(120))
    contact_number  = db.Column(db.String(20))
    address         = db.Column(db.String(255))

    # Employment
    employment_type = db.Column(db.String(30))   # employed / ofw-landbased / ofw-seafarer / licensed-professional / with-financial-support / with-attorney-in-fact / with-co-borrower
    employer_name   = db.Column(db.String(120))
    employer_phone  = db.Column(db.String(30))
    employer_email  = db.Column(db.String(120))
    employer_business_address = db.Column(db.String(255))
    employer_region_code = db.Column(db.String(10))
    employer_region_name = db.Column(db.String(100))
    employer_province_code = db.Column(db.String(10))
    employer_province_name = db.Column(db.String(100))
    employer_citymun_code = db.Column(db.String(10))
    employer_citymun_name = db.Column(db.String(120))
    employer_barangay_code = db.Column(db.String(15))
    employer_barangay_name = db.Column(db.String(120))
    sss_gsis_umid   = db.Column(db.String(60))
    tin_no          = db.Column(db.String(30))
    tenure_months   = db.Column(db.Integer)      # months of service
    gross_income    = db.Column(db.Numeric(12, 2))

    # Financial obligations
    monthly_loans        = db.Column(db.Numeric(12, 2), default=0)
    other_deductions     = db.Column(db.Numeric(12, 2), default=0)

    # Employment (extended)
    age                  = db.Column(db.Integer)

    # Avatar / banner (stored as binary blobs, max 16 MB each)
    avatar_data      = db.Column(db.LargeBinary(length=16777215), nullable=True)
    avatar_mimetype  = db.Column(db.String(50),  nullable=True)
    banner_data      = db.Column(db.LargeBinary(length=16777215), nullable=True)
    banner_mimetype  = db.Column(db.String(50),  nullable=True)

    # Client documentation uploads (optional)
    has_valid_id       = db.Column(db.Boolean, nullable=True)
    has_income_proof   = db.Column(db.Boolean, nullable=True)
    valid_id_data      = db.Column(db.LargeBinary(length=16777215), nullable=True)
    valid_id_mimetype  = db.Column(db.String(80), nullable=True)
    valid_id_filename  = db.Column(db.String(255), nullable=True)
    income_proof_data     = db.Column(db.LargeBinary(length=16777215), nullable=True)
    income_proof_mimetype = db.Column(db.String(80), nullable=True)
    income_proof_filename = db.Column(db.String(255), nullable=True)
    esignature_data       = db.Column(db.LargeBinary(length=16777215), nullable=True)
    esignature_mimetype   = db.Column(db.String(80), nullable=True)
    esignature_filename   = db.Column(db.String(255), nullable=True)

    # Housing preferences
    preferred_type     = db.Column(db.String(40))   # house-and-lot (house-only policy)
    budget_min         = db.Column(db.Numeric(14, 2), default=0)
    budget_max         = db.Column(db.Numeric(14, 2), default=0)

    # Home address (PSGC-structured)
    address_line = db.Column(db.String(255))
    home_region_code = db.Column(db.String(10))
    home_region_name = db.Column(db.String(100))
    home_province_code = db.Column(db.String(10))
    home_province_name = db.Column(db.String(100))
    home_citymun_code = db.Column(db.String(10))
    home_citymun_name = db.Column(db.String(120))
    home_barangay_code = db.Column(db.String(15))
    home_barangay_name = db.Column(db.String(120))

    # Detailed address fields
    street = db.Column(db.String(120))
    blk = db.Column(db.String(30))
    lot = db.Column(db.String(30))
    country = db.Column(db.String(80))
    zip_code = db.Column(db.String(20))
    subdivision_name = db.Column(db.String(120))

    # Social media accounts
    social_instagram = db.Column(db.String(120))
    social_twitter_x = db.Column(db.String(120))
    social_viber = db.Column(db.String(40))
    social_whatsapp = db.Column(db.String(40))

    # Agent-specific details
    license_no         = db.Column(db.String(60))
    contact_no         = db.Column(db.String(20))
    bio                = db.Column(db.Text)

    updated_at = db.Column(db.DateTime,
                           default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))

    user = db.relationship("User", back_populates="profile")

    def __repr__(self):
        return f"<UserProfile user_id={self.user_id}>"


# ---------------------------------------------------------------------------
# QUALIFICATION RESULTS
# ---------------------------------------------------------------------------

class QualificationResult(db.Model):
    __tablename__ = "qualification_results"

    id               = db.Column(db.Integer, primary_key=True)
    user_id          = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    status           = db.Column(db.String(30), nullable=False)  # Qualified / Conditionally Qualified / Not Qualified
    dti_ratio        = db.Column(db.Float)
    max_loanable     = db.Column(db.Numeric(14, 2))
    similarity_score = db.Column(db.Float)
    assessment_mode  = db.Column(db.String(20), default="reassess")  # new / reassess
    factors_json     = db.Column(db.Text)    # JSON string of top-3 factors
    created_at       = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    user = db.relationship("User", back_populates="qualification_results")

    def __repr__(self):
        return f"<QualResult user_id={self.user_id} status={self.status}>"


# ---------------------------------------------------------------------------
# PROJECTS  (only admin can create)
# ---------------------------------------------------------------------------

class Project(db.Model):
    __tablename__ = "projects"

    id         = db.Column(db.Integer, primary_key=True)
    name       = db.Column(db.String(150), unique=True, nullable=False)
    street     = db.Column(db.String(120))
    block      = db.Column(db.String(30))
    lot_no     = db.Column(db.String(30))
    location    = db.Column(db.String(200))
    region_code = db.Column(db.String(10))
    region_name = db.Column(db.String(100))
    province_code = db.Column(db.String(10))
    province_name = db.Column(db.String(100))
    citymun_code = db.Column(db.String(10))
    citymun_name = db.Column(db.String(120))
    barangay_code = db.Column(db.String(15))
    barangay_name = db.Column(db.String(120))
    description = db.Column(db.Text)
    images_csv  = db.Column("images", db.Text, default="")
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    subdivisions = db.relationship("Subdivision", back_populates="project", cascade="all, delete-orphan")

    @property
    def images(self):
        raw = (self.images_csv or "").strip()
        if not raw:
            return []
        return [item.strip() for item in raw.split(",") if item and item.strip()]

    @images.setter
    def images(self, value):
        if not value:
            self.images_csv = ""
            return
        self.images_csv = ",".join([str(item).strip() for item in value if str(item).strip()])

    def __repr__(self):
        return f"<Project {self.name}>"

class Subdivision(db.Model):
    __tablename__ = "subdivisions"

    id          = db.Column(db.Integer, primary_key=True)
    name        = db.Column(db.String(150), unique=True, nullable=False)
    project_id  = db.Column(db.Integer, db.ForeignKey("projects.id"), nullable=True, index=True)
    street      = db.Column(db.String(120))
    block       = db.Column(db.String(30))
    lot_no      = db.Column(db.String(30))
    location    = db.Column(db.String(200))
    region_code = db.Column(db.String(10))
    region_name = db.Column(db.String(100))
    province_code = db.Column(db.String(10))
    province_name = db.Column(db.String(100))
    citymun_code = db.Column(db.String(10))
    citymun_name = db.Column(db.String(120))
    barangay_code = db.Column(db.String(15))
    barangay_name = db.Column(db.String(120))
    description = db.Column(db.Text)
    images_csv  = db.Column("images", db.Text, default="")
    image_data  = db.Column(db.LargeBinary(length=16777215), nullable=True)
    image_mimetype = db.Column(db.String(50), nullable=True)
    created_at  = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    project = db.relationship("Project", back_populates="subdivisions")
    properties = db.relationship("Property", back_populates="subdivision")

    @property
    def images(self):
        raw = (self.images_csv or "").strip()
        if not raw:
            return []
        return [item.strip() for item in raw.split(",") if item and item.strip()]

    @images.setter
    def images(self, value):
        if not value:
            self.images_csv = ""
            return
        self.images_csv = ",".join([str(item).strip() for item in value if str(item).strip()])

    @property
    def city_province(self):
        """Returns clean 'City, Province' e.g. 'Carmona, Cavite'."""
        if self.citymun_name and self.province_name:
            city = re.sub(r"^city of\s+", "", self.citymun_name.strip(), flags=re.I)
            prov = self.province_name.strip()
            return f"{city}, {prov}"
        if self.citymun_name:
            return re.sub(r"^city of\s+", "", self.citymun_name.strip(), flags=re.I)
        if self.province_name:
            return self.province_name.strip()
        if self.location:
            parts = [p.strip() for p in self.location.split(",") if p.strip()]
            if len(parts) >= 3:
                city = re.sub(r"^city of\s+", "", parts[1], flags=re.I)
                return f"{city}, {parts[2]}"
            elif len(parts) == 2:
                city = re.sub(r"^city of\s+", "", parts[0], flags=re.I)
                return f"{city}, {parts[1]}"
            elif len(parts) == 1:
                return parts[0]
        return ""

    def __repr__(self):
        return f"<Subdivision {self.name}>"


# ---------------------------------------------------------------------------
# PROPERTIES
# ---------------------------------------------------------------------------

class Property(db.Model):
    __tablename__ = "properties"

    id          = db.Column(db.Integer, primary_key=True)
    name        = db.Column(db.String(120), nullable=False)
    street      = db.Column(db.String(120))
    block       = db.Column(db.String(30))
    lot_no      = db.Column(db.String(30))
    location    = db.Column(db.String(150), nullable=False)
    region      = db.Column(db.String(100))
    region_code = db.Column(db.String(10))
    region_name = db.Column(db.String(100))
    province_code = db.Column(db.String(10))
    province_name = db.Column(db.String(100))
    citymun_code = db.Column(db.String(10))
    citymun_name = db.Column(db.String(120))
    barangay_code = db.Column(db.String(15))
    barangay_name = db.Column(db.String(120))
    prop_type   = db.Column(db.String(40))      # kept for legacy compatibility (house-and-lot)
    unit_type   = db.Column(db.String(40))      # pre-selling / ready-for-occupancy / resale
    price       = db.Column(db.Numeric(14, 2),  nullable=False)
    promo_discount_rate = db.Column(db.Numeric(5, 2), nullable=True)
    reservation_fee = db.Column(db.Numeric(14, 2), nullable=True)
    downpayment_rate = db.Column(db.Numeric(5, 2), nullable=True)
    downpayment_terms_months = db.Column(db.Integer, nullable=True)
    loanable_percentage = db.Column(db.Numeric(5, 2), nullable=True)
    vat_rate = db.Column(db.Numeric(5, 2), nullable=True)
    lmf_rate = db.Column(db.Numeric(5, 2), nullable=True)
    annual_interest_rate = db.Column(db.Numeric(5, 2), nullable=True)
    bedrooms    = db.Column(db.SmallInteger)      # matches schema.sql TINYINT UNSIGNED
    bathrooms   = db.Column(db.SmallInteger)      # matches schema.sql TINYINT UNSIGNED
    storeys     = db.Column(db.SmallInteger)      # matches schema.sql TINYINT UNSIGNED; number of floors/storeys
    floor_area  = db.Column(db.Float)            # sqm
    lot_area    = db.Column(db.Float)           # sqm
    description = db.Column(db.Text)
    images      = db.Column(db.Text)            # comma-separated filenames
    agent_id    = db.Column(db.Integer, db.ForeignKey("users.id"))
    subdivision_id = db.Column(db.Integer, db.ForeignKey("subdivisions.id"), nullable=True)
    unit_id     = db.Column(db.String(60), nullable=True, index=True)
    status      = db.Column(db.String(20), default="available")  # available / sold / reserved
    approval_status = db.Column(db.String(20), default="approved")  # pending / approved / rejected
    created_at  = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    agent            = db.relationship("User", foreign_keys=[agent_id])
    subdivision      = db.relationship("Subdivision", back_populates="properties")
    tripping_requests = db.relationship("TrippingRequest", back_populates="property_item")
    pricing_detail_requests = db.relationship("PropertyPricingDetailRequest", back_populates="property_item")
    sale_record      = db.relationship("PropertySale", back_populates="property_item", uselist=False)

    @property
    def city_province(self):
        """Returns clean 'City, Province' e.g. 'Carmona, Cavite'."""
        if self.citymun_name and self.province_name:
            city = re.sub(r"^(?:City of|Municipality of)\s+", "", self.citymun_name.strip(), flags=re.I)
            prov = self.province_name.strip()
            return f"{city}, {prov}"
        if self.citymun_name:
            return re.sub(r"^(?:City of|Municipality of)\s+", "", self.citymun_name.strip(), flags=re.I)
        if self.province_name:
            return self.province_name.strip()
        if self.location:
            parts = [p.strip() for p in self.location.split(",") if p.strip()]
            if len(parts) >= 3:
                city = re.sub(r"^(?:City of|Municipality of)\s+", "", parts[1], flags=re.I)
                return f"{city}, {parts[2]}"
            elif len(parts) == 2:
                city = re.sub(r"^(?:City of|Municipality of)\s+", "", parts[0], flags=re.I)
                return f"{city}, {parts[1]}"
            elif len(parts) == 1:
                return parts[0]
        return ""

    def __repr__(self):
        return f"<Property {self.name} @ {self.location}>"


# ---------------------------------------------------------------------------
# TRIPPING REQUESTS
# ---------------------------------------------------------------------------

class TrippingRequest(db.Model):
    __tablename__ = "tripping_requests"

    id             = db.Column(db.Integer, primary_key=True)
    client_id         = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    property_id       = db.Column(db.Integer, db.ForeignKey("properties.id"), nullable=False)
    preferred_date    = db.Column(db.Date, nullable=False)
    preferred_time    = db.Column(db.String(10))
    status            = db.Column(db.String(20), default="pending")  # pending / approved / visited / rejected / sold
    agent_note        = db.Column(db.Text)
    notification_read = db.Column(db.Boolean, default=False)
    purchase_form_submitted = db.Column(db.Boolean, default=False)
    purchase_form_submitted_at = db.Column(db.DateTime, nullable=True)
    purchase_form_data = db.Column(db.Text, nullable=True)
    created_at        = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at        = db.Column(db.DateTime,
                               default=lambda: datetime.now(timezone.utc),
                               onupdate=lambda: datetime.now(timezone.utc))

    client        = db.relationship("User", back_populates="tripping_requests",
                                    foreign_keys=[client_id])
    property_item = db.relationship("Property", back_populates="tripping_requests")
    sale_record   = db.relationship("PropertySale", back_populates="trip_item", uselist=False)

    def __repr__(self):
        return f"<TrippingRequest client={self.client_id} property={self.property_id} [{self.status}]>"


class AgentAvailability(db.Model):
    __tablename__ = "agent_availability"

    id = db.Column(db.Integer, primary_key=True)
    agent_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    available_date = db.Column(db.Date, nullable=False, index=True)
    availability_status = db.Column(db.String(20), nullable=False, default="available")
    start_time = db.Column(db.Time, nullable=False)
    end_time = db.Column(db.Time, nullable=False)
    notes = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    agent = db.relationship("User", back_populates="availability_slots")

    def __repr__(self):
        return f"<AgentAvailability agent={self.agent_id} {self.available_date} {self.start_time}-{self.end_time}>"


# ---------------------------------------------------------------------------
# PROPERTY SALES  (closed deals / bought properties)
# ---------------------------------------------------------------------------

class PropertySale(db.Model):
    __tablename__ = "property_sales"

    id            = db.Column(db.Integer, primary_key=True)
    property_id   = db.Column(db.Integer, db.ForeignKey("properties.id"), nullable=False, unique=True, index=True)
    client_id     = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    trip_id       = db.Column(db.Integer, db.ForeignKey("tripping_requests.id"), nullable=True, unique=True, index=True)
    agent_id      = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)
    selling_price = db.Column(db.Numeric(14, 2), nullable=True)
    note          = db.Column(db.Text, nullable=True)
    sold_at       = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    property_item = db.relationship("Property", foreign_keys=[property_id], back_populates="sale_record")
    client        = db.relationship("User", foreign_keys=[client_id], back_populates="bought_properties")
    trip_item     = db.relationship("TrippingRequest", foreign_keys=[trip_id], back_populates="sale_record")
    agent         = db.relationship("User", foreign_keys=[agent_id], back_populates="sold_properties")

    def __repr__(self):
        return f"<PropertySale property={self.property_id} client={self.client_id}>"


# ---------------------------------------------------------------------------
# HISTORICAL BUYERS  (training data for the ML engine)
# ---------------------------------------------------------------------------

class HistoricalBuyer(db.Model):
    __tablename__ = "training_data"

    id               = db.Column(db.Integer, primary_key=True)
    civil_status     = db.Column(db.String(20))
    dependents       = db.Column(db.SmallInteger, default=0)  # matches schema.sql TINYINT
    age              = db.Column(db.Integer, default=30)
    employment_type  = db.Column(db.String(30))
    tenure_months    = db.Column(db.Integer, default=0)
    gross_income     = db.Column(db.Numeric(12, 2))
    monthly_loans    = db.Column(db.Numeric(12, 2), default=0)
    other_deductions = db.Column(db.Numeric(12, 2), default=0)
    dti_ratio        = db.Column(db.Float)
    outcome          = db.Column(
        db.Enum("Qualified", "Conditionally Qualified", "Not Qualified", name="training_data_outcome"),
        nullable=False,
    )
    notes            = db.Column(db.String(255))
    created_at       = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def __repr__(self):
        return f"<HistoricalBuyer outcome={self.outcome}>"


# ---------------------------------------------------------------------------
# HISTORICAL BUYER RECORDS  (captured from actual closed sales)
# ---------------------------------------------------------------------------

class HistoricalBuyerRecord(db.Model):
    __tablename__ = "historical_buyer_records"

    id               = db.Column(db.Integer, primary_key=True)
    sale_id          = db.Column(db.Integer, db.ForeignKey("property_sales.id"), nullable=False, unique=True, index=True)
    client_id        = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    property_id      = db.Column(db.Integer, db.ForeignKey("properties.id"), nullable=True, index=True)
    civil_status     = db.Column(db.String(20))
    dependents       = db.Column(db.Integer, default=0)
    age              = db.Column(db.Integer, default=30)
    employment_type  = db.Column(db.String(30))
    tenure_months    = db.Column(db.Integer, default=0)
    gross_income     = db.Column(db.Numeric(12, 2))
    monthly_loans    = db.Column(db.Numeric(12, 2), default=0)
    dti_ratio        = db.Column(db.Float)
    outcome          = db.Column(db.String(30))
    notes            = db.Column(db.String(255))
    created_at       = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    sale             = db.relationship("PropertySale", foreign_keys=[sale_id])
    client           = db.relationship("User", foreign_keys=[client_id])
    property_item    = db.relationship("Property", foreign_keys=[property_id])

    def __repr__(self):
        return f"<HistoricalBuyerRecord sale={self.sale_id} outcome={self.outcome}>"


# ---------------------------------------------------------------------------
# ACTIVITY LOG  (site-wide event trail)
# ---------------------------------------------------------------------------

class ActivityLog(db.Model):
    __tablename__ = "activity_logs"

    id          = db.Column(db.Integer, primary_key=True)
    actor_id    = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    actor_name  = db.Column(db.String(160), nullable=False, default="System")
    actor_role  = db.Column(db.String(20),  nullable=False, default="system")
    action      = db.Column(db.String(40),  nullable=False)
    description = db.Column(db.String(500), nullable=False)
    created_at  = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    actor = db.relationship("User", foreign_keys=[actor_id])

    def __repr__(self):
        return f"<ActivityLog {self.action} by {self.actor_name}>"


class AgentNotification(db.Model):
    __tablename__ = "agent_notifications"

    id          = db.Column(db.Integer, primary_key=True)
    agent_id    = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    property_id = db.Column(db.Integer, db.ForeignKey("properties.id", ondelete="SET NULL"), nullable=True, index=True)
    event_type  = db.Column(db.String(40), nullable=False)
    message     = db.Column(db.String(255), nullable=False)
    is_read     = db.Column(db.Boolean, default=False, nullable=False)
    created_at  = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    agent = db.relationship("User", foreign_keys=[agent_id], back_populates="agent_notifications")
    property_item = db.relationship("Property", foreign_keys=[property_id])

    def __repr__(self):
        return f"<AgentNotification agent={self.agent_id} type={self.event_type}>"


class PropertyPricingDetailRequest(db.Model):
    __tablename__ = "property_pricing_detail_requests"

    id          = db.Column(db.Integer, primary_key=True)
    client_id   = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    property_id = db.Column(db.Integer, db.ForeignKey("properties.id", ondelete="CASCADE"), nullable=False, index=True)
    status      = db.Column(db.String(20), nullable=False, default="pending")  # pending / approved / rejected
    agent_note  = db.Column(db.Text, nullable=True)
    reviewed_by_agent_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    reviewed_at = db.Column(db.DateTime, nullable=True)
    created_at  = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at  = db.Column(db.DateTime,
                            default=lambda: datetime.now(timezone.utc),
                            onupdate=lambda: datetime.now(timezone.utc),
                            nullable=False)

    client = db.relationship("User", foreign_keys=[client_id], back_populates="pricing_detail_requests")
    property_item = db.relationship("Property", foreign_keys=[property_id], back_populates="pricing_detail_requests")
    reviewed_by_agent = db.relationship("User", foreign_keys=[reviewed_by_agent_id])

    __table_args__ = (
        db.UniqueConstraint("client_id", "property_id", name="uq_pricing_detail_client_property"),
    )

    def __repr__(self):
        return f"<PropertyPricingDetailRequest client={self.client_id} property={self.property_id} [{self.status}]>"


class PropertyPricingDetailRequestHistory(db.Model):
    __tablename__ = "property_pricing_detail_request_history"

    id          = db.Column(db.Integer, primary_key=True)
    request_id  = db.Column(db.Integer, db.ForeignKey("property_pricing_detail_requests.id", ondelete="SET NULL"), nullable=True, index=True)
    client_id   = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    property_id = db.Column(db.Integer, db.ForeignKey("properties.id", ondelete="CASCADE"), nullable=False, index=True)
    status      = db.Column(db.String(20), nullable=False, default="pending")  # pending / approved / rejected
    agent_note  = db.Column(db.Text, nullable=True)
    reviewed_by_agent_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    requested_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    reviewed_at  = db.Column(db.DateTime, nullable=True)
    created_at   = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    request = db.relationship("PropertyPricingDetailRequest", foreign_keys=[request_id])
    client = db.relationship("User", foreign_keys=[client_id])
    property_item = db.relationship("Property", foreign_keys=[property_id])
    reviewed_by_agent = db.relationship("User", foreign_keys=[reviewed_by_agent_id])

    def __repr__(self):
        return f"<PropertyPricingDetailRequestHistory req={self.request_id} client={self.client_id} property={self.property_id} [{self.status}]>"


def log_activity(action: str, description: str, actor=None) -> None:
    """Add an ActivityLog entry to the current DB session. Caller must commit."""
    if actor is None:
        try:
            from flask_login import current_user as _cu
            if _cu and _cu.is_authenticated:
                actor = _cu
        except Exception:
            pass
    db.session.add(ActivityLog(
        actor_id    = getattr(actor, "id",        None),
        actor_name  = getattr(actor, "full_name",  None) or getattr(actor, "email", None) or "System",
        actor_role  = getattr(actor, "role",       "system"),
        action      = action,
        description = description,
    ))


# ---------------------------------------------------------------------------
# SYSTEM CONFIG  (key-value qualification criteria + settings)
# ---------------------------------------------------------------------------

class SystemConfig(db.Model):
    __tablename__ = "system_config"

    id          = db.Column(db.Integer, primary_key=True)
    key         = db.Column(db.String(64),  unique=True, nullable=False, index=True)
    value       = db.Column(db.String(255), nullable=False)
    label       = db.Column(db.String(128))
    description = db.Column(db.String(512))
    updated_at  = db.Column(db.DateTime,
                            default=lambda: datetime.now(timezone.utc),
                            onupdate=lambda: datetime.now(timezone.utc))

    def __repr__(self):
        return f"<SystemConfig {self.key}={self.value}>"