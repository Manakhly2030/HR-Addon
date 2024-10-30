from __future__ import unicode_literals
import frappe
from frappe import _
from frappe.utils.data import date_diff, time_diff_in_hours
from frappe.utils import get_datetime, getdate, today, comma_sep, flt,add_days, date_diff, cstr
from frappe.core.doctype.role.role import get_info_based_on_role
from frappe.query_builder import DocType
from frappe.query_builder import DocType
from frappe.query_builder.functions import Count


def get_employee_checkin(employee,atime):
    ''' select DATE('date time');'''
    employee = employee
    atime = atime
    checkin_list = frappe.db.sql(
        """
        SELECT  name,log_type,time,skip_auto_attendance,attendance FROM `tabEmployee Checkin` 
        WHERE employee='%s' AND DATE(time)= DATE('%s') ORDER BY time ASC
        """%(employee,atime), as_dict=1
    )
    return checkin_list or []

def get_employee_default_work_hour(aemployee,adate):
    ''' weekly working hour'''
    employee = aemployee
    adate = adate    
    #validate current or active FY year WHERE --
    # AND YEAR(valid_from) = CAST(%(year)s as INT) AND YEAR(valid_to) = CAST(%(year)s as INT)
    # AND YEAR(w.valid_from) = CAST(('2022-01-01') as INT) AND YEAR(w.valid_to) = CAST(('2022-12-30') as INT);
    # Convert date to datetime object and get the day name
    adate = getdate(adate)
    dayname = adate.strftime('%A')  # Get the day name (e.g., 'Monday', 'Tuesday')

    # Define the doctypes
    WeeklyWorkingHours = DocType("Weekly Working Hours")
    DailyHoursDetail = DocType("Daily Hours Detail")

    # Build the query using Frappe's query builder
    query = (
        frappe.qb.from_(WeeklyWorkingHours)
        .left_join(DailyHoursDetail)
        .on(WeeklyWorkingHours.name == DailyHoursDetail.parent)
        .select(
            WeeklyWorkingHours.name,
            WeeklyWorkingHours.employee,
            WeeklyWorkingHours.valid_from,
            WeeklyWorkingHours.valid_to,
            DailyHoursDetail.day,
            DailyHoursDetail.hours,
            DailyHoursDetail.break_minutes
        )
        .where(
            (WeeklyWorkingHours.employee == aemployee)
            & (DailyHoursDetail.day == dayname)
            & (WeeklyWorkingHours.valid_from <= adate)
            & (WeeklyWorkingHours.valid_to >= adate)
            & (WeeklyWorkingHours.docstatus == 1)
        )
    )

    # Execute the query
    target_work_hours = query.run(as_dict=True)

    if not target_work_hours:
        frappe.throw(_('Please create Weekly Working Hours for the selected Employee:{0} first.').format(employee))

    if len(target_work_hours) > 1:
        target_work_hours= "<br> ".join([frappe.get_desk_link("Weekly Working Hours", w.name) for w in target_work_hours])
        frappe.throw(_('There exist multiple Weekly Working Hours exist for the Date <b>{0}</b>: <br>{1} <br>').format(adate, target_work_hours))

    return target_work_hours[0]


@frappe.whitelist()
def get_missing_workdays(employee, date_from, date_to):
    """
    Get the list of missing workdays for an employee between two dates using Frappe's query builder.
    """
    # Validate the date range
    date_from = getdate(date_from)
    date_to = getdate(date_to)

    # Calculate the number of days in the range
    total_days = date_diff(date_to, date_from) + 1

    missing_workdays = []

    # Define the doctypes
    WeeklyWorkingHours = DocType("Weekly Working Hours")
    DailyHoursDetail = DocType("Daily Hours Detail")

    # Loop through each day in the date range
    for i in range(total_days):
        current_date = add_days(date_from, i)

        # Build the query using Frappe query builder
        query = (
            frappe.qb.from_(WeeklyWorkingHours)
            .left_join(DailyHoursDetail)
            .on(WeeklyWorkingHours.name == DailyHoursDetail.parent)
            .select(
                WeeklyWorkingHours.name,
                WeeklyWorkingHours.employee,
                WeeklyWorkingHours.valid_from,
                WeeklyWorkingHours.valid_to,
                DailyHoursDetail.day,
                DailyHoursDetail.hours,
                DailyHoursDetail.break_minutes
            )
            .where(
                (WeeklyWorkingHours.employee == employee) &
                (WeeklyWorkingHours.valid_from <= current_date) &
                (WeeklyWorkingHours.valid_to >= current_date) &
                (WeeklyWorkingHours.docstatus == 1)
            )
        )

        # Execute the query
        target_work_hours = query.run(as_dict=True)

        # If no working hours are found for this date, add the date to missing workdays
        if not target_work_hours:
            missing_workdays.append(current_date)

    # Log missing workdays if any, and return the result
    if missing_workdays:
        missing_workdays_str = ', '.join([date.strftime('%Y-%m-%d') for date in missing_workdays])
        frappe.log_error(
            title="Missing Workdays During Bulk Workday Creation",
            message=f"Missing workdays for employee {employee}: {missing_workdays_str}"
        )
        return missing_workdays
    else:
        return 0



