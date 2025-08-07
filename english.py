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
import csv

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
        response.say("Hello, I am Arya an AI Voice Assistant! Welcome to Shalby Hospital.",
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

def extract_any_date(text):
    # Use non-capturing groups in regex
    date_candidates = re.findall(r'\d{1,2}(?:st|nd|rd|th)?\s*(?:of)?\s*[A-Za-z]+|\d{1,2}[/-]\d{1,2}[/-]\d{2,4}', text)
    for candidate in date_candidates:
        if not isinstance(candidate, str):
            candidate = ' '.join(candidate)  # flatten tuple if needed
        parsed = dateparser.parse(candidate)
        if parsed:
            return parsed.strftime('%Y-%m-%d')
    # Fallback to whole text
    parsed = dateparser.parse(text)
    if parsed:
        return parsed.strftime('%Y-%m-%d')
    return None

# --- In /server-rag, add lab test booking intent detection and redirect ---
@app.route('/server-rag',methods=['POST'])
def server_rag():
    rag_question = request.values.get('SpeechResult', '')
    logger.info(f"User input speech to rag: {rag_question}")

    resp = VoiceResponse()

    # --- Lab test reschedule intent detection (must come before booking intent) ---
    if 'reschedule' in rag_question.lower() and 'lab test' in rag_question.lower():
        resp.redirect('/reschedule-lab-test', method='POST')
        return str(resp)

    # --- Lab test booking intent detection ---
    lab_keywords = ['lab test', 'book lab test', 'blood test', 'health checkup', 'scan', 'package']
    test_names = get_lab_test_names()
    if any(kw in rag_question.lower() for kw in lab_keywords) or any(test.lower() in rag_question.lower() for test in test_names):
        test_list = ', '.join(test_names)
        # Speak the available lab tests inside a Gather with barge_in=True
        gather = Gather(input='speech', action='/collect-lab-test', method='POST', barge_in=True, timeout=10)
        gather.say(f"We have the following lab tests available: {test_list}. Which one would you like to book?")
        resp.append(gather)
        # Add a second prompt if no response
        gather2 = Gather(input='speech', action='/collect-lab-test', method='POST', barge_in=True, timeout=8)
        gather2.say("Are you still there? Please say the lab test name.")
        resp.append(gather2)
        resp.say("We didn't receive any input. Thank you for calling. Goodbye!")
        resp.hangup()
        return str(resp)

    # --- Existing doctor appointment logic ---
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
            # Custom fallback for not-found answers
            if rag_ans and (
                'document does not contain information' in rag_ans.lower() or
                'cannot fulfill this request' in rag_ans.lower() or
                'no information' in rag_ans.lower()
            ):
                rag_ans = "Sorry, I am unable to help with that as an AI voice agent. Can you please ask another question?"
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
            is_booked_json = (doc_name, slot_str) in booked_slots_for_date
            is_booked_db = is_slot_booked(doc_name, date, slot_str)
            if not is_in_lunch and not is_booked_json and not is_booked_db:
                all_available_slots.append({'doctor': doc_name, 'time': slot_str})
            current_time += timedelta(minutes=30)
    all_available_slots.sort(key=lambda x: (x['time'], x['doctor']))
    return all_available_slots

def is_slot_booked(doctor, date, time):
    """Check if a slot is already in bookings.json or the DB."""
    # Check in bookings.json
    try:
        with open('bookings.json', 'r', encoding='utf-8') as f:
            bookings = json.load(f)
            for booking in bookings:
                if (booking.get('doctor') == doctor and
                    booking.get('date') == date and
                    booking.get('time') == time):
                    return True
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    # Check in PostgreSQL
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            """SELECT 1 FROM bookings WHERE doctor=%s AND date=%s AND time=%s LIMIT 1""",
            (doctor, date, time)
        )
        exists = cur.fetchone() is not None
        cur.close()
        conn.close()
        if exists:
            return True
    except Exception as e:
        logger.error(f"Error checking slot in DB: {e}")
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

# NOTE: Ensure you run this SQL in your DB:
# ALTER TABLE bookings ADD CONSTRAINT unique_doctor_slot UNIQUE (doctor, date, time);
def insert_booking(department, doctor, date, time, name, mobile):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """INSERT INTO bookings (department, doctor, date, time, name, mobile)
            VALUES (%s, %s, %s, %s, %s, %s)""",
            (department, doctor, date, time, name, mobile)
        )
        conn.commit()
    except psycopg2.errors.UniqueViolation:
        conn.rollback()
        raise Exception("Slot already booked")
    finally:
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
    date = extract_any_date(date_text)
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
    logger.info(f"/collect-time: User input: {time_text}, Session: {session}")
    # DO NOT extract or update date here!
    department = session.get('department')
    date = session.get('date')
    logger.info(f"/collect-time: Using department: {department}, date: {date}")
    available_slots = get_available_slots_for_department_and_date(department, date)
    # Only allow times that are not already booked
    valid_times = [slot['time'] for slot in available_slots]
    # Try to extract a valid slot from user input
    time_val = extract_time(time_text)
    matched_slots = [slot for slot in available_slots if slot['time'].split('-')[0] == time_val]
    from datetime import datetime
    # If user requested a time and at least one doctor is available at that time
    if time_val and matched_slots:
        slot = matched_slots[0]
        session['time'] = slot['time']
        session['doctor'] = slot['doctor']
        user_sessions[call_sid] = session
        resp = VoiceResponse()
        gather = Gather(input='speech', action='/confirm-datetime', method='POST', timeout=12)
        gather.say(f"{slot['doctor']} is available in {department} on {date} at {slot['time']}. Would you like to book with {slot['doctor']}? Please say yes or no.")
        resp.append(gather)
        return str(resp)
    # If user requested a time but no doctor is available at that time, suggest next available slot after requested time
    if time_val and not matched_slots:
        try:
            t = datetime.strptime(time_val, '%H:%M')
        except Exception:
            t = None
        next_slot = None
        min_diff = None
        for slot in available_slots:
            slot_start, _ = slot['time'].split('-')
            slot_time = datetime.strptime(slot_start, '%H:%M')
            if t and slot_time > t:
                diff = (slot_time - t).total_seconds()
                if min_diff is None or diff < min_diff:
                    min_diff = diff
                    next_slot = slot
        resp = VoiceResponse()
        if next_slot:
            gather = Gather(input='speech', action='/confirm-datetime', method='POST', timeout=12)
            gather.say(f"Sorry, that slot is already booked for all doctors. The next available slot is at {next_slot['time']} with {next_slot['doctor']}. Would you like to book this slot? Please say yes or no.")
            session['time'] = next_slot['time']
            session['doctor'] = next_slot['doctor']
            user_sessions[call_sid] = session
            resp.append(gather)
            return str(resp)
        else:
            if available_slots:
                earliest_slot = available_slots[0]
                gather = Gather(input='speech', action='/confirm-datetime', method='POST', timeout=12)
                gather.say(f"Sorry, no doctors are available at that time or later. The earliest available slot is at {earliest_slot['time']} with {earliest_slot['doctor']}. Would you like to book this slot? Please say yes or no.")
                session['time'] = earliest_slot['time']
                session['doctor'] = earliest_slot['doctor']
                user_sessions[call_sid] = session
                resp.append(gather)
                return str(resp)
            else:
                gather = Gather(input='speech', action='/collect-time', method='POST', timeout=12)
                gather.say("Sorry, no doctors are available at any time today. Please try another day.")
                resp.append(gather)
                return str(resp)
    if not time_val and available_slots:
        slot = available_slots[0]
        session['time'] = slot['time']
        session['doctor'] = slot['doctor']
        user_sessions[call_sid] = session
        resp = VoiceResponse()
        gather = Gather(input='speech', action='/confirm-datetime', method='POST', timeout=12)
        gather.say(f"The closest available slot is at {slot['time']} with {slot['doctor']}. Would you like to book this slot? Please say yes or no.")
        resp.append(gather)
        return str(resp)
    resp = VoiceResponse()
    slot_list = ', '.join([f"{slot['time']} with {slot['doctor']}" for slot in available_slots]) if available_slots else 'No slots available.'
    gather = Gather(input='speech', action='/collect-time', method='POST', timeout=12)
    gather.say(f"Sorry, available times for this department are: {slot_list}. Please say a valid time.")
    resp.append(gather)
    return str(resp)

