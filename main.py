from flask import Flask, request, jsonify, Response
from twilio.twiml.voice_response import VoiceResponse, Gather
from twilio.request_validator import RequestValidator
import os
import logging
import json
from datetime import datetime
from dotenv import load_dotenv
import re
from rapidfuzz import process
import dateparser
from sms import send_sms
from appointment import AppointmentManager
import requests
from model import summarize, is_bye, extract_date, extract_time, is_confirm

appointment_mgr = AppointmentManager()
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
        
        # If no input is received
        response.say("We didn't receive any input. Please call back later. Goodbye!")
        
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
    # Departments
    departments = appointment_mgr.get_departments()
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

def best_match_department(user_text, departments, threshold=70):
    result = process.extractOne(user_text, departments, score_cutoff=threshold)
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
            departments = appointment_mgr.get_departments()
            dept_list = ', '.join(departments)
            gather = Gather(input='speech', action='/collect-department', method='POST')
            gather.say(f"Which department do you want to book an appointment in? Available departments are: {dept_list}.")
            resp.append(gather)
            resp.say("We didn't receive any input. Goodbye!")
            return str(resp)
        if department and date and time:
            # Find all available doctors for that slot
            available_doctors = appointment_mgr.get_available_doctors_by_date(department, date, time)
            if available_doctors:
                call_sid = request.values.get('CallSid')
                # If only one doctor, ask for confirmation
                if len(available_doctors) == 1:
                    slot = {
                        'doctor': available_doctors[0],
                        'department': department,
                        'date': date,
                        'time': time
                    }
                    user_sessions[call_sid] = {'step': 'confirm', **slot}
                    gather = Gather(input='speech', action='/confirm-booking', method='POST')
                    gather.say(f"Dr. {slot['doctor']} is available in {department} on {date} at {time}. Would you like to book with Dr. {slot['doctor']}? Please say yes or no.")
                    resp.append(gather)
                    return str(resp)
                else:
                    # Multiple doctors available, list them and ask for confirmation for the first
                    slot = {
                        'doctor': available_doctors[0],
                        'department': department,
                        'date': date,
                        'time': time
                    }
                    user_sessions[call_sid] = {'step': 'confirm', **slot}
                    doc_list = ', '.join([f"Dr. {d}" for d in available_doctors])
                    gather = Gather(input='speech', action='/confirm-booking', method='POST')
                    gather.say(f"The following doctors are available in {department} on {date} at {time}: {doc_list}. Would you like to book with Dr. {slot['doctor']}? Please say yes or no.")
                    resp.append(gather)
                    return str(resp)
            else:
                # Suggest nearest slot
                suggestion = appointment_mgr.suggest_nearest_slot(department, date, time)
                if suggestion:
                    call_sid = request.values.get('CallSid')
                    user_sessions[call_sid] = {'step': 'suggest', **suggestion}
                    gather = Gather(input='speech', action='/confirm-booking', method='POST')
                    gather.say(f"No doctor is available at that time. The nearest available slot is with Dr. {suggestion['doctor']} in {department} on {suggestion['day']} at {suggestion['time']}. Do you want to book this slot? Please say yes or no.")
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
            departments = appointment_mgr.get_departments()
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


@app.route('/collect-department', methods=['POST'])
def collect_department():
    call_sid = request.values.get('CallSid')
    session = user_sessions.get(call_sid, {})
    if session.get('department'):
        department = session['department']
    else:
        spoken_dept = request.values.get('SpeechResult', '')
        departments = appointment_mgr.get_departments()
        department = best_match_department(spoken_dept, departments)
        session['department'] = department
        user_sessions[call_sid] = session
    resp = VoiceResponse()
    if not department:
        dept_list = ', '.join(appointment_mgr.get_departments())
        gather = Gather(input='speech', action='/collect-department', method='POST')
        gather.say(f"Sorry, no such department. Available departments are: {dept_list}. Please say the department.")
        resp.append(gather)
        return str(resp)
    # Ask for date
    session['department'] = department
    user_sessions[call_sid] = session
    gather = Gather(input='speech', action='/collect-date', method='POST')
    gather.say("For which date do you want the appointment? Please say the date in the format 21 August 2025 or 21-08-2025.")
    resp.append(gather)
    return str(resp)

# New: /collect-date
@app.route('/collect-date', methods=['POST'])
def collect_date():
    call_sid = request.values.get('CallSid')
    session = user_sessions.get(call_sid, {})
    date_text = request.values.get('SpeechResult', '')
    logger.info(f"User said date: {date_text}")
    # Use LLM-powered date extraction
    date = extract_date(date_text)
    if not date:
        # fallback to dateparser
        parsed = dateparser.parse(date_text)
        if parsed:
            date = parsed.strftime('%Y-%m-%d')
    if date:
        logger.info(f"Parsed date: {date}")
        session['date'] = date
        user_sessions[call_sid] = session
        resp = VoiceResponse()
        gather = Gather(input='speech', action='/collect-time', method='POST')
        gather.say("At what time? You can say 3pm, 14:00, or 2:30 p.m.")
        resp.append(gather)
        return str(resp)
    else:
        logger.warning(f"Could not parse date from: {date_text}")
        resp = VoiceResponse()
        gather = Gather(input='speech', action='/collect-date', method='POST')
        gather.say("Sorry, I didn't understand the date. Please say the date in the format 21 August 2025 or 21-08-2025.")
        resp.append(gather)
        return str(resp)

