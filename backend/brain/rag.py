"""Repair knowledge for Pass 2: a small ChromaDB collection of hardware repair guides, searched by meaning.

Everything heavy is lazy: chromadb, its embedding model and the Claude client are only created on first use (call warm() at
startup to pay that cost in the background). If chromadb is not installed, retrieval returns no extra context instead of
breaking the import of everything that depends on this module (CI installs a light set of packages on purpose)."""
import os
import threading
from pathlib import Path

DB_PATH = Path(__file__).resolve().parents[2] / "data" / "rag_chroma_db"     # under data/, not wherever the server was started
_lock = threading.Lock()
_state: dict = {}

# 2. Define an Extensive Knowledge Base (8+ Detailed Repair Guides)
repair_documents = [
    {
        "id": "doc_spacebar_stabilizer_screws",
        "text": (
            "How to replace a space key that uses a stabilizer bar and hidden screws: "
            "1. Safety first: Always unplug the keyboard completely or remove batteries before opening electronics. "
            "2. Removing the spacebar keycap: Gently pull up evenly using a wire keycap puller. Be careful not to bend the metal wire stabilizer bar (Costar or Cherry style) attached underneath the keycap hooks. "
            "3. Locating hidden screws: Many keyboards hide assembly screws underneath rubber feet pads, serial number stickers, or fold-out tilt legs. Peel these back carefully. "
            "4. Unscrewing the case: Use a precision Phillips screwdriver to remove all case screws. Keep track of screw lengths. "
            "5. Splitting the case: Carefully separate top and bottom plastic case bezels using a plastic opening tool. Watch out for internal ribbon cables connecting the daughterboard or lights—do not yank them."
        ),
        "metadata": {"category": "keyboards", "component": "spacebar", "difficulty": "medium"}
    },
    {
        "id": "doc_standard_keycaps",
        "text": (
            "How to replace standard mechanical or membrane keys: "
            "1. Power down and disconnect the device. "
            "2. Position a wire keycap puller around opposite corners of the target keycap. "
            "3. Pull straight upward with steady, even force until the keycap snaps off the cross-shaped stem. Avoid twisting or yanking sideways to prevent breaking the switch stem. "
            "4. To install a new keycap, align it directly over the switch stem and press down firmly until it seats completely."
        ),
        "metadata": {"category": "keyboards", "component": "keycaps", "difficulty": "easy"}
    },
    {
        "id": "doc_hotswap_switches",
        "text": (
            "How to replace a broken switch on a hot-swappable mechanical keyboard: "
            "1. Unplug the keyboard. Remove the keycap covering the faulty switch using a keycap puller. "
            "2. Take a metal switch puller tool and clip its prongs into the top and bottom clips of the mechanical switch housing. "
            "3. Squeeze gently and pull straight up with steady vertical force. Do not rock or bend the switch violently, as this can damage the hot-swap metal sockets on the PCB. "
            "4. Inspect the pins of the replacement switch to ensure they are completely straight. Align the pins with the PCB socket holes and push straight down until it clicks securely into place."
        ),
        "metadata": {"category": "keyboards", "component": "switches", "difficulty": "easy"}
    },
    {
        "id": "doc_soldered_switches",
        "text": (
            "How to desolder and replace a broken switch on a soldered mechanical keyboard: "
            "1. Disconnect the keyboard, unscrew the case, and safely detach the main circuit board (PCB) from the plate. "
            "2. Heat your soldering iron to ~350°C (660°F). Apply a touch of fresh solder to the two switch pins to improve heat transfer. "
            "3. Place a solder sucker (desoldering pump) or desoldering braid directly over the molten solder joint, trigger the pump, and suction out the old solder completely. "
            "4. Once both pins are free of solder, gently wiggle or pull the switch from the top side while applying mild heat if necessary. Clean the pad, insert the new switch, and solder the pins back down."
        ),
        "metadata": {"category": "keyboards", "component": "switches", "difficulty": "advanced"}
    },
    {
        "id": "doc_liquid_spills",
        "text": (
            "How to fix sticky keys caused by liquid spills: "
            "1. Immediate action: Disconnect the USB cable or power source instantly. Flip the keyboard upside down immediately to prevent liquid from seeping into the main controller PCB. "
            "2. Key removal: Remove all sticky keycaps surrounding the spill zone. "
            "3. Cleaning: Use a cotton swab saturated with high-purity isopropyl alcohol (90% or higher) to clean dried residue around and inside the switch housing. "
            "4. Drying: Leave the keyboard completely inverted and disassembled in a well-ventilated area for at least 24 to 48 hours before testing or reconnecting power."
        ),
        "metadata": {"category": "maintenance", "component": "spills", "difficulty": "medium"}
    },
    {
        "id": "doc_mouse_micro_switch",
        "text": (
            "How to replace a mouse double-clicking micro-switch: "
            "1. Unplug the mouse. Peel off the adhesive mouse skates (feet) carefully to expose hidden assembly screws underneath. "
            "2. Unscrew the housing and lift the top shell away, taking care not to strain any internal side-button wires. "
            "3. Locate the faulty mechanical micro-switch (such as an Omron switch) on the circuit board. "
            "4. Desolder the three metal pins of the old micro-switch using a soldering iron and a solder sucker or desoldering braid. "
            "5. Insert the new micro-switch into the PCB holes, solder the pins securely, reassemble the mouse shell, and apply fresh replacement mouse skates."
        ),
        "metadata": {"category": "mice", "component": "micro_switch", "difficulty": "advanced"}
    },
    {
        "id": "doc_mouse_scroll_wheel",
        "text": (
            "How to fix a jumping or skipping mouse scroll wheel: "
            "1. Disconnect the mouse, remove the mouse skates, and open the outer plastic shell. "
            "2. Locate the mechanical scroll wheel encoder (the small metal box-like component the wheel passes through). "
            "3. Spray a tiny, controlled burst of specialized electrical contact cleaner (like DeoxIT D5) directly inside the small gaps of the encoder housing. Avoid using WD-40 or general lubricants which attract dust. "
            "4. Rotate the scroll wheel rapidly back and forth for 30 seconds to clean the internal copper wiper contacts, let it dry completely, and reassemble."
        ),
        "metadata": {"category": "mice", "component": "scroll_wheel", "difficulty": "medium"}
    },
    {
        "id": "doc_ribbon_cable_handling",
        "text": (
            "Safe handling of internal ribbon cables (FPC connectors): "
            "1. Never pull or yank flat plastic ribbon cables directly out of their connectors; this will tear the traces or bend locking pins. "
            "2. Locate the locking mechanism on the connector socket: flip-up black/brown latch bars or sliding push-bars. "
            "3. Gently unlock the latch using a fine plastic spudger or fingernail. The ribbon cable will slide out effortlessly with zero resistance. "
            "4. When reconnecting, push the ribbon cable straight and flat into the slot until fully seated, then snap the locking latch back down securely."
        ),
        "metadata": {"category": "general", "component": "ribbon_cables", "difficulty": "easy"}
    }
]

