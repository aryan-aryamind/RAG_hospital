# Download the helper library from https://www.twilio.com/docs/python/install
import os
from twilio.rest import Client
from twilio.twiml.voice_response import VoiceResponse, Say
from dotenv import load_dotenv
load_dotenv()

account_sid = os.environ["TWILIO_SID"]
auth_token = os.environ["TWILIO_AUTH"]
from_number = os.environ["TWILIO_NUMBER"]
to_number = "+917043156067"
# to_number = "+919428192520"

try:
    client = Client(account_sid, auth_token)
except Exception as e:
    print(f"Error initializing Twilio client: {e}")
    raise


def make_webhook_call(webhook_url):
    """
    Make a call using a webhook URL
    
    Args:
        webhook_url (str): The webhook URL to use for the call
        
    Returns:
        str: The call SID
    """
    try:
        call = client.calls.create(
            url=f"{webhook_url}/voice",
            from_=from_number,
            to=to_number,
            status_callback=f"{webhook_url}/status",
            status_callback_event=['completed', 'answered', 'busy', 'failed', 'no-answer']
        )
        return call.sid
    except Exception as e:
        print(f"Error making webhook call: {e}")
        raise

# Example webhook usage (uncomment and modify the URL):
webhook_url = "https://c7cdd045ec46.ngrok-free.app"  # Replace with your actual ngrok URL
call_sid = make_webhook_call(webhook_url)
print(f"Webhook Call SID: {call_sid}")