@frappe.whitelist()
def get_actual_employee_log(aemployee, adate):
    '''total actual log'''
    employee_checkins = get_employee_checkin(aemployee,adate)

    # check empty or none
    if not employee_checkins:
        frappe.msgprint("No Checkin found for {0} on date {1}".format(frappe.get_desk_link("Employee", aemployee) ,adate))
        return

    employee_default_work_hour = get_employee_default_work_hour(aemployee,adate)
    is_date_in_holiday_list = date_is_in_holiday_list(aemployee,adate)
    fields=["name", "no_break_hours", "set_target_hours_to_zero_when_date_is_holiday"]
    weekly_working_hours = frappe.db.get_list(doctype="Weekly Working Hours", filters={"employee": aemployee}, fields=fields)
    no_break_hours = True if len(weekly_working_hours) > 0 and weekly_working_hours[0]["no_break_hours"] == 1 else False
    is_target_hours_zero_on_holiday = len(weekly_working_hours) > 0 and weekly_working_hours[0]["set_target_hours_to_zero_when_date_is_holiday"] == 1
    
    new_workday = get_workday(employee_checkins, employee_default_work_hour, no_break_hours, is_target_hours_zero_on_holiday, is_date_in_holiday_list)

    return new_workday


def get_workday(employee_checkins, employee_default_work_hour, no_break_hours, is_target_hours_zero_on_holiday, is_date_in_holiday_list=False):
    new_workday = {}

    hours_worked = 0.0
    break_hours = 0.0

    # not pair of IN/OUT either missing
    if len(employee_checkins)% 2 != 0:
        employee_checkin_message = ""
        for d in employee_checkins:
            employee_checkin_message += "<li>CheckIn Type:{0} for {1}</li>".format(d.log_type, frappe.get_desk_link("Employee Checkin", d.name))

        frappe.msgprint("CheckIns must be in pair for the given date:<ul>{}</ul>".format(employee_checkin_message))
        return new_workday

    if (len(employee_checkins) % 2 == 0):
        # seperate 'IN' from 'OUT'
        clockin_list = [get_datetime(kin.time) for x,kin in enumerate(employee_checkins) if x % 2 == 0]
        clockout_list = [get_datetime(kout.time) for x,kout in enumerate(employee_checkins) if x % 2 != 0]

        # get total worked hours
        for i in range(len(clockin_list)):
            wh = time_diff_in_hours(clockout_list[i],clockin_list[i])
            hours_worked += float(str(wh))
        
        # get total break hours
        for i in range(len(clockout_list)):
            if ((i+1) < len(clockout_list)):
                wh = time_diff_in_hours(clockin_list[i+1],clockout_list[i])
                break_hours += float(str(wh))

    break_minutes = employee_default_work_hour.break_minutes
    target_hours = employee_default_work_hour.hours

    total_target_seconds = target_hours * 60 * 60
    total_work_seconds = flt(hours_worked * 60 * 60)
    expected_break_hours = flt(break_minutes / 60)
    total_break_seconds = flt(break_hours * 60 * 60)
    break_hours = flt(break_hours)
    hours_worked = flt(hours_worked)
    actual_working_hours = hours_worked - expected_break_hours
    attendance = employee_checkins[0].attendance if len(employee_checkins) > 0 else ""

    if no_break_hours and hours_worked < 6: # TODO: set 6 as constant
        break_minutes = 0
        total_break_seconds = 0
        expected_break_hours = 0
        actual_working_hours = hours_worked

    if is_target_hours_zero_on_holiday and is_date_in_holiday_list:
        target_hours = 0
        total_target_seconds = 0

    hr_addon_settings = frappe.get_doc("HR Addon Settings")
    if hr_addon_settings.enable_default_break_hour_for_shorter_breaks:
        default_break_hours = flt(employee_default_work_hour.break_minutes/60)
        if break_hours <= default_break_hours:
            break_hours = flt(default_break_hours)

    # if target_hours == 0:
    #     expected_break_hours = 0
    #     total_break_seconds = 0

    new_workday.update({
        "target_hours": target_hours,
        "total_target_seconds": total_target_seconds,
        "break_minutes": break_minutes,
        "hours_worked": hours_worked,
        "expected_break_hours": expected_break_hours,
        "actual_working_hours": actual_working_hours,
        "total_work_seconds": total_work_seconds,
        "nbreak": 0,
        "attendance": attendance,        
        "break_hours": break_hours,
        "total_break_seconds": total_break_seconds,
        "employee_checkins":employee_checkins,
    })

    return new_workday


