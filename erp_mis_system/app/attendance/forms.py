from datetime import date

from flask_wtf import FlaskForm
from wtforms import DateField, SelectField, StringField, SubmitField, TimeField
from wtforms.validators import DataRequired, Length, Optional, ValidationError

ATTENDANCE_STATUSES = [
    ("present", "Present"),
    ("absent", "Absent"),
    ("leave", "Leave"),
    ("half_day", "Half-day"),
]
VERIFICATION_STATUSES = [
    ("pending", "Pending"),
    ("verified", "Verified"),
    ("rejected", "Rejected"),
]


class AttendanceForm(FlaskForm):
    employee_id = SelectField("Employee", coerce=int, validators=[DataRequired()])
    attendance_date = DateField(
        "Date",
        format="%Y-%m-%d",
        validators=[DataRequired(message="Enter a valid attendance date.")],
    )
    status = SelectField("Attendance status", choices=ATTENDANCE_STATUSES, validators=[DataRequired()])
    in_time = TimeField("In time", format="%H:%M", validators=[Optional()])
    out_time = TimeField("Out time", format="%H:%M", validators=[Optional()])
    remarks = StringField("Remarks", validators=[Optional(), Length(max=1000)])
    submit = SubmitField("Save attendance")

    def validate_attendance_date(self, field):
        if field.data and field.data > date.today():
            raise ValidationError("Attendance date cannot be in the future.")

    def validate_out_time(self, field):
        if field.data and self.in_time.data and field.data < self.in_time.data:
            raise ValidationError("Out time cannot be earlier than in time.")


class RejectAttendanceForm(FlaskForm):
    rejection_reason = StringField(
        "Reason for rejection",
        validators=[
            DataRequired(message="Enter a reason for rejecting this attendance record."),
            Length(max=500),
        ],
    )
    submit = SubmitField("Reject record")
