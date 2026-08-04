import os
from groq import Groq
from .session_manager import session_manager

# Initialize Groq client
client = Groq(api_key=os.getenv("GROQ_API_KEY", ""))

SYSTEM_PROMPT = """Eres un asistente virtual avanzado y preciso. 
Utiliza el contexto proporcionado del documento PDF para responder a la pregunta del usuario.
Si la respuesta no se encuentra en el contexto, indica amablemente que no tienes información suficiente basándote en el documento.
No inventes información.
Responde siempre en Markdown estructurado.

CONTEXTO DEL DOCUMENTO:
{context}
"""

def generate_rag_response(session_id: str, query: str) -> str:
    # 1. Retrieve context
    context = session_manager.retrieve_context(session_id, query, top_k=3)
    
    # 2. Get history
    session = session_manager.get_session(session_id)
    history = session.history
    
    # 3. Build messages
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT.format(context=context if context else "No hay contexto disponible.")}
    ]
    
    # Add history (limit to last 10 messages)
    for msg in history[-10:]:
        messages.append(msg)
        
    messages.append({"role": "user", "content": query})
    
    try:
        # 4. Generate response via Groq
        response = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=messages,
            temperature=0.3,
            max_tokens=1024,
        )
        
        bot_reply = response.choices[0].message.content
        
        # 5. Update history
        session.history.append({"role": "user", "content": query})
        session.history.append({"role": "assistant", "content": bot_reply})
        
        return bot_reply
    except Exception as e:
        print(f"Error generating response: {e}")
        return "Lo siento, hubo un error al procesar tu solicitud con el modelo de lenguaje."
