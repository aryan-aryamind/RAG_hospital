from twilio.rest import Client
import os

# You can set these as environment variables or pass them directly
TWILIO_SID = os.getenv('TWILIO_SID')
TWILIO_AUTH = os.getenv('TWILIO_AUTH')
TWILIO_NUMBER = os.getenv('TWILIO_NUMBER')

def send_sms(to_number, message):
    """
    Send an SMS using Twilio.
    :param to_number: Recipient phone number (e.g., '+911234567890')
    :param message: Text message to send
    :return: Message SID if sent, None otherwise
    """
    try:
        client = Client(TWILIO_SID, TWILIO_AUTH)
        msg = client.messages.create(
            body=message,
            from_=TWILIO_NUMBER,
            to=to_number
        )
        return msg.sid
    except Exception as e:
        print(f"Error sending SMS: {e}")
        return None