@app.route('/confirm-datetime', methods=['POST'])
def confirm_datetime():
    call_sid = request.values.get('CallSid')
    session = user_sessions.get(call_sid, {})
    answer = request.values.get('SpeechResult', '').strip().lower()
    # DO NOT extract or update date here!
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
        # Instead of restarting, go back to time selection for same department/date
        gather = Gather(input='speech', action='/collect-time', method='POST', timeout=12)
        gather.say(f"Okay, let's try another time. Please say the time you want for your appointment in {department} on {date}.")
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
    # DO NOT extract or update date here!
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
        # Instead of ending, go back to time selection for same department/date
        department = session.get('department')
        date = session.get('date')
        gather = Gather(input='speech', action='/collect-time', method='POST', timeout=12)
        gather.say(f"Okay, let's try another time. Please say the time you want for your appointment in {department} on {date}.")
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
            gather = Gather(input='speech', action='/collect-name', method='POST', timeout=10, language='en-IN')
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
            # Do NOT send SMS here. Only confirm details and proceed to finalize-booking
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
            try:
                insert_booking(department, doctor, date, time, name, digits)
                logger.info(f"Inserted booking into PostgreSQL for {doctor} on {date} at {time}")
                # Also append to bookings.json for backup/audit
                booking_data = {
                    'department': department,
                    'doctor': doctor,
                    'date': date,
                    'time': time,
                    'name': name,
                    'mobile': digits
                }
                try:
                    bookings_file = 'bookings.json'
                    if os.path.exists(bookings_file):
                        with open(bookings_file, 'r', encoding='utf-8') as f:
                            bookings = json.load(f)
                            if not isinstance(bookings, list):
                                bookings = []
                    else:
                        bookings = []
                    bookings.append(booking_data)
                    with open(bookings_file, 'w', encoding='utf-8') as f:
                        json.dump(bookings, f, indent=2)
                except Exception as e:
                    logger.error(f"Error writing to bookings.json: {e}")
                booked = True
            except Exception as e:
                if 'Slot already booked' in str(e):
                    booked = False
                else:
                    logger.error(f"Error inserting booking into DB: {e}")
                    resp.say("Sorry, there was an error booking your slot. Please try again.")
                    resp.hangup()
                    return str(resp)
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
            resp.say(f"Your slot has been booked with {doctor} in {department} on {date} at {time}. Thank you!")
            gather = Gather(input='speech', action='/post-booking-options', method='POST', timeout=10)
            gather.say("Do you have any more questions to ask, or would you like to book another appointment? You can say 'book appointment', 'ask a question', or 'no'.")
            resp.append(gather)
            return str(resp)
        else:
            logger.warning(f"Appointment booking failed: {log_msg}")
            logger.warning(f"Booking failure reason: slot was already booked")
            resp.say("Sorry, the slot was just booked by someone else. Please try again.")
        gather = Gather(input='speech', action='/post-booking-options', method='POST', timeout=10)
        gather.say("Do you have any more questions to ask, or would you like to book another appointment? You can say 'book appointment', 'ask a question', or 'no'.")
        resp.append(gather)
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

# --- In /post-booking-options, allow user to say 'book lab test' to start lab test flow ---
@app.route('/post-booking-options', methods=['POST'])
def post_booking_options():
    answer = request.values.get('SpeechResult', '').strip().lower()
    logger.info(f"/post-booking-options: User response: {answer}")
    resp = VoiceResponse()
    # Booking/lab/no intents
    if any(word in answer for word in ['book lab test', 'lab test', 'blood test', 'health checkup', 'scan', 'package']):
        gather = Gather(input='speech', action='/collect-lab-test', method='POST', timeout=10)
        gather.say("Which lab test would you like to book? Please say the test name.")
        resp.append(gather)
        return str(resp)
    if any(word in answer for word in ['book', 'appointment', 'another']):
        gather = Gather(input='speech', action='/collect-department', method='POST', timeout=10)
        gather.say("Which department do you want to book an appointment in? Please say the department.")
        resp.append(gather)
        return str(resp)
    elif any(word in answer for word in ['question', 'ask', 'info', 'information', 'query']):
        gather = Gather(input='speech', action='/server-rag', method='POST', timeout=10)
        gather.say("Please ask your question now.")
        resp.append(gather)
        return str(resp)
    elif any(word in answer for word in ['no', 'none', 'nothing', 'bye', 'exit', 'quit', 'thank you', 'thanks']):
        resp.say("Thank you for calling. Have a great day! Goodbye!")
        resp.hangup()
        return str(resp)
    else:
        # Treat any other input as a RAG question
        try:
            api_resp = requests.post(
                API,
                json={
                    'question': answer,
                    "session_id": "user123",
                }
            )
            if api_resp.status_code == 200:
                rag_ans = api_resp.json().get('answer')
                # Custom fallback for not-found answers
                if rag_ans and (
                    'document does not contain information' in rag_ans.lower() or
                    'cannot fulfill this request' in rag_ans.lower() or
                    'no information' in rag_ans.lower()
                ):
                    rag_ans = "Sorry, I am unable to help with that as an AI voice agent. Can you please ask another question?"
                logger.info(f"RAG API response (post-booking): {rag_ans}")
                summarize_ans = summarize(rag_ans)
                logger.info(f"Summarize Answer (post-booking): {summarize_ans}")
                gather = Gather(
                    input='speech',
                    action='/post-booking-options',
                    method='POST',
                    barge_in=True
                )
                gather.say(summarize_ans)
                resp.append(gather)
            else:
                raise Exception(f"API returned status {api_resp.status_code}")
        except Exception as e:
            logger.error(f"Error calling RAG API (post-booking): {e}")
            resp.say("Sorry, I'm having trouble accessing the information right now.")
        # Always prompt again for more questions or bookings
        gather2 = Gather(input='speech', action='/post-booking-options', method='POST', timeout=10)
        gather2.say("Do you have any more questions to ask, or would you like to book another appointment or lab test? You can say 'book appointment', 'book lab test', 'ask a question', or 'no'.")
        resp.append(gather2)
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

# --- Add this comment near the top of the file for DB setup ---
#
# -- SQL to create the lab_bookings table in PostgreSQL --
# CREATE TABLE lab_bookings (
#     id SERIAL PRIMARY KEY,
#     test_name VARCHAR(255) NOT NULL,
#     date DATE NOT NULL,
#     time VARCHAR(50) NOT NULL,
#     name VARCHAR(255) NOT NULL,
#     mobile VARCHAR(20) NOT NULL,
#     home_collection BOOLEAN NOT NULL DEFAULT FALSE,
#     created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
# );
#
# You can run this SQL in your PostgreSQL database to create the table for lab test bookings.

# --- Ensure all lab test booking endpoints and helpers are at the top level and properly indented ---
# (No code change, just fix indentation and remove stray lines if any)

# --- At the top, after loading doctors_list.json ---
with open('lab_tests.json', 'r', encoding='utf-8') as f:
    LAB_TESTS_LIST = json.load(f)["tests"]

def get_lab_test_names():
    return sorted(set(test["name"] for test in LAB_TESTS_LIST))

def get_lab_test_by_name(name):
    for test in LAB_TESTS_LIST:
        if test["name"].lower() == name.lower():
            return test
    return None

def get_available_lab_test_timings(name):
    test = get_lab_test_by_name(name)
    if test:
        return test["timings"]
    return None

def is_home_collection_available(name):
    test = get_lab_test_by_name(name)
    if test:
        return test.get("home_sample_collection", False)
    return False

# --- In /server-rag or main booking entry, add logic to detect lab test booking intent ---
# Example: If user says 'book lab test' or mentions a test name, start lab test booking flow
# (Add this logic in /server-rag or similar entry point)
# Pseudocode:
# if 'lab test' in rag_question.lower() or any(test in rag_question.lower() for test in get_lab_test_names()):
#     # Start lab test booking flow
#     ...

# --- Add new endpoints for lab test booking flow, similar to doctor appointment ---
# /collect-lab-test, /confirm-lab-test, /collect-lab-date, /collect-lab-time, /confirm-lab-home, /finalize-lab-booking
# Each should handle the relevant step, check home_sample_collection, and respond accordingly.
# If home collection is not available, inform user to visit hospital.
# Store lab test bookings in DB or JSON as needed. 

# --- Lab Test Booking Endpoints ---

# Helper: DB connection for lab bookings

def get_lab_db_connection():
    import os
    return psycopg2.connect(
        dbname=os.environ.get('PG_DB', 'your_db'),
        user=os.environ.get('PG_USER', 'your_user'),
        password=os.environ.get('PG_PASSWORD', 'your_password'),
        host=os.environ.get('PG_HOST', 'localhost'),
        port=os.environ.get('PG_PORT', 5432)
    )

def insert_lab_booking(test_name, date, time, name, mobile, home_collection):
    conn = get_lab_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """INSERT INTO lab_bookings (test_name, date, time, name, mobile, home_collection) VALUES (%s, %s, %s, %s, %s, %s)""",
            (test_name, date, time, name, mobile, home_collection)
        )
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        cur.close()
        conn.close()

