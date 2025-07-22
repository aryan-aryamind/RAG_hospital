from flask import Flask, request, jsonify, Response
from twilio.twiml.voice_response import VoiceResponse, Gather
from twilio.request_validator import RequestValidator
import os
import logging
import json
from datetime import datetime
from dotenv import load_dotenv
import re
from rapidfuzz import process, fuzz
import dateparser
from sms import send_sms
import requests
from model import summarize, is_bye, extract_date, extract_time, is_confirm
import psycopg2

user_sessions = {}  # {call_sid: {step, doctor, department, ...}}

load_dotenv()
account_sid = os.environ["TWILIO_SID"]
auth_token = os.environ["TWILIO_AUTH"]
from_number = os.environ["TWILIO_NUMBER"]
to_number = os.environ["TO_NUMBER"]
ADMISSION_JSON = r"admision.json"
API = "http://127.0.0.1:8000/ask"

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('webhook.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Twilio authentication
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN")
VALIDATE_REQUESTS = os.environ.get("VALIDATE_REQUESTS", "false").lower() == "true"

def validate_twilio_request():
    """Validate that the request is from Twilio"""
    if not VALIDATE_REQUESTS:
        return True
        
    if not TWILIO_AUTH_TOKEN:
        logger.warning("TWILIO_AUTH_TOKEN not set, skipping validation")
        return True
        
    validator = RequestValidator(TWILIO_AUTH_TOKEN)
    
    # Get the request URL and POST data
    request_url = str(request.url)
    request_data = request.form
    
    # Get X-Twilio-Signature header
    signature = request.headers.get('X-TWILIO-SIGNATURE', '')
    
    return validator.validate(request_url, request_data, signature)

@app.before_request
def before_request():
    """Validate all requests are from Twilio"""
    if request.method == 'POST':
        if not validate_twilio_request():
            logger.warning("Invalid Twilio signature")
            return jsonify({"error": "Invalid request signature"}), 403


# Main routes

@app.route('/voice', methods=['GET','POST'])
def voice_webhook():
    """Handle incoming voice calls and return TwiML"""
    try:
        # Create a TwiML response
        response = VoiceResponse()
        
        # Get the call details from Twilio
        call_sid = request.values.get('CallSid')
        from_number = request.values.get('From')
        to_number = request.values.get('To')
        
        logger.info(f"Incoming call from {from_number} to {to_number} (SID: {call_sid})")
        
        # Welcome message
        response.say("Hello, I am Arya an AI Voice Assitant! Welcome to Shalby Hospital.",
        )
        
        # Main menu
        gather = Gather(
            input='speech',
            action='/server-rag',
            method='POST',
            barge_in=True,
            # timeout=5
        )
        response.append(gather)
        # If no input is received, prompt again
        response.pause(length=1)
        gather2 = Gather(
            input='speech',
            action='/server-rag',
            method='POST',
            barge_in=True
        )
        gather2.say("Are you still there? How can I help you?")
        response.append(gather2)
        # If still no input, say goodbye
        response.say("We didn't receive any input. Please call back later. Goodbye!")
        response.hangup()
        return str(response)
    
    except Exception as e:
        logger.error(f"Error in voice_webhook: {str(e)}")
        response = VoiceResponse()
        response.say("We're sorry, but there was an error processing your call. Please try again.")
        return str(response)


def parse_booking_request(text):
    """Extract department, date, and time from user input using regex and keywords."""
    department = None
    date = None
    time = None
    # Departments from doctors_list.json
    departments = get_departments_from_doctors_list()
    department = best_match_department(text, departments)
    # Date (robust: look for dd-mm-yyyy, dd Month yyyy, dd Month, Month dd, etc.)
    date_match = re.search(r'(\d{1,2}[\-/ ]?(?:[A-Za-z]+)?[\-/ ]?(\d{2,4})?)', text)
    if date_match:
        date_str = date_match.group(0)
        parsed = dateparser.parse(date_str)
        if parsed:
            date = parsed.strftime('%Y-%m-%d')
    else:
        # Try to parse any date in the text
        parsed = dateparser.parse(text, settings={'PREFER_DATES_FROM': 'future'})
        if parsed:
            date = parsed.strftime('%Y-%m-%d')
    # Time (robust: look for \d{1,2}(:\d{2})? ?[ap]m or 24h)
    time_match = re.search(r'(\d{1,2}:\d{2}|\d{1,2} ?[ap]m)', text.lower())
    if time_match:
        time_str = time_match.group(1).replace(' ', '').upper()
        if ':' not in time_str and len(time_str) <= 4:
            hour_match = re.match(r'(\d{1,2})', time_str)
            if hour_match:
                hour = int(hour_match.group(1))
                suffix = 'AM' if 'AM' in time_str else 'PM'
                if suffix == 'PM' and hour != 12:
                    hour += 12
                slot = f"{hour:02d}:00-{hour:02d}:30"
                time = slot
        else:
            # Try to match to slot format
            parts = time_str.split(':')
            if len(parts) == 2:
                hour = int(parts[0])
                minute = int(parts[1][:2])
                slot = f"{hour:02d}:{minute:02d}-{hour:02d}:{minute+30:02d}"
                time = slot
    return department, date, time

def best_match_department(user_text, departments, threshold=60):
    # Lower threshold for better matching
    result = process.extractOne(user_text, departments, scorer=fuzz.token_sort_ratio, score_cutoff=threshold)
    if result:
        match, score, _ = result
        logger.info(f"Fuzzy department match: '{user_text}' -> '{match}' (score: {score})")
        return match if score >= threshold else None
    logger.info(f"No fuzzy department match for: '{user_text}'")
    return None

@app.route('/server-rag',methods=['POST'])
def server_rag():
    rag_question = request.values.get('SpeechResult', '')
    logger.info(f"User input speech to rag: {rag_question}")

    resp = VoiceResponse()

    if 'book' in rag_question.lower() and 'appointment' in rag_question.lower():
        # Try to parse all details from the utterance
        department, date, time = parse_booking_request(rag_question)
        if not department:
            # If not enough info, ask for department
            call_sid = request.values.get('CallSid')
            user_sessions[call_sid] = {'step': 'department'}
            resp = VoiceResponse()
            departments = get_departments_from_doctors_list()
            dept_list = ', '.join(departments)
            gather = Gather(input='speech', action='/collect-department', method='POST')
            gather.say(f"Which department do you want to book an appointment in? Available departments are: {dept_list}.")
            resp.append(gather)
            resp.say("We didn't receive any input. Goodbye!")
            return str(resp)
        if department and date and time:
            # Find all available doctors for that slot
            all_slots = get_available_slots_for_department_and_date(department, date)
            available_doctors = sorted(list(set(s['doctor'] for s in all_slots if s['time'] == time)))
            if available_doctors:
                call_sid = request.values.get('CallSid')
                # If only one doctor, ask for confirmation
                if len(available_doctors) == 1:
                    slot = {
                        'doctor': available_doctors[0],
                        'department': get_department_by_doctor_name(available_doctors[0]) or department,
                        'date': date,
                        'time': time
                    }
                    user_sessions[call_sid] = {'step': 'confirm', **slot}
                    gather = Gather(input='speech', action='/confirm-booking', method='POST')
                    gather.say(f"{slot['doctor']} is available in {slot['department']} on {date} at {time}. Would you like to book with {slot['doctor']}? Please say yes or no.")
                    resp.append(gather)
                    return str(resp)
                else:
                    # Multiple doctors available, list them and ask for confirmation for the first
                    slot = {
                        'doctor': available_doctors[0],
                        'department': get_department_by_doctor_name(available_doctors[0]) or department,
                        'date': date,
                        'time': time
                    }
                    user_sessions[call_sid] = {'step': 'confirm', **slot}
                    doc_list = ', '.join([f" {d}" for d in available_doctors])
                    gather = Gather(input='speech', action='/confirm-booking', method='POST')
                    gather.say(f"The following doctors are available in {slot['department']} on {date} at {time}: {doc_list}. Would you like to book with {slot['doctor']}? Please say yes or no.")
                    resp.append(gather)
                    return str(resp)
            else:
                # Suggest nearest slot
                all_slots = get_available_slots_for_department_and_date(department, date)
                suggestion = next((s for s in all_slots), None) # Simplified suggestion
                if suggestion:
                    call_sid = request.values.get('CallSid')
                    # Use department from doctors_list.json if possible
                    suggestion_department = get_department_by_doctor_name(suggestion['doctor']) or department
                    user_sessions[call_sid] = {'step': 'suggest', **suggestion, 'department': suggestion_department}
                    gather = Gather(input='speech', action='/confirm-booking', method='POST')
                    gather.say(f"No doctor is available at that time. The nearest available slot is with {suggestion['doctor']} in {suggestion_department} on {date} at {suggestion['time']}. Do you want to book this slot? Please say yes or no.")
                    resp.append(gather)
                    return str(resp)
                else:
                    resp.say("Sorry, no slots are available in that department. Thank you.")
                    resp.hangup()
                    return str(resp)
        else:
            # If not enough info, ask for department
            call_sid = request.values.get('CallSid')
            user_sessions[call_sid] = {'step': 'department'}
            resp = VoiceResponse()
            departments = get_departments_from_doctors_list()
            dept_list = ', '.join(departments)
            gather = Gather(input='speech', action='/collect-department', method='POST')
            gather.say(f"Which department do you want to book an appointment in? Available departments are: {dept_list}.")
            resp.append(gather)
            resp.say("We didn't receive any input. Goodbye!")
            return str(resp)

    # RAG fallback
    try:
        api_resp = requests.post(
            API,
            json={
                'question': rag_question,
                "session_id": "user123",
            }
        )
        if api_resp.status_code == 200:
            rag_ans = api_resp.json().get('answer')
            logger.info(f"RAG API response: {rag_ans}")
            summarize_ans = summarize(rag_ans)
            logger.info(f"Summarize Answer: {summarize_ans}")
            gather = Gather(
                input='speech',
                action='/server-rag',
                method='POST',
                barge_in=True
            )
            gather.say(summarize_ans)
            resp.append(gather)
        else:
            raise Exception(f"API returned status {api_resp.status_code}")
    except Exception as e:
        logger.error(f"Error calling RAG API: {e}")
        resp.say("Sorry, I'm having trouble accessing the information right now.")
    return str(resp)


# Helper: Get departments from doctors_list.json
with open('doctors_list.json', 'r', encoding='utf-8') as f:
    DOCTORS_LIST = json.load(f)["doctors"]

def get_departments_from_doctors_list():
    return sorted(set(doc["doctor_department"] for doc in DOCTORS_LIST))

def get_doctors_by_department_from_list(department):
    return [doc for doc in DOCTORS_LIST if doc["doctor_department"].lower() == department.lower()]

def get_department_by_doctor_name(doctor_name):
    for doc in DOCTORS_LIST:
        if doc["doctor_name"].lower() == doctor_name.lower():
            return doc["doctor_department"]
    return None

def get_valid_times_for_department(department):
    from datetime import datetime, timedelta
    # Union of all available times for all doctors in the department, minus lunch break
    valid_times = set()
    for doc in DOCTORS_LIST:
        if doc['doctor_department'].lower() == department.lower():
            # Parse available time range
            start_str, end_str = doc['doctor_available_time'].replace(' ', '').split('to')
            start_dt = datetime.strptime(start_str, '%H')
            end_dt = datetime.strptime(end_str, '%H')
            # Build 30-min slots
            t = start_dt
            while t < end_dt:
                slot_start = t.strftime('%H:%M')
                slot_end = (t + timedelta(minutes=30)).strftime('%H:%M')
                valid_times.add(f"{slot_start}-{slot_end}")
                t += timedelta(minutes=30)
            # Remove lunch break slots
            lunch = doc.get('lunch_break')
            if lunch:
                lunch_start, lunch_end = lunch.replace(' ', '').split('-')
                lunch_start_dt = datetime.strptime(lunch_start, '%H:%M')
                lunch_end_dt = datetime.strptime(lunch_end, '%H:%M')
                t = lunch_start_dt
                while t < lunch_end_dt:
                    slot_start = t.strftime('%H:%M')
                    slot_end = (t + timedelta(minutes=30)).strftime('%H:%M')
                    valid_times.discard(f"{slot_start}-{slot_end}")
                    t += timedelta(minutes=30)
    return sorted(valid_times)

def get_available_slots_for_department_and_date(department, date):
    from datetime import datetime, timedelta
    doctors_in_dept = [doc for doc in DOCTORS_LIST if doc['doctor_department'].lower() == department.lower()]
    booked_slots_for_date = set()
    try:
        with open('bookings.json', 'r', encoding='utf-8') as f:
            bookings = json.load(f)
            for b in bookings:
                if b.get('date') == date:
                    booked_slots_for_date.add((b.get('doctor'), b.get('time')))
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    all_available_slots = []
    for doc_info in doctors_in_dept:
        doc_name = doc_info['doctor_name']
        try:
            start_str, end_str = doc_info['doctor_available_time'].replace(' ', '').split('to')
            start_hour = int(start_str)
            if start_hour < 7: start_hour += 12
            end_hour = int(end_str)
            if end_hour <= 7: end_hour += 12
            start_dt = datetime.strptime(str(start_hour), '%H')
            end_dt = datetime.strptime(str(end_hour), '%H')
        except (ValueError, KeyError):
            logger.error(f"Could not parse available_time for {doc_name}")
            continue
        lunch_start_dt, lunch_end_dt = None, None
        if lunch := doc_info.get('lunch_break'):
            try:
                lunch_start_str, lunch_end_str = lunch.replace(' ', '').split('-')
                lunch_start_dt = datetime.strptime(lunch_start_str, '%H:%M')
                lunch_end_dt = datetime.strptime(lunch_end_str, '%H:%M')
            except (ValueError, KeyError):
                logger.error(f"Could not parse lunch_break for {doc_name}")
        current_time = start_dt
        while current_time < end_dt:
            slot_start_time = current_time
            slot_end_time = current_time + timedelta(minutes=30)
            slot_str = f"{slot_start_time.strftime('%H:%M')}-{slot_end_time.strftime('%H:%M')}"
            is_in_lunch = lunch_start_dt and lunch_start_dt <= slot_start_time < lunch_end_dt
            is_booked = (doc_name, slot_str) in booked_slots_for_date
            if not is_in_lunch and not is_booked:
                all_available_slots.append({'doctor': doc_name, 'time': slot_str})
            current_time += timedelta(minutes=30)
    all_available_slots.sort(key=lambda x: (x['time'], x['doctor']))
    return all_available_slots

def is_slot_booked(doctor, date, time):
    """Check if a slot is already in bookings.json."""
    try:
        with open('bookings.json', 'r', encoding='utf-8') as f:
            bookings = json.load(f)
            for booking in bookings:
                if (booking.get('doctor') == doctor and
                    booking.get('date') == date and
                    booking.get('time') == time):
                    return True
    except (FileNotFoundError, json.JSONDecodeError):
        return False
    return False

def get_db_connection():
    import os
    return psycopg2.connect(
        dbname=os.environ.get('PG_DB', 'your_db'),
        user=os.environ.get('PG_USER', 'your_user'),
        password=os.environ.get('PG_PASSWORD', 'your_password'),
        host=os.environ.get('PG_HOST', 'localhost'),
        port=os.environ.get('PG_PORT', 5432)
    )

def insert_booking(department, doctor, date, time, name, mobile):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO bookings (department, doctor, date, time, name, mobile)
        VALUES (%s, %s, %s, %s, %s, %s)""",
        (department, doctor, date, time, name, mobile)
    )
    conn.commit()
    cur.close()
    conn.close()

@app.route('/collect-department', methods=['POST'])
def collect_department():
    call_sid = request.values.get('CallSid')
    session = user_sessions.get(call_sid, {})
    spoken_dept = request.values.get('SpeechResult', '')
    departments = get_departments_from_doctors_list()
    match = process.extractOne(spoken_dept, departments, scorer=fuzz.token_sort_ratio)
    resp = VoiceResponse()
    if not match:
        dept_list = ', '.join(departments)
        gather = Gather(input='speech', action='/collect-department', method='POST', barge_in=True)
        gather.say(f"Sorry, I didn't recognize that department. Available departments are: {dept_list}. Please say the department.")
        resp.append(gather)
        return str(resp)
    department, score, _ = match
    if score < 85:
        session['pending_department'] = department
        user_sessions[call_sid] = session
        gather = Gather(input='speech', action='/confirm-department', method='POST', barge_in=True)
        gather.say(f"Did you mean {department}? Please say yes or no.")
        resp.append(gather)
        return str(resp)
    session['department'] = department
    user_sessions[call_sid] = session
    gather = Gather(input='speech', action='/collect-date', method='POST', barge_in=True)
    gather.say(f"For which date do you want the appointment in {department}? Please say the date in the format 22 july 2025 or 22-07-2025.")
    resp.append(gather)
    return str(resp)

@app.route('/confirm-department', methods=['POST'])
def confirm_department():
    call_sid = request.values.get('CallSid')
    session = user_sessions.get(call_sid, {})
    answer = request.values.get('SpeechResult', '').strip().lower()
    logger.info(f"/confirm-department: User response: {answer}, Session: {session}")
    resp = VoiceResponse()
    department = session.get('pending_department')
    yes_words = ['yes', 'yeah', 'yup', 'yep', 'correct', 'right', 'ya', 'sure', 'ok', 'okay']
    no_words = ['no', 'nope', 'nah', 'not', 'incorrect', 'wrong']
    if any(word in answer for word in yes_words) and department:
        session['department'] = department
        session.pop('pending_department', None)
        user_sessions[call_sid] = session
        agent_msg = f"Great. For which date do you want the appointment in {department}? Please say the date in the format 22 july 2025 or 22-07-2025."
        logger.info(f"Agent: {agent_msg}")
        gather = Gather(input='speech', action='/collect-date', method='POST', timeout=12)
        gather.say(agent_msg)
        resp.append(gather)
        return str(resp)
    elif any(word in answer for word in no_words):
        session.pop('pending_department', None)
        user_sessions[call_sid] = session
        departments = get_departments_from_doctors_list()
        dept_list = ', '.join(departments)
        agent_msg = f"Okay, please say the department again. Available departments are: {dept_list}."
        logger.info(f"Agent: {agent_msg}")
        gather = Gather(input='speech', action='/collect-department', method='POST', timeout=12)
        gather.say(agent_msg)
        resp.append(gather)
        return str(resp)
    else:
        agent_msg1 = f"Did you mean {department}? Please say yes or no."
        agent_msg2 = "Are you there? Can you speak yes or no?"
        logger.info(f"Agent: {agent_msg1}")
        gather = Gather(input='speech', action='/confirm-department', method='POST', timeout=12)
        gather.say(agent_msg1)
        resp.append(gather)
        logger.info(f"Agent: {agent_msg2}")
        gather2 = Gather(input='speech', action='/confirm-department', method='POST', timeout=12)
        gather2.say(agent_msg2)
        resp.append(gather2)
        resp.say("We didn't receive any input. Goodbye!")
        resp.hangup()
        return str(resp)

@app.route('/collect-date', methods=['POST'])
def collect_date():
    call_sid = request.values.get('CallSid')
    session = user_sessions.get(call_sid, {})
    date_text = request.values.get('SpeechResult', '')
    logger.info(f"User said date: {date_text}")
    date = extract_date(date_text)
    if not date:
        parsed = dateparser.parse(date_text)
        if parsed:
            date = parsed.strftime('%Y-%m-%d')
    resp = VoiceResponse()
    if date:
        logger.info(f"Parsed date: {date}")
        from datetime import timedelta
        parsed_date = datetime.strptime(date, '%Y-%m-%d').date()
        today = datetime.today().date()
        two_months_later = today + timedelta(days=30)
        if parsed_date < today or parsed_date > two_months_later:
            gather = Gather(input='speech', action='/collect-date', method='POST')
            gather.say("Sorry, you can only book appointments from today up to one month ahead. Please say a valid date.")
            resp.append(gather)
            return str(resp)
        session['date'] = date
        user_sessions[call_sid] = session
        # Always proceed to collect time
        gather = Gather(input='speech', action='/collect-time', method='POST')
        gather.say(f"At what time on {date}? You can say 3pm, 14:00, or 2:30 p.m.")
        resp.append(gather)
        return str(resp)
    else:
        gather = Gather(input='speech', action='/collect-date', method='POST')
        gather.say("Sorry, I didn't understand the date. Please say the date in the format 21 August 2025 or 21-08-2025.")
        resp.append(gather)
        return str(resp)

def is_time_in_range(start, end, check):
    """Check if check (HH:MM) is in [start, end) (HH:MM)."""
    from datetime import datetime
    fmt = '%H:%M'
    s = datetime.strptime(start, fmt)
    e = datetime.strptime(end, fmt)
    c = datetime.strptime(check, fmt)
    return s <= c < e

# Load doctors list with lunch breaks
with open('doctors_list.json', 'r', encoding='utf-8') as f:
    DOCTORS_LIST = json.load(f)["doctors"]

def get_doctor_lunch_break(doctor_name):
    for doc in DOCTORS_LIST:
        if doc["doctor_name"].lower() == doctor_name.lower():
            return doc.get("lunch_break")
    return None

def get_next_available_slot_after_lunch(slots, lunch_end):
    from datetime import datetime
    fmt = '%H:%M'
    lunch_end_dt = datetime.strptime(lunch_end, fmt)
    for slot in slots:
        slot_start = slot["time"].split('-')[0]
        slot_start_dt = datetime.strptime(slot_start, fmt)
        if slot_start_dt >= lunch_end_dt and slot["available"]:
            return slot["time"]
    return None

@app.route('/collect-time', methods=['POST'])
def collect_time():
    call_sid = request.values.get('CallSid')
    session = user_sessions.get(call_sid, {})
    time_text = request.values.get('SpeechResult', '')
    department = session.get('department')
    date = session.get('date')
    available_slots = get_available_slots_for_department_and_date(department, date)
    # Try to extract a valid slot from user input
    time_val = extract_time(time_text)
    matched_slot = None
    for slot in available_slots:
        slot_start, _ = slot['time'].split('-')
        if time_val == slot_start:
            matched_slot = slot
            break
    if not matched_slot:
        # fallback to dateparser
        from datetime import datetime
        parsed = dateparser.parse(time_text)
        if parsed:
            for slot in available_slots:
                slot_start, _ = slot['time'].split('-')
                if parsed.strftime('%H:%M') == slot_start:
                    matched_slot = slot
                    break
    # If still not found, suggest the next available slot
    if not matched_slot and time_val:
        # Find the next available slot after the requested time
        from datetime import datetime, timedelta
        try:
            t = datetime.strptime(time_val, '%H:%M')
        except Exception:
            t = None
        next_slot = None
        min_diff = timedelta(hours=24)
        for slot in available_slots:
            slot_start, _ = slot['time'].split('-')
            slot_time = datetime.strptime(slot_start, '%H:%M')
            if t and slot_time >= t:
                diff = slot_time - t
                if diff < min_diff:
                    min_diff = diff
                    next_slot = slot
        if next_slot:
            resp = VoiceResponse()
            gather = Gather(input='speech', action='/confirm-datetime', method='POST', timeout=10)
            gather.say(f"The closest available slot is at {next_slot['time']} with {next_slot['doctor']}. Do you want to book this slot? Please say yes or no.")
            session['time'] = next_slot['time']
            session['doctor'] = next_slot['doctor']
            user_sessions[call_sid] = session
            resp.append(gather)
            return str(resp)
    if matched_slot:
        session['time'] = matched_slot['time']
        session['doctor'] = matched_slot['doctor']
        user_sessions[call_sid] = session
        resp = VoiceResponse()
        gather = Gather(input='speech', action='/confirm-datetime', method='POST', timeout=10)
        gather.say(f"You want to book an appointment in {department} on {date} at {matched_slot['time']} with {matched_slot['doctor']}. Is this correct? Please say yes or no.")
        resp.append(gather)
        return str(resp)
    else:
        resp = VoiceResponse()
        slot_list = ', '.join([f"{slot['time']} with {slot['doctor']}" for slot in available_slots]) if available_slots else 'No slots available.'
        gather = Gather(input='speech', action='/collect-time', method='POST', timeout=10)
        gather.say(f"Sorry, available times for this department are: {slot_list}. Please say a valid time.")
        resp.append(gather)
        return str(resp)

@app.route('/confirm-datetime', methods=['POST'])
def confirm_datetime():
    call_sid = request.values.get('CallSid')
    session = user_sessions.get(call_sid, {})
    answer = request.values.get('SpeechResult', '').strip().lower()
    logger.info(f"/confirm-datetime: User response: {answer}, Session: {session}")
    resp = VoiceResponse()
    department = session.get('department')
    date = session.get('date')
    time = session.get('time')
    doctor = session.get('doctor')
    yes_words = ['yes', 'yeah', 'yup', 'yep', 'correct', 'right', 'ya', 'sure', 'ok', 'okay']
    no_words = ['no', 'nope', 'nah', 'not', 'incorrect', 'wrong']
    if any(word in answer for word in yes_words):
        # If doctor is already set (from slot match), proceed to confirm-booking
        if doctor:
            session['step'] = 'confirm'
            user_sessions[call_sid] = session
            gather = Gather(input='speech', action='/confirm-booking', method='POST')
            gather.say(f"{doctor} is available in {department} on {date} at {time}. Would you like to book with {doctor}? Please say yes or no.")
            resp.append(gather)
            return str(resp)
        # If not, find all available doctors for that department/date/time
        available_slots = get_available_slots_for_department_and_date(department, date)
        doctors_at_time = [slot['doctor'] for slot in available_slots if slot['time'] == time]
        if len(doctors_at_time) == 1:
            session['doctor'] = doctors_at_time[0]
            session['step'] = 'confirm'
            user_sessions[call_sid] = session
            gather = Gather(input='speech', action='/confirm-booking', method='POST')
            gather.say(f"{doctors_at_time[0]} is available in {department} on {date} at {time}. Would you like to book with {doctors_at_time[0]}? Please say yes or no.")
            resp.append(gather)
            return str(resp)
        elif len(doctors_at_time) > 1:
            session['available_doctors'] = doctors_at_time
            user_sessions[call_sid] = session
            doc_list = ', '.join([f"Dr. {d}" for d in doctors_at_time])
            gather = Gather(input='speech', action='/choose-doctor', method='POST')
            gather.say(f"The following doctors are available in {department} on {date} at {time}: {doc_list}. Which doctor would you like to book with? Please say the doctor's name.")
            resp.append(gather)
            return str(resp)
        else:
            gather = Gather(input='speech', action='/collect-time', method='POST')
            gather.say(f"Sorry, no doctors are available at that time. Please say another time.")
            resp.append(gather)
            return str(resp)
    elif any(word in answer for word in no_words):
        agent_msg = f"Okay, let's try again. For which date do you want the appointment in {department}? Please say the date in the format 22 july 2025 or 22-07-2025."
        logger.info(f"Agent: {agent_msg}")
        gather = Gather(input='speech', action='/collect-date', method='POST', timeout=12)
        gather.say(agent_msg)
        resp.append(gather)
        return str(resp)
    else:
        agent_msg1 = f"You want to book an appointment in {department} on {date} at {time}. Is this correct? Please say yes or no."
        agent_msg2 = "Are you there? Can you speak yes or no?"
        logger.info(f"Agent: {agent_msg1}")
        gather = Gather(input='speech', action='/confirm-datetime', method='POST', timeout=12)
        gather.say(agent_msg1)
        resp.append(gather)
        logger.info(f"Agent: {agent_msg2}")
        gather2 = Gather(input='speech', action='/confirm-datetime', method='POST', timeout=12)
        gather2.say(agent_msg2)
        resp.append(gather2)
        resp.say("We didn't receive any input. Goodbye!")
        resp.hangup()
        return str(resp)

def find_matching_slot(slots, parsed_time):
    # slots: list of slot dicts with 'time'
    # parsed_time: datetime.time object
    for slot in slots:
        start, _ = slot['time'].split('-')
        start_hour, start_minute = map(int, start.split(':'))
        if parsed_time.hour == start_hour and parsed_time.minute == start_minute:
            return slot['time']
    return None

@app.route('/confirm-booking', methods=['POST'])
def confirm_booking():
    call_sid = request.values.get('CallSid')
    answer = request.values.get('SpeechResult', '').strip().lower()
    session = user_sessions.get(call_sid, {})
    logger.info(f"/confirm-booking: User response: {answer}, Session: {session}")
    resp = VoiceResponse()
    yes_words = ['yes', 'yeah', 'yup', 'yep', 'correct', 'right', 'ya', 'sure', 'ok', 'okay']
    no_words = ['no', 'nope', 'nah', 'not', 'incorrect', 'wrong']
    if any(word in answer for word in yes_words):
        session['step'] = 'name'
        user_sessions[call_sid] = session
        agent_msg = "Can you please share your good name for the booking?"
        logger.info(f"Agent: {agent_msg}")
        gather = Gather(input='speech', action='/collect-name', method='POST', timeout=12)
        gather.say(agent_msg)
        resp.append(gather)
        return str(resp)
    elif any(word in answer for word in no_words):
        agent_msg = "Okay, thank you for calling. You can try booking another slot if you wish."
        logger.info(f"Agent: {agent_msg}")
        resp.say(agent_msg)
        resp.hangup()
        return str(resp)
    else:
        agent_msg1 = "Is the information correct? Please say yes or no."
        agent_msg2 = "Are you there? Can you speak yes or no?"
        logger.info(f"Agent: {agent_msg1}")
        gather = Gather(input='speech', action='/confirm-booking', method='POST', timeout=12)
        gather.say(agent_msg1)
        resp.append(gather)
        logger.info(f"Agent: {agent_msg2}")
        gather2 = Gather(input='speech', action='/confirm-booking', method='POST', timeout=12)
        gather2.say(agent_msg2)
        resp.append(gather2)
        resp.say("We didn't receive any input. Goodbye!")
        resp.hangup()
        return str(resp)

@app.route('/collect-name', methods=['POST'])
def collect_name():
    call_sid = request.values.get('CallSid')
    name = request.values.get('SpeechResult', '')
    logger.info(f"/collect-name: User said name: {name}")
    session = user_sessions.get(call_sid, {})
    resp = VoiceResponse()
    # Track name attempts
    attempts = session.get('name_attempts', 0)
    if not name.strip():
        attempts += 1
        session['name_attempts'] = attempts
        user_sessions[call_sid] = session
        if attempts < 2:
            gather = Gather(input='speech', action='/collect-name', method='POST', timeout=10)
            gather.say("Sorry, I didn't catch your name. Can you repeat your name for the booking?")
            resp.append(gather)
            return str(resp)
        else:
            resp.say("Sorry, I didn't catch your name. Please call again to book your appointment. Goodbye!")
            resp.hangup()
            return str(resp)
    # Reset attempts on success
    session['name'] = name
    session['step'] = 'mobile'
    session['name_attempts'] = 0
    user_sessions[call_sid] = session
    # Increase DTMF timeout to 15 seconds
    gather = Gather(input='dtmf', num_digits=10, action='/confirm-mobile', method='POST', timeout=15)
    gather.say("Thank you. Now, please enter your 10 digit mobile number using the keypad.")
    resp.append(gather)
    return str(resp)

@app.route('/confirm-mobile', methods=['POST'])
def confirm_mobile():
    call_sid = request.values.get('CallSid')
    digits = request.values.get('Digits', '')
    logger.info(f"/confirm-mobile: Received digits: {digits}")
    session = user_sessions.get(call_sid, {})
    session['pending_mobile'] = digits
    user_sessions[call_sid] = session
    resp = VoiceResponse()
    try:
        if len(digits) == 10:
            # Send SMS immediately after collecting number
            sms_msg = (
                f"We have received your details for appointment booking.\n"
                f"Doctor: {session.get('doctor','')}\n"
                f"Department: {session.get('department','')}\n"
                f"Date: {session.get('date','')}\n"
                f"Time: {session.get('time','')}\n"
                f"Name: {session.get('name','')}\n"
                f"Mobile: {digits}"
            )
            send_sms(f"+91{digits}", sms_msg)
            logger.info(f"/confirm-mobile: Sent confirmation SMS to {digits}")
            # Read back all details for confirmation
            details = (
                f"You are booking an appointment with {session.get('doctor','')} in {session.get('department','')} on {session.get('date','')} at {session.get('time','')}. "
                f"Your name is {session.get('name','')} and your mobile number is {digits}. Is this correct? Please say yes or no."
            )
            gather = Gather(input='speech', action='/finalize-booking', method='POST')
            gather.say(details)
            resp.append(gather)
        else:
            logger.warning(f"/confirm-mobile: Invalid mobile number entered: {digits}")
            gather = Gather(input='dtmf', num_digits=10, action='/confirm-mobile', method='POST', timeout=15)
            gather.say("That was not a valid mobile number. Please enter your 10 digit mobile number using the keypad.")
            resp.append(gather)
    except Exception as e:
        logger.error(f"/confirm-mobile: Exception occurred: {e}")
        resp.say("Sorry, there was an error processing your input. Please try again later.")
        resp.hangup()
    return str(resp)

@app.route('/finalize-booking', methods=['POST'])
def finalize_booking():
    call_sid = request.values.get('CallSid')
    session = user_sessions.get(call_sid, {})  # Ensure session is defined first
    answer = request.values.get('SpeechResult', '').strip().lower()
    logger.info(f"/finalize-booking: User response: {answer}, Session: {session}")
    resp = VoiceResponse()
    digits = session.get('pending_mobile', '')
    doctor = session.get('doctor')
    department = session.get('department')
    date = session.get('date')
    time = session.get('time')
    name = session.get('name')
    yes_words = ['yes', 'yeah', 'yup', 'yep', 'correct', 'right', 'ya', 'sure', 'ok', 'okay']
    no_words = ['no', 'nope', 'nah', 'not', 'incorrect', 'wrong']
    if any(word in answer for word in yes_words) and len(digits) == 10:
        # Final check if slot was booked by someone else
        if is_slot_booked(doctor, date, time):
            booked = False
        else:
            booked = True
        log_msg = (
            f"Attempting to book: Doctor={doctor}, Department={department}, "
            f"Date={date}, Time={time}, Name={name}, Mobile={digits}"
        )
        if booked:
            logger.info(log_msg)
            sms_msg = (
                f"Your appointment is confirmed!\n"
                f"Doctor: {doctor}\n"
                f"Department: {department}\n"
                f"Date: {date}\n"
                f"Time: {time}\n"
                f"Name: {name}\n"
                f"Mobile: {digits}"
            )
            try:
                send_sms(f"+91{digits}", sms_msg)
            except Exception as e:
                logger.error(f"Error sending SMS: {e}")
            # --- Insert booking into PostgreSQL ---
            try:
                insert_booking(department, doctor, date, time, name, digits)
                logger.info(f"Inserted booking into PostgreSQL for {doctor} on {date} at {time}")
            except Exception as e:
                logger.error(f"Error inserting booking into DB: {e}")
            # --- End insert ---
            resp.say(f"Your slot has been booked with {doctor} in {department} on {date} at {time}. Thank you!")
            gather = Gather(input='speech', action='/post-booking-questions', method='POST', timeout=7)
            gather.say("Do you have any more questions to ask?")
            resp.append(gather)
            return str(resp)
        else:
            logger.warning(f"Appointment booking failed: {log_msg}")
            logger.warning(f"Booking failure reason: slot was already booked")
            resp.say("Sorry, the slot was just booked by someone else. Please try again.")
        resp.hangup()
        return str(resp)
    elif any(word in answer for word in no_words):
        agent_msg = "Let's try again. Please tell me your name for the booking."
        logger.info(f"Agent: {agent_msg}")
        gather = Gather(input='speech', action='/collect-name', method='POST', timeout=12)
        gather.say(agent_msg)
        resp.append(gather)
        return str(resp)
    else:
        agent_msg1 = "Is the information correct? Please say yes or no."
        agent_msg2 = "Are you there? Can you speak yes or no?"
        logger.info(f"Agent: {agent_msg1}")
        gather = Gather(input='speech', action='/finalize-booking', method='POST', timeout=12)
        gather.say(agent_msg1)
        resp.append(gather)
        logger.info(f"Agent: {agent_msg2}")
        gather2 = Gather(input='speech', action='/finalize-booking', method='POST', timeout=12)
        gather2.say(agent_msg2)
        resp.append(gather2)
        resp.say("We didn't receive any input. Goodbye!")
        resp.hangup()
        return str(resp)

@app.route('/post-booking-questions', methods=['POST'])
def post_booking_questions():
    answer = request.values.get('SpeechResult', '').strip().lower()
    resp = VoiceResponse()
    yes_words = ['yes', 'yeah', 'yup', 'yep', 'sure', 'ok', 'okay', 'of course', 'please']
    no_words = ['no', 'nope', 'nah', 'not', 'none', 'nothing', 'thank you', 'thanks', 'bye']
    if any(word in answer for word in yes_words):
        gather = Gather(input='speech', action='/server-rag', method='POST', timeout=10)
        gather.say("Please ask your question now.")
        resp.append(gather)
        return str(resp)
    elif any(word in answer for word in no_words):
        resp.say("Thank you for calling. Have a great day! Goodbye!")
        resp.hangup()
        return str(resp)
    else:
        gather = Gather(input='speech', action='/post-booking-questions', method='POST', timeout=7)
        gather.say("Sorry, I didn't catch that. Do you have any more questions to ask? Please say yes or no.")
        resp.append(gather)
        return str(resp)


@app.route('/status', methods=['POST'])
def call_status():
    """Handle call status updates"""
    try:
        # Get call details
        call_sid = request.values.get('CallSid')
        call_status = request.values.get('CallStatus')
        call_duration = request.values.get('CallDuration')
        
        # Log the status update
        logger.info(
            f"Call Status Update - SID: {call_sid}, "
            f"Status: {call_status}, "
            f"Duration: {call_duration} seconds"
        )
        
        return '', 200
        
    except Exception as e:
        logger.error(f"Error in status callback: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "version": "1.0.0"
    })

if __name__ == '__main__':
    # Log startup
    logger.info("Starting webhook server on http://localhost:5000")
    logger.info("Make sure to expose this server to the internet using ngrok or similar")
    
    # Start the server
    app.run(
        debug=True,
        host='0.0.0.0',
        port=5000,
        ssl_context=None  # Let ngrok handle SSL
    ) 

