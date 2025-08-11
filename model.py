from langchain_google_genai import ChatGoogleGenerativeAI
from dotenv import load_dotenv
from langchain_core.prompts import PromptTemplate
from langchain_core.messages import SystemMessage, HumanMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.output_parsers import StrOutputParser
import dateparser

load_dotenv()

model = ChatGoogleGenerativeAI(model='gemini-2.0-flash')

parser = StrOutputParser()

bye_prompt = ChatPromptTemplate.from_messages([
    SystemMessage(content="""
        Analyze the user's message and determine if they want to end the conversation.
        Respond with ONLY 'True' if the message clearly indicates ending the conversation
        (e.g., 'bye', 'goodbye', 'that's all', 'thank you', 'end chat', etc.).
        Respond with ONLY 'False' if the message doesn't indicate ending the conversation.
        Do not add any explanations or other text.
        """),
    MessagesPlaceholder(variable_name="messages"),
])

yes_prompt = ChatPromptTemplate.from_messages([
    SystemMessage(content="""
You are a multilingual assistant that detects affirmative responses in both English and Hindi.

Determine if the user's most recent message is an affirmative confirmation such as:

English:
- 'yes'
- 'yeah'
- 'yup'
- 'uh-huh'
- 'sure'
- 'definitely'
- 'of course'
- 'okay'
- 'correct'
- 'that's right'

Hindi:
- 'हाँ' (haan)
- 'जी' (ji)
- 'जी हाँ' (ji haan)
- 'हां' (han)
- 'ठीक है' (theek hai)
- 'सही है' (sahi hai)
- 'बिल्कुल' (bilkul)
- 'अच्छा' (achha)
- 'ओके' (okay)
- 'सही' (sahi)

Return ONLY 'True' if the message clearly means yes in either language.
Return ONLY 'False' if it does NOT clearly mean yes.

Consider:
- Common variations and misspellings
- Casual speech patterns
- Regional variations in pronunciation spelling

Do NOT return anything else. Only output 'True' or 'False'.
    """),
    MessagesPlaceholder(variable_name="messages")
])

language_detection_prompt = ChatPromptTemplate.from_messages([
    SystemMessage(content="""
        Analyze the user's message to identify the language it is written in.

        **Strictly return ONLY the corresponding Google TTS language code** as per the following mapping:
        
        - Hindi -> hi-IN
        - English -> en-IN

        Do not include any explanations, greetings, or other text.

        ---
        **Examples:**
        
        - **User Input:** "Hello, I would like to know more about your services."
        - **Your Output:** en-IN

        - **User Input:** "नमस्ते, आप कैसे हैं?"
        - **Your Output:** hi-IN
                  
        - **User Input:** "Mujhe admission lena hai."
        - **Your Output:** hi-IN
        ---
    """),
    MessagesPlaceholder(variable_name="messages"),
])

extract_time_prompt = ChatPromptTemplate.from_messages([
    SystemMessage(content="""
        Extract the time from the user's message. Ignore unnecessary words, filler, or context. 
        Return ONLY the time in 24-hour HH:MM format if possible, or 'None' if no time is found or the time is ambiguous.
        Handle a wide variety of time expressions, including:
        - '3am', '3 a.m.', '3 in the morning', 'at night', 'noon', 'midnight', 'evening', 'quarter past three', 'half past two', '3pm', '14:00', '2:30 p.m.', etc.
        Examples:
        - "I want to book at 3am." → 03:00
        - "Uh, 2:30 p.m." → 14:30
        - "Can I come at 14:00?" → 14:00
        - "Let's do half past two in the afternoon." → 14:30
        - "quarter past three" → 03:15
        - "noon" → 12:00
        - "midnight" → 00:00
        - "in the evening at 7" → 19:00
        - "I want to book an appointment." → None
        Do not add any explanations or extra text.
    """),
    MessagesPlaceholder(variable_name="messages"),
])

