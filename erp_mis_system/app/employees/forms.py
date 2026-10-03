import re
from datetime import date

from flask_wtf import FlaskForm
from wtforms import DateField, SelectField, StringField, SubmitField
from wtforms.validators import DataRequired, Email, Length, Optional, Regexp


PHONE_PATTERN = r"^\+?[0-9().\-\s]{7,30}$"
STATUS_CHOICES = [("active", "Active"), ("inactive", "Inactive")]


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
    phone = StringField(
        "Phone",
        validators=[
            Optional(),
            Length(max=30),
            Regexp(PHONE_PATTERN, message="Enter a valid phone number."),
        ],
    )
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
    submit = SubmitField("Save employee")

    def validate_joining_date(self, field):
        if field.data and field.data > date.today():
            from wtforms.validators import ValidationError

            raise ValidationError("Joining date cannot be in the future.")


def valid_phone(value):
    return not value or re.fullmatch(PHONE_PATTERN, value) is not None