@frappe.whitelist()
def get_actual_employee_log_for_bulk_process(aemployee, adate):

    employee_checkins = get_employee_checkin(aemployee, adate)
    #employee_default_work_hour = get_employee_default_work_hour(aemployee, adate)

    # Convert date to datetime object and get the day name
    adate = getdate(adate)
    dayname = adate.strftime('%A')  # Get the day name (e.g., 'Monday', 'Tuesday')

    # Define the doctypes
    WeeklyWorkingHours = DocType("Weekly Working Hours")
    DailyHoursDetail = DocType("Daily Hours Detail")

    # Build the query using Frappe's query builder
    query = (
        frappe.qb.from_(WeeklyWorkingHours)
        .left_join(DailyHoursDetail)
        .on(WeeklyWorkingHours.name == DailyHoursDetail.parent)
        .select(
            WeeklyWorkingHours.name,
            WeeklyWorkingHours.employee,
            WeeklyWorkingHours.valid_from,
            WeeklyWorkingHours.valid_to,
            DailyHoursDetail.day,
            DailyHoursDetail.hours,
            DailyHoursDetail.break_minutes
        )
        .where(
            (WeeklyWorkingHours.employee == aemployee)
            & (DailyHoursDetail.day == dayname)
            & (WeeklyWorkingHours.valid_from <= adate)
            & (WeeklyWorkingHours.valid_to >= adate)
            & (WeeklyWorkingHours.docstatus == 1)
        )
    )

    # Execute the query
    target_work_hours = query.run(as_dict=True)

    if len(target_work_hours) == 1:
        employee_default_work_hour = target_work_hours[0]

        if employee_checkins:
            is_date_in_holiday_list = date_is_in_holiday_list(aemployee, adate)
            fields=["name", "no_break_hours", "set_target_hours_to_zero_when_date_is_holiday"]
            weekly_working_hours = frappe.db.get_list(doctype="Weekly Working Hours", filters={"employee": aemployee}, fields=fields)
            no_break_hours = True if len(weekly_working_hours) > 0 and weekly_working_hours[0]["no_break_hours"] == 1 else False
            is_target_hours_zero_on_holiday = len(weekly_working_hours) > 0 and weekly_working_hours[0]["set_target_hours_to_zero_when_date_is_holiday"] == 1
            new_workday = get_workday(employee_checkins, employee_default_work_hour, no_break_hours, is_target_hours_zero_on_holiday, is_date_in_holiday_list)
        else:
            view_employee_attendance = get_employee_attendance(aemployee, adate)

            new_workday = {
            "target_hours": employee_default_work_hour.hours,
            "break_minutes": employee_default_work_hour.break_minutes,
            "hours_worked": 0,
            "nbreak": 0,
            "attendance": view_employee_attendance[0].name if len(view_employee_attendance) > 0 else "",
            "break_hours": 0,
            "employee_checkins":[],
            }

        return new_workday


