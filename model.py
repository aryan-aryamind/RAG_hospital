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

admission_prompt = ChatPromptTemplate.from_messages([
    SystemMessage(content="""
        Analyze the user's message and **strictly** determine if they are expressing **only initial intent** to start admission.  
        **Only return 'True' if the message is a direct request to begin admission (no follow-up questions).**  

        **Examples of 'True':**  
        - "I want to take admission."  
        - "I want to apply for B.Tech."  
        - "Sign me up for the course."  

        **Examples of 'False':**  
        - "What is the admission process?" (→ handled by another model)  
        - "How do I apply?" (→ handled by another model)  
        - "I want to take admission. What’s next?" (→ `False`, because it asks for process)  
        - "Can you give me the contact number?" (→ `False`, not direct intent)  

        **Only return 'True' or 'False'—no explanations.**   
        """),
    MessagesPlaceholder(variable_name="messages"),
])

# Date extraction chain
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

summarize_chain = summarize_prompt | model | parser
bye_chain = bye_prompt | model | parser
admission_chain = admission_prompt | model | parser
extract_date_chain = extract_date_prompt | model | parser

# Time extraction chain
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

# Confirmation chain
confirm_prompt = ChatPromptTemplate.from_messages([
    SystemMessage(content="""
        Analyze the user's message and determine if it is a clear confirmation (yes, yeah, yup, sure, haa, ha, hove, hen, henh, haah, etc.).
        Respond with ONLY 'True' if the message is a confirmation.
        Respond with ONLY 'False' if the message is not a confirmation.
        Do not add any explanations or extra text.
    """),
    MessagesPlaceholder(variable_name="messages"),
])

extract_time_chain = extract_time_prompt | model | parser
confirm_chain = confirm_prompt | model | parser

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
    
def want_admission(text):
    try:
        messages = [
            HumanMessage(content=f"check:\n\n{text}")
        ]
        result = admission_chain.invoke({"messages":messages})
        return result.strip().lower() == 'true'
    except Exception as e:
        print(f"Error checking good bye: {e}")
        return False

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

def is_confirm(text):
    try:
        messages = [
            HumanMessage(content=f"{text}")
        ]
        result = confirm_chain.invoke({"messages": messages})
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
        if want_admission(user_input):
            print("AI: give me your number")
        summary = summarize(user_input)
        print(f"AI Summary: {summary}")
        date = extract_date(user_input)
        print(f"Extracted Date: {date}")
        time = extract_time(user_input)
        print(f"Extracted Time: {time}")

# Example usage
if __name__ == "__main__":
    conversation_loop()