def _collection():
    """The collection, created (and filled once) on first use. Raises ImportError if chromadb is not installed."""
    with _lock:
        if "collection" in _state:
            return _state["collection"]
        import chromadb
        from chromadb.utils import embedding_functions

        client = chromadb.PersistentClient(path=str(DB_PATH))
        coll = client.get_or_create_collection(name="comprehensive_hardware_repair_kb",
                                               embedding_function=embedding_functions.DefaultEmbeddingFunction())
        if coll.count() < len(repair_documents):                  # prevents duplication when it runs again
            coll.add(ids=[d["id"] for d in repair_documents], documents=[d["text"] for d in repair_documents],
                     metadatas=[d["metadata"] for d in repair_documents])
        _state["collection"] = coll
        return coll


def warm() -> bool:
    """Build the store and load the embedding model now (a first query otherwise pays for it). False if unavailable."""
    try:
        _collection().query(query_texts=["warm up"], n_results=1)
        return True
    except Exception as e:
        print(f"RAG unavailable ({type(e).__name__}: {str(e)[:100]}): Pass 2 runs without the extra guides", flush=True)
        return False


def retrieve_repair_knowledge(query: str, top_k: int = 2) -> str:
    """The guides most relevant to `query`, as text for a prompt (no Claude call: Pass 2 adds them to its own)."""
    if not query.strip():
        return ""
    try:
        results = _collection().query(query_texts=[query], n_results=top_k)
    except Exception as e:                      # chromadb missing, model download failed...: the live loop must not break on it
        if not _state.get("warned"):
            _state["warned"] = True
            print(f"RAG unavailable ({type(e).__name__}): continuing without the extra guides", flush=True)
        return ""
    chunks = results["documents"][0] if results.get("documents") else []
    return "\n\n---\n\n".join(chunks)


# 4. Setup RAG Query Function with Claude
def query_repair_rag(user_question: str, top_k: int = 3):
    """Searches ChromaDB for relevant repair instructions, builds prompt context, and queries Claude."""
    
    # Query ChromaDB for semantically similar chunks
    results = _collection().query(
        query_texts=[user_question],
        n_results=top_k
    )
    
    retrieved_chunks = results["documents"][0] if results["documents"] else []
    
    print(f"\n[RAG] Retrieved {len(retrieved_chunks)} relevant chunks from ChromaDB.")
    
    # Format context for LLM
    context_text = "\n\n---\n\n".join(retrieved_chunks)
    
    system_prompt = (
        "You are an expert hardware repair assistant. Answer the user's question "
        "strictly and accurately using the provided repair documentation context. "
        "Emphasize safety steps (like unplugging first) and detail specific mechanical actions."
    )
    
    user_prompt = f"""Repair Documentation Context:
{context_text}

User Question: {user_question}"""

    # Call Claude API
    import anthropic

    response = anthropic.Anthropic().messages.create(
        model=os.getenv("RAG_MODEL", "claude-sonnet-5-5"),
        max_tokens=600,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}]
    )
    
    return response.content[0].text, retrieved_chunks


# --- Example Test Execution ---
if __name__ == "__main__":
    test_query = "My mouse scroll wheel is skipping pages when I scroll. How do I fix it?"
    
    answer, sources = query_repair_rag(test_query, top_k=2)
    
    print("\n=== AI Repair Answer ===")
    print(answer)