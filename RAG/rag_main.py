# main.py
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from rag_system import RAGSystem
import os
from typing import Optional

app = FastAPI()

# Initialize the RAG system
PDF_PATH = os.path.join(os.path.dirname(__file__), "shalby_main.pdf")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY") or "AIzaSyCmCdzv4ZL3z19NTpFhRSkLKK25HPZEvcA"

try:
    rag_system = RAGSystem(pdf_path=PDF_PATH, google_api_key=GOOGLE_API_KEY)
except Exception as e:
    raise RuntimeError(f"Failed to initialize RAG system: {str(e)}")

# Request model
class QuestionRequest(BaseModel):
    question: str
    session_id: Optional[str] = None  # Optional for session tracking

# Response model
class AnswerResponse(BaseModel):
    answer: str
    session_id: Optional[str] = None

@app.post("/ask", response_model=AnswerResponse)
async def ask_question(request: QuestionRequest):
    """
    Endpoint to ask questions about the PDF content.
    Maintains conversation memory within the session.
    """
    try:
        answer = rag_system.ask_question(request.question)
        return AnswerResponse(answer=answer, session_id=request.session_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)