def get_employee_attendance(employee,atime):
    ''' select DATE('date time');'''
    employee = employee
    atime = atime
    
    attendance_list = frappe.db.sql(
        """
        SELECT  name,employee,status,attendance_date,shift FROM `tabAttendance` 
        WHERE employee='%s' AND DATE(attendance_date)= DATE('%s') AND docstatus = 1 ORDER BY attendance_date ASC
        """%(employee,atime), as_dict=1
    )
    return attendance_list


@frappe.whitelist()
def date_is_in_holiday_list(employee, date):
	holiday_list = frappe.db.get_value("Employee", employee, "holiday_list")
	if not holiday_list:
		frappe.msgprint(_("Holiday list not set in {0}").format(employee))
		return False

	holidays = frappe.db.sql(
        """
            SELECT holiday_date FROM `tabHoliday`
            WHERE parent=%s AND holiday_date=%s
        """,(holiday_list, getdate(date))
    )

	return len(holidays) > 0


# ----------------------------------------------------------------------
# WORK ANNIVERSARY REMINDERS SEND TO EMPLOYEES LIST IN HR-ADDON-SETTINGS
# ----------------------------------------------------------------------
def send_work_anniversary_notification():
    if not int(frappe.db.get_single_value("HR Addon Settings", "enable_work_anniversaries_notification")):
        return
    
    """
        Sending email to employees set in HR Addon Settings field anniversary_notification_email_list.
        Filtering recipient employees from just in case employees inactive at some later point in time.
    """
    Employee = DocType("Employee")
    EmployeeItem = DocType("Employee Item")
    emp_email_list = (
        frappe.qb.from_(Employee)
        .join(EmployeeItem)
        .on(Employee.name == EmployeeItem.employee)
        .where(
            (Employee.status == "Active") &
            (EmployeeItem.parent == "HR Addon Settings") &
            (EmployeeItem.parentfield == "anniversary_notification_email_list")
        )
        .select(Employee.name, Employee.user_id, Employee.personal_email, Employee.company_email, Employee.company)
    ).run(as_dict=True)

    recipients = []
    for employee in emp_email_list:
        employee_email = employee.get("user_id") or employee.get("personal_email") or employee.get("company_email")
        if employee_email:
            recipients.append({"employee_email": employee_email, "company": employee.company})

    joining_date = today()
    employees_joined_today = get_employees_having_an_event_on_given_date("work_anniversary", joining_date)
    send_emails(employees_joined_today, recipients, joining_date)

    ############## Sending email to specified employees with Role in HR Addon Settings field anniversary_notification_email_recipient_role
    email_recipient_role = frappe.db.get_single_value("HR Addon Settings", "anniversary_notification_email_recipient_role")
    notification_x_days_before = int(frappe.db.get_single_value("HR Addon Settings", "notification_x_days_before"))
    joining_date = frappe.utils.add_days(today(), notification_x_days_before)
    employees_joined_seven_days_later = get_employees_having_an_event_on_given_date("work_anniversary", joining_date)
    if email_recipient_role:
        role_email_recipients = []
        users_with_role = get_info_based_on_role(email_recipient_role, field="email")
        for user in users_with_role:
            user_data = frappe.get_cached_value("Employee", {"user_id": user}, ["company", "user_id"], as_dict=True)
            if user_data:
                role_email_recipients.extend([{"employee_email": user_data.get("user_id"), "company": user_data.get("company")}])
            else:
                # TODO: if user not found in employee, then what?
                pass

        if role_email_recipients:
            send_emails(employees_joined_seven_days_later, role_email_recipients, joining_date)

    ############## Sending email to specified employee leave approvers if HR Addon Settings field enable_work_anniversaries_notification_for_leave_approvers is checked
    if int(frappe.db.get_single_value("HR Addon Settings", "enable_work_anniversaries_notification_for_leave_approvers")):
        leave_approvers_email_list = {}
        for company, anniversary_persons in employees_joined_seven_days_later.items():
            leave_approvers_email_list.setdefault(company, {"leave_approver_missing": []})
            for anniversary_person in anniversary_persons:
                leave_approver = anniversary_person.get("leave_approver")
                approver_key = leave_approver if leave_approver else "leave_approver_missing"
                leave_approvers_email_list[company].setdefault(approver_key, [])
                leave_approvers_email_list[company][approver_key].append(anniversary_person)

        for company, leave_approvers_email_list_by_company in leave_approvers_email_list.items():
            for leave_approver, anniversary_persons in leave_approvers_email_list_by_company.items():
                if leave_approver != "leave_approver_missing":
                    reminder_text, message = get_work_anniversary_reminder_text_and_message(anniversary_persons, joining_date)
                    send_work_anniversary_reminder(leave_approver, reminder_text, anniversary_persons, message)


