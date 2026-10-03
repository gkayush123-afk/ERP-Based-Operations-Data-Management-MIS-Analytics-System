from flask_wtf import FlaskForm
from wtforms import BooleanField, StringField, SubmitField
from wtforms.validators import DataRequired, Length


class DepartmentForm(FlaskForm):
    name = StringField(
        "Department name",
        validators=[DataRequired(message="Enter a department name."), Length(max=120)],
    )
    is_active = BooleanField("Active department")
    submit = SubmitField("Save department")