@app.route('/collect-lab-test', methods=['POST'])
def collect_lab_test():
    call_sid = request.values.get('CallSid')
    session = user_sessions.get(call_sid, {})
    spoken_test = request.values.get('SpeechResult', '')
    logger.info(f"/collect-lab-test: User input: {spoken_test}, Session: {session}")
    test_names = get_lab_test_names()
    from rapidfuzz import process, fuzz
    match = process.extractOne(spoken_test, test_names, scorer=fuzz.token_sort_ratio)
    resp = VoiceResponse()
    test_list = ', '.join(test_names)
    if not match:
        gather = Gather(input='speech', action='/collect-lab-test', method='POST', barge_in=True, timeout=10)
        gather.say(f"Available lab tests are: {test_list}. Which lab test would you like to book? Please say the test name.")
        resp.append(gather)
        # Add second prompt if no response
        gather2 = Gather(input='speech', action='/collect-lab-test', method='POST', barge_in=True, timeout=8)
        gather2.say("Are you still there? Please say the lab test name.")
        resp.append(gather2)
        resp.say("We didn't receive any input. Thank you for calling. Goodbye!")
        resp.hangup()
        return str(resp)
    test, score, _ = match
    if score < 85:
        session['pending_lab_test'] = test
        user_sessions[call_sid] = session
        gather = Gather(input='speech', action='/confirm-lab-test', method='POST', barge_in=True, timeout=10)
        gather.say(f"Did you mean {test}? Please say yes or no.")
        resp.append(gather)
        gather2 = Gather(input='speech', action='/confirm-lab-test', method='POST', barge_in=True, timeout=8)
        gather2.say("Are you still there? Please say yes or no.")
        resp.append(gather2)
        resp.say("We didn't receive any input. Thank you for calling. Goodbye!")
        resp.hangup()
        return str(resp)
    session['lab_test'] = test
    user_sessions[call_sid] = session
    gather = Gather(input='speech', action='/collect-lab-date', method='POST', barge_in=True, timeout=10)
    gather.say(f"For which date do you want the {test}? Please say the date in the format 22 July 2025 or 22-07-2025.")
    resp.append(gather)
    gather2 = Gather(input='speech', action='/collect-lab-date', method='POST', barge_in=True, timeout=8)
    gather2.say("Are you still there? Please say the date for your lab test booking.")
    resp.append(gather2)
    resp.say("We didn't receive any input. Thank you for calling. Goodbye!")
    resp.hangup()
    return str(resp)

@app.route('/confirm-lab-test', methods=['POST'])
def confirm_lab_test():
    call_sid = request.values.get('CallSid')
    session = user_sessions.get(call_sid, {})
    answer = request.values.get('SpeechResult', '').strip().lower()
    resp = VoiceResponse()
    test = session.get('pending_lab_test')
    yes_words = ['yes', 'yeah', 'yup', 'yep', 'correct', 'right', 'ya', 'sure', 'ok', 'okay']
    no_words = ['no', 'nope', 'nah', 'not', 'incorrect', 'wrong']
    if any(word in answer for word in yes_words) and test:
        session['lab_test'] = test
        session.pop('pending_lab_test', None)
        user_sessions[call_sid] = session
        gather = Gather(input='speech', action='/collect-lab-date', method='POST', timeout=10)
        gather.say(f"Great. For which date do you want the {test}? Please say the date in the format 22 July 2025 or 22-07-2025.")
        resp.append(gather)
        gather2 = Gather(input='speech', action='/collect-lab-date', method='POST', timeout=8)
        gather2.say("Are you still there? Please say the date for your lab test booking.")
        resp.append(gather2)
        resp.say("We didn't receive any input. Thank you for calling. Goodbye!")
        resp.hangup()
        return str(resp)
    elif any(word in answer for word in no_words):
        session.pop('pending_lab_test', None)
        user_sessions[call_sid] = session
        test_list = ', '.join(get_lab_test_names())
        gather = Gather(input='speech', action='/collect-lab-test', method='POST', timeout=10)
        gather.say(f"Okay, please say the test name again. Available lab tests are: {test_list}.")
        resp.append(gather)
        gather2 = Gather(input='speech', action='/collect-lab-test', method='POST', timeout=8)
        gather2.say("Are you still there? Please say the lab test name.")
        resp.append(gather2)
        resp.say("We didn't receive any input. Thank you for calling. Goodbye!")
        resp.hangup()
        return str(resp)
    else:
        gather = Gather(input='speech', action='/confirm-lab-test', method='POST', timeout=10)
        gather.say(f"Did you mean {test}? Please say yes or no.")
        resp.append(gather)
        gather2 = Gather(input='speech', action='/confirm-lab-test', method='POST', timeout=8)
        gather2.say("Are you still there? Please say yes or no.")
        resp.append(gather2)
        resp.say("We didn't receive any input. Thank you for calling. Goodbye!")
        resp.hangup()
        return str(resp)

@app.route('/collect-lab-date', methods=['POST'])
def collect_lab_date():
    call_sid = request.values.get('CallSid')
    session = user_sessions.get(call_sid, {})
    date_text = request.values.get('SpeechResult', '')
    logger.info(f"/collect-lab-date: User input: {date_text}, Session: {session}")
    date = extract_any_date(date_text)
    if not date:
        import dateparser
        parsed = dateparser.parse(date_text)
        if parsed:
            date = parsed.strftime('%Y-%m-%d')
    resp = VoiceResponse()
    if date:
        session['lab_date'] = date
        user_sessions[call_sid] = session
        gather = Gather(input='speech', action='/confirm-lab-date', method='POST', barge_in=True, timeout=10)
        gather.say(f"You want to book the test on {date}. Is this correct? Please say yes or no.")
        resp.append(gather)
        gather2 = Gather(input='speech', action='/confirm-lab-date', method='POST', barge_in=True, timeout=8)
        gather2.say("Are you still there? Please say yes or no.")
        resp.append(gather2)
        resp.say("We didn't receive any input. Thank you for calling. Goodbye!")
        resp.hangup()
        return str(resp)
    else:
        gather = Gather(input='speech', action='/collect-lab-date', method='POST', barge_in=True, timeout=10)
        gather.say("Sorry, I didn't understand the date. Please say the date in the format 21 August 2025 or 21-08-2025.")
        resp.append(gather)
        gather2 = Gather(input='speech', action='/collect-lab-date', method='POST', barge_in=True, timeout=8)
        gather2.say("Are you still there? Please say the date for your lab test booking.")
        resp.append(gather2)
        resp.say("We didn't receive any input. Thank you for calling. Goodbye!")
        resp.hangup()
        return str(resp)

# --- Helper: Check if a lab test slot is already booked ---
def is_lab_slot_booked(test_name, date, time):
    # Check in lab_bookings.json
    try:
        with open('lab_bookings.json', 'r', encoding='utf-8') as f:
            bookings = json.load(f)
            for booking in bookings:
                if (booking.get('test_name') == test_name and
                    booking.get('date') == date and
                    booking.get('time') == time):
                    return True
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    # Check in PostgreSQL
    try:
        conn = get_lab_db_connection()
        cur = conn.cursor()
        cur.execute(
            """SELECT 1 FROM lab_bookings WHERE test_name=%s AND date=%s AND time=%s LIMIT 1""",
            (test_name, date, time)
        )
        exists = cur.fetchone() is not None
        cur.close()
        conn.close()
        if exists:
            return True
    except Exception as e:
        logger.error(f"Error checking lab slot in DB: {e}")
    return False

