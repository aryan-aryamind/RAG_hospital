# Hiospital Voice Assistant

This project is a Flask + Twilio based conversational voice assistant for booking doctor appointments at a hospital. It guides users through booking via phone call, manages available slots using a JSON file, and sends SMS confirmations with payment links.

## Features
- Conversational voice flow for appointment booking
- Doctor, department, day, and time slot selection
- Slot availability check and alternative suggestions
- User details collection (name, mobile via DTMF)
- SMS confirmation with payment link
- Slot management via `appointments.json`

# VENV 
   ```bash
# TWILIO_SID=your_twilio_sid_here
# TWILIO_AUTH=2268ac2b9bb31d7006f49f0070c521a9
# TWILIO_NUMBER=+18146820227
# TO_NUMBER=+917043156067
# NGROK_URL=https://6bf2330266be.ngrok-free.app
# GOOGLE_API_KEY="AIzaSyCmCdzv4ZL3z19NTpFhRSkLKK25HPZEvcA"
#  twilio, elenlabs, ngrok
   ```

## Setup
1. Clone the repository and install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
2. Set up your `.env` file with Twilio and other required credentials.
3. Run the Flask server:
   ```bash
   python main.py
   ```
4. Expose your server to the internet (e.g., with ngrok):
   ```bash
   ngrok http 5000
   ```
5. Configure your Twilio number's webhook to point to your `/voice` endpoint.

## Files
- `main.py` - Main Flask app and Twilio voice logic
- `appointment.py` - Appointment management logic
- `appointments.json` - Doctor schedules and slot data
- `sms.py` - SMS sending utility



