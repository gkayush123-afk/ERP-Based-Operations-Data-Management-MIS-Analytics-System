from flask_wtf import FlaskForm
from wtforms import PasswordField, SelectField, StringField, SubmitField
from wtforms.validators import (
    DataRequired,
    Email,
    Length,
    Regexp,
)

from ..auth.forms import validate_password_strength

ROLE_CHOICES = [
    ("admin", "Admin"),
    ("manager", "Manager"),
    ("data_entry", "Data-entry"),
]


class UserCreateForm(FlaskForm):
    username = StringField(
        "Username",
        validators=[
            DataRequired(),
            Length(min=3, max=80),
            Regexp(r"^[A-Za-z0-9_.-]+$", message="Use letters, numbers, periods, underscores, or hyphens."),
        ],
    )
    full_name = StringField("Full name", validators=[DataRequired(), Length(max=160)])
    email = StringField("Email", validators=[DataRequired(), Email(), Length(max=254)])
    role = SelectField("Role", choices=ROLE_CHOICES, validators=[DataRequired()])
    department_id = SelectField("Department", coerce=int, validators=[DataRequired()])
    temporary_password = PasswordField(
        "Temporary password",
        validators=[
            DataRequired(),
            Length(min=12, message="Temporary password must be at least 12 characters."),
            validate_password_strength,
        ],
    )
    submit = SubmitField("Create user")


class UserEditForm(FlaskForm):
    role = SelectField("Role", choices=ROLE_CHOICES, validators=[DataRequired()])
    department_id = SelectField("Department", coerce=int, validators=[DataRequired()])
    submit = SubmitField("Save changes")


class TemporaryPasswordForm(FlaskForm):
    temporary_password = PasswordField(
        "New temporary password",
        validators=[
            DataRequired(),
            Length(min=12, message="Temporary password must be at least 12 characters."),
            validate_password_strength,
        ],
    )
    submit = SubmitField("Reset password")

