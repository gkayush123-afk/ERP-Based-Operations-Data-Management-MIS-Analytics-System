import re
from datetime import date

from flask_wtf import FlaskForm
from wtforms import DateField, SelectField, StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Email, Length, Optional, Regexp


PHONE_PATTERN = r"^\+?[0-9().\-\s]{7,30}$"
STATUS_CHOICES = [("active", "Active"), ("inactive", "Inactive")]
GENDER_CHOICES = [("male", "Male"), ("female", "Female"), ("other", "Other")]
MARITAL_STATUS_CHOICES = [
    ("single", "Single"),
    ("married", "Married"),
    ("divorced", "Divorced"),
    ("widowed", "Widowed"),
]
BLOOD_GROUP_CHOICES = [
    ("", "— Select —"),
    ("A+", "A+"),
    ("A-", "A-"),
    ("B+", "B+"),
    ("B-", "B-"),
    ("AB+", "AB+"),
    ("AB-", "AB-"),
    ("O+", "O+"),
    ("O-", "O-"),
]


class EmployeeForm(FlaskForm):
    employee_code = StringField(
        "Employee code",
        validators=[
            DataRequired(message="Enter an employee code."),
            Length(max=40),
            Regexp(
                r"^[A-Za-z0-9._/-]+$",
                message="Use only letters, numbers, periods, underscores, hyphens, or slashes.",
            ),
        ],
    )
    full_name = StringField(
        "Full name",
        validators=[DataRequired(message="Enter the employee's full name."), Length(max=160)],
    )
    email = StringField(
        "Email",
        validators=[
            DataRequired(message="Enter an email address."),
            Email(message="Enter a valid email address."),
            Length(max=254),
        ],
    )
    date_of_birth = DateField(
        "Date of birth",
        format="%Y-%m-%d",
        validators=[Optional()],
    )
    gender = SelectField("Gender", choices=GENDER_CHOICES, validators=[Optional()])
    phone = StringField(
        "Phone",
        validators=[
            Optional(),
            Length(max=30),
            Regexp(PHONE_PATTERN, message="Enter a valid phone number."),
        ],
    )
    address = TextAreaField("Residential address", validators=[Optional(), Length(max=500)])
    emergency_contact_name = StringField("Emergency contact name", validators=[Optional(), Length(max=160)])
    emergency_contact_phone = StringField(
        "Emergency contact phone",
        validators=[
            Optional(),
            Length(max=30),
            Regexp(PHONE_PATTERN, message="Enter a valid phone number."),
        ],
    )
    emergency_contact_relationship = StringField("Relationship", validators=[Optional(), Length(max=50)])
    photo_url = StringField("Photo URL", validators=[Optional(), Length(max=500)])
    blood_group = SelectField("Blood group", choices=BLOOD_GROUP_CHOICES, validators=[Optional()])
    marital_status = SelectField("Marital status", choices=MARITAL_STATUS_CHOICES, validators=[Optional()])
    nationality = StringField("Nationality", validators=[Optional(), Length(max=80)])
    id_number = StringField("ID number (Passport/Driving License)", validators=[Optional(), Length(max=60)])
    designation = StringField(
        "Designation",
        validators=[DataRequired(message="Enter a designation."), Length(max=120)],
    )
    department_id = SelectField("Department", coerce=int, validators=[DataRequired()])
    joining_date = DateField(
        "Joining date",
        format="%Y-%m-%d",
        validators=[DataRequired(message="Enter a valid joining date.")],
    )
    status = SelectField("Status", choices=STATUS_CHOICES, validators=[DataRequired()])
    bank_account_number = StringField("Bank account number", validators=[Optional(), Length(max=60)])
    bank_name = StringField("Bank name", validators=[Optional(), Length(max=120)])
    bank_ifsc = StringField("Bank IFSC", validators=[Optional(), Length(max=20)])
    pan_number = StringField("PAN number", validators=[Optional(), Length(max=20)])
    aadhar_number = StringField("Aadhar number", validators=[Optional(), Length(max=20)])
    pf_number = StringField("PF number", validators=[Optional(), Length(max=40)])
    uan_number = StringField("UAN number", validators=[Optional(), Length(max=40)])
    esi_number = StringField("ESI number", validators=[Optional(), Length(max=40)])
    submit = SubmitField("Save employee")

    def validate_joining_date(self, field):
        if field.data and field.data > date.today():
            from wtforms.validators import ValidationError

            raise ValidationError("Joining date cannot be in the future.")

    def validate_date_of_birth(self, field):
        if field.data and field.data > date.today():
            from wtforms.validators import ValidationError

            raise ValidationError("Date of birth cannot be in the future.")


def valid_phone(value):
    return not value or re.fullmatch(PHONE_PATTERN, value) is not None