def send_emails(employees_joined_today, recipients, joining_date):

    for company, anniversary_persons in employees_joined_today.items():
        reminder_text, message = get_work_anniversary_reminder_text_and_message(anniversary_persons, joining_date)
        recipients_by_company = [d.get('employee_email') for d in recipients if d.get('company') == company ]
        if recipients_by_company:
            send_work_anniversary_reminder(recipients_by_company, reminder_text, anniversary_persons, message)


def get_employees_having_an_event_on_given_date(event_type, date):
    """Get all employee who have `event_type` on specific_date
    & group them based on their company. `event_type`
    can be `birthday` or `work_anniversary`"""

    from collections import defaultdict

    # Set column based on event type
    if event_type == "birthday":
        condition_column = "date_of_birth"
    elif event_type == "work_anniversary":
        condition_column = "date_of_joining"
    else:
        return

    employees_born_on_given_date = frappe.db.sql("""
            SELECT `personal_email`, `company`, `company_email`, `user_id`, `employee_name` AS 'name', `leave_approver`, `image`, `date_of_joining`
            FROM `tabEmployee`
            WHERE
                DAY({0}) = DAY(%(date)s)
            AND
                MONTH({0}) = MONTH(%(date)s)
            AND
                YEAR({0}) < YEAR(%(date)s)
            AND
                `status` = 'Active'
        """.format(condition_column), {"date": date}, as_dict=1
    )
    grouped_employees = defaultdict(lambda: [])

    for employee_doc in employees_born_on_given_date:
        grouped_employees[employee_doc.get("company")].append(employee_doc)

    return grouped_employees


def get_work_anniversary_reminder_text_and_message(anniversary_persons, joining_date):
    today_date = today()
    if joining_date == today_date:
        days_alias = "Today"
        completed = "completed"

    elif joining_date > today_date:
        days_alias = "{0} days later".format(date_diff(joining_date, today_date))
        completed = "will complete"

    if len(anniversary_persons) == 1:
        anniversary_person = anniversary_persons[0]["name"]
        persons_name = anniversary_person
        # Number of years completed at the company
        completed_years = getdate().year - anniversary_persons[0]["date_of_joining"].year
        anniversary_person += f" {completed} {get_pluralized_years(completed_years)}"
    else:
        person_names_with_years = []
        names = []
        for person in anniversary_persons:
            person_text = person["name"]
            names.append(person_text)
            # Number of years completed at the company
            completed_years = getdate().year - person["date_of_joining"].year
            person_text += f" {completed} {get_pluralized_years(completed_years)}"
            person_names_with_years.append(person_text)

        # converts ["Jim", "Rim", "Dim"] to Jim, Rim & Dim
        anniversary_person = comma_sep(person_names_with_years, frappe._("{0} & {1}"), False)
        persons_name = comma_sep(names, frappe._("{0} & {1}"), False)

    reminder_text = _("{0} {1} at our Company! 🎉").format(days_alias, anniversary_person)
    message = _("A friendly reminder of an important date for our team.")
    message += "<br>"
    message += _("Everyone, let’s congratulate {0} on their work anniversary!").format(persons_name)

    return reminder_text, message


def send_work_anniversary_reminder(recipients, reminder_text, anniversary_persons, message):
    frappe.sendmail(
        recipients=recipients,
        subject=_("Work Anniversary Reminder"),
        template="anniversary_reminder",
        args=dict(
            reminder_text=reminder_text,
            anniversary_persons=anniversary_persons,
            message=message,
        ),
        header=_("Work Anniversary Reminder"),
    )


def get_pluralized_years(years):
    if years == 1:
        return "1 year"
    return f"{years} years"
