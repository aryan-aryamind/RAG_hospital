from flask import Flask, request, jsonify, Response
from twilio.twiml.voice_response import VoiceResponse, Gather
from twilio.rest import Client
from twilio.request_validator import RequestValidator
import os
import logging
import requests
import json

from datetime import datetime
from dotenv import load_dotenv

# from the file
from model import summarize, is_bye, want_admission
from elevenlab import generate_speech
from rag import setup_rag_system
from sms import send_sms
from appointment import AppointmentManager
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


@app.route('/server-rag',methods=['POST'])
def server_rag():
    """handle RAG question by calling external API"""
    rag_question = request.values.get('SpeechResult', '')
    logger.info(f"User input speech to rag: {rag_question}")

    resp = VoiceResponse()

    # admission = want_admission(rag_question)

    if want_admission(rag_question):
        gather = Gather(
            input='dtmf',
            num_digits=10,
            action='/collect_phone',
            method='POST',
            timeout=15
        )
        gather.say("To help you with admission, please enter your 10-digit phone number using the keypad.")
        resp.append(gather)
        resp.say("We didn't receive any input. Goodbye!")
        resp.hangup()
        return Response(str(resp), mimetype='text/xml')

    if is_bye(rag_question):
        resp.say("Thank you!")
        return str(resp)

    if 'book' in rag_question.lower() and 'appointment' in rag_question.lower():
        return start_booking()

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
            # eleven_gen = generate_speech(summarize_ans)
            
            gather = Gather(
                input='speech',
                action='/counter-question',
                method='POST',
                barge_in=True
            )
            gather.say(summarize_ans)
            # gather.play(eleven_gen)
            resp.append(gather)
        else:
            raise Exception(f"API returned status {api_resp.status_code}")
    
    except Exception as e:
        logger.error(f"Error calling RAG API: {e}")
        resp.say("Sorry, I'm having trouble accessing the information right now.")

    resp.redirect('/question')
    return str(resp)


@app.route('/counter-question', methods=['POST'])
def counter_question():
    """"Give answers to the counter question"""
    counter_question = request.values.get('SpeechResult', '')
    logger.info(f"User input Speech: {counter_question}")
    resp = VoiceResponse()
    try:
        api_resp = requests.post(
            API,
            json={
                'question': counter_question,
                "session_id": "user123",
                }
        )

        if api_resp.status_code == 200:
            rag_ans = api_resp.json().get('answer')
            logger.info(f"RAG API response: {rag_ans}")
            
            summarize_ans = summarize(rag_ans)
            logger.info(f"Summarize Answer: {summarize_ans}")
            # eleven_gen = generate_speech(summarize_ans)

            # Wrap the response in a Gather to allow interruption
            gather = Gather(
                input='speech',
                action='/counter-question',
                method='POST',
                barge_in=True
            )
            gather.say(summarize_ans)
            # gather.play(eleven_gen)
            resp.append(gather)
    except Exception as e:
        logger.error(f"Error calling RAG API: {e}")
        resp.say("Sorry, I'm having trouble accessing the information right now.")

    resp.redirect('/question')
    return str(resp)

@app.route('/question',methods=['GET','POST'])
def get_question():
    """Get the question"""
    response = VoiceResponse()
    gather = Gather(
            input='speech',
            action='/server-rag',
            method='POST',
            # speechTimeout="auto",
            barge_in=True,
            # timeout=5
        )
    response.append(gather)
    # response.say("We didn't receive any input. Please call back later. Goodbye!", voice='alice')
    return str(response)

