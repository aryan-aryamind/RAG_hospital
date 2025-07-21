# Twilio Webhook Setup Guide

## What is a Webhook?

A webhook is a way for Twilio to send HTTP requests to your server when certain events happen (like when a call starts). Your server then responds with TwiML that tells Twilio what to do.

## Setup Steps

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Set Environment Variables
```bash
# Required environment variables
export TWILIO_ACCOUNT_SID="your_account_sid"
export TWILIO_AUTH_TOKEN="your_auth_token"
export TWILIO_FROM_NUMBER="+15716206753"  # Your Twilio phone number
export TWILIO_TO_NUMBER="+1234567890"     # The number you want to call
```

### 3. Start the Webhook Server
```bash
python webhook_server.py
```
This starts a Flask server on `http://localhost:5000`

### 4. Expose Your Server to the Internet
Since Twilio needs to reach your server, you need to expose it to the internet. Use ngrok:

```bash
# Install ngrok (if you haven't already)
# Download from https://ngrok.com/download

# Expose your local server
ngrok http 5000
```

You'll get a URL like: `https://abc123.ngrok.io`

### 5. Test Your Setup

1. **Test Direct TwiML**:
```bash
python twilio.py
```

2. **Test Webhook**:
```python
# In twilio.py, uncomment and update the webhook section:
webhook_url = "https://abc123.ngrok.io/voice"  # Your ngrok URL
call_sid = make_webhook_call(webhook_url)
print(f"Webhook Call SID: {call_sid}")
```

## Error Handling

The code now includes comprehensive error handling:

1. **Environment Variables**:
   - Checks for all required variables
   - Provides clear error messages if any are missing

2. **Twilio Client**:
   - Handles initialization errors
   - Provides detailed error messages

3. **Call Creation**:
   - Handles API errors
   - Reports specific failure reasons

## Webhook Endpoints

- `/voice` - Handles incoming calls
- `/handle-key` - Handles user input (key presses)
- `/status` - Handles call status updates

## Advanced Features

### Dynamic Responses
You can make responses dynamic based on:
- Caller's phone number
- Time of day
- Database lookups
- API calls

### Interactive Menus
The webhook supports:
- Keypad input gathering
- Speech recognition
- Call transfers
- Recording calls

### Call Status Tracking
Monitor call status with the `/status` endpoint:
- `completed` - Call finished normally
- `answered` - Call was answered
- `busy` - Number was busy
- `failed` - Call failed
- `no-answer` - No one answered

## Troubleshooting

1. **Missing Environment Variables**:
   - Error: "Missing required environment variables: ..."
   - Solution: Set all required environment variables

2. **Twilio Client Errors**:
   - Error: "Error initializing Twilio client: ..."
   - Solution: Check your account SID and auth token

3. **Call Creation Errors**:
   - Error: "Error making call: ..."
   - Solution: Check phone numbers and Twilio account balance

4. **Webhook Issues**:
   - Error: Webhook not receiving calls
   - Solution: Check ngrok URL and server status

5. **Audio Issues**:
   - Error: No audio or poor quality
   - Solution: Check TwiML syntax and voice parameters