@app.route('/collect-time', methods=['POST'])
def collect_time():
    call_sid = request.values.get('CallSid')
    session = user_sessions.get(call_sid, {})
    time_text = request.values.get('SpeechResult', '')
    department = session.get('department')
    date = session.get('date')
    # Get slots for the selected department and date
    slots = []
    for doc in appointment_mgr.schedule['doctors']:
        if doc['department'].lower() == department.lower():
            for sch in doc['schedule']:
                if sch.get('date') == date:
                    slots = sch['slots']
                    break
    # Use LLM-powered time extraction
    time_val = extract_time(time_text)
    time = None
    if time_val and slots:
        # Find the slot that matches the extracted time
        for slot in slots:
            start, _ = slot['time'].split('-')
            if start == time_val:
                time = slot['time']
                break
    if not time:
        # fallback to dateparser
        parsed = dateparser.parse(time_text)
        if parsed and slots:
            for slot in slots:
                start, _ = slot['time'].split('-')
                if parsed.strftime('%H:%M') == start:
                    time = slot['time']
                    break
    if not time:
        resp = VoiceResponse()
        gather = Gather(input='speech', action='/collect-time', method='POST')
        slot_list = ', '.join([slot['time'] for slot in slots]) if slots else 'No slots available.'
        gather.say(f"Sorry, I didn't understand the time or no matching slot found. Available slots are: {slot_list}. Please say the time in the format 3pm, 14:00, or 2:30 p.m.")
        resp.append(gather)
        return str(resp)
    session['time'] = time
    user_sessions[call_sid] = session
    # Find all available doctors for that department, date, and time
    available_doctors = appointment_mgr.get_available_doctors_by_date(department, date, time)
    resp = VoiceResponse()
    if available_doctors:
        slot = {
            'doctor': available_doctors[0],
            'department': department,
            'date': date,
            'time': time
        }
        user_sessions[call_sid] = {'step': 'confirm', **slot}
        gather = Gather(input='speech', action='/confirm-booking', method='POST')
        gather.say(f" {slot['doctor']} is available in {department} on {date} at {time}. Would you like to book with {slot['doctor']}? Please say yes or no.")
        resp.append(gather)
        return str(resp)
    else:
        resp.say("Sorry, no doctors are available at that time. Please try another time or date.")
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
    if is_confirm(answer):
        # Collect user name
        session['step'] = 'name'
        user_sessions[call_sid] = session
        gather = Gather(input='speech', action='/collect-name', method='POST')
        gather.say("Can you please share your good name for the booking?")
        resp.append(gather)
    else:
        resp.say("Okay, thank you for calling. You can try booking another slot if you wish.")
        resp.hangup()
    return str(resp)

@app.route('/collect-name', methods=['POST'])
def collect_name():
    call_sid = request.values.get('CallSid')
    name = request.values.get('SpeechResult', '')
    logger.info(f"/collect-name: User said name: {name}")
    session = user_sessions.get(call_sid, {})
    resp = VoiceResponse()
    if not name.strip():
        gather = Gather(input='speech', action='/collect-name', method='POST', timeout=10)
        gather.say("Sorry, I didn't catch your name. Can you please share your good name for the booking?")
        resp.append(gather)
        resp.say("Sorry, I didn't catch your name. Please call again to book your appointment. Goodbye!")
        resp.hangup()
        return str(resp)
    session['name'] = name
    session['step'] = 'mobile'
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
    answer = request.values.get('SpeechResult', '').strip().lower()
    session = user_sessions.get(call_sid, {})
    resp = VoiceResponse()
    digits = session.get('pending_mobile', '')
    if is_confirm(answer) and len(digits) == 10:
        # Book the slot
        booked = appointment_mgr.book_slot(
            session['doctor'], session['department'], session.get('day', ''), session['time'],
            session['name'], digits
        )
        log_msg = (
            f"Appointment booked: Doctor={session['doctor']}, Department={session['department']}, "
            f"Date={session.get('date','')}, Time={session['time']}, Name={session['name']}, Mobile={digits}"
        )
        if booked:
            logger.info(log_msg)
            sms_msg = (
                f"Your appointment is confirmed!\n"
                f"Doctor: {session['doctor']}\nDepartment: {session['department']}\nDate: {session.get('date','')}\nTime: {session['time']}\nName: {session['name']}\nMobile: {digits}"
            )
            send_sms(f"+91{digits}", sms_msg)
            resp.say("Your appointment is confirmed. We have sent you a confirmation SMS with all details. Thank you!")
        else:
            logger.warning("Appointment booking failed: " + log_msg)
            resp.say("Sorry, the slot was just booked by someone else. Please try again.")
        resp.hangup()
        return str(resp)
    elif answer in ['no', 'nope', 'nah']:
        gather = Gather(input='speech', action='/collect-name', method='POST')
        gather.say("Let's try again. Please tell me your name for the booking.")
        resp.append(gather)
        return str(resp)
    else:
        gather = Gather(input='speech', action='/finalize-booking', method='POST')
        gather.say("Is the information correct? Please say yes or no.")
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
