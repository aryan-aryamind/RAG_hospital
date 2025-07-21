import os
import PyPDF2
from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_community.vectorstores import FAISS
from langchain_core.prompts import PromptTemplate
from langchain_core.runnables import RunnableParallel, RunnablePassthrough, RunnableLambda
from langchain_core.output_parsers import StrOutputParser
from dotenv import load_dotenv

load_dotenv()

def setup_rag_system(pdf_path):
    """
    Setup a RAG system for PDF question answering.
    
    Args:
        pdf_path (str): Path to the PDF file
        google_api_key (str, optional): Google API key. If not provided, uses environment variable.
    
    Returns:
        dict: Contains 'qa_chain' for question answering and 'retriever' for direct access to retriever
    """
    # Set API key if provided
    # if google_api_key:
    #     os.environ["GOOGLE_API_KEY"] = google_api_key
    
    # Check if file exists
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"PDF file not found at {pdf_path}")

    # 1. Extract text from PDF
    def extract_text_from_pdf(pdf_path):
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

    # 2. Process PDF text into chunks
    def process_pdf_text(text):
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=1000,
            chunk_overlap=200,
            separators=["\n\n", "\n", " ", ""]
        )
        return splitter.create_documents([text])

    # 3. Create vector store
    def create_vector_store(chunks):
        embeddings = HuggingFaceEmbeddings(
            model_name='sentence-transformers/all-MiniLM-L6-v2'
        )
        return FAISS.from_documents(chunks, embeddings)

    # 4. Format docs for output
    def format_docs(retrieved_docs):
        return "\n\n".join(doc.page_content for doc in retrieved_docs)

    # Main processing
    text = extract_text_from_pdf(pdf_path)
    chunks = process_pdf_text(text)
    vector_store = create_vector_store(chunks)
    retriever = vector_store.as_retriever(search_type="similarity", search_kwargs={"k": 4})

    # 5. Build QA chain
    prompt = PromptTemplate(
        template="""You are a helpful assistant.
        Answer ONLY from the provided PDF document context.
        If the context is insufficient to answer the question, just say you don't know.
        Provide detailed answers when possible and cite relevant information from the context.

        Context from PDF:
        {context}

        Question: {question}

        Answer:""",
        input_variables=['context', 'question']
    )

    llm = ChatGoogleGenerativeAI(model='gemini-2.0-flash')

    parallel_chain = RunnableParallel({
        'context': retriever | RunnableLambda(format_docs),
        'question': RunnablePassthrough()
    })

    qa_chain = (
        parallel_chain | prompt | llm | StrOutputParser()
    )

    return {
        'qa_chain': qa_chain,
        'retriever': retriever,
        'vector_store': vector_store
    }