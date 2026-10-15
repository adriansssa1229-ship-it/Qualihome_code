import re

from flask_wtf import FlaskForm
from wtforms import (
    IntegerField, DecimalField, SelectField, StringField, SubmitField,
)
from wtforms.validators import DataRequired, Length, NumberRange, Optional, ValidationError


def _strip_commas(value):
    """WTForms filter: remove thousands separators before Decimal conversion."""
    if isinstance(value, str):
        return value.replace(",", "")
    return value


class Step1FinancialForm(FlaskForm):
    """Step 1 — Financial Capability."""

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
    submit = SubmitField("Next: Employment Info →")


class Step2EmploymentForm(FlaskForm):
    """Step 2 — Employment."""

    employment_status = SelectField(
        "Employment Status",
        choices=[
            ("", "— Select —"),
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
    dependents = IntegerField(
        "Number of Dependents",
        validators=[Optional(), NumberRange(min=0, max=20, message="Dependents must be between 0 and 20.")],
        render_kw={"placeholder": "e.g. 0"},
    )
    submit = SubmitField("Next: Housing Preferences →")


class QualifyForm(FlaskForm):
    """Combined single-page qualification wizard (all 4 steps in one POST)."""

    # ── Step 1: Financial ──────────────────────────────────────────
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
    sss_gsis_umid = StringField(
        "SSS/GSIS/UMID",
        validators=[Optional(), Length(max=60)],
        render_kw={
            "placeholder": "00-0000000-0",
            "inputmode": "numeric",
            "maxlength": 13,
        },
    )
    tin_no = StringField(
        "Tax Identification No (TIN)",
        validators=[Optional(), Length(max=30)],
        render_kw={
            "placeholder": "000-000-000-000",
            "inputmode": "numeric",
            "maxlength": 15,
        },
    )
    # ── Step 2: Employment ─────────────────────────────────────────
    employment_status = SelectField(
        "Employment Status",
        choices=[
            ("", "— Select —"),
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
        validators=[
            DataRequired(),
            NumberRange(min=18, max=80, message="Age must be between 18 and 80."),
        ],
        render_kw={"placeholder": "e.g. 28"},
    )
    dependents = IntegerField(
        "Number of Dependents",
        validators=[Optional(), NumberRange(min=0, max=20, message="Dependents must be between 0 and 20.")],
        render_kw={"placeholder": "e.g. 0"},
    )

    # ── Step 3: Model Preferences ──────────────────────────────────────────
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

    submit = SubmitField("Submit & View Results")

    def validate_budget_max(self, field):
        min_budget = self.budget_min.data or 0
        max_budget = field.data or 0
        if max_budget < min_budget:
            raise ValidationError("Maximum budget must be greater than or equal to minimum budget.")

    def validate_sss_gsis_umid(self, field):
        value = (field.data or "").strip()
        if not value:
            return
        if not re.fullmatch(r"\d{2}-\d{7}-\d", value):
            raise ValidationError("SSS/GSIS/UMID must follow 00-0000000-0 format.")

    def validate_tin_no(self, field):
        value = (field.data or "").strip()
        if not value:
            return
        if not re.fullmatch(r"\d{3}-\d{3}-\d{3}-\d{3}", value):
            raise ValidationError("TIN must follow 000-000-000-000 format.")