# --- Update /collect-lab-time endpoint to prevent double-booking and suggest next available slot ---
@app.route('/collect-lab-time', methods=['POST'])
def collect_lab_time():
    call_sid = request.values.get('CallSid')
    session = user_sessions.get(call_sid, {})
    time_text = request.values.get('SpeechResult', '')
    logger.info(f"/collect-lab-time: User input: {time_text}, Session: {session}")
    # 30-min slot logic
    test_name = session.get('lab_test')
    date = session.get('lab_date')
    timings = get_available_lab_test_timings(test_name)
    import re
    from datetime import datetime, timedelta
    slot_list = []
    if timings:
        match = re.match(r'(\d{1,2}:\d{2} [APMapm]{2}) to (\d{1,2}:\d{2} [APMapm]{2})', timings)
        if match:
            start_str, end_str = match.groups()
            start_dt = datetime.strptime(start_str.upper(), '%I:%M %p')
            end_dt = datetime.strptime(end_str.upper(), '%I:%M %p')
            t = start_dt
            while t < end_dt:
                slot_start = t.strftime('%H:%M')
                slot_end = (t + timedelta(minutes=30)).strftime('%H:%M')
                slot_list.append(f"{slot_start}-{slot_end}")
                t += timedelta(minutes=30)
    logger.info(f"/collect-lab-time: Available slots for {test_name} on {date}: {slot_list}")
    def extract_time_slot(text):
        import dateparser
        parsed = dateparser.parse(text)
        if parsed:
            slot_start = parsed.strftime('%H:%M')
            for slot in slot_list:
                if slot.startswith(slot_start):
                    return slot
        for slot in slot_list:
            if text.strip() in slot:
                return slot
        return None
    slot_val = extract_time_slot(time_text)
    resp = VoiceResponse()
    if slot_val:
        # Check if slot is already booked
        if is_lab_slot_booked(test_name, date, slot_val):
            # Suggest next available slot
            requested_time = datetime.strptime(slot_val.split('-')[0], '%H:%M')
            next_slot = None
            for slot in slot_list:
                slot_time = datetime.strptime(slot.split('-')[0], '%H:%M')
                if slot_time > requested_time and not is_lab_slot_booked(test_name, date, slot):
                    next_slot = slot
                    break
            if not next_slot:
                # If no later slot, suggest earliest available
                for slot in slot_list:
                    if not is_lab_slot_booked(test_name, date, slot):
                        next_slot = slot
                        break
            if next_slot:
                gather = Gather(input='speech', action='/confirm-lab-time', method='POST', barge_in=True, timeout=10)
                gather.say(f"Sorry, that slot is already booked. The next available slot is at {next_slot}. Is this okay? Please say yes or no.")
                session['lab_time'] = next_slot
                user_sessions[call_sid] = session
                resp.append(gather)
                return str(resp)
            else:
                gather = Gather(input='speech', action='/collect-lab-time', method='POST', barge_in=True, timeout=10)
                gather.say("Sorry, all slots are booked for this test on this date. Please try another date.")
                resp.append(gather)
                return str(resp)
        else:
            session['lab_time'] = slot_val
            user_sessions[call_sid] = session
            gather = Gather(input='speech', action='/confirm-lab-time', method='POST', barge_in=True, timeout=10)
            gather.say(f"You want to book the test at {slot_val}. Is this correct? Please say yes or no.")
            resp.append(gather)
            return str(resp)
    slot_str = ', '.join(slot_list) if slot_list else 'No slots available.'
    gather = Gather(input='speech', action='/collect-lab-time', method='POST', barge_in=True, timeout=10)
    gather.say(f"Sorry, available 30 minute slots for this test are: {slot_str}. Please say a valid time.")
    resp.append(gather)
    gather2 = Gather(input='speech', action='/collect-lab-time', method='POST', barge_in=True, timeout=8)
    gather2.say("Are you still there? Please say the time for your lab test booking.")
    resp.append(gather2)
    resp.say("We didn't receive any input. Thank you for calling. Goodbye!")
    resp.hangup()
    return str(resp)

@app.route('/confirm-lab-time', methods=['POST'])
def confirm_lab_time():
    call_sid = request.values.get('CallSid')
    session = user_sessions.get(call_sid, {})
    answer = request.values.get('SpeechResult', '').strip().lower()
    logger.info(f"/confirm-lab-time: User response: {answer}, Session: {session}")
    resp = VoiceResponse()
    time = session.get('lab_time')
    yes_words = ['yes', 'yeah', 'yup', 'yep', 'correct', 'right', 'ya', 'sure', 'ok', 'okay']
    no_words = ['no', 'nope', 'nah', 'not', 'incorrect', 'wrong']
    if any(word in answer for word in yes_words) and time:
        user_sessions[call_sid] = session
        test_name = session.get('lab_test')
        if is_home_collection_available(test_name):
            gather = Gather(input='speech', action='/confirm-lab-home', method='POST', barge_in=True, timeout=10)
            gather.say(f"Do you want to book a home lab test? Please say yes or no.")
            resp.append(gather)
            gather2 = Gather(input='speech', action='/confirm-lab-home', method='POST', barge_in=True, timeout=8)
            gather2.say("Are you still there? Please say yes or no.")
            resp.append(gather2)
            resp.say("We didn't receive any input. Thank you for calling. Goodbye!")
            resp.hangup()
            return str(resp)
        else:
            gather = Gather(input='speech', action='/confirm-lab-home', method='POST', barge_in=True, timeout=10)
            gather.say(f"Sorry, home lab test is not available for {test_name}. You can do this test at our hospital. Do you want to proceed with hospital lab test? Please say yes or no.")
            resp.append(gather)
            gather2 = Gather(input='speech', action='/confirm-lab-home', method='POST', barge_in=True, timeout=8)
            gather2.say("Are you still there? Please say yes or no.")
            resp.append(gather2)
            resp.say("We didn't receive any input. Thank you for calling. Goodbye!")
            resp.hangup()
            return str(resp)
    elif any(word in answer for word in no_words):
        gather = Gather(input='speech', action='/collect-lab-time', method='POST', barge_in=True, timeout=10)
        gather.say("Okay, please say the time again for your lab test booking.")
        resp.append(gather)
        gather2 = Gather(input='speech', action='/collect-lab-time', method='POST', barge_in=True, timeout=8)
        gather2.say("Are you still there? Please say the time for your lab test booking.")
        resp.append(gather2)
        resp.say("We didn't receive any input. Thank you for calling. Goodbye!")
        resp.hangup()
        return str(resp)
    else:
        gather = Gather(input='speech', action='/confirm-lab-time', method='POST', barge_in=True, timeout=10)
        gather.say(f"You want to book the test at {time}. Is this correct? Please say yes or no.")
        resp.append(gather)
        gather2 = Gather(input='speech', action='/confirm-lab-time', method='POST', barge_in=True, timeout=8)
        gather2.say("Are you still there? Please say yes or no.")
        resp.append(gather2)
        resp.say("We didn't receive any input. Thank you for calling. Goodbye!")
        resp.hangup()
        return str(resp)

@app.route('/confirm-lab-home', methods=['POST'])
def confirm_lab_home():
    call_sid = request.values.get('CallSid')
    session = user_sessions.get(call_sid, {})
    answer = request.values.get('SpeechResult', '').strip().lower()
    logger.info(f"/confirm-lab-home: User response: {answer}, Session: {session}")
    resp = VoiceResponse()
    test_name = session.get('lab_test')
    yes_words = ['yes', 'yeah', 'yup', 'yep', 'correct', 'right', 'ya', 'sure', 'ok', 'okay']
    no_words = ['no', 'nope', 'nah', 'not', 'incorrect', 'wrong']
    if is_home_collection_available(test_name):
        if any(word in answer for word in yes_words):
            session['lab_home_collection'] = True
            user_sessions[call_sid] = session
            gather = Gather(input='speech', action='/collect-name-lab', method='POST', barge_in=True, timeout=10)
            gather.say("Okay, we will arrange for a home lab test. Can you please share your good name for the lab test booking?")
            resp.append(gather)
            gather2 = Gather(input='speech', action='/collect-name-lab', method='POST', barge_in=True, timeout=8)
            gather2.say("Are you still there? Please say yes or no.")
            resp.append(gather2)
            resp.say("We didn't receive any input. Thank you for calling. Goodbye!")
            resp.hangup()
            return str(resp)
        elif any(word in answer for word in no_words):
            session['lab_home_collection'] = False
            user_sessions[call_sid] = session
            gather = Gather(input='speech', action='/collect-name-lab', method='POST', barge_in=True, timeout=10)
            gather.say("Okay, we will book your test at the hospital. Can you please share your good name for the lab test booking?")
            resp.append(gather)
            gather2 = Gather(input='speech', action='/collect-name-lab', method='POST', barge_in=True, timeout=8)
            gather2.say("Are you still there? Please say yes or no.")
            resp.append(gather2)
            resp.say("We didn't receive any input. Thank you for calling. Goodbye!")
            resp.hangup()
            return str(resp)
        else:
            gather = Gather(input='speech', action='/confirm-lab-home', method='POST', barge_in=True, timeout=10)
            gather.say("Do you want to book a home lab test? Please say yes or no.")
            resp.append(gather)
            gather2 = Gather(input='speech', action='/confirm-lab-home', method='POST', barge_in=True, timeout=8)
            gather2.say("Are you still there? Please say yes or no.")
            resp.append(gather2)
            resp.say("We didn't receive any input. Thank you for calling. Goodbye!")
            resp.hangup()
            return str(resp)
    else:
        session['lab_home_collection'] = False
        user_sessions[call_sid] = session
        if any(word in answer for word in yes_words):
            gather = Gather(input='speech', action='/collect-name-lab', method='POST', barge_in=True, timeout=10)
            gather.say("Can you please share your good name for the lab test booking?")
            resp.append(gather)
            gather2 = Gather(input='speech', action='/collect-name-lab', method='POST', barge_in=True, timeout=8)
            gather2.say("Are you still there? Please say yes or no.")
            resp.append(gather2)
            resp.say("We didn't receive any input. Thank you for calling. Goodbye!")
            resp.hangup()
            return str(resp)
        elif any(word in answer for word in no_words):
            gather = Gather(input='speech', action='/collect-lab-test', method='POST', barge_in=True, timeout=10)
            gather.say("Okay, please say the test name again.")
            resp.append(gather)
            gather2 = Gather(input='speech', action='/collect-lab-test', method='POST', barge_in=True, timeout=8)
            gather2.say("Are you still there? Please say the lab test name.")
            resp.append(gather2)
            resp.say("We didn't receive any input. Thank you for calling. Goodbye!")
            resp.hangup()
            return str(resp)
        else:
            gather = Gather(input='speech', action='/confirm-lab-home', method='POST', barge_in=True, timeout=10)
            gather.say(f"Sorry, home lab test is not available for {test_name}. You can do this test at our hospital. Do you want to proceed with hospital lab test? Please say yes or no.")
            resp.append(gather)
            gather2 = Gather(input='speech', action='/confirm-lab-home', method='POST', barge_in=True, timeout=8)
            gather2.say("Are you still there? Please say yes or no.")
            resp.append(gather2)
            resp.say("We didn't receive any input. Thank you for calling. Goodbye!")
            resp.hangup()
            return str(resp)