@app.route('/collect_phone', methods=['POST'])
def collect_phone():
    response = VoiceResponse()
    digits = request.form.get('Digits', '')
    if len(digits) == 10:
        # Save phone number to JSON file
        try:
            if os.path.exists(ADMISSION_JSON):
                with open(ADMISSION_JSON, 'r') as f:
                    leads = json.load(f)
            else:
                leads = []
            leads.append({"phone": digits})
            with open(ADMISSION_JSON, 'w') as f:
                json.dump(leads, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving phone number: {e}")
        # Send SMS with link
        try:
            send_sms(f"+91{digits}", 
            f"Thank you for your interest! You can continue your queries here Contact 12345 ")
        except Exception as e:
            logger.error(f"Error sending SMS: {e}")
        response.say("Thank you. We have sent you a link to our Dashboard for Payment. You can contact your queries at this 12345 number, or ask me more questions now.")
    else:
        response.say("Sorry, that was not a valid phone number. Please try again.")
        response.redirect('/voice')
    return Response(str(response), mimetype='text/xml')

@app.route('/start-booking', methods=['POST'])
def start_booking():
    call_sid = request.values.get('CallSid')
    user_sessions[call_sid] = {'step': 'doctor'}
    resp = VoiceResponse()
    gather = Gather(input='speech', action='/collect-doctor', method='POST')
    gather.say("Which doctor would you like to book an appointment with?")
    resp.append(gather)
    resp.say("We didn't receive any input. Goodbye!")
    return str(resp)

@app.route('/collect-doctor', methods=['POST'])
def collect_doctor():
    call_sid = request.values.get('CallSid')
    doctor = request.values.get('SpeechResult', '')
    user_sessions[call_sid]['doctor'] = doctor
    user_sessions[call_sid]['step'] = 'department'
    resp = VoiceResponse()
    gather = Gather(input='speech', action='/collect-department', method='POST')
    gather.say(f"Which department is {doctor} in?")
    resp.append(gather)
    return str(resp)

@app.route('/collect-department', methods=['POST'])
def collect_department():
    call_sid = request.values.get('CallSid')
    department = request.values.get('SpeechResult', '')
    user_sessions[call_sid]['department'] = department
    user_sessions[call_sid]['step'] = 'day'
    resp = VoiceResponse()
    gather = Gather(input='speech', action='/collect-day', method='POST')
    gather.say("Which day do you want the appointment?")
    resp.append(gather)
    return str(resp)

@app.route('/collect-day', methods=['POST'])
def collect_day():
    call_sid = request.values.get('CallSid')
    day = request.values.get('SpeechResult', '')
    user_sessions[call_sid]['day'] = day
    user_sessions[call_sid]['step'] = 'time'
    resp = VoiceResponse()
    gather = Gather(input='speech', action='/collect-time', method='POST')
    gather.say("What time do you want the appointment?")
    resp.append(gather)
    return str(resp)

@app.route('/collect-time', methods=['POST'])
def collect_time():
    call_sid = request.values.get('CallSid')
    time = request.values.get('SpeechResult', '')
    session = user_sessions[call_sid]
    session['time'] = time
    session['step'] = 'name'
    available = appointment_mgr.check_availability(session['doctor'], session['department'], session['day'], time)
    resp = VoiceResponse()
    if available:
        gather = Gather(input='speech', action='/collect-name', method='POST')
        gather.say("Please tell me your name for the booking.")
        resp.append(gather)
    else:
        alt = appointment_mgr.suggest_alternative(session['doctor'], session['department'], session['day'], time)
        if alt:
            session['suggested'] = alt
            gather = Gather(input='speech', action='/handle-suggestion', method='POST')
            gather.say(f"Sorry, that slot is not available. Would you like to book {alt['day']} at {alt['time']} instead? Please say yes or no.")
            resp.append(gather)
        else:
            resp.say("Sorry, no slots are available. Thank you.")
            resp.hangup()
    return str(resp)

@app.route('/handle-suggestion', methods=['POST'])
def handle_suggestion():
    call_sid = request.values.get('CallSid')
    answer = request.values.get('SpeechResult', '').strip().lower()
    session = user_sessions[call_sid]
    resp = VoiceResponse()
    if answer in ['yes', 'yeah', 'yup', 'sure']:
        session['day'] = session['suggested']['day']
        session['time'] = session['suggested']['time']
        session['step'] = 'name'
        gather = Gather(input='speech', action='/collect-name', method='POST')
        gather.say("Great. Please tell me your name for the booking.")
        resp.append(gather)
    else:
        resp.say("Okay, thank you for calling.")
        resp.hangup()
    return str(resp)

@app.route('/collect-name', methods=['POST'])
def collect_name():
    call_sid = request.values.get('CallSid')
    name = request.values.get('SpeechResult', '')
    user_sessions[call_sid]['name'] = name
    user_sessions[call_sid]['step'] = 'mobile'
    resp = VoiceResponse()
    gather = Gather(input='dtmf', num_digits=10, action='/finalize-booking', method='POST')
    gather.say("Please enter your 10 digit mobile number using the keypad.")
    resp.append(gather)
    return str(resp)

@app.route('/finalize-booking', methods=['POST'])
def finalize_booking():
    call_sid = request.values.get('CallSid')
    digits = request.values.get('Digits', '')
    session = user_sessions[call_sid]
    resp = VoiceResponse()
    if len(digits) == 10:
        booked = appointment_mgr.book_slot(
            session['doctor'], session['department'], session['day'], session['time'],
            session['name'], digits
        )
        if booked:
            send_sms(f"+91{digits}", f"Your appointment with {session['doctor']} is confirmed for {session['day']} at {session['time']}. Pay here: <link>")
            resp.say("Your appointment is confirmed. We have sent you a confirmation SMS with a payment link. Thank you!")
        else:
            resp.say("Sorry, the slot was just booked by someone else. Please try again.")
    else:
        resp.say("That was not a valid mobile number. Please try again.")
        resp.redirect('/collect-name')
    resp.hangup()
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
