from langchain_google_genai import ChatGoogleGenerativeAI
from dotenv import load_dotenv
from langchain_core.prompts import PromptTemplate
from langchain_core.messages import SystemMessage, HumanMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.output_parsers import StrOutputParser

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

        
summarize_chain = summarize_prompt | model | parser
bye_chain = bye_prompt | model | parser
admission_chain = admission_prompt | model | parser

def summarize(text):
    try:
        # Create the message structure
        messages = [
            HumanMessage(content=f"Please summarize this text:\n\n{text}")
        ]
        
        # Invoke the chain with the messages
        result = summarize_chain.invoke({"messages": messages})
        # print("Generated Summary:", result)
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

def conversation_loop():
    print("Conversation started. Type 'bye' or similar to end.")
    while True:
        user_input = input("You: ")
        
        if is_bye(user_input):
            print("AI: Goodbye! Have a great day!")
            break
        
        if want_admission(user_input):
            print("AI: give me your number")
        # If not goodbye, proceed with summarization
        summary = summarize(user_input)
        print(f"AI Summary: {summary}")

# Example usage
if __name__ == "__main__":
    conversation_loop()