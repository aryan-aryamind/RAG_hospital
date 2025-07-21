# rag_system.py
import os
import PyPDF2
from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_community.vectorstores import FAISS
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables import RunnableParallel, RunnablePassthrough, RunnableLambda
from langchain_core.output_parsers import StrOutputParser
from langchain_core.messages import HumanMessage, AIMessage
from langchain.memory import ConversationBufferMemory

class RAGSystem:
    def __init__(self, pdf_path, google_api_key=None):
        self.pdf_path = pdf_path
        self.google_api_key = google_api_key
        self.initialize_system()
    
    def initialize_system(self):
        # Set API key if provided
        if self.google_api_key:
            os.environ["GOOGLE_API_KEY"] = self.google_api_key
        
        # Check if file exists
        if not os.path.exists(self.pdf_path):
            raise FileNotFoundError(f"PDF file not found at {self.pdf_path}")

        # Extract and process text
        text = self.extract_text_from_pdf(self.pdf_path)
        chunks = self.process_pdf_text(text)
        
        # Create vector store
        self.vector_store = self.create_vector_store(chunks)
        self.retriever = self.vector_store.as_retriever(search_type="similarity", search_kwargs={"k": 4})
        
        # Initialize memory
        self.memory = ConversationBufferMemory(
            return_messages=True,
            output_key='answer',
            input_key='question'
        )
        
        # Build the QA chain
        self.qa_chain = self.build_qa_chain()

    def extract_text_from_pdf(self, pdf_path):
        try:
            with open(pdf_path, 'rb') as file:
                pdf_reader = PyPDF2.PdfReader(file)
                text = ""
                for page_num in range(len(pdf_reader.pages)):
                    page = pdf_reader.pages[page_num]
                    text += page.extract_text() + "\n"
            return text
        except Exception as e:
            raise Exception(f"Error reading PDF: {e}")

    def process_pdf_text(self, text):
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=1000,
            chunk_overlap=200,
            separators=["\n\n", "\n", " ", ""]
        )
        return splitter.create_documents([text])

    def create_vector_store(self, chunks):
        embeddings = HuggingFaceEmbeddings(
            model_name='sentence-transformers/all-MiniLM-L6-v2'
        )
        return FAISS.from_documents(chunks, embeddings)

    def format_docs(self, retrieved_docs):
        return "\n\n".join(doc.page_content for doc in retrieved_docs)

    def load_memory(self, _):
        stored_messages = self.memory.load_memory_variables({})['history']
        return stored_messages

    def save_memory(self, input_output):
        input_text = input_output['question']
        output_text = input_output['answer']
        self.memory.save_context(
            {"question": input_text},
            {"answer": output_text}
        )
        return input_output['answer']

    def build_qa_chain(self):
        prompt = ChatPromptTemplate.from_messages([
            ("system", """You are a helpful assistant.
            Answer ONLY from the provided PDF document context and conversation history.
            If the context is insufficient to answer the question, just say you don't know.
            Provide detailed answers when possible and cite relevant information from the context.

            Context from PDF:
            {context}"""),
            MessagesPlaceholder(variable_name="chat_history"),
            ("human", "{question}"),
        ])

        llm = ChatGoogleGenerativeAI(model='gemini-2.0-flash')

        conversational_qa_chain = (
            RunnableParallel({
                "context": self.retriever | RunnableLambda(self.format_docs),
                "question": RunnablePassthrough(),
                "chat_history": RunnableLambda(self.load_memory)
            })
            | prompt
            | llm
            | StrOutputParser()
        )

        return (
            RunnableParallel({
                "question": RunnablePassthrough(),
                "answer": conversational_qa_chain
            })
            | RunnableLambda(self.save_memory)
        )

    def ask_question(self, question):
        return self.qa_chain.invoke(question)