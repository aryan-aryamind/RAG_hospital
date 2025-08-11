"""
Google Cloud Text-to-Speech Implementation
=========================================

This module provides a comprehensive interface for Google Cloud Text-to-Speech,
including voice selection, audio format options, and caching capabilities.

Requirements:
- Google Cloud account with Text-to-Speech API enabled
- Service account key file (JSON)
- google-cloud-texttospeech library installed

Setup:
1. Create a Google Cloud project
2. Enable the Text-to-Speech API
3. Create a service account and download the JSON key file
4. Set GOOGLE_APPLICATION_CREDENTIALS environment variable to point to your key file
"""

import os
import logging
import hashlib
from typing import Optional, Dict, List, Tuple
from google.cloud import texttospeech
from google.cloud.texttospeech import VoiceSelectionParams, AudioConfig, SynthesisInput

logger = logging.getLogger(__name__)

class GoogleCloudTTS:
    """Google Cloud Text-to-Speech client with caching and voice management."""
    
    def __init__(self, cache_dir: str = "static", enable_caching: bool = True):
        """
        Initialize Google Cloud TTS client.
        
        Args:
            cache_dir: Directory to store cached audio files
            enable_caching: Whether to enable audio file caching
        """
        self.client = texttospeech.TextToSpeechClient()
        self.cache_dir = cache_dir
        self.enable_caching = enable_caching
        
        # Create cache directory if it doesn't exist
        if enable_caching:
            os.makedirs(cache_dir, exist_ok=True)
        
        # Default voice settings
        self.default_voice = {
            'language_code': 'en-IN',
            'name': 'en-IN-Standard-A',
            'ssml_gender': texttospeech.SsmlVoiceGender.FEMALE
        }
        
        # Available voices cache
        self._voices_cache = None
        
        logger.info("Google Cloud TTS client initialized")
    
    def get_available_voices(self, language_code: str = None) -> List[Dict]:
        """
        Get available voices from Google Cloud TTS.
        
        Args:
            language_code: Filter voices by language code (e.g., 'en-IN', 'hi-IN')
            
        Returns:
            List of available voices with their properties
        """
        try:
            # Use cached voices if available
            if self._voices_cache is not None:
                voices = self._voices_cache
            else:
                voices = self.client.list_voices()
                self._voices_cache = voices
            
            available_voices = []
            
            for voice in voices.voices:
                voice_info = {
                    'name': voice.name,
                    'language_codes': list(voice.language_codes),
                    'ssml_gender': voice.ssml_gender.name,
                    'natural_sample_rate_hertz': voice.natural_sample_rate_hertz
                }
                
                # Filter by language if specified
                if language_code is None or language_code in voice.language_codes:
                    available_voices.append(voice_info)
            
            logger.info(f"Found {len(available_voices)} voices for language: {language_code or 'all'}")
            return available_voices
            
        except Exception as e:
            logger.error(f"Error fetching available voices: {str(e)}")
            return []
    
    def generate_speech(
        self,
        text: str,
        voice_name: str = None,
        language_code: str = None,
        ssml_gender: str = None,
        audio_encoding: str = 'MP3',
        speaking_rate: float = 1.0,
        pitch: float = 0.0,
        volume_gain_db: float = 0.0,
        use_ssml: bool = False
    ) -> Optional[str]:
        """
        Generate speech audio using Google Cloud TTS.
        
        Args:
            text: Text to convert to speech
            voice_name: Specific voice name (e.g., 'en-IN-Standard-A')
            language_code: Language code (e.g., 'en-IN', 'hi-IN')
            ssml_gender: Voice gender ('MALE', 'FEMALE', 'NEUTRAL')
            audio_encoding: Audio format ('MP3', 'LINEAR16', 'OGG_OPUS')
            speaking_rate: Speaking rate (0.25 to 4.0)
            pitch: Pitch adjustment (-20.0 to 20.0)
            volume_gain_db: Volume gain in dB (-96.0 to 16.0)
            use_ssml: Whether the text is SSML format
            
        Returns:
            Path to generated audio file or None if failed
        """
        if not text.strip():
            logger.warning("Empty text provided for TTS")
            return None
        # Truncate text to 5000 characters to avoid Google TTS limit
        MAX_TTS_LENGTH = 5000
        if len(text) > MAX_TTS_LENGTH:
            logger.warning(f"Text too long for TTS ({len(text)} chars), truncating to {MAX_TTS_LENGTH}.")
            text = text[:MAX_TTS_LENGTH]
        
        try:
            # Generate cache key for this request
            cache_key = self._generate_cache_key(
                text, voice_name, language_code, ssml_gender,
                audio_encoding, speaking_rate, pitch, volume_gain_db, use_ssml
            )
            
            # Check cache first
            if self.enable_caching:
                cached_file = self._get_cached_file(cache_key, audio_encoding)
                if cached_file:
                    logger.info(f"Using cached audio file: {cached_file}")
                    return cached_file
            
            # Prepare voice selection
            voice_params = VoiceSelectionParams()
            
            if voice_name:
                # When using a specific voice name, don't set other parameters
                voice_params.name = voice_name
            elif language_code:
                # When using language code, set it and optionally gender
                voice_params.language_code = language_code
                if ssml_gender:
                    voice_params.ssml_gender = getattr(texttospeech.SsmlVoiceGender, ssml_gender.upper())
            else:
                # Default configuration
                voice_params.language_code = self.default_voice['language_code']
                voice_params.name = self.default_voice['name']
                voice_params.ssml_gender = self.default_voice['ssml_gender']
            
            # Prepare audio configuration
            audio_config = AudioConfig(
                audio_encoding=getattr(texttospeech.AudioEncoding, audio_encoding.upper()),
                speaking_rate=speaking_rate,
                pitch=pitch,
                volume_gain_db=volume_gain_db
            )
            
            # Prepare synthesis input
            synthesis_input = SynthesisInput()
            if use_ssml:
                synthesis_input.ssml = text
            else:
                synthesis_input.text = text
            
            # Perform synthesis
            response = self.client.synthesize_speech(
                input=synthesis_input,
                voice=voice_params,
                audio_config=audio_config
            )
            
            # Save audio file
            file_extension = self._get_file_extension(audio_encoding)
            filename = f"speech_{cache_key}.{file_extension}"
            filepath = os.path.join(self.cache_dir, filename)
            
            with open(filepath, 'wb') as f:
                f.write(response.audio_content)
            
            logger.info(f"Generated speech audio: {filepath}")
            return f"/static/audio_cache/{filename}"
            
        except Exception as e:
            logger.error(f"Error generating speech: {str(e)}")
            return None
    
    def generate_speech_ssml(self, ssml_text: str, **kwargs) -> Optional[str]:
        """
        Generate speech from SSML text.
        
        Args:
            ssml_text: SSML formatted text
            **kwargs: Additional parameters for generate_speech
            
        Returns:
            Path to generated audio file or None if failed
        """
        return self.generate_speech(ssml_text, use_ssml=True, **kwargs)
    
    def _generate_cache_key(self, text: str, *args) -> str:
        """Generate a unique cache key for the TTS request."""
        # Create a hash of all parameters
        content = f"{text}_{'_'.join(str(arg) for arg in args)}"
        return hashlib.md5(content.encode()).hexdigest()
    
    def _get_cached_file(self, cache_key: str, audio_encoding: str) -> Optional[str]:
        """Check if a cached file exists for the given cache key."""
        file_extension = self._get_file_extension(audio_encoding)
        filename = f"speech_{cache_key}.{file_extension}"
        filepath = os.path.join(self.cache_dir, filename)
        
        if os.path.exists(filepath):
            return f"/{self.cache_dir}/{filename}"
        return None
    
    def _get_file_extension(self, audio_encoding: str) -> str:
        """Get file extension for the given audio encoding."""
        extensions = {
            'MP3': 'mp3',
            'LINEAR16': 'wav',
            'OGG_OPUS': 'ogg',
            'MULAW': 'wav',
            'ALAW': 'wav'
        }
        return extensions.get(audio_encoding.upper(), 'mp3')
    
    def clear_cache(self) -> bool:
        """Clear all cached audio files."""
        try:
            if os.path.exists(self.cache_dir):
                for filename in os.listdir(self.cache_dir):
                    if filename.startswith('speech_'):
                        filepath = os.path.join(self.cache_dir, filename)
                        os.remove(filepath)
                logger.info("Cache cleared successfully")
                return True
        except Exception as e:
            logger.error(f"Error clearing cache: {str(e)}")
        return False
    
    def get_cache_info(self) -> Dict:
        """Get information about cached files."""
        try:
            if not os.path.exists(self.cache_dir):
                return {'count': 0, 'total_size': 0, 'files': []}
            
            files = []
            total_size = 0
            count = 0
            
            for filename in os.listdir(self.cache_dir):
                if filename.startswith('speech_'):
                    filepath = os.path.join(self.cache_dir, filename)
                    file_size = os.path.getsize(filepath)
                    files.append({
                        'name': filename,
                        'size': file_size,
                        'path': filepath
                    })
                    total_size += file_size
                    count += 1
            
            return {
                'count': count,
                'total_size': total_size,
                'files': files
            }
        except Exception as e:
            logger.error(f"Error getting cache info: {str(e)}")
            return {'count': 0, 'total_size': 0, 'files': []}