@app.route('/collect-name-lab', methods=['POST'])
def collect_name_lab():
    call_sid = request.values.get('CallSid')
    name = request.values.get('SpeechResult', '')
    session = user_sessions.get(call_sid, {})
    resp = VoiceResponse()
    attempts = session.get('name_attempts', 0)
    if not name.strip():
        attempts += 1
        session['name_attempts'] = attempts
        user_sessions[call_sid] = session
        if attempts < 2:
            gather = Gather(input='speech', action='/collect-name-lab', method='POST', timeout=10, language='en-IN')
            gather.say("Sorry, I didn't catch your name. Can you repeat your name for the lab test booking?")
            resp.append(gather)
            gather2 = Gather(input='speech', action='/collect-name-lab', method='POST', timeout=8, language='en-IN')
            gather2.say("Are you still there? Please say your name for the lab test booking.")
            resp.append(gather2)
            resp.say("We didn't receive any input. Thank you for calling. Goodbye!")
            resp.hangup()
            return str(resp)
        else:
            resp.say("Sorry, I didn't catch your name. Please call again to book your lab test. Goodbye!")
            resp.hangup()
            return str(resp)
    session['lab_name'] = name
    session['name_attempts'] = 0
    user_sessions[call_sid] = session
    gather = Gather(input='dtmf', num_digits=10, action='/confirm-mobile-lab', method='POST', timeout=10)
    gather.say("Thank you. Now, please enter your 10 digit mobile number using the keypad.")
    resp.append(gather)
    gather2 = Gather(input='dtmf', num_digits=10, action='/confirm-mobile-lab', method='POST', timeout=8)
    gather2.say("Are you still there? Please enter your 10 digit mobile number.")
    resp.append(gather2)
    resp.say("We didn't receive any input. Thank you for calling. Goodbye!")
    resp.hangup()
    return str(resp)

@app.route('/confirm-mobile-lab', methods=['POST'])
def confirm_mobile_lab():
    call_sid = request.values.get('CallSid')
    digits = request.values.get('Digits', '')
    session = user_sessions.get(call_sid, {})
    session['lab_pending_mobile'] = digits
    user_sessions[call_sid] = session
    resp = VoiceResponse()
    try:
        if len(digits) == 10:
            details = (
                f"You are booking {session.get('lab_test','')} on {session.get('lab_date','')} at {session.get('lab_time','')}. "
                f"Your name is {session.get('lab_name','')} and your mobile number is {digits}. Is this correct? Please say yes or no."
            )
            gather = Gather(input='speech', action='/finalize-lab-booking', method='POST', timeout=10)
            gather.say(details)
            resp.append(gather)
            gather2 = Gather(input='speech', action='/finalize-lab-booking', method='POST', timeout=8)
            gather2.say("Are you still there? Please say yes or no.")
            resp.append(gather2)
            resp.say("We didn't receive any input. Thank you for calling. Goodbye!")
            resp.hangup()
        else:
            gather = Gather(input='dtmf', num_digits=10, action='/confirm-mobile-lab', method='POST', timeout=10)
            gather.say("That was not a valid mobile number. Please enter your 10 digit mobile number using the keypad.")
            resp.append(gather)
            gather2 = Gather(input='dtmf', num_digits=10, action='/confirm-mobile-lab', method='POST', timeout=8)
            gather2.say("Are you still there? Please enter your 10 digit mobile number.")
            resp.append(gather2)
            resp.say("We didn't receive any input. Thank you for calling. Goodbye!")
            resp.hangup()
    except Exception as e:
        resp.say("Sorry, there was an error processing your input. Please try again later.")
        resp.hangup()
    return str(resp)

@app.route('/finalize-lab-booking', methods=['POST'])
def finalize_lab_booking():
    call_sid = request.values.get('CallSid')
    session = user_sessions.get(call_sid, {})
    answer = request.values.get('SpeechResult', '').strip().lower()
    logger.info(f"/finalize-lab-booking: User response: {answer}, Session: {session}")
    resp = VoiceResponse()
    digits = session.get('lab_pending_mobile', '')
    test_name = session.get('lab_test')
    date = session.get('lab_date')
    time = session.get('lab_time')
    name = session.get('lab_name')
    home_collection = session.get('lab_home_collection', False)
    yes_words = ['yes', 'yeah', 'yup', 'yep', 'correct', 'right', 'ya', 'sure', 'ok', 'okay']
    no_words = ['no', 'nope', 'nah', 'not', 'incorrect', 'wrong']
    if any(word in answer for word in yes_words) and len(digits) == 10:
        try:
            insert_lab_booking(test_name, date, time, name, digits, home_collection)
            logger.info(f"/finalize-lab-booking: Booking confirmed for {test_name} on {date} at {time}, Name: {name}, Mobile: {digits}, Home: {home_collection}")
            # --- Also append to lab_bookings.json for backup/audit ---
            import os, json
            from datetime import datetime
            booking_data = {
                'test_name': test_name,
                'date': date,
                'time': time,
                'name': name,
                'mobile': digits,
                'home_lab_test': home_collection,
                'created_at': datetime.now().isoformat()
            }
            try:
                bookings_file = 'lab_bookings.json'
                if os.path.exists(bookings_file):
                    with open(bookings_file, 'r', encoding='utf-8') as f:
                        bookings = json.load(f)
                        if not isinstance(bookings, list):
                            bookings = []
                else:
                    bookings = []
                bookings.append(booking_data)
                with open(bookings_file, 'w', encoding='utf-8') as f:
                    json.dump(bookings, f, indent=2)
            except Exception as e:
                logger.error(f"Error writing to lab_bookings.json: {e}")
            # --- Send SMS confirmation ---
            sms_msg = (
                f"Your lab test booking is confirmed!\n"
                f"Test: {test_name}\n"
                f"Date: {date}\n"
                f"Time: {time}\n"
                f"Name: {name}\n"
                f"Mobile: {digits}\n"
                f"Home Lab Test: {'Yes' if home_collection else 'No'}"
            )
            try:
                send_sms(f"+91{digits}", sms_msg)
            except Exception as e:
                logger.error(f"Error sending SMS for lab test: {e}")
            resp.say(f"Your lab test {test_name} has been booked for {date} at {time}. Thank you!")
            gather = Gather(input='speech', action='/post-booking-options', method='POST', timeout=10)
            gather.say("Do you have any more questions to ask, or would you like to book another appointment or lab test? You can say 'book appointment', 'book lab test', 'ask a question', or 'no'.")
            resp.append(gather)
            gather2 = Gather(input='speech', action='/post-booking-options', method='POST', timeout=8)
            gather2.say("Are you still there? Do you want to book another appointment or lab test?")
            resp.append(gather2)
            resp.say("We didn't receive any input. Thank you for calling. Goodbye!")
            resp.hangup()
            return str(resp)
        except Exception as e:
            logger.error(f"/finalize-lab-booking: Error booking: {e}")
            resp.say("Sorry, there was an error booking your lab test. Please try again.")
            resp.hangup()
            return str(resp)
    elif any(word in answer for word in no_words):
        gather = Gather(input='speech', action='/collect-name-lab', method='POST', timeout=10)
        gather.say("Let's try again. Please tell me your name for the lab test booking.")
        resp.append(gather)
        gather2 = Gather(input='speech', action='/collect-name-lab', method='POST', timeout=8)
        gather2.say("Are you still there? Please say your name for the lab test booking.")
        resp.append(gather2)
        resp.say("We didn't receive any input. Thank you for calling. Goodbye!")
        resp.hangup()
        return str(resp)
    else:
        gather = Gather(input='speech', action='/finalize-lab-booking', method='POST', timeout=10)
        gather.say("Is the information correct? Please say yes or no.")
        resp.append(gather)
        gather2 = Gather(input='speech', action='/finalize-lab-booking', method='POST', timeout=8)
        gather2.say("Are you still there? Please say yes or no.")
        resp.append(gather2)
        resp.say("We didn't receive any input. Thank you for calling. Goodbye!")
        resp.hangup()
        return str(resp)

