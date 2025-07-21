# voice/elevenlabs_tts.py
import os
import logging
import requests

logger = logging.getLogger(__name__)

ELEVENLABS_API_KEY = "sk_0b3386e698e699eb982210a7a3739a8ad85e42adc1815e67"  # <-- Update with your real API key
ELEVENLABS_VOICE_ID = "H8bdWZHK2OgZwTN7ponr"
TIMEOUT = 120

def generate_speech(text):
    """Generate speech audio using ElevenLabs and save to static/ with a unique filename."""
    try:
        response = requests.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}",
            headers={
                "xi-api-key": ELEVENLABS_API_KEY,
                "Content-Type": "application/json"
            },
            json={"text": text,
                  "model_id": "eleven_monolingual_v1"},
            timeout=TIMEOUT
        )
        logger.info(f"[TTS] Status: {response.status_code}")
        logger.info(f"[TTS] Content (start): {response.content[:200]}")

        if response.status_code != 200:
            logger.error(f"ElevenLabs API error: {response.text}")
            return None
        
        audio = response.content

        filename = f"speech_{abs(hash(text))}.mp3"
        static_folder = os.path.join(os.getcwd(), "static")
        os.makedirs(static_folder, exist_ok=True)
        
        filepath = os.path.join(static_folder, filename)
        with open(filepath, 'wb') as f:
            f.write(audio)
            
        return f"/static/{filename}"
    except Exception as e:
        logger.error(f"Error generating speech: {str(e)}")
        return None

# if __name__ == "__main__":
#     # Configure logging
#     logging.basicConfig(
#         level=logging.DEBUG,
#         format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
#     )
    
#     print("\n=== Testing ElevenLabs TTS ===")
    
#     # Test cases
#     test_cases = [
#         ("Hello world, this is a basic test", "Basic test"),
#         ("This verifies caching works - same text should use cached file", "Caching test"),
#         ("", "Empty string test"),
#         ("Special characters: @#$%^&*()", "Special characters test"),
#         ("This is a very long text designed to test how the system handles longer inputs. " * 5, "Long text test")
#     ]
    
#     for text, description in test_cases:
#         print(f"\nTest: {description}")
#         print(f"Input: '{text[:50]}{'...' if len(text) > 50 else ''}'")
        
#         audio_path = generate_speech(text)
        
#         if audio_path:
#             print(f"SUCCESS: Audio generated at {audio_path}")
#             # Verify file exists
#             if os.path.exists(audio_path.lstrip('/')):
#                 file_size = os.path.getsize(audio_path.lstrip('/'))
#                 print(f"FILE VERIFIED: {file_size} bytes")
#             else:
#                 print("WARNING: File not found at generated path")
#         else:
#             print("FAILED: No audio path returned")
    
#     print("\n=== Testing Complete ===")