extract_date_prompt = ChatPromptTemplate.from_messages([
    SystemMessage(content="""
        Extract the date from the user's message. Ignore unnecessary words, filler, or context. 
        Return ONLY the date in YYYY-MM-DD format if possible, or 'None' if no date is found or the date is ambiguous.
        Examples:
        - "I want to book on July 25th, 2025." → 2025-07-25
        - "Uh, 23rd. July 2025." → 2025-07-23
        - "Can I come next Friday?" → (return the next Friday's date in YYYY-MM-DD)
        - "I want to book an appointment." → None
        Do not add any explanations or extra text.
    """),
    MessagesPlaceholder(variable_name="messages"),
])

summarize_prompt = ChatPromptTemplate.from_messages([
    SystemMessage(content="""You are a conversation summarizer for a university admission assistant. 
    Your task is to concisely summarize the key information from the AI's response while:
    1. Maintaining a natural, conversational tone
    2. Preserving all critical details (requirements, deadlines, processes)
    3. Keeping it under 80 words
    4. Removing any redundant phrases like 'based on the document'
    5. Formatting lists clearly when present
    
    Speak directly to the user (use "you" instead of "the applicant").
    """),
    MessagesPlaceholder(variable_name="messages"),
])

summarize_chain = summarize_prompt | model | parser
extract_time_chain = extract_time_prompt | model | parser
extract_date_chain = extract_date_prompt | model | parser
bye_chain = bye_prompt | model | parser
yes_chain = yes_prompt | model | parser
language_detection_chain = language_detection_prompt | model  | parser

def summarize(text):
    try:
        messages = [
            HumanMessage(content=f"Please summarize this text:\n\n{text}")
        ]
        result = summarize_chain.invoke({"messages": messages})
        return result
    except Exception as e:
        print(f"Error during summarization: {e}")
        return "Sorry, I couldn't generate a summary at this time."


def extract_date(text):
    try:
        messages = [
            HumanMessage(content=f"{text}")
        ]
        result = extract_date_chain.invoke({"messages": messages})
        # Try to parse the result to ensure it's a valid date
        parsed = dateparser.parse(result)
        if parsed:
            return parsed.strftime('%Y-%m-%d')
        return None
    except Exception as e:
        print(f"Error extracting date: {e}")
        return None

def extract_time(text):
    try:
        messages = [
            HumanMessage(content=f"{text}")
        ]
        result = extract_time_chain.invoke({"messages": messages})
        # Try to parse the result to ensure it's a valid time
        parsed = dateparser.parse(result)
        if parsed:
            return parsed.strftime('%H:%M')
        return None
    except Exception as e:
        print(f"Error extracting time: {e}")
        return None

def is_bye(text):
    try:
        messages = [
            HumanMessage(content=f"check:\n\n{text}")
        ]
        result = bye_chain.invoke({"messages":messages})
        return result.strip().lower() == 'true'
    except Exception as e:
        print(f"Error checking good bye: {e}")
        return False


def is_yes(text):
    try:
        messages = [
            HumanMessage(content=f"{text}")
        ]
        result = yes_chain.invoke({"messages": messages})
        return result.strip().lower() == 'true'
    except Exception as e:
        print(f"Error checking confirmation: {e}")
        return False

def conversation_loop():
    print("Conversation started. Type 'bye' or similar to end.")
    while True:
        user_input = input("You: ")
        if is_bye(user_input):
            print("AI: Goodbye! Have a great day!")
            break
        language = detect_language(user_input)
        print(f"Language: {language}")


def detect_language(text):
    try:    
        messages = [
            HumanMessage(content=text)
        ]

        # Invoke the language detection chain
        result = language_detection_chain.invoke({"messages": messages})

        # The AIMessage object's content holds the language name.
        # .strip() removes any leading/trailing whitespace.
        language_name = result
        
        return language_name
        
    except Exception as e:
        print(f"An error occurred during language detection: {e}")
        return "Unknown"


# Example usage
if __name__ == "__main__":
    conversation_loop()