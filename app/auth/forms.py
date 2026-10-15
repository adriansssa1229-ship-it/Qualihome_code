from flask_wtf import FlaskForm
from wtforms import (
    StringField, PasswordField, BooleanField, SelectField,
    IntegerField, DecimalField, SubmitField, DateField,
)
from wtforms.validators import (
    DataRequired, InputRequired, Email, EqualTo, Length, NumberRange,
    Optional, Regexp, ValidationError,
)
from ..models import User


def _strip_commas(value):
    """WTForms filter: remove thousands separators before Decimal conversion."""
    if isinstance(value, str):
        return value.replace(",", "")
    return value


class LoginForm(FlaskForm):
    email    = StringField("Email Address",
                           validators=[DataRequired(), Email()])
    password = PasswordField("Password",
                             validators=[DataRequired()])
    remember = BooleanField("Remember Me")
    submit   = SubmitField("Sign In")


class RegistrationForm(FlaskForm):
    """Combined account-creation + pre-qualification form (Get Qualified wizard)."""

    # ── Step 1 — Personal Info ────────────────────────────────────────────────
    first_name = StringField(
        "First Name",
        validators=[DataRequired(), Length(min=2, max=80)],
        render_kw={"placeholder": "e.g. Juan"},
    )
    middle_name = StringField(
        "Middle Name",
        validators=[DataRequired(), Length(min=2, max=80)],
        render_kw={"placeholder": "e.g. Santos"},
    )
    last_name = StringField(
        "Last Name",
        validators=[DataRequired(), Length(min=2, max=80)],
        render_kw={"placeholder": "e.g. Dela Cruz"},
    )
    username = StringField(
        "Username",
        validators=[
            DataRequired(),
            Length(min=3, max=60),
            Regexp(r'^[\w.]+$', message="Only letters, numbers, dots and underscores allowed."),
        ],
        render_kw={"placeholder": "e.g. juan.delacruz"},
    )
    email = StringField(
        "Email Address",
        validators=[DataRequired(), Email()],
        render_kw={"placeholder": "your@email.com"},
    )
    contact_number = StringField(
        "Contact Number",
        validators=[
            DataRequired(),
            Length(min=11, max=11, message="Contact number must be exactly 11 digits."),
            Regexp(r"^09\d{9}$", message="Enter a valid 11-digit mobile number (e.g. 09171234567)."),
        ],
        render_kw={"placeholder": "e.g. 09171234567"},
    )
    civil_status = SelectField(
        "Civil Status",
        choices=[
            ("", "-- Select --"),
            ("single", "Single"),
            ("married", "Married"),
            ("widowed", "Widowed"),
            ("separated", "Separated"),
        ],
        validators=[DataRequired(message="Please select your civil status.")],
    )
    citizenship = SelectField(
        "Citizenship",
        choices=[
            ("", "-- Select --"),
            ("filipino", "Filipino"),
            ("dual-citizen", "Dual Citizen"),
            ("foreign-national", "Foreign National"),
        ],
        validators=[DataRequired(message="Please select your citizenship.")],
    )
    gender = SelectField(
        "Gender",
        choices=[
            ("", "-- Select --"),
            ("male", "Male"),
            ("female", "Female"),
            ("non-binary", "Non-Binary"),
            ("prefer-not-to-say", "Prefer Not to Say"),
        ],
        validators=[DataRequired(message="Please select your gender.")],
    )
    dependents = IntegerField(
        "Number of Dependents",
        validators=[
            InputRequired(message="Please enter number of dependents."),
            NumberRange(min=0, max=20, message="Dependents must be between 0 and 20."),
        ],
        render_kw={"placeholder": "e.g. 0"},
    )
    birth_date = DateField(
        "Birthdate",
        format="%Y-%m-%d",
        validators=[DataRequired(message="Birthdate is required.")],
        render_kw={"type": "date"},
    )
    password = PasswordField(
        "Password",
        validators=[DataRequired(), Length(min=8, message="Password must be at least 8 characters.")],
        render_kw={"placeholder": "At least 8 characters"},
    )
    confirm_password = PasswordField(
        "Confirm Password",
        validators=[DataRequired(), EqualTo("password", message="Passwords must match.")],
        render_kw={"placeholder": "Repeat your password"},
    )

    # ── Step 2 — Financial Capability ────────────────────────────────────────
    gross_monthly_income = DecimalField(
        "Gross Monthly Income (₱)",
        places=2,
        filters=[_strip_commas],
        validators=[
            DataRequired(message="Gross monthly income is required."),
            NumberRange(min=1, max=10000000, message="Gross monthly income must be between ₱1.00 and ₱10,000,000.00."),
        ],
        render_kw={"placeholder": "e.g. 35000.00", "type": "text", "inputmode": "decimal"},
    )
    monthly_debt_loans = DecimalField(
        "Monthly Debt / Loan Payments (₱)",
        places=2,
        filters=[_strip_commas],
        validators=[
            Optional(),
            NumberRange(min=0, max=10000000, message="Monthly debt must not exceed ₱10,000,000.00."),
        ],
        render_kw={"placeholder": "Car loan, personal loan, credit card, etc.", "type": "text", "inputmode": "decimal"},
    )
    # ── Step 2 — Employment ───────────────────────────────────────────────────
    employment_status = SelectField(
        "Employment Status",
        choices=[
            ("", "-- Select --"),
            ("employed", "Employed"),
            ("ofw-landbased", "OFW - LandBased"),
            ("ofw-seafarer", "OFW - Seaferer"),
            ("licensed-professional", "Licensed Professional"),
            ("with-financial-support", "With Financial Support"),
            ("with-attorney-in-fact", "With Attorney In-Fact"),
            ("with-co-borrower", "With Co-Borrower"),
        ],
        validators=[DataRequired(message="Please select your employment status.")],
    )
    tenure_months = IntegerField(
        "Tenure (months)",
        validators=[Optional(), NumberRange(min=0, max=600)],
        render_kw={"placeholder": "e.g. 24 (= 2 years)"},
    )
    age = IntegerField(
        "Age",
        validators=[DataRequired(), NumberRange(min=18, max=80, message="Age must be between 18 and 80.")],
        render_kw={"placeholder": "e.g. 28"},
    )
    sss_gsis_umid = StringField(
        "SSS/GSIS/UMID",
        validators=[Optional(), Length(max=60)],
        render_kw={"placeholder": "e.g. 33-1234567-8"},
    )
    tin_no = StringField(
        "Tax Identification No (TIN)",
        validators=[Optional(), Length(max=30)],
        render_kw={"placeholder": "e.g. 123-456-789-000"},
    )

    # ── Step 3 — Document Honesty Declaration ───────────────────────────────
    has_valid_id = SelectField(
        "Do you currently have one valid ID?",
        choices=[
            ("", "-- Select --"),
            ("yes", "Yes"),
            ("no", "No"),
        ],
        validators=[DataRequired(message="Please confirm if you have a valid ID.")],
    )
    has_income_proof = SelectField(
        "Do you currently have proof of income?",
        choices=[
            ("", "-- Select --"),
            ("yes", "Yes"),
            ("no", "No"),
        ],
        validators=[DataRequired(message="Please confirm if you have proof of income.")],
    )

    # ── Step 3 — Model Preferences ─────────────────────────────────────────
    preferred_type = SelectField(
        "Model Type",
        choices=[
            ("", "Any model (optional)"),
        ],
        default="",
        validators=[Optional()],
    )
    budget_min = DecimalField(
        "Minimum Budget (₱)",
        places=2,
        filters=[_strip_commas],
        validators=[DataRequired(message="Minimum budget is required."), NumberRange(min=1, message="Minimum budget must be greater than 0.")],
        render_kw={"placeholder": "e.g. 1500000.00", "type": "text", "inputmode": "decimal"},
    )
    budget_max = DecimalField(
        "Maximum Budget (₱)",
        places=2,
        filters=[_strip_commas],
        validators=[DataRequired(message="Maximum budget is required."), NumberRange(min=1, message="Maximum budget must be greater than 0.")],
        render_kw={"placeholder": "e.g. 4000000.00", "type": "text", "inputmode": "decimal"},
    )

    submit = SubmitField("Submit & Get Results")

    # ── Custom validators ─────────────────────────────────────────────────────
    def validate_email(self, field):
        if User.query.filter_by(email=field.data.lower()).first():
            raise ValidationError("This email is already registered. Please log in.")

    def validate_username(self, field):
        if User.query.filter_by(username=field.data.strip()).first():
            raise ValidationError("This username is already taken. Please choose another.")

    def validate_budget_max(self, field):
        min_budget = self.budget_min.data or 0
        max_budget = field.data or 0
        if max_budget < min_budget:
            raise ValidationError("Maximum budget must be greater than or equal to minimum budget.")


class ForgotPasswordForm(FlaskForm):
    email  = StringField("Email Address",
                          validators=[DataRequired(), Email()])
    submit = SubmitField("Send Reset Link")


class ResetPasswordForm(FlaskForm):
    password = PasswordField(
        "New Password",
        validators=[
            DataRequired(),
            Length(min=8, message="Password must be at least 8 characters."),
            Regexp(
                r"^(?=.*[A-Z])(?=.*\d)(?=.*[^A-Za-z0-9]).+$",
                message="Password must include at least 1 uppercase letter, 1 number, and 1 symbol.",
            ),
        ],
    )
    confirm_password = PasswordField(
        "Confirm New Password",
        validators=[DataRequired(), EqualTo("password", message="Passwords must match.")],
    )
    submit = SubmitField("Reset Password")