# Convenience functions for easy usage
def generate_speech(text: str, **kwargs) -> Optional[str]:
    """
    Convenience function to generate speech using default settings.
    
    Args:
        text: Text to convert to speech
        **kwargs: Additional parameters for GoogleCloudTTS.generate_speech
        
    Returns:
        Path to generated audio file or None if failed
    """
    tts = GoogleCloudTTS()
    return tts.generate_speech(text, **kwargs)


def generate_speech_ssml(ssml_text: str, **kwargs) -> Optional[str]:
    """
    Convenience function to generate speech from SSML.
    
    Args:
        ssml_text: SSML formatted text
        **kwargs: Additional parameters for GoogleCloudTTS.generate_speech
        
    Returns:
        Path to generated audio file or None if failed
    """
    tts = GoogleCloudTTS()
    return tts.generate_speech_ssml(ssml_text, **kwargs)


if __name__ == "__main__":
    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    print("=== Google Cloud TTS Test ===")
    
    # Check if credentials are set
    if not os.getenv('GOOGLE_APPLICATION_CREDENTIALS'):
        print("WARNING: GOOGLE_APPLICATION_CREDENTIALS environment variable not set!")
        print("Please set it to point to your Google Cloud service account key file.")
        print("Example: export GOOGLE_APPLICATION_CREDENTIALS='/path/to/your/key.json'")
        exit(1)
    
    # Initialize TTS client
    tts = GoogleCloudTTS()
    
    # Test cases
    test_cases = [
        {
            'text': "Hello world! This is a test of Google Cloud Text-to-Speech.",
            'description': "Basic English test",
            'voice_name': 'en-IN-Standard-A'
        },
        {
            'text': "This is a test with different speaking rate and pitch.",
            'description': "Custom parameters test",
            'speaking_rate': 0.8,
            'pitch': 2.0,
            'volume_gain_db': 2.0
        },
        {
            'text': "नमस्ते! यह हिंदी में एक परीक्षण है।",
            'description': "Hindi language test",
            'language_code': 'hi-IN'
        },
        {
            'text': '<speak>Hello! This is <prosody rate="slow">slow speech</prosody> and this is <prosody rate="fast">fast speech</prosody>.</speak>',
            'description': "SSML test",
            'use_ssml': True
        }
    ]
    
    print(f"\nAvailable voices for English: {len(tts.get_available_voices('en-IN'))}")
    print(f"Available voices for Hindi: {len(tts.get_available_voices('hi-IN'))}")
    
    for i, test_case in enumerate(test_cases, 1):
        print(f"\n--- Test {i}: {test_case['description']} ---")
        print(f"Input: {test_case['text'][:50]}{'...' if len(test_case['text']) > 50 else ''}")
        
        # Remove description from kwargs
        kwargs = {k: v for k, v in test_case.items() if k != 'description'}
        text = kwargs.pop('text')
        
        audio_path = tts.generate_speech(text, **kwargs)
        
        if audio_path:
            print(f"✅ SUCCESS: Audio generated at {audio_path}")
            # Verify file exists
            file_path = audio_path.lstrip('/')
            if os.path.exists(file_path):
                file_size = os.path.getsize(file_path)
                print(f"📁 File verified: {file_size} bytes")
            else:
                print("⚠️  WARNING: File not found at generated path")
        else:
            print("❌ FAILED: No audio path returned")
    
    # Show cache information
    cache_info = tts.get_cache_info()
    print(f"\n📊 Cache Info: {cache_info['count']} files, {cache_info['total_size']} bytes total")
    
    print("\n=== Testing Complete ===")