@app.route('/confirm-lab-date', methods=['POST'])
def confirm_lab_date():
    call_sid = request.values.get('CallSid')
    session = user_sessions.get(call_sid, {})
    answer = request.values.get('SpeechResult', '').strip().lower()
    logger.info(f"/confirm-lab-date: User response: {answer}, Session: {session}")
    resp = VoiceResponse()
    date = session.get('lab_date')
    yes_words = ['yes', 'yeah', 'yup', 'yep', 'correct', 'right', 'ya', 'sure', 'ok', 'okay']
    no_words = ['no', 'nope', 'nah', 'not', 'incorrect', 'wrong']
    if any(word in answer for word in yes_words) and date:
        user_sessions[call_sid] = session
        gather = Gather(input='speech', action='/collect-lab-time', method='POST', barge_in=True, timeout=10)
        timings = get_available_lab_test_timings(session.get('lab_test'))
        gather.say(f"Great. At what time on {date}? Available timings for this test are: {timings}. Please say the time.")
        resp.append(gather)
        gather2 = Gather(input='speech', action='/collect-lab-time', method='POST', barge_in=True, timeout=8)
        gather2.say("Are you still there? Please say the time for your lab test booking.")
        resp.append(gather2)
        resp.say("We didn't receive any input. Thank you for calling. Goodbye!")
        resp.hangup()
        return str(resp)
    elif any(word in answer for word in no_words):
        gather = Gather(input='speech', action='/collect-lab-date', method='POST', barge_in=True, timeout=10)
        gather.say("Okay, please say the date again for your lab test booking.")
        resp.append(gather)
        gather2 = Gather(input='speech', action='/collect-lab-date', method='POST', barge_in=True, timeout=8)
        gather2.say("Are you still there? Please say the date for your lab test booking.")
        resp.append(gather2)
        resp.say("We didn't receive any input. Thank you for calling. Goodbye!")
        resp.hangup()
        return str(resp)
    else:
        gather = Gather(input='speech', action='/confirm-lab-date', method='POST', barge_in=True, timeout=10)
        gather.say(f"You want to book the test on {date}. Is this correct? Please say yes or no.")
        resp.append(gather)
        gather2 = Gather(input='speech', action='/confirm-lab-date', method='POST', barge_in=True, timeout=8)
        gather2.say("Are you still there? Please say yes or no.")
        resp.append(gather2)
        resp.say("We didn't receive any input. Thank you for calling. Goodbye!")
        resp.hangup()
        return str(resp)

# --- Doctor Appointment Rescheduling ---
@app.route('/reschedule-appointment', methods=['POST'])
def reschedule_appointment():
    from twilio.twiml.voice_response import VoiceResponse, Gather
    call_sid = request.values.get('CallSid')
    session = user_sessions.get(call_sid, {})
    step = session.get('reschedule_step', 'start')
    resp = VoiceResponse()
    if step == 'start':
        # Step 1: Ask for mobile number
        session['reschedule_step'] = 'get_mobile'
        user_sessions[call_sid] = session
        gather = Gather(input='dtmf', num_digits=10, action='/reschedule-appointment', method='POST', timeout=15)
        gather.say("To reschedule your appointment, please enter your 10 digit mobile number using the keypad.")
        resp.append(gather)
        return str(resp)
    elif step == 'get_mobile':
        digits = request.values.get('Digits', '')
        if len(digits) != 10:
            gather = Gather(input='dtmf', num_digits=10, action='/reschedule-appointment', method='POST', timeout=15)
            gather.say("That was not a valid mobile number. Please enter your 10 digit mobile number using the keypad.")
            resp.append(gather)
            return str(resp)
        session['reschedule_mobile'] = digits
        # Step 2: Find latest booking for this mobile
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT id, doctor, department, date, time, name FROM bookings WHERE mobile=%s ORDER BY date DESC, time DESC LIMIT 1", (digits,))
        booking = cur.fetchone()
        cur.close()
        conn.close()
        if not booking:
            resp.say("Sorry, no appointment was found for this mobile number. Please check and try again.")
            resp.hangup()
            return str(resp)
        session['reschedule_booking_id'] = booking[0]
        session['reschedule_doctor'] = booking[1]
        session['reschedule_department'] = booking[2]
        session['reschedule_old_date'] = booking[3]
        session['reschedule_old_time'] = booking[4]
        session['reschedule_name'] = booking[5]
        user_sessions[call_sid] = session
        # Step 3: Ask for new date
        session['reschedule_step'] = 'get_new_date'
        user_sessions[call_sid] = session
        gather = Gather(input='speech', action='/reschedule-appointment', method='POST', timeout=12)
        gather.say(f"Found your appointment with {booking[1]} in {booking[2]} on {booking[3]} at {booking[4]}. What new date would you like to reschedule to? Please say the date.")
        resp.append(gather)
        return str(resp)
    elif step == 'get_new_date':
        date_text = request.values.get('SpeechResult', '')
        date = extract_any_date(date_text)
        if not date:
            gather = Gather(input='speech', action='/reschedule-appointment', method='POST', timeout=12)
            gather.say("Sorry, I didn't understand the date. Please say the new date for your appointment.")
            resp.append(gather)
            return str(resp)
        session['reschedule_new_date'] = date
        session['reschedule_step'] = 'get_new_time'
        user_sessions[call_sid] = session
        gather = Gather(input='speech', action='/reschedule-appointment', method='POST', timeout=12)
        gather.say(f"On {date}, what time would you like? Please say the time, for example 3pm or 14:00.")
        resp.append(gather)
        return str(resp)
    elif step == 'get_new_time':
        time_text = request.values.get('SpeechResult', '')
        time_val = extract_time(time_text)
        if not time_val:
            gather = Gather(input='speech', action='/reschedule-appointment', method='POST', timeout=12)
            gather.say("Sorry, I didn't understand the time. Please say the new time for your appointment.")
            resp.append(gather)
            return str(resp)
        # Step 4: Check slot availability
        department = session['reschedule_department']
        doctor = session['reschedule_doctor']
        date = session['reschedule_new_date']
        available_slots = get_available_slots_for_department_and_date(department, date)
        slot_available = any(slot['doctor'] == doctor and slot['time'] == time_val for slot in available_slots)
        if not slot_available:
            # Suggest next available slot for this doctor
            next_slot = None
            from datetime import datetime
            requested_time = datetime.strptime(time_val.split('-')[0], '%H:%M')
            for slot in available_slots:
                if slot['doctor'] == doctor:
                    slot_time = datetime.strptime(slot['time'].split('-')[0], '%H:%M')
                    if slot_time > requested_time:
                        next_slot = slot['time']
                        break
            if next_slot:
                gather = Gather(input='speech', action='/reschedule-appointment', method='POST', timeout=12)
                gather.say(f"Sorry, that slot is not available. The next available slot for {doctor} is at {next_slot}. Would you like to reschedule to this time? Please say yes or no.")
                session['reschedule_suggested_time'] = next_slot
                session['reschedule_step'] = 'confirm_suggested_time'
                user_sessions[call_sid] = session
                resp.append(gather)
                return str(resp)
            else:
                resp.say("Sorry, no available slots for this doctor on that date. Please try another date.")
                resp.hangup()
                return str(resp)
        session['reschedule_new_time'] = time_val
        session['reschedule_step'] = 'confirm_new_time'
        user_sessions[call_sid] = session
        gather = Gather(input='speech', action='/reschedule-appointment', method='POST', timeout=12)
        gather.say(f"You want to reschedule your appointment with {doctor} in {department} to {date} at {time_val}. Is this correct? Please say yes or no.")
        resp.append(gather)
        return str(resp)
    elif step == 'confirm_suggested_time':
        answer = request.values.get('SpeechResult', '').strip().lower()
        yes_words = ['yes', 'yeah', 'yup', 'yep', 'correct', 'right', 'ya', 'sure', 'ok', 'okay']
        no_words = ['no', 'nope', 'nah', 'not', 'incorrect', 'wrong']
        if any(word in answer for word in yes_words):
            session['reschedule_new_time'] = session['reschedule_suggested_time']
            session['reschedule_step'] = 'confirm_new_time'
            user_sessions[call_sid] = session
            gather = Gather(input='speech', action='/reschedule-appointment', method='POST', timeout=12)
            gather.say(f"You want to reschedule your appointment to {session['reschedule_new_date']} at {session['reschedule_new_time']}. Is this correct? Please say yes or no.")
            resp.append(gather)
            return str(resp)
        elif any(word in answer for word in no_words):
            session['reschedule_step'] = 'get_new_time'
            user_sessions[call_sid] = session
            gather = Gather(input='speech', action='/reschedule-appointment', method='POST', timeout=12)
            gather.say("Okay, please say another time for your appointment.")
            resp.append(gather)
            return str(resp)
        else:
            gather = Gather(input='speech', action='/reschedule-appointment', method='POST', timeout=12)
            gather.say("Would you like to reschedule to the suggested time? Please say yes or no.")
            resp.append(gather)
            return str(resp)
    elif step == 'confirm_new_time':
        answer = request.values.get('SpeechResult', '').strip().lower()
        yes_words = ['yes', 'yeah', 'yup', 'yep', 'correct', 'right', 'ya', 'sure', 'ok', 'okay']
        no_words = ['no', 'nope', 'nah', 'not', 'incorrect', 'wrong']
        if any(word in answer for word in yes_words):
            # Step 5: Update booking in DB and JSON
            booking_id = session['reschedule_booking_id']
            new_date = session['reschedule_new_date']
            new_time = session['reschedule_new_time']
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("UPDATE bookings SET date=%s, time=%s WHERE id=%s", (new_date, new_time, booking_id))
            conn.commit()
            cur.close()
            conn.close()
            # Update bookings.json
            try:
                with open('bookings.json', 'r', encoding='utf-8') as f:
                    bookings = json.load(f)
                for b in bookings:
                    if b.get('mobile') == session['reschedule_mobile'] and b.get('doctor') == session['reschedule_doctor'] and b.get('date') == session['reschedule_old_date'] and b.get('time') == session['reschedule_old_time']:
                        b['date'] = new_date
                        b['time'] = new_time
                with open('bookings.json', 'w', encoding='utf-8') as f:
                    json.dump(bookings, f, indent=2)
            except Exception as e:
                logger.error(f"Error updating bookings.json: {e}")
            # Step 6: Confirm and send SMS
            sms_msg = (
                f"Your appointment has been rescheduled!\n"
                f"Doctor: {session['reschedule_doctor']}\n"
                f"Department: {session['reschedule_department']}\n"
                f"Date: {new_date}\n"
                f"Time: {new_time}\n"
                f"Name: {session['reschedule_name']}\n"
                f"Mobile: {session['reschedule_mobile']}"
            )
            try:
                send_sms(f"+91{session['reschedule_mobile']}", sms_msg)
            except Exception as e:
                logger.error(f"Error sending SMS: {e}")
            resp.say(f"Your appointment has been rescheduled to {new_date} at {new_time}. Thank you!")
            resp.hangup()
            return str(resp)
        elif any(word in answer for word in no_words):
            resp.say("Okay, rescheduling cancelled. Your appointment remains unchanged. Thank you!")
            resp.hangup()
            return str(resp)
        else:
            gather = Gather(input='speech', action='/reschedule-appointment', method='POST', timeout=12)
            gather.say("Is the new date and time correct? Please say yes or no.")
            resp.append(gather)
            return str(resp)
    else:
        resp.say("Sorry, something went wrong in the rescheduling process. Please try again.")
        resp.hangup()
        return str(resp)

# --- Lab Test Rescheduling ---
@app.route('/reschedule-lab-test', methods=['POST'])
def reschedule_lab_test():
    from twilio.twiml.voice_response import VoiceResponse, Gather
    call_sid = request.values.get('CallSid')
    session = user_sessions.get(call_sid, {})
    step = session.get('lab_reschedule_step', 'start')
    resp = VoiceResponse()
    if step == 'start':
        # Step 1: Ask for mobile number
        session['lab_reschedule_step'] = 'get_mobile'
        user_sessions[call_sid] = session
        gather = Gather(input='dtmf', num_digits=10, action='/reschedule-lab-test', method='POST', timeout=15)
        gather.say("To reschedule your lab test, please enter your 10 digit mobile number using the keypad.")
        resp.append(gather)
        return str(resp)
    elif step == 'get_mobile':
        digits = request.values.get('Digits', '')
        if len(digits) != 10:
            gather = Gather(input='dtmf', num_digits=10, action='/reschedule-lab-test', method='POST', timeout=15)
            gather.say("That was not a valid mobile number. Please enter your 10 digit mobile number using the keypad.")
            resp.append(gather)
            return str(resp)
        session['lab_reschedule_mobile'] = digits
        # Step 2: Find latest lab booking for this mobile
        conn = get_lab_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT id, test_name, date, time, name FROM lab_bookings WHERE mobile=%s ORDER BY date DESC, time DESC LIMIT 1", (digits,))
        booking = cur.fetchone()
        cur.close()
        conn.close()
        if not booking:
            resp.say("Sorry, no lab test booking was found for this mobile number. Please check and try again.")
            resp.hangup()
            return str(resp)
        session['lab_reschedule_booking_id'] = booking[0]
        session['lab_reschedule_test'] = booking[1]
        session['lab_reschedule_old_date'] = booking[2]
        session['lab_reschedule_old_time'] = booking[3]
        session['lab_reschedule_name'] = booking[4]
        user_sessions[call_sid] = session
        # Step 3: Ask for new date
        session['lab_reschedule_step'] = 'get_new_date'
        user_sessions[call_sid] = session
        gather = Gather(input='speech', action='/reschedule-lab-test', method='POST', timeout=12)
        gather.say(f"Found your lab test booking for {booking[1]} on {booking[2]} at {booking[3]}. What new date would you like to reschedule to? Please say the date.")
        resp.append(gather)
        return str(resp)
    elif step == 'get_new_date':
        date_text = request.values.get('SpeechResult', '')
        date = extract_any_date(date_text)
        if not date:
            gather = Gather(input='speech', action='/reschedule-lab-test', method='POST', timeout=12)
            gather.say("Sorry, I didn't understand the date. Please say the new date for your lab test.")
            resp.append(gather)
            return str(resp)
        session['lab_reschedule_new_date'] = date
        session['lab_reschedule_step'] = 'get_new_time'
        user_sessions[call_sid] = session
        gather = Gather(input='speech', action='/reschedule-lab-test', method='POST', timeout=12)
        gather.say(f"On {date}, what time would you like? Please say the time, for example 3pm or 14:00.")
        resp.append(gather)
        return str(resp)
    elif step == 'get_new_time':
        time_text = request.values.get('SpeechResult', '')
        # Build slot list for the test
        test_name = session['lab_reschedule_test']
        timings = get_available_lab_test_timings(test_name)
        import re
        from datetime import datetime, timedelta
        slot_list = []
        if timings:
            match = re.match(r'(\d{1,2}:\d{2} [APMapm]{2}) to (\d{1,2}:\d{2} [APMapm]{2})', timings)
            if match:
                start_str, end_str = match.groups()
                start_dt = datetime.strptime(start_str.upper(), '%I:%M %p')
                end_dt = datetime.strptime(end_str.upper(), '%I:%M %p')
                t = start_dt
                while t < end_dt:
                    slot_start = t.strftime('%H:%M')
                    slot_end = (t + timedelta(minutes=30)).strftime('%H:%M')
                    slot_list.append(f"{slot_start}-{slot_end}")
                    t += timedelta(minutes=30)
        def extract_time_slot(text):
            import dateparser
            parsed = dateparser.parse(text)
            if parsed:
                slot_start = parsed.strftime('%H:%M')
                for slot in slot_list:
                    if slot.startswith(slot_start):
                        return slot
            for slot in slot_list:
                if text.strip() in slot:
                    return slot
            return None
        slot_val = extract_time_slot(time_text)
        if not slot_val:
            slot_str = ', '.join(slot_list) if slot_list else 'No slots available.'
            gather = Gather(input='speech', action='/reschedule-lab-test', method='POST', timeout=12)
            gather.say(f"Sorry, available 30 minute slots for this test are: {slot_str}. Please say a valid time.")
            resp.append(gather)
            return str(resp)
        # Step 4: Check slot availability
        date = session['lab_reschedule_new_date']
        if is_lab_slot_booked(test_name, date, slot_val):
            # Suggest next available slot
            requested_time = datetime.strptime(slot_val.split('-')[0], '%H:%M')
            next_slot = None
            for slot in slot_list:
                slot_time = datetime.strptime(slot.split('-')[0], '%H:%M')
                if slot_time > requested_time and not is_lab_slot_booked(test_name, date, slot):
                    next_slot = slot
                    break
            if not next_slot:
                for slot in slot_list:
                    if not is_lab_slot_booked(test_name, date, slot):
                        next_slot = slot
                        break
            if next_slot:
                gather = Gather(input='speech', action='/reschedule-lab-test', method='POST', timeout=12)
                gather.say(f"Sorry, that slot is already booked. The next available slot is at {next_slot}. Would you like to reschedule to this time? Please say yes or no.")
                session['lab_reschedule_suggested_time'] = next_slot
                session['lab_reschedule_step'] = 'confirm_suggested_time'
                user_sessions[call_sid] = session
                resp.append(gather)
                return str(resp)
            else:
                resp.say("Sorry, all slots are booked for this test on this date. Please try another date.")
                resp.hangup()
                return str(resp)
        session['lab_reschedule_new_time'] = slot_val
        session['lab_reschedule_step'] = 'confirm_new_time'
        user_sessions[call_sid] = session
        gather = Gather(input='speech', action='/reschedule-lab-test', method='POST', timeout=12)
        gather.say(f"You want to reschedule your lab test {test_name} to {date} at {slot_val}. Is this correct? Please say yes or no.")
        resp.append(gather)
        return str(resp)
    elif step == 'confirm_suggested_time':
        answer = request.values.get('SpeechResult', '').strip().lower()
        yes_words = ['yes', 'yeah', 'yup', 'yep', 'correct', 'right', 'ya', 'sure', 'ok', 'okay']
        no_words = ['no', 'nope', 'nah', 'not', 'incorrect', 'wrong']
        if any(word in answer for word in yes_words):
            session['lab_reschedule_new_time'] = session['lab_reschedule_suggested_time']
            session['lab_reschedule_step'] = 'confirm_new_time'
            user_sessions[call_sid] = session
            gather = Gather(input='speech', action='/reschedule-lab-test', method='POST', timeout=12)
            gather.say(f"You want to reschedule your lab test to {session['lab_reschedule_new_date']} at {session['lab_reschedule_new_time']}. Is this correct? Please say yes or no.")
            resp.append(gather)
            return str(resp)
        elif any(word in answer for word in no_words):
            session['lab_reschedule_step'] = 'get_new_time'
            user_sessions[call_sid] = session
            gather = Gather(input='speech', action='/reschedule-lab-test', method='POST', timeout=12)
            gather.say("Okay, please say another time for your lab test.")
            resp.append(gather)
            return str(resp)
        else:
            gather = Gather(input='speech', action='/reschedule-lab-test', method='POST', timeout=12)
            gather.say("Would you like to reschedule to the suggested time? Please say yes or no.")
            resp.append(gather)
            return str(resp)
    elif step == 'confirm_new_time':
        answer = request.values.get('SpeechResult', '').strip().lower()
        yes_words = ['yes', 'yeah', 'yup', 'yep', 'correct', 'right', 'ya', 'sure', 'ok', 'okay']
        no_words = ['no', 'nope', 'nah', 'not', 'incorrect', 'wrong']
        if any(word in answer for word in yes_words):
            # Step 5: Update booking in DB and JSON
            booking_id = session['lab_reschedule_booking_id']
            new_date = session['lab_reschedule_new_date']
            new_time = session['lab_reschedule_new_time']
            conn = get_lab_db_connection()
            cur = conn.cursor()
            cur.execute("UPDATE lab_bookings SET date=%s, time=%s WHERE id=%s", (new_date, new_time, booking_id))
            conn.commit()
            cur.close()
            conn.close()
            # Update lab_bookings.json
            try:
                with open('lab_bookings.json', 'r', encoding='utf-8') as f:
                    bookings = json.load(f)
                for b in bookings:
                    if b.get('mobile') == session['lab_reschedule_mobile'] and b.get('test_name') == session['lab_reschedule_test'] and b.get('date') == session['lab_reschedule_old_date'] and b.get('time') == session['lab_reschedule_old_time']:
                        b['date'] = new_date
                        b['time'] = new_time
                with open('lab_bookings.json', 'w', encoding='utf-8') as f:
                    json.dump(bookings, f, indent=2)
            except Exception as e:
                logger.error(f"Error updating lab_bookings.json: {e}")
            # Step 6: Confirm and send SMS
            sms_msg = (
                f"Your lab test booking has been rescheduled!\n"
                f"Test: {session['lab_reschedule_test']}\n"
                f"Date: {new_date}\n"
                f"Time: {new_time}\n"
                f"Name: {session['lab_reschedule_name']}\n"
                f"Mobile: {session['lab_reschedule_mobile']}"
            )
            try:
                send_sms(f"+91{session['lab_reschedule_mobile']}", sms_msg)
            except Exception as e:
                logger.error(f"Error sending SMS: {e}")
            resp.say(f"Your lab test has been rescheduled to {new_date} at {new_time}. Thank you!")
            resp.hangup()
            return str(resp)
        elif any(word in answer for word in no_words):
            resp.say("Okay, rescheduling cancelled. Your lab test booking remains unchanged. Thank you!")
            resp.hangup()
            return str(resp)
        else:
            gather = Gather(input='speech', action='/reschedule-lab-test', method='POST', timeout=12)
            gather.say("Is the new date and time correct? Please say yes or no.")
            resp.append(gather)
            return str(resp)
    else:
        resp.say("Sorry, something went wrong in the rescheduling process. Please try again.")
        resp.hangup()
        return str(resp)

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return 'No file part', 400
    file = request.files['file']
    if file.filename == '':
        return 'No selected file', 400
    save_path = os.path.join('upload', file.filename)
    file.save(save_path)
    return 'File uploaded', 200

@app.route('/api/lab-booking', methods=['POST'])
def api_lab_booking():
    data = request.get_json()
    required = ['test_name', 'date', 'time', 'name', 'mobile', 'home_collection']
    if not all(k in data for k in required):
        return jsonify({"success": False, "message": "Missing required fields."}), 400
    try:
        # Check if slot is already booked (Postgres)
        if is_lab_slot_booked(data['test_name'], data['date'], data['time']):
            return jsonify({"success": False, "message": "Slot already booked."}), 409
        insert_lab_booking(
            data['test_name'],
            data['date'],
            data['time'],
            data['name'],
            data['mobile'],
            data['home_collection']
        )
        return jsonify({"success": True, "message": "Lab test booked successfully."})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@app.route('/api/doctor-booking', methods=['POST'])
def api_doctor_booking():
    data = request.get_json()
    required = ['department', 'doctor', 'date', 'time', 'name', 'mobile']
    if not all(k in data for k in required):
        return jsonify({"success": False, "message": "Missing required fields."}), 400
    try:
        # Check if slot is already booked (Postgres)
        if is_slot_booked(data['doctor'], data['date'], data['time']):
            return jsonify({"success": False, "message": "Slot already booked."}), 409
        insert_booking(
            data['department'],
            data['doctor'],
            data['date'],
            data['time'],
            data['name'],
            data['mobile']
        )
        return jsonify({"success": True, "message": "Doctor appointment booked successfully."})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@app.route('/api/lab-bookings', methods=['GET'])
def api_get_lab_bookings():
    try:
        conn = get_lab_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT id, test_name, date, time, name, mobile, home_collection, created_at FROM lab_bookings ORDER BY created_at DESC")
        rows = cur.fetchall()
        cur.close()
        conn.close()
        bookings = [
            {
                "id": row[0],
                "test_name": row[1],
                "date": str(row[2]),
                "time": row[3],
                "name": row[4],
                "mobile": row[5],
                "home_collection": row[6],
                "created_at": row[7].isoformat() if row[7] else None
            }
            for row in rows
        ]
        return jsonify({"success": True, "bookings": bookings})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@app.route('/api/doctor-bookings', methods=['GET'])
def api_get_doctor_bookings():
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT id, department, doctor, date, time, name, mobile FROM bookings ORDER BY date DESC, time DESC")
        rows = cur.fetchall()
        cur.close()
        conn.close()
        bookings = [
            {
                "id": row[0],
                "department": row[1],
                "doctor": row[2],
                "date": str(row[3]),
                "time": row[4],
                "name": row[5],
                "mobile": row[6]
            }
            for row in rows
        ]
        return jsonify({"success": True, "bookings": bookings})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@app.route('/upload-csv', methods=['POST'])
def upload_csv():
    if 'file' not in request.files:
        return jsonify({'success': False, 'message': 'No file part'}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({'success': False, 'message': 'No selected file'}), 400
    save_path = os.path.join('upload_csv', file.filename)
    file.save(save_path)
    # Convert to JSON
    hospital_name = file.filename.rsplit('.', 1)[0]
    json_path = os.path.join('upload', f"{hospital_name}.json")
    data = []
    try:
        import csv, json
        with open(save_path, 'r', encoding='utf-8') as csvfile:
            reader = csv.DictReader(csvfile)
            for row in reader:
                data.append(row)
        with open(json_path, 'w', encoding='utf-8') as jsonfile:
            json.dump(data, jsonfile, indent=2)
        return jsonify({'success': True, 'message': f'File {file.filename} uploaded and converted to {hospital_name}.json.'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

if __name__ == '__main__':
    # Log startup
    logger.info("Starting webhook server on http://localhost:5000")
    logger.info("Make sure to expose this server to the internet using ngrok or similar")
    
    # Start the server
    app.run(
        debug=True,
        host='0.0.0.0',
        port=5001,
        ssl_context=None  # Let ngrok handle SSL
    ) 
