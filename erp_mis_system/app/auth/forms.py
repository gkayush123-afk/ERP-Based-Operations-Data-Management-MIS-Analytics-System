import re

from flask_wtf import FlaskForm
from wtforms import BooleanField, PasswordField, SelectField, StringField, SubmitField
from wtforms.validators import (
    DataRequired,
    Email,
    EqualTo,
    Length,
    Regexp,
    ValidationError,
)


def validate_password_strength(_form, field):
    password = field.data or ""
    character_groups = (
        bool(re.search(r"[a-z]", password)),
        bool(re.search(r"[A-Z]", password)),
        bool(re.search(r"\d", password)),
        bool(re.search(r"[^A-Za-z0-9]", password)),
    )
    if sum(character_groups) < 3:
        raise ValidationError(
            "Use at least three of: lowercase letters, uppercase letters, numbers, and symbols."
        )


class LoginForm(FlaskForm):
    username = StringField(
        "Username",
        validators=[DataRequired(message="Enter your username."), Length(max=80)],
    )
    password = PasswordField(
        "Password",
        validators=[DataRequired(message="Enter your password.")],
    )
    remember = BooleanField("Remember me")
    submit = SubmitField("Log in")


class RegistrationForm(FlaskForm):
    full_name = StringField(
        "Full name",
        validators=[DataRequired(message="Enter your full name."), Length(max=160)],
    )
    username = StringField(
        "Username",
        validators=[
            DataRequired(message="Choose a username."),
            Length(min=3, max=80, message="Username must be 3 to 80 characters."),
            Regexp(
                r"^[A-Za-z0-9_.-]+$",
                message="Use only letters, numbers, periods, underscores, and hyphens.",
            ),
        ],
    )
    email = StringField(
        "Email",
        validators=[
            DataRequired(message="Enter your email address."),
            Email(message="Enter a valid email address."),
            Length(max=254),
        ],
    )
    department_id = SelectField(
        "Department",
        coerce=int,
        validators=[DataRequired(message="Choose a department.")],
    )
    password = PasswordField(
        "Password",
        validators=[
            DataRequired(message="Choose a password."),
            Length(min=8, message="Password must be at least 8 characters."),
            validate_password_strength,
        ],
    )
    confirm_password = PasswordField(
        "Confirm password",
        validators=[
            DataRequired(message="Confirm your password."),
            EqualTo("password", message="The passwords do not match."),
        ],
    )
    submit = SubmitField("Create account")


class ChangePasswordForm(FlaskForm):
    current_password = PasswordField(
        "Current password",
        validators=[DataRequired(message="Enter your current password.")],
    )
    new_password = PasswordField(
        "New password",
        validators=[
            DataRequired(message="Choose a new password."),
            Length(min=8, message="Password must be at least 8 characters."),
            validate_password_strength,
        ],
    )
    confirm_password = PasswordField(
        "Confirm new password",
        validators=[
            DataRequired(message="Confirm your new password."),
            EqualTo("new_password", message="The passwords do not match."),
        ],
    )
    submit = SubmitField("Change password")

    def validate_current_password(self, field):
        from flask_login import current_user

        if not current_user.check_password(field.data):
            raise ValidationError("Your current password is incorrect